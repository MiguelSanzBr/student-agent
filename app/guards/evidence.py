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

from app.config import load_slo_targets
from app.guards.fallback import domain_of
from app.guards.sentences import split_sentences
from app.guards.tokens import tokenize
from app.guards.types import Failure, FailureKind, GuardResult
from app.rules.commitments import commitment_sources, conditions_in
from app.tools.base import Domain, EntityRecord, ToolCallTrace, find_entity
from core.ontology import Ontology

_SA = re.compile(r"SA-\d{4}-\d{6}")
_REFUSAL = {"dados", "registros", "registro", "informacao", "informacoes", "consigo", "posso",
            "sei", "previsao", "acesso", "disponivel", "encontrei", "localizei"}  # fmt: skip
_LIGHT = {"tem", "ha", "existe", "existem", "possui", "ter", "mais", "um", "uma", "o", "a", "as",
          "os", "de", "da", "do", "das", "dos", "qualquer", "pendente", "pendentes", "em",
          "aberto", "nem", "e", "eh", "esta", "ficou", "igual", "total", "atual", "valor", "foi",
          "sera", "com", "para", "nenhum", "nenhuma", "voce", "seu", "sua"}  # fmt: skip
_FIN_NOUNS = {"fatura", "boleto", "mensalidade", "parcela", "pagamento", "deve", "dever", "devo",
              "devemos", "devem"}  # fmt: skip
_PROMISE = re.compile(
    r"\b(pode|podem|podemos|posso|poderia|podera|poderao|vai|vamos|sera|serao|tera|terao|"
    r"consegue|conseguira|ficara|garant\w*|prometo|oferec\w+|concedemos|liberamos|"
    r"tem direito|possivel|parcele|negocie|pague|solicite|peca)\b"
)
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
    if lemma in ("aprovar", "quitar", "pagar"):
        part = []  # "aprovado"/"quitado"/"pago" são status de registro
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
    action_participle = re.compile(rf"{b}{aux}(?:{_alt(part)})(?![a-z0-9])" if part else r"(?!x)x")
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


def _rec_texts(r: EntityRecord) -> list[str]:
    return [r.id, *r.aliases, *r.fields.values()]


def _sentence_domain(text: str, cited: Sequence[EntityRecord]) -> Domain | None:
    for r in cited:
        if d := domain_of(r.kind):
            return d
    f = fold(text)
    return next((d for d, rx in _KEYWORD_DOMAIN if re.search(rx, f)), None)


def _statuses(f: str) -> set[str]:
    f = _PASSIVE_OPEN.sub(" ", f)
    return {k for k, rx in _STATUS_RX.items() if rx.search(f)}


def _canon_status(value: str) -> str | None:
    v = fold(value)
    return next((k for k, rx in _STATUS_RX.items() if rx.fullmatch(v)), None)


def _zero_like(v: str) -> bool:
    return bool(re.fullmatch(r"(?:r\$\s?)?0(?:[.,]0+)?|nao|nenhum|nenhuma|zero|", fold(v).strip()))


@dataclass
class _Ctx:
    records: Mapping[str, EntityRecord]
    card: Mapping[str, str]
    enrollments: tuple[str, ...]
    notes: tuple[str, ...]
    question: str
    pool: Pool
    sources: frozenset[str]
    lex: _Lex
    ont: Ontology


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
    for i, w in enumerate(words):
        if w in ("em_dia",) or (w in neg and w.startswith("sem_")):
            claims.append("financeiro")
            continue
        if w in neg or w in zero:
            span = words[i + 1 : i + 4] if w in neg else []
            for x in span:
                if stem(x) in nouns or x in debt:
                    claims.append(stem(x))
                    break
                if x not in _LIGHT:
                    break
            if w in zero:
                for x in reversed(words[max(0, i - 3) : i]):
                    if stem(x) in nouns or x in debt:
                        claims.append(stem(x))
                        break
                    if x not in _LIGHT:
                        break
    if not claims or any(x in _REFUSAL for x in words):
        return True
    for noun in claims:
        if noun == "financeiro" or noun in debt or stem(noun) in debt:
            if not _fin_zero(c, sorted(enrs), cited, bool(re.search(r"vencid|atras|inadimpl", f))):
                return False
        else:
            vals = [v for r in cited for v in r.fields.values()]
            vals += [v for k, v in c.card.items() if noun.rstrip("s") in fold(k)]
            vals += [
                v
                for r in c.records.values()
                if noun.rstrip("s") in fold(r.kind)
                for v in r.fields.values()
            ]
            if not any(_zero_like(v) or fold(v) in ("pago", "quitado") for v in vals):
                return False
    return True


