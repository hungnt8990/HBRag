from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import anyio

from app.services.rerankers.base import RerankCandidate, RerankScore


class BGEReranker:
    def __init__(self, *, model_name: str = "BAAI/bge-reranker-base") -> None:
        self._model_name = model_name
        self._model: Any | None = None

    async def rerank(
        self,
        *,
        query: str,
        candidates: Sequence[RerankCandidate],
    ) -> list[RerankScore]:
        if not candidates:
            return []

        return await anyio.to_thread.run_sync(self._rerank_sync, query, candidates)

    def _rerank_sync(
        self,
        query: str,
        candidates: Sequence[RerankCandidate],
    ) -> list[RerankScore]:
        model = self._load_model()
        pairs = [(query, candidate.content) for candidate in candidates]
        raw_scores = model.predict(pairs)

        return [
            RerankScore(chunk_id=candidate.chunk_id, score=float(score))
            for candidate, score in zip(candidates, raw_scores, strict=True)
        ]

    def _load_model(self) -> Any:
        if self._model is not None:
            return self._model

        try:
            from sentence_transformers import CrossEncoder
        except ImportError as exc:
            raise RuntimeError(
                "BGEReranker requires the optional 'sentence-transformers' package."
            ) from exc

        self._model = CrossEncoder(self._model_name)
        return self._model
