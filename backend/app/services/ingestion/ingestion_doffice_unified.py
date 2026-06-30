"""Ingest 1 văn bản DOffice qua pipeline 4 GIAI ĐOẠN tách bạch.

1. ``prepare_postgres`` (PG): lưu dữ liệu THÔ — lấy thế nào lưu thế đó. parsed_text =
   noi_dung thô; metadata = trường source thô + ACL THÔ (raw_assignment). KHÔNG làm sạch,
   KHÔNG nén ACL, KHÔNG chunk.
2. ``clean_data`` (Làm sạch, in-memory): normalize noi_dung + làm sạch tom_tat + NÉN ACL
   (allow[]/deny[]). Không ghi DB — chỉ làm giàu item.
3. ``index_elasticsearch`` (ES, BM25 doc-level): nội dung ĐÃ SẠCH + ACL ĐÃ NÉN (không lưu
   ACL thô).
4. ``index_qdrant`` (Qdrant): chunk nội dung đã sạch (in-memory) -> embed -> Col1 (chunks)
   + Col2 (docmeta), payload kèm ACL nén. Chunk ghi PG TẠM để embed rồi xóa (PG chỉ giữ raw).

Enrichment TẮT. Idempotent: chạy lại 1 văn bản -> xóa dấu vết cũ (PG/ES/Qdrant) rồi ghi lại.
Tái dùng VectorIndexingService (embed + Qdrant Col1). Runner gọi 4 luồng song song qua hàng đợi.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
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
    """Đơn vị công việc đi qua pipeline 4 luồng.

    Luồng 1 (PG) tạo item với dữ liệu THÔ. Luồng 2 (Làm sạch) điền các trường ``*_clean`` +
    ``normalized`` + ACL ĐÃ NÉN. Luồng 3 (ES) & Luồng 4 (Qdrant) tiêu thụ phần đã làm sạch.
    """

    id_vb: str
    document_id: str
    source: dict[str, Any]
    acl_lists: dict[str, Any]  # ACL THÔ (raw_assignment chưa nén)
    # --- điền sau khi Làm sạch (Luồng 2) ---
    normalized: Any = None  # NormalizedDofficeDocument (để chunk ở Luồng 4)
    clean_noi_dung: str | None = None
    clean_tom_tat: str | None = None
    acl_subjects: list[str] = field(default_factory=list)  # allow[] đã nén
    acl_deny: list[str] = field(default_factory=list)       # deny[] đã nén
    acl_payload: dict[str, Any] = field(default_factory=dict)
    has_acl: bool = False
    chunk_count: int = 0


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
        # DOffice: PG chỉ giữ RAW -> mặc định KHÔNG giữ chunk (xoá sau khi embed). Dùng
        # cờ riêng doffice_store_chunks_in_pg (KHÔNG dùng store_chunks_in_pg chung).
        self._store_chunks_in_pg = (
            settings.doffice_store_chunks_in_pg if store_chunks_in_pg is None else store_chunks_in_pg
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
        # Giữ tương thích: chạy tuần tự cả 4 giai đoạn (dùng cho mode id-lẻ/test). Pipeline
        # 4 luồng ở runner gọi: prepare_postgres -> clean_data -> index_elasticsearch + index_qdrant.
        item = await self.prepare_postgres(
            source=source,
            acl_lists=acl_lists,
            uploaded_by_user_id=uploaded_by_user_id,
            organization_id=organization_id,
            knowledge_base_id=knowledge_base_id,
        )
        await self.clean_data(item)
        await self.index_elasticsearch(item)
        await self.index_qdrant(item)
        logger.info(
            "Ingest 3-DB xong id_vb=%s document=%s chunks=%s", item.id_vb, item.document_id, item.chunk_count
        )
        return UnifiedIngestResult(
            id_vb=item.id_vb, document_id=item.document_id, chunks=item.chunk_count, has_acl=item.has_acl
        )

    # ===================== LUỒNG 1: PostgreSQL (RAW) ==========================
    async def prepare_postgres(
        self,
        *,
        source: dict[str, Any],
        acl_lists: dict[str, Any] | None = None,
        uploaded_by_user_id: Any = None,
        organization_id: Any = None,
        knowledge_base_id: Any = None,
    ) -> DofficeJobItem:
        """Luồng 1: lưu dữ liệu THÔ vào PostgreSQL — lấy thế nào lưu thế đó.

        KHÔNG làm sạch, KHÔNG nén ACL, KHÔNG chunk. ``parsed_text`` = noi_dung thô;
        metadata = các trường source thô + ACL THÔ (raw_assignment). Trả item để đẩy sang
        Luồng 2 (Làm sạch).
        """
        id_vb = " ".join(str(source.get("id_vb") or "").split()).strip()
        if not id_vb:
            raise ValueError("source.id_vb is required.")
        acl_lists = acl_lists or {}

        # Idempotent: xóa dấu vết cũ ở 3 DB trước khi ghi lại.
        existing = await self._find_existing(id_vb)
        if existing is not None:
            await self._delete_everywhere(existing, id_vb)

        raw_noi_dung = str(source.get("noi_dung") or "")
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
        # noi_dung thô vào parsed_text (nguồn sự thật). KHÔNG làm sạch.
        await self._repository.update_document_parsed_content(
            document, parsed_text=raw_noi_dung, parsed_at=datetime.now(UTC), status="parsed",
        )
        # Metadata = mọi trường source thô (trừ noi_dung đã ở parsed_text) + ACL THÔ chưa nén.
        metadata = {k: v for k, v in source.items() if k != "noi_dung"}
        metadata["access"] = {"raw_assignment": acl_lists, "acl_ver": self._signature}
        metadata["has_embedding"] = True
        # Cờ cho Job Qdrant: False = chưa embed (Job Qdrant quét cờ này để biết doc nào
        # cần chunk+embed). Re-sync (idempotent) tạo lại doc -> cờ về False -> embed lại.
        metadata["qdrant_indexed"] = False
        await self._repository.update_document_metadata(document, metadata)
        await self._repository.commit()

        return DofficeJobItem(
            id_vb=id_vb, document_id=str(document.id), source=source, acl_lists=acl_lists,
        )

    # ===================== LUỒNG 2: Làm sạch + nén ACL ========================
    async def clean_data(self, item: DofficeJobItem) -> DofficeJobItem:
        """Luồng 2: làm sạch ``noi_dung`` + ``tom_tat`` và NÉN ACL (allow[]/deny[]).

        Điền vào item: ``normalized`` (để chunk), ``clean_noi_dung``, ``clean_tom_tat``,
        ACL đã nén. KHÔNG ghi DB — chỉ làm giàu item để đẩy sang Luồng 3 (ES) & Luồng 4 (Qdrant).
        """
        # normalize là CPU nặng (doc lớn 1-7s) -> chạy ở THREAD để KHÔNG chặn event loop
        # (các luồng PG/ES + dashboard vẫn chạy khi đang normalize 1 doc lớn).
        normalized = await asyncio.to_thread(normalize_doffice_source, item.source)
        item.normalized = normalized
        item.clean_noi_dung = (normalized.clean_text or "").strip() or None
        item.clean_tom_tat = clean_for_chunking(str(item.source.get("tom_tat") or "")) or None
        if not item.clean_noi_dung:
            logger.warning("id_vb=%s không có nội dung sau khi làm sạch", item.id_vb)

        acl, acl_subjects, acl_deny, acl_payload = self._resolve_acl(item.acl_lists)
        item.acl_subjects = acl_subjects
        item.acl_deny = acl_deny
        item.acl_payload = acl_payload
        item.has_acl = acl is not None
        return item

    # ===================== LUỒNG 3: Elasticsearch (đã sạch + ACL nén) =========
    async def index_elasticsearch(self, item: DofficeJobItem) -> None:
        """Luồng 3: đổ BM25 doc-level — nội dung ĐÃ LÀM SẠCH + ACL ĐÃ NÉN (allow/deny).

        Không lưu ACL thô. ``tom_tat`` cũng dùng bản đã làm sạch.
        """
        fields = {**item.source, "tom_tat": item.clean_tom_tat or item.source.get("tom_tat")}
        await self._bm25_store.upsert_document(
            document_id=item.document_id,
            id_vb=item.id_vb,
            fields=fields,
            noi_dung_clean=item.clean_noi_dung,
            acl_subjects=item.acl_subjects,
            acl_deny=item.acl_deny,
            acl_ver=self._signature,
        )

    # ===================== LUỒNG 4: Qdrant (chunk + embed) ====================
    async def index_qdrant(self, item: DofficeJobItem, *, embed_progress: Any = None) -> None:
        """Luồng 4: chunk nội dung đã làm sạch (in-memory) -> embed -> Qdrant Col1 (chunks)
        & Col2 (docmeta) + ACL nén. Làm sạch -> chunk -> LƯU chunk vào PG -> embed (giữ
        chunk nếu doffice_store_chunks_in_pg=True; False = xoá sau khi embed)."""
        from uuid import UUID

        from app.services.chunkers.chunker_doffice_chunking import build_doffice_chunks
        from app.services.ingestion.ingestion_profiles import get_profile_config

        document_id = UUID(item.document_id)
        if item.normalized is None:
            await self.clean_data(item)  # an toàn nếu gọi trực tiếp (ngoài pipeline)

        cfg = get_profile_config("doffice_admin")
        chunk_records = await asyncio.to_thread(
            build_doffice_chunks,
            item.normalized,
            body_max_chars=int(cfg.get("doffice_body_max_chars") or 2800),
            body_overlap=int(cfg.get("doffice_body_overlap") or 300),
            table_max_chars=int(cfg.get("doffice_table_max_chars") or 3500),
        )
        item.chunk_count = len(chunk_records)

        # Lưu chunk vào PG (xoá chunk cũ của doc -> ghi mới) để VectorIndexingService embed.
        await self._repository.delete_chunks_for_document(document_id)
        await self._repository.create_chunks(document_id=document_id, chunks=chunk_records)
        # VectorIndexingService chỉ embed document ở trạng thái "chunked"/"indexed".
        document = await self._repository.get_document(document_id)
        if document is not None:
            await self._repository.update_document_status(document, "chunked")
        await self._repository.commit()
        await VectorIndexingService(
            repository=self._repository,
            llm_gateway=self._llm_gateway,
            vector_store=self._chunks_store,
            sparse_embedding_provider=self._sparse_provider,
            keyword_index_store=None,
        ).index_document(
            document_id,
            use_enriched_content_for_embedding=False,
            embed_batch_size=settings.doffice_embed_request_batch_size,
            on_embed_progress=embed_progress,
        )
        await self._chunks_store.set_acl_payload_for_document(document_id, item.acl_payload)

        if not self._store_chunks_in_pg:
            await self._repository.delete_chunks_for_document(document_id)
            await self._repository.commit()

        # Col2 (docmeta): embed metadata (tom_tat dùng bản đã sạch) + ACL nén.
        docmeta_source = {**item.source}
        if item.clean_tom_tat:
            docmeta_source["tom_tat"] = item.clean_tom_tat
        await self._index_docmeta(
            document_id=item.document_id, source=docmeta_source, acl_payload=item.acl_payload
        )

        # Đánh dấu đã embed -> Job Qdrant bỏ qua doc này ở lần quét sau.
        doc = await self._repository.get_document(document_id)
        if doc is not None:
            meta = dict(doc.document_metadata or {})
            meta["qdrant_indexed"] = True
            await self._repository.update_document_metadata(doc, meta)
            await self._repository.commit()

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
