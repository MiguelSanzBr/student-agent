from app.agent.llm import LLMProvider
from app.config import AppSettings


def make_llm(settings: AppSettings) -> LLMProvider:
    match settings.llm_provider:
        case "gemini":
            from app.agent.gemini import GeminiProvider

            return GeminiProvider(settings)
        case "fake":
            from app.agent.fakes import ScriptedLLM

            return ScriptedLLM([])
        case other:
            raise NotImplementedError(f"llm_provider={other!r} chega na Task 23")
