"""EvidenceGuard determinístico: cada frase do LLM contra os registros citados (spec §6.4).

Só confere texto gerado pelo LLM. Limites conhecidos (shortcut): polaridade só pela grade
negador x substantivo; resposta curta só afirmativa (a negativa não importa a pergunta);
presente do indicativo não conta como ação (ambíguo com substantivo: "registro").
"""

import re
import unicodedata
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import cache
from itertools import islice, product

from app.config import load_slo_targets
from app.guards.fallback import domain_of
from app.guards.sentences import split_sentences
from app.guards.tokens import Tok, tokenize
from app.guards.types import Failure, FailureKind, GuardResult
from app.rules.commitments import commitment_sources, conditions_in
from app.tools.base import Domain, EntityRecord, ToolCallTrace, find_entity
from core.ontology import Ontology

_SA = re.compile(r"SA-\d{4}-\d{6}")
_REFUSAL = {"dados", "registros", "registro", "informacao", "informacoes", "consigo", "posso",
            "sei", "previsao", "acesso", "disponivel", "encontrei", "localizei"}  # fmt: skip
_LIGHT = {"tem", "ha", "existe", "existem", "possui", "ter", "mais", "um", "uma", "o", "a", "as",
          "os", "de", "restou", "resta", "restam", "sobrou", "sobra", "consta", "constam", "apresenta", "da", "do", "das", "dos", "qualquer", "pendente", "pendentes", "em",  # noqa: E501
          "aberto", "nem", "e", "eh", "esta", "ficou", "igual", "total", "atual", "valor", "foi",
          "sera", "com", "para", "nenhum", "nenhuma", "voce", "seu", "sua"}  # fmt: skip
_FIN_NOUNS = {"fatura", "boleto", "mensalidade", "parcela", "pagamento", "deve", "dever", "devo",
              "devemos", "devem"}  # fmt: skip
_PROMISE = re.compile(
    r"\b(pode|podem|podemos|posso|poderia|podera|poderao|vai|vamos|sera|serao|tera|terao|"
    r"consegue|conseguira|consigo|conseguimos|ficara|fica|garant\w*|prometo|oferec\w+|"
    r"concedemos|concedo|conceder|liberamos|libero|liberar|damos|dou|dar|da para|da pra|"
    r"e possivel|possivel|ganha|ganhara|estendemos|prorrogamos|perdoamos|isentamos|"
    r"tem direito|parcele|negocie|pague|solicite|peca)\b"
)
# "Em 6 vezes." solto responde "como posso pagar?": é promessa sem verbo
_INSTALLMENT_FRAGMENT = re.compile(r"\W*(?:em|ate)\s+(?:ate\s+)?\d{1,2}\s*(?:x|vezes|parcelas)\b")
_STATUS: Mapping[str, str] = {
    "aprovado": r"aprovad[oa]s?|passou|passei|passamos|passaram",
    "reprovado": r"reprovad[oa]s?|reprovou|reprovei|reprovaram",
    "em analise": r"em analise",
    "aberto": r"abert[oa]s?",
    "aguardando voce": r"aguardando (?:voce|retorno)",
    "ativo": r"ativ[oa]s?",
    "rompido": r"rompid[oa]s?|quebrad[oa]s?",
    "quitado": r"quitad[oa]s?|pag[oa]s?",
    "parcial": r"parcial(?:mente)?",
    "trancado": r"trancad[oa]s?",
    "cancelado": r"cancelad[oa]s?",
    "concluido": r"conclu[ií]d[oa]s?",
    "vigente": r"vigentes?",
    "vencido": r"vencid[oa]s?",
    "pendente": r"pendentes?",
}  # fmt: skip
_STATUS_RX = {k: re.compile(rf"(?<![a-z0-9])(?:{v})(?![a-z0-9])") for k, v in _STATUS.items()}
_PASSIVE_OPEN = re.compile(r"(?:foi|foram|sido|sendo|sera|serao)\s+abert[oa]s?")
_KEYWORD_DOMAIN: tuple[tuple[Domain, str], ...] = (
    ("financeiro", r"divida|saldo|fatura|boleto|mensalidade|pagar|quitar|parcel|acordo|desconto|bolsa|deve"),  # noqa: E501
    ("protocolos", r"protocolo|pedido|solicitac|documento|declarac"),
    ("lms", r"\bava\b|\blms\b|simulado|acesso|tentativa"),
    ("academico", r"nota|media|frequencia|aprovad|reprovad|disciplina|previsao|conclusao|formatura|matricula"),  # noqa: E501
    ("catalogo", r"curso|catalogo|oferta"),
)  # fmt: skip


