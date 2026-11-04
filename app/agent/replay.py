"""Gravação e replay das respostas do LLM, para rodar a demo sem GEMINI_API_KEY (spec D19)."""

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Any

from app.agent.llm import LLMProvider, LLMResponse, Message, ToolCall, ToolChoice, Usage
from app.tools.base import FERRAMENTA_INDISPONIVEL, ToolSpec

DEFAULT_REPLAY = Path(__file__).resolve().parents[2] / "evals" / "replays" / "demo.jsonl"
UNKNOWN_TEXT = "Modo demonstração: escolha uma das perguntas sugeridas."
# Rótulo de TODA resposta do replay: não passa por um modelo real nem em medição (D19).
REPLAY_MODEL = "demo-replay"
# Início da mensagem de feedback que o grafo (_guard_node) manda ao LLM quando o guard reprova.
_FEEDBACK = "Sua resposta foi reprovada na conferência"


def _without_feedback(messages: Sequence[Message]) -> list[Message]:
    """Tira o par (resposta reprovada, feedback do guard): o replay refaz a mesma pergunta."""
    out: list[Message] = []
    for m in messages:
        if m.role == "user" and (m.text or "").startswith(_FEEDBACK) and out:
            out.pop()
        else:
            out.append(m)
    return out


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def _fin_rows(data: Mapping[str, Any]) -> list[tuple[str, Mapping[str, Any]]]:
    rows = data.get("matriculas")
    return [(f"Matrícula {r['matrícula']}: ", r) for r in rows] if rows else [("", data)]


def _s(lead: str, text: str) -> str:
    return lead + text if lead else text[0].upper() + text[1:]


# total_geral fica de fora: a ferramenta não o põe em registro e o EvidenceGuard o recusaria.
def _fin_total(data: Mapping[str, Any]) -> str:
    out = []
    for lead, d in _fin_rows(data):
        out.append(_s(lead, f"o total para quitar é {d['total_para_quitar']}."))
        if d["faturas_vencidas"]:
            n = _plural(d["faturas_vencidas"], "fatura vencida", "faturas vencidas")
            out.append(f"Há {n}, com principal vencido de {d['principal_vencido']}.")
    return " ".join(out)


def _fin_next(data: Mapping[str, Any]) -> str:
    out = []
    for lead, d in _fin_rows(data):
        if d["proximo_vencimento"] == "nenhum":
            out.append(_s(lead, "não há próximo vencimento."))
        else:
            out.append(
                _s(
                    lead,
                    f"o próximo vencimento é {d['proximo_vencimento']}, "
                    f"no valor de {d['valor_proximo_vencimento']}.",
                )
            )
    return " ".join(out)


def _lms(data: Mapping[str, Any]) -> str:
    if "mensagem" in data:  # matrícula sem conta no AVA
        return str(data["mensagem"])
    # sem "AVA"/"aberto"/data de acesso: o EvidenceGuard ancora essas palavras a outros registros
    out = [
        f"Você tem {_plural(data['pendentes'], 'pendência', 'pendências')} no período "
        f"{data['periodo']}."
    ]
    if (nxt := data.get("proxima_avaliacao")) is not None:
        out.append(f"A próxima avaliação é em {nxt['data']} ({nxt['tipo']}).")
    return " ".join(out)


# Roteiro: pergunta normalizada -> (ferramenta chamada no passo 0, molde do passo 1 preenchido
# com a saída REAL da ferramenta: nenhum número vem do roteiro, então outro aluno vê os próprios).
_SCRIPT: dict[str, tuple[str, Callable[[Mapping[str, Any]], str]]] = {
    "quanto eu devo?": ("get_financial_summary", _fin_total),
    "quanto falta para quitar?": ("get_financial_summary", _fin_total),  # scripts/a2a_demo
    "qual é o meu próximo vencimento?": ("get_financial_summary", _fin_next),
    "quais são minhas atividades no lms?": ("get_lms_activity", _lms),
}
# Chips: grafia de exibição; as chaves de _SCRIPT são normalizadas (casefold), então não dá
# para derivá-los de lá. Chaves fora daqui (a2a_demo) respondem mas não são sugeridas.
_CHIPS = ["Quanto eu devo?", "Qual é o meu próximo vencimento?",
          "Quais são minhas atividades no LMS?"]  # fmt: skip


