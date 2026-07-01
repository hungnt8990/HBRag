"""Ingest 1 văn bản DOffice — PG là NGUỒN SỰ THẬT (raw + sạch + chunk + ACL nén).

1. ``prepare_postgres`` (PG): lưu THÔ. parsed_text = noi_dung thô; metadata = source thô +
   ACL THÔ (raw_assignment). KHÔNG làm sạch/nén/chunk.
2. ``clean_data`` (in-memory): normalize noi_dung + làm sạch tom_tat + NÉN ACL (allow/deny).
3. ``persist_to_postgres`` (PG): GHI nội dung SẠCH (``metadata["clean"]``) + ACL ĐÃ NÉN
   (``metadata["access"].acl_subjects/acl_deny``, cạnh raw) + CHUNK (bảng ``chunks``).
   Đặt ``metadata["pg_prepared"]=True``. (> max_chunks -> skip: lưu clean+ACL, không chunk.)
4. ``index_elasticsearch`` (ES doc-level): nội dung SẠCH + ACL NÉN (KHÔNG lưu ACL thô).
5. ``embed_to_qdrant`` (Qdrant): ĐỌC PG (chunk/clean/ACL) -> embed Col1 (chunks) + Col2
   (docmeta) + ACL/filter payload. KHÔNG re-chunk/re-clean. Dùng cho run_qdrant ("chỉ đọc PG").

``index_qdrant`` = persist_to_postgres + embed_to_qdrant (tương thích đường gọi cũ/legacy).
Enrichment TẮT. Idempotent. Runner: PG-raw -> Clean+Chunk(ghi PG) -> {ES, Qdrant đọc PG}.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from uuid import UUID
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.core.chunk_ids import deterministic_chunk_id
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
# Logger RIÊNG liệt kê văn bản BỎ QUA vì > max_chunks -> file ``vanban_bo_qua_qua_chunk.log``
# (handler do jobs.doffice_sync.logger.setup_job_logging gắn). Khi chạy ngoài job -> không có
# handler riêng, record propagate lên root (vô hại).
oversize_logger = logging.getLogger("doffice_sync.oversize")

# Trường đưa vào payload Qdrant docmeta (Col 2) — đúng object mẫu, TRỪ noi_dung.
_DOCMETA_FIELDS = (
    "id_vb", "ky_hieu", "trich_yeu", "id_dv_ban_hanh", "noi_ban_hanh", "nguoi_ky",
    "ten_file", "duong_dan", "tom_tat", "ngay_tao", "type_ocr", "ngay_capnhat",
    "nam", "thang", "ngay_vb",
)
# Text để embed docmeta: ngữ nghĩa (trich_yeu/tom_tat/noi_ban_hanh) + ký hiệu (ky_hieu).
_DOCMETA_EMBED_FIELDS = ("ky_hieu", "trich_yeu", "tom_tat", "noi_ban_hanh", "ten_file")

# Trường LỌC cấp văn bản gắn vào MỌI chunk của Col1 (để filter thời gian/đơn vị ở cấp chunk,
# đồng bộ với Col2). int: nam/thang/id_dv_ban_hanh; chuỗi ISO: ngay_vb. Xem docs/METADATA_SCHEMA.md.
_C1_DOC_FILTER_INT_FIELDS = ("nam", "thang", "id_dv_ban_hanh")
_C1_DOC_FILTER_STR_FIELDS = ("ngay_vb",)


def _coerce_int(value: Any) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _build_c1_doc_filter_payload(source: dict[str, Any]) -> dict[str, Any]:
    """Dựng payload lọc cấp văn bản cho chunk (Col1) từ source thô."""
    payload: dict[str, Any] = {}
    for field in _C1_DOC_FILTER_INT_FIELDS:
        coerced = _coerce_int(source.get(field))
        if coerced is not None:
            payload[field] = coerced
    for field in _C1_DOC_FILTER_STR_FIELDS:
        value = source.get(field)
        if value not in (None, ""):
            payload[field] = str(value)
    return payload

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
    # True nếu BỎ QUA vì vượt ngưỡng max_chunks (KHÔNG embed, KHÔNG đánh dấu qdrant_indexed).
    skipped: bool = False
    # Chunk vừa dựng (ChunkCreate) — luồng ES chunk dùng để index nhánh 2 (in-memory).
    chunk_records: list[Any] = field(default_factory=list)


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
        chunk_bm25_store: Any = None,
    ) -> None:
        self._repository = repository
        self._chunking_service = chunking_service
        self._llm_gateway = llm_gateway
        self._sparse_provider = sparse_provider
        self._chunks_store = chunks_store
        self._docmeta_store = docmeta_store
        self._bm25_store = bm25_store
        self._chunk_bm25_store = chunk_bm25_store  # nhánh ES chunk (None = không index)
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

    # ============ Các bước XỬ LÝ tách rời (cho 3 pool worker vật lý) ===========
    async def clean_only(self, item: DofficeJobItem) -> DofficeJobItem:
        """Chỉ LÀM SẠCH: normalize (CPU nặng -> THREAD) + làm sạch noi_dung/tom_tat. KHÔNG ACL."""
        normalized = await asyncio.to_thread(normalize_doffice_source, item.source)
        item.normalized = normalized
        item.clean_noi_dung = clean_for_chunking(normalized.clean_text or "") or None
        item.clean_tom_tat = clean_for_chunking(str(item.source.get("tom_tat") or "")) or None
        if not item.clean_noi_dung:
            logger.warning("id_vb=%s không có nội dung sau khi làm sạch", item.id_vb)
        return item

    def compress_acl(self, item: DofficeJobItem) -> DofficeJobItem:
        """Chỉ NÉN ACL (in-memory): điền acl_subjects/acl_deny/acl_payload."""
        acl, acl_subjects, acl_deny, acl_payload = self._resolve_acl(item.acl_lists)
        item.acl_subjects = acl_subjects
        item.acl_deny = acl_deny
        item.acl_payload = acl_payload
        item.has_acl = acl is not None
        return item

    async def clean_data(self, item: DofficeJobItem) -> DofficeJobItem:
        """Tương thích: làm sạch + nén ACL (in-memory). = clean_only + compress_acl."""
        await self.clean_only(item)
        self.compress_acl(item)
        return item

    async def persist_clean(self, item: DofficeJobItem) -> None:
        """Ghi nội dung SẠCH vào PG: ``metadata["clean"]``."""
        document = await self._repository.get_document(UUID(item.document_id))
        if document is None:
            return
        meta = dict(document.document_metadata or {})
        meta["clean"] = {"noi_dung": item.clean_noi_dung, "tom_tat": item.clean_tom_tat}
        await self._repository.update_document_metadata(document, meta)
        await self._repository.commit()

    async def persist_acl(self, item: DofficeJobItem) -> None:
        """Ghi ACL ĐÃ NÉN vào PG: ``metadata["access"].acl_subjects/acl_deny`` (giữ raw)."""
        document = await self._repository.get_document(UUID(item.document_id))
        if document is None:
            return
        meta = dict(document.document_metadata or {})
        access = dict(meta.get("access") or {})
        access[F_SUBJECTS] = item.acl_subjects
        access[F_DENY] = item.acl_deny
        meta["access"] = access
        await self._repository.update_document_metadata(document, meta)
        await self._repository.commit()

    async def persist_chunks(self, item: DofficeJobItem, *, max_chunks: int | None = None) -> None:
        """CHUNK + ghi bảng ``chunks`` + đặt ``pg_prepared``. > max_chunks -> skip (không chunk)."""
        from app.services.chunkers.chunker_doffice_chunking import build_doffice_chunks
        from app.services.ingestion.ingestion_profiles import get_profile_config

        document_id = UUID(item.document_id)
        document = await self._repository.get_document(document_id)
        if document is None:
            return
        cfg = get_profile_config("doffice_admin")
        chunk_records = await asyncio.to_thread(
            build_doffice_chunks, item.normalized,
            body_max_chars=int(cfg.get("doffice_body_max_chars") or 2800),
            body_overlap=int(cfg.get("doffice_body_overlap") or 300),
            table_max_chars=int(cfg.get("doffice_table_max_chars") or 3500),
        )
        item.chunk_count = len(chunk_records)
        item.chunk_records = chunk_records
        meta = dict(document.document_metadata or {})
        meta["chunk_count"] = item.chunk_count
        if max_chunks and item.chunk_count > max_chunks:
            item.skipped = True
            meta["pg_prepared"] = False
            logger.warning("id_vb=%s BỎ QUA: %s chunk > max %s (không chunk)", item.id_vb, item.chunk_count, max_chunks)
            oversize_logger.warning(
                "id_vb=%s ky_hieu=%s BỎ QUA (không chunk): %s chunk > ngưỡng %s",
                item.id_vb, (item.source or {}).get("ky_hieu", "") or "", item.chunk_count, max_chunks,
            )
            await self._repository.update_document_metadata(document, meta)
            await self._repository.commit()
            return
        await self._repository.delete_chunks_for_document(document_id)
        await self._repository.create_chunks(document_id=document_id, chunks=chunk_records)
        await self._repository.update_document_status(document, "chunked")
        meta["pg_prepared"] = True
        await self._repository.update_document_metadata(document, meta)
        await self._repository.commit()

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

    # ============== LUỒNG 3b: Elasticsearch CHUNK (nhánh 2) ===================
    async def index_elasticsearch_chunks(self, item: DofficeJobItem) -> None:
        """Nhánh ES thứ 2: index TỪNG chunk (chunk_text + ngữ cảnh + ACL nén) để BM25 đúng
        đoạn/căn cứ. Dùng ``item.chunk_records`` (đã dựng ở persist_to_postgres). Idempotent:
        xoá chunk cũ theo id_vb rồi bulk ghi. KHÔNG lưu ACL thô."""
        if self._chunk_bm25_store is None:
            return
        chunk_records = item.chunk_records or []
        if not chunk_records:
            # Văn bản bỏ qua/không có chunk -> vẫn dọn chunk ES cũ (idempotent).
            await self._chunk_bm25_store.delete_by_id_vb(item.id_vb)
            return
        src = item.source
        doc_filter = _build_c1_doc_filter_payload(src)  # nam/thang/ngay_vb/id_dv_ban_hanh
        records: list[dict[str, Any]] = []
        for chunk in chunk_records:
            meta = chunk.metadata or {}
            section = meta.get("section_path")
            record: dict[str, Any] = {
                "document_id": item.document_id,
                "id_vb": str(item.id_vb),
                "chunk_id": str(deterministic_chunk_id(item.document_id, chunk.chunk_index)),
                "chunk_index": chunk.chunk_index,
                "chunk_type": meta.get("chunk_type"),
                "chunk_text": chunk.content,
                "section_path": " > ".join(str(p) for p in section) if isinstance(section, (list, tuple)) else section,
                "table_name": meta.get("table_name"),
                "ky_hieu": src.get("ky_hieu"),
                "trich_yeu": src.get("trich_yeu"),
                "noi_ban_hanh": src.get("noi_ban_hanh"),
                **doc_filter,
            }
            record = {k: v for k, v in record.items() if v not in (None, "", [])}
            # ACL luôn ghi (kể cả rỗng) để lọc nhất quán.
            record["acl_subjects"] = item.acl_subjects
            record["acl_deny"] = item.acl_deny
            if self._signature:
                record["acl_ver"] = self._signature
            records.append(record)
        await self._chunk_bm25_store.delete_by_id_vb(item.id_vb)
        await self._chunk_bm25_store.bulk_upsert_chunks(records)

    # ============ LUỒNG Làm sạch + Chunk + Nén ACL -> GHI PostgreSQL ==========
    async def persist_to_postgres(
        self, item: DofficeJobItem, *, max_chunks: int | None = None
    ) -> DofficeJobItem:
        """Làm sạch + nén ACL + CHUNK -> LƯU TẤT CẢ vào PostgreSQL (không embed).

        Ghi vào PG: ``metadata["clean"]`` (noi_dung/tom_tat đã sạch), ``metadata["access"]``
        thêm ACL ĐÃ NÉN (acl_subjects/acl_deny, cạnh raw_assignment), và bảng ``chunks``.
        Đặt ``metadata["pg_prepared"]=True`` để ``run_qdrant`` biết chỉ cần ĐỌC PG rồi embed.

        ``max_chunks``: > ngưỡng -> ``item.skipped=True``, KHÔNG chunk/embed, KHÔNG đánh dấu
        pg_prepared (giữ pending). Vẫn lưu clean + ACL nén để tra cứu."""
        from uuid import UUID

        from app.services.chunkers.chunker_doffice_chunking import build_doffice_chunks
        from app.services.ingestion.ingestion_profiles import get_profile_config

        document_id = UUID(item.document_id)
        if item.normalized is None:
            await self.clean_data(item)  # normalize + làm sạch tom_tat + nén ACL (in-memory)
        document = await self._repository.get_document(document_id)
        if document is None:
            return item

        meta = dict(document.document_metadata or {})
        # 1) Nội dung ĐÃ SẠCH.
        meta["clean"] = {"noi_dung": item.clean_noi_dung, "tom_tat": item.clean_tom_tat}
        # 2) ACL ĐÃ NÉN (cạnh raw_assignment — KHÔNG xoá raw để truy vết).
        access = dict(meta.get("access") or {})
        access[F_SUBJECTS] = item.acl_subjects
        access[F_DENY] = item.acl_deny
        meta["access"] = access

        # 3) Chunk.
        cfg = get_profile_config("doffice_admin")
        chunk_records = await asyncio.to_thread(
            build_doffice_chunks,
            item.normalized,
            body_max_chars=int(cfg.get("doffice_body_max_chars") or 2800),
            body_overlap=int(cfg.get("doffice_body_overlap") or 300),
            table_max_chars=int(cfg.get("doffice_table_max_chars") or 3500),
        )
        item.chunk_count = len(chunk_records)
        item.chunk_records = chunk_records  # cho luồng ES chunk (nhánh 2) index in-memory
        meta["chunk_count"] = item.chunk_count

        if max_chunks and item.chunk_count > max_chunks:
            item.skipped = True
            meta["pg_prepared"] = False
            logger.warning(
                "id_vb=%s BỎ QUA: %s chunk > max %s -> lưu clean+ACL nhưng KHÔNG chunk/embed",
                item.id_vb, item.chunk_count, max_chunks,
            )
            await self._repository.update_document_metadata(document, meta)
            await self._repository.commit()
            return item

        # Ghi chunk (xoá chunk cũ -> ghi mới) + chuyển trạng thái sang "chunked".
        await self._repository.delete_chunks_for_document(document_id)
        await self._repository.create_chunks(document_id=document_id, chunks=chunk_records)
        await self._repository.update_document_status(document, "chunked")
        meta["pg_prepared"] = True
        await self._repository.update_document_metadata(document, meta)
        await self._repository.commit()
        return item

    def _hydrate_from_pg(self, item: DofficeJobItem, document: Any) -> None:
        """Điền ACL nén + nội dung sạch từ PG vào item (khi item dựng lại từ PG ở run_qdrant)."""
        meta = document.document_metadata or {}
        clean = meta.get("clean") or {}
        access = meta.get("access") or {}
        if not item.acl_payload:
            subs = list(access.get(F_SUBJECTS) or [])
            deny = list(access.get(F_DENY) or [])
            item.acl_subjects = subs
            item.acl_deny = deny
            item.acl_payload = {F_SUBJECTS: subs, F_DENY: deny}
            item.has_acl = bool(subs or deny)
        if item.clean_tom_tat is None:
            item.clean_tom_tat = clean.get("tom_tat")
        if item.clean_noi_dung is None:
            item.clean_noi_dung = clean.get("noi_dung")

    # ================ LUỒNG Qdrant: ĐỌC PG (chunk+clean+ACL) -> embed ==========
    async def embed_to_qdrant(self, item: DofficeJobItem, *, embed_progress: Any = None) -> None:
        """Embed lên Qdrant Col1 (chunks) + Col2 (docmeta) bằng dữ liệu ĐÃ CÓ TRONG PG.

        KHÔNG re-chunk, KHÔNG re-clean: chunk đọc từ bảng ``chunks`` (qua VectorIndexingService),
        ACL nén + tom_tat sạch đọc từ ``metadata``. Dùng cho run_qdrant ("chỉ đọc PG")."""
        from uuid import UUID

        document_id = UUID(item.document_id)
        document = await self._repository.get_document(document_id)
        if document is None:
            return
        self._hydrate_from_pg(item, document)

        # VectorIndexingService chỉ embed document ở trạng thái "chunked"/"indexed".
        if document.status not in ("chunked", "indexed"):
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
        # Gắn ACL + trường LỌC cấp văn bản (nam/thang/ngay_vb/id_dv_ban_hanh) lên MỌI chunk.
        c1_doc_payload = {**item.acl_payload, **_build_c1_doc_filter_payload(item.source)}
        await self._chunks_store.set_acl_payload_for_document(document_id, c1_doc_payload)

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

    # ============ Tương thích: chuẩn bị PG rồi embed (1 lượt) =================
    async def index_qdrant(
        self, item: DofficeJobItem, *, embed_progress: Any = None, max_chunks: int | None = None
    ) -> None:
        """Tương thích cũ: persist_to_postgres (sạch+chunk+ACL -> PG) RỒI embed_to_qdrant.

        Dùng cho đường gọi tuần tự/legacy. ``max_chunks`` > ngưỡng -> bỏ qua (không embed)."""
        await self.persist_to_postgres(item, max_chunks=max_chunks)
        if item.skipped:
            return
        await self.embed_to_qdrant(item, embed_progress=embed_progress)

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

    async def delete_by_id_vb(
        self, id_vb: str, *, pg: bool = True, es: bool = True, qdrant: bool = True
    ) -> bool:
        """Xóa 1 văn bản khỏi các store ĐƯỢC CHỌN (mặc định cả 3) theo id_vb.

        ``pg``/``es``/``qdrant``: bật/tắt từng store. True nếu tìm thấy Document trong PG.
        Nếu PG không còn doc -> chỉ dọn được ES (Qdrant cần document_id) và trả False.
        """
        id_vb = " ".join(str(id_vb or "").split()).strip()
        if not id_vb:
            return False
        document = await self._find_existing(id_vb)
        if document is None:
            if es:  # PG đã hết doc -> vẫn cố dọn dấu vết ES theo id_vb
                await self._delete_es(id_vb)
            return False
        await self._delete_stores(document, id_vb, pg=pg, es=es, qdrant=qdrant)
        return True

    async def _delete_es(self, id_vb: str) -> None:
        try:
            await self._bm25_store.delete_by_id_vb(id_vb)
        except Exception:
            logger.exception("Xóa ES doc id_vb=%s thất bại", id_vb)
        if self._chunk_bm25_store is not None:
            try:
                await self._chunk_bm25_store.delete_by_id_vb(id_vb)
            except Exception:
                logger.exception("Xóa ES chunk id_vb=%s thất bại", id_vb)

    async def _delete_stores(
        self, document: Any, id_vb: str, *, pg: bool = True, es: bool = True, qdrant: bool = True
    ) -> None:
        document_id = document.id
        tenant_id = getattr(document, "organization_id", None)
        if qdrant:
            for store in (self._chunks_store, self._docmeta_store):
                try:
                    await store.delete_points_for_document(document_id, tenant_id=tenant_id)
                except Exception:
                    logger.exception("Xóa Qdrant point thất bại document=%s", document_id)
        if es:
            await self._delete_es(id_vb)
        if pg:
            await self._repository.delete_document(document)
            await self._repository.commit()

    async def _delete_everywhere(self, document: Any, id_vb: str) -> None:
        """Xóa khỏi CẢ 3 store (dùng cho re-ingest idempotent)."""
        await self._delete_stores(document, id_vb, pg=True, es=True, qdrant=True)
