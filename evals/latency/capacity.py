"""Capacidade e custo mensal (spec §6.5): contas puras, sem I/O."""

import math
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.agent.llm import Usage
from app.config import Quota
from app.obs.cost import cost_usd

# Premissa de um turno médio (~4.700 tokens, como no teste de capacidade): 4.000 entrada, 300 saída
# e 400 de raciocínio. Refit com os tokens medidos na janela A.
TURN_USAGE = Usage(input_tokens=4000, output_tokens=300, thinking_tokens=400)


@dataclass(frozen=True)
class Capacity:
    turns_per_s: float
    in_flight: float
    tpm: float
    rpm: float
    replicas: int  # cotas (projetos) necessárias para absorver a carga


def capacity(
    active_students: int, msg_interval_s: float, tokens_per_turn: int, turn_seconds: float,
    quota: Quota, *, calls_per_turn: float = 1.0,
) -> Capacity:  # fmt: skip
    tps = active_students / msg_interval_s
    tpm, rpm = tps * 60 * tokens_per_turn, tps * 60 * calls_per_turn
    return Capacity(
        tps, tps * turn_seconds, tpm, rpm, math.ceil(max(tpm / quota.tpm, rpm / quota.rpm))
    )


def monthly_cost(students: int, turns_per_student: int, model: str, year: int) -> Decimal:
    return students * turns_per_student * cost_usd(TURN_USAGE, model, date(year, 6, 1))
