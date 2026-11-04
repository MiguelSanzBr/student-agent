"""Ontologia (`ontology/*.yaml`): destino de cada coluna, tipos de texto e listas congeladas."""

import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, get_args

import yaml

TableDestination = Literal["loaded", "partial", "resolution_only", "discarded"]
ColumnDestination = Literal["load", "transform", "resolution", "discard"]
ColumnType = Literal["id", "enum", "text", "date", "instant", "money", "number", "bool", "code"]
TextFieldType = Literal["enum", "label_pt", "exact_list", "regex", "free_text"]
QuizType = Literal["AV1", "AV2", "simulado"]
ServiceMode = Literal["agent", "orientation"]


@dataclass(frozen=True)
class ColumnSpec:
    destination: ColumnDestination
    type: ColumnType
    reason: str
    target: str | None  # "tabela_canon.campo"; só load/transform


@dataclass(frozen=True)
class Service:
    name_pt: str
    ui_label: str
    sla_hours: int
    fee_cents: int
    mode: ServiceMode
    channels: tuple[str, ...]
    why_not_agent: str | None


@dataclass(frozen=True)
class GuardLexicon:
    entity_nouns: tuple[str, ...]
    debt_nouns: tuple[str, ...]
    negators: tuple[str, ...]
    zero_words: tuple[str, ...]
    commitment_terms: tuple[str, ...]
    action_lemmas: tuple[str, ...]
    confirmation_words: tuple[str, ...]


def _yaml(path: Path) -> Any:
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def _norm(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).split())


def _check(value: str, allowed: Any, what: str) -> Any:
    if value not in get_args(allowed):
        raise ValueError(f"{what} inválido: {value!r}")
    return value


