"""Compile and re-index knowledge artifacts for existing documents.

Usage examples:
  python scripts/maintenance/reindex_artifacts.py --document-id <uuid>
  python scripts/maintenance/reindex_artifacts.py --all-indexable
  python scripts/maintenance/reindex_artifacts.py --all-indexable --limit 50

This script does not parse or chunk documents again. It reads existing chunks
from PostgreSQL, compiles deterministic knowledge artifacts, replaces the
document's artifact rows, and upserts artifact vectors into the configured
Qdrant artifact collection.
"""
from __future__ import annotations

import argparse
import asyncio
from uuid import UUID

from sqlalchemy import select

from app.db.session import AsyncSessionLocal
from app.models.document import Document
from app.repositories.documents import DocumentRepository
from app.repositories.knowledge_artifacts import KnowledgeArtifactRepository
from app.services.embeddings.factory import get_embedding_provider
from app.services.embeddings.sparse_factory import get_sparse_embedding_provider
from app.services.knowledge_artifact_compiler import (
    KnowledgeArtifactCompiler,
    KnowledgeArtifactCompilerConfig,
)
from app.services.knowledge_artifact_indexing_service import KnowledgeArtifactIndexingService
from app.services.vector_store import get_artifact_vector_store


async def _candidate_document_ids(
    *,
    all_indexable: bool,
    document_id: str | None,
    limit: int | None,
) -> list[UUID]:
    if document_id:
        return [UUID(document_id)]
    if not all_indexable:
        raise SystemExit("Pass --document-id <uuid> or --all-indexable.")
    async with AsyncSessionLocal() as session:
        stmt = (
            select(Document.id)
            .where(Document.status.in_(["chunked", "indexed"]))
            .order_by(Document.created_at.desc())
        )
        if limit:
            stmt = stmt.limit(limit)
        result = await session.execute(stmt)
        return list(result.scalars().all())


async def _reindex_one(document_id: UUID, *, enable_llm_extraction: bool) -> None:
    async with AsyncSessionLocal() as session:
        document_repository = DocumentRepository(session)
        artifact_repository = KnowledgeArtifactRepository(session)
        document = await document_repository.get_document(document_id)
        if document is None:
            print(f"skipped document={document_id} reason=not_found")
            return

        chunks = await document_repository.list_chunks_for_document(document_id)
        compiler = KnowledgeArtifactCompiler(
            config=KnowledgeArtifactCompilerConfig(
                enable_llm_extraction=enable_llm_extraction,
            )
        )
        artifacts = compiler.compile_document(
            document=document,
            chunks=chunks,
            docling_metadata=dict((document.document_metadata or {}).get("parsed_metadata") or {}),
        )
        await artifact_repository.replace_for_document(document_id, artifacts)
        await artifact_repository.commit()

        response = await KnowledgeArtifactIndexingService(
            repository=artifact_repository,
            embedding_provider=get_embedding_provider(),
            vector_store=get_artifact_vector_store(),
            sparse_embedding_provider=get_sparse_embedding_provider(),
        ).index_document(document_id)
        print(
            "indexed artifacts "
            f"document={response.document_id} "
            f"compiled={len(artifacts)} "
            f"indexed={response.indexed_artifact_count} "
            f"status={response.status}"
        )


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--document-id", help="Compile/index artifacts for one document UUID")
    parser.add_argument("--all-indexable", action="store_true", help="Compile/index artifacts for all chunked/indexed documents")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of documents when using --all-indexable")
    parser.add_argument("--enable-llm-extraction", action="store_true", help="Enable optional LLM artifact extraction config")
    args = parser.parse_args()

    document_ids = await _candidate_document_ids(
        all_indexable=args.all_indexable,
        document_id=args.document_id,
        limit=args.limit,
    )
    print(f"Found {len(document_ids)} document(s) to compile/index artifacts")
    for doc_id in document_ids:
        await _reindex_one(doc_id, enable_llm_extraction=args.enable_llm_extraction)


if __name__ == "__main__":
    asyncio.run(main())
