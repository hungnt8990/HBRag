from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from sqlalchemy import String, cast, delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.knowledge_artifact import KnowledgeArtifact


@dataclass(frozen=True)
class KnowledgeArtifactCreate:
    artifact_type: str
    context_type: str
    canonical_text: str
    source_chunk_ids: list[str] = field(default_factory=list)
    title: str | None = None
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
        models = [
            KnowledgeArtifact(
                document_id=document_id,
                source_chunk_ids=list(artifact.source_chunk_ids),
                artifact_type=artifact.artifact_type,
                context_type=artifact.context_type,
                title=artifact.title,
                canonical_text=artifact.canonical_text,
                structured_data=dict(artifact.structured_data),
                normalized_identifiers=dict(artifact.normalized_identifiers),
                citation_map=dict(artifact.citation_map),
                confidence_score=float(artifact.confidence_score),
                extraction_method=artifact.extraction_method,
                status=artifact.status,
            )
            for artifact in artifacts
        ]
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
        for term in clean_terms:
            like_value = f"%{term}%"
            clauses.append(
                or_(
                    KnowledgeArtifact.title.ilike(like_value),
                    KnowledgeArtifact.canonical_text.ilike(like_value),
                    normalized_identifiers.ilike(like_value),
                    structured_data.ilike(like_value),
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
            statement = statement.where(KnowledgeArtifact.artifact_type.in_(artifact_types))
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
