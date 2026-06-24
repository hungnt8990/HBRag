from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

from app.core.config import settings
from app.repositories.documents import ChunkCreate, DocumentRepository
from app.schemas.documents import ChunkPreview, DocumentChunkResponse
from app.services.chunkers.chunker_gis_chunking import STRUCTURED_NO_OVERLAP_CHUNK_TYPES
from app.services.chunkers.chunker_heading_rule_engine import DetectedHeading, detect_headings, heading_rules_from_config
from app.services.rag.rag_chunk import rag_chunk_from_record, should_index_chunk
from app.services.documents.document_storage import StorageClient

logger = logging.getLogger(__name__)

DEFAULT_CHUNK_SIZE = settings.default_chunk_size
DEFAULT_CHUNK_OVERLAP = settings.default_chunk_overlap
CHUNK_PREVIEW_LIMIT = 2
MIN_SPLIT_RATIO = 0.85
DOCUMENT_PROFILE_DB_MAX_LENGTH = 32
DOCUMENT_PROFILE_ALIASES = {
    "mixed_administrative_technical": "mixed_admin_tech",
    "mixed_administrative_technical_with_relationships": "mixed_admin_tech_rel",
}

ChunkMode = Literal[
    "recursive",
    "legal_article",
    "table_aware",
    "hybrid_structured",
    "slide_page",
    "heading_aware",
    "docling_router",
    "docling_v6",
]
DEFAULT_CHUNK_MODE: ChunkMode = "recursive"

LEGAL_SEPARATORS = ("\n\n", "\n", ". ")
DEFAULT_TEXT_SEPARATORS = ("\n\n\n", "\n\n", "\n- ", "\n+ ", ". ", "\n", " ", "")
GIS_SCHEMA_TABLE_PATTERN = re.compile(
    r"(?ms)(\(\d+\)\s+F\d+_[A-Za-z0-9_]+\s*[â€“-].*?)(?=^\s*\(\d+\)\s+F\d+_[A-Za-z0-9_]+|\Z)"
)
GIS_LAYER_PATTERN = re.compile(r"\bF\d+_[A-Za-z0-9_]+\b")


@dataclass(frozen=True)
class TextChunk:
    content: str
    start_char: int
    end_char: int
    metadata: dict[str, Any] = field(default_factory=dict)


def split_tables_and_text(raw_text: str) -> list[dict[str, Any]]:
    segments: list[dict[str, Any]] = []
    last_end = 0

    for match in GIS_SCHEMA_TABLE_PATTERN.finditer(raw_text):
        if match.start() > last_end:
            segments.append(
                {
                    "type": "text",
                    "content": raw_text[last_end : match.start()],
                    "start_char": last_end,
                    "end_char": match.start(),
                }
            )
        table_text, trailing_text = _split_gis_table_tail(match.group(0))
        table_end = match.start() + len(table_text)
        segments.append(
            {
                "type": "table",
                "content": table_text,
                "start_char": match.start(),
                "end_char": table_end,
                "layer_id": extract_layer_id(table_text),
            }
        )
        if trailing_text.strip():
            segments.append(
                {
                    "type": "text",
                    "content": trailing_text,
                    "start_char": table_end,
                    "end_char": match.end(),
                }
            )
        last_end = match.end()

    if last_end < len(raw_text):
        segments.append(
            {
                "type": "text",
                "content": raw_text[last_end:],
                "start_char": last_end,
                "end_char": len(raw_text),
            }
        )

    return segments or [
        {"type": "text", "content": raw_text, "start_char": 0, "end_char": len(raw_text)}
    ]


def extract_layer_id(table_text: str) -> str:
    match = GIS_LAYER_PATTERN.search(table_text)
    return match.group(0) if match else "unknown"


def _split_gis_table_tail(table_text: str) -> tuple[str, str]:
    separator_index = table_text.rfind("\n\n")
    if separator_index < 0:
        return table_text, ""

    protected = table_text[: separator_index + 1]
    tail = table_text[separator_index + 1 :]
    first_tail_line = next((line.strip() for line in tail.splitlines() if line.strip()), "")
    if not first_tail_line:
        return table_text, ""
    if GIS_SCHEMA_TABLE_PATTERN.match(first_tail_line) or "|" in first_tail_line:
        return table_text, ""
    return protected, tail


class RecursiveTextChunker:
    default_separators = DEFAULT_TEXT_SEPARATORS

    def __init__(
        self,
        *,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
        separators: tuple[str, ...] | None = None,
    ) -> None:
        if chunk_size <= 0:
            raise ValueError("chunk_size must be greater than 0.")
        if chunk_overlap < 0:
            raise ValueError("chunk_overlap cannot be negative.")
        if chunk_overlap >= chunk_size:
            raise ValueError("chunk_overlap must be smaller than chunk_size.")

        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.separators = separators or self.default_separators

    def chunk_text(self, text: str) -> list[TextChunk]:
        if not text.strip():
            return []

        chunks: list[TextChunk] = []
        for segment in split_tables_and_text(text):
            content = str(segment["content"])
            if not content.strip():
                continue
            if segment["type"] == "table":
                chunks.append(
                    TextChunk(
                        content=content,
                        start_char=int(segment["start_char"]),
                        end_char=int(segment["end_char"]),
                        metadata={
                            "chunk_type": "gis_table",
                            "type": "gis_table",
                            "layer_id": segment["layer_id"],
                        },
                    )
                )
                continue
            chunks.extend(self._chunk_plain_text(content, base_offset=int(segment["start_char"])))
        return chunks

    def _chunk_plain_text(self, text: str, *, base_offset: int = 0) -> list[TextChunk]:
        if not text.strip():
            return []

        chunks: list[TextChunk] = []
        text_length = len(text)
        start_char = 0

        while start_char < text_length:
            target_end = min(start_char + self.chunk_size, text_length)
            end_char = self._find_split_boundary(text, start_char, target_end)
            if end_char <= start_char:
                end_char = target_end

            chunks.append(
                TextChunk(
                    content=text[start_char:end_char],
                    start_char=base_offset + start_char,
                    end_char=base_offset + end_char,
                )
            )

            if end_char >= text_length:
                break

            start_char = max(end_char - self.chunk_overlap, start_char + 1)

        return chunks

    def _find_split_boundary(self, text: str, start_char: int, target_end: int) -> int:
        if target_end >= len(text):
            return len(text)

        window = text[start_char:target_end]
        min_split_index = max(1, int(len(window) * MIN_SPLIT_RATIO))
        for separator in self.separators:
            index = window.rfind(separator)
            if index >= min_split_index:
                return start_char + index + len(separator)

        return target_end