def fold(s: str) -> str:
    s = unicodedata.normalize("NFKC", s)
    return "".join(
        "".join(
            x for x in unicodedata.normalize("NFD", c.casefold()) if not unicodedata.combining(x)
        )
        for c in s
    )


@dataclass(frozen=True)
class _Lex:
    negators: frozenset[str]
    zeros: frozenset[str]
    nouns: frozenset[str]
    debt: frozenset[str]
    confirm: frozenset[str]
    strong: re.Pattern[str]
    debt_rx: re.Pattern[str]
    service_rx: re.Pattern[str]
    subject_rx: re.Pattern[str]
    action_finite: re.Pattern[str]
    action_participle: re.Pattern[str]
    phrases: tuple[str, ...]


def _f(xs: Sequence[str]) -> list[str]:
    return [fold(x) for x in xs]


def _alt(terms: Sequence[str]) -> str:
    return "|".join(re.escape(t) for t in sorted(terms, key=len, reverse=True))


def _forms(lemma: str) -> tuple[list[str], list[str]]:
    """(finitas, particípios) de verbo regular em -ar/-ir; sem presente, infinitivo e gerúndio."""
    s, ending = lemma[:-2], lemma[-2:]
    if ending == "ar":
        first = s[:-1] + "quei" if s.endswith("c") else s + "uei" if s.endswith("g") else s + "ei"
        fin = [first, s + "ou", s + "amos", s + "aram", s + "arei", s + "ara", s + "aremos",
               s + "arao"]  # fmt: skip
        part = [s + "ad" + e for e in ("o", "a", "os", "as")]
    else:
        fin = [s + e for e in ("i", "iu", "imos", "iram", "irei", "ira", "iremos", "irao")]
        part = [s + "id" + e for e in ("o", "a", "os", "as")]
        if lemma == "abrir":
            part = ["abert" + e for e in ("o", "a", "os", "as")]
    if lemma in ("aprovar", "quitar", "pagar", "abrir"):
        part = []  # status de registro ("está aberto") ou tratado à parte (abrir)  # "aprovado"/"quitado"/"pago" são status de registro  # noqa: E501
    return fin, part


@cache
def _lex(ont: Ontology) -> _Lex:
    lx = ont.guard_lexicon()
    lemmas = _f(lx.action_lemmas)
    fin: list[str] = []
    part: list[str] = []
    for lem in lemmas:
        a, pp = _forms(lem)
        fin += a
        part += pp
    inf = [re.escape(lem) for lem in lemmas]
    b = r"(?<![a-z0-9])"
    action_finite = re.compile(
        rf"{b}(?:{_alt(fin)}|(?:vou|vamos|irei|iremos)\s+(?:\w+\s+)?(?:{'|'.join(inf)})|"
        rf"(?:estou|estamos)\s+\w+ndo)(?![a-z0-9])"
    )
    aux = r"(?:foi|foram|esta|estao|sido|ficou|ficaram)\s+(?:ja\s+|tambem\s+|devidamente\s+)?"
    ev = r"(?:foi|foram|sido)\s+(?:ja\s+)?abert[oa]s?" if "abrir" in lemmas else r"(?!x)x"
    action_participle = re.compile(rf"{b}(?:{aux}(?:{_alt(part)})|{ev})(?![a-z0-9])")
    names = [fold(ont.service(c).name_pt) for c in ont.service_codes()]
    neg = frozenset(_f(lx.negators))
    zero = frozenset(_f(lx.zero_words)) | {"zeromoney"}
    nouns = frozenset(_f(lx.entity_nouns + lx.debt_nouns))
    debt = frozenset(_f(lx.debt_nouns)) | _FIN_NOUNS
    phrases = tuple(sorted({t for t in (*neg, *zero, *nouns) if " " in t}, key=len, reverse=True))
    return _Lex(
        neg, zero, nouns, debt, frozenset(_f(lx.confirmation_words)),
        re.compile(rf"{b}(?:{_alt(_f(lx.commitment_terms))})(?![a-z0-9])"),
        re.compile(rf"{b}(?:{_alt(_f(lx.debt_nouns))}|dev(?:e|o|emos|em|endo|ia|iam))(?![a-z0-9])"),
        re.compile(rf"{b}(?:{_alt(names)})(?![a-z0-9])"),
        re.compile(rf"{b}(?:{_alt([fold(s) for s in ont.subject_names_pt()])})(?![a-z0-9])"),
        action_finite, action_participle, phrases,
    )  # fmt: skip


