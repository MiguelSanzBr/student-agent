"""Orçamento de novas tentativas, comum a todos os provedores de LLM (spec §6.5)."""

from collections import deque

from app.obs.metrics import METRICS
from core.clock import monotonic

RETRY_FRACTION, RETRY_FLOOR_PER_MIN = 0.10, 3
BACKOFF_S = 0.3  # sem dica de espera do provedor (RetryInfo / retry-after)
MIN_CALL_S = 0.5  # sobra mínima para valer a pena uma nova tentativa


class RetryBudget:
    """No máximo 10% das chamadas do último minuto viram nova tentativa (piso de 3 por minuto)."""

    def __init__(self) -> None:
        self._calls: deque[float] = deque()
        self._retries: deque[float] = deque()

    def call(self) -> None:
        """Conta uma chamada (não uma tentativa): o teto de 10% é sobre chamadas."""
        self._calls.append(monotonic())

    def may_retry(self, now: float) -> bool:
        for q in (self._calls, self._retries):
            while q and now - q[0] > 60:
                q.popleft()
        return len(self._retries) < max(RETRY_FLOOR_PER_MIN, RETRY_FRACTION * len(self._calls))

    def retried(self, now: float) -> None:
        self._retries.append(now)
        METRICS["agent_llm_retries_total"].inc()  # type: ignore[union-attr]

    def worth_retry(self, now: float, deadline: float, delay: float) -> bool:
        """Há tempo para esperar `delay` e ainda fazer uma chamada útil, e há orçamento."""
        return deadline - now >= delay + MIN_CALL_S and self.may_retry(now)
