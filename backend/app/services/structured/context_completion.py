from __future__ import annotations

from typing import Any

from app.services.structured.row_normalizer import normalize_structured_row
from app.services.structured.row_scorer import score_structured_row


def collect_structured_evidence(
    *,
    query: str,
    context_chunks: list[Any],
    min_score: float = 0.25,
) -> list[Any]:
    evidences = []

    for context_chunk in context_chunks:
        row = normalize_structured_row(
            chunk=context_chunk.chunk,
            citation=context_chunk.citation_index,
        )
        if row is None:
            continue

        evidence = score_structured_row(query, row)
        if evidence.score >= min_score:
            evidences.append(evidence)

    evidences.sort(key=lambda item: item.score, reverse=True)
    return evidences