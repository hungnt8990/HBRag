from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from app.repositories.documents import DocumentRepository
from app.schemas.documents import DofficeIngestResponse
from app.services.chunk_enrichment_service import ChunkEnrichmentService
from app.services.chunking_service import ChunkingService
from app.services.document_sources import (
    DOFFICE_SOURCE_TYPE,
    DofficeDocument,
    DofficeElasticsearchSource,
)
from app.services.doffice_content_normalizer import (
    NormalizedDofficeDocument,
    normalize_doffice_source,
)
from app.services.elasticsearch_keyword_search import ElasticsearchKeywordStore
from app.services.vector_indexing_service import VectorIndexingService
from app.services.vector_store import QdrantVectorStore

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
    ) -> None:
        self._repository = repository
        self._source = source
        self._chunking_service = chunking_service
        self._vector_indexing_service = vector_indexing_service
        self._vector_store = vector_store
        self._enrichment_service = enrichment_service
        self._keyword_index_store = keyword_index_store

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
        if existing is not None and not options.force_refresh:
            chunk_count = await self._repository.count_chunks_for_document(existing.id)
            metadata = dict(getattr(existing, "document_metadata", None) or {})
            _emit_progress(
                options,
                "parse",
                "succeeded",
                "DOffice document already exists; skipping fetch because force_refresh=false.",
                {"document_id": str(existing.id), "id_vb": clean_id, "chunk_count": chunk_count},
            )
            _emit_progress(
                options,
                "chunk",
                "succeeded",
                "Existing chunk set reused.",
                {"chunk_count": chunk_count},
            )
            _emit_progress(
                options,
                "index",
                "succeeded",
                "Existing vector/keyword index reused.",
                {"document_id": str(existing.id)},
            )
            logger.info(
                "Skipped DOffice ingest id_vb=%s document=%s chunks=%s reason=already_ingested duration_ms=%s",
                clean_id,
                existing.id,
                chunk_count,
                _duration_ms(started),
            )
            return DofficeIngestResponse(
                status="skipped",
                id_vb=clean_id,
                ky_hieu=_optional_string(metadata.get("ky_hieu")),
                trich_yeu=_optional_string(metadata.get("trich_yeu")),
                noi_ban_hanh=_optional_string(metadata.get("noi_ban_hanh")),
                chunks_created=chunk_count,
                document_id=existing.id,
                source_type=DOFFICE_SOURCE_TYPE,
                message="Văn bản đã có trong hệ thống. Bật Force refresh nếu cần lấy và xử lý lại.",
            )

        if existing is not None:
            _emit_progress(
                options,
                "parse",
                "running",
                "Force refresh enabled; deleting previous DOffice document before re-ingest.",
                {"document_id": str(existing.id), "id_vb": clean_id},
            )
            await self._delete_existing(existing)

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
        await self._repository.update_doffice_raw_status(raw_record, parse_status="normalized", clean_status="cleaned")
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
        _emit_progress(
            options,
            "parse",
            "succeeded",
            "DOffice document created in PostgreSQL with parsed content.",
            {"document_id": str(document.id), "id_vb": clean_id},
        )

        try:
            _emit_progress(
                options,
                "chunk",
                "running",
                "Chunking DOffice document.",
                {"document_id": str(document.id), "profile": "auto"},
            )
            chunk_response = await self._chunking_service.chunk_document(document.id, profile="auto")
            logger.info(
                "Chunked DOffice document id_vb=%s document=%s chunks=%s",
                clean_id,
                document.id,
                chunk_response.chunk_count,
            )
            await self._repository.update_doffice_raw_status(raw_record, chunk_status="chunked")
            _emit_progress(
                options,
                "chunk",
                "succeeded",
                "DOffice document chunking completed.",
                {"document_id": str(document.id), "chunk_count": chunk_response.chunk_count},
            )

            if options.enable_enrichment and self._enrichment_service is not None:
                _emit_progress(
                    options,
                    "enrich",
                    "running",
                    "Running chunk enrichment for DOffice document.",
                    {"document_id": str(document.id)},
                )
                enrich_response = await self._enrichment_service.enrich_document(
                    document.id,
                    enabled=True,
                    update_keyword_search_vector=True,
                )
                logger.info(
                    "Enriched DOffice document id_vb=%s document=%s status=%s enriched=%s skipped=%s failed=%s",
                    clean_id,
                    document.id,
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
                        "document_id": str(document.id),
                        "status": enrich_response.status,
                        "enriched_count": enrich_response.enriched_count,
                        "skipped_count": enrich_response.skipped_count,
                        "failed_count": enrich_response.failed_count,
                    },
                )
            else:
                logger.info("Skipped DOffice enrichment id_vb=%s document=%s", clean_id, document.id)
                _emit_progress(
                    options,
                    "enrich",
                    "succeeded",
                    "Chunk enrichment skipped.",
                    {
                        "document_id": str(document.id),
                        "enabled": options.enable_enrichment,
                        "has_enrichment_service": self._enrichment_service is not None,
                    },
                )

            _emit_progress(
                options,
                "index",
                "running",
                "Indexing DOffice chunks into Qdrant and Elasticsearch keyword store if enabled.",
                {"document_id": str(document.id)},
            )
            index_response = await self._vector_indexing_service.index_document(
                document.id,
                use_enriched_content_for_embedding=options.enable_enrichment,
            )
            await self._repository.update_doffice_raw_status(raw_record, embedding_status="indexed", sync_status="indexed")
            logger.info(
                "Indexed DOffice document id_vb=%s document=%s indexed_chunks=%s duration_ms=%s",
                clean_id,
                document.id,
                index_response.indexed_chunk_count,
                _duration_ms(started),
            )
            _emit_progress(
                options,
                "index",
                "succeeded",
                "Vector and keyword indexing completed.",
                {
                    "document_id": str(document.id),
                    "status": index_response.status,
                    "indexed_chunk_count": index_response.indexed_chunk_count,
                    "duration_ms": _duration_ms(started),
                },
            )
        except Exception as exc:
            logger.exception("DOffice ingest failed id_vb=%s document=%s", clean_id, document.id)
            raise DofficeIngestionError(f"Failed to ingest DOffice document: {exc}") from exc

        return DofficeIngestResponse(
            status="success",
            id_vb=source_document.id_vb,
            ky_hieu=source_document.ky_hieu,
            trich_yeu=source_document.trich_yeu,
            noi_ban_hanh=source_document.noi_ban_hanh,
            chunks_created=chunk_response.chunk_count,
            document_id=document.id,
            source_type=DOFFICE_SOURCE_TYPE,
            message="Đã lấy thông tin văn bản DOffice; pipeline phía sau đã convert, chunk, enrich nếu bật và index.",
        )

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
