from __future__ import annotations

from dataclasses import replace
from difflib import SequenceMatcher
from typing import Any

from app.core.config import settings
from app.repositories.knowledge_artifacts import KnowledgeArtifactCreate


class SemanticDedupService:
    """Scoped semantic/lexical de-duplication for typed IdeaBlocks.

    This service is intentionally conservative: it only compares artifacts that
    are already in the same safe metadata scope and refuses to merge rows or
    legal/deadline facts that differ on key discriminators.
    """

    SAFE_KEYS = (
        "doc_code",
        "issued_date",
        "issuing_org",
        "assigned_unit",
        "assigned_units",
        "person_name",
        "person_names",
        "stt",
        "row_index",
        "article_no",
        "clause_no",
        "deadline",
    )

    LIST_MERGE_KEYS = (
        "evidence_chunk_ids",
        "source_chunk_ids",
        "person_names",
        "assigned_units",
        "recipient_units",
        "department_names",
        "project_names",
        "system_names",
    )

    def __init__(self, *, enabled: bool | None = None, threshold: float | None = None) -> None:
        self.enabled = (
            bool(getattr(settings, "idea_block_semantic_dedup_enabled", True))
            if enabled is None
            else enabled
        )
        self.threshold = float(
            getattr(settings, "idea_block_semantic_dedup_threshold", 0.92)
            if threshold is None
            else threshold
        )

    def dedupe(self, artifacts: list[KnowledgeArtifactCreate]) -> list[KnowledgeArtifactCreate]:
        by_hash: dict[str, KnowledgeArtifactCreate] = {}
        ordered: list[KnowledgeArtifactCreate] = []
        for artifact in artifacts:
            key = artifact.dedup_hash or self._hash_key(artifact)
            existing = by_hash.get(key)
            if existing is None:
                by_hash[key] = artifact
                ordered.append(artifact)
                continue
            merged = self.merge(existing, artifact)
            by_hash[key] = merged
            ordered[ordered.index(existing)] = merged

        if not self.enabled:
            return ordered

        result: list[KnowledgeArtifactCreate] = []
        for artifact in ordered:
            merged = False
            for index, existing in enumerate(result):
                if self.should_merge(existing, artifact):
                    result[index] = self.merge(existing, artifact)
                    merged = True
                    break
            if not merged:
                result.append(artifact)
        return result

    def should_merge(self, left: KnowledgeArtifactCreate, right: KnowledgeArtifactCreate) -> bool:
        if (left.idea_block_type or left.artifact_type) != (right.idea_block_type or right.artifact_type):
            return False
        if not self._same_safe_scope(left, right):
            return False
        return self._similarity(left.canonical_text, right.canonical_text) >= self.threshold

    def merge(self, left: KnowledgeArtifactCreate, right: KnowledgeArtifactCreate) -> KnowledgeArtifactCreate:
        evidence = self._unique(
            [
                *left.evidence_chunk_ids,
                *right.evidence_chunk_ids,
                *left.source_chunk_ids,
                *right.source_chunk_ids,
            ]
        )
        metadata = self._merge_metadata(left.metadata, right.metadata)
        metadata["evidence_chunk_ids"] = evidence
        structured_data = self._merge_metadata(left.structured_data, right.structured_data)
        structured_data["evidence_chunk_ids"] = evidence
        left_score = float(left.confidence_score or 0.0)
        right_score = float(right.confidence_score or 0.0)
        better_text = (
            left.canonical_text
            if (len(left.canonical_text or ""), left_score) >= (len(right.canonical_text or ""), right_score)
            else right.canonical_text
        )
        return replace(
            left,
            canonical_text=better_text,
            summary_text=left.summary_text or right.summary_text,
            metadata=metadata,
            structured_data=structured_data,
            evidence_chunk_ids=evidence,
            source_chunk_ids=evidence,
            normalized_identifiers=self._merge_metadata(
                left.normalized_identifiers,
                right.normalized_identifiers,
            ),
            citation_map={
                "document_id": (left.citation_map or {}).get("document_id")
                or (right.citation_map or {}).get("document_id"),
                "chunks": [
                    *list((left.citation_map or {}).get("chunks") or []),
                    *list((right.citation_map or {}).get("chunks") or []),
                ],
            },
            confidence_score=max(left_score, right_score),
        )

    def _same_safe_scope(self, left: KnowledgeArtifactCreate, right: KnowledgeArtifactCreate) -> bool:
        left_metadata = left.metadata or {}
        right_metadata = right.metadata or {}
        for key in self.SAFE_KEYS:
            left_value = self._scope_value(left_metadata.get(key))
            right_value = self._scope_value(right_metadata.get(key))
            if left_value and right_value and left_value != right_value:
                return False
        return True

    @staticmethod
    def _similarity(left: str, right: str) -> float:
        return SequenceMatcher(None, left or "", right or "").ratio()

    @staticmethod
    def _hash_key(artifact: KnowledgeArtifactCreate) -> str:
        return "|".join(
            [
                artifact.idea_block_type or artifact.artifact_type,
                artifact.scope_key or "",
                artifact.content_hash or artifact.canonical_text,
            ]
        )

    def _merge_metadata(self, left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
        merged = {**(left or {})}
        for key, value in (right or {}).items():
            if value in (None, "", [], {}):
                continue
            if key in self.LIST_MERGE_KEYS:
                merged[key] = self._unique([*self._as_list(merged.get(key)), *self._as_list(value)])
            elif key not in merged or merged[key] in (None, "", [], {}):
                merged[key] = value
        return merged

    @staticmethod
    def _as_list(value: Any) -> list[Any]:
        if value in (None, "", [], {}):
            return []
        if isinstance(value, list):
            return value
        if isinstance(value, tuple | set):
            return list(value)
        return [value]

    @staticmethod
    def _unique(values: list[Any]) -> list[Any]:
        output: list[Any] = []
        seen: set[str] = set()
        for value in values:
            if value in (None, "", [], {}):
                continue
            key = str(value)
            if key in seen:
                continue
            seen.add(key)
            output.append(value)
        return output

    @classmethod
    def _scope_value(cls, value: Any) -> str:
        return "|".join(str(item).strip().casefold() for item in cls._as_list(value) if str(item).strip())
