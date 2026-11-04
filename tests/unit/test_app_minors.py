import asyncio
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from app.agent.levers import HedgeBudget, Hedger
from app.agent.llm import LLMResponse, Usage
from app.config import AppSettings, load_slo_targets
from app.service.turn import _Meter


def test_slo_targets_path_ignored_in_prod(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SLO_TARGETS_PATH", str(tmp_path / "nao-existe.yaml"))
    monkeypatch.setenv("APP_ENV", "dev")
    with pytest.raises(FileNotFoundError):
        load_slo_targets()
    monkeypatch.setenv("APP_ENV", "prod")
    assert load_slo_targets().turn_slots  # ignorou a variável: leu o slo_targets.yaml


@pytest.mark.parametrize("provider", ["fake", "loadtest"])
def test_prod_refuses_fake_and_loadtest_provider(app_secrets: Path, provider: str) -> None:
    with pytest.raises(ValidationError, match="llm_provider"):
        AppSettings(app_env="prod", cookie_secure=True, llm_provider=provider)
    AppSettings(app_env="dev", llm_provider=provider)


async def test_hedge_extra_call_counted_on_cancel(app_secrets: Path) -> None:
    class Hang:
        async def generate(self, **_: Any) -> LLMResponse:
            await asyncio.sleep(10)
            raise AssertionError

    cfg = AppSettings().slo.hedge
    m = _Meter(Hang(), Hedger(0.01, HedgeBudget(cfg), _free))  # type: ignore[arg-type]
    t = asyncio.ensure_future(m.generate(model="m", system="", messages=[], tools=[], deadline=0))
    await asyncio.sleep(0.1)  # as duas em voo
    t.cancel()
    await asyncio.gather(t, return_exceptions=True)
    assert m.with_hedge(Usage(), 1)[1] == 2


async def test_hedge_extra_call_not_double_counted(app_secrets: Path) -> None:
    class Slow1:
        n = 0

        async def generate(self, **_: Any) -> LLMResponse:
            self.n += 1
            await asyncio.sleep(10 if self.n == 1 else 0)
            return LLMResponse("r", (), Usage(100, 20), "m")

    cfg = AppSettings().slo.hedge
    m = _Meter(Slow1(), Hedger(0.01, HedgeBudget(cfg), _free))  # type: ignore[arg-type]
    await m.generate(model="m", system="", messages=[], tools=[], deadline=0)
    u, calls = m.with_hedge(m.usage, m.calls)
    assert calls == 2 and u.input_tokens == 200


async def _free() -> bool:
    return False
