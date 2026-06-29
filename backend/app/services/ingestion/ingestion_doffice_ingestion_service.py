from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from app.core.config import settings
from app.repositories.documents import DocumentRepository
from app.repositories.knowledge_artifacts import KnowledgeArtifactRepository
from app.schemas.documents import DofficeIngestResponse
from app.services.chunkers.chunker_chunk_enrichment_service import ChunkEnrichmentService
from app.services.chunkers.chunker_chunking_service import ChunkingService
from app.services.document_sources import (
    DOFFICE_SOURCE_TYPE,
    DofficeDocument,
    DofficeElasticsearchSource,
)
from app.services.ingestion.ingestion_doffice_content_normalizer import (
    NormalizedDofficeDocument,
    normalize_doffice_source,
)
from app.services.retrieval.retrieval_elasticsearch_keyword_search import ElasticsearchKeywordStore
from app.services.knowledge.knowledge_artifact_compiler import KnowledgeArtifactCompiler
from app.services.knowledge.knowledge_artifact_indexing_service import KnowledgeArtifactIndexingService
from app.services.security.security_acl_compressor import OrgCatalog
from app.services.security.security_acl_payload import to_chunk_payload_flat
from app.services.security.security_acl_resolver import (
    UnitTree,
    resolve_doffice_and_compress,
)
from app.services.vector.vector_indexing_service import VectorIndexingService
from app.services.vector.vector_store import QdrantVectorStore

logger = logging.getLogger(__name__)


class EmptyDofficeDocumentError(ValueError):
    pass


class DofficeIngestionError(RuntimeError):
    pass


DofficeProgressCallback = Callable[[str, str, str, dict[str, Any] | None], None]


@dataclass(frozen=True)
class DofficeIngestOptions:
    force_refresh: bool = False
    enable_enrichment: bool = True
    progress_callback: DofficeProgressCallback | None = None