Pool = dict[str, set[str]]


def _pool(texts: Sequence[str]) -> Pool:
    p: Pool = {}
    for t in texts:
        for k in tokenize(t):
            p.setdefault(k.kind, set()).add(k.norm)
            if k.kind in ("int", "small_int"):
                p.setdefault("int", set()).add(k.norm)
            if k.kind == "date":
                p.setdefault("ddmm", set()).add(k.norm[:5])
                if len(k.norm) > 5:
                    p.setdefault("period", set()).add(k.norm[6:])
            if k.kind == "period":
                p.setdefault("period", set()).add(k.norm[:4])
    return p


def _period_pool(args: Sequence[str]) -> Pool:
    """Argumentos de ferramenta só sustentam período (AAAA ou AAAA/N), nunca valores."""
    return {k: v for k, v in _pool(args).items() if k == "period"}


def _rec_texts(r: EntityRecord) -> list[str]:
    return [r.id, *r.aliases, *r.fields.values()]


def _sentence_domain(text: str, cited: Sequence[EntityRecord]) -> Domain | None:
    for r in cited:
        if d := domain_of(r.kind):
            return d
    f = fold(text)
    return next((d for d, rx in _KEYWORD_DOMAIN if re.search(rx, f)), None)


_STATE_ONLY = ("vencido", "pendente")  # só contam como status depois de "está/ficou"
_STATE_RX = {
    k: re.compile(rf"(?:esta|estao|ficou|ficaram)\s+(?:ja\s+)?(?:{_STATUS[k]})(?![a-z0-9])")
    for k in _STATE_ONLY
}


def _statuses(f: str) -> set[str]:
    f = _PASSIVE_OPEN.sub(" ", f)
    out = {k for k, rx in _STATUS_RX.items() if k not in _STATE_ONLY and rx.search(f)}
    return out | {k for k, rx in _STATE_RX.items() if rx.search(f)}


def _canon_status(value: str) -> str | None:
    v = fold(value)
    return next((k for k, rx in _STATUS_RX.items() if rx.fullmatch(v)), None)


def _zero_like(v: str) -> bool:
    return bool(re.fullmatch(r"(?:r\$\s?)?0(?:[.,]0+)?|nao|nenhum|nenhuma|zero", fold(v).strip()))


@dataclass
class _Ctx:
    records: Mapping[str, EntityRecord]
    card: Mapping[str, str]
    enrollments: tuple[str, ...]
    notes: tuple[str, ...]
    question: str
    pool: Pool
    base_pool: Pool
    calls: tuple[ToolCallTrace, ...]
    sources: frozenset[str]
    lex: _Lex
    ont: Ontology


def _merge(a: Pool, b: Pool) -> Pool:
    return {k: a.get(k, set()) | b.get(k, set()) for k in a.keys() | b.keys()}


# substantivo -> (padrão do nome da ferramenta, tipos de registro) usados na polaridade vazia
_NOUN_SOURCES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        r"protocolo|documento|declarac|historico|diploma|pedido|solicitac",
        r"protocol",
        ("protocolo",),
    ),
    (r"fatura|boleto|mensalidade|parcela|pagamento", r"invoice|financial", ("fatura",)),
    (r"acordo", r"agreement", ("acordo",)),
    (r"desconto|bolsa", r"financial|discount|agreement", ("desconto",)),
    (r"simulado|prova|avaliac|acesso|atividade|tentativa", r"lms|course_results", ("prova", "lms")),
    (r"nota|disciplina|frequencia", r"course_results|academic", ("disciplina",)),
    (r"curso|matriz|servico", r"catalog|curriculum", ("curso", "servico")),
)  # noqa: E501


