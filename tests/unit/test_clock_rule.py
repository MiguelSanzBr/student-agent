import re
from datetime import UTC, date
from pathlib import Path

from core.clock import monotonic, reference_today, wall_now

ROOT = Path(__file__).resolve().parents[2]
RULE = re.compile(
    r"(?<![\w.])now\(|CURRENT_DATE|CURRENT_TIMESTAMP|date\.today|datetime\.now"
    r"|datetime\.utcnow|time\.time\("
)
DIRS = ["core", "app", "etl", "evals"]
SKIP = {ROOT / "core" / "clock.py"}
SKIP_DIRS = [ROOT / "evals" / "latency"]


def test_regex_matches_now_not_wall_now() -> None:
    assert RULE.search("x = now()")
    assert RULE.search("(now())")
    assert RULE.search("SELECT CURRENT_DATE")
    assert RULE.search("datetime.now(UTC)")
    assert not RULE.search("wall_now()")
    assert not RULE.search("self.now()")


def test_clock_only_in_core_clock() -> None:
    bad = []
    for d in DIRS:
        for p in (ROOT / d).rglob("*"):
            if p.suffix not in {".py", ".sql"} or p in SKIP:
                continue
            if any(p.is_relative_to(s) for s in SKIP_DIRS):
                continue
            if RULE.search(p.read_text()):
                bad.append(str(p.relative_to(ROOT)))
    assert bad == []


def test_clock_functions() -> None:
    class S:
        reference_date = date(2026, 10, 31)

    assert reference_today(S()) == date(2026, 10, 31)
    assert wall_now().tzinfo is UTC
    assert monotonic() <= monotonic()