class LegalArticleChunker:
    """Structure-aware chunker driven by configurable heading rules.

    The chunker does not know language-specific labels such as legal article or
    chapter names. Boundary headings and parent-heading metadata are supplied
    by an ingestion profile so admins can edit them from RAG Config.
    """

    def __init__(
        self,
        *,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
        heading_rules: list[dict[str, Any]] | None = None,
    ) -> None:
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        if heading_rules is None:
            from app.services.ingestion.ingestion_profiles import get_profile_config

            heading_rules = list(get_profile_config("legal_admin").get("heading_rules") or [])
        self.heading_rules = heading_rules_from_config({"heading_rules": heading_rules})
        # Use line-aware separators to avoid splitting inside table rows.
        self._inner_chunker = RecursiveTextChunker(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            separators=LEGAL_SEPARATORS,
        )

    def chunk_text(self, text: str) -> list[TextChunk]:
        if not text.strip():
            return []

        headings = detect_headings(text, self.heading_rules)
        boundary_headings = [heading for heading in headings if heading.boundary]
        results: list[TextChunk] = []

        if not boundary_headings:
            return self._inner_chunker.chunk_text(text)

        first_boundary_start = boundary_headings[0].start
        if first_boundary_start > 0:
            preamble = text[:first_boundary_start]
            parent_metadata = self._latest_parent_heading_metadata(
                headings,
                position=first_boundary_start,
                boundary_level=boundary_headings[0].level,
            )
            results.extend(
                self._emit_chunks(
                    body=preamble,
                    body_offset=0,
                    base_metadata=parent_metadata,
                )
            )

        for index, heading in enumerate(boundary_headings):
            start = heading.start
            end = (
                boundary_headings[index + 1].start
                if index + 1 < len(boundary_headings)
                else len(text)
            )
            section_text = text[start:end]
            base_metadata = {
                **self._latest_parent_heading_metadata(
                    headings,
                    position=start,
                    boundary_level=heading.level,
                ),
                **self._heading_metadata(heading),
            }

            if len(section_text.strip()) <= self.chunk_size:
                results.append(
                    TextChunk(
                        content=section_text.strip(),
                        start_char=start,
                        end_char=end,
                        metadata={**base_metadata, "subchunk_index": 0},
                    )
                )
                continue

            results.extend(
                self._emit_chunks(
                    body=section_text,
                    body_offset=start,
                    base_metadata=base_metadata,
                    track_subchunks=True,
                )
            )

        return results

    def _emit_chunks(
        self,
        *,
        body: str,
        body_offset: int,
        base_metadata: dict[str, Any],
        track_subchunks: bool = False,
    ) -> list[TextChunk]:
        sub_chunks = self._inner_chunker.chunk_text(body)
        emitted: list[TextChunk] = []
        for index, sub in enumerate(sub_chunks):
            metadata = dict(base_metadata)
            if track_subchunks:
                metadata["subchunk_index"] = index
            emitted.append(
                TextChunk(
                    content=sub.content,
                    start_char=body_offset + sub.start_char,
                    end_char=body_offset + sub.end_char,
                    metadata=metadata,
                )
            )
        return emitted

    @staticmethod
    def _heading_metadata(heading: DetectedHeading) -> dict[str, Any]:
        metadata: dict[str, Any] = {
            "section_title": heading.display_text,
            "heading_label": heading.label,
            "heading_number": heading.number,
            "heading_title": heading.title,
            "heading_level": heading.level,
            "heading_name": heading.name,
        }
        if heading.metadata_key:
            metadata[heading.metadata_key] = (
                heading.display_text
                if heading.metadata_value == "display_text"
                else heading.title or heading.display_text
            )
        if heading.number_metadata_key:
            metadata[heading.number_metadata_key] = heading.number
        return metadata

    @classmethod
    def _latest_parent_heading_metadata(
        cls,
        headings: list[DetectedHeading],
        *,
        position: int,
        boundary_level: int,
    ) -> dict[str, Any]:
        latest: DetectedHeading | None = None
        for heading in headings:
            if heading.start >= position:
                break
            if heading.level < boundary_level:
                latest = heading
        return cls._heading_metadata(latest) if latest is not None else {}


class DocumentChunkingError(RuntimeError):
    pass


class DocumentNotFoundError(LookupError):
    pass


class DocumentChunkStatusError(ValueError):
    pass


class EmptyParsedTextError(ValueError):
    pass


def document_profile_column_value(profile: str | None) -> str:
    """Return a stable value that fits documents.document_profile VARCHAR(32)."""

    clean = re.sub(r"\s+", "_", str(profile or "general").strip()) or "general"
    alias = DOCUMENT_PROFILE_ALIASES.get(clean)
    if alias:
        return alias
    if len(clean) <= DOCUMENT_PROFILE_DB_MAX_LENGTH:
        return clean

    digest = hashlib.sha1(clean.encode("utf-8")).hexdigest()[:8]
    prefix_length = DOCUMENT_PROFILE_DB_MAX_LENGTH - len(digest) - 1
    return f"{clean[:prefix_length].rstrip('_')}_{digest}"


