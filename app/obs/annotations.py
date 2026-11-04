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


_queue: deque[Annotation] = deque(maxlen=1000)
_roots: OrderedDict[str, str] = OrderedDict()  # trace_id -> span_id do AGENT (alvo da anotação)
_cfg: dict[str, Any] = {"flush": None, "send": None, "backoff_s": 0.5}
_worker: asyncio.Task[None] | None = None


def configure(
    *, flush: Callable[[], object], send: Callable[[list[Annotation]], None], backoff_s: float = 0.5
) -> None:
    _cfg.update(flush=flush, send=send, backoff_s=backoff_s)


def remember_root(trace_id: str, span_id: str) -> None:
    _roots[trace_id] = span_id
    while len(_roots) > 10_000:
        _roots.popitem(last=False)


def annotate(
    trace_id: str, name: str, label: str, score: float | None, explanation: str | None
) -> None:
    """Enfileira (não bloqueia); o worker envia depois do flush. Sem loop: fica para `drain()`."""
    global _worker
    _queue.append(Annotation(trace_id, name, label, score, explanation))
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    if _worker is None or _worker.done():
        _worker = loop.create_task(_run())


async def _run() -> None:
    await asyncio.sleep(BATCH_WINDOW_S)
    await drain()


async def drain() -> None:
    if not _queue:
        return
    batch = list(_queue)
    _queue.clear()
    if (flush := _cfg["flush"]) is not None:
        try:  # o span precisa estar no Phoenix antes da anotação; o flush bloqueia: em thread
            await asyncio.wait_for(asyncio.to_thread(flush), FLUSH_TIMEOUT_S)
        except Exception as e:  # noqa: BLE001 - segue: o retry cobre o span ainda em trânsito
            log.warning("force_flush falhou: %s", type(e).__name__)
    if (send := _cfg["send"]) is None:
        return
    for i in range(ATTEMPTS):
        try:
            await asyncio.to_thread(send, batch)
            return
        except Exception as e:  # noqa: BLE001
            log.warning("anotação: tentativa %d falhou: %s", i + 1, type(e).__name__)
            if i + 1 < ATTEMPTS:
                await asyncio.sleep(_cfg["backoff_s"] * 2**i)
    log.warning("anotações descartadas: %d", len(batch))


def phoenix_sender(base_url: str) -> Callable[[list[Annotation]], None]:
    def send(batch: list[Annotation]) -> None:
        from phoenix.client import Client

        client = Client(base_url=base_url)
        for a in batch:
            if (span_id := _roots.get(a.trace_id)) is None:
                # shortcut: só resolve traces deste processo; trace de outro processo, descarta
                log.warning("anotação sem span raiz conhecido: %s", a.name)
                continue
            client.spans.add_span_annotation(
                span_id=span_id, annotation_name=a.name, annotator_kind="HUMAN",
                label=a.label, score=a.score, explanation=a.explanation, sync=True,
            )  # fmt: skip

    return send