class Ontology:
    @classmethod
    def load(cls, root: Path, class_subjects: Mapping[str, str] | None = None) -> "Ontology":
        """`class_subjects` (CLASS_ID -> SUBJECT_CODE) liga as salas `SAG-<id>`."""
        return cls(root, class_subjects or {})

    def __init__(self, root: Path, class_subjects: Mapping[str, str]) -> None:
        self._tables: dict[str, tuple[TableDestination, str, dict[str, ColumnSpec]]] = {}
        for name, t in _yaml(root / "tables.yaml").items():
            cols = {}
            for col, (dest, typ, target, why) in t["columns"].items():
                _check(dest, ColumnDestination, f"{name}.{col} destino")
                _check(typ, ColumnType, f"{name}.{col} tipo")
                if (target is not None) != (dest in ("load", "transform")):
                    raise ValueError(f"{name}.{col}: só load/transform têm alvo")
                cols[col] = ColumnSpec(dest, typ, why, target)
            _check(t["destination"], TableDestination, f"{name} destino")
            self._tables[name] = (t["destination"], t["reason"], cols)
        self._entities: dict[str, dict[str, dict[str, Any]]] = {
            e: v["fields"] for e, v in _yaml(root / "entities.yaml").items()
        }
        self._enums: dict[str, dict[str, str]] = _yaml(root / "enums.yaml")
        self._text_fields: dict[str, TextFieldType] = {
            k: _check(v, TextFieldType, k) for k, v in _yaml(root / "text_fields.yaml").items()
        }
        self._professors = tuple(_yaml(root / "professor_names.yaml"))
        self._professor_set = frozenset(self._professors)
        self._units: dict[str, str] = _yaml(root / "units.yaml")
        self._subjects: dict[str, str] = _yaml(root / "subject_names_pt.yaml")
        self._services = {
            c: Service(
                s["name_pt"],
                s["ui_label"],
                s["sla_hours"],
                s["fee_cents"],
                _check(s["mode"], ServiceMode, c),
                tuple(s["channels"]),
                s["why_not_agent"],
            )
            for c, s in _yaml(root / "services.yaml").items()
        }
        lex = _yaml(root / "guard_lexicon.yaml")
        self._lexicon = GuardLexicon(**{k: tuple(v) for k, v in lex.items()})
        tpl = _yaml(root / "free_text_templates.yaml")
        self._messages = {_norm(m["text"]): m["label_pt"] for m in tpl["messages"]}
        self._quizzes: list[tuple[QuizType, re.Pattern[str]]] = [
            (_check(k, QuizType, "prova"), re.compile(v)) for k, v in tpl["quizzes"].items()
        ]
        rooms = tpl["rooms"]
        self._idnumber_re = re.compile(rooms["idnumber"])
        self._shortname_re = re.compile(rooms["shortname"])
        self._inst_re = re.compile(rooms["inst"])
        self._inst_labels: dict[str, str] = rooms["inst_labels"]
        self._class_subjects = class_subjects
        self._tools: dict[str, frozenset[str]] = {}
        for f in sorted((root / "tools").glob("*.yaml")):
            for tool, spec in _yaml(f)["tools"].items():
                self._tools[tool] = frozenset(spec["outputs"])

    # tabelas e colunas de origem
    def tables(self) -> tuple[str, ...]:
        return tuple(self._tables)

    def table_destination(self, table: str) -> TableDestination:
        return self._tables[table][0]

    def table_reason(self, table: str) -> str:
        return self._tables[table][1]

    def table_columns(self, table: str) -> tuple[str, ...]:
        return tuple(self._tables[table][2])

    def column(self, table: str, col: str) -> ColumnSpec:
        return self._tables[table][2][col]

    # canon
    def entities(self) -> tuple[str, ...]:
        return tuple(self._entities)

    def entity_fields(self, entity: str) -> tuple[str, ...]:
        return tuple(self._entities[entity])

    def has_field(self, entity: str, field: str) -> bool:
        return field in self._entities.get(entity, {})

    def field_type(self, entity: str, field: str) -> str:
        return str(self._entities[entity][field]["type"])

    def field_enum(self, entity: str, field: str) -> str | None:
        return self._entities[entity][field].get("enum")

    def is_derived(self, entity: str, field: str) -> bool:
        return bool(self._entities[entity][field].get("derived"))

    def text_fields(self) -> Mapping[str, TextFieldType]:
        return self._text_fields

    def text_field_type(self, entity: str, field: str) -> TextFieldType:
        return self._text_fields[f"{entity}.{field}"]

    # enums e catálogos PT
    def enum_values(self, enum: str) -> tuple[str, ...]:
        return tuple(self._enums[enum])

    def label_pt(self, enum: str, value: str) -> str:
        return self._enums[enum][value]

    def course_name_pt(self, code: str) -> str:
        return self._enums["course"][code]

    def subject_name_pt(self, code: str) -> str:
        return self._subjects[code]

    def subject_names_pt(self) -> tuple[str, ...]:
        return tuple(self._subjects.values())

    def service_codes(self) -> tuple[str, ...]:
        return tuple(self._services)

    def service(self, code: str) -> Service:
        return self._services[code]

    # listas exatas congeladas
    def professor_names(self) -> tuple[str, ...]:
        return self._professors

    def professor_allowed(self, name: str) -> bool:
        return name in self._professor_set

    def unit_ids(self) -> tuple[str, ...]:
        return tuple(self._units)

    def unit_label(self, unit_id: str) -> str:
        return self._units[unit_id]

    # texto tipificado
    def message_texts(self) -> tuple[str, ...]:
        return tuple(self._messages)

    def classify_message(self, text: str) -> str | None:
        """Rótulo PT do modelo se o texto é igual a um dos 4 (após NFKC e espaços), senão None."""
        return self._messages.get(_norm(text))

    def quiz_type(self, name: str) -> QuizType | None:
        for kind, rx in self._quizzes:
            if rx.fullmatch(name):
                return kind
        return None

    def room_subject(self, shortname: str, idnumber: str | None) -> str | None:
        """Nome PT da disciplina da sala (ou o rótulo fixo das salas INST); None se não tipifica."""
        if m := self._inst_re.fullmatch(shortname):
            return self._inst_labels.get(m["n"])
        code = None
        if idnumber and (m := self._idnumber_re.fullmatch(idnumber)):
            code = self._class_subjects.get(m["class_id"])
        if code is None and (m := self._shortname_re.fullmatch(shortname)):
            code = m["subject"]
        return self._subjects.get(code) if code else None

    def guard_lexicon(self) -> GuardLexicon:
        return self._lexicon

    def tools(self) -> tuple[str, ...]:
        return tuple(self._tools)

    def tool_outputs(self, tool: str) -> frozenset[str]:
        return self._tools[tool]
