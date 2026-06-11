from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

from app.core.config import settings
from app.services.document_profiles import profile_config, resolve_profile
from app.services.parsers import ParsedElement
from app.services.table_relationships import looks_like_staff_area_table

ChunkStrategy = Literal[
    "recursive",
    "legal_article",
    "table_aware",
    "hybrid_structured",
    "slide_page",
    "heading_aware",
    "semantic",
    "code",
    "adaptive_segmented",
    "fallback",
]

HEADING_PATTERN = re.compile(r"^(#{1,6}\s+.+|[A-Z0-9][^.?!\n]{2,80})$", re.MULTILINE)
LEGAL_PATTERN = re.compile(r"(?mi)^\s*(Điều\s+\d+|CHƯƠNG\s+([IVXLCDM]+|\d+))\b")
CODE_EXTENSIONS = {".py", ".ts", ".tsx", ".js", ".jsx", ".cs", ".java", ".go"}
TABLE_REGION_MARKERS = (
    "DANH SÁCH NHÂN SỰ",
    "DANH SACH NHAN SU",
    "STT Mảng công nghệ",
    "STT Mang cong nghe",
)


@dataclass(frozen=True)
class RoutedTextChunk:
    content: str
    start_char: int
    end_char: int
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ChunkingPlan:
    strategy: ChunkStrategy
    parser_hint: str | None
    chunk_size: int
    chunk_overlap: int
    reason: str
    metadata_required: tuple[str, ...] = ()
    use_elements: bool = False
    document_profile: str = "general"
    chunk_mode: str = "recursive"


@dataclass(frozen=True)
class ChunkingRequest:
    filename: str | None
    mime_type: str | None
    parsed_text: str
    parsed_elements: list[ParsedElement] = field(default_factory=list)
    document_profile: str | None = None
    requested_chunk_mode: str | None = None
    chunk_size: int | None = None
    chunk_overlap: int | None = None
    parser_hint: str | None = None


