from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import UUID

from app.core.config import settings
from app.repositories.documents import ChunkCreate, DocumentRepository
from app.schemas.documents import ChunkPreview, DocumentChunkResponse
from app.services.document_profiles import profile_config, resolve_profile

DEFAULT_CHUNK_SIZE = settings.default_chunk_size
DEFAULT_CHUNK_OVERLAP = settings.default_chunk_overlap
CHUNK_PREVIEW_LIMIT = 2
MIN_SPLIT_RATIO = 0.5

ChunkMode = Literal["recursive", "legal_article"]
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

        selected_profile = profile if profile is not None else getattr(
            document, "document_profile", None
        )
        effective_profile = resolve_profile(selected_profile, text=document.parsed_text)
        config = profile_config(effective_profile)

        resolved_size = chunk_size if chunk_size is not None else config["chunk_size"]
        resolved_overlap = (
            chunk_overlap if chunk_overlap is not None else config["chunk_overlap"]
        )
        if resolved_overlap >= resolved_size:
            resolved_overlap = max(0, resolved_size // 2)
        mode: ChunkMode = chunk_mode or config["chunk_mode"]

        if mode == "legal_article":
            chunker: RecursiveTextChunker | LegalArticleChunker = LegalArticleChunker(
                chunk_size=resolved_size, chunk_overlap=resolved_overlap
            )
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
                ),
            )
            for index, text_chunk in enumerate(text_chunks)
        ]

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
                    content=text_chunk.content,
                    start_char=text_chunk.start_char,
                    end_char=text_chunk.end_char,
                )
                for index, text_chunk in enumerate(text_chunks[:CHUNK_PREVIEW_LIMIT])
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
    ) -> dict[str, Any]:
        metadata: dict[str, Any] = {
            "chunk_size": chunk_size,
            "chunk_overlap": chunk_overlap,
            "chunk_mode": chunk_mode,
            "document_profile": profile,
            "start_char": text_chunk.start_char,
            "end_char": text_chunk.end_char,
        }
        for key in ("chapter_title", "article_number", "article_title", "subchunk_index"):
            if key in text_chunk.metadata:
                metadata[key] = text_chunk.metadata[key]
        return metadata