_EXIST_NEG = {"nenhum", "nenhuma"}
_EXISTENCE = {"ha", "existe", "existem", "tem", "possui", "possuem", "temos", "resta", "restam"}
_NOT_A_NOUN = {"como", "que", "se", "ainda", "nada", "porque", "de"}
_DETERMINERS = {"outro", "outra", "outros", "outras"}  # o núcleo é a palavra seguinte
# núcleos abstratos (conversa, não dado); lista mínima e testada. Com sinal de dado depois
# ("pendente", "para você", "seu") voltam a ser afirmação de ausência.
_ABSTRACT = {"necessidade", "necessidades", "motivo", "motivos", "duvida", "duvidas", "opcao",
             "opcoes", "caminho", "caminhos", "acao", "acoes", "limite", "limites", "problema",
             "problemas", "pressa", "urgencia", "preocupacao", "preocupacoes"}  # fmt: skip
_PREP = {"de", "da", "do", "das", "dos", "para", "pra"}
_ARTICLES = {"o", "a", "os", "as", "um", "uma"}
_DATA_SIGNAL = re.compile(
    r"\b(?:abert[oa]s?|marcad[oa]s?|agendad[oa]s?|disponive(?:l|is)|pendentes?|ativ[oa]s?|"
    r"vigentes?|vencid[oa]s?|seus?|suas?|pa?ra voce)\b"
)
# taxa/prazo zero são regra do serviço: só o registro do serviço sustenta
_SERVICE_FIELD = {"custo": "taxa", "taxa": "taxa", "tarifa": "taxa", "prazo": "prazo",
                  "limite": "limite"}  # fmt: skip
_NEUTRAL_ARGS = {"enrollment", "enrollment_index", "matricula", "limit", "page", "offset"}


def _is_noun(w: str) -> bool:
    return bool(w) and w not in _NOT_A_NOUN and not w.endswith(("ar", "er", "ir"))  # infinitivo


def _abstract_claim(words: Sequence[str], k: int) -> str | None:
    """Núcleo abstrato em words[k]: None se é conversa; senão o que a frase diz não existir."""
    rest = words[k + 1 :]
    if _DATA_SIGNAL.search(" ".join(rest[:4])):
        return words[k]  # "nenhuma ação pendente para você"
    if not rest or rest[0] not in _PREP:
        return None
    y = next((w for w in rest[1:] if w not in _ARTICLES and w not in _DETERMINERS), "")
    if not _is_noun(y) or y in _ABSTRACT:
        return None  # "necessidade de ir", "dúvida de que", "motivo para preocupação"
    return "limite" if words[k].startswith("limite") else y  # "opções de estágio" -> estágio


def _narrowed_away(args: Mapping[str, str], f: str) -> bool:
    """Filtro da chamada que a frase não repete: a chamada vazia não fala do que a frase diz."""
    words = re.findall(r"[a-z0-9/]+", f)
    for k, v in args.items():
        fv = fold(v).strip()
        if k in _NEUTRAL_ARGS or not fv:
            continue
        if fv in f or any(w.startswith(fv[: max(4, len(fv) - 2)]) for w in words):
            continue
        return True
    return False


def _empty_backed(c: _Ctx, noun: str, cited: Sequence[EntityRecord], f: str) -> bool:
    """Ausência sustentada: sem contagem no cartão, sem registro do tipo e chamada vazia sem filtro."""  # noqa: E501
    stem = noun.rstrip("s")
    tool_rx, kinds = next(((t, k) for n, t, k in _NOUN_SOURCES if re.search(n, stem)), (None, ()))
    card_vals = [v for k, v in c.card.items() if stem in fold(k)]
    if any(v.strip() and not _zero_like(v) for v in card_vals):
        return False  # o cartão diz que existe
    vals = [v for r in cited for v in r.fields.values()] + card_vals
    of_kind = [r for r in c.records.values() if r.kind in kinds or stem in fold(r.kind)]
    vals += [v for r in of_kind for v in r.fields.values()]
    if any(_zero_like(v) or fold(v) in ("pago", "quitado") for v in vals):
        return True
    return bool(
        tool_rx
        and not of_kind
        and any(
            tc.returned_records == 0
            and re.search(tool_rx, tc.name)
            and not _narrowed_away(tc.args, f)
            for tc in c.calls
        )
    )


