from collections.abc import Mapping
from typing import Any

from app.data.queries import catalog as q
from app.tools.base import (
    ENROLLMENT,
    EntityRecord,
    Tool,
    ToolContext,
    ToolError,
    ToolOutput,
    ToolSpec,
    _default_ontology,
    _norm,
    resolve_enrollment,
    schema,
    service_record,
)
from core.format import fmt_brl, fmt_date

MAX_ITEMS = 20
_KINDS = ["curso", "grade", "disciplina", "calendario", "unidade", "servico", "preco"]
_CODE = {"presencial": "CAMPUS", "ead": "DL"}
_UNIT_TYPE = {"CAMPUS": "Campus", "POLO": "Polo"}
_MODALITY_PT = {"presencial": "Presencial", "ead": "EAD"}
_NAO_OFERECIDO = "não oferecido"


def _match(query: str | None, *names: str) -> bool:
    return not query or _norm(query) in _norm(" ".join(names))


def _page(items: list[Any], what: str, query: str | None) -> dict[str, Any]:
    """Até MAX_ITEMS + total; lista vazia diz que está vazia (nunca só '0')."""
    out: dict[str, Any] = {"total": len(items)}
    if not items:
        out["mensagem"] = f"Nenhum(a) {what} encontrado(a)" + (
            f' para "{query}".' if query else "."
        )
    return out


def _disciplina(row: tuple[Any, ...]) -> tuple[dict[str, Any], EntityRecord]:
    code, name, hours, credits, semester, mandatory = row
    inside = semester is not None
    d: dict[str, Any] = {"disciplina": name, "horas": int(hours), "creditos": int(credits),
                         "na_sua_matriz": inside}  # fmt: skip
    f = {"na_sua_matriz": "sim" if inside else "não", "horas": str(int(hours))}
    if inside:
        d["semestre"], d["obrigatoria"] = int(semester), bool(mandatory)
        f["semestre"] = str(int(semester))
    # id com prefixo: não colide com o registro da disciplina cursada (ferramentas acadêmicas)
    return d, EntityRecord("disciplina", f"catalogo-{code}", (name,), f)


