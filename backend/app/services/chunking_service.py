from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import UUID

from app.core.config import settings
from app.repositories.documents import ChunkCreate, DocumentRepository
from app.schemas.documents import ChunkPreview, DocumentChunkResponse

DEFAULT_CHUNK_SIZE = settings.default_chunk_size
DEFAULT_CHUNK_OVERLAP = settings.default_chunk_overlap
CHUNK_PREVIEW_LIMIT = 2
MIN_SPLIT_RATIO = 0.85

ChunkMode = Literal[
    "recursive",
    "legal_article",
    "table_aware",
    "hybrid_structured",
    "slide_page",
    "heading_aware",
    "semantic",
    "code",
    "fallback",
]
DEFAULT_CHUNK_MODE: ChunkMode = "recursive"

# Vietnamese legal-document heading patterns. The article pattern is the
# primary segmentation boundary; chapters are tracked as ambient metadata.
ARTICLE_PATTERN = re.compile(
    r"^[ \t]*Điều\s+(\d+)\s*[\.\:\-]?[ \t]*(.*)$",
    flags=re.MULTILINE,
)
CHAPTER_PATTERN = re.compile(
    r"^[ \t]*CHƯƠNG\s+([IVXLCDM]+|\d+)\s*[\.\:\-]?[ \t]*(.*)$",
    flags=re.MULTILINE,
)
LEGAL_SEPARATORS = ("\n\n", "\n", ". ")


@dataclass(frozen=True)
class TextChunk:
    content: str
    start_char: int
    end_char: int
    metadata: dict[str, Any] = field(default_factory=dict)


