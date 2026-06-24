from functools import lru_cache

from app.core.config import settings
from app.services.embeddings.embedding_sparse import (
    HashingSparseEmbeddingProvider,
    SparseEmbeddingProvider,
)


@lru_cache
def get_sparse_embedding_provider() -> SparseEmbeddingProvider | None:
    if not settings.sparse_embedding_enabled:
        return None
    provider = settings.sparse_embedding_provider.lower().strip()
    if provider == "hashing":
        return HashingSparseEmbeddingProvider(
            dimensions=settings.sparse_embedding_hash_dimensions
        )
    raise ValueError(f"Unsupported sparse embedding provider: {provider}")