def _check_sentence(c: _Ctx, idx: int, text: str, imported: bool) -> list[Failure]:
    text = unicodedata.normalize("NFKC", text)
    f = fold(text)
    toks = tokenize(text)
    out: list[Failure] = []

    def fail(kind: FailureKind, token: str, entity: str | None = None) -> None:
        out.append(Failure(kind, idx, token, entity))

    # menções de entidade
    cited: list[EntityRecord] = []
    for r in c.records.values():
        names = [
            *r.aliases,
            *([r.id] if len(r.id) >= 4 and any(ch.isdigit() for ch in r.id) else []),
        ]
        if (
            any(re.search(rf"(?<!\w){re.escape(fold(a))}(?!\w)", f) for a in names)
            and r not in cited
        ):
            cited.append(r)
    enrs = {t.norm for t in toks if t.kind == "enr"}
    for n in sorted(enrs - set(c.enrollments)):
        fail("entity_without_record", f"matrícula {n}")
    for m in c.lex.subject_rx.finditer(f):
        if find_entity(c.records, text[m.start() : m.end()]) is None and not any(
            fold(a) == m[0] for r in cited for a in r.aliases
        ):
            fail("entity_without_record", text[m.start() : m.end()])
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
    for t in toks:
        k, p = t.kind, c.pool
        if k in ("url", "opaque"):
            fail(k, t.raw, ent)  # type: ignore[arg-type]
        elif k in ("email", "cpf", "cnpj", "phone", "id", "decimal", "pct", "int", "date", "money"):
            allowed = p.get(k, set())
            if k == "money" and t is anchored:
                allowed = _total_pool(c, sorted(enrs)) | _pool(
                    [v for r in cited for v in r.fields.values()]).get("money", set())  # fmt: skip
            if k == "date" and len(t.norm) == 5:
                allowed = p.get("ddmm", set())
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
                    allowed = _pool(texts).get("int", set())
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
        for r in cited:
            canon = _canon_status(r.fields.get("resultado") or r.fields.get("status") or "")
            if canon and canon not in sts:
                fail("status", ",".join(sorted(sts)), r.key)
                break

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
        if conds and (imported or _PROMISE.search(nm)) and not conds <= c.sources:
            fail("commitment", ",".join(sorted(conds - c.sources)), ent)

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
    if len(answer) > load_slo_targets().limits["default"].answer_chars:
        return GuardResult(False, (Failure("length", 0, str(len(answer)), None),), None)
    lex = _lex(ontology)
    texts = [t for r in records_by_entity.values() for t in _rec_texts(r)]
    texts += [*card_facts.values(), *system_notes]
    args = [v for tc in tool_calls_this_turn if tc.returned_records > 0 for v in tc.args.values()]
    pool = _pool([*texts, *args])
    sources = set(commitment_sources(records_by_entity.values()))
    if any(
        r.kind == "desconto" and r.fields.get("vigente") == "sim"
        for r in records_by_entity.values()
    ):
        sources.add("desconto")
    c = _Ctx(records_by_entity, card_facts, enrollment_indices, system_notes, question, pool,
             frozenset(sources), lex, ontology)  # fmt: skip
    failures: list[Failure] = []
    if canary and canary in answer:
        failures.append(Failure("canary", 0, "canary", None))
    if _ngrams(prompt_prefix) & _ngrams(answer):
        failures.append(Failure("prompt_ngram", 0, "prefixo", None))
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
