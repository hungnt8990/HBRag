"""Dựng document index (Stage 1) từ Postgres — nguồn sự thật.

Đọc Document + document_metadata, lấy ACL từ ``access.acl`` (bản nén đã lưu), embed
``trich_yeu + tom_tat`` nếu ``two_stage_document_embedding_enabled`` bật, rồi bulk index
vào ``hbrag_documents_v1``. Chạy lại an toàn (upsert theo document_id).

    python -m scripts.maintenance.build_document_index
    python -m scripts.maintenance.build_document_index --batch 200 --limit 1000
    python -m scripts.maintenance.build_document_index --skip-embed
    python -m scripts.maintenance.build_document_index --recreate   # xoá + tạo lại (thêm field BBQ)
"""

from __future__ import annotations

import argparse
import asyncio
import logging

import httpx
from sqlalchemy import select

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.models.document import Document
from app.services.llm_gateway import get_llm_gateway
from app.services.retrieval.retrieval_document_index import DocumentIndexStore
from app.services.security.security_acl_compressor import CompressedAcl
from app.services.security.security_acl_payload import acl_keys_from_acl

logger = logging.getLogger("build_document_index")


def _int_or_none(value: object) -> int | None:
    return value if isinstance(value, int) else None


def _str_or_none(value: object) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


async def _recreate_index(store: DocumentIndexStore) -> None:
    async with httpx.AsyncClient(timeout=30) as client:
        await client.delete(f"{store.url}/{store.index_name}")
    logger.info("Đã xoá index %s, sẽ tạo lại với mapping mới", store.index_name)


async def main(*, batch: int, limit: int | None, skip_embed: bool, recreate: bool) -> None:
    store = DocumentIndexStore()
    if recreate:
        await _recreate_index(store)
    await store.ensure_index()

    llm_gateway = None
    if not skip_embed and settings.two_stage_document_embedding_enabled:
        llm_gateway = get_llm_gateway()
        logger.info(
            "Embedding bật: model=%s dim=%d", settings.embedding_model, settings.embedding_dimension
        )
    else:
        logger.info(
            "Embedding tắt (skip_embed=%s, config=%s)",
            skip_embed,
            settings.two_stage_document_embedding_enabled,
        )

    processed = embedded = 0
    async with AsyncSessionLocal() as session:
        documents = (await session.execute(select(Document))).scalars().all()
        total = len(documents)
        logger.info("Tổng %d document trong Postgres", total)

        buf: list[dict] = []
        for doc in documents:
            if limit is not None and processed >= limit:
                break

            meta = doc.document_metadata or {}
            acl_data = (meta.get("access") or {}).get("acl") or {}
            compressed = CompressedAcl(
                allow_unit_ids=list(acl_data.get("allow_unit_ids", [])),
                allow_department_ids=list(acl_data.get("allow_department_ids", [])),
                allow_user_ids=list(acl_data.get("allow_user_ids", [])),
                deny_department_ids=list(acl_data.get("deny_department_ids", [])),
                deny_user_ids=list(acl_data.get("deny_user_ids", [])),
            )

            trich_yeu = _str_or_none(meta.get("trich_yeu") or meta.get("subject"))
            tom_tat = _str_or_none(meta.get("tom_tat"))

            embedding = None
            if llm_gateway is not None:
                embed_text = " ".join(filter(None, [trich_yeu, tom_tat])).strip()
                if embed_text:
                    try:
                        embedding = await llm_gateway.embed_query(embed_text)
                        embedded += 1
                    except Exception:
                        logger.warning("Embed thất bại document=%s", doc.id, exc_info=True)

            record: dict = {
                "document_id": str(doc.id),
                "acl_subjects": acl_keys_from_acl(compressed),
                "acl_deny_pb": sorted(compressed.deny_department_ids),
                "acl_deny_nv": sorted(compressed.deny_user_ids),
            }
            for key, value in (
                ("id_vb", _str_or_none(meta.get("id_vb"))),
                ("ky_hieu", _str_or_none(meta.get("ky_hieu") or meta.get("doc_code"))),
                ("trich_yeu", trich_yeu),
                ("tom_tat", tom_tat),
                ("nam", _int_or_none(meta.get("nam"))),
                ("ngay_vb", _str_or_none(meta.get("ngay_vb"))),
            ):
                if value is not None:
                    record[key] = value
            if embedding is not None:
                record["embedding"] = embedding

            buf.append(record)
            processed += 1
            if len(buf) >= batch:
                await store.bulk_index(buf)
                logger.info("Indexed %d/%d (embedded=%d)", processed, total, embedded)
                buf.clear()

        if buf:
            await store.bulk_index(buf)

    async with httpx.AsyncClient(timeout=30) as client:
        await client.post(f"{store.url}/{store.index_name}/_refresh")
    logger.info("Hoàn tất: %d document, %d embedded -> %s", processed, embedded, store.index_name)


def cli() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Dựng document-level index từ Postgres.")
    parser.add_argument("--batch", type=int, default=200)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--skip-embed", action="store_true")
    parser.add_argument("--recreate", action="store_true", help="Xoá index rồi tạo lại (thêm field BBQ)")
    args = parser.parse_args()
    asyncio.run(
        main(batch=args.batch, limit=args.limit, skip_embed=args.skip_embed, recreate=args.recreate)
    )


if __name__ == "__main__":
    cli()