class DofficeIngestionService:
    def __init__(
        self,
        *,
        repository: DocumentRepository,
        source: DofficeElasticsearchSource,
        chunking_service: ChunkingService,
        vector_indexing_service: VectorIndexingService,
        vector_store: QdrantVectorStore,
        enrichment_service: ChunkEnrichmentService | None = None,
        keyword_index_store: ElasticsearchKeywordStore | None = None,
        knowledge_artifact_repository: KnowledgeArtifactRepository | None = None,
        artifact_indexing_service: KnowledgeArtifactIndexingService | None = None,
        document_index_store: Any = None,
        llm_gateway: Any = None,
    ) -> None:
        self._repository = repository
        self._source = source
        self._chunking_service = chunking_service
        self._vector_indexing_service = vector_indexing_service
        self._vector_store = vector_store
        self._enrichment_service = enrichment_service
        self._keyword_index_store = keyword_index_store
        self._knowledge_artifact_repository = knowledge_artifact_repository
        self._artifact_indexing_service = artifact_indexing_service
        self._document_index_store = document_index_store
        self._llm_gateway = llm_gateway

    async def ingest_doffice_document(
        self,
        id_vb: str,
        options: DofficeIngestOptions,
        *,
        uploaded_by_user_id: UUID | None,
        organization_id: UUID | None,
        knowledge_base_id: UUID | None,
        access: dict[str, Any] | None = None,
    ) -> DofficeIngestResponse:
        clean_id = " ".join(str(id_vb or "").split()).strip()
        if not clean_id:
            raise ValueError("id_vb is required.")

        started = time.perf_counter()
        existing = await self._find_existing(clean_id)
        pg_exists = existing is not None
        es_exists = await self._document_index_has(clean_id)

        # force_refresh: ép ingest đầy đủ -> dọn sạch PG/ES rồi lấy lại từ DOffice.
        if options.force_refresh:
            if pg_exists:
                await self._delete_existing(existing)
            if es_exists:
                await self._delete_document_index(clean_id)
            return await self._full_ingest_from_doffice(
                clean_id,
                options,
                started=started,
                uploaded_by_user_id=uploaded_by_user_id,
                organization_id=organization_id,
                knowledge_base_id=knowledge_base_id,
                access=access,
            )

        # CẢ 2 ĐỀU CÓ (đã sync) -> chunk + đẩy Qdrant/chunk-ES, KHÔNG tạo/đè document-level.
        if pg_exists and es_exists:
            return await self._ingest_existing_for_retrieval(
                existing, clean_id, options, started=started
            )

        # LỆCH 1 DB (XOR) -> xóa phần thừa rồi ingest đầy đủ lại từ DOffice.
        if pg_exists != es_exists:
            logger.warning(
                "DOffice id_vb=%s lệch PG/ES (pg=%s es=%s) -> xóa phần thừa + ingest lại",
                clean_id,
                pg_exists,
                es_exists,
            )
            if pg_exists:
                await self._delete_existing(existing)
            if es_exists:
                await self._delete_document_index(clean_id)
            _emit_progress(
                options,
                "parse",
                "running",
                "Phát hiện lệch PG/ES; đã xóa phần thừa, ingest lại đầy đủ từ DOffice.",
                {"id_vb": clean_id, "pg_exists": pg_exists, "es_exists": es_exists},
            )

        # CẢ 2 ĐỀU KHÔNG CÓ (hoặc vừa dọn do lệch) -> ingest đầy đủ từ DOffice.
        return await self._full_ingest_from_doffice(
            clean_id,
            options,
            started=started,
            uploaded_by_user_id=uploaded_by_user_id,
            organization_id=organization_id,
            knowledge_base_id=knowledge_base_id,
            access=access,
        )

    def _doc_index_store(self) -> Any:
        """DocumentIndexStore để kiểm tra/xóa tồn tại ES (cần cả khi two-stage tắt).

        KHÔNG gán vào ``self._document_index_store`` để tránh bật nhầm việc ghi
        ``hbrag_documents_v1`` ở ``_attach_acl_from_source`` (vốn gate theo two-stage).
        """
        if self._document_index_store is not None:
            return self._document_index_store
        try:
            from app.services.retrieval.retrieval_document_index import DocumentIndexStore

            return DocumentIndexStore()
        except Exception:
            logger.exception("Không khởi tạo được DocumentIndexStore để kiểm tra tồn tại")
            return None

    async def _document_index_has(self, id_vb: str) -> bool:
        store = self._doc_index_store()
        if store is None:
            return False
        found = await store.existing_id_vb([id_vb])
        return id_vb in found

    async def _delete_document_index(self, id_vb: str) -> None:
        store = self._doc_index_store()
        if store is None:
            return
        try:
            deleted = await store.delete_by_id_vb(id_vb)
            logger.info("Đã xóa %s record ES document index id_vb=%s", deleted, id_vb)
        except Exception:
            logger.exception("Xóa ES document index thất bại id_vb=%s", id_vb)

    async def _ingest_existing_for_retrieval(
        self,
        document: Any,
        clean_id: str,
        options: DofficeIngestOptions,
        *,
        started: float,
    ) -> DofficeIngestResponse:
        """Nhánh 'cả 2 DB đều có': chunk + đẩy Qdrant/chunk-ES, không tạo document mới.

        Đọc noi_dung từ PG (job sync lưu ``noi_dung_raw``); doc cũ chưa có thì lấy 1 lần
        từ DOffice rồi cache lại. KHÔNG ghi đè ``hbrag_documents_v1`` (ACL/định danh do
        sync quản lý) — chỉ gắn ACL cho point Qdrant + chunk-ES.
        """

        document_id = document.id
        meta = dict(getattr(document, "document_metadata", None) or {})

        existing_chunks = await self._repository.count_chunks_for_document(document_id)
        if existing_chunks > 0:
            _emit_progress(options, "parse", "succeeded", "Văn bản đã có chunk; tái sử dụng (không chunk lại).", {"document_id": str(document_id), "id_vb": clean_id, "chunk_count": existing_chunks})
            _emit_progress(options, "chunk", "succeeded", "Tái sử dụng chunk hiện có.", {"chunk_count": existing_chunks})
            _emit_progress(options, "index", "succeeded", "Tái sử dụng vector/keyword index hiện có.", {"document_id": str(document_id)})
            return DofficeIngestResponse(
                status="skipped",
                id_vb=clean_id,
                ky_hieu=_optional_string(meta.get("ky_hieu")),
                trich_yeu=_optional_string(meta.get("trich_yeu")),
                noi_ban_hanh=_optional_string(meta.get("noi_ban_hanh")),
                chunks_created=existing_chunks,
                document_id=document_id,
                source_type=DOFFICE_SOURCE_TYPE,
                message="Văn bản đã được chunk/index trước đó; tái sử dụng.",
            )

        noi_dung = _optional_string(meta.get("noi_dung_raw"))
        if not noi_dung:
            _emit_progress(options, "parse", "running", "PG chưa có noi_dung_raw -> lấy 1 lần từ DOffice và cache lại.", {"id_vb": clean_id})
            fetched = await self._source.fetch_document_by_id_vb(clean_id)
            noi_dung = fetched.raw_noi_dung
            await self._repository.update_document_metadata(document, {"noi_dung_raw": noi_dung})

        source = self._source_dict_from_metadata(meta, noi_dung)
        normalized = normalize_doffice_source(source)
        if not normalized.clean_text.strip():
            raise EmptyDofficeDocumentError(
                f"DOffice document id_vb={clean_id} has no content after cleaning."
            )
        source_document = self._doffice_document_from_source(source, normalized)

        document.document_profile = "doffice_admin"
        await self._repository.update_document_parsed_content(
            document,
            parsed_text=normalized.clean_text,
            parsed_at=datetime.now(UTC),
            status="parsed",
        )
        await self._repository.update_document_metadata(
            document,
            self._document_metadata(source_document, normalized),
        )
        await self._repository.commit()
        _emit_progress(options, "parse", "succeeded", "Đã nạp nội dung normalized vào document đã sync (không tạo mới).", {"document_id": str(document_id), "id_vb": clean_id})

        try:
            _emit_progress(options, "chunk", "running", "Chunking DOffice document.", {"document_id": str(document_id), "profile": "auto"})
            chunk_response = await self._chunking_service.chunk_document(document_id, profile="auto")
            _emit_progress(options, "chunk", "succeeded", "DOffice document chunking completed.", {"document_id": str(document_id), "chunk_count": chunk_response.chunk_count})

            artifact_count = await self._compile_and_index_artifacts(document=document, options=options)

            if options.enable_enrichment and self._enrichment_service is not None:
                _emit_progress(options, "enrich", "running", "Running chunk enrichment for DOffice document.", {"document_id": str(document_id)})
                enrich_response = await self._enrichment_service.enrich_document(document_id, enabled=True, update_keyword_search_vector=True)
                _emit_progress(options, "enrich", "succeeded", "Chunk enrichment completed.", {"document_id": str(document_id), "status": enrich_response.status, "enriched_count": enrich_response.enriched_count, "skipped_count": enrich_response.skipped_count, "failed_count": enrich_response.failed_count})
            else:
                _emit_progress(options, "enrich", "succeeded", "Chunk enrichment skipped.", {"document_id": str(document_id), "enabled": options.enable_enrichment, "has_enrichment_service": self._enrichment_service is not None})

            _emit_progress(options, "index", "running", "Indexing DOffice chunks into Qdrant and Elasticsearch keyword store if enabled.", {"document_id": str(document_id), "artifact_count": artifact_count})
            index_response = await self._vector_indexing_service.index_document(document_id, use_enriched_content_for_embedding=options.enable_enrichment)
            # write_document_index=False: bản ghi document-level + ACL do job sync sở hữu;
            # chỉ gắn ACL cho point Qdrant + chunk-ES, KHÔNG đè hbrag_documents_v1.
            await self._attach_acl_from_source(document_id=document_id, source_document=source_document, write_document_index=False)
            _emit_progress(options, "index", "succeeded", "Vector and keyword indexing completed (document-level giữ nguyên bản sync).", {"document_id": str(document_id), "status": index_response.status, "indexed_chunk_count": index_response.indexed_chunk_count, "indexed_artifact_count": artifact_count, "duration_ms": _duration_ms(started)})
        except Exception as exc:
            logger.exception("DOffice retrieval-ingest failed id_vb=%s document=%s", clean_id, document_id)
            raise DofficeIngestionError(f"Failed to ingest DOffice document: {exc}") from exc

        return DofficeIngestResponse(
            status="success",
            id_vb=clean_id,
            ky_hieu=source_document.ky_hieu,
            trich_yeu=source_document.trich_yeu,
            noi_ban_hanh=source_document.noi_ban_hanh,
            chunks_created=chunk_response.chunk_count,
            document_id=document_id,
            source_type=DOFFICE_SOURCE_TYPE,
            message="Văn bản đã có ở PG+ES; đã chunk và đẩy Qdrant/chunk-ES, giữ nguyên bản ghi document-level đã sync.",
        )

    @staticmethod
    def _source_dict_from_metadata(meta: dict[str, Any], noi_dung: str) -> dict[str, Any]:
        access = meta.get("access") or {}
        raw_assignment = access.get("raw_assignment") or {}
        return {
            "id_vb": meta.get("id_vb"),
            "ky_hieu": meta.get("ky_hieu"),
            "trich_yeu": meta.get("trich_yeu"),
            "id_dv_ban_hanh": meta.get("id_dv_ban_hanh"),
            "noi_ban_hanh": meta.get("noi_ban_hanh"),
            "nguoi_ky": meta.get("nguoi_ky"),
            "ten_file": meta.get("ten_file"),
            "duong_dan": meta.get("duong_dan"),
            "ngay_vb": meta.get("ngay_vb"),
            "ngay_tao": meta.get("ngay_tao"),
            "ngay_capnhat": meta.get("ngay_capnhat"),
            "nam": meta.get("nam"),
            "thang": meta.get("thang"),
            "tom_tat": meta.get("tom_tat"),
            "noi_dung": noi_dung,
            "don_vi_list": raw_assignment.get("don_vi_list"),
            "phong_ban_list": raw_assignment.get("phong_ban_list"),
            "ca_nhan_list": raw_assignment.get("ca_nhan_list"),
        }

    @staticmethod
    def _doffice_document_from_source(source: dict[str, Any], normalized: NormalizedDofficeDocument) -> DofficeDocument:
        return DofficeDocument(
            id_vb=str(source.get("id_vb") or ""),
            ky_hieu=_optional_string(source.get("ky_hieu")),
            trich_yeu=_optional_string(source.get("trich_yeu")),
            id_dv_ban_hanh=source.get("id_dv_ban_hanh"),
            noi_ban_hanh=_optional_string(source.get("noi_ban_hanh")),
            nguoi_ky=_optional_string(source.get("nguoi_ky")),
            ten_file=_optional_string(source.get("ten_file")),
            duong_dan=_optional_string(source.get("duong_dan")),
            raw_noi_dung=str(source.get("noi_dung") or ""),
            ngay_vb=_optional_string(source.get("ngay_vb")),
            ngay_tao=_optional_string(source.get("ngay_tao")),
            ngay_capnhat=_optional_string(source.get("ngay_capnhat")),
            nam=source.get("nam"),
            thang=source.get("thang"),
            tom_tat=_optional_string(source.get("tom_tat")),
            clean_text=normalized.clean_text,
            raw_source=source,
        )

    async def _full_ingest_from_doffice(
        self,
        clean_id: str,
        options: DofficeIngestOptions,
        *,
        started: float,
        uploaded_by_user_id: UUID | None,
        organization_id: UUID | None,
        knowledge_base_id: UUID | None,
        access: dict[str, Any] | None,
    ) -> DofficeIngestResponse:
        """Ingest đầy đủ từ DOffice (pipeline cũ): tạo Document + chunk + index + ghi
        ``hbrag_documents_v1``. Dùng cho nhánh 'cả 2 đều không có' và force_refresh."""

        _emit_progress(
            options,
            "parse",
            "running",
            f"Fetching DOffice source document id_vb={clean_id}.",
            {"id_vb": clean_id},
        )
        source_document = await self._source.fetch_document_by_id_vb(clean_id)
        raw_source = dict(source_document.raw_source or {})
        if not raw_source:
            raw_source = _source_document_to_payload(source_document)
        normalized = normalize_doffice_source(raw_source)
        raw_record = await self._repository.upsert_doffice_raw_document(
            payload=raw_source,
            content_hash=normalized.content_hash,
            metadata_hash=normalized.metadata_hash,
            source_type=DOFFICE_SOURCE_TYPE,
        )
        raw_record_id = getattr(raw_record, "id", None)
        await self._update_raw_status(
            raw_record=raw_record,
            raw_record_id=raw_record_id,
            parse_status="normalized",
            clean_status="cleaned",
        )
        _emit_progress(
            options,
            "parse",
            "running",
            "DOffice source fetched and normalized to clean text.",
            {
                "id_vb": clean_id,
                "clean_characters": len(normalized.clean_text),
                "table_count": len(normalized.tables),
                "element_count": len(normalized.elements),
            },
        )
        if not normalized.clean_text.strip():
            raise EmptyDofficeDocumentError(
                f"DOffice document id_vb={clean_id} has no content after cleaning."
            )

        logger.info(
            "Starting DOffice ingest id_vb=%s clean_chars=%s force_refresh=%s enable_enrichment=%s",
            clean_id,
            len(normalized.clean_text),
            options.force_refresh,
            options.enable_enrichment,
        )
        document = await self._create_document(
            source_document,
            normalized,
            uploaded_by_user_id=uploaded_by_user_id,
            organization_id=organization_id,
            knowledge_base_id=knowledge_base_id,
            access=access,
        )
        document_id = document.id
        _emit_progress(
            options,
            "parse",
            "succeeded",
            "DOffice document created in PostgreSQL with parsed content.",
            {"document_id": str(document_id), "id_vb": clean_id},
        )

        try:
            _emit_progress(
                options,
                "chunk",
                "running",
                "Chunking DOffice document.",
                {"document_id": str(document_id), "profile": "auto"},
            )
            chunk_response = await self._chunking_service.chunk_document(document_id, profile="auto")
            logger.info(
                "Chunked DOffice document id_vb=%s document=%s chunks=%s",
                clean_id,
                document_id,
                chunk_response.chunk_count,
            )
            await self._update_raw_status(raw_record=raw_record, raw_record_id=raw_record_id, chunk_status="chunked")
            _emit_progress(
                options,
                "chunk",
                "succeeded",
                "DOffice document chunking completed.",
                {"document_id": str(document_id), "chunk_count": chunk_response.chunk_count},
            )

            artifact_count = await self._compile_and_index_artifacts(document=document, options=options)

            if options.enable_enrichment and self._enrichment_service is not None:
                _emit_progress(
                    options,
                    "enrich",
                    "running",
                    "Running chunk enrichment for DOffice document.",
                    {"document_id": str(document_id)},
                )
                enrich_response = await self._enrichment_service.enrich_document(
                    document_id,
                    enabled=True,
                    update_keyword_search_vector=True,
                )
                logger.info(
                    "Enriched DOffice document id_vb=%s document=%s status=%s enriched=%s skipped=%s failed=%s",
                    clean_id,
                    document_id,
                    enrich_response.status,
                    enrich_response.enriched_count,
                    enrich_response.skipped_count,
                    enrich_response.failed_count,
                )
                _emit_progress(
                    options,
                    "enrich",
                    "succeeded",
                    "Chunk enrichment completed.",
                    {
                        "document_id": str(document_id),
                        "status": enrich_response.status,
                        "enriched_count": enrich_response.enriched_count,
                        "skipped_count": enrich_response.skipped_count,
                        "failed_count": enrich_response.failed_count,
                    },
                )
            else:
                logger.info("Skipped DOffice enrichment id_vb=%s document=%s", clean_id, document_id)
                _emit_progress(
                    options,
                    "enrich",
                    "succeeded",
                    "Chunk enrichment skipped.",
                    {
                        "document_id": str(document_id),
                        "enabled": options.enable_enrichment,
                        "has_enrichment_service": self._enrichment_service is not None,
                    },
                )

            _emit_progress(
                options,
                "index",
                "running",
                "Indexing DOffice chunks into Qdrant and Elasticsearch keyword store if enabled.",
                {"document_id": str(document_id), "artifact_count": artifact_count},
            )
            index_response = await self._vector_indexing_service.index_document(
                document_id,
                use_enriched_content_for_embedding=options.enable_enrichment,
            )
            await self._update_raw_status(
                raw_record=raw_record,
                raw_record_id=raw_record_id,
                embedding_status="indexed",
                sync_status="indexed",
            )
            await self._attach_acl_from_source(
                document_id=document_id,
                source_document=source_document,
            )
            logger.info(
                "Indexed DOffice document id_vb=%s document=%s indexed_chunks=%s duration_ms=%s",
                clean_id,
                document_id,
                index_response.indexed_chunk_count,
                _duration_ms(started),
            )
            _emit_progress(
                options,
                "index",
                "succeeded",
                "Vector and keyword indexing completed.",
                {
                    "document_id": str(document_id),
                    "status": index_response.status,
                    "indexed_chunk_count": index_response.indexed_chunk_count,
                    "indexed_artifact_count": artifact_count,
                    "duration_ms": _duration_ms(started),
                },
            )
        except Exception as exc:
            logger.exception("DOffice ingest failed id_vb=%s document=%s", clean_id, document_id)
            raise DofficeIngestionError(f"Failed to ingest DOffice document: {exc}") from exc

        return DofficeIngestResponse(
            status="success",
            id_vb=source_document.id_vb,
            ky_hieu=source_document.ky_hieu,
            trich_yeu=source_document.trich_yeu,
            noi_ban_hanh=source_document.noi_ban_hanh,
            chunks_created=chunk_response.chunk_count,
            document_id=document_id,
            source_type=DOFFICE_SOURCE_TYPE,
            message="Đã lấy thông tin văn bản DOffice; pipeline phía sau đã convert, chunk, enrich nếu bật và index.",
        )

    async def _update_raw_status(
        self,
        *,
        raw_record: Any,
        raw_record_id: UUID | None,
        **statuses: str,
    ) -> None:
        updater_by_id = getattr(self._repository, "update_doffice_raw_status_by_id", None)
        if updater_by_id is not None and raw_record_id is not None:
            await updater_by_id(raw_record_id, **statuses)
            return
        await self._repository.update_doffice_raw_status(raw_record, **statuses)

    async def _attach_acl_from_source(self, *, document_id: Any, source_document: Any, write_document_index: bool = True) -> None:
        """Tính ACL từ 3 list DOffice (đơn vị/phòng ban/cá nhân) và gắn vào payload chunk.

        Mô hình ĐỘNG: chỉ ghi bộ quyền rút gọn ``acl_*`` lên point Qdrant + doc ES
        (không lưu list input, không lưu danh sách cá nhân dài). Người mới vào phòng/đơn vị
        tự được xem theo ``acl_allow_pb``/``acl_allow_dv``.
        """
        raw = getattr(source_document, "raw_source", None) or {}
        don_vi = raw.get("don_vi_list")
        phong_ban = raw.get("phong_ban_list")
        ca_nhan = raw.get("ca_nhan_list")
        if not (don_vi or phong_ban or ca_nhan) and settings.doffice_synthetic_acl_enabled:
            # API DOffice hiện chưa trả ACL -> dùng giá trị giả định để bộ quyền vẫn chạy.
            don_vi = settings.doffice_synthetic_don_vi_list
            phong_ban = settings.doffice_synthetic_phong_ban_list
            ca_nhan = settings.doffice_synthetic_ca_nhan_list
            logger.info("Văn bản %s thiếu ACL DOffice -> dùng ACL giả định (synthetic)", document_id)
        if not (don_vi or phong_ban or ca_nhan):
            logger.info("Văn bản %s không có thông tin phân quyền DOffice -> bỏ qua gắn ACL", document_id)
            return

        session = getattr(self._repository, "_session", None)
        if session is None:
            logger.warning("Không lấy được session để dựng danh mục -> bỏ qua gắn ACL")
            return

        catalog = await OrgCatalog.from_session(session)
        unit_tree = await UnitTree.from_session(session)
        acl, _assignment, warnings = resolve_doffice_and_compress(
            don_vi_list=don_vi,
            phong_ban_list=phong_ban,
            ca_nhan_list=ca_nhan,
            catalog=catalog,
            unit_tree=unit_tree,
        )
        for warning in warnings:
            logger.warning("ACL validate document=%s: %s", document_id, warning)

        payload = to_chunk_payload_flat(acl)  # dynamic + acl_subjects (flatten)
        await self._vector_store.set_acl_payload_for_document(document_id, payload)
        if self._keyword_index_store is not None:
            await self._keyword_index_store.set_acl_payload_for_document(document_id, payload)
        logger.info("Đã gắn ACL document=%s: %s", document_id, acl.to_dict())

        # Two-stage: ghi 1 record vào ES document index (Stage 1). Embed trich_yeu +
        # tom_tat nếu bật BBQ. Backward compat: chỉ chạy khi two-stage + có store.
        # ``write_document_index=False`` (nhánh 'cả 2 DB đều có') -> KHÔNG đè bản ghi
        # hbrag_documents_v1 do job sync sở hữu, chỉ gắn ACL cho chunk Qdrant/ES ở trên.
        if write_document_index and settings.two_stage_retrieval_enabled and self._document_index_store is not None:
            from app.services.security.security_acl_payload import acl_keys_from_acl

            trich_yeu = _optional_string(raw.get("trich_yeu"))
            tom_tat = _optional_string(raw.get("tom_tat"))
            embedding: list[float] | None = None
            if settings.two_stage_document_embedding_enabled and self._llm_gateway is not None:
                embed_text = " ".join(filter(None, [trich_yeu, tom_tat])).strip()
                if embed_text:
                    try:
                        embedding = await self._llm_gateway.embed_query(embed_text)
                    except Exception:
                        logger.warning(
                            "Embed document index thất bại document=%s -> bỏ qua vector",
                            document_id,
                            exc_info=True,
                        )
            await self._document_index_store.upsert_document(
                document_id=str(document_id),
                id_vb=_optional_string(raw.get("id_vb")),
                ky_hieu=_optional_string(raw.get("ky_hieu")),
                trich_yeu=trich_yeu,
                tom_tat=tom_tat,
                keywords=None,
                nam=raw.get("nam") if isinstance(raw.get("nam"), int) else None,
                ngay_vb=_optional_string(raw.get("ngay_vb")),
                acl_subjects=acl_keys_from_acl(acl),
                acl_deny_pb=sorted(acl.deny_department_ids),
                acl_deny_nv=sorted(acl.deny_user_ids),
                embedding=embedding,
            )
            logger.info(
                "Đã ghi document index document=%s (vector=%s)",
                document_id,
                embedding is not None,
            )

    async def _compile_and_index_artifacts(
        self,
        *,
        document: Any,
        options: DofficeIngestOptions,
    ) -> int:
        if self._knowledge_artifact_repository is None:
            _emit_progress(
                options,
                "compile_artifacts",
                "succeeded",
                "Knowledge artifact compilation skipped because no repository was configured.",
                {"document_id": str(document.id), "artifact_count": 0},
            )
            return 0

        _emit_progress(
            options,
            "compile_artifacts",
            "running",
            "Compiling DOffice knowledge artifacts before chunk indexing.",
            {"document_id": str(document.id)},
        )
        chunks = await self._repository.list_chunks_for_document(document.id)
        artifacts = KnowledgeArtifactCompiler().compile_document(document=document, chunks=chunks)
        created = await self._knowledge_artifact_repository.replace_for_document(document.id, artifacts)
        await self._knowledge_artifact_repository.commit()

        indexed_count = 0
        if self._artifact_indexing_service is not None:
            response = await self._artifact_indexing_service.index_document(document.id)
            indexed_count = response.indexed_artifact_count

        _emit_progress(
            options,
            "compile_artifacts",
            "succeeded",
            "DOffice knowledge artifacts compiled and indexed.",
            {
                "document_id": str(document.id),
                "artifact_count": len(created),
                "indexed_artifact_count": indexed_count,
            },
        )
        return indexed_count or len(created)

    async def _find_existing(self, id_vb: str) -> Any | None:
        finder = getattr(self._repository, "find_document_by_source_metadata", None)
        if finder is None:
            return None
        return await finder(source_type=DOFFICE_SOURCE_TYPE, id_vb=id_vb)

    async def _delete_existing(self, document: Any) -> None:
        logger.info(
            "Replacing existing DOffice document id_vb=%s document=%s",
            dict(getattr(document, "document_metadata", None) or {}).get("id_vb"),
            document.id,
        )
        await self._vector_store.delete_points_for_document(
            document.id,
            tenant_id=getattr(document, "organization_id", None),
        )
        if self._keyword_index_store is not None:
            try:
                await self._keyword_index_store.delete_points_for_document(
                    document.id,
                    tenant_id=getattr(document, "organization_id", None),
                )
            except Exception:
                logger.exception(
                    "Failed to delete existing Elasticsearch keyword docs for DOffice document=%s",
                    document.id,
                )
        await self._repository.delete_document(document)
        await self._repository.commit()

    async def _create_document(
        self,
        source_document: DofficeDocument,
        normalized: NormalizedDofficeDocument,
        *,
        uploaded_by_user_id: UUID | None,
        organization_id: UUID | None,
        knowledge_base_id: UUID | None,
        access: dict[str, Any] | None,
    ) -> Any:
        title = _source_name(source_document)
        document = await self._repository.create_document(
            title=title,
            source_type=DOFFICE_SOURCE_TYPE,
            status="uploaded",
            uploaded_by_user_id=uploaded_by_user_id,
            organization_id=organization_id,
            knowledge_base_id=knowledge_base_id,
            visibility="organization",
            access=access,
        )
        document.document_profile = "doffice_admin"
        await self._repository.update_document_parsed_content(
            document,
            parsed_text=normalized.clean_text,
            parsed_at=datetime.now(UTC),
            status="parsed",
        )
        await self._repository.update_document_metadata(
            document,
            self._document_metadata(source_document, normalized),
        )
        return document

    @staticmethod
    def _document_metadata(source_document: DofficeDocument, normalized: NormalizedDofficeDocument) -> dict[str, Any]:
        identifiers = _unique_strings(
            [
                source_document.id_vb,
                source_document.ky_hieu,
                _document_code_number(source_document.ky_hieu),
            ]
        )
        source_name = _source_name(source_document)
        metadata = {
            **normalized.metadata,
            "source_type": DOFFICE_SOURCE_TYPE,
            "source_name": source_name,
            "id_vb": source_document.id_vb,
            "ky_hieu": source_document.ky_hieu,
            "trich_yeu": source_document.trich_yeu,
            "id_dv_ban_hanh": source_document.id_dv_ban_hanh,
            "noi_ban_hanh": source_document.noi_ban_hanh,
            "nguoi_ky": source_document.nguoi_ky,
            "ten_file": source_document.ten_file,
            "duong_dan": source_document.duong_dan,
            "ngay_vb": source_document.ngay_vb,
            "ngay_tao": source_document.ngay_tao,
            "ngay_capnhat": source_document.ngay_capnhat,
            "nam": source_document.nam,
            "thang": source_document.thang,
            "tom_tat": source_document.tom_tat,
            "source_summary": normalized.summary_text,
            "noi_dung_raw": source_document.raw_noi_dung,
            "plain_text": normalized.plain_text,
            "markdown_text": normalized.markdown_text,
            "content_hash": normalized.content_hash,
            "metadata_hash": normalized.metadata_hash,
            "doc_code": source_document.ky_hieu,
            "doc_codes": normalized.metadata.get("doc_codes") or ([source_document.ky_hieu] if source_document.ky_hieu else []),
            "identifiers": _unique_strings([*identifiers, *list(normalized.metadata.get("identifiers") or [])]),
            "issuing_org": source_document.noi_ban_hanh,
            "issuer": source_document.noi_ban_hanh,
            "subject": source_document.trich_yeu,
            "parser": DOFFICE_SOURCE_TYPE,
            "parsed_elements": _normalized_elements_to_dicts(normalized),
            "normalized_elements": _normalized_elements_to_dicts(normalized),
            "normalized_tables": _normalized_tables_to_dicts(normalized),
            "normalized_table_rows": _normalized_table_rows_to_dicts(normalized),
            "parsed_metadata": {
                "parser_version": "doffice_es_v1",
                "source_type": DOFFICE_SOURCE_TYPE,
                "normalizer_version": "doffice_structured_v1",
                "table_count": len(normalized.tables),
                "element_count": len(normalized.elements),
            },
            "raw_source_metadata": {
                **dict(source_document.raw_source or _source_document_to_payload(source_document)),
            },
        }
        return {key: value for key, value in metadata.items() if value not in (None, "", [])}


