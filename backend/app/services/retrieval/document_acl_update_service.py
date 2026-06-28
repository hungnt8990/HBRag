"""Cập nhật ACL của 1 văn bản DOffice (Postgres + Elasticsearch) — cho DOffice gọi.

Tách rõ trách nhiệm:
- ``update_document_acl(...)`` = **HÀM LÕI** thuần domain (resolve 3 list -> nén -> ghi PG + ES).
  KHÔNG xử lý xác thực. Sau này bổ sung phân quyền ở route/dependency sẽ KHÔNG đụng hàm này.
- Route (``api/routes/doffice_acl.py``) chỉ: chokepoint quyền + gọi hàm này + map lỗi -> HTTP.

Mirror đúng case ``acl_updated`` của job sync: re-resolve từ 3 list MỚI -> nén ->
cập nhật ``document_metadata.access`` (PG) + 3 trường ACL phẳng (ES).
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

from pydantic import BaseModel, Field
from sqlalchemy import String, cast, select

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.models.document import Document
from app.repositories.documents import DocumentRepository
from app.services.retrieval.retrieval_document_index import DocumentIndexStore
from app.services.security.security_acl_compressor import OrgCatalog
from app.services.security.security_acl_payload import acl_keys_from_acl
from app.services.security.security_acl_recompress import catalog_signature
from app.services.security.security_acl_resolver import UnitTree, resolve_doffice_and_compress
from jobs.doffice_sync.clients.quyen_client import QuyenEsClient, QuyenRecord
from jobs.doffice_sync.clients.vanban_client import VanbanEsClient, VanbanRecord

logger = logging.getLogger("doffice_acl_update")
DOFFICE_SOURCE_TYPE = "doffice_elasticsearch"


class AclUpdateError(RuntimeError):
    """Lỗi backend khi cập nhật ACL (ES…) -> route trả 502."""


class DocumentNotFoundError(RuntimeError):
    """Không tìm thấy văn bản ở cả Postgres LẪN nguồn DOffice -> route trả 404."""


class AclUpdateRequest(BaseModel):
    id_vb: str = Field(description="Mã văn bản DOffice cần cập nhật quyền")
    don_vi_list: list[int] = Field(default_factory=list, description="Danh sách id đơn vị được cấp")
    phong_ban_list: list[int] = Field(default_factory=list, description="Danh sách id phòng ban được cấp")
    ca_nhan_list: list[int] = Field(default_factory=list, description="Danh sách id nhân viên được cấp")


class AclUpdateResponse(BaseModel):
    id_vb: str
    document_id: str
    action: str  # created (tự fetch+embed) | acl_updated (đã có, chỉ đổi quyền)
    acl_source: str  # params (DOffice đẩy) | doffice_vanban_quyen (fetch từ nguồn khi tạo mới)
    updated: bool  # đã ghi Postgres
    es_updated: bool  # đã ghi Elasticsearch
    has_embedding: bool  # có vector BBQ (chỉ liên quan khi created)
    acl_subjects: list[str]
    acl_deny_pb: list[int]
    acl_deny_nv: list[int]
    quyen_checksum: str
    warnings: list[str] = Field(default_factory=list)


def _quyen_checksum(don_vi: list[int], phong_ban: list[int], ca_nhan: list[int]) -> str:
    """Checksum ổn định từ 3 list (sorted, unique) -> dùng để audit/idempotent."""
    payload = json.dumps(
        {
            "don_vi": sorted(set(don_vi)),
            "phong_ban": sorted(set(phong_ban)),
            "ca_nhan": sorted(set(ca_nhan)),
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _access_block(
    don_vi_list: list[int], phong_ban_list: list[int], ca_nhan_list: list[int],
    acl: Any, signature: str, checksum: str,
) -> dict[str, Any]:
    """Block ``access`` lưu vào document_metadata (giống job sync)."""
    return {
        "quyen_checksum": checksum,
        "quyen_ngay_capnhat": datetime.now(timezone.utc).isoformat(),
        "raw_assignment": {
            "don_vi_list": list(don_vi_list),
            "phong_ban_list": list(phong_ban_list),
            "ca_nhan_list": list(ca_nhan_list),
        },
        "acl": acl.to_dict(),
        "acl_ver": signature,
    }


def _doffice_host() -> str:
    parts = urlsplit(settings.doffice_es_url)
    return f"{parts.scheme}://{parts.netloc}"


async def _fetch_vanban(id_vb: str) -> VanbanRecord | None:
    """Lấy NỘI DUNG văn bản từ nguồn DOffice (``doffice_vanban``) theo id_vb (term, đơn lẻ)."""
    client = VanbanEsClient(
        url=_doffice_host(),
        user=settings.doffice_es_username,
        password=settings.doffice_es_password,
        verify_ssl=settings.doffice_es_verify_ssl,
        timeout_seconds=float(settings.doffice_es_timeout_seconds),
    )
    records = await client.fetch_by_id_vb([str(id_vb)])
    return records[0] if records else None


async def _fetch_quyen(id_vb: str) -> QuyenRecord | None:
    """Lấy QUYỀN văn bản từ nguồn DOffice (``doffice_vanban_quyen``) theo id_vb (term, đơn lẻ)."""
    client = QuyenEsClient(
        url=_doffice_host(),
        user=settings.doffice_es_username,
        password=settings.doffice_es_password,
        verify_ssl=settings.doffice_es_verify_ssl,
        timeout_seconds=float(settings.doffice_es_timeout_seconds),
    )
    return (await client.get_batch([str(id_vb)])).get(str(id_vb))


async def _resolve_and_pack(
    don_vi_list: list[int], phong_ban_list: list[int], ca_nhan_list: list[int]
) -> dict[str, Any]:
    """Load danh mục + nén ACL từ 3 list -> gói kết quả (acl phẳng + checksum + access block)."""
    async with AsyncSessionLocal() as session:
        catalog = await OrgCatalog.from_session(session)
        unit_tree = await UnitTree.from_session(session)
    signature = catalog_signature(catalog)
    acl, _assignment, warnings = resolve_doffice_and_compress(
        don_vi_list=don_vi_list,
        phong_ban_list=phong_ban_list,
        ca_nhan_list=ca_nhan_list,
        catalog=catalog,
        unit_tree=unit_tree,
    )
    checksum = _quyen_checksum(don_vi_list, phong_ban_list, ca_nhan_list)
    return {
        "acl_subjects": acl_keys_from_acl(acl),
        "acl_deny_pb": sorted(acl.deny_department_ids),
        "acl_deny_nv": sorted(acl.deny_user_ids),
        "checksum": checksum,
        "access": _access_block(don_vi_list, phong_ban_list, ca_nhan_list, acl, signature, checksum),
        "warnings": warnings,
    }


async def _embed(text: str) -> tuple[list[float] | None, bool]:
    """Embed BBQ; lỗi/text rỗng -> (None, False) để tạo BM25-only (giống job)."""
    if not text.strip():
        return None, False
    try:
        from app.services.llm_gateway import get_llm_gateway

        return await get_llm_gateway().embed_query(text), True
    except Exception:
        logger.warning("Embed thất bại -> tạo BM25-only", exc_info=True)
        return None, False


async def _create_document(
    session, store: DocumentIndexStore, vanban: VanbanRecord, access: dict[str, Any],
    acl_subjects: list[str], acl_deny_pb: list[int], acl_deny_nv: list[int],
    embedding: list[float] | None, has_embedding: bool,
) -> str:
    """Tạo mới văn bản trong PG + ES từ nội dung DOffice + ACL (mirror job CASE created)."""
    repo = DocumentRepository(session)
    title = (vanban.trich_yeu or vanban.ky_hieu or vanban.id_vb or "DOffice document")[:255]
    document = await repo.create_document(
        title=title, source_type=DOFFICE_SOURCE_TYPE, status="indexed", visibility="organization"
    )
    document.document_profile = "doffice_admin"
    document.document_metadata = {
        "id_vb": vanban.id_vb, "ky_hieu": vanban.ky_hieu, "trich_yeu": vanban.trich_yeu,
        "noi_ban_hanh": vanban.noi_ban_hanh, "nguoi_ky": vanban.nguoi_ky,
        "ten_file": vanban.ten_file, "tom_tat": vanban.tom_tat, "ngay_vb": vanban.ngay_vb,
        "nam": vanban.nam, "source_type": DOFFICE_SOURCE_TYPE, "has_embedding": has_embedding,
        "access": access,
    }
    await session.flush()
    await store.upsert_document(
        document_id=str(document.id), id_vb=vanban.id_vb, ky_hieu=vanban.ky_hieu,
        trich_yeu=vanban.trich_yeu, tom_tat=vanban.tom_tat, noi_dung=vanban.noi_dung_truncated,
        noi_ban_hanh=vanban.noi_ban_hanh, nguoi_ky=vanban.nguoi_ky, ten_file=vanban.ten_file,
        ngay_vb=vanban.ngay_vb, nam=vanban.nam,
        acl_subjects=acl_subjects, acl_deny_pb=acl_deny_pb, acl_deny_nv=acl_deny_nv,
        embedding=embedding,
    )
    await session.commit()
    return str(document.id)


async def update_document_acl(
    id_vb: str,
    *,
    don_vi_list: list[int],
    phong_ban_list: list[int],
    ca_nhan_list: list[int],
) -> AclUpdateResponse:
    """HÀM LÕI: cập nhật ACL 1 văn bản trong PG + ES từ 3 list DOffice. KHÔNG xác thực.

    Raise ``DocumentNotFoundError`` nếu văn bản chưa đồng bộ; ``AclUpdateError`` nếu lỗi ES.
    """
    store = DocumentIndexStore(url=settings.two_stage_document_index_url or settings.elasticsearch_url)

    # 1) Văn bản đã có trong Postgres chưa? (đã có -> UPDATE quyền bằng params do DOffice đẩy)
    id_vb_col = cast(Document.document_metadata["id_vb"].astext, String)
    async with AsyncSessionLocal() as session:
        doc = (
            await session.execute(
                select(Document).where(
                    Document.source_type == DOFFICE_SOURCE_TYPE, id_vb_col == str(id_vb)
                )
            )
        ).scalars().first()
        if doc is not None:
            pack = await _resolve_and_pack(don_vi_list, phong_ban_list, ca_nhan_list)
            meta = dict(doc.document_metadata or {})
            meta["access"] = pack["access"]
            doc.document_metadata = meta
            await session.commit()
            document_id = str(doc.id)
        else:
            document_id = None

    # 2) ĐÃ có -> partial update 3 trường ACL trên ES.
    if document_id is not None:
        try:
            es_updated = str(id_vb) in await store.existing_id_vb([str(id_vb)])
            await store.update_acl(
                document_id,
                acl_subjects=pack["acl_subjects"],
                acl_deny_pb=pack["acl_deny_pb"],
                acl_deny_nv=pack["acl_deny_nv"],
            )
        except Exception as exc:
            raise AclUpdateError(f"Cập nhật ACL trên Elasticsearch lỗi: {exc}") from exc
        warnings = list(pack["warnings"])
        if not es_updated:
            warnings.append("Văn bản chưa có record trong Elasticsearch index (chỉ cập nhật Postgres).")
        logger.info("ACL updated id_vb=%s doc=%s es=%s (acl_source=params)", id_vb, document_id, es_updated)
        return AclUpdateResponse(
            id_vb=str(id_vb), document_id=document_id, action="acl_updated", acl_source="params",
            updated=True, es_updated=es_updated, has_embedding=True,
            acl_subjects=pack["acl_subjects"], acl_deny_pb=pack["acl_deny_pb"],
            acl_deny_nv=pack["acl_deny_nv"], quyen_checksum=pack["checksum"], warnings=warnings,
        )

    # 3) CHƯA có -> tạo mới: fetch NỘI DUNG + fetch QUYỀN từ nguồn DOffice (đơn lẻ theo id_vb).
    vanban = await _fetch_vanban(id_vb)
    if vanban is None:
        raise DocumentNotFoundError(
            f"Văn bản id_vb={id_vb} không có trong Postgres LẪN nguồn DOffice -> không thể tạo."
        )
    quyen = await _fetch_quyen(id_vb)
    create_warnings: list[str] = []
    if quyen is not None and quyen.has_acl:
        acl_source = "doffice_vanban_quyen"
        dv, pb, nv = quyen.don_vi_list, quyen.phong_ban_list, quyen.ca_nhan_list
    elif any([don_vi_list, phong_ban_list, ca_nhan_list]):
        acl_source = "params"
        dv, pb, nv = don_vi_list, phong_ban_list, ca_nhan_list
        create_warnings.append("Không có record quyền ở doffice_vanban_quyen -> dùng quyền từ params.")
    else:
        acl_source = "params"
        dv, pb, nv = [], [], []
        create_warnings.append("Văn bản chưa có quyền (doffice_vanban_quyen trống, params rỗng) -> ACL rỗng.")

    pack = await _resolve_and_pack(dv, pb, nv)
    embedding, ok = await _embed(vanban.embed_text)
    try:
        async with AsyncSessionLocal() as session:
            document_id = await _create_document(
                session, store, vanban, pack["access"],
                pack["acl_subjects"], pack["acl_deny_pb"], pack["acl_deny_nv"], embedding, ok,
            )
    except Exception as exc:
        raise AclUpdateError(f"Tạo mới văn bản (PG/ES) lỗi: {exc}") from exc
    logger.info(
        "ACL update -> created id_vb=%s doc=%s embed=%s acl_source=%s", id_vb, document_id, ok, acl_source
    )
    return AclUpdateResponse(
        id_vb=str(id_vb), document_id=document_id, action="created", acl_source=acl_source,
        updated=True, es_updated=True, has_embedding=ok,
        acl_subjects=pack["acl_subjects"], acl_deny_pb=pack["acl_deny_pb"],
        acl_deny_nv=pack["acl_deny_nv"], quyen_checksum=pack["checksum"],
        warnings=list(pack["warnings"]) + create_warnings,
    )
