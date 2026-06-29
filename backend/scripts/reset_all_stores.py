"""Reset 3 DB cho thiết kế DOffice mới (XÓA KHÔNG HỒI PHỤC).

Xóa toàn bộ dữ liệu văn bản ở PostgreSQL + Elasticsearch + Qdrant, GIỮ LẠI danh mục
(đơn vị/phòng ban/nhân viên), cấu hình ingestion và rag runtime. Tạo lại:
- ES: 1 index BM25 cấp văn bản (settings.doffice_documents_index_name).
- Qdrant: 2 collection (chunks + docmeta).

Chạy:  python -m scripts.reset_all_stores --yes
Thêm --keep-pg / --keep-es / --keep-qdrant để bỏ qua từng phần.
"""

from __future__ import annotations

import argparse
import asyncio
import logging

import httpx
from qdrant_client import AsyncQdrantClient
from sqlalchemy import text

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.services.retrieval.retrieval_doffice_bm25 import DofficeBm25DocumentStore
from app.services.vector.vector_store import (
    get_doffice_chunks_vector_store,
    get_doffice_docmeta_vector_store,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("reset_all_stores")

# Bảng dữ liệu văn bản + trạng thái job -> XÓA. CASCADE để dọn bảng tham chiếu (vd
# citations, log). GIỮ: dm_don_vi/dm_phong_ban/dm_nhan_vien, organizations, users,
# roles, ingestion_profile_configs, rag_runtime_configs, knowledge_bases.
_PG_TRUNCATE_TABLES = (
    "documents",
    "document_files",
    "chunks",
    "citations",
    "doffice_raw_documents",
    "knowledge_artifacts",
    "document_pipeline_logs",
    "document_access_logs",
    "graph_extraction_logs",
    "graph_document_status",
    "retrieval_logs",
    "job_sync_runs",
    "job_sync_checkpoints",
    "job_sync_retries",
)


async def reset_postgres() -> None:
    async with AsyncSessionLocal() as session:
        existing = (
            await session.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'public' AND table_name = ANY(:names)"
                ),
                {"names": list(_PG_TRUNCATE_TABLES)},
            )
        ).scalars().all()
        if not existing:
            logger.info("PG: không có bảng nào để xóa.")
            return
        joined = ", ".join(f'"{name}"' for name in existing)
        await session.execute(text(f"TRUNCATE TABLE {joined} RESTART IDENTITY CASCADE"))
        await session.commit()
        logger.info("PG: đã TRUNCATE %s bảng: %s", len(existing), ", ".join(existing))


async def reset_elasticsearch() -> None:
    # Xóa các index cũ + index doffice mới (clean recreate).
    old_indices = {
        settings.elasticsearch_index_name,          # chunk BM25 cũ
        "hbrag_documents_v1",                        # two-stage doc index cũ
        settings.doffice_documents_index_name,       # index mới (recreate sạch)
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        for index in sorted(i for i in old_indices if i):
            resp = await client.delete(f"{settings.elasticsearch_url.rstrip('/')}/{index}")
            logger.info("ES: DELETE %s -> HTTP %s", index, resp.status_code)
    store = DofficeBm25DocumentStore()
    await store.ensure_index()
    logger.info("ES: đã tạo lại index BM25 %s", store.index_name)


async def reset_qdrant() -> None:
    client = AsyncQdrantClient(url=settings.qdrant_url, api_key=settings.qdrant_api_key)
    collections = (await client.get_collections()).collections
    for col in collections:
        await client.delete_collection(collection_name=col.name)
        logger.info("Qdrant: drop collection %s", col.name)
    await get_doffice_chunks_vector_store().ensure_collection()
    await get_doffice_docmeta_vector_store().ensure_collection()
    logger.info(
        "Qdrant: đã tạo lại 2 collection %s + %s",
        settings.qdrant_chunks_collection_name,
        settings.qdrant_docmeta_collection_name,
    )


async def main(*, keep_pg: bool, keep_es: bool, keep_qdrant: bool) -> None:
    if not keep_pg:
        await reset_postgres()
    if not keep_es:
        await reset_elasticsearch()
    if not keep_qdrant:
        await reset_qdrant()
    logger.info("Reset hoàn tất.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Reset 3 DB cho thiết kế DOffice mới (XÓA KHÔNG HỒI PHỤC).")
    parser.add_argument("--yes", action="store_true", help="Bắt buộc: xác nhận xóa dữ liệu.")
    parser.add_argument("--keep-pg", action="store_true")
    parser.add_argument("--keep-es", action="store_true")
    parser.add_argument("--keep-qdrant", action="store_true")
    args = parser.parse_args()
    if not args.yes:
        raise SystemExit("Cần --yes để xác nhận XÓA KHÔNG HỒI PHỤC dữ liệu 3 DB.")
    asyncio.run(main(keep_pg=args.keep_pg, keep_es=args.keep_es, keep_qdrant=args.keep_qdrant))
