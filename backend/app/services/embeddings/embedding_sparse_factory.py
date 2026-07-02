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
    if provider == "learned":
        # Sparse học được (BGE-M3/SPLADE) qua HTTP — module độc lập, xem docstring
        # embedding_sparse_learned.py. Đổi provider -> phải re-embed (run_qdrant.bat).
        from app.services.embeddings.embedding_sparse_learned import (
            LearnedSparseEmbeddingProvider,
        )

        fallback = (
            HashingSparseEmbeddingProvider(dimensions=settings.sparse_embedding_hash_dimensions)
            if settings.sparse_learned_fallback_hashing
            else None
        )
        return LearnedSparseEmbeddingProvider(
            base_url=settings.sparse_learned_base_url or "",
            model=settings.sparse_learned_model,
            endpoint_path=settings.sparse_learned_endpoint_path,
            api_key=settings.sparse_learned_api_key,
            timeout_seconds=settings.sparse_learned_timeout_seconds,
            batch_size=settings.sparse_learned_batch_size,
            hash_dimensions=settings.sparse_embedding_hash_dimensions,
            hash_token_weights=settings.sparse_learned_hash_token_weights,
            fallback=fallback,
        )
    raise ValueError(f"Unsupported sparse embedding provider: {provider}")
