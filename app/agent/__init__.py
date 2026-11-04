from app.agent.llm import LLMProvider
from app.config import AppSettings


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
        case "replay":
            from app.agent.replay import DEFAULT_REPLAY, ReplayLLM

            settings.demo_mode = True  # a faixa "modo demonstração" da UI depende disto
            return ReplayLLM(DEFAULT_REPLAY)
        case other:
            raise NotImplementedError(f"llm_provider={other!r}")
