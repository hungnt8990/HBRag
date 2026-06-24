from __future__ import annotations

import re

from app.services.graph.graph_models import ExtractedEntity, ExtractedRelation

ALIAS_MAP = {
    "nlÄ‘": "ngÆ°á»i lao Ä‘á»™ng",
    "nsdlÄ‘": "ngÆ°á»i sá»­ dá»¥ng lao Ä‘á»™ng",
    "tÆ°lÄ‘tt": "thá»a Æ°á»›c lao Ä‘á»™ng táº­p thá»ƒ",
    "evncpc": "tá»•ng cÃ´ng ty Ä‘iá»‡n lá»±c miá»n trung",
}


class GraphMergeService:
    def normalize_entity_name(self, name: str) -> str:
        normalized = " ".join(name.strip().lower().split())
        normalized = re.sub(r"\s*([,.;:()])\s*", r"\1", normalized)
        return ALIAS_MAP.get(normalized, normalized)

    def merge_entities(self, entities: list[ExtractedEntity]) -> list[ExtractedEntity]:
        merged: dict[tuple[str, str], ExtractedEntity] = {}
        for entity in entities:
            normalized_name = self.normalize_entity_name(entity.normalized_name or entity.name)
            key = (normalized_name, entity.type)
            current = merged.get(key)
            candidate = ExtractedEntity(
                name=entity.name,
                normalized_name=normalized_name,
                type=entity.type,
                confidence=entity.confidence,
                evidence=entity.evidence,
            )
            if current is None or candidate.confidence >= current.confidence:
                merged[key] = candidate
        return list(merged.values())

    def merge_relations(
        self,
        relations: list[ExtractedRelation],
        *,
        entity_lookup: dict[str, str] | None = None,
    ) -> list[ExtractedRelation]:
        merged: dict[tuple[str, str, str], ExtractedRelation] = {}
        lookup = entity_lookup or {}
        for relation in relations:
            source = lookup.get(relation.source, self.normalize_entity_name(relation.source))
            target = lookup.get(relation.target, self.normalize_entity_name(relation.target))
            key = (source, target, relation.type)
            current = merged.get(key)
            candidate = ExtractedRelation(
                source=source,
                target=target,
                type=relation.type,
                description=relation.description,
                confidence=relation.confidence,
                evidence=relation.evidence,
            )
            if current is None or candidate.confidence >= current.confidence:
                merged[key] = candidate
        return list(merged.values())