def _emit_progress(
    options: DofficeIngestOptions,
    step: str,
    state: str,
    message: str,
    output: dict[str, Any] | None = None,
) -> None:
    callback = options.progress_callback
    if callback is None:
        return
    try:
        callback(step, state, message, output or {})
    except Exception:
        logger.exception("DOffice progress callback failed step=%s state=%s", step, state)


def _source_name(source_document: DofficeDocument) -> str:
    return (
        source_document.ten_file
        or source_document.ky_hieu
        or source_document.trich_yeu
        or f"DOffice {source_document.id_vb}"
    )


def _normalized_elements_to_dicts(normalized: NormalizedDofficeDocument) -> list[dict[str, Any]]:
    return [
        {
            "element_type": element.element_type,
            "text": element.text,
            "metadata": dict(element.metadata),
        }
        for element in normalized.elements
    ]


def _normalized_tables_to_dicts(normalized: NormalizedDofficeDocument) -> list[dict[str, Any]]:
    return [
        {
            "table_index": table.table_index,
            "headers": table.headers,
            "markdown": table.markdown,
            "text": table.text,
            "metadata": dict(table.metadata),
            "rows": [
                {
                    "row_index": row.row_index,
                    "values": row.values,
                    "metadata": dict(row.metadata),
                }
                for row in table.rows
            ],
        }
        for table in normalized.tables
    ]


