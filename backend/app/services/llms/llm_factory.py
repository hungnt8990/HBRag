from __future__ import annotations

from collections.abc import AsyncIterator
from functools import lru_cache

from app.core.config import settings
from app.services.llms.llm_base import LLMProvider
from app.services.llms.llm_fake_llm import FakeLLM
from app.services.llms.llm_openai_llm import OpenAICompatibleLLM

DEFAULT_PROVIDER_ALIASES = {"", "default", "runtime_default", "runtime-default"}
SUPPORTED_LLM_PROVIDERS = {"fake", "openai_compatible"}


class ErrorLLM:
    def __init__(self, message: str) -> None:
        self._message = message

    async def generate(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
    ) -> str:
        raise RuntimeError(self._message)

    async def stream_generate(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
    ) -> AsyncIterator[str]:
        raise RuntimeError(self._message)
        yield ""


def build_llm_provider(
    *,
    provider: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
) -> LLMProvider:
    effective_provider = _normalize_provider(provider) or settings.llm_provider.lower().strip()
    if effective_provider == "fake":
        return FakeLLM()
    if effective_provider == "openai_compatible":
        return OpenAICompatibleLLM(
            base_url=_required(base_url or settings.llm_base_url, "LLM_BASE_URL"),
            api_key=settings.llm_api_key,
            model=_required(model or settings.llm_model, "LLM_MODEL"),
        )

    raise ValueError(f"Unsupported LLM provider: {effective_provider}")


def build_llm_provider_or_error(
    *,
    provider: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
) -> LLMProvider:
    try:
        return build_llm_provider(provider=provider, base_url=base_url, model=model)
    except Exception as exc:
        return ErrorLLM(str(exc))


@lru_cache
def get_llm_provider() -> LLMProvider:
    return build_llm_provider()


def _normalize_provider(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip().casefold().replace("-", "_")
    if normalized in DEFAULT_PROVIDER_ALIASES:
        return None
    if normalized not in SUPPORTED_LLM_PROVIDERS:
        raise ValueError(f"Unsupported LLM provider: {value}")
    return normalized


def _required(value: str | None, env_name: str) -> str:
    if value is None:
        raise ValueError(f"{env_name} is required for the selected provider.")
    stripped_value = value.strip()
    if not stripped_value:
        raise ValueError(f"{env_name} is required for the selected provider.")
    return stripped_value
