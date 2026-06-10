from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any

PAGE_MARKER_RE = re.compile(r"^\[\[PAGE\s+(?P<page>\d+)\]\]\s*$", re.MULTILINE)
MIN_SPLIT_RATIO = 0.55


@dataclass(frozen=True)
class SlideBlock:
    page_number: int | None
    title: str | None
    content: str
    start_char: int
    end_char: int


def slide_aware_chunk_text(
    text: str,
    *,
    chunk_size: int = 1000,
    chunk_overlap: int = 150,
) -> list[dict[str, Any]]:
    """Chunk slide/presentation-like PDFs without losing slide context.

    The normal recursive splitter is good for prose documents, but slide PDFs often
    contain short pages, repeated decorative headings, and lines extracted in a
    layout order. This splitter first cleans common PDF extraction noise, then
    groups complete slide blocks before applying character-sized chunks.
    """
    if not text or not text.strip():
        return []

    slides = split_slide_blocks(text)
    if not slides:
        cleaned = normalize_presentation_text(text)
        return _chunk_plain_text(cleaned, chunk_size=chunk_size, chunk_overlap=chunk_overlap)

    chunks: list[dict[str, Any]] = []
    current_blocks: list[SlideBlock] = []
    current_text = ""

    for slide in slides:
        rendered = _render_slide(slide)
        if not rendered:
            continue

        next_text = _join_blocks(current_text, rendered)
        should_flush = bool(current_blocks) and len(next_text) > chunk_size and len(current_text) >= int(chunk_size * MIN_SPLIT_RATIO)
        if should_flush:
            chunks.append(_make_slide_chunk(current_blocks, current_text, len(chunks)))
            overlap_text = _tail_overlap(current_text, chunk_overlap)
            current_blocks = []
            current_text = overlap_text

        current_blocks.append(slide)
        current_text = _join_blocks(current_text, rendered)

        # Very large single-slide blocks still need to be split safely.
        while len(current_text) > chunk_size * 1.25:
            split_at = _find_split_boundary(current_text, chunk_size)
            head = current_text[:split_at].strip()
            if head:
                chunks.append(_make_slide_chunk(current_blocks, head, len(chunks)))
            current_text = _join_blocks(_tail_overlap(head, chunk_overlap), current_text[split_at:].strip())

    if current_text.strip():
        chunks.append(_make_slide_chunk(current_blocks, current_text.strip(), len(chunks)))

    for index, chunk in enumerate(chunks):
        chunk["chunk_index"] = index
    return chunks


def split_slide_blocks(text: str) -> list[SlideBlock]:
    matches = list(PAGE_MARKER_RE.finditer(text))
    if not matches:
        normalized = normalize_presentation_text(text)
        if not normalized:
            return []
        return [SlideBlock(page_number=None, title=_infer_title(normalized), content=normalized, start_char=0, end_char=len(text))]

    slides: list[SlideBlock] = []
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        raw_content = text[start:end]
        normalized = normalize_presentation_text(raw_content)
        if not normalized:
            continue
        slides.append(
            SlideBlock(
                page_number=int(match.group("page")),
                title=_infer_title(normalized),
                content=normalized,
                start_char=match.start(),
                end_char=end,
            )
        )
    return slides


def normalize_presentation_text(text: str) -> str:
    """Clean common extraction noise from slide PDFs.

    Handles lines like ``QQuuáá ttrrììnnhh`` by collapsing doubled glyphs, removes
    NULL characters, trims excessive whitespace, and drops near-duplicate lines
    that often come from decorative title layers in PowerPoint-exported PDFs.
    """
    lines: list[str] = []
    recent_keys: list[str] = []

    for raw_line in text.replace("\x00", "\n").splitlines():
        line = _normalize_line(raw_line)
        if not line:
            continue

        key = _line_key(line)
        if key and key in recent_keys:
            continue

        lines.append(line)
        if key:
            recent_keys.append(key)
            recent_keys = recent_keys[-8:]

    return "\n".join(lines).strip()


def _normalize_line(line: str) -> str:
    stripped = line.strip()
    if not stripped:
        return ""
    stripped = _collapse_doubled_glyph_line(stripped)
    stripped = re.sub(r"\s+", " ", stripped).strip()
    stripped = re.sub(r"\s+([,.;:])", r"\1", stripped)
    return stripped


def _collapse_doubled_glyph_line(line: str) -> str:
    compact = "".join(char for char in line if not char.isspace())
    if len(compact) < 4:
        return line

    pair_count = 0
    duplicated_pairs = 0
    for index in range(0, len(compact) - 1, 2):
        pair_count += 1
        if compact[index] == compact[index + 1]:
            duplicated_pairs += 1

    if pair_count == 0 or duplicated_pairs / pair_count < 0.55:
        return line

    collapsed: list[str] = []
    index = 0
    while index < len(line):
        char = line[index]
        if index + 1 < len(line) and line[index + 1] == char and not char.isspace():
            collapsed.append(char)
            index += 2
            continue
        collapsed.append(char)
        index += 1
    return "".join(collapsed)


def _line_key(line: str) -> str:
    normalized = unicodedata.normalize("NFD", line.lower())
    normalized = "".join(char for char in normalized if unicodedata.category(char) != "Mn")
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def _infer_title(text: str) -> str | None:
    for line in text.splitlines():
        cleaned = line.strip()
        if not cleaned:
            continue
        if len(cleaned) <= 120:
            return cleaned
    return None


def _render_slide(slide: SlideBlock) -> str:
    prefix = f"SLIDE page={slide.page_number}" if slide.page_number is not None else "SLIDE"
    if slide.title:
        prefix += f" title={slide.title}"
    return f"{prefix}\n{slide.content}".strip()


def _join_blocks(left: str, right: str) -> str:
    if not left.strip():
        return right.strip()
    if not right.strip():
        return left.strip()
    return f"{left.strip()}\n\n{right.strip()}"


def _make_slide_chunk(blocks: list[SlideBlock], content: str, index: int) -> dict[str, Any]:
    page_numbers = [block.page_number for block in blocks if block.page_number is not None]
    return {
        "chunk_index": index,
        "content": content.strip(),
        "metadata": {
            "chunk_type": "slide_group",
            "chunk_mode": "slide_aware",
            "page_numbers": page_numbers,
            "page_start": min(page_numbers) if page_numbers else None,
            "page_end": max(page_numbers) if page_numbers else None,
            "slide_titles": [block.title for block in blocks if block.title],
            "start_char": min((block.start_char for block in blocks), default=0),
            "end_char": max((block.end_char for block in blocks), default=len(content)),
        },
    }


def _chunk_plain_text(
    text: str,
    *,
    chunk_size: int,
    chunk_overlap: int,
) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    start = 0
    while start < len(text):
        target_end = min(start + chunk_size, len(text))
        end = _find_split_boundary(text[start:target_end], chunk_size)
        if end <= 0:
            end = target_end - start
        absolute_end = start + end
        content = text[start:absolute_end].strip()
        if content:
            chunks.append(
                {
                    "chunk_index": len(chunks),
                    "content": content,
                    "metadata": {
