from app.agent.llm import LLMProvider
from app.config import AppSettings, Quota


def make_llm(settings: AppSettings) -> LLMProvider:
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
        case "loadtest":  # teste de carga sem gastar: latências dos lognormais do slo_targets
            from app.agent.fakes import LoadTestLLM

            q = settings.slo.quotas[settings.llm_model]
            # o limitador sob teste é o balde; a cota do fake fica folgada (sem 429 próprio)
            wide = Quota(rpm=q.rpm * 10, tpm=q.tpm * 10, rpd=q.rpd)
            return LoadTestLLM(settings.slo.components, wide, seed=0, p_retry=0.03)
        case "replay":
            from app.agent.replay import DEFAULT_REPLAY, ReplayLLM

            return ReplayLLM(DEFAULT_REPLAY)
        case other:
            raise NotImplementedError(f"llm_provider={other!r}")
