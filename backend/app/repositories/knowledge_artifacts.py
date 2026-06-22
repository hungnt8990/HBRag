from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from sqlalchemy import String, cast, delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.knowledge_artifact import KnowledgeArtifact


@dataclass(frozen=True)
class KnowledgeArtifactCreate:
    artifact_type: str
    context_type: str
    canonical_text: str
    source_chunk_ids: list[str] = field(default_factory=list)
    idea_block_type: str | None = None
    title: str | None = None
    summary_text: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    evidence_chunk_ids: list[str] = field(default_factory=list)
    scope_key: str | None = None
    content_hash: str | None = None
    dedup_hash: str | None = None
    embedding_status: str = "pending"
    structured_data: dict[str, Any] = field(default_factory=dict)
    normalized_identifiers: dict[str, Any] = field(default_factory=dict)
    citation_map: dict[str, Any] = field(default_factory=dict)
    confidence_score: float = 0.0
    extraction_method: str = "deterministic"
    status: str = "ready"


class KnowledgeArtifactRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def replace_for_document(
        self,
        document_id: UUID,
        artifacts: Sequence[KnowledgeArtifactCreate],
    ) -> list[KnowledgeArtifact]:
        await self.delete_for_document(document_id)
        return await self.create_for_document(document_id, artifacts)

    async def delete_for_document(self, document_id: UUID) -> None:
        await self._session.execute(
            delete(KnowledgeArtifact).where(KnowledgeArtifact.document_id == document_id)
        )
        await self._session.flush()

    async def create_for_document(
        self,
        document_id: UUID,
        artifacts: Sequence[KnowledgeArtifactCreate],
    ) -> list[KnowledgeArtifact]:
        models = [self._to_model(document_id=document_id, artifact=artifact) for artifact in artifacts]
        self._session.add_all(models)
        await self._session.flush()
        return models

    async def list_for_document(
        self,
        document_id: UUID,
        *,
        statuses: set[str] | None = None,
    ) -> list[KnowledgeArtifact]:
        statement = (
            select(KnowledgeArtifact)
            .options(selectinload(KnowledgeArtifact.document))
            .where(KnowledgeArtifact.document_id == document_id)
            .order_by(KnowledgeArtifact.confidence_score.desc(), KnowledgeArtifact.created_at.asc())
        )
        if statuses is not None:
            if not statuses:
                return []
            statement = statement.where(KnowledgeArtifact.status.in_(statuses))
        result = await self._session.execute(statement)
        return list(result.scalars().all())

    async def get_by_ids(self, artifact_ids: Sequence[UUID | str]) -> list[KnowledgeArtifact]:
        ids = [UUID(str(artifact_id)) for artifact_id in artifact_ids if str(artifact_id).strip()]
        if not ids:
            return []
        statement = (
            select(KnowledgeArtifact)
            .options(selectinload(KnowledgeArtifact.document))
            .where(KnowledgeArtifact.id.in_(ids))
        )
        result = await self._session.execute(statement)
        return list(result.scalars().all())

    async def update_embedding_status_for_document(
        self,
        document_id: UUID,
        status: str,
    ) -> None:
        await self._session.execute(
            update(KnowledgeArtifact)
            .where(KnowledgeArtifact.document_id == document_id)
            .values(embedding_status=status)
        )
        await self._session.flush()

    async def search_exact(
        self,
        *,
        terms: Sequence[str],
        document_ids: set[UUID] | None = None,
        artifact_types: set[str] | None = None,
        context_types: set[str] | None = None,
        min_confidence: float = 0.0,
        limit: int = 10,
    ) -> list[KnowledgeArtifact]:
        clean_terms = self._clean_terms(terms)
        if not clean_terms:
            return []
        if document_ids is not None and not document_ids:
            return []

        clauses = []
        normalized_identifiers = cast(KnowledgeArtifact.normalized_identifiers, String)
        structured_data = cast(KnowledgeArtifact.structured_data, String)
        idea_metadata = cast(KnowledgeArtifact.idea_metadata, String)
        for term in clean_terms:
            like_value = f"%{term}%"
            clauses.append(
                or_(
                    KnowledgeArtifact.title.ilike(like_value),
                    KnowledgeArtifact.canonical_text.ilike(like_value),
                    KnowledgeArtifact.scope_key.ilike(like_value),
                    normalized_identifiers.ilike(like_value),
                    structured_data.ilike(like_value),
                    idea_metadata.ilike(like_value),
                )
            )

        statement = (
            select(KnowledgeArtifact)
            .options(selectinload(KnowledgeArtifact.document))
            .where(
                KnowledgeArtifact.status == "ready",
                KnowledgeArtifact.confidence_score >= min_confidence,
                or_(*clauses),
            )
            .order_by(KnowledgeArtifact.confidence_score.desc(), KnowledgeArtifact.updated_at.desc())
            .limit(limit)
        )
        if document_ids is not None:
            statement = statement.where(KnowledgeArtifact.document_id.in_(document_ids))
        if artifact_types is not None:
            if not artifact_types:
                return []
            statement = statement.where(
                or_(
                    KnowledgeArtifact.artifact_type.in_(artifact_types),
                    KnowledgeArtifact.idea_block_type.in_(artifact_types),
                )
            )
        if context_types is not None:
            if not context_types:
                return []
            statement = statement.where(KnowledgeArtifact.context_type.in_(context_types))

        result = await self._session.execute(statement)
        return list(result.scalars().all())

    async def commit(self) -> None:
        await self._session.commit()

    async def rollback(self) -> None:
        await self._session.rollback()

    @classmethod
    def _to_model(
        cls,
        *,
        document_id: UUID,
        artifact: KnowledgeArtifactCreate,
    ) -> KnowledgeArtifact:
        idea_block_type = artifact.idea_block_type or cls._idea_block_type_for_artifact(artifact.artifact_type)
        evidence_chunk_ids = list(artifact.evidence_chunk_ids or artifact.source_chunk_ids)
        metadata = {
            **dict(artifact.structured_data or {}),
            **dict(artifact.metadata or {}),
            "evidence_chunk_ids": evidence_chunk_ids,
        }
        source_chunk_ids = list(artifact.source_chunk_ids or evidence_chunk_ids)
        content_hash = artifact.content_hash or cls._hash_text(artifact.canonical_text)
        scope_key = artifact.scope_key or cls._default_scope_key(
            document_id=document_id,
            idea_block_type=idea_block_type,
            metadata=metadata,
        )
        dedup_hash = artifact.dedup_hash or cls._hash_text(
            "|".join([scope_key, idea_block_type or "", content_hash])
        )
        return KnowledgeArtifact(
            document_id=document_id,
            source_chunk_ids=source_chunk_ids,
            artifact_type=artifact.artifact_type,
            idea_block_type=idea_block_type,
            context_type=artifact.context_type,
            title=artifact.title,
            canonical_text=artifact.canonical_text,
            summary_text=artifact.summary_text,
            idea_metadata=metadata,
            evidence_chunk_ids=evidence_chunk_ids,
            scope_key=scope_key,
            content_hash=content_hash,
            dedup_hash=dedup_hash,
            embedding_status=artifact.embedding_status,
            structured_data=dict(artifact.structured_data),
            normalized_identifiers=dict(artifact.normalized_identifiers),
            citation_map=dict(artifact.citation_map),
            confidence_score=float(artifact.confidence_score),
            extraction_method=artifact.extraction_method,
            status=artifact.status,
        )

    @staticmethod
    def _idea_block_type_for_artifact(artifact_type: str) -> str:
        return {
            "document_profile": "document_identity",
            "identifier_lookup": "document_identity",
            "procedure_artifact": "implementation_plan",
            "policy_rule_artifact": "legal_clause",
            "table_row_artifact": "assignment_table_row",
            "person_assignment_artifact": "assignment_table_row",
        }.get(str(artifact_type or ""), str(artifact_type or "summary_block"))

    @staticmethod
    def _default_scope_key(
        *,
        document_id: UUID,
        idea_block_type: str | None,
        metadata: dict[str, Any],
    ) -> str:
        doc_code = metadata.get("doc_code") or metadata.get("document_code") or metadata.get("official_dispatch_code")
        issued_date = metadata.get("issued_date") or metadata.get("ngay_vb")
        issuing_org = metadata.get("issuing_org") or metadata.get("issuer") or metadata.get("noi_ban_hanh")
        return "|".join(
            str(value or "").casefold()
            for value in (doc_code or document_id, issued_date, issuing_org, idea_block_type)
        )

    @staticmethod
    def _hash_text(text: str) -> str:
        return hashlib.sha256((text or "").encode("utf-8")).hexdigest()

    @staticmethod
    def _clean_terms(terms: Sequence[str]) -> list[str]:
        ordered: list[str] = []
        seen: set[str] = set()
        for term in terms:
            clean = " ".join(str(term or "").split()).strip(" ?!.,;:")
            if len(clean) < 2:
                continue
            key = clean.casefold()
            if key in seen:
                continue
            seen.add(key)
            ordered.append(clean)
        return ordered
