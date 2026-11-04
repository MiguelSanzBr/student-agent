import math
import statistics

import pytest

from app.agent.fakes import LoadTestLLM
from app.agent.llm import LLMUnavailable, Message
from app.config import Quota, load_slo_targets

SLO = load_slo_targets()


def _llm(quota: Quota | None = None, seed: int = 1, **kw) -> LoadTestLLM:
    return LoadTestLLM(SLO.components, quota or SLO.quotas["gemini-3.8-flash"], seed=seed, **kw)


def test_loadtest_llm_samples_fit() -> None:
    llm = _llm()
    for name, c in SLO.components.items():
        med = statistics.median(llm.sample(name) for _ in range(4000))
        assert med == pytest.approx(math.exp(c.mu), rel=0.05)


async def test_generate_sleeps_drawn_latency_and_answers() -> None:
    slept: list[float] = []

    async def sleep(s: float) -> None:
        slept.append(s)

    llm = _llm(sleep=sleep, p_tool=0.0)
    r = await llm.generate(model="m", system="", messages=[Message("user", "oi")], tools=(),
                           deadline=1e9)  # fmt: skip
    assert r.text and not r.tool_calls and len(slept) == 1 and slept[0] > 0


async def test_quota_exceeded_raises() -> None:
    async def sleep(_: float) -> None: ...

    llm = _llm(Quota(rpm=2, tpm=10**9, rpd=10), sleep=sleep, p_tool=0.0)
    kw = dict(model="m", system="", messages=[Message("user", "oi")], tools=(), deadline=1e9)
    await llm.generate(**kw)
    await llm.generate(**kw)
    with pytest.raises(LLMUnavailable) as e:
        await llm.generate(**kw)
    assert e.value.reason == "quota"
