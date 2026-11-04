"""Anotações no Phoenix: fila + worker que envia em lote depois de `force_flush` em thread."""

import asyncio
import logging
from collections import OrderedDict, deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)
FLUSH_TIMEOUT_S = 2.0
ATTEMPTS = 3
BATCH_WINDOW_S = 0.2


@dataclass(frozen=True)
class Annotation:
    trace_id: str
    name: str
    label: str
    score: float | None
    explanation: str | None
    span_id: str | None = None  # alvo direto (runner da eval, outro processo)


_queue: deque[Annotation] = deque(maxlen=1000)
_roots: OrderedDict[str, str] = OrderedDict()  # trace_id -> span_id do AGENT (alvo da anotação)
_cfg: dict[str, Any] = {"flush": None, "send": None, "backoff_s": 0.5}
_worker: asyncio.Task[None] | None = None
_lock: asyncio.Lock | None = None  # um drain por vez (worker, lifespan e chamadas manuais)


def configure(
    *, flush: Callable[[], object], send: Callable[[list[Annotation]], None], backoff_s: float = 0.5
) -> None:
    _cfg.update(flush=flush, send=send, backoff_s=backoff_s)


def remember_root(trace_id: str, span_id: str) -> None:
    _roots[trace_id] = span_id
    while len(_roots) > 10_000:
        _roots.popitem(last=False)


def annotate_span(
    span_id: str, name: str, label: str, score: float | None, explanation: str | None
) -> None:
    """Como `annotate`, pelo span raiz (runner da eval em outro processo, via `meta.span_id`)."""
    _enqueue(Annotation("", name, label, score, explanation, span_id))


def annotate(
    trace_id: str, name: str, label: str, score: float | None, explanation: str | None
) -> None:
    """Enfileira (não bloqueia); o worker envia depois do flush. Sem loop: fica para `drain()`."""
    _enqueue(Annotation(trace_id, name, label, score, explanation))


def _enqueue(a: Annotation) -> None:
    global _worker
    _queue.append(a)
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    if _worker is None or _worker.done():
        _worker = loop.create_task(_run())


async def _run() -> None:
    await asyncio.sleep(BATCH_WINDOW_S)
    while _queue and await drain():  # o que chegou durante um drain em curso não fica para trás
        pass


async def drain() -> bool:
    """Envia a fila; só remove o que foi enviado. False se falhou (a fila é mantida)."""
    global _lock
    _lock = _lock or asyncio.Lock()
    async with _lock:
        return await _drain()


async def _drain() -> bool:
    n = len(_queue)
    if not n:
        return True
    batch = list(_queue)
    if (flush := _cfg["flush"]) is not None:
        try:  # o span precisa estar no Phoenix antes da anotação; o flush bloqueia: em thread
            await asyncio.wait_for(asyncio.to_thread(flush), FLUSH_TIMEOUT_S)
        except Exception as e:  # noqa: BLE001 - segue: o retry cobre o span ainda em trânsito
            log.warning("force_flush falhou: %s", type(e).__name__)
    if (send := _cfg["send"]) is None:
        return False
    for i in range(ATTEMPTS):
        try:
            await asyncio.to_thread(send, batch)
            for a in batch:  # tudo-ou-nada; só sai o que é do lote (overflow pode ter descartado)
                if _queue and _queue[0] is a:
                    _queue.popleft()
            return True
        except Exception as e:  # noqa: BLE001
            log.warning("anotação: tentativa %d falhou: %s", i + 1, type(e).__name__)
            if i + 1 < ATTEMPTS:
                await asyncio.sleep(_cfg["backoff_s"] * 2**i)
    log.warning("anotações mantidas na fila: %d", n)
    return False


def phoenix_sender(base_url: str) -> Callable[[list[Annotation]], None]:
    def send(batch: list[Annotation]) -> None:
        from phoenix.client import Client

        data: list[Any] = []
        for a in batch:
            if not (span_id := a.span_id or _roots.get(a.trace_id)):
                log.warning(
                    "anotação sem span raiz conhecido: %s", a.name
                )  # trace de outro processo
                continue
            data.append({"span_id": span_id, "name": a.name, "annotator_kind": "HUMAN",
                         "result": {"label": a.label, "score": a.score,
                                    "explanation": a.explanation}})  # fmt: skip
        if data:  # um POST só: falha = nada enviado, o retry não duplica
            Client(base_url=base_url).spans.log_span_annotations(span_annotations=data, sync=True)

    return send
