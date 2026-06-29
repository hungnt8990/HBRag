"""Ingest 1 văn bản DOffice vào CẢ 3 DB (thiết kế mới).

Cho mỗi văn bản (dữ liệu đã fetch sẵn từ doffice_vanban + quyền từ doffice_vanban_quyen):
1. PostgreSQL: 1 Document = noi_dung THÔ (markdown) + metadata + ACL + cấu trúc normalized.
2. Elasticsearch (BM25 doc-level): full nội dung đã làm sạch + mọi trường + ACL (không vector).
3. Qdrant Col 1 (chunks): vector dense+sparse từng chunk; payload = ACL + id_vb + metadata truy hồi.
4. Qdrant Col 2 (docmeta): 1 point/văn bản; vector dense+sparse của metadata (mọi trường trừ
   noi_dung); payload = các trường đó + ACL + id_vb.

Enrichment TẮT. Idempotent: chạy lại 1 văn bản -> xóa dấu vết cũ (PG/ES/Qdrant) rồi ghi lại.
Tái dùng: ChunkingService (PG chunks), VectorIndexingService (embed + Qdrant Col1),
DofficeIngestionService._document_metadata (dựng metadata chuẩn cho chunker).
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.core.config import settings
from app.services.chunkers.chunker_text_cleaning import clean_for_chunking
from app.services.document_sources import DOFFICE_SOURCE_TYPE
from app.services.ingestion.ingestion_doffice_content_normalizer import normalize_doffice_source
from app.services.ingestion.ingestion_doffice_ingestion_service import DofficeIngestionService
from app.services.security.security_acl_payload import (
    F_DENY,
    F_SUBJECTS,
    acl_deny_keys_from_acl,
    acl_keys_from_acl,
)
from app.services.security.security_acl_resolver import resolve_doffice_and_compress
from app.services.vector.vector_indexing_service import VectorIndexingService

logger = logging.getLogger(__name__)

# Trường đưa vào payload Qdrant docmeta (Col 2) — đúng object mẫu, TRỪ noi_dung.
_DOCMETA_FIELDS = (
    "id_vb", "ky_hieu", "trich_yeu", "id_dv_ban_hanh", "noi_ban_hanh", "nguoi_ky",
    "ten_file", "duong_dan", "tom_tat", "ngay_tao", "type_ocr", "ngay_capnhat",
    "nam", "thang", "ngay_vb",
)
# Text để embed docmeta: ngữ nghĩa (trich_yeu/tom_tat/noi_ban_hanh) + ký hiệu (ky_hieu).
_DOCMETA_EMBED_FIELDS = ("ky_hieu", "trich_yeu", "tom_tat", "noi_ban_hanh", "ten_file")

_DOCMETA_NAMESPACE = uuid.UUID("0d0ff1ce-0000-4000-8000-000000000001")


@dataclass
class UnifiedIngestResult:
    id_vb: str
    document_id: str
    chunks: int
    has_acl: bool


@dataclass
class DofficeJobItem:
    """Đơn vị công việc đi qua pipeline 3 luồng. Giai đoạn PG sinh ra, giai đoạn ES &
    Qdrant tiêu thụ (mọi dữ liệu cần thiết đã nằm sẵn đây, không cần truy vấn lại)."""

    id_vb: str
    document_id: str
    source: dict[str, Any]
    acl_subjects: list[str]
    acl_deny: list[str]
    acl_payload: dict[str, Any]
    noi_dung_clean: str | None
    chunk_count: int
    has_acl: bool


class DofficeUnifiedIngestor:
    def __init__(
        self,
        *,
        repository: Any,
        chunking_service: Any,
        llm_gateway: Any,
        sparse_provider: Any,
        chunks_store: Any,
        docmeta_store: Any,
        bm25_store: Any,
        catalog: Any,
        unit_tree: Any,
        signature: str | None = None,
        store_chunks_in_pg: bool | None = None,
    ) -> None:
        self._repository = repository
        self._chunking_service = chunking_service
        self._llm_gateway = llm_gateway
        self._sparse_provider = sparse_provider
        self._chunks_store = chunks_store
        self._docmeta_store = docmeta_store
        self._bm25_store = bm25_store
        self._catalog = catalog
        self._unit_tree = unit_tree
        self._signature = signature
        self._store_chunks_in_pg = (
            settings.store_chunks_in_pg if store_chunks_in_pg is None else store_chunks_in_pg
        )

    async def ingest(
        self,
        *,
        source: dict[str, Any],
        acl_lists: dict[str, Any] | None = None,
        uploaded_by_user_id: Any = None,
        organization_id: Any = None,
        knowledge_base_id: Any = None,
    ) -> UnifiedIngestResult:
        # Giữ tương thích: chạy tuần tự cả 3 giai đoạn (dùng cho mode id-lẻ/test). Pipeline
        # 3 luồng ở runner gọi trực tiếp prepare_postgres -> index_qdrant + index_elasticsearch.
        item = await self.prepare_postgres(
            source=source,
            acl_lists=acl_lists,
            uploaded_by_user_id=uploaded_by_user_id,
            organization_id=organization_id,
            knowledge_base_id=knowledge_base_id,
        )
        await self.index_qdrant(item)
        await self.index_elasticsearch(item)
        logger.info(
            "Ingest 3-DB xong id_vb=%s document=%s chunks=%s", item.id_vb, item.document_id, item.chunk_count
        )
        return UnifiedIngestResult(
            id_vb=item.id_vb, document_id=item.document_id, chunks=item.chunk_count, has_acl=item.has_acl
        )

    # ============================ GIAI ĐOẠN 1: PostgreSQL =====================
    async def prepare_postgres(
        self,
        *,
        source: dict[str, Any],
        acl_lists: dict[str, Any] | None = None,
        uploaded_by_user_id: Any = None,
        organization_id: Any = None,
        knowledge_base_id: Any = None,
    ) -> DofficeJobItem:
        """Luồng 1: ACL + normalize + ghi Document & chunks vào PostgreSQL. Trả DofficeJobItem
        để đẩy vào hàng đợi cho luồng ES & Qdrant."""
        id_vb = " ".join(str(source.get("id_vb") or "").split()).strip()
        if not id_vb:
            raise ValueError("source.id_vb is required.")

        acl_lists = acl_lists or {}
        acl, acl_subjects, acl_deny, acl_payload = self._resolve_acl(acl_lists)
        access_block = {
            "raw_assignment": acl_lists,
            "acl": acl.to_dict() if acl is not None else None,
            "acl_ver": self._signature,
        }

        normalized = normalize_doffice_source(source)
        if not normalized.clean_text.strip():
            logger.warning("id_vb=%s không có nội dung sau khi làm sạch -> bỏ qua", id_vb)

        # Idempotent: xóa dấu vết cũ ở 3 DB trước khi ghi lại.
        existing = await self._find_existing(id_vb)
        if existing is not None:
            await self._delete_everywhere(existing, id_vb)

        source_document = DofficeIngestionService._doffice_document_from_source(source, normalized)
        document = await self._repository.create_document(
            title=(source.get("ten_file") or source.get("ky_hieu") or f"DOffice {id_vb}")[:255],
            source_type=DOFFICE_SOURCE_TYPE,
            status="uploaded",
            uploaded_by_user_id=uploaded_by_user_id,
            organization_id=organization_id,
            knowledge_base_id=knowledge_base_id,
            visibility="organization",
            access=None,
        )
        document.document_profile = "doffice_admin"
        await self._repository.update_document_parsed_content(
            document, parsed_text=normalized.clean_text, parsed_at=datetime.now(UTC), status="parsed",
        )
        metadata = DofficeIngestionService._document_metadata(source_document, normalized)
        metadata["access"] = access_block
        metadata["has_embedding"] = True
        await self._repository.update_document_metadata(document, metadata)
        await self._repository.commit()
        document_id = document.id

        # Chunk vào PG (cần normalized_elements để chunker doffice chạy).
        chunk_response = await self._chunking_service.chunk_document(document_id, profile="auto")

        noi_dung_clean = clean_for_chunking(str(source.get("noi_dung") or "")) or None
        return DofficeJobItem(
            id_vb=id_vb,
            document_id=str(document_id),
            source=source,
            acl_subjects=acl_subjects,
            acl_deny=acl_deny,
            acl_payload=acl_payload,
            noi_dung_clean=noi_dung_clean,
            chunk_count=chunk_response.chunk_count,
            has_acl=acl is not None,
        )

    # ============================ GIAI ĐOẠN 2: Elasticsearch =================
    async def index_elasticsearch(self, item: DofficeJobItem) -> None:
        """Luồng 2: đổ BM25 doc-level (full nội dung đã làm sạch + ACL) vào Elasticsearch."""
        await self._bm25_store.upsert_document(
            document_id=item.document_id,
            id_vb=item.id_vb,
            fields=item.source,
            noi_dung_clean=item.noi_dung_clean,
            acl_subjects=item.acl_subjects,
            acl_deny=item.acl_deny,
            acl_ver=self._signature,
        )

    # ============================ GIAI ĐOẠN 3: Qdrant ========================
    async def index_qdrant(self, item: DofficeJobItem) -> None:
        """Luồng 3: embed chunk (đọc từ PG) + đẩy Qdrant Col1 (chunks) & Col2 (docmeta) + ACL."""
        from uuid import UUID

        document_id = UUID(item.document_id)
        await VectorIndexingService(
            repository=self._repository,
            llm_gateway=self._llm_gateway,
            vector_store=self._chunks_store,
            sparse_embedding_provider=self._sparse_provider,
            keyword_index_store=None,
        ).index_document(document_id, use_enriched_content_for_embedding=False)
        await self._chunks_store.set_acl_payload_for_document(document_id, item.acl_payload)

        if not self._store_chunks_in_pg:
            await self._repository.delete_chunks_for_document(document_id)
            await self._repository.commit()

        await self._index_docmeta(
            document_id=item.document_id, source=item.source, acl_payload=item.acl_payload
        )

    # ------------------------------------------------------------------ ACL --
    def _resolve_acl(self, acl_lists: dict[str, Any]):
        don_vi = acl_lists.get("don_vi_list")
        phong_ban = acl_lists.get("phong_ban_list")
        ca_nhan = acl_lists.get("ca_nhan_list")
        if not (don_vi or phong_ban or ca_nhan) and settings.doffice_synthetic_acl_enabled:
            don_vi = settings.doffice_synthetic_don_vi_list
            phong_ban = settings.doffice_synthetic_phong_ban_list
            ca_nhan = settings.doffice_synthetic_ca_nhan_list
        if not (don_vi or phong_ban or ca_nhan):
            return None, [], [], {}
        acl, _assignment, warnings = resolve_doffice_and_compress(
            don_vi_list=don_vi, phong_ban_list=phong_ban, ca_nhan_list=ca_nhan,
            catalog=self._catalog, unit_tree=self._unit_tree,
        )
        for warning in warnings:
            logger.debug("ACL: %s", warning)
        # ACL GỌN: 2 list keyword prefixed — acl_subjects (allow ["dv_/pb_/nv_"]) +
        # acl_deny (deny ["pb_/nv_"]). KHÔNG lưu acl_allow_dv/pb/nv hay acl_deny_pb/nv số.
        acl_subjects = acl_keys_from_acl(acl)
        acl_deny = acl_deny_keys_from_acl(acl)
        acl_payload = {F_SUBJECTS: acl_subjects, F_DENY: acl_deny}
        return (acl, acl_subjects, acl_deny, acl_payload)

    # -------------------------------------------------------------- docmeta --
    async def _index_docmeta(self, *, document_id: str, source: dict[str, Any], acl_payload: dict[str, Any]) -> None:
        embed_text = " ".join(
            str(source.get(field) or "").strip() for field in _DOCMETA_EMBED_FIELDS if source.get(field)
        ).strip()
        if not embed_text:
            embed_text = str(source.get("id_vb") or "")
        dense = await self._llm_gateway.embed_query(embed_text)
        sparse = None
        if self._sparse_provider is not None and getattr(self._chunks_store, "sparse_enabled", False):
            try:
                sparse = await self._sparse_provider.embed_query(embed_text)
            except Exception:
                logger.warning("Sparse embed docmeta thất bại id_vb=%s", source.get("id_vb"), exc_info=True)

        payload: dict[str, Any] = {"document_id": document_id}
        for field in _DOCMETA_FIELDS:
            value = source.get(field)
            if value not in (None, ""):
                payload[field] = value
        payload.update(acl_payload)  # acl_subjects (allow) + acl_deny — 2 list keyword

        point_id = str(uuid.uuid5(_DOCMETA_NAMESPACE, f"docmeta:{source.get('id_vb')}"))
        point = self._docmeta_store.build_point(
            point_id=point_id, vector=dense, sparse_vector=sparse, payload=payload,
        )
        await self._docmeta_store.upsert_chunks([point])

    # --------------------------------------------------------------- delete --
    async def _find_existing(self, id_vb: str) -> Any | None:
        finder = getattr(self._repository, "find_document_by_source_metadata", None)
        if finder is None:
            return None
        return await finder(source_type=DOFFICE_SOURCE_TYPE, id_vb=id_vb)

    async def delete_by_id_vb(self, id_vb: str) -> bool:
        """Xóa 1 văn bản khỏi CẢ 3 DB (PostgreSQL + Elasticsearch + Qdrant) theo id_vb.

        True nếu tìm thấy & xóa Document trong PG. Nếu PG không còn doc -> vẫn cố dọn dấu
        vết ES theo id_vb (Qdrant cần document_id nên không dọn được khi thiếu PG) và trả False.
        """
        id_vb = " ".join(str(id_vb or "").split()).strip()
        if not id_vb:
            return False
        document = await self._find_existing(id_vb)
        if document is None:
            try:
                await self._bm25_store.delete_by_id_vb(id_vb)
            except Exception:
                logger.exception("Xóa ES doc id_vb=%s thất bại", id_vb)
            return False
        await self._delete_everywhere(document, id_vb)
        return True

    async def _delete_everywhere(self, document: Any, id_vb: str) -> None:
        document_id = document.id
        tenant_id = getattr(document, "organization_id", None)
        for store in (self._chunks_store, self._docmeta_store):
            try:
                await store.delete_points_for_document(document_id, tenant_id=tenant_id)
            except Exception:
                logger.exception("Xóa Qdrant point cũ thất bại document=%s", document_id)
        try:
            await self._bm25_store.delete_by_id_vb(id_vb)
        except Exception:
            logger.exception("Xóa ES doc cũ thất bại id_vb=%s", id_vb)
        await self._repository.delete_document(document)
        await self._repository.commit()
