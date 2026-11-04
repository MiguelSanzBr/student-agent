import math

from app.agent.llm import LLMProvider
from app.config import AppSettings


def make_llm(settings: AppSettings) -> LLMProvider:
    llm = _make_provider(settings)
    if settings.lever_hedge_first_call:
        from app.agent.levers import HedgedLLM

        c = settings.slo.components["tool_call"]
        return HedgedLLM(llm, math.exp(c.mu + 1.2816 * c.sigma))  # p90 da lognormal
    return llm


def _make_provider(settings: AppSettings) -> LLMProvider:
    provider: str = settings.llm_provider
    if provider == "auto":
        provider = "gemini" if settings.gemini_api_key else "replay"
    match provider:
        case "gemini":
            from app.agent.gemini import GeminiProvider

            return GeminiProvider(settings)
        case "fake":
            from app.agent.fakes import SmokeLLM

            return SmokeLLM()
        case "replay":
            from app.agent.replay import DEFAULT_REPLAY, ReplayLLM

            return ReplayLLM(DEFAULT_REPLAY)
        case other:
            raise NotImplementedError(f"llm_provider={other!r}")
