from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VARS = ["COMPOSE_PROJECT_NAME", "API_PORT", "DB_PORT", "REDIS_PORT"]
BUSY = {631, 8000, 13469, 17600, 27036, 35291, 45403, 46949, 57343, 61209}


def parse() -> dict[str, dict[str, str]]:
    lanes: dict[str, dict[str, str]] = {}
    for line in (ROOT / "ops" / "lanes.env").read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, val = line.split("=", 1)
        lane, _, var = key.partition("_")
        lanes.setdefault(lane, {})[var] = val
    return lanes


def test_lanes_isolated() -> None:
    lanes = parse()
    assert sorted(lanes) == ["A", "B", "C", "D"]
    for var in VARS:
        vals = [lanes[x][var] for x in lanes]
        assert len(set(vals)) == 4, var
    for x, cfg in lanes.items():
        assert cfg["COMPOSE_PROJECT_NAME"] == f"sa-{x.lower()}"
        for v in ("API_PORT", "DB_PORT", "REDIS_PORT"):
            assert int(cfg[v]) not in BUSY
