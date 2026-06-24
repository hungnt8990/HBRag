from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.services.graph.graph_models import ExtractedEntity, ExtractedRelation


@dataclass(frozen=True)
class ExtractionResult:
    entities: list[ExtractedEntity]
    relationships: list[ExtractedRelation]


class GraphExtractor(Protocol):
    async def extract(
        self,
        *,
        content: str,
        max_entities: int,
        max_relations: int,
    ) -> ExtractionResult:
        """Extract entities and relationships from chunk text."""
