"""Batch check trạng thái văn bản trong Postgres (1 query cho cả batch)."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import String, cast, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import Document

DOFFICE_SOURCE_TYPE = "doffice_elasticsearch"


@dataclass
class PgStatus:
    id_vb: str
    exists: bool
    document_id: UUID | None
    pg_quyen_checksum: str | None
    has_embedding: bool


async def check_batch(session: AsyncSession, id_vb_list: list[str]) -> dict[str, PgStatus]:
    """1 query Postgres cho cả batch -> dict[id_vb, PgStatus]."""
    if not id_vb_list:
        return {}
    id_vb_col = cast(Document.document_metadata["id_vb"].astext, String)
    checksum_col = Document.document_metadata["access"]["quyen_checksum"].astext
    has_emb_col = Document.document_metadata["has_embedding"].astext
    stmt = select(Document.id, id_vb_col, checksum_col, has_emb_col).where(
        Document.source_type == DOFFICE_SOURCE_TYPE,
        id_vb_col.in_([str(v) for v in id_vb_list]),
    )
    rows = (await session.execute(stmt)).all()
    out: dict[str, PgStatus] = {}
    for doc_id, id_vb, checksum, has_emb in rows:
        if id_vb is None:
            continue
        out[str(id_vb)] = PgStatus(
            id_vb=str(id_vb),
            exists=True,
            document_id=doc_id,
            pg_quyen_checksum=checksum,
            has_embedding=str(has_emb).lower() == "true",
        )
    return out