class ChunkingRouter:
    def plan(self, request: ChunkingRequest) -> ChunkingPlan:
        profile = resolve_profile(request.document_profile, text=request.parsed_text)
        config = profile_config(profile)
        chunk_size = request.chunk_size if request.chunk_size is not None else config["chunk_size"]
        chunk_overlap = (
            request.chunk_overlap if request.chunk_overlap is not None else config["chunk_overlap"]
        )
        if chunk_overlap >= chunk_size:
            chunk_overlap = max(0, chunk_size // 2)

        requested = request.requested_chunk_mode
        if requested:
            strategy, reason = self._validated_requested_strategy(requested, request)
            return ChunkingPlan(
                strategy=strategy,
                parser_hint=request.parser_hint,
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
                reason=reason,
                metadata_required=self._metadata_for_strategy(strategy),
                use_elements=strategy in {"heading_aware", "hybrid_structured", "slide_page"},
                document_profile=profile,
                chunk_mode=strategy if strategy != "fallback" else "recursive",
            )

        strategy, reason = self._detect_strategy(request, config["chunk_mode"])
        if strategy == "adaptive_segmented":
            profile = "mixed_administrative_technical"
        return ChunkingPlan(
            strategy=strategy,
            parser_hint=request.parser_hint,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            reason=reason,
            metadata_required=self._metadata_for_strategy(strategy),
            use_elements=strategy in {"heading_aware", "hybrid_structured", "slide_page"},
            document_profile=profile,
            chunk_mode=strategy if strategy != "fallback" else "recursive",
        )

    def _detect_strategy(
        self,
        request: ChunkingRequest,
        profile_chunk_mode: str,
    ) -> tuple[ChunkStrategy, str]:
        if self._has_table_elements(request.parsed_elements):
            if self._looks_like_mixed_adaptive_document(request.parsed_text):
                return "adaptive_segmented", "mixed_gis_administrative_schema_document"
            if self._has_prose_elements(request.parsed_elements):
                return "hybrid_structured", "mixed_prose_and_table_elements"
            return "table_aware", "parsed_table_elements_only"
        if self._looks_like_mixed_adaptive_document(request.parsed_text):
            return "adaptive_segmented", "mixed_gis_administrative_schema_document"
        if looks_like_staff_area_table(request.parsed_text):
            return "table_aware", "staff_area_table_markers"
        if self._looks_like_slide_document(request.parsed_elements):
            return "slide_page", "parsed_slide_or_page_elements"
        if self._has_heading_elements(request.parsed_elements):
            return "heading_aware", "parsed_heading_elements"
        if self._is_code_file(request.filename):
            return "fallback", "code_chunker_unavailable"
        if LEGAL_PATTERN.search(request.parsed_text):
            return "legal_article", "vietnamese_legal_markers"
        if "TABLE_ROW " in request.parsed_text or self._looks_like_pipe_table(request.parsed_text):
            return "table_aware", "table_markers_or_pipe_table"
        if self._looks_heading_aware(request.parsed_text):
            return "heading_aware", "heading_structure_detected"
        if profile_chunk_mode == "semantic":
            if settings.enable_semantic_chunking:
                return "fallback", "semantic_chunker_unavailable"
            return "fallback", "semantic_chunking_disabled"
        if profile_chunk_mode in {"recursive", "legal_article", "table_aware"}:
            return profile_chunk_mode, "document_profile_default"  # type: ignore[return-value]
        return "fallback", "no_structure_detected"

    @staticmethod
    def _validated_requested_strategy(
        chunk_mode: str,
        request: ChunkingRequest,
    ) -> tuple[ChunkStrategy, str]:
        allowed: set[str] = {
            "recursive",
            "legal_article",
            "table_aware",
            "hybrid_structured",
            "slide_page",
            "heading_aware",
            "semantic",
            "code",
            "adaptive_segmented",
            "fallback",
        }
        if chunk_mode not in allowed:
            return "fallback", "unsupported_requested_chunk_mode"
        if chunk_mode == "semantic":
            if settings.enable_semantic_chunking:
                return "fallback", "semantic_chunker_unavailable"
            return "fallback", "semantic_chunking_disabled"
        if chunk_mode == "code":
            return "fallback", "code_chunker_unavailable"
        if chunk_mode == "slide_page" and not ChunkingRouter._has_page_elements(
            request.parsed_elements
        ):
            return "fallback", "no_page_or_slide_elements_available"
        return chunk_mode, "requested_chunk_mode"  # type: ignore[return-value]

    @staticmethod
    def _metadata_for_strategy(strategy: ChunkStrategy) -> tuple[str, ...]:
        common = ("chunk_strategy", "router_reason", "document_profile")
        if strategy in {"hybrid_structured", "table_aware"}:
            return (*common, "chunk_type", "table_id", "headers")
        if strategy == "adaptive_segmented":
            return (*common, "segment_type", "page_range")
        if strategy == "legal_article":
            return (*common, "article_number", "article_title", "chapter_title")
        if strategy == "slide_page":
            return (*common, "page_range", "section_title")
        if strategy == "heading_aware":
            return (*common, "section_title", "heading_path")
        return common

    @staticmethod
    def _is_code_file(filename: str | None) -> bool:
        if not filename or "." not in filename:
            return False
        return f".{filename.rsplit('.', 1)[1].lower()}" in CODE_EXTENSIONS

    @staticmethod
    def _looks_like_pipe_table(text: str) -> bool:
        lines = [line for line in text.splitlines() if line.strip()]
        if len(lines) < 3:
            return False
        pipe_lines = sum(1 for line in lines[:20] if " | " in line)
        return pipe_lines >= 3

    @staticmethod
    def _has_heading_elements(elements: list[ParsedElement]) -> bool:
        return any(element.element_type in {"title", "heading"} for element in elements)

    @staticmethod
    def _has_table_elements(elements: list[ParsedElement]) -> bool:
        return any(element.element_type in {"table", "table_row"} for element in elements)

    @staticmethod
    def _has_prose_elements(elements: list[ParsedElement]) -> bool:
        prose_types = {"title", "heading", "paragraph", "list_item", "code"}
        if any(
            element.element_type in prose_types and element.text.strip() for element in elements
        ):
            return True
        return any(
            element.element_type == "page"
            and ChunkingRouter._page_has_prose_outside_table(element.text)
            for element in elements
        )

    @staticmethod
    def _page_has_prose_outside_table(text: str) -> bool:
        prose_text = ChunkingRouter._strip_table_region_from_prose(text)
        if not prose_text:
            return False
        if "TABLE_ROW " in prose_text or ChunkingRouter._looks_like_pipe_table(prose_text):
            return False
        return bool(HEADING_PATTERN.search(prose_text[:2000])) or len(prose_text.split()) >= 8

    @staticmethod
    def _strip_table_region_from_prose(text: str) -> str:
        normalized = text.casefold()
        candidates = [
            normalized.find(marker.casefold())
            for marker in TABLE_REGION_MARKERS
            if marker.casefold() in normalized
        ]
        if not candidates:
            return text.strip()
        return text[: min(candidates)].strip()

    @staticmethod
    def _has_page_elements(elements: list[ParsedElement]) -> bool:
        return any(element.element_type in {"slide", "page"} for element in elements)

    @staticmethod
    def _looks_like_slide_document(elements: list[ParsedElement]) -> bool:
        slide_like = [element for element in elements if element.element_type in {"slide", "page"}]
        if len(slide_like) < 2:
            return False
        return sum(len(element.text) for element in slide_like) / len(slide_like) < 1500

    @staticmethod
    def _looks_heading_aware(text: str) -> bool:
        return len(HEADING_PATTERN.findall(text[:10000])) >= 3

    @staticmethod
    def _looks_like_mixed_adaptive_document(text: str) -> bool:
        from app.services.segment_router import looks_like_mixed_gis_document

        return looks_like_mixed_gis_document(text)


class HeadingAwareChunker:
    def __init__(
        self,
        *,
        chunk_size: int = settings.default_chunk_size,
        chunk_overlap: int = 0,
    ) -> None:
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

    def chunk_text(self, text: str) -> list[RoutedTextChunk]:
        sections = self._split_sections(text)
        chunks: list[RoutedTextChunk] = []
        for heading, start, end in sections:
            section_text = text[start:end].strip()
            if not section_text:
                continue
            section_metadata = {
                "section_title": heading,
                "heading_path": [heading] if heading else [],
            }
            if len(section_text) <= self.chunk_size:
                chunks.append(
                    RoutedTextChunk(
                        content=section_text,
                        start_char=start,
                        end_char=end,
                        metadata={"chunk_type": "heading_section", **section_metadata},
                    )
                )
                continue

            sub_chunks = self._split_long_section(section_text)
            part_total = len(sub_chunks)
            for part_index, sub_chunk in enumerate(sub_chunks):
                chunks.append(
                    RoutedTextChunk(
                        content=sub_chunk.content,
                        start_char=start + sub_chunk.start_char,
                        end_char=start + sub_chunk.end_char,
                        metadata={
                            "chunk_type": "heading_section_part",
                            "part_index": part_index,
                            "part_total": part_total,
                            **section_metadata,
                        },
                    )
                )
        return chunks

    def chunk_elements(
        self,
        elements: list[ParsedElement],
        fallback_text: str,
    ) -> list[RoutedTextChunk]:
        sections = self._sections_from_elements(elements)
        if not sections:
            return self.chunk_text(fallback_text)

        chunks: list[RoutedTextChunk] = []
        cursor = 0
        for section in sections:
            content = "\n\n".join(part for part in section["parts"] if part.strip()).strip()
            if not content:
                continue
            start = fallback_text.find(content, cursor)
            if start < 0:
                start = cursor
            end = start + len(content)
            cursor = end
            section_metadata = {
                "section_title": section["section_title"],
                "heading_path": section["heading_path"],
            }
            if len(content) <= self.chunk_size:
                chunks.append(
                    RoutedTextChunk(
                        content=content,
                        start_char=start,
                        end_char=end,
                        metadata={"chunk_type": "heading_section", **section_metadata},
                    )
                )
                continue

            sub_chunks = self._split_long_section(content)
            part_total = len(sub_chunks)
            for part_index, sub_chunk in enumerate(sub_chunks):
                chunks.append(
                    RoutedTextChunk(
                        content=sub_chunk.content,
                        start_char=start + sub_chunk.start_char,
                        end_char=start + sub_chunk.end_char,
                        metadata={
                            "chunk_type": "heading_section_part",
                            "part_index": part_index,
                            "part_total": part_total,
                            **section_metadata,
                        },
                    )
                )
        return chunks

    @staticmethod
    def _sections_from_elements(elements: list[ParsedElement]) -> list[dict[str, Any]]:
        sections: list[dict[str, Any]] = []
        current: dict[str, Any] | None = None

        for element in elements:
            if element.element_type in {"title", "heading"}:
                current = {
                    "section_title": element.section_title or element.text,
                    "heading_path": element.heading_path or [element.text],
                    "parts": [element.text],
                }
                sections.append(current)
                continue
            if element.element_type not in {"paragraph", "list_item", "code", "table"}:
                continue
            if current is None:
                heading_path = element.heading_path or []
                section_title = element.section_title or (
                    heading_path[-1] if heading_path else None
                )
                if not section_title:
                    continue
                current = {
                    "section_title": section_title,
                    "heading_path": heading_path or [section_title],
                    "parts": [],
                }
                sections.append(current)
            current["parts"].append(element.text)

        return sections

    def _split_long_section(self, text: str) -> list[RoutedTextChunk]:
        chunks: list[RoutedTextChunk] = []
        start_char = 0
        while start_char < len(text):
            end_char = min(start_char + self.chunk_size, len(text))
            if end_char < len(text):
                split_at = text.rfind("\n\n", start_char, end_char)
                if split_at <= start_char:
                    split_at = text.rfind(". ", start_char, end_char)
                if split_at > start_char + int(self.chunk_size * 0.5):
                    end_char = split_at + 1
            chunks.append(
                RoutedTextChunk(
                    content=text[start_char:end_char].strip(),
                    start_char=start_char,
                    end_char=end_char,
                )
            )
            if end_char >= len(text):
                break
            start_char = max(end_char - self.chunk_overlap, start_char + 1)
        return chunks

    @staticmethod
    def _split_sections(text: str) -> list[tuple[str | None, int, int]]:
        matches = [match for match in HEADING_PATTERN.finditer(text) if match.group(0).strip()]
        if not matches:
            return [(None, 0, len(text))]
        sections: list[tuple[str | None, int, int]] = []
        if matches[0].start() > 0 and text[: matches[0].start()].strip():
            sections.append((None, 0, matches[0].start()))
        for index, match in enumerate(matches):
            end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
            sections.append((match.group(0).lstrip("# ").strip(), match.start(), end))
        return sections


class SlidePageChunker:
    def chunk_elements(
        self,
        elements: list[ParsedElement],
        fallback_text: str,
    ) -> list[RoutedTextChunk]:
        page_elements = [
            element for element in elements if element.element_type in {"slide", "page"}
        ]
        if not page_elements:
            return [
                RoutedTextChunk(
                    content=fallback_text,
                    start_char=0,
                    end_char=len(fallback_text),
                    metadata={"chunk_type": "page"},
                )
            ]
        chunks: list[RoutedTextChunk] = []
        cursor = 0
        for element in page_elements:
            content = element.text.strip()
            if not content:
                continue
            start = fallback_text.find(content, cursor)
            if start < 0:
                start = cursor
            end = start + len(content)
            cursor = end
            page_number = element.page_number
            chunks.append(
                RoutedTextChunk(
                    content=content,
                    start_char=start,
                    end_char=end,
                    metadata={
                        "chunk_type": "slide" if element.element_type == "slide" else "page",
                        "page_number": page_number,
                        "page_range": (
                            [page_number, page_number] if page_number is not None else None
                        ),
                        "section_title": element.section_title,
                        "heading_path": element.heading_path,
                    },
                )
            )
        return chunks
