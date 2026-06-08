from __future__ import annotations

import re

from app.services.graph.extractors.base import ExtractionResult, GraphExtractor
from app.services.graph.models import ExtractedEntity, ExtractedRelation

ARTICLE_PATTERN = re.compile(r"(Điều\s+\d+)", flags=re.IGNORECASE)
ORG_PATTERN = re.compile(
    r"\b(EVNCPC|NLĐ|NSDLĐ|TƯLĐTT|tổng công ty điện lực miền trung)\b",
    flags=re.IGNORECASE,
)


class FakeGraphExtractor(GraphExtractor):
    async def extract(
        self,
        *,
        content: str,
        max_entities: int,
        max_relations: int,
    ) -> ExtractionResult:
        entities: list[ExtractedEntity] = []
        relationships: list[ExtractedRelation] = []

        for match in ARTICLE_PATTERN.finditer(content):
            name = match.group(1).strip()
            entities.append(
                ExtractedEntity(
                    name=name,
                    normalized_name=name.lower(),
                    type="legal_article",
                    confidence=0.95,
                    evidence=name,
                )
            )

        for match in ORG_PATTERN.finditer(content):
            name = match.group(1).strip()
            entities.append(
                ExtractedEntity(
                    name=name,
                    normalized_name=name.lower(),
                    type="organization",
                    confidence=0.85,
                    evidence=name,
                )
            )

        if len(entities) >= 2:
            relationships.append(
                ExtractedRelation(
                    source=entities[0].name,
                    target=entities[1].name,
                    type="lien_quan_den",
                    description="Derived by fake extractor.",
                    confidence=0.7,
                    evidence=entities[0].evidence,
                )
            )

        return ExtractionResult(
            entities=entities[:max_entities],
            relationships=relationships[:max_relations],
        )
