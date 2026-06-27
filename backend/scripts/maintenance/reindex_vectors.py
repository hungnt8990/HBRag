"""Re-index Qdrant vectors after changing embedding input/payload metadata.

Usage examples:
  python scripts/maintenance/reindex_vectors.py --document-id <uuid>
  python scripts/maintenance/reindex_vectors.py --all-indexable
  python scripts/maintenance/reindex_vectors.py --all-indexable --limit 50

This script does not parse/chunk documents again. It reads existing chunks from
PostgreSQL, builds the new enriched embedding_text, deletes old Qdrant points
for each document, and upserts fresh dense/sparse vectors + payload.
"""
from __future__ import annotations

import argparse
import asyncio
from uuid import UUID

from sqlalchemy import select

from app.db.session import AsyncSessionLocal
from app.models.document import Document
from app.repositories.documents import DocumentRepository
from app.services.embeddings.embedding_sparse_factory import get_sparse_embedding_provider
from app.services.llm_gateway import get_llm_gateway
from app.services.vector.vector_indexing_service import VectorIndexingService
from app.services.vector.vector_store import get_vector_store


async def _candidate_document_ids(*, all_indexable: bool, document_id: str | None, limit: int | None) -> list[UUID]:
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


async def _reindex_one(document_id: UUID) -> None:
    async with AsyncSessionLocal() as session:
        repository = DocumentRepository(session)
        service = VectorIndexingService(
            repository=repository,
            llm_gateway=get_llm_gateway(),
            vector_store=get_vector_store(),
            sparse_embedding_provider=get_sparse_embedding_provider(),
        )
        response = await service.index_document(document_id)
        print(f"indexed document={response.document_id} chunks={response.indexed_chunk_count}")


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--document-id", help="Re-index one document UUID")
    parser.add_argument("--all-indexable", action="store_true", help="Re-index all chunked/indexed documents")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of documents when using --all-indexable")
    args = parser.parse_args()

    document_ids = await _candidate_document_ids(
        all_indexable=args.all_indexable,
        document_id=args.document_id,
        limit=args.limit,
    )
    print(f"Found {len(document_ids)} document(s) to re-index")
    for doc_id in document_ids:
        await _reindex_one(doc_id)


if __name__ == "__main__":
    asyncio.run(main())