def _service_backed(c: _Ctx, field: str, cited: Sequence[EntityRecord]) -> bool:
    """Taxa zero exige `taxa` zerada; sem prazo, registro sem `prazo` (citados, ou do turno)."""
    svc = [r for r in cited if r.kind == "servico"]
    svc = svc or [r for r in c.records.values() if r.kind == "servico"]
    if field == "limite":  # só um registro que diga "sem limite" sustenta
        ok = [bool(re.search(r"sem limite|ilimitad", fold(r.fields.get(field, "")))) for r in svc]
    else:
        missing = "0" if field == "prazo" else ""
        ok = [_zero_like(r.fields.get(field, missing)) for r in svc]
    return bool(ok) and all(ok)


def _total_pool(c: _Ctx, enrs: Sequence[str]) -> set[str]:
    texts = [v for k, v in c.card.items() if k.endswith(".total_para_quitar")
             and (not enrs or k.split(".")[0] in enrs)]  # fmt: skip
    texts += [r.fields["total_para_quitar"] for r in c.records.values()
              if "total_para_quitar" in r.fields and (not enrs or r.id in enrs)]  # fmt: skip
    return _pool(texts).get("money", set())


def _fin_zero(c: _Ctx, enrs: Sequence[str], cited: Sequence[EntityRecord], vencida: bool) -> bool:
    others = [r for r in cited if r.kind in ("fatura", "acordo")]
    if others and not enrs:
        return all(
            _zero_like(r.fields.get("saldo", "x")) or _canon_status(r.fields.get("status", "")) == "quitado"  # noqa: E501
            for r in others
        )  # fmt: skip
    for n in enrs or c.enrollments:
        fin = c.records.get(f"financeiro:{n}")

        def get(k: str, n: str = n, fin: EntityRecord | None = fin) -> str | None:
            return c.card.get(f"{n}.{k}") or (fin.fields.get(k) if fin else None)

        venc, tot = get("faturas_vencidas"), get("total_para_quitar")
        v = venc if vencida and venc is not None else tot
        if v is None or not _zero_like(v):
            return False
    return True


def _polarity_ok(c: _Ctx, text: str, cited: Sequence[EntityRecord], enrs: set[str]) -> bool:
    """False se a frase afirma ausência/zero que os fatos não sustentam."""
    lx = c.lex
    f = re.sub(r"r\$\s?0,00|(?<![\d,])0,00", " zeromoney ", fold(text))
    for p in lx.phrases:
        f = f.replace(p, p.replace(" ", "_"))
    neg = {n.replace(" ", "_") for n in lx.negators}
    zero = {z.replace(" ", "_") for z in lx.zeros}
    nouns = {n.replace(" ", "_") for n in lx.nouns}
    debt = {n.replace(" ", "_") for n in lx.debt}
    words = re.findall(r"[a-z0-9_]+", f)

    def stem(w: str) -> str:
        return w if w in nouns else w.rstrip("s")

    claims: list[str] = []
    typed = any(r.fields.get("status") or r.fields.get("resultado") for r in cited)
    if re.search(r"nada a pagar|nada devendo|nenhum valor (?:a pagar|em aberto)|nada em aberto", f):
        claims.append("financeiro")
    if not typed and re.search(r"\bquit(?:ad[oa]s?|es?)\b", f):
        claims.append("financeiro")
    for i, w in enumerate(words):
        if w in ("em_dia",) or (w in neg and w.startswith("sem_")):
            claims.append("financeiro")
            continue
        nxt = words[i + 1] if i + 1 < len(words) else ""
        head = next((x for x in words[i + 1 :] if x not in _DETERMINERS), "")
        if w in ("sem", "zero") and len(head) > 3 and head.endswith("s") and head not in _ABSTRACT:
            claims.append(stem(head))  # "Sem (outras) ofertas de estágio.": contável no plural
        if w in neg or w in zero:
            span = words[i + 1 : i + 4] if w in neg else []
            exist = w in _EXIST_NEG or (w == "nada" and nxt == "de" and i == 0)  # "Nada de X."
            for j, x in enumerate(span):
                if stem(x) in nouns or x in debt:
                    claims.append(stem(x))
                    break
                if x in _EXISTENCE:
                    exist = True
                if x in _LIGHT or x in _EXISTENCE or x in _DETERMINERS:
                    continue
                # núcleo fora do léxico: ausência sem apoio (ver _empty_backed/_service_backed)
                y = _abstract_claim(words, i + 1 + j) if x in _ABSTRACT else x
                if exist and y and _is_noun(y):
                    claims.append(stem(y))
                break
            if w in zero:
                for x in reversed(words[max(0, i - 3) : i]):
                    if stem(x) in nouns or x in debt:
                        claims.append(stem(x))
                        break
                    if x not in _LIGHT:
                        break
    if (
        not claims
        and re.search(r"\bzerad[oa]s?\b", f)
        and re.search(r"conta|financeir|mensalidade|cobranca|situacao", f)
    ):
        claims.append("financeiro")
    if not claims or any(x in _REFUSAL for x in words):
        return True
    for noun in claims:
        if noun == "financeiro" or noun in debt or stem(noun) in debt:
            if not _fin_zero(c, sorted(enrs), cited, bool(re.search(r"vencid|atras|inadimpl", f))):
                return False
        elif noun in _SERVICE_FIELD:
            if not _service_backed(c, _SERVICE_FIELD[noun], cited):
                return False
        elif not _empty_backed(c, noun, cited, f):
            return False
    return True


