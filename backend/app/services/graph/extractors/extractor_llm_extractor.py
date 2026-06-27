from __future__ import annotations

import json
import re

from app.services.graph.extractors.extractor_base import ExtractionResult, GraphExtractor
from app.services.graph.graph_models import ExtractedEntity, ExtractedRelation
from app.services.llm_gateway import LLMGateway

PROMPT = """Extract entities and relationships from the following Vietnamese administrative/legal \
document chunk.

Return strict JSON only:
{
  "entities": [
    {
      "name": "...",
      "normalized_name": "...",
      "type": "organization|person|legal_article|benefit|condition|date|amount|\
document|policy|process|system|concept|other",
      "confidence": 0.0,
      "evidence": "short quote from chunk"
    }
  ],
  "relationships": [
    {
      "source": "...",
      "target": "...",
      "type": "quy_dinh|duoc_huong|ap_dung_cho|thuoc_dieu|lien_quan_den|\
can_cu_vao|thay_the|co_dieu_kien|ho_tro|other",
      "description": "...",
      "confidence": 0.0,
      "evidence": "short quote from chunk"
    }
  ]
}

Rules:
- Preserve exact Vietnamese legal terms.
- Extract benefits, durations, money amounts, dates, article numbers, organizations.
- For tables, extract row-level relationships.
- Example:
  "Kết hôn" -> "Nghỉ 03 ngày hưởng nguyên lương"
  relation type: "duoc_huong"
- Do not invent.
- Do not include explanations outside JSON.
"""

FENCE_PATTERN = re.compile(r"```(?:json)?\s*(.*?)```", flags=re.DOTALL | re.IGNORECASE)


class LLMGraphExtractor(GraphExtractor):
    def __init__(self, llm_provider: LLMGateway) -> None:
        self._llm_provider = llm_provider

    async def extract(
        self,
        *,
        content: str,
        max_entities: int,
        max_relations: int,
    ) -> ExtractionResult:
        raw = await self._llm_provider.generate(
            system_prompt=PROMPT,
            user_prompt=content,
        )
        payload = self._parse_json(raw)

        entities = _dedupe_entities(
            [
                ExtractedEntity(
                    name=str(item.get("name", "")).strip(),
                    normalized_name=str(item.get("normalized_name", "")).strip(),
                    type=str(item.get("type", "other")).strip() or "other",
                    confidence=float(item.get("confidence", 0.0) or 0.0),
                    evidence=str(item.get("evidence", "")).strip(),
                )
                for item in payload.get("entities", [])
                if isinstance(item, dict)
                and str(item.get("name", "")).strip()
                and _appears_in_source(str(item.get("name", "")), content)
            ]
        )
        relationships = [
            ExtractedRelation(
                source=str(item.get("source", "")).strip(),
                target=str(item.get("target", "")).strip(),
                type=str(item.get("type", "other")).strip() or "other",
                description=str(item.get("description", "")).strip(),
                confidence=float(item.get("confidence", 0.0) or 0.0),
                evidence=str(item.get("evidence", "")).strip(),
            )
            for item in payload.get("relationships", [])
            if isinstance(item, dict)
            and str(item.get("source", "")).strip()
            and str(item.get("target", "")).strip()
        ]
        return ExtractionResult(
            entities=entities[:max_entities],
            relationships=relationships[:max_relations],
        )

    @staticmethod
    def _parse_json(raw: str) -> dict[str, object]:
        candidate = raw.strip()
        match = FENCE_PATTERN.search(candidate)
        if match is not None:
            candidate = match.group(1).strip()
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start != -1 and end != -1:
            candidate = candidate[start : end + 1]
        data = json.loads(candidate)
        if not isinstance(data, dict):
            raise ValueError("LLM graph extractor must return a JSON object.")
        return data

def _dedupe_entities(entities: list[ExtractedEntity]) -> list[ExtractedEntity]:
    seen: set[str] = set()
    ordered: list[ExtractedEntity] = []
    for entity in entities:
        key = (entity.normalized_name or entity.name).casefold()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(entity)
    return ordered


def _appears_in_source(value: str, source_text: str) -> bool:
    clean_value = " ".join(str(value or "").split()).casefold()
    clean_source = " ".join(str(source_text or "").split()).casefold()
    return bool(clean_value and clean_value in clean_source)