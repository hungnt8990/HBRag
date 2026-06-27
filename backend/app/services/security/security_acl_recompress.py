"""Re-compress ACL khi danh mục tổ chức thay đổi.

Khi nhân sự thay đổi (vào/ra/chuyển phòng), biểu diễn ACL nén theo phòng ban/đơn vị
phản ánh *thành viên cũ* nên có thể sai. Module này:

1. Tính **chữ ký danh mục** hiện tại (:func:`catalog_signature`).
2. Với mỗi văn bản còn lưu ``raw_assignment`` (nguồn sự thật), resolve + compress lại
   theo danh mục mới.
3. Nếu kết quả khác bản đã lưu (hoặc khác chữ ký), cập nhật:
   - ``document_metadata["access"]`` trong PostgreSQL,
   - payload ACL của các point trong Qdrant (set_payload, không nhúng lại).

Nguồn sự thật là ``document_metadata["access"]["raw_assignment"]`` — được adapter điền
lúc ingest. Bản nén (``acl``) và chữ ký (``acl_ver``) là cache suy ra được.

Lưu ý: việc đồng bộ Elasticsearch đang để ngỏ (ES hiện chưa áp filter ACL — xem
PROJECT_OVERVIEW). Khi bật ACL cho ES cần bổ sung bước update tương ứng tại đây.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass

from sqlalchemy import select

from app.core.config import settings
from app.models.document import Document
from app.services.security.security_acl_compressor import OrgCatalog
from app.services.security.security_acl_payload import acl_keys_from_acl, to_chunk_payload
from app.services.security.security_acl_resolver import (
    RawAssignment,
    UnitTree,
    resolve_and_compress,
)

logger = logging.getLogger(__name__)


def catalog_signature(catalog: OrgCatalog) -> str:
    """Chữ ký ổn định cho trạng thái biên chế (id_nv -> id_dv, id_pb).

    Đổi khi có người vào/ra/chuyển phòng/đổi đơn vị.
    """
    parts = [
        f"{id_nv}:{loc[0]}:{loc[1]}"
        for id_nv, loc in sorted(catalog.user_location.items())
    ]
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
    return f"sha256:{digest[:16]}"


@dataclass
class RecompressStats:
    scanned: int = 0
    recompressed: int = 0
    skipped_no_source: int = 0
    unchanged: int = 0


def _access_block(document: Document) -> dict:
    meta = document.document_metadata or {}
    access = meta.get("access")
    return access if isinstance(access, dict) else {}


async def recompress_document(
    session,
    document: Document,
    *,
    catalog: OrgCatalog,
    unit_tree: UnitTree | None,
    signature: str,
    vector_store=None,
    document_index_store=None,
    force: bool = False,
) -> bool:
    """Re-compress ACL cho một văn bản. Trả về True nếu có cập nhật.

    Bỏ qua nếu không có ``raw_assignment``; bỏ qua nếu chữ ký không đổi và ``force``
    False; ngược lại tính lại và ghi nếu kết quả khác bản đã lưu.
    """
    access = _access_block(document)
    raw_data = access.get("raw_assignment")
    if not raw_data:
        return False

    if not force and access.get("acl_ver") == signature:
        return False

    raw = RawAssignment.from_dict(raw_data)
    compressed = resolve_and_compress(raw, catalog, unit_tree=unit_tree)
    new_acl = compressed.to_dict()

    if not force and access.get("acl") == new_acl and access.get("acl_ver") == signature:
        return False

    new_access = dict(access)
    new_access["acl"] = new_acl
    new_access["acl_ver"] = signature
    new_meta = dict(document.document_metadata or {})
    new_meta["access"] = new_access
    document.document_metadata = new_meta  # gán dict mới để SQLAlchemy phát hiện thay đổi
    await session.flush()

    if document_index_store is not None:
        # Two-stage mode: chỉ partial-update 3 trường ACL ở document index (ES) —
        # Qdrant KHÔNG bị đụng (ACL lưu document-level, không nhân theo từng chunk),
        # embedding/BM25 fields cũng giữ nguyên.
        await document_index_store.update_acl(
            str(document.id),
            acl_subjects=acl_keys_from_acl(compressed),
            acl_deny_pb=sorted(compressed.deny_department_ids),
            acl_deny_nv=sorted(compressed.deny_user_ids),
        )
        logger.info(
            "Two-stage ACL update document=%s -> document index (Qdrant bỏ qua)",
            document.id,
        )
    elif vector_store is not None:
        payload = to_chunk_payload(compressed, version=signature)
        await vector_store.set_acl_payload_for_document(document.id, payload)

    return True


async def recompress_all(
    session,
    *,
    vector_store=None,
    document_index_store=None,
    force: bool = False,
    batch_commit: int = 200,
) -> RecompressStats:
    """Quét toàn bộ văn bản và re-compress ACL theo danh mục hiện tại."""
    catalog = await OrgCatalog.from_session(session)
    unit_tree = await UnitTree.from_session(session)
    signature = catalog_signature(catalog)
    logger.info("Chữ ký danh mục hiện tại: %s", signature)

    # Two-stage: ACL chỉ cần cập nhật ở document index (ES), không phải Qdrant.
    if document_index_store is None and settings.two_stage_retrieval_enabled:
        from app.services.retrieval.retrieval_document_index import DocumentIndexStore

        document_index_store = DocumentIndexStore()
        logger.info("recompress_all: two-stage mode -> cập nhật document index (Qdrant bỏ qua)")

    stats = RecompressStats()
    result = await session.execute(select(Document))
    documents = result.scalars().all()

    pending = 0
    for document in documents:
        stats.scanned += 1
        access = _access_block(document)
        if not access.get("raw_assignment"):
            stats.skipped_no_source += 1
            continue
        changed = await recompress_document(
            session,
            document,
            catalog=catalog,
            unit_tree=unit_tree,
            signature=signature,
            vector_store=vector_store,
            document_index_store=document_index_store,
            force=force,
        )
        if changed:
            stats.recompressed += 1
            pending += 1
            if pending >= batch_commit:
                await session.commit()
                pending = 0
        else:
            stats.unchanged += 1

    await session.commit()
    logger.info(
        "Re-compress xong: scanned=%d recompressed=%d unchanged=%d no_source=%d",
        stats.scanned,
        stats.recompressed,
        stats.unchanged,
        stats.skipped_no_source,
    )
    return stats