def _check_sentence(c: _Ctx, idx: int, text: str, imported: bool) -> list[Failure]:
    text = unicodedata.normalize("NFKC", text)
    f = fold(text)
    toks = tokenize(text)
    out: list[Failure] = []

    def fail(kind: FailureKind, token: str, entity: str | None = None) -> None:
        out.append(Failure(kind, idx, token, entity))

    # menções de entidade, com o trecho casado (registros no mesmo trecho são alternativas)
    cited: list[EntityRecord] = []
    spans: list[list[tuple[int, int]]] = []
    for r in c.records.values():
        names = [
            *r.aliases,
            *([r.id] if len(r.id) >= 4 and any(ch.isdigit() for ch in r.id) else []),
        ]
        hits = [m.span() for a in names
                for m in re.finditer(rf"(?<!\w){re.escape(fold(a))}(?!\w)", f)]  # fmt: skip
        if hits and r not in cited:
            cited.append(r)
            spans.append(hits)
    enrs = {t.norm for t in toks if t.kind == "enr"}
    for n in sorted(enrs - set(c.enrollments)):
        fail("entity_without_record", f"matrícula {n}")
    for m in c.lex.subject_rx.finditer(f):
        if find_entity(c.records, text[m.start() : m.end()]) is None and not any(
            fold(a) == m[0] for r in cited for a in r.aliases
        ):
            fail("entity_without_record", text[m.start() : m.end()])
    # vale a escolha de registros que sustenta tudo; senão, a que tem menos falhas
    tries = [_claims(c, idx, text, f, toks, enrs, imported, alt)
             for alt in _anchors(cited, spans, f, toks)]  # fmt: skip
    return out + min(tries, key=len)


def _anchors(
    cited: Sequence[EntityRecord], spans: Sequence[Sequence[tuple[int, int]]], f: str,
    toks: Sequence[Tok],
) -> list[list[tuple[EntityRecord, bool]]]:  # fmt: skip
    """Uma escolha de registro por trecho citado; (registro, veio de colisão de alias)."""

    def overlap(i: int, j: int) -> bool:
        return any(s1 < e2 and s2 < e1 for s1, e1 in spans[i] for s2, e2 in spans[j])

    groups: list[list[int]] = []
    for i in range(len(spans)):
        hit = [g for g in groups if any(overlap(i, j) for j in g)]
        groups = [g for g in groups if g not in hit] + [sorted([i, *(x for g in hit for x in g)])]
    keys = [t.norm for t in toks if t.kind == "period"]
    keys += [f"matricula {t.norm}" for t in toks if t.kind == "enr"]
    keys += ["catalogo"] if re.search(r"\bcatalogo\b", f) else []
    options: list[list[tuple[EntityRecord, bool]]] = []
    for g in sorted(groups):
        amb, pick = len(g) > 1, g
        for k in keys:  # desambiguador ("2024/1", "matrícula 2", "catálogo") restringe o trecho
            rx = rf"(?<![\w/]){re.escape(k)}(?![\w/])"
            sub = [i for i in pick if re.search(rx, fold(" ".join(_rec_texts(cited[i]))))]
            pick = sub if sub else pick
        options.append([(cited[i], amb) for i in pick])
    # shortcut: até 64 combinações (frase com muitas colisões); além disso bloqueia, nunca libera
    return [list(x) for x in islice(product(*options), 64)]


