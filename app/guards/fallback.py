"""Degradação por frase, fallback pelo domínio e limpeza de Markdown/códigos (spec §6.4)."""

import re
from collections.abc import Mapping, Sequence
from typing import Literal

from app.config import load_slo_targets
from app.guards.sentences import split_sentences
from app.guards.types import FailureKind, GuardResult
from app.tools.base import Domain, EntityKind, EntityRecord
from core.ontology import Ontology

# Igual a `Ontology.service("GEN-090").ui_label` (testado): o fallback não recebe a ontologia.
SECRETARIAT_LABEL = "Falar com a secretaria"

DEGRADE_NOTICES: Mapping[FailureKind, str] = {
    "money": "Não consegui confirmar um dos valores; confira no seu cartão ou na secretaria.",
    "date": "Não consegui confirmar uma das datas; confira no seu cartão ou na secretaria.",
    "period": "Não consegui confirmar um dos períodos citados.",
    "pct": "Não consegui confirmar um dos percentuais.",
    "decimal": "Não consegui confirmar uma das notas ou médias.",
    "int": "Não consegui confirmar uma das quantidades.",
    "small_int": "Não consegui confirmar uma das quantidades.",
    "id": "Não consegui confirmar um dos números de documento ou protocolo.",
    "cpf": "Retirei um documento pessoal que não consegui confirmar.",
    "cnpj": "Retirei um documento que não consegui confirmar.",
    "email": "Retirei um e-mail que não consegui confirmar.",
    "phone": "Retirei um telefone que não consegui confirmar.",
    "url": "Retirei um link que não posso confirmar.",
    "opaque": "Retirei um código ou endereço que não posso confirmar.",
    "status": "Não consegui confirmar a situação de um item.",
    "polarity": "Não consegui confirmar se há ou não pendências.",
    "action": "Não realizei nenhuma ação em seu nome nesta conversa.",
    "commitment": "Não posso prometer condições que não estejam no seu cartão ou nas orientações.",
    "entity_without_record": "Não tenho dados sobre um dos itens citados.",
    "length": "A resposta foi encurtada.",
    "canary": "Parte da resposta foi retirada.",
    "prompt_ngram": "Parte da resposta foi retirada.",
}


def degrade(answer: str, result: GuardResult) -> str | None:
    """Remove as frases reprovadas e acrescenta o aviso de cada tipo; None se não sobrar nada."""
    bad = {f.sentence for f in result.failures}
    kept = [s for i, s in enumerate(split_sentences(answer)) if i not in bad]
    if "length" in result.kinds():
        cap, out, n = load_slo_targets().limits["default"].answer_chars, [], 0
        for s in kept:
            n += len(s) + 1
            if n > cap:
                break
            out.append(s)
        kept = out
    if not kept:
        return None
    notices = dict.fromkeys(DEGRADE_NOTICES[k] for k in DEGRADE_NOTICES if k in result.kinds())
    return " ".join([*kept, *notices])


_DOMAIN_OF: Mapping[EntityKind, Domain] = {
    "matricula": "academico", "disciplina": "academico", "prova": "academico",
    "calendario": "academico", "financeiro": "financeiro", "fatura": "financeiro",
    "acordo": "financeiro", "desconto": "financeiro", "protocolo": "protocolos",
    "documento": "protocolos", "lms": "lms", "servico": "catalogo", "curso": "catalogo",
}  # fmt: skip


def domain_of(kind: EntityKind) -> Domain | None:
    return _DOMAIN_OF.get(kind)


def _line(r: EntityRecord) -> str:
    body = "; ".join(f"{k.replace('_', ' ')}: {v}" for k, v in r.fields.items())
    return f"{r.aliases[0] if r.aliases else r.id} ({r.kind}) - {body}."


def template_fallback(
    domain: Domain | None,
    *,
    records: Sequence[EntityRecord],
    card_facts: Mapping[str, str],
    channel: Literal["web", "a2a"],
    tainted: bool,
) -> str:
    """Texto fixo montado só com dados de registro; sem domínio, só a frase genérica."""
    rows = [_line(r) for r in records if domain and _DOMAIN_OF.get(r.kind) == domain][:5]
    if domain == "financeiro" and not rows:
        rows = [
            f"Matrícula {k.split('.')[0]}: total para quitar {v}."
            for k, v in card_facts.items()
            if k.endswith(".total_para_quitar")
        ]
    head = "Não consegui montar uma resposta confiável para isso agora."
    text = head + (" Estes são os dados que tenho: " + " ".join(rows) if rows else "")
    if channel == "web" and not tainted:
        text += f" Se precisar, use o botão {SECRETARIAT_LABEL}."
    return text


_CODE = re.compile(r"\b[A-Z]{3}-\d{3}\b")


def strip_markdown_and_codes(text: str, ontology: Ontology) -> str:
    """Remove Markdown e troca códigos de serviço (DOC-040) pelo nome em português."""
    t = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    t = re.sub(r"^\s{0,3}(?:#{1,6}|>|[-*+])\s+", "", t, flags=re.M)
    t = re.sub(r"[*_`]{1,3}", "", t)
    codes = set(ontology.service_codes())
    return _CODE.sub(lambda m: ontology.service(m[0]).name_pt if m[0] in codes else m[0], t)
