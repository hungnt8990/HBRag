from __future__ import annotations

import re
from collections.abc import Sequence

from app.services.rerankers.reranker_base import RerankCandidate, RerankScore

TOKEN_PATTERN = re.compile(r"\w+", flags=re.UNICODE)


class FakeReranker:
    async def rerank(
        self,
        *,
        query: str,
        candidates: Sequence[RerankCandidate],
    ) -> list[RerankScore]:
        query_tokens = self._tokenize(query)
        return [
            RerankScore(
                chunk_id=candidate.chunk_id,
                score=self._score(query_tokens=query_tokens, content=candidate.content),
            )
            for candidate in candidates
        ]

    @staticmethod
    def _score(*, query_tokens: set[str], content: str) -> float:
        if not query_tokens:
            return 0.0

        content_tokens = FakeReranker._tokenize(content)
        if not content_tokens:
            return 0.0

        overlap = query_tokens.intersection(content_tokens)
        return len(overlap) / len(query_tokens)

    @staticmethod
    def _tokenize(text: str) -> set[str]:
        return {token.lower() for token in TOKEN_PATTERN.findall(text)}