def _claims(
    c: _Ctx, idx: int, text: str, f: str, toks: Sequence[Tok], enrs: set[str], imported: bool,
    alt: Sequence[tuple[EntityRecord, bool]],
) -> list[Failure]:  # fmt: skip
    """Afirmações da frase contra uma escolha de registros citados."""
    cited = [r for r, _ in alt]
    out: list[Failure] = []

    def fail(kind: FailureKind, token: str, entity: str | None = None) -> None:
        out.append(Failure(kind, idx, token, entity))

    ent = cited[0].key if cited else None
    masked = c.lex.service_rx.sub(lambda m: " " * len(m[0]), f)

    # fatos
    debt_m = c.lex.debt_rx.search(masked)
    anchored = next(
        (t for t in toks if t.kind == "money" and debt_m and t.pos > debt_m.start()), None
    )
    refusal = bool(
        re.search(
            r"\b(?:nao|nenhum)\b.{0,30}\b(?:dados|registros?|informac\w+|consigo|posso|sei)\b", f
        )
    )
    q_years = {t.norm[:4] for t in tokenize(c.question) if t.kind in ("period", "date")}
    # com entidade citada, só valem os valores dela e os do cartão/notas/argumentos
    ps = _merge(_pool([v for r in cited for v in _rec_texts(r)]), c.base_pool) if cited else c.pool
    for t in toks:
        k, p = t.kind, c.pool
        if k in ("url", "opaque"):
            fail(k, t.raw, ent)  # type: ignore[arg-type]
        elif k in ("email", "cpf", "cnpj", "phone", "id", "decimal", "pct", "int", "date", "money"):
            allowed = ps.get(k, set())
            if k == "money" and t is anchored:
                allowed = _total_pool(c, sorted(enrs)) | _pool(
                    [v for r in cited for v in r.fields.values()]).get("money", set())  # fmt: skip
            if k == "date" and len(t.norm) == 5:
                allowed = ps.get("ddmm", set())
            if t.norm not in allowed:
                fail(k, t.raw, ent)  # type: ignore[arg-type]
        elif k == "period":
            ok = t.norm in p.get("period", set())
            if not ok and refusal and t.norm[:4] in q_years:
                ok = True
            if not ok:
                fail("period", t.raw, ent)
        elif k == "small_int":
            after = re.match(r"\s*([a-z]+)", f[t.pos + len(t.raw) :])
            counting = bool(
                after and (after[1] in c.lex.nouns or after[1].rstrip("s") in c.lex.nouns)
            )
            if cited or enrs or counting:
                if cited or enrs:
                    texts = [v for r in cited for v in _rec_texts(r)]
                    texts += [v for key, v in c.card.items() if key.split(".")[0] in enrs]
                    allowed = _merge(_pool(texts), c.base_pool).get("int", set())
                    if not cited:  # só "matrícula N": cartão e registros dessa matrícula
                        allowed |= _pool([v for r in c.records.values() if r.id in enrs
                                          for v in _rec_texts(r)]).get("int", set())  # fmt: skip
                else:
                    allowed = p.get("int", set())
                if t.norm not in allowed:
                    fail("small_int", t.raw, ent)

    # status no registro citado
    sts = _statuses(masked)
    if sts:
        for r, amb in alt:
            canon = _canon_status(r.fields.get("resultado") or r.fields.get("status") or "")
            if (canon and canon not in sts) or (amb and not canon):  # colisão: tem que sustentar
                fail("status", ",".join(sorted(sts)), r.key)
                break

    if sts and not cited and not re.search(r"\b(?:nao|nenhum\w*|sem|nada)\b", f):
        typed_recs = [
            r for n, _, ks in _NOUN_SOURCES if re.search(n, f)
            for r in c.records.values() if r.kind in ks
        ]  # fmt: skip
        canons = {
            c2
            for r in typed_recs
            if (c2 := _canon_status(r.fields.get("resultado") or r.fields.get("status") or ""))
        }
        if canons and not canons & sts:
            fail("status", ",".join(sorted(sts)), typed_recs[0].key)

    if not imported:
        fin, part = c.lex.action_finite.search(masked), c.lex.action_participle.search(masked)
        if part and ("matricula" in f and re.search(r"trancad|cancelad", part[0])):
            part = None  # estado da matrícula (status do cartão), não ação
        m_act = fin or (part if not any(r.kind == "protocolo" for r in cited) else None)
        if m_act and not any(_SA.search(n) for n in c.notes):
            fail("action", m_act[0], ent)

    # compromisso
    nm = masked
    if strong := c.lex.strong.search(nm):
        fail("commitment", strong[0], ent)
    else:
        conds = set(conditions_in(nm))
        nx = [t.norm for t in toks if t.kind == "nx"]
        if nx:
            conds |= {"parcelamento", *(f"parcelamento {n}x" for n in nx)}
        missing = conds - c.sources
        if "desconto" in conds and any(
            t.kind == "pct" and t.norm not in c.pool.get("pct", set()) for t in toks
        ):
            missing.add("desconto pct")  # a fonte vale para o percentual do registro
        if missing and (imported or _PROMISE.search(nm) or _INSTALLMENT_FRAGMENT.match(nm)):
            fail("commitment", ",".join(sorted(missing)), ent)

    # polaridade ("sem juros" é condição, não polaridade)
    if not _polarity_ok(c, nm.replace("sem juros", " "), cited, enrs):
        fail("polarity", text, ent)
    return out