async def get_catalog(ctx: ToolContext, args: Mapping[str, Any]) -> ToolOutput:
    kind: str = args["kind"]
    if kind not in _KINDS:
        raise ToolError("ARGUMENTO_INVALIDO")
    query: str | None = args.get("query")
    ref = resolve_enrollment(ctx.scope, args.get("enrollment"), None)
    modality = args.get("modality") or ref.modality
    family = ref.brand_family
    onto = _default_ontology()
    data: dict[str, Any] = {"marca": onto.label_pt("brand_family", family)}
    records: list[EntityRecord] = []

    if kind == "servico":
        codes = [c for c in onto.service_codes()
                 if _match(query, onto.service(c).name_pt, onto.service(c).ui_label)]  # fmt: skip
        items = []
        for c in codes[:MAX_ITEMS]:
            s, rec = onto.service(c), service_record(c, onto)
            items.append({"servico": s.name_pt, "rotulo_na_interface": s.ui_label,
                          "o_agente_abre_o_pedido": s.mode == "agent", **rec.fields})  # fmt: skip
            records.append(rec)
        return ToolOutput(
            {**data, "servicos": items, **_page(codes, "serviço", query)}, tuple(records)
        )

    async with ctx.db() as conn:
        if kind in ("curso", "preco"):
            code = _CODE[modality]
            courses = [c for c in await q.courses(conn) if _match(query, c[2], c[1])]
            offers = await q.offers(conn, family, code)
            prices = await q.prices(conn, family, code, ctx.today.year) if kind == "preco" else {}
            data["modalidade"] = _MODALITY_PT[modality]
            rows, absent = [], []
            for course_id, acronym, name, _degree in courses:
                units = offers.get(course_id)
                f: dict[str, str] = {"oferta": "oferecido" if units else _NAO_OFERECIDO}
                row: dict[str, Any] = {"curso": name, "sigla": acronym, "oferta": f["oferta"]}
                if units and kind == "curso":
                    row["unidades"] = f["unidades"] = "; ".join(units)
                if units and kind == "preco" and (p := prices.get(course_id)):
                    row |= {
                        "mensalidade": fmt_brl(p[0]),
                        "ano": p[1],
                        "parcelas_por_semestre": p[2],
                    }
                    f |= {"mensalidade": row["mensalidade"], "ano": str(p[1]),
                          "parcelas_por_semestre": str(p[2])}  # fmt: skip
                if kind == "preco" and not units:
                    absent.append(name)
                else:
                    rows.append(row)
                records.append(EntityRecord("curso", acronym, (name, acronym), f))
            if kind == "preco":
                data |= {"precos": rows[:MAX_ITEMS], "nao_oferecidos": absent,
                         **_page(rows, "preço", query)}  # fmt: skip
                records = [r for r in records if r.fields["oferta"] != _NAO_OFERECIDO or not rows]
            else:
                data |= {"cursos": rows[:MAX_ITEMS], **_page(rows, "curso", query)}
            records = records[:MAX_ITEMS]
        elif kind in ("disciplina", "grade"):
            curriculum_id, course_id = await q.curriculum_of(conn, ref.enrollment_key)
            rows_ = [r for r in await q.subjects(conn, curriculum_id) if _match(query, r[1])]
            if kind == "grade":
                rows_ = sorted((r for r in rows_ if r[4] is not None), key=lambda r: (r[4], r[1]))
            built = [_disciplina(r) for r in rows_]
            key = "disciplinas"
            data |= {key: [d for d, _ in built[:MAX_ITEMS]], **_page(built, "disciplina", query)}
            records = [r for _, r in built[:MAX_ITEMS]]
        elif kind == "calendario":
            terms = [t for t in await q.terms(conn) if _match(query, t[0])]
            data |= {
                "periodos": [
                    {
                        "periodo": t[0],
                        "inicio": fmt_date(t[1]),
                        "fim": fmt_date(t[2]),
                        "av1": fmt_date(t[3]),
                        "av2": fmt_date(t[4]),
                        "atual": bool(t[5]),
                    }
                    for t in terms[:MAX_ITEMS]
                ],  # fmt: skip
                **_page(terms, "período", query),
            }
            records = [
                EntityRecord(
                    "calendario",
                    t[0],
                    (t[0],),
                    {
                        "av1": fmt_date(t[3]),
                        "av2": fmt_date(t[4]),
                        "inicio": fmt_date(t[1]),
                        "fim": fmt_date(t[2]),
                    },
                )  # fmt: skip
                for t in terms[:MAX_ITEMS]
            ]
        else:  # unidade
            brand = family + "_EAD" if modality == "ead" else family
            us = [u for u in await q.units(conn, family, brand) if _match(query, u[0])]
            data["modalidade"] = _MODALITY_PT[modality]
            data |= {
                "unidades": [
                    {"unidade": u[0], "tipo": _UNIT_TYPE.get(u[1], u[1]), "uf": u[2]}
                    for u in us[:MAX_ITEMS]
                ],  # fmt: skip
                **_page(us, "unidade", query),
            }
    return ToolOutput(data, tuple(records))


TOOLS: tuple[Tool, ...] = (
    Tool(
        ToolSpec(
            "get_catalog",
            "Consulta o catálogo da marca: cursos, grade, disciplinas, preços, calendário, "
            "unidades e serviços.",
            schema(
                {
                    "kind": {"type": "string", "enum": _KINDS},
                    "query": {"type": "string", "description": "Termo de busca"},
                    "modality": {"type": "string", "enum": ["presencial", "ead"]},
                    "enrollment": ENROLLMENT,
                },
                ("kind",),
            ),
        ),
        get_catalog,
        "catalogo",
    ),
)
