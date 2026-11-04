"""Capacidade e custo mensal (spec §6.5): contas puras, sem I/O."""

import math
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.agent.llm import Usage
from app.config import Quota, load_slo_targets
from app.obs.cost import cost_usd

# Turno médio (~4.700 tokens): `model_constants.turn_usage` do slo_targets.yaml (refit na janela A).
_TU = load_slo_targets().model_constants["turn_usage"]
TURN_USAGE = Usage(
    input_tokens=_TU["input"], output_tokens=_TU["output"], thinking_tokens=_TU["thinking"]
)


@dataclass(frozen=True)
class Capacity:
    turns_per_s: float
    in_flight: float
    tpm: float
    rpm: float
    replicas: int  # cotas (projetos) necessárias para absorver a carga


def capacity(
    active_students: int, msg_interval_s: float, tokens_per_turn: int, turn_seconds: float,
    quota: Quota, *, calls_per_turn: float | None = None, quota_fraction: float | None = None,
) -> Capacity:  # fmt: skip
    """Por padrão, chamadas por turno (`admission.est_requests`) e fração da cota usável
    (`admission.quota_fraction`) vêm do slo_targets.yaml: o balde só admite essa fração."""
    adm = load_slo_targets().admission
    calls = adm.est_requests if calls_per_turn is None else calls_per_turn
    frac = adm.quota_fraction if quota_fraction is None else quota_fraction
    tps = active_students / msg_interval_s
    tpm, rpm = tps * 60 * tokens_per_turn, tps * 60 * calls
    replicas = math.ceil(max(tpm / (quota.tpm * frac), rpm / (quota.rpm * frac)))
    return Capacity(tps, tps * turn_seconds, tpm, rpm, replicas)


def monthly_cost(students: int, turns_per_student: int, model: str, year: int) -> Decimal:
    return students * turns_per_student * cost_usd(TURN_USAGE, model, date(year, 6, 1))