def _ngrams(text: str, n: int = 8) -> set[tuple[str, ...]]:
    w = re.findall(r"\w+", fold(text))
    return {tuple(w[i : i + n]) for i in range(len(w) - n + 1)}


def check_evidence(
    answer: str,
    *,
    question: str,
    records_by_entity: Mapping[str, EntityRecord],
    card_facts: Mapping[str, str],
    enrollment_indices: tuple[str, ...],
    tool_calls_this_turn: tuple[ToolCallTrace, ...],
    system_notes: tuple[str, ...],
    canary: str,
    prompt_prefix: str,
    ontology: Ontology,
) -> GuardResult:
    lex = _lex(ontology)
    texts = [t for r in records_by_entity.values() for t in _rec_texts(r)]
    texts += [*card_facts.values(), *system_notes]
    args = [v for tc in tool_calls_this_turn if tc.returned_records > 0 for v in tc.args.values()]
    pool = _merge(_pool(texts), _period_pool(args))
    sources = set(commitment_sources(records_by_entity.values()))
    if any(
        r.kind == "desconto" and r.fields.get("vigente") == "sim"
        for r in records_by_entity.values()
    ):
        sources.add("desconto")
    c = _Ctx(records_by_entity, card_facts, enrollment_indices, system_notes, question, pool,
             _merge(_pool([*card_facts.values(), *system_notes]), _period_pool(args)),
             tool_calls_this_turn,
             frozenset(sources), lex, ontology)  # fmt: skip
    failures: list[Failure] = []
    if canary and canary in answer:
        failures.append(Failure("canary", 0, "canary", None))
    if _ngrams(prompt_prefix) & _ngrams(answer):
        failures.append(Failure("prompt_ngram", 0, "prefixo", None))
    if len(answer) > load_slo_targets().limits["default"].answer_chars:
        return GuardResult(False, (*failures, Failure("length", 0, str(len(answer)), None)), None)
    sentences = split_sentences(answer)
    items = [(i, s, False) for i, s in enumerate(sentences)]
    first = fold(answer).split()
    if (
        question
        and 0 < len(first) <= 6
        and re.sub(r"\W+", " ", fold(answer)).strip().split(" ")[0] in lex.confirm
    ):
        items += [(0, q, True) for q in split_sentences(question)]
    sent_domain: dict[int, Domain | None] = {}
    for i, s, imp in items:
        fs = _check_sentence(c, i, s, imp)
        failures += fs
        if fs:
            cited = [r for r in records_by_entity.values() if fs[0].entity == r.key]
            sent_domain[i] = _sentence_domain(s, cited)
    doms = Counter(d for d in sent_domain.values() if d)
    domain = doms.most_common(1)[0][0] if doms else None
    return GuardResult(not failures, tuple(failures), domain)
