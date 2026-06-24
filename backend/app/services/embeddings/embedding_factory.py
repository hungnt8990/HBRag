from functools import lru_cache

from app.core.config import settings
from app.services.embeddings.embedding_base import EmbeddingProvider
from app.services.embeddings.embedding_fake_provider import FakeEmbeddingProvider
from app.services.embeddings.embedding_openai_provider import OpenAICompatibleEmbeddingProvider


@lru_cache
def get_embedding_provider() -> EmbeddingProvider:
    provider = settings.embedding_provider.lower().strip()
    if provider == "fake":
        return FakeEmbeddingProvider(dimension=settings.embedding_dimension)
    if provider == "openai_compatible":
        return OpenAICompatibleEmbeddingProvider(
            base_url=_required(settings.embedding_base_url, "EMBEDDING_BASE_URL"),
            api_key=settings.embedding_api_key,
            model=_required(settings.embedding_model, "EMBEDDING_MODEL"),
            dimension=settings.embedding_dimension,
        )

    raise ValueError(f"Unsupported embedding provider: {settings.embedding_provider}")


def _required(value: str | None, env_name: str) -> str:
    if value is None:
        raise ValueError(f"{env_name} is required for the selected provider.")
    stripped_value = value.strip()
    if not stripped_value:
        raise ValueError(f"{env_name} is required for the selected provider.")
    return stripped_value
