"""Prazo e status de protocolos."""

import math
from datetime import date, timedelta

_STATUS_PT = {
    "Open": "aberto",
    "In Analysis": "em análise",
    "Pending Requester": "aguardando você",
    "Resolved": "resolvido",
    "Closed": "encerrado",
    "Cancelled": "cancelado",
}


def sla_due(opened: date, sla_hours: int) -> date:
    return opened + timedelta(days=math.ceil(sla_hours / 24))


def protocol_status_pt(status: str) -> str:
    return _STATUS_PT.get(status, status)
