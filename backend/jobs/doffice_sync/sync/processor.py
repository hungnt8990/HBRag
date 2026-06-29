"""Xử lý từng văn bản — 5 case: created / acl_updated / emb_updated / skipped / no_acl.

KHÔNG đẩy Qdrant. CASE 4 re-resolve ACL từ quyen MỚI (không dùng recompress_document
vì hàm đó re-resolve từ raw_assignment đã lưu — sai khi nguồn quyền thay đổi).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import Document
from app.services.retrieval.retrieval_document_index import DocumentIndexStore
from app.services.security.security_acl_payload import acl_keys_from_acl
from app.services.security.security_acl_resolver import resolve_doffice_and_compress
from jobs.doffice_sync.clients.quyen_client import QuyenRecord
from jobs.doffice_sync.clients.vanban_client import VanbanRecord
from jobs.doffice_sync.sync.checker import DOFFICE_SOURCE_TYPE, PgStatus
from jobs.doffice_sync.stores.retry import upsert_retry

logger = logging.getLogger("doffice_sync.processor")


@dataclass
class SyncResult:
    id_vb: str
    action: str  # created|acl_updated|emb_updated|skipped|no_acl|error
    has_embedding: bool = False
    error: str | None = None
    duration_ms: int = 0


async def _try_embed(gateway: Any, text: str) -> tuple[list[float] | None, bool]:
    if not text.strip() or gateway is None:
        return None, False
    try:
        return await gateway.embed_query(text), True
    except Exception:
        logger.warning("Embed thất bại", exc_info=True)
        return None, False


def _resolve(quyen: QuyenRecord, catalog: Any, unit_tree: Any):
    return resolve_doffice_and_compress(
        don_vi_list=quyen.don_vi_list,
        phong_ban_list=quyen.phong_ban_list,
        ca_nhan_list=quyen.ca_nhan_list,
        catalog=catalog,
        unit_tree=unit_tree,
    )


def _access_block(quyen: QuyenRecord, acl: Any, signature: str) -> dict[str, Any]:
    return {
        "quyen_checksum": quyen.quyen_checksum,
        "quyen_ngay_capnhat": quyen.quyen_ngay_capnhat,
        "raw_assignment": {
            "don_vi_list": quyen.don_vi_list,
            "phong_ban_list": quyen.phong_ban_list,
            "ca_nhan_list": quyen.ca_nhan_list,
        },
        "acl": acl.to_dict(),
        "acl_ver": signature,
    }


async def process_one(
    session: AsyncSession,
    store: DocumentIndexStore,
    gateway: Any,
    catalog: Any,
    unit_tree: Any,
    vanban: VanbanRecord,
    quyen: QuyenRecord | None,
    pg: PgStatus,
    *,
    signature: str,
    in_es: bool = True,
    dry_run: bool = False,
) -> SyncResult:
    start = time.monotonic()

    def done(action: str, *, has_embedding: bool = False, error: str | None = None) -> SyncResult:
        return SyncResult(
            id_vb=vanban.id_vb,
            action=action,
            has_embedding=has_embedding,
            error=error,
            duration_ms=int((time.monotonic() - start) * 1000),
        )

    # CASE 1 — no_acl
    if quyen is None or not quyen.has_acl:
        if not dry_run:
            await upsert_retry(session, vanban.id_vb, reason="no_acl", delay_minutes=60)
        logger.warning("id_vb=%s -> no_acl (chưa có quyền) -> retry", vanban.id_vb)
        return done("no_acl")

    same_checksum = pg.exists and pg.pg_quyen_checksum == quyen.quyen_checksum

    # CASE 2 — skipped: đã có PG (quyền không đổi + có embedding) VÀ đã có record ES.
    if same_checksum and pg.has_embedding and in_es:
        return done("skipped", has_embedding=True)

    acl, _assignment, warnings = _resolve(quyen, catalog, unit_tree)
    # Chi tiết từng cảnh báo ACL -> full.log (DEBUG); console/info chỉ 1 dòng tổng.
    for warning in warnings:
        logger.debug("id_vb=%s ACL: %s", vanban.id_vb, warning)
    if warnings:
        logger.info("id_vb=%s: %d cảnh báo ACL (chi tiết ở full.log)", vanban.id_vb, len(warnings))

    # CASE 5 — created (chưa có trong PG).
    if not pg.exists:
        embedding, ok = await _try_embed(gateway, vanban.embed_text)
        if not dry_run:
            await _create_document(session, store, vanban, quyen, acl, signature, embedding, ok)
        logger.info("id_vb=%s -> created (embed=%s)", vanban.id_vb, ok)
        return done("created", has_embedding=ok)

    # Đã có PG. Tính embedding nếu còn thiếu.
    embedding, ok = (None, pg.has_embedding)
    if not pg.has_embedding:
        embedding, ok = await _try_embed(gateway, vanban.embed_text)

    # ES thiếu record (vd index vừa được tạo lại) -> ghi lại đầy đủ.
    if not in_es:
        # ES đã mất vector -> re-embed để khôi phục BBQ (dù PG báo has_embedding).
        if embedding is None:
            embedding, ok = await _try_embed(gateway, vanban.embed_text)
        if not dry_run:
            await _set_meta(
                session, pg.document_id,
                {
                    "access": _access_block(quyen, acl, signature),
                    "has_embedding": ok,
                    # Backfill noi_dung_raw cho doc tạo trước khi thêm trường này.
                    "noi_dung_raw": vanban.noi_dung,
                },
            )
            await store.upsert_document(
                document_id=str(pg.document_id),
                id_vb=vanban.id_vb, ky_hieu=vanban.ky_hieu, trich_yeu=vanban.trich_yeu,
                tom_tat=vanban.tom_tat, noi_dung=vanban.noi_dung_truncated,
                noi_ban_hanh=vanban.noi_ban_hanh, nguoi_ky=vanban.nguoi_ky,
                ten_file=vanban.ten_file, ngay_vb=vanban.ngay_vb, nam=vanban.nam,
                acl_subjects=acl_keys_from_acl(acl),
                acl_deny_pb=sorted(acl.deny_department_ids),
                acl_deny_nv=sorted(acl.deny_user_ids),
                embedding=embedding,
            )
        logger.info("id_vb=%s -> created (ES restore, embed=%s)", vanban.id_vb, ok)
        return done("created", has_embedding=ok)

    # CASE 4 — acl_updated (quyền đổi). Re-resolve từ quyen MỚI.
    if not same_checksum:
        if not dry_run:
            await _set_meta(
                session, pg.document_id,
                {"access": _access_block(quyen, acl, signature), "has_embedding": ok},
            )
            await store.update_acl(
                str(pg.document_id),
                acl_subjects=acl_keys_from_acl(acl),
                acl_deny_pb=sorted(acl.deny_department_ids),
                acl_deny_nv=sorted(acl.deny_user_ids),
            )
            if embedding is not None:
                await store.update_document_embedding(str(pg.document_id), embedding)
        logger.info("id_vb=%s -> acl_updated (embed=%s)", vanban.id_vb, ok)
        return done("acl_updated", has_embedding=ok)

    # CASE 3 — emb_updated (quyền không đổi, có ES, thiếu embedding).
    if not dry_run and embedding is not None:
        await store.update_document_embedding(str(pg.document_id), embedding)
        await _set_meta(session, pg.document_id, {"has_embedding": True})
    logger.info("id_vb=%s -> emb_updated (embed=%s)", vanban.id_vb, ok)
    return done("emb_updated", has_embedding=ok)


async def _create_document(
    session, store, vanban, quyen, acl, signature, embedding, ok
) -> None:
    from app.repositories.documents import DocumentRepository

    repo = DocumentRepository(session)
    title = (vanban.trich_yeu or vanban.ky_hieu or vanban.id_vb or "DOffice document")[:255]
    document = await repo.create_document(
        title=title, source_type=DOFFICE_SOURCE_TYPE, status="indexed", visibility="organization"
    )
    document.document_profile = "doffice_admin"
    document.document_metadata = {
        "id_vb": vanban.id_vb,
        "ky_hieu": vanban.ky_hieu,
        "trich_yeu": vanban.trich_yeu,
        "noi_ban_hanh": vanban.noi_ban_hanh,
        "nguoi_ky": vanban.nguoi_ky,
        "ten_file": vanban.ten_file,
        "tom_tat": vanban.tom_tat,
        "ngay_vb": vanban.ngay_vb,
        "nam": vanban.nam,
        "source_type": DOFFICE_SOURCE_TYPE,
        "has_embedding": ok,
        # Lưu FULL noi_dung vào PG để nhánh "click văn bản" chunk lại không phải gọi
        # API DOffice (xem DofficeIngestionService._ingest_existing_for_retrieval).
        "noi_dung_raw": vanban.noi_dung,
        "access": _access_block(quyen, acl, signature),
    }
    await session.flush()
    await store.upsert_document(
        document_id=str(document.id),
        id_vb=vanban.id_vb,
        ky_hieu=vanban.ky_hieu,
        trich_yeu=vanban.trich_yeu,
        tom_tat=vanban.tom_tat,
        noi_dung=vanban.noi_dung_truncated,
        noi_ban_hanh=vanban.noi_ban_hanh,
        nguoi_ky=vanban.nguoi_ky,
        ten_file=vanban.ten_file,
        ngay_vb=vanban.ngay_vb,
        nam=vanban.nam,
        acl_subjects=acl_keys_from_acl(acl),
        acl_deny_pb=sorted(acl.deny_department_ids),
        acl_deny_nv=sorted(acl.deny_user_ids),
        embedding=embedding,
    )


async def _set_meta(session: AsyncSession, document_id: Any, updates: dict[str, Any]) -> None:
    """Merge cập nhật vào document_metadata của Document (giữ key khác)."""
    document = await session.get(Document, document_id)
    if document is None:
        return
    meta = dict(document.document_metadata or {})
    meta.update(updates)
    document.document_metadata = meta
    await session.flush()
