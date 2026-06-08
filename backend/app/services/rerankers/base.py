from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class RerankCandidate:
    chunk_id: str
    content: str


@dataclass(frozen=True)
class RerankScore:
    chunk_id: str
    score: float


class Reranker(Protocol):
    async def rerank(
        self,
        *,
        query: str,
        candidates: Sequence[RerankCandidate],
    ) -> list[RerankScore]:
        """Score candidate chunks for a query."""
