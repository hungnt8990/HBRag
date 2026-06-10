from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

from app.core.config import settings
from app.services.document_profiles import profile_config, resolve_profile
from app.services.parsers import ParsedElement

ChunkStrategy = Literal[
    "recursive",
    "legal_article",
    "table_aware",
    "slide_page",
    "heading_aware",
    "semantic",
    "code",
    "fallback",
]

HEADING_PATTERN = re.compile(r"^(#{1,6}\s+.+|[A-Z0-9][^.?!\n]{2,80})$", re.MULTILINE)
LEGAL_PATTERN = re.compile(r"(?mi)^\s*(Điều\s+\d+|CHƯƠNG\s+([IVXLCDM]+|\d+))\b")
CODE_EXTENSIONS = {".py", ".ts", ".tsx", ".js", ".jsx", ".cs", ".java", ".go"}


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
            strategy = self._validated_requested_strategy(requested)
            return ChunkingPlan(
                strategy=strategy,
                parser_hint=request.parser_hint,
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
                reason="requested_chunk_mode",
                metadata_required=self._metadata_for_strategy(strategy),
                use_elements=strategy in {"heading_aware", "slide_page"},
                document_profile=profile,
                chunk_mode=requested,
            )

        strategy, reason = self._detect_strategy(request, config["chunk_mode"])
        return ChunkingPlan(
            strategy=strategy,
            parser_hint=request.parser_hint,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            reason=reason,
            metadata_required=self._metadata_for_strategy(strategy),
            use_elements=strategy in {"heading_aware", "slide_page"},
            document_profile=profile,
            chunk_mode=strategy if strategy != "fallback" else "recursive",
        )

    def _detect_strategy(
        self,
        request: ChunkingRequest,
        profile_chunk_mode: str,
    ) -> tuple[ChunkStrategy, str]:
        if self._is_code_file(request.filename):
            return "code", "code_extension"
        if "TABLE_ROW " in request.parsed_text or self._looks_like_pipe_table(request.parsed_text):
            return "table_aware", "table_markers_or_pipe_table"
        if LEGAL_PATTERN.search(request.parsed_text):
            return "legal_article", "vietnamese_legal_markers"
        if self._looks_like_slide_document(request.parsed_elements):
            return "slide_page", "parsed_slide_or_page_elements"
        if self._has_heading_elements(request.parsed_elements) or self._looks_heading_aware(
            request.parsed_text
        ):
            return "heading_aware", "heading_structure_detected"
        if settings.enable_semantic_chunking and profile_chunk_mode == "semantic":
            return "semantic", "semantic_chunking_enabled"
        if profile_chunk_mode in {"recursive", "legal_article", "table_aware"}:
            return profile_chunk_mode, "document_profile_default"  # type: ignore[return-value]
        return "fallback", "no_structure_detected"

    @staticmethod
    def _validated_requested_strategy(chunk_mode: str) -> ChunkStrategy:
        allowed: set[str] = {
            "recursive",
            "legal_article",
            "table_aware",
            "slide_page",
            "heading_aware",
            "semantic",
            "code",
            "fallback",
        }
        if chunk_mode not in allowed:
            return "fallback"
        if chunk_mode == "semantic" and not settings.enable_semantic_chunking:
            return "fallback"
        return chunk_mode  # type: ignore[return-value]

    @staticmethod
    def _metadata_for_strategy(strategy: ChunkStrategy) -> tuple[str, ...]:
        common = ("chunk_strategy", "router_reason", "document_profile")
        if strategy == "table_aware":
            return (*common, "chunk_type", "table_id", "headers")
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
    def _looks_like_slide_document(elements: list[ParsedElement]) -> bool:
        slide_like = [
            element for element in elements if element.element_type in {"slide", "page"}
        ]
        if len(slide_like) < 2:
            return False
        return sum(len(element.text) for element in slide_like) / len(slide_like) < 1500

    @staticmethod
    def _looks_heading_aware(text: str) -> bool:
        return len(HEADING_PATTERN.findall(text[:10000])) >= 3


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
            chunks.append(
                RoutedTextChunk(
                    content=section_text,
                    start_char=start,
                    end_char=end,
                    metadata={
                        "chunk_type": "text",
                        "section_title": heading,
                        "heading_path": [heading] if heading else [],
                    },
                )
            )
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