class RecursiveTextChunker:
    default_separators = ("\n\n", ". ", " ")

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
                    start_char=start_char,
                    end_char=end_char,
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
    """Structure-aware chunker for Vietnamese legal/administrative documents.

    Splits primarily on ``Điều N`` headings while tracking the current
    ``CHƯƠNG`` for ambient metadata. Articles short enough to fit within
    ``chunk_size`` are kept as a single semantic chunk; longer articles are
    sub-split using the recursive chunker with line-aware separators so that
    label/value rows in tabular text stay together when possible.
    """

    def __init__(
        self,
        *,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
    ) -> None:
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        # Use line-aware separators to avoid splitting inside table rows.
        self._inner_chunker = RecursiveTextChunker(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            separators=LEGAL_SEPARATORS,
        )

    def chunk_text(self, text: str) -> list[TextChunk]:
        if not text.strip():
            return []

        article_matches = list(ARTICLE_PATTERN.finditer(text))
        chapter_matches = list(CHAPTER_PATTERN.finditer(text))
        results: list[TextChunk] = []

        first_article_start = (
            article_matches[0].start() if article_matches else len(text)
        )

        if first_article_start > 0:
            preamble = text[:first_article_start]
            chapter_title = self._latest_chapter_title(chapter_matches, first_article_start)
            results.extend(
                self._emit_chunks(
                    body=preamble,
                    body_offset=0,
                    base_metadata={
                        "chapter_title": chapter_title,
                        "article_number": None,
                        "article_title": None,
                    },
                )
            )

        for index, match in enumerate(article_matches):
            start = match.start()
            end = (
                article_matches[index + 1].start()
                if index + 1 < len(article_matches)
                else len(text)
            )
            article_text = text[start:end]
            article_number = match.group(1)
            article_title = (match.group(2) or "").strip() or None
            chapter_title = self._latest_chapter_title(chapter_matches, start)

            base_metadata = {
                "chapter_title": chapter_title,
                "article_number": article_number,
                "article_title": article_title,
            }

            if len(article_text.strip()) <= self.chunk_size:
                results.append(
                    TextChunk(
                        content=article_text.strip(),
                        start_char=start,
                        end_char=end,
                        metadata={**base_metadata, "subchunk_index": 0},
                    )
                )
                continue

            results.extend(
                self._emit_chunks(
                    body=article_text,
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
    def _latest_chapter_title(
        chapter_matches: list[re.Match[str]],
        position: int,
    ) -> str | None:
        latest: re.Match[str] | None = None
        for match in chapter_matches:
            if match.start() < position:
                latest = match
            else:
                break
        if latest is None:
            return None
        title = (latest.group(2) or "").strip()
        if title:
            return f"CHƯƠNG {latest.group(1)} {title}".strip()
        return f"CHƯƠNG {latest.group(1)}".strip()


class DocumentChunkingError(RuntimeError):
    pass


class DocumentNotFoundError(LookupError):
    pass


class DocumentChunkStatusError(ValueError):
    pass


class EmptyParsedTextError(ValueError):
    pass


class ChunkingService:
    def __init__(
        self,
        *,
        repository: DocumentRepository,
        chunker: RecursiveTextChunker | None = None,
    ) -> None:
        self._repository = repository
        self._chunker = chunker or RecursiveTextChunker()

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

        from app.services.chunking_router import (
            ChunkingRequest,
            ChunkingRouter,
            HeadingAwareChunker,
            SlidePageChunker,
        )
        from app.services.parsers import parsed_element_from_dict

        document_file = await self._get_primary_document_file(document.id)
        document_metadata = dict(getattr(document, "document_metadata", None) or {})
        parsed_elements = [
            parsed_element_from_dict(element)
            for element in document_metadata.get("parsed_elements", [])
            if isinstance(element, dict)
        ]
        router = ChunkingRouter()
        plan = router.plan(
            ChunkingRequest(
                filename=getattr(document_file, "filename", None),
                mime_type=getattr(document_file, "mime_type", None),
                parsed_text=document.parsed_text,
                parsed_elements=parsed_elements,
                document_profile=(
                    profile if profile is not None else getattr(document, "document_profile", None)
                ),
                requested_chunk_mode=chunk_mode,
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
                parser_hint=document_metadata.get("parser"),
            )
        )
        effective_profile = plan.document_profile
        resolved_size = plan.chunk_size
        resolved_overlap = plan.chunk_overlap
        mode = plan.chunk_mode
        router_metadata = {
            "chunk_strategy": plan.strategy,
            "router_reason": plan.reason,
            "parser": plan.parser_hint,
            "source_file": getattr(document_file, "filename", None),
        }

        if plan.strategy == "hybrid_structured":
            raw_chunks = self._chunks_from_hybrid_elements(
                parsed_elements,
                document.parsed_text,
                chunk_size=resolved_size,
                chunk_overlap=resolved_overlap,
            )
            chunk_records = [
                ChunkCreate(
                    chunk_index=index,
                    content=chunk_dict["content"],
                    metadata={
                        **chunk_dict.get("metadata", {}),
                        "chunk_size": resolved_size,
                        "chunk_overlap": resolved_overlap,
                        "chunk_mode": mode,
                        **router_metadata,
                        "document_profile": effective_profile,
                    },
                )
                for index, chunk_dict in enumerate(raw_chunks)
            ]
            text_chunks_for_preview = [
                TextChunk(
                    content=c["content"],
                    start_char=c.get("metadata", {}).get("start_char", 0),
                    end_char=c.get("metadata", {}).get("end_char", 0),
                )
                for c in raw_chunks[:CHUNK_PREVIEW_LIMIT]
            ]
        elif plan.strategy == "table_aware" and self._has_table_elements(parsed_elements):
            raw_chunks = self._chunks_from_table_elements(parsed_elements)
            from app.services.table_relationships import build_entity_profile_chunks

            raw_chunks.extend(
                build_entity_profile_chunks(raw_chunks, start_index=len(raw_chunks))
            )
            chunk_records = [
                ChunkCreate(
                    chunk_index=index,
                    content=chunk_dict["content"],
                    metadata={
                        **chunk_dict.get("metadata", {}),
                        "chunk_size": resolved_size,
                        "chunk_overlap": resolved_overlap,
                        "chunk_mode": mode,
                        **router_metadata,
                        "document_profile": effective_profile,
                    },
                )
                for index, chunk_dict in enumerate(raw_chunks)
            ]
            text_chunks_for_preview = [
                TextChunk(
                    content=c["content"],
                    start_char=c.get("metadata", {}).get("start_char", 0),
                    end_char=c.get("metadata", {}).get("end_char", 0),
                )
                for c in raw_chunks[:CHUNK_PREVIEW_LIMIT]
            ]
        elif plan.strategy == "table_aware":
            from app.services.table_aware_chunking import table_aware_chunk_text

            raw_chunks, _entity_index = table_aware_chunk_text(
                document.parsed_text,
                chunk_size=resolved_size,
                chunk_overlap=resolved_overlap,
            )
            chunk_records = [
                ChunkCreate(
                    chunk_index=chunk_dict["chunk_index"],
                    content=chunk_dict["content"],
                    metadata={
                        **chunk_dict.get("metadata", {}),
                        "chunk_size": resolved_size,
                        "chunk_overlap": resolved_overlap,
                        "chunk_mode": mode,
                        **router_metadata,
                        "document_profile": effective_profile,
                    },
                )
                for chunk_dict in raw_chunks
            ]
            text_chunks_for_preview = [
                TextChunk(
                    content=c["content"],
                    start_char=c.get("metadata", {}).get("start_char", 0),
                    end_char=c.get("metadata", {}).get("end_char", 0),
                )
                for c in raw_chunks[:CHUNK_PREVIEW_LIMIT]
            ]
        elif plan.strategy == "legal_article":
            chunker: RecursiveTextChunker | LegalArticleChunker = LegalArticleChunker(
                chunk_size=resolved_size, chunk_overlap=resolved_overlap
            )
            text_chunks = chunker.chunk_text(document.parsed_text)
            chunk_records = [
                ChunkCreate(
                    chunk_index=index,
                    content=text_chunk.content,
                    metadata=self._build_metadata(
                        chunk_size=resolved_size,
                        chunk_overlap=resolved_overlap,
                        chunk_mode=mode,
                        profile=effective_profile,
                        text_chunk=text_chunk,
                        extra_metadata=router_metadata,
                    ),
                )
                for index, text_chunk in enumerate(text_chunks)
            ]
            text_chunks_for_preview = text_chunks[:CHUNK_PREVIEW_LIMIT]
        elif plan.strategy == "heading_aware":
            heading_chunker = HeadingAwareChunker(
                chunk_size=resolved_size,
                chunk_overlap=resolved_overlap,
            )
            if parsed_elements:
                text_chunks = heading_chunker.chunk_elements(
                    parsed_elements,
                    document.parsed_text,
                )
            else:
                text_chunks = heading_chunker.chunk_text(document.parsed_text)
            chunk_records = [
                ChunkCreate(
                    chunk_index=index,
                    content=text_chunk.content,
                    metadata=self._build_metadata(
                        chunk_size=resolved_size,
                        chunk_overlap=resolved_overlap,
                        chunk_mode=mode,
                        profile=effective_profile,
                        text_chunk=text_chunk,
                        extra_metadata=router_metadata,
                    ),
                )
                for index, text_chunk in enumerate(text_chunks)
            ]
            text_chunks_for_preview = text_chunks[:CHUNK_PREVIEW_LIMIT]
        elif plan.strategy == "slide_page":
            text_chunks = SlidePageChunker().chunk_elements(parsed_elements, document.parsed_text)
            chunk_records = [
                ChunkCreate(
                    chunk_index=index,
                    content=text_chunk.content,
                    metadata=self._build_metadata(
                        chunk_size=resolved_size,
                        chunk_overlap=resolved_overlap,
                        chunk_mode=mode,
                        profile=effective_profile,
                        text_chunk=text_chunk,
                        extra_metadata=router_metadata,
                    ),
                )
                for index, text_chunk in enumerate(text_chunks)
            ]
            text_chunks_for_preview = text_chunks[:CHUNK_PREVIEW_LIMIT]
        else:
            chunker = RecursiveTextChunker(
                chunk_size=resolved_size, chunk_overlap=resolved_overlap
            )
            text_chunks = chunker.chunk_text(document.parsed_text)
            chunk_records = [
                ChunkCreate(
                    chunk_index=index,
                    content=text_chunk.content,
                    metadata=self._build_metadata(
                        chunk_size=resolved_size,
                        chunk_overlap=resolved_overlap,
                        chunk_mode=mode,
                        profile=effective_profile,
                        text_chunk=text_chunk,
                        extra_metadata=router_metadata,
                    ),
                )
                for index, text_chunk in enumerate(text_chunks)
            ]
            text_chunks_for_preview = text_chunks[:CHUNK_PREVIEW_LIMIT]

        try:
            document.document_profile = effective_profile
            await self._repository.delete_chunks_for_document(document.id)
            await self._repository.create_chunks(document_id=document.id, chunks=chunk_records)
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
                    content=tc.content,
                    start_char=tc.start_char,
                    end_char=tc.end_char,
                )
                for index, tc in enumerate(text_chunks_for_preview)
            ],
        )

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
        metadata: dict[str, Any] = {
            "chunk_size": chunk_size,
            "chunk_overlap": chunk_overlap,
            "chunk_mode": chunk_mode,
            "document_profile": profile,
            "chunk_type": text_chunk.metadata.get("chunk_type", "text"),
            "start_char": text_chunk.start_char,
            "end_char": text_chunk.end_char,
        }
        if extra_metadata:
            metadata.update(extra_metadata)
        for key in (
            "chapter_title",
            "article_number",
            "article_title",
            "subchunk_index",
            "section_title",
            "heading_path",
            "page_number",
            "page_range",
            "table_id",
            "headers",
            "row_start",
            "row_end",
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
        ):
            if key in text_chunk.metadata:
                metadata[key] = text_chunk.metadata[key]
        return metadata

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
    def _chunks_from_hybrid_elements(
        parsed_elements: list,
        parsed_text: str,
        *,
        chunk_size: int,
        chunk_overlap: int,
    ) -> list[dict[str, Any]]:
        from app.services.table_relationships import build_entity_profile_chunks

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
        from app.services.chunking_router import HeadingAwareChunker

        prose_types = {"title", "heading", "paragraph", "list_item", "code"}
        explicit_prose = [
            element
            for element in parsed_elements
            if getattr(element, "element_type", None) in prose_types
            and getattr(element, "text", "").strip()
        ]
        chunker = HeadingAwareChunker(chunk_size=chunk_size, chunk_overlap=chunk_overlap)
        if explicit_prose:
            fallback_text = "\n\n".join(element.text.strip() for element in explicit_prose)
            text_chunks = chunker.chunk_elements(explicit_prose, fallback_text)
            if not text_chunks:
                text_chunks = chunker.chunk_text(fallback_text)
            section_pages = ChunkingService._section_pages_from_prose_elements(
                explicit_prose
            )
            return ChunkingService._raw_chunks_from_text_chunks(
                text_chunks,
                section_pages=section_pages,
            )

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
                    page_element.text
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

        prose_text = ChunkingService._strip_table_region_from_prose(parsed_text)
        return ChunkingService._raw_chunks_from_text_chunks(chunker.chunk_text(prose_text))

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
    def _strip_table_region_from_prose(text: str) -> str:
        table_markers = (
            "DANH SÁCH NHÂN SỰ",
            "DANH SACH NHAN SU",
            "STT Mảng công nghệ",
            "STT Mang cong nghe",
        )
        candidates = [
            text.casefold().find(marker.casefold())
            for marker in table_markers
            if marker.casefold() in text.casefold()
        ]
        if not candidates:
            return text.strip()
        return text[: min(candidates)].strip()

    @staticmethod
    def _chunks_from_table_elements(parsed_elements: list) -> list[dict[str, Any]]:
        from app.services.table_relationships import is_trusted_relationship_metadata

        chunks: list[dict[str, Any]] = []
        for element in parsed_elements:
            if getattr(element, "element_type", None) not in {"table", "table_row"}:
                continue
            metadata = dict(getattr(element, "metadata", {}) or {})
            if (
                getattr(element, "element_type", None) == "table_row"
                and metadata.get("relationship_type") == "technology_area_staff"
                and not is_trusted_relationship_metadata(
                    {**metadata, "chunk_type": "table_row"}
                )
            ):
                continue
            row_index = getattr(element, "row_index", None)
            chunk_type = "table_row" if element.element_type == "table_row" else "table_block"
            chunks.append(
                {
                    "content": element.text,
                    "metadata": {
                        **metadata,
                        "chunk_type": chunk_type,
                        "table_id": getattr(element, "table_id", None),
                        "row_start": row_index,
                        "row_end": row_index,
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
