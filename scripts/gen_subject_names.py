"""Gera `ontology/subject_names_pt.yaml` com o Gemini, uma vez. Exige GEMINI_API_KEY no ambiente.

Uso: python -m scripts.gen_subject_names <zip|dir> <saida.yaml>. O autor revisa uma amostra de 20.
"""

import json
import os
import sys
from pathlib import Path

import yaml
from google import genai

from etl.raw import read_raw

MODEL = "gemini-3.5-flash"
PROMPT = (
    "Traduza para o português do Brasil os nomes de disciplinas de graduação abaixo, no estilo "
    "de uma grade curricular (Title Case de PT-BR, com acentos, algarismos romanos mantidos). "
    'Responda só com um objeto JSON {"CODIGO": "Nome"}.\n'
)


def main(source: Path, out: Path) -> None:
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        sys.exit("GEMINI_API_KEY ausente")
    subjects = {r["SUBJECT_CODE"]: r["SUBJECT_NAME"] for r in read_raw(source)["sag/subject"]}
    reply = genai.Client(api_key=key).models.generate_content(
        model=MODEL,
        contents=PROMPT + json.dumps(subjects, ensure_ascii=False),
        config={"response_mime_type": "application/json"},
    )
    names = json.loads(reply.text or "{}")
    if set(names) != set(subjects) or len(set(names.values())) != len(subjects):
        sys.exit("resposta incompleta ou com nomes repetidos")
    out.write_text(yaml.safe_dump(names, allow_unicode=True, sort_keys=False), encoding="utf-8")


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
