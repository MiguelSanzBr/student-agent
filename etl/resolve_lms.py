"""Resolução das contas LMS (Moodle) para matrículas (marca, RA).

SSO: `student_id` (trim/upper, `^(PRES|EAD)_\\d{5}$` = RA sem DV) + tenant -> marca.
Manual: o login citado nos eventos `user_loggedin` (nunca o nome). Ambíguo não liga.
"""

import json
import re
from collections.abc import Iterable, Mapping

from core.normalize import lms_login
from etl.resolve_core import Enrollment, EnrollmentKey

Row = Mapping[str, str]

# shortcut: mapa estático tenant -> família (testado contra mdl_tenant.csv); atualizar se mudar.
_TENANT_FAMILY = {
    1: "UniSaoPaulo",
    2: "UniMontanhas",
    3: "UniLitoral",
    4: "UniAtlantico",
    5: "UniAraucaria",
}
_NON_STUDENT = re.compile(r"(prof\d+|admin|integration\..*|support\..*|aluno\.teste\d*)")


def tenant_brand(tenant_id: int, modality: str) -> str:
    """tenant + modalidade (`EAD`, senão presencial) -> BRAND. KeyError se tenant desconhecido."""
    family = _TENANT_FAMILY[tenant_id]
    return f"{family}_EAD" if modality.strip().upper() == "EAD" else family


def is_student_login(s: str) -> bool:
    return _NON_STUDENT.fullmatch(s.strip().lower()) is None


def _link(
    tenant: int, login: str, by_prefix: Mapping[tuple[str, str], list[EnrollmentKey]]
) -> EnrollmentKey | None:
    modality, ra5 = login.split("_")
    try:
        brand = tenant_brand(tenant, modality)
    except KeyError:
        return None
    hits = by_prefix.get((brand, ra5), [])
    return hits[0] if len(hits) == 1 else None


def resolve_lms_accounts(
    users: Iterable[Row], logs: Iterable[Row], enrollments: Mapping[EnrollmentKey, Enrollment]
) -> dict[int, tuple[str, str]]:
    """student_lms_id -> (brand, ra). Contas não-aluno, sem login ou ambíguas ficam de fora."""
    by_prefix: dict[tuple[str, str], list[EnrollmentKey]] = {}
    for key in enrollments:
        by_prefix.setdefault((key[0], key[1][:5]), []).append(key)

    users = [u for u in users if is_student_login(u["username"])]
    manual = {int(u["student_lms_id"]) for u in users if not u["student_id"].strip()}
    logins: dict[int, set[str]] = {}
    for ev in logs:
        uid = int(ev["userid"])
        if uid in manual and ev["eventname"].endswith("user_loggedin"):
            name = lms_login(json.loads(ev["other"]).get("username", ""))
            if name:
                logins.setdefault(uid, set()).add(name)

    out: dict[int, tuple[str, str]] = {}
    for u in users:
        uid, tenant = int(u["student_lms_id"]), int(u["tenant_id"])
        if u["student_id"].strip():
            sso = lms_login(u["student_id"])
            names = {sso} if sso else set()
        else:
            names = logins.get(uid, set())
        if len(names) != 1:
            continue
        target = _link(tenant, next(iter(names)), by_prefix)
        if target:
            out[uid] = target
    return out