def _normalized_table_rows_to_dicts(normalized: NormalizedDofficeDocument) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for table in normalized.tables:
        for row in table.rows:
            rows.append(
                {
                    "table_index": table.table_index,
                    "table_name": table.metadata.get("table_name"),
                    "section_title": table.metadata.get("section_title"),
                    "row_index": row.row_index,
                    "values": row.values,
                    "metadata": dict(row.metadata),
                }
            )
    return rows


def _source_document_to_payload(source_document: DofficeDocument) -> dict[str, Any]:
    return {
        "id_vb": source_document.id_vb,
        "ky_hieu": source_document.ky_hieu,
        "trich_yeu": source_document.trich_yeu,
        "id_dv_ban_hanh": source_document.id_dv_ban_hanh,
        "noi_ban_hanh": source_document.noi_ban_hanh,
        "nguoi_ky": source_document.nguoi_ky,
        "ten_file": source_document.ten_file,
        "duong_dan": source_document.duong_dan,
        "ngay_vb": source_document.ngay_vb,
        "ngay_tao": source_document.ngay_tao,
        "ngay_capnhat": source_document.ngay_capnhat,
        "nam": source_document.nam,
        "thang": source_document.thang,
        "tom_tat": source_document.tom_tat,
        "noi_dung": source_document.raw_noi_dung,
    }


def _document_code_number(value: str | None) -> str | None:
    if not value or "/" not in value:
        return None
    number = value.split("/", 1)[0].strip()
    return number or None


def _unique_strings(values: list[Any]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for value in values:
        clean = " ".join(str(value or "").split()).strip()
        if not clean:
            continue
        key = clean.casefold()
        if key in seen:
            continue
        seen.add(key)
        unique.append(clean)
    return unique


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    clean = " ".join(str(value).split()).strip()
    return clean or None


def _duration_ms(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)
