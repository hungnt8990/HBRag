"""Generic table-aware chunking for HBRag.

Detects tables in parsed text (from PDF or DOCX), splits into per-row chunks
preserving full row context, extracts entities, and generates entity summary
chunks. No column names or domain schemas are hard-coded.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# --- Table detection patterns (generic, no domain assumptions) ---
# Rows with pipe separators (from DOCX table rendering).
_PIPE_LINE = re.compile(r"^[^|]+(\|[^|]+){1,}$")
# Rows starting with ordinal-like numbering: "1.", "2.", "a)", "- "
_ORDINAL_LINE = re.compile(r"^\s*(\d+[\.\):]|[a-z][\.\)]|\-)\s+")
# Capitalized proper noun clusters (Vietnamese names, organizations).
_ENTITY_PATTERN = re.compile(
    r"\b(?:[A-ZÀÁẠẢÃÂẦẤẬẨẪĂẰẮẶẲẴÈÉẸẺẼÊỀẾỆỂỄÌÍỊỈĨÒÓỌỎÕÔỒỐỘỔỖƠỜỚỢỞỠÙÚỤỦŨƯỪỨỰỬỮỲÝỴỶỸĐ]"
    r"[a-zàáạảãâầấậẩẫăằắặẳẵèéẹẻẽêềếệểễìíịỉĩòóọỏõôồốộổỗơờớợởỡùúụủũưừứựửữỳýỵỷỹđ]*"
    r"(?:\s+[A-ZÀÁẠẢÃÂẦẤẬẨẪĂẰẮẶẲẴÈÉẸẺẼÊỀẾỆỂỄÌÍỊỈĨÒÓỌỎÕÔỒỐỘỔỖƠỜỚỢỞỠÙÚỤỦŨƯỪỨỰỬỮỲÝỴỶỸĐ]"
    r"[a-zàáạảãâầấậẩẫăằắặẳẵèéẹẻẽêềếệểễìíịỉĩòóọỏõôồốộổỗơờớợởỡùúụủũưừứựửữỳýỵỷỹđ]*){1,5})"
)
# Short all-caps tokens (abbreviations, org codes).
_ABBREV_PATTERN = re.compile(r"\b[A-ZĐÀÁẠẢÃÂẦẤẬẨẪ]{2,10}\b")

MIN_TABLE_ROWS = 3
PIPE_SEPARATOR = " | "
ENTITY_MIN_ROWS = 2  # Minimum rows for entity summary generation.


@dataclass
class TableRow:
    """A single row from a detected table, with column values."""

    headers: list[str]
    values: list[str]
    row_index: int
    table_title: str | None = None
    source_page: int | None = None

    @property
    def content_text(self) -> str:
        parts: list[str] = []
        if self.table_title:
            parts.append(f"Bảng: {self.table_title}")
        parts.append(f"Hàng {self.row_index}:")
        for header, value in zip(self.headers, self.values, strict=False):
            if value.strip():
                parts.append(f"  {header}: {value}")
        return "\n".join(parts)


@dataclass
class TableBlock:
    """A detected table region in parsed text."""

    headers: list[str]
    rows: list[list[str]]
    title: str | None = None
    start_char: int = 0
    end_char: int = 0
    source_page: int | None = None


@dataclass
class EntityIndex:
    """Inverted index: entity name → list of row chunk indices."""

    entities: dict[str, list[int]] = field(default_factory=dict)

    def add(self, entity: str, chunk_index: int) -> None:
        normalized = entity.strip()
        if not normalized:
            return
        self.entities.setdefault(normalized, []).append(chunk_index)


# --- Table Detection ---


def detect_tables_in_text(text: str) -> list[TableBlock]:
    """Detect table-like structures in parsed text. Generic, no domain assumptions."""
    tables: list[TableBlock] = []

    # Strategy 1: pipe-separated tables (from DOCX parser or formatted text).
    tables.extend(_detect_pipe_tables(text))

    # Strategy 2: whitespace-aligned columns (common in PDF text extraction).
    if not tables:
        tables.extend(_detect_aligned_tables(text))

    return tables


def _detect_pipe_tables(text: str) -> list[TableBlock]:
    """Detect tables using pipe '|' as column separator."""
    lines = text.splitlines()
    tables: list[TableBlock] = []
    current_block: list[tuple[int, str]] = []  # (line_idx, line)

    for idx, line in enumerate(lines):
        if _PIPE_LINE.match(line.strip()):
            current_block.append((idx, line))
        else:
            if len(current_block) >= MIN_TABLE_ROWS:
                tables.append(_pipe_block_to_table(text, current_block))
            current_block = []

    if len(current_block) >= MIN_TABLE_ROWS:
        tables.append(_pipe_block_to_table(text, current_block))

    return tables


def _pipe_block_to_table(
    full_text: str,
    block: list[tuple[int, str]],
) -> TableBlock:
    """Convert a block of pipe-separated lines into a TableBlock."""
    all_lines = full_text.splitlines()
    first_idx = block[0][0]

    # Attempt to find a title line immediately above the table.
    title = None
    if first_idx > 0:
        candidate = all_lines[first_idx - 1].strip()
        if candidate and not _PIPE_LINE.match(candidate) and len(candidate) < 200:
            title = candidate

    # First pipe line is assumed to be the header.
    header_line = block[0][1]
    headers = [cell.strip() for cell in header_line.split("|")]
    headers = [h if h else f"Cột {i + 1}" for i, h in enumerate(headers)]

    rows: list[list[str]] = []
    for _, line in block[1:]:
        cells = [cell.strip() for cell in line.split("|")]
        # Pad to header length.
        while len(cells) < len(headers):
            cells.append("")
        rows.append(cells[: len(headers)])

    start_char = full_text.find(block[0][1])
    end_char = full_text.find(block[-1][1]) + len(block[-1][1])

    return TableBlock(
        headers=headers,
        rows=rows,
        title=title,
        start_char=max(0, start_char),
        end_char=min(len(full_text), end_char),
    )


def _detect_aligned_tables(text: str) -> list[TableBlock]:
    """Detect tables from PDF-extracted text using alignment heuristics.

    Looks for blocks of consecutive short lines that share a consistent
    multi-column structure separated by 2+ spaces or tabs.
    """
    lines = text.splitlines()
    tables: list[TableBlock] = []
    multi_col_re = re.compile(r"\s{2,}|\t")

    current_block: list[tuple[int, str]] = []
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            if len(current_block) >= MIN_TABLE_ROWS:
                table = _aligned_block_to_table(text, current_block, multi_col_re)
                if table:
                    tables.append(table)
            current_block = []
            continue

        # Line looks tabular if it has multiple columns (2+ space gaps).
        parts = [p for p in multi_col_re.split(stripped) if p.strip()]
        if len(parts) >= 2:
            current_block.append((idx, line))
        else:
            if len(current_block) >= MIN_TABLE_ROWS:
                table = _aligned_block_to_table(text, current_block, multi_col_re)
                if table:
                    tables.append(table)
            current_block = []

    if len(current_block) >= MIN_TABLE_ROWS:
        table = _aligned_block_to_table(text, current_block, multi_col_re)
        if table:
            tables.append(table)

    return tables


def _aligned_block_to_table(
    full_text: str,
    block: list[tuple[int, str]],
    separator_re: re.Pattern[str],
) -> TableBlock | None:
    """Try to interpret an aligned block as a table."""
    parsed_rows: list[list[str]] = []
    for _, line in block:
        cells = [p.strip() for p in separator_re.split(line.strip()) if p.strip()]
        parsed_rows.append(cells)

    if not parsed_rows:
        return None

    # Infer column count from the most common row width.
    widths = [len(row) for row in parsed_rows]
    most_common_width = max(set(widths), key=widths.count)
    if most_common_width < 2:
        return None

    # Use the first row as header.
    headers = parsed_rows[0]
    while len(headers) < most_common_width:
        headers.append(f"Cột {len(headers) + 1}")

    rows: list[list[str]] = []
    for row in parsed_rows[1:]:
        padded = row + [""] * (most_common_width - len(row))
        rows.append(padded[:most_common_width])

    if len(rows) < 2:
        return None

    start_char = full_text.find(block[0][1])
    end_char = full_text.find(block[-1][1]) + len(block[-1][1])

    return TableBlock(
        headers=headers,
        rows=rows,
        title=None,
        start_char=max(0, start_char),
        end_char=min(len(full_text), end_char),
    )


# --- Row Chunk Generation ---


def table_to_row_chunks(
    table: TableBlock,
    *,
    start_index: int = 0,
) -> list[dict[str, Any]]:
    """Convert a TableBlock into per-row chunk dicts (content + metadata)."""
    chunks: list[dict[str, Any]] = []
    for row_idx, row_values in enumerate(table.rows):
        row_obj = TableRow(
            headers=table.headers,
            values=row_values,
            row_index=row_idx + 1,
            table_title=table.title,
            source_page=table.source_page,
        )
        content = row_obj.content_text
        if not content.strip():
            continue
        chunks.append(
            {
                "chunk_index": start_index + len(chunks),
                "content": content,
                "metadata": {
                    "chunk_type": "table_row",
                    "chunk_mode": "table_aware",
                    "table_title": table.title,
                    "headers": table.headers,
                    "row_index": row_obj.row_index,
                    "source_page": table.source_page,
                    "start_char": table.start_char,
                    "end_char": table.end_char,
                },
            }
        )
    return chunks


# --- Entity Extraction ---


def extract_entities_from_text(text: str) -> list[str]:
    """Extract entity candidates from text using generic patterns.

    Finds capitalized proper noun clusters (Vietnamese names, organizations)
    and short all-caps abbreviations. No domain-specific hard-coding.
    """
    entities: set[str] = set()

    for match in _ENTITY_PATTERN.finditer(text):
        candidate = match.group(0).strip()
        # Filter out very short matches or common Vietnamese stopwords.
        if len(candidate) >= 4 and not _is_common_word(candidate):
            entities.add(candidate)

    for match in _ABBREV_PATTERN.finditer(text):
        candidate = match.group(0).strip()
        if len(candidate) >= 2:
            entities.add(candidate)

    return sorted(entities)


def _is_common_word(word: str) -> bool:
    """Filter out common Vietnamese words that look like proper nouns."""
    common = {
        "Theo", "Tổng", "Công", "Phòng", "Điều", "Chương", "Bảng",
        "Hàng", "Nội", "Đơn", "Người", "Giám", "Trưởng", "Phụ",
        "Trách", "Nhiệm", "Thực", "Hiện", "Liên", "Quan", "Tham",
        "Không", "Được", "Trong", "Những", "Quy", "Định",
    }
    return word.split()[0] in common if word else False


# --- Entity Index & Summary ---


def build_entity_index(row_chunks: list[dict[str, Any]]) -> EntityIndex:
    """Build an inverted index: entity → list of chunk indices."""
    index = EntityIndex()
    for chunk in row_chunks:
        entities = extract_entities_from_text(chunk["content"])
        for entity in entities:
            index.add(entity, chunk["chunk_index"])
    return index


def generate_entity_summary_chunks(
    row_chunks: list[dict[str, Any]],
    entity_index: EntityIndex,
    *,
    start_index: int = 0,
    min_rows: int = ENTITY_MIN_ROWS,
) -> list[dict[str, Any]]:
    """Generate entity summary chunks for entities appearing in multiple rows."""
    summaries: list[dict[str, Any]] = []
    chunk_by_index = {chunk["chunk_index"]: chunk for chunk in row_chunks}

    for entity, indices in sorted(entity_index.entities.items()):
        if len(indices) < min_rows:
            continue

        # Deduplicate indices.
        unique_indices = sorted(set(indices))
        related_chunks = [
            chunk_by_index[idx] for idx in unique_indices if idx in chunk_by_index
        ]
        if not related_chunks:
            continue

        # Build summary content.
        lines = [f"Thực thể: {entity}", "Các hàng bảng liên quan:"]
        source_pages: set[int] = set()
        table_title = None
        for chunk in related_chunks:
            lines.append(f"- {chunk['content']}")
            meta = chunk.get("metadata", {})
            if meta.get("source_page"):
                source_pages.add(meta["source_page"])
            if meta.get("table_title") and not table_title:
                table_title = meta["table_title"]

        content = "\n".join(lines)
        summaries.append(
            {
                "chunk_index": start_index + len(summaries),
                "content": content,
                "metadata": {
                    "chunk_type": "entity_summary",
                    "chunk_mode": "table_aware",
                    "entity_name": entity,
                    "row_count": len(related_chunks),
                    "source_table": table_title,
                    "source_pages": sorted(source_pages) if source_pages else [],
                },
            }
        )

    return summaries


# --- Full Pipeline ---


def table_aware_chunk_text(
    text: str,
    *,
    chunk_size: int = 1000,
    chunk_overlap: int = 150,
) -> tuple[list[dict[str, Any]], EntityIndex]:
    """Full table-aware chunking pipeline.

    1. Detect tables in text.
    2. Create table_row chunks for each row.
    3. For text outside tables, create recursive text chunks.
    4. Build entity index from row chunks.
    5. Generate entity_summary chunks.

    Returns (all_chunks, entity_index).
    """
    from app.services.chunking_service import RecursiveTextChunker

    tables = detect_tables_in_text(text)
    all_chunks: list[dict[str, Any]] = []

    # Track table regions to exclude from text chunking.
    table_regions: list[tuple[int, int]] = []

    # Generate row chunks from tables.
    for table in tables:
        row_chunks = table_to_row_chunks(table, start_index=len(all_chunks))
        all_chunks.extend(row_chunks)
        table_regions.append((table.start_char, table.end_char))

    # Chunk non-table text with recursive chunker.
    non_table_text_parts: list[tuple[int, str]] = []
    prev_end = 0
    for start, end in sorted(table_regions):
        if start > prev_end:
            non_table_text_parts.append((prev_end, text[prev_end:start]))
        prev_end = max(prev_end, end)
    if prev_end < len(text):
        non_table_text_parts.append((prev_end, text[prev_end:]))

    chunker = RecursiveTextChunker(chunk_size=chunk_size, chunk_overlap=chunk_overlap)
    for offset, part in non_table_text_parts:
        if not part.strip():
            continue
        text_chunks = chunker.chunk_text(part)
        for tc in text_chunks:
            all_chunks.append(
                {
                    "chunk_index": len(all_chunks),
                    "content": tc.content,
                    "metadata": {
                        "chunk_type": "text",
                        "chunk_mode": "table_aware",
                        "start_char": offset + tc.start_char,
                        "end_char": offset + tc.end_char,
                    },
                }
            )

    # Build entity index from row chunks only.
    row_chunks = [c for c in all_chunks if c.get("metadata", {}).get("chunk_type") == "table_row"]
    entity_index = build_entity_index(row_chunks)

    # Generate entity summaries.
    summaries = generate_entity_summary_chunks(
        row_chunks, entity_index, start_index=len(all_chunks)
    )
    all_chunks.extend(summaries)

    # Reassign sequential chunk indices.
    for idx, chunk in enumerate(all_chunks):
        chunk["chunk_index"] = idx

    return all_chunks, entity_index