def _turn_key(messages: Sequence[Message]) -> tuple[str, int]:
    """(pergunta, passo): a pergunta é a última do usuário; o passo, quantas chamadas do
    assistente já houve depois dela (0 = primeira chamada do turno)."""
    last = max((i for i, m in enumerate(messages) if m.role == "user"), default=-1)
    question = (messages[last].text or "") if last >= 0 else ""
    return question, sum(1 for m in messages[last + 1 :] if m.role == "assistant")


def _norm(question: str) -> str:
    return " ".join(question.split()).casefold()


class RecordingLLM:
    """Repassa ao LLM real e acrescenta cada resposta em `path` (JSONL)."""

    def __init__(self, inner: LLMProvider, path: Path) -> None:
        self._inner = inner
        self._path = path

    async def generate(
        self, *, model: str, system: str, messages: Sequence[Message],
        tools: Sequence[ToolSpec], deadline: float,
        tool_choice: ToolChoice = "auto",
    ) -> LLMResponse:  # fmt: skip
        resp = await self._inner.generate(
            model=model, system=system, messages=messages, tools=tools, deadline=deadline,
            tool_choice=tool_choice,
        )  # fmt: skip
        question, step = _turn_key(messages)
        line: dict[str, Any] = {
            "pergunta": question, "passo": step, "text": resp.text,
            "tool_calls": [{"id": c.id, "name": c.name, "args": dict(c.args)}
                           for c in resp.tool_calls],
            "usage": asdict(resp.usage), "model": resp.model, "finish_reason": resp.finish_reason,
        }  # fmt: skip
        with self._path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
        return resp


class ReplayLLM:
    """Devolve a resposta gravada para (pergunta, passo); pergunta fora do roteiro recebe
    `UNKNOWN_TEXT`."""

    def __init__(self, path: Path) -> None:
        self._by_key: dict[tuple[str, int], LLMResponse] = {}
        self._suggested: list[str] = list(_CHIPS)
        for raw in path.read_text(encoding="utf-8").splitlines():
            if not raw.strip():
                continue
            rec = json.loads(raw)
            resp = LLMResponse(
                rec["text"],
                tuple(ToolCall(c["id"], c["name"], c["args"]) for c in rec["tool_calls"]),
                Usage(**rec["usage"]), REPLAY_MODEL, rec["finish_reason"],
            )  # fmt: skip
            self._by_key[(_norm(rec["pergunta"]), rec["passo"])] = resp
            if rec["passo"] == 0 and rec["pergunta"] not in self._suggested:
                self._suggested.append(rec["pergunta"])

    def suggested(self) -> list[str]:
        """Perguntas que abrem um turno gravado, na ordem do arquivo."""
        return list(self._suggested)

    async def generate(
        self, *, model: str, system: str, messages: Sequence[Message],
        tools: Sequence[ToolSpec], deadline: float,
        tool_choice: ToolChoice = "auto",
    ) -> LLMResponse:  # fmt: skip
        messages = _without_feedback(messages)
        question, step = _turn_key(messages)
        key = _norm(question)
        if key in _SCRIPT:
            tool, render = _SCRIPT[key]
            if step == 0:
                return LLMResponse(
                    None, (ToolCall(f"demo-{tool}", tool, {}),), Usage(), REPLAY_MODEL
                )
            last = messages[-1]
            res = last.tool_result if last.role == "tool" and last.tool_name == tool else None
            if not res or "erro" in res:
                return LLMResponse(FERRAMENTA_INDISPONIVEL, (), Usage(), REPLAY_MODEL, "STOP")
            return LLMResponse(render(res), (), Usage(), REPLAY_MODEL, "STOP")
        found = self._by_key.get((key, step))
        if found is not None:
            return found
        return LLMResponse(UNKNOWN_TEXT, (), Usage(), REPLAY_MODEL, "STOP")
