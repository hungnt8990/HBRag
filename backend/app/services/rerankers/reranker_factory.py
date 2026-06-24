from __future__ import annotations

from functools import lru_cache

from app.core.config import settings
from app.services.rerankers.reranker_base import Reranker
from app.services.rerankers.reranker_bge_reranker import BGEReranker
from app.services.rerankers.reranker_fake_reranker import FakeReranker
from app.services.rerankers.reranker_openai_compatible_reranker import OpenAICompatibleReranker


@lru_cache
def get_reranker() -> Reranker:
    provider = settings.reranker_provider.lower().strip()
    if provider == "fake":
        return FakeReranker()
    if provider == "openai_compatible":
        return OpenAICompatibleReranker(
            base_url=_required(settings.reranker_base_url, "RERANKER_BASE_URL"),
            api_key=settings.reranker_api_key,
            model=_required(settings.reranker_model, "RERANKER_MODEL"),
            endpoint_path=settings.reranker_endpoint_path,
        )
    if provider == "bge":
        return BGEReranker(model_name=_required(settings.bge_reranker_model, "BGE_RERANKER_MODEL"))

    raise ValueError(f"Unsupported reranker provider: {settings.reranker_provider}")


def _required(value: str | None, env_name: str) -> str:
    if value is None:
        raise ValueError(f"{env_name} is required for the selected provider.")
    stripped_value = value.strip()
    if not stripped_value:
        raise ValueError(f"{env_name} is required for the selected provider.")
    return stripped_value
