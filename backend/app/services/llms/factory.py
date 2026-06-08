from __future__ import annotations

from functools import lru_cache

from app.core.config import settings
from app.services.llms.base import LLMProvider
from app.services.llms.fake_llm import FakeLLM
from app.services.llms.openai_llm import OpenAICompatibleLLM


@lru_cache
def get_llm_provider() -> LLMProvider:
    provider = settings.llm_provider.lower().strip()
    if provider == "fake":
        return FakeLLM()
    if provider == "openai_compatible":
        return OpenAICompatibleLLM(
            base_url=_required(settings.llm_base_url, "LLM_BASE_URL"),
            api_key=settings.llm_api_key,
            model=_required(settings.llm_model, "LLM_MODEL"),
        )

    raise ValueError(f"Unsupported LLM provider: {settings.llm_provider}")


def _required(value: str | None, env_name: str) -> str:
    if value is None:
        raise ValueError(f"{env_name} is required for the selected provider.")
    stripped_value = value.strip()
    if not stripped_value:
        raise ValueError(f"{env_name} is required for the selected provider.")
    return stripped_value