class ChunkingService:
    def __init__(
        self,
        *,
        repository: DocumentRepository,
        chunker: RecursiveTextChunker | None = None,
        storage: StorageClient | None = None,
    ) -> None:
        self._repository = repository
        self._chunker = chunker or RecursiveTextChunker()
        self._storage = storage

    async def chunk_document(
        self,
        document_id: UUID,
        *,
        chunk_size: int | None = None,
        chunk_overlap: int | None = None,
        chunk_mode: ChunkMode | None = None,
        profile: str | None = None,
    ) -> DocumentChunkResponse:
        document = await self._repository.get_document(document_id)
        if document is None:
            raise DocumentNotFoundError("Document not found.")
        if document.status not in {"parsed", "chunked"}:
            raise DocumentChunkStatusError("Only parsed or chunked documents can be chunked.")
        if not document.parsed_text or not document.parsed_text.strip():
            raise EmptyParsedTextError("Document has no parsed text to chunk.")

        document_metadata = dict(getattr(document, "document_metadata", None) or {})
        if document_metadata.get("source_type") == "doffice_elasticsearch" and document_metadata.get("normalized_elements"):
            return await self._chunk_doffice_document(document=document, document_metadata=document_metadata)

        document_file = await self._get_primary_document_file(document.id)

        if self._can_use_docling_router(
            document_metadata=document_metadata,
            requested_chunk_mode=chunk_mode,
        ):
            return await self._chunk_document_with_docling_router(
                document=document,
                document_file=document_file,
                document_metadata=document_metadata,
                chunk_size=chunk_size,
            )

        if chunk_mode in {"docling_router", "docling_v6"}:
            raise DocumentChunkingError(
                "Docling router chunking requires a document parsed by Docling and a "
                "persisted DoclingDocument artifact."
            )

        # Non-Docling documents are dispatched directly by the service. This keeps
        # backward-compatible chunk modes and document profiles without restoring a
        # separate ChunkingRouter abstraction.
        from app.services.documents.document_profiles import profile_config, resolve_profile
        from app.services.parsers.parser_base import parsed_element_from_dict

        requested_profile = profile or getattr(document, "document_profile", None)
        effective_profile = resolve_profile(requested_profile, text=document.parsed_text)
        config = profile_config(effective_profile)
        mode: ChunkMode = chunk_mode or config["chunk_mode"]
        resolved_size = chunk_size or int(config["chunk_size"])
        resolved_overlap = (
            chunk_overlap
            if chunk_overlap is not None
            else int(config["chunk_overlap"])
        )

        raw_elements = list(document_metadata.get("parsed_elements") or [])
        parsed_elements = [
            parsed_element_from_dict(element)
            for element in raw_elements
            if isinstance(element, dict)
        ]

        # Structured parsed elements should remain structured by default. This is a
        # direct content policy, not a strategy router.
        if chunk_mode is None and self._has_table_elements(parsed_elements):
            mode = "hybrid_structured"

        raw_chunks: list[dict[str, Any]]
        chunk_strategy = mode
        if mode == "legal_article":
            configured_heading_rules = list(config.get("heading_rules") or [])
            chunker = LegalArticleChunker(
                chunk_size=resolved_size,
                chunk_overlap=resolved_overlap,
                heading_rules=configured_heading_rules or None,
            )
            raw_chunks = self._raw_chunks_from_text_chunks(
                chunker.chunk_text(document.parsed_text)
            )
        elif mode == "slide_page":
            raw_chunks = self._chunks_from_slide_elements(parsed_elements)
            if not raw_chunks:
                fallback = RecursiveTextChunker(
                    chunk_size=resolved_size,
                    chunk_overlap=resolved_overlap,
                )
                raw_chunks = self._raw_chunks_from_text_chunks(
                    fallback.chunk_text(document.parsed_text)
                )
                chunk_strategy = "recursive"
        elif mode == "heading_aware":
            raw_chunks = self._chunks_from_prose_elements(
                parsed_elements,
                document.parsed_text,
                chunk_size=resolved_size,
                chunk_overlap=resolved_overlap,
            )
            if not raw_chunks:
                fallback = RecursiveTextChunker(
                    chunk_size=resolved_size,
                    chunk_overlap=resolved_overlap,
                )
                raw_chunks = self._raw_chunks_from_text_chunks(
                    fallback.chunk_text(document.parsed_text)
                )
                chunk_strategy = "recursive"
        elif mode in {"hybrid_structured", "table_aware"}:
            raw_chunks = self._chunks_from_hybrid_elements(
                parsed_elements,
                document.parsed_text,
                chunk_size=resolved_size,
                chunk_overlap=resolved_overlap,
            )
            if not raw_chunks:
                fallback = RecursiveTextChunker(
                    chunk_size=resolved_size,
                    chunk_overlap=resolved_overlap,
                )
                raw_chunks = self._raw_chunks_from_text_chunks(
                    fallback.chunk_text(document.parsed_text)
                )
                chunk_strategy = "recursive"
        else:
            fallback = RecursiveTextChunker(
                chunk_size=resolved_size,
                chunk_overlap=resolved_overlap,
            )
            raw_chunks = self._raw_chunks_from_text_chunks(
                fallback.chunk_text(document.parsed_text)
            )
            chunk_strategy = "recursive"
            mode = "recursive"

        source_file = (
            getattr(document_file, "filename", None)
            or document_metadata.get("source_name")
            or document_metadata.get("ten_file")
            or getattr(document, "title", None)
        )
        compatibility_metadata = {
            "chunk_strategy": chunk_strategy,
            "router_reason": "document_profile_default",
            "parser": document_metadata.get("parser"),
            "source_file": source_file,
            **self._document_metadata_for_chunks(document_metadata),
        }
        chunk_records: list[ChunkCreate] = []
        preview_chunks: list[TextChunk] = []
        from app.services.ingestion.ingestion_profiles import apply_chunk_metadata_rules

        for index, raw_chunk in enumerate(raw_chunks):
            content = str(raw_chunk.get("content") or "")
            metadata = dict(raw_chunk.get("metadata") or {})
            text_chunk = TextChunk(
                content=content,
                start_char=int(metadata.get("start_char") or 0),
                end_char=int(metadata.get("end_char") or len(content)),
                metadata=metadata,
            )
            chunk_metadata = self._build_metadata(
                chunk_size=resolved_size,
                chunk_overlap=resolved_overlap,
                chunk_mode=mode,
                profile=effective_profile,
                text_chunk=text_chunk,
                extra_metadata=compatibility_metadata,
            )
            chunk_metadata = apply_chunk_metadata_rules(
                chunk_metadata,
                content=content,
                config=config,
            )
            chunk_records.append(
                ChunkCreate(
                    chunk_index=index,
                    content=content,
                    metadata=chunk_metadata,
                )
            )
            if len(preview_chunks) < CHUNK_PREVIEW_LIMIT:
                preview_chunks.append(text_chunk)

        try:
            document.document_profile = document_profile_column_value(effective_profile)
            await self._repository.delete_chunks_for_document(document.id)
            await self._repository.create_chunks(
                document_id=document.id,
                chunks=chunk_records,
            )
            await self._repository.update_document_status(document, "chunked")
            await self._repository.commit()
        except Exception as exc:
            await self._repository.rollback()
            raise DocumentChunkingError("Failed to chunk document.") from exc

        return DocumentChunkResponse(
            document_id=document.id,
            status=document.status,
            chunk_count=len(chunk_records),
            preview=[
                ChunkPreview(
                    chunk_index=index,
                    content=text_chunk.content,
                    start_char=text_chunk.start_char,
                    end_char=text_chunk.end_char,
                )
                for index, text_chunk in enumerate(preview_chunks)
            ],
        )

    async def _chunk_doffice_document(self, *, document: Any, document_metadata: dict[str, Any]) -> DocumentChunkResponse:
        from app.services.chunkers.chunker_doffice_chunking import build_doffice_chunks
        from app.services.ingestion.ingestion_doffice_content_normalizer import NormalizedDofficeDocument, NormalizedElement, NormalizedTable, NormalizedTableRow

        elements = [
            NormalizedElement(
                element_type=str(item.get("element_type") or item.get("chunk_type") or "paragraph"),
                text=str(item.get("text") or ""),
                metadata=dict(item.get("metadata") or {}),
            )
            for item in document_metadata.get("normalized_elements", [])
            if isinstance(item, dict)
        ]
        tables = [
            NormalizedTable(
                table_index=int(item.get("table_index") or 0),
                headers=list(item.get("headers") or []),
                rows=[
                    NormalizedTableRow(
                        row_index=int(row.get("row_index") or 0),
                        values=list(row.get("values") or []),
                        metadata=dict(row.get("metadata") or {}),
                    )
                    for row in item.get("rows", [])
                    if isinstance(row, dict)
                ],
                markdown=str(item.get("markdown") or ""),
                text=str(item.get("text") or ""),
                metadata=dict(item.get("metadata") or {}),
            )
            for item in document_metadata.get("normalized_tables", [])
            if isinstance(item, dict)
        ]
        normalized = NormalizedDofficeDocument(
            id_vb=str(document_metadata.get("id_vb") or ""),
            document_code=document_metadata.get("document_code") or document_metadata.get("ky_hieu"),
            title=document_metadata.get("trich_yeu"),
            issued_date=document_metadata.get("issued_date"),
            issuer=document_metadata.get("issuer") or document_metadata.get("noi_ban_hanh"),
            signer=document_metadata.get("signer") or document_metadata.get("nguoi_ky"),
            raw_text=str(document_metadata.get("noi_dung_raw") or ""),
            clean_text=document.parsed_text or "",
            markdown_text=str(document_metadata.get("markdown_text") or ""),
            plain_text=str(document_metadata.get("plain_text") or document.parsed_text or ""),
            summary_text=document_metadata.get("source_summary"),
            elements=elements,
            tables=tables,
            metadata=document_metadata,
            content_hash=str(document_metadata.get("content_hash") or ""),
            metadata_hash=str(document_metadata.get("metadata_hash") or ""),
        )
        chunk_records = build_doffice_chunks(normalized)
        preview_chunks = [
            TextChunk(
                content=chunk.content,
                start_char=0,
                end_char=len(chunk.content),
                metadata=chunk.metadata,
            )
            for chunk in chunk_records[:CHUNK_PREVIEW_LIMIT]
        ]

        try:
            document.document_profile = "doffice_admin"
            await self._repository.delete_chunks_for_document(document.id)
            await self._repository.create_chunks(document_id=document.id, chunks=chunk_records)
            await self._repository.update_document_status(document, "chunked")
            await self._repository.commit()
        except Exception as exc:
            await self._repository.rollback()
            raise DocumentChunkingError("Failed to chunk DOffice document.") from exc

        return DocumentChunkResponse(
            document_id=document.id,
            status=document.status,
            chunk_count=len(chunk_records),
            preview=[
                ChunkPreview(
                    chunk_index=index,
                    content=text_chunk.content,
                    start_char=text_chunk.start_char,
                    end_char=text_chunk.end_char,
                )
                for index, text_chunk in enumerate(preview_chunks)
            ],
        )

    def _can_use_docling_router(
        self,
        *,
        document_metadata: dict[str, Any],
        requested_chunk_mode: ChunkMode | None,
    ) -> bool:
        if not settings.enable_docling_v6_chunking or self._storage is None:
            return False
        if requested_chunk_mode not in {None, "docling_router", "docling_v6"}:
            return False
        parsed_metadata = dict(document_metadata.get("parsed_metadata") or {})
        artifact_paths = dict(parsed_metadata.get("artifact_paths") or {})
        return bool(
            document_metadata.get("parser") == "docling"
            and artifact_paths.get("docling_json")
        )

    async def _chunk_document_with_docling_router(
        self,
        *,
        document: Any,
        document_file: Any,
        document_metadata: dict[str, Any],
        chunk_size: int | None,
    ) -> DocumentChunkResponse:
        from docling_core.types.doc import DoclingDocument

        from app.services.chunkers.chunker_docling_router import route_docling_chunks
        from app.services.chunkers.chunker_docling_generic_chunking import (
            DoclingV6ChunkingResult,
            RegexVietnameseTokenizer,
            build_quality_report,
            chunk_docling_document,
            enforce_token_limit,
            reindex_records,
        )

        parsed_metadata = dict(document_metadata.get("parsed_metadata") or {})
        artifact_paths = dict(parsed_metadata.get("artifact_paths") or {})
        docling_path = str(artifact_paths["docling_json"])
        raw_document = await self._storage.get_file(object_name=docling_path)
        import tempfile

        temp_path: str | None = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".docling.json", delete=False) as temp_file:
                temp_file.write(raw_document)
                temp_path = temp_file.name
            doc = DoclingDocument.load_from_json(Path(temp_path))
        finally:
            if temp_path is not None:
                Path(temp_path).unlink(missing_ok=True)

        max_tokens = chunk_size or settings.docling_chunk_max_tokens
        stored_page_texts = dict(parsed_metadata.get("page_texts") or {})
        page_texts = {
            int(page_no): str(text)
            for page_no, text in stored_page_texts.items()
            if str(page_no).isdigit()
        }
        result = chunk_docling_document(
            doc,
            source_file=str(getattr(document_file, "filename", "document")),
            max_tokens=max_tokens,
            context_budget=min(settings.docling_context_budget, max_tokens - 1),
            document_context_mode=settings.docling_context_mode,
            page_texts=page_texts,
        )

        route = route_docling_chunks(
            generic_records=result.records,
            page_texts=page_texts,
            source_file=str(getattr(document_file, "filename", "document")),
            max_tokens=max_tokens,
            parsed_text=str(getattr(document, "parsed_text", "") or ""),
        )
        from app.services.ingestion.ingestion_profiles import (
            apply_chunk_metadata_rules,
            get_profile_config,
        )

        route_config = get_profile_config(route.document_profile)
        routed_records = [
            apply_chunk_metadata_rules(
                record,
                content=str(
                    record.get("contextualized_text")
                    or record.get("text")
                    or record.get("content")
                    or ""
                ),
                config=route_config,
            )
            for record in route.records
        ]
        result = DoclingV6ChunkingResult(
            records=routed_records,
            quality=result.quality,
            coverage=result.coverage,
            document_context=result.document_context,
        )

        # Defensive service-level token guard. This deliberately runs after the
        # Docling pipeline returns and immediately before strict quality checks,
        # so a stale quality report or a late record mutation cannot reject the
        # document with records that have not been split.
        token_counter = RegexVietnameseTokenizer(max_tokens=max_tokens)
        guarded_records = enforce_token_limit(
            result.records,
            token_counter,
            max_tokens,
        )
        guarded_records = reindex_records(guarded_records, token_counter)
        guarded_quality = build_quality_report(
            guarded_records,
            token_counter,
            max_tokens,
        )
        result = DoclingV6ChunkingResult(
            records=guarded_records,
            quality=guarded_quality,
            coverage=result.coverage,
            document_context=result.document_context,
        )

        oversized_after_guard = [
            {
                "chunk_id": record.get("chunk_id"),
                "tokens": token_counter.count_tokens(
                    str(record.get("contextualized_text") or "")
                ),
            }
            for record in result.records
            if token_counter.count_tokens(
                str(record.get("contextualized_text") or "")
            )
            > max_tokens
        ]
        if oversized_after_guard:
            raise RuntimeError(
                "Service-level token guard failed: "
                f"{oversized_after_guard}"
            )

        if settings.docling_strict_quality and result.quality.get("critical_count", 0):
            critical_issues = result.quality.get("critical", [])

            logger.error(
                "Docling router quality gate rejected document",
                extra={
                    "critical_count": len(critical_issues),
                    "critical_issues": critical_issues,
                },
            )

            raise RuntimeError(
                "Docling router quality gate rejected the document: "
                f"{len(critical_issues)} critical issue(s): "
                f"{critical_issues}"
            )

        source_file = str(getattr(document_file, "filename", "document"))
        source_uri = str(getattr(document_file, "storage_path", "")) or None
        document_version = str(document_metadata.get("document_version") or "v1")
        parser_version = parsed_metadata.get("parser_version")
        quality_status = str(result.quality.get("status") or "pass")
        rag_chunks = [
            rag_chunk_from_record(
                {
                    **record,
                    "quality_status": record.get("quality_status", quality_status),
                },
                document_id=document.id,
                source_file=source_file,
                source_uri=source_uri,
                document_title=getattr(document, "title", None),
                document_version=document_version,
                tenant_id=getattr(document, "organization_id", None),
                parser="docling",
                parser_version=str(parser_version) if parser_version else None,
                chunker="docling_router_v1",
                chunker_version="1",
                chunk_index=index,
            )
            for index, record in enumerate(result.records)
        ]
        chunk_records = [
            ChunkCreate(
                chunk_index=index,
                content=rag_chunk.text,
                token_count=rag_chunk.token_count,
                metadata={
                    **rag_chunk.model_dump(
                        mode="json",
                        exclude={
                            "text",
                            "raw_text",
                            "source_raw_text",
                            "normalized_text",
                            "document_id",
                            "tenant_id",
                            "source_file",
                            "source_uri",
                            "document_title",
                            "organization_id",
                            "knowledge_base_id",
                            "uploaded_by_user_id",
                            "visibility",
                            "database_chunk_id",
                        },
                        exclude_none=True,
                    ),
                    "raw_text": rag_chunk.raw_text,
                    "document_context": result.document_context,
                    "chunk_strategy": record.get("chunk_strategy") or route.primary_strategy,
                    "chunk_mode": "docling_router",
                    "docling_router_strategy": route.primary_strategy,
                    "docling_router_used_generic": route.used_generic_docling,
                    "docling_router_supplemental_strategies": route.supplemental_strategies,
                    "detected_document_profile": route.document_profile,
                    "document_profile": document_profile_column_value(route.document_profile),
                    "chunk_size": max_tokens,
                    "chunk_overlap": 0,
                    "overlap_applied": False,
                    "source_file": source_file,
                    "source_uri": source_uri,
                    "parser": "docling",
                    "parser_version": parser_version,
                    "chunker": "docling_router_v1",
                    "chunker_version": "1",
                },
            )
            for index, (record, rag_chunk) in enumerate(
                zip(result.records, rag_chunks, strict=True)
            )
        ]
        document_profile_value = document_profile_column_value(route.document_profile)

        stored_artifacts = await self._store_docling_chunk_artifacts(
            document_id=document.id,
            source_file=source_file,
            result=result,
            rag_chunks=rag_chunks,
            max_tokens=max_tokens,
        )
        try:
            document.document_profile = document_profile_value
            await self._repository.delete_chunks_for_document(document.id)
            await self._repository.create_chunks(document_id=document.id, chunks=chunk_records)
            metadata_updater = getattr(self._repository, "update_document_metadata", None)
            if metadata_updater is not None:
                await metadata_updater(
                    document,
                    {
                        "document_version": document_version,
                        "document_profile": document_profile_value,
                        "detected_document_profile": route.document_profile,
                        "chunker": "docling_router_v1",
                        "chunker_version": "1",
                        "chunking_router_strategy": route.primary_strategy,
                        "chunking_router_used_generic": route.used_generic_docling,
                        "chunking_router_supplemental_strategies": route.supplemental_strategies,
                        "chunk_count_total": len(chunk_records),
                        "chunk_count_indexable": sum(
                            1 for chunk in rag_chunks if should_index_chunk(chunk)
                        ),
                        "quality_status": result.quality.get("status", "unknown"),
                        "quality_summary": result.quality,
                        "artifact_paths": {
                            **dict(document_metadata.get("artifact_paths") or {}),
                            **stored_artifacts,
                        },
                    },
                )
            await self._repository.update_document_status(document, "chunked")
            await self._repository.commit()
        except Exception as exc:
            await self._repository.rollback()
            raise DocumentChunkingError("Failed to persist Docling router chunks.") from exc

        return DocumentChunkResponse(
            document_id=document.id,
            status=document.status,
            chunk_count=len(chunk_records),
            preview=[
                ChunkPreview(
                    chunk_index=index,
                    content=rag_chunk.text,
                    start_char=0,
                    end_char=len(rag_chunk.text),
                )
                for index, rag_chunk in enumerate(rag_chunks[:CHUNK_PREVIEW_LIMIT])
            ],
        )

    async def _store_docling_chunk_artifacts(
        self,
        *,
        document_id: UUID,
        source_file: str,
        result: Any,
        rag_chunks: list[Any],
        max_tokens: int,
    ) -> dict[str, str]:
        from app.services.chunkers.chunker_docling_generic_chunking import render_chunks_markdown

        if self._storage is None:
            return {}
        stem = Path(source_file).stem or "document"
        base = f"documents/{document_id}/artifacts"
        artifacts: dict[str, tuple[str, bytes, str]] = {
            "chunks_jsonl": (
                f"{base}/{stem}.chunks.jsonl",
                (
                    "".join(
                        json.dumps(
                            chunk.model_dump(mode="json", exclude_none=True),
                            ensure_ascii=False,
                        )
                        + "\n"
                        for chunk in rag_chunks
                    )
                ).encode("utf-8"),
                "application/x-ndjson",
            ),
            "chunks_markdown": (
                f"{base}/{stem}.chunks.md",
                render_chunks_markdown(
                    result,
                    source_file=source_file,
                    max_tokens=max_tokens,
                    document_context_mode=settings.docling_context_mode,
                ).encode("utf-8"),
                "text/markdown",
            ),
            "quality_json": (
                f"{base}/{stem}.quality.json",
                json.dumps(result.quality, ensure_ascii=False, indent=2).encode("utf-8"),
                "application/json",
            ),
            "coverage_json": (
                f"{base}/{stem}.coverage.json",
                json.dumps(result.coverage, ensure_ascii=False, indent=2).encode("utf-8"),
                "application/json",
            ),
        }
        stored: dict[str, str] = {}
        for key, (object_name, payload, content_type) in artifacts.items():
            await self._storage.put_file(
                object_name=object_name,
                data=BytesIO(payload),
                length=len(payload),
                content_type=content_type,
            )
            stored[key] = object_name
        return stored

    @staticmethod
    def _document_metadata_for_chunks(document_metadata: dict[str, Any]) -> dict[str, Any]:
        propagated: dict[str, Any] = {}
        for key in (
            "source_type",
            "source_name",
            "id_vb",
            "ky_hieu",
            "trich_yeu",
            "noi_ban_hanh",
            "nguoi_ky",
            "ten_file",
            "duong_dan",
            "doc_code",
            "doc_codes",
            "identifiers",
            "issuing_org",
            "issuer",
            "subject",
        ):
            value = document_metadata.get(key)
            if value not in (None, "", []):
                propagated[key] = value
        return propagated

    @staticmethod
    def _build_metadata(
        *,
        chunk_size: int,
        chunk_overlap: int,
        chunk_mode: ChunkMode,
        profile: str,
        text_chunk: TextChunk,
        extra_metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        chunk_type = str(text_chunk.metadata.get("chunk_type") or "text")
        no_overlap = (
            chunk_type == "table_row"
            or chunk_type in STRUCTURED_NO_OVERLAP_CHUNK_TYPES
        )
        metadata: dict[str, Any] = {
            "chunk_size": chunk_size,
            "chunk_overlap": 0 if no_overlap else chunk_overlap,
            "overlap_applied": False if no_overlap else chunk_overlap > 0,
            "chunk_mode": chunk_mode,
            "document_profile": profile,
            "chunk_type": chunk_type,
            "start_char": text_chunk.start_char,
            "end_char": text_chunk.end_char,
            "source_span": {
                "start": text_chunk.start_char,
                "end": text_chunk.end_char,
            },
        }
        if extra_metadata:
            metadata.update(extra_metadata)
        for key in (
            "chapter_title",
            "article_number",
            "article_title",
            "subchunk_index",
            "section_title",
            "table_title",
            "table_headers",
            "heading_path",
            "page_number",
            "page_range",
            "table_id",
            "headers",
            "row_start",
            "row_end",
            "row_index",
            "source_table",
            "stt",
            "area",
            "lead_department",
            "staff_names",
            "staff",
            "person_name",
            "entity_type",
            "areas",
            "table_ids",
            "page_numbers",
            "relationship_type",
            "confidence",
            "type",
            "layer_id",
        ):
            if key in text_chunk.metadata:
                metadata[key] = text_chunk.metadata[key]
        return metadata

    @staticmethod
    def _raw_chunk_metadata(
        *,
        chunk_dict: dict[str, Any],
        chunk_size: int,
        chunk_overlap: int,
        chunk_mode: ChunkMode,
        router_metadata: dict[str, Any],
        profile: str,
    ) -> dict[str, Any]:
        metadata = dict(chunk_dict.get("metadata", {}) or {})
        chunk_type = str(metadata.get("chunk_type") or "")
        is_table_row = chunk_type == "table_row"
        no_overlap = is_table_row or chunk_type in STRUCTURED_NO_OVERLAP_CHUNK_TYPES
        return {
            **metadata,
            "chunk_size": chunk_size,
            "chunk_overlap": 0 if no_overlap else chunk_overlap,
            "overlap_applied": False if no_overlap else chunk_overlap > 0,
            "chunk_mode": chunk_mode,
            **router_metadata,
            "document_profile": profile,
        }

    async def _get_primary_document_file(self, document_id: UUID):
        getter = getattr(self._repository, "get_primary_document_file", None)
        if getter is None:
            return None
        return await getter(document_id)

    @staticmethod
    def _has_table_elements(parsed_elements: list) -> bool:
        return any(
            getattr(element, "element_type", None) in {"table", "table_row"}
            for element in parsed_elements
        )

    @staticmethod
    def _chunks_from_slide_elements(parsed_elements: list) -> list[dict[str, Any]]:
        chunks: list[dict[str, Any]] = []
        for element in parsed_elements:
            if getattr(element, "element_type", None) != "slide":
                continue
            page_number = getattr(element, "page_number", None)
            metadata = dict(getattr(element, "metadata", {}) or {})
            chunks.append(
                {
                    "content": element.text,
                    "metadata": {
                        **metadata,
                        "chunk_type": "slide_page",
                        "page_number": page_number,
                        "page_range": [page_number, page_number]
                        if page_number is not None
                        else [],
                    },
                }
            )
        return chunks

    @staticmethod
    def _chunks_from_hybrid_elements(
        parsed_elements: list,
        parsed_text: str,
        *,
        chunk_size: int,
        chunk_overlap: int,
    ) -> list[dict[str, Any]]:
        from app.services.chunkers.chunker_table_relationships import build_entity_profile_chunks

        prose_chunks = ChunkingService._chunks_from_prose_elements(
            parsed_elements,
            parsed_text,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
        )
        table_chunks = ChunkingService._chunks_from_table_elements(parsed_elements)
        entity_profile_chunks = build_entity_profile_chunks(
            table_chunks,
            start_index=len(prose_chunks) + len(table_chunks),
        )
        chunks = [*prose_chunks, *table_chunks, *entity_profile_chunks]
        for index, chunk in enumerate(chunks):
            chunk["chunk_index"] = index
        return chunks

    @staticmethod
    def _chunks_from_prose_elements(
        parsed_elements: list,
        parsed_text: str,
        *,
        chunk_size: int,
        chunk_overlap: int,
    ) -> list[dict[str, Any]]:
        prose_types = {"title", "heading", "paragraph", "list_item", "code"}
        explicit_prose = [
            element
            for element in parsed_elements
            if getattr(element, "element_type", None) in prose_types
            and getattr(element, "text", "").strip()
        ]
        chunker = RecursiveTextChunker(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
        )
        if explicit_prose:
            groups: list[dict[str, Any]] = []
            current: dict[str, Any] | None = None
            for element in explicit_prose:
                element_type = getattr(element, "element_type", None)
                section_title = ChunkingService._section_key_from_prose_element(element)
                heading_path = list(getattr(element, "heading_path", None) or [])
                if element_type in {"title", "heading"}:
                    current = {
                        "section_title": section_title or element.text.strip(),
                        "heading_path": heading_path or [element.text.strip()],
                        "elements": [element],
                    }
                    groups.append(current)
                    continue
                if current is None:
                    current = {
                        "section_title": section_title,
                        "heading_path": heading_path,
                        "elements": [],
                    }
                    groups.append(current)
                current["elements"].append(element)

            chunks: list[dict[str, Any]] = []
            for group in groups:
                elements = group["elements"]
                text = "\n\n".join(element.text.strip() for element in elements)
                if not text.strip():
                    continue
                pages = sorted(
                    {
                        int(element.page_number)
                        for element in elements
                        if getattr(element, "page_number", None) is not None
                    }
                )
                split_chunks = chunker.chunk_text(text)
                for part_index, text_chunk in enumerate(split_chunks):
                    chunk_type = (
                        "heading_section"
                        if len(split_chunks) == 1
                        else "heading_section_part"
                    )
                    metadata: dict[str, Any] = {
                        **dict(text_chunk.metadata),
                        "chunk_type": chunk_type,
                        "section_title": group["section_title"],
                        "heading_path": group["heading_path"],
                        "start_char": text_chunk.start_char,
                        "end_char": text_chunk.end_char,
                    }
                    if len(split_chunks) > 1:
                        metadata["subchunk_index"] = part_index
                    if pages:
                        metadata["page_number"] = pages[0]
                        metadata["page_range"] = [pages[0], pages[-1]]
                    chunks.append(
                        {
                            "content": text_chunk.content,
                            "metadata": metadata,
                        }
                    )
            return chunks

        page_elements = [
            element
            for element in parsed_elements
            if getattr(element, "element_type", None) == "page"
            and getattr(element, "text", "").strip()
        ]
        chunks: list[dict[str, Any]] = []
        if page_elements:
            for page_element in page_elements:
                page_text = ChunkingService._strip_table_region_from_prose(
                    page_element.text,
                    table_texts=ChunkingService._table_texts_for_page(
                        parsed_elements,
                        getattr(page_element, "page_number", None),
                    ),
                )
                if not page_text.strip():
                    continue
                for text_chunk in chunker.chunk_text(page_text):
                    metadata = dict(text_chunk.metadata)
                    page_number = getattr(page_element, "page_number", None)
                    if page_number is not None:
                        metadata["page_number"] = page_number
                        metadata["page_range"] = [page_number, page_number]
                    chunks.append(
                        {
                            "content": text_chunk.content,
                            "metadata": {
                                **metadata,
                                "start_char": text_chunk.start_char,
                                "end_char": text_chunk.end_char,
                            },
                        }
                    )
            return chunks

        prose_text = ChunkingService._strip_table_region_from_prose(
            parsed_text,
            table_texts=ChunkingService._table_texts_from_elements(parsed_elements),
        )
        return ChunkingService._raw_chunks_from_text_chunks(
            chunker.chunk_text(prose_text)
        )

    @staticmethod
    def _raw_chunks_from_text_chunks(
        text_chunks: list[Any],
        *,
        section_pages: dict[str, list[int]] | None = None,
    ) -> list[dict[str, Any]]:
        chunks: list[dict[str, Any]] = []
        for text_chunk in text_chunks:
            metadata = dict(text_chunk.metadata)
            section_key = metadata.get("section_title")
            pages = section_pages.get(section_key, []) if section_pages else []
            if pages:
                metadata["page_number"] = pages[0]
                metadata["page_range"] = [pages[0], pages[-1]]
            metadata["source_span"] = {
                "start": text_chunk.start_char,
                "end": text_chunk.end_char,
            }
            metadata.setdefault("start_char", text_chunk.start_char)
            metadata.setdefault("end_char", text_chunk.end_char)
            chunks.append(
                {
                    "content": text_chunk.content,
                    "metadata": {
                        **metadata,
                        "start_char": text_chunk.start_char,
                        "end_char": text_chunk.end_char,
                    },
                }
            )
        return chunks

    @staticmethod
    def _section_pages_from_prose_elements(elements: list) -> dict[str, list[int]]:
        pages_by_section: dict[str, set[int]] = {}
        for element in elements:
            page_number = getattr(element, "page_number", None)
            if page_number is None:
                continue
            section_key = ChunkingService._section_key_from_prose_element(element)
            if not section_key:
                continue
            pages_by_section.setdefault(section_key, set()).add(int(page_number))
        return {
            section_key: sorted(page_numbers)
            for section_key, page_numbers in pages_by_section.items()
        }

    @staticmethod
    def _section_key_from_prose_element(element: Any) -> str | None:
        section_title = getattr(element, "section_title", None)
        if section_title:
            return str(section_title)
        heading_path = getattr(element, "heading_path", None) or []
        if heading_path:
            return str(heading_path[-1])
        if getattr(element, "element_type", None) in {"title", "heading"}:
            text = getattr(element, "text", "").strip()
            return text or None
        return None

    @staticmethod
    def _strip_table_region_from_prose(
        text: str,
        *,
        table_texts: list[str] | None = None,
    ) -> str:
        """Remove table text from prose using parser-provided table elements.

        This avoids document-specific marker rules. If the parser emits table or
        table-row elements, the service removes those exact text spans from the
        page/body prose before recursive chunking. When no structured table text
        is available, the function leaves the prose unchanged instead of guessing
        from language-specific headings.
        """

        cleaned = text
        for table_text in table_texts or []:
            snippet = str(table_text or "").strip()
            if not snippet:
                continue
            cleaned = cleaned.replace(snippet, "")
        return cleaned.strip()

    @staticmethod
    def _table_texts_from_elements(parsed_elements: list) -> list[str]:
        table_texts: list[str] = []
        for element in parsed_elements:
            if getattr(element, "element_type", None) not in {"table", "table_row"}:
                continue
            text = str(getattr(element, "text", "") or "").strip()
            if text:
                table_texts.append(text)
        return table_texts

    @staticmethod
    def _table_texts_for_page(parsed_elements: list, page_number: Any) -> list[str]:
        if page_number is None:
            return ChunkingService._table_texts_from_elements(parsed_elements)
        table_texts: list[str] = []
        for element in parsed_elements:
            if getattr(element, "element_type", None) not in {"table", "table_row"}:
                continue
            if getattr(element, "page_number", None) != page_number:
                continue
            text = str(getattr(element, "text", "") or "").strip()
            if text:
                table_texts.append(text)
        return table_texts

    @staticmethod
    def _chunks_from_table_elements(parsed_elements: list) -> list[dict[str, Any]]:
        from app.services.chunkers.chunker_table_relationships import is_trusted_relationship_metadata

        chunks: list[dict[str, Any]] = []
        for element in parsed_elements:
            if getattr(element, "element_type", None) not in {"table", "table_row"}:
                continue
            metadata = dict(getattr(element, "metadata", {}) or {})
            if (
                getattr(element, "element_type", None) == "table_row"
                and metadata.get("relationship_type") == "technology_area_staff"
                and not is_trusted_relationship_metadata({**metadata, "chunk_type": "table_row"})
            ):
                continue
            row_index = getattr(element, "row_index", None)
            chunk_type = "table_row" if element.element_type == "table_row" else "table_block"
            table_title = metadata.get("table_title") or metadata.get("table_name")
            table_headers = metadata.get("table_headers") or metadata.get("headers") or []
            chunks.append(
                {
                    "content": element.text,
                    "metadata": {
                        **metadata,
                        "chunk_type": chunk_type,
                        "table_id": getattr(element, "table_id", None),
                        "table_title": table_title,
                        "table_headers": table_headers,
                        "row_start": row_index,
                        "row_end": row_index,
                        "row_index": row_index,
                        "headers": metadata.get("headers", []),
                        "source_table": metadata.get("source_table"),
                        "stt": metadata.get("stt"),
                        "area": metadata.get("area"),
                        "lead_department": metadata.get("lead_department"),
                        "staff_names": metadata.get("staff_names", []),
                        "staff": metadata.get("staff", []),
                        "relationship_type": metadata.get("relationship_type"),
                        "confidence": metadata.get("confidence"),
                        "page_number": getattr(element, "page_number", None),
                        "section_title": getattr(element, "section_title", None),
                        "heading_path": getattr(element, "heading_path", []),
                    },
                }
            )
        return chunks
