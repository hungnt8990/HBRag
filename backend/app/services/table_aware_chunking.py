"""Generic table-aware chunking for parsed PDF/DOCX content."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.services.parsers.table_serialization import (
    build_table_row_record,
    build_table_title_record,
    infer_headers,
)
from app.services.table_relationships import (
    ALLOWED_DEPARTMENTS,
    build_entity_profile_chunks,
    is_valid_staff_name,
    normalize_metadata_value,
    parse_staff_members,
    parse_technology_area_rows_from_text,
    row_to_chunk,
)

_PIPE_LINE = re.compile(r"^[^|]+(?:\|[^|]+){1,}$")
_TABLE_TITLE_LINE = re.compile(
    r"^TABLE_TITLE\s+table_id=(?P<table_id>\S+)(?:\s+page=(?P<page>\d+))?:\s*(?P<title>.*)$"
)
_TABLE_ROW_LINE = re.compile(
    r"^TABLE_ROW\s+table_id=(?P<table_id>\S+)(?:\s+page=(?P<page>\d+))?\s+row=(?P<row>\d+)\s+\|\s*(?P<fields>.+)$"
)
_ABBREV_PATTERN = re.compile(r"\b[A-Z0-9][A-Z0-9._/-]{1,}\b")
_ENTITY_STOP_WORDS = {
    "Cac",
    "C�c",
    "Danh",
    "ENTITY_SUMMARY",
    "Ph�ng",
    "Phong",
    "STT",
    "TABLE_HEADER",
    "TABLE_ROW",
    "TABLE_TITLE",
    "M?ng",
    "Mang",
    "Nhom",
    "Nh�m",
}

_ALIGNED_SEGMENT_RE = re.compile(r'\S.*?(?=\s{2,}|\s*$)')

MIN_TABLE_ROWS = 2
PIPE_SEPARATOR = " | "
ENTITY_MIN_ROWS = 2
TABLE_HEADER_PREFIX = "TABLE_HEADER"
STAFF_AREA_TABLE_TITLE = "Danh sách nhân sự phụ trách từng mảng công nghệ lõi"
STAFF_AREA_LAYOUT_TABLE_ID = "staff_area_layout"
STAFF_AREA_DEPARTMENTS = ("KTMVT", "PTUD", "PM", "VH", "ATTT")
STAFF_AREA_ANCHOR_RE = re.compile(
    r"^(?P<stt>\d{1,2})\s+(?:(?P<area>.*?)\s+)?(?P<department>KTMVT|PTUD|PM|VH|ATTT)\b(?P<tail>.*)$"
)
STAFF_AREA_ITEM_RE = re.compile(
    r"(?:^|\s)(?P<index>\d{1,2})\.\s+(?P<name>.+?)(?=(?:\s+\d{1,2}\.\s+)|$)"
)
STAFF_AREA_TABLE_MARKER_RE = re.compile(
    r"danh\s+sach\s+nhan\s+su\s+phu\s+trach\s+tung\s+mang\s+cong\s+nghe\s+loi",
    flags=re.IGNORECASE,
)
STAFF_AREA_NOTE_MARKER_RE = re.compile(
    r"diem\s+can\s+luu\s+y|điểm\s+cần\s+lưu\s+ý",
    flags=re.IGNORECASE,
)
SUMMARY_ENTITY_STOPLIST = {
    "ai",
    "rag",
    "noi bo",
    "dung chung",
    "cong nghe",
    "nhan su",
    "nhiem vu",
}


@dataclass
class TableRow:
    headers: list[str]
    values: list[str]
    row_index: int
    table_id: str
    table_title: str | None = None
    page_number: int | None = None

    @property
    def content_text(self) -> str:
        return build_table_row_record(
            table_id=self.table_id,
            row_index=self.row_index,
            headers=self.headers,
            values=self.values,
            page_number=self.page_number,
        )


@dataclass
class TableBlock:
    table_id: str
    headers: list[str]
    rows: list[list[str]]
    title: str | None = None
    start_char: int = 0
    end_char: int = 0
    page_number: int | None = None
    has_header: bool = True


@dataclass
class EntityIndex:
    entities: dict[str, list[int]] = field(default_factory=dict)

    def add(self, entity: str, chunk_index: int) -> None:
        normalized = entity.strip()
        if not normalized:
            return
        self.entities.setdefault(normalized, []).append(chunk_index)


def detect_tables_in_text(text: str) -> list[TableBlock]:
    tables = _detect_serialized_tables(text)
    if tables:
        return tables

    pipe_tables = _detect_pipe_tables(text)
    if pipe_tables:
        return pipe_tables

    return _detect_aligned_tables(text)


def _detect_serialized_tables(text: str) -> list[TableBlock]:
    lines = text.splitlines(keepends=True)
    tables_by_id: dict[str, TableBlock] = {}
    order: list[str] = []
    offset = 0

    for raw_line in lines:
        line = raw_line.rstrip("\r\n")
        title_match = _TABLE_TITLE_LINE.match(line)
        if title_match:
            table_id = title_match.group("table_id")
            table = tables_by_id.get(table_id)
            if table is None:
                table = TableBlock(
                    table_id=table_id,
                    headers=[],
                    rows=[],
                    title=title_match.group("title").strip() or None,
                    start_char=offset,
                    end_char=offset + len(line),
                    page_number=_to_int(title_match.group("page")),
                    has_header=True,
                )
                tables_by_id[table_id] = table
                order.append(table_id)
            else:
                table.title = title_match.group("title").strip() or table.title
                table.page_number = table.page_number or _to_int(title_match.group("page"))
                table.start_char = min(table.start_char, offset)
                table.end_char = max(table.end_char, offset + len(line))
            offset += len(raw_line)
            continue

        row_match = _TABLE_ROW_LINE.match(line)
        if row_match:
            table_id = row_match.group("table_id")
            headers, values = _parse_serialized_row_fields(row_match.group("fields"))
            table = tables_by_id.get(table_id)
            if table is None:
                table = TableBlock(
                    table_id=table_id,
                    headers=headers,
                    rows=[],
                    start_char=offset,
                    end_char=offset + len(line),
                    page_number=_to_int(row_match.group("page")),
                    has_header=not all(header.startswith("cell_") for header in headers),
                )
                tables_by_id[table_id] = table
                order.append(table_id)
            elif not table.headers:
                table.headers = headers
            table.rows.append(values)
            table.start_char = min(table.start_char, offset)
            table.end_char = max(table.end_char, offset + len(line))
            table.page_number = table.page_number or _to_int(row_match.group("page"))
            offset += len(raw_line)
            continue

        offset += len(raw_line)

    return [tables_by_id[table_id] for table_id in order if tables_by_id[table_id].rows]


def _parse_serialized_row_fields(fields: str) -> tuple[list[str], list[str]]:
    headers: list[str] = []
    values: list[str] = []
    for column in fields.split(PIPE_SEPARATOR):
        label, separator, value = column.partition(":")
        if separator:
            headers.append(label.strip())
            values.append(value.strip())
        else:
            headers.append(f"cell_{len(headers) + 1}")
            values.append(column.strip())
    return headers, values


def _detect_pipe_tables(text: str) -> list[TableBlock]:
    lines = text.splitlines()
    tables: list[TableBlock] = []
    current_block: list[tuple[int, str]] = []

    for index, line in enumerate(lines):
        if _PIPE_LINE.match(line.strip()):
            current_block.append((index, line))
            continue
        if len(current_block) >= MIN_TABLE_ROWS:
            tables.append(_pipe_block_to_table(text, current_block, table_index=len(tables) + 1))
        current_block = []

    if len(current_block) >= MIN_TABLE_ROWS:
        tables.append(_pipe_block_to_table(text, current_block, table_index=len(tables) + 1))

    return tables


def _pipe_block_to_table(
    full_text: str,
    block: list[tuple[int, str]],
    *,
    table_index: int,
) -> TableBlock:
    all_lines = full_text.splitlines()
    rows = [[cell.strip() for cell in line.split("|")] for _, line in block]
    headers, data_rows, has_header = infer_headers(rows)
    first_line_index = block[0][0]
    title = None
    if first_line_index > 0:
        candidate = all_lines[first_line_index - 1].strip()
        if candidate and not _PIPE_LINE.match(candidate):
            title = candidate

    start_char = full_text.find(block[0][1])
    end_char = full_text.find(block[-1][1]) + len(block[-1][1])
    return TableBlock(
        table_id=f"pipe_{table_index}",
        headers=headers,
        rows=data_rows,
        title=title or None,
        start_char=max(0, start_char),
        end_char=min(len(full_text), end_char),
        has_header=has_header,
    )


def _detect_aligned_tables(text: str) -> list[TableBlock]:
    lines = text.splitlines()
    tables: list[TableBlock] = []
    separator_re = re.compile(r"\s{2,}|\t")
    current_block: list[tuple[int, str]] = []
    gap_lines = 0

    def flush_current() -> None:
        nonlocal current_block, gap_lines
        if len(current_block) >= MIN_TABLE_ROWS:
            table = _aligned_block_to_table(
                text,
                current_block,
                separator_re,
                table_index=len(tables) + 1,
            )
            if table is not None:
                tables.append(table)
        current_block = []
        gap_lines = 0

    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            if current_block:
                gap_lines += 1
                if gap_lines > 1:
                    flush_current()
            continue

        segments = _split_aligned_segments(line)
        if len(segments) >= 2:
            current_block.append((index, line))
            gap_lines = 0
            continue

        if current_block and line[: len(line) - len(line.lstrip())]:
            current_block.append((index, line))
            gap_lines = 0
            continue

        flush_current()

    if len(current_block) >= MIN_TABLE_ROWS:
        flush_current()

    return tables


def _aligned_block_to_table(
    full_text: str,
    block: list[tuple[int, str]],
    separator_re: re.Pattern[str],
    *,
    table_index: int,
) -> TableBlock | None:
    segmented_rows = [
        (line_index, line, _split_aligned_segments(line))
        for line_index, line in block
        if line.strip()
    ]
    segmented_rows = [row for row in segmented_rows if row[2]]
    if len(segmented_rows) < MIN_TABLE_ROWS:
        return None

    column_starts = _infer_aligned_column_starts(
        [segments for _, _, segments in segmented_rows]
    )
    if column_starts:
        data_rows, headers, has_header = _aligned_segments_to_rows(
            segmented_rows,
            column_starts,
        )
    else:
        parsed_rows = [
            [part.strip() for part in separator_re.split(line.strip()) if part.strip()]
            for _, line in block
        ]
        parsed_rows = [row for row in parsed_rows if row]
        headers, data_rows, has_header = infer_headers(parsed_rows)

    if not headers or len(data_rows) < 1:
        return None

    start_char = full_text.find(block[0][1])
    end_char = full_text.find(block[-1][1]) + len(block[-1][1])
    return TableBlock(
        table_id=f"aligned_{table_index}",
        headers=headers,
        rows=data_rows,
        start_char=max(0, start_char),
        end_char=min(len(full_text), end_char),
        has_header=has_header,
    )


def _split_aligned_segments(line: str) -> list[tuple[int, str]]:
    return [
        (match.start(), match.group(0).strip())
        for match in _ALIGNED_SEGMENT_RE.finditer(line.rstrip())
        if match.group(0).strip()
    ]

def _infer_aligned_column_starts(
    segmented_rows: list[list[tuple[int, str]]],
) -> list[int]:
    counts = [len(segments) for segments in segmented_rows if len(segments) >= 2]
    if not counts:
        return []

    width = max(counts)
    full_rows = [segments for segments in segmented_rows if len(segments) == width]
    if not full_rows:
        return []

    starts = [
        _median_int([segments[column_index][0] for segments in full_rows])
        for column_index in range(width)
    ]
    if any(right <= left for left, right in zip(starts, starts[1:], strict=False)):
        return []
    return starts

def _median_int(values: list[int]) -> int:
    ordered = sorted(values)
    return ordered[len(ordered) // 2]

def _aligned_segments_to_rows(
    segmented_rows: list[tuple[int, str, list[tuple[int, str]]]],
    column_starts: list[int],
) -> tuple[list[list[str]], list[str], bool]:
    assigned_rows = [
        _segments_to_cells(segments, column_starts)
        for _, _, segments in segmented_rows
    ]
    if not assigned_rows:
        return [], [], False

    header_candidates = [row for row in assigned_rows[1:] if row[0]]
    has_header = infer_headers([assigned_rows[0], *header_candidates])[2]
    headers = [f'cell_{index + 1}' for index in range(len(column_starts))]
    data_start = 0
    if has_header:
        headers = list(assigned_rows[0])
        data_start = 1
        while data_start < len(assigned_rows):
            row = assigned_rows[data_start]
            if row[0]:
                break
            non_empty_columns = [index for index, value in enumerate(row) if value]
            if non_empty_columns and max(non_empty_columns) < len(row) - 1:
                _append_aligned_cells(headers, row)
                data_start += 1
                continue
            break

    rows = _merge_aligned_data_rows(assigned_rows[data_start:])
    if not has_header:
        headers, rows, has_header = infer_headers(rows)
    return rows, headers, has_header

def _segments_to_cells(
    segments: list[tuple[int, str]],
    column_starts: list[int],
) -> list[str]:
    cells = [''] * len(column_starts)
    boundaries = [
        (left + right) / 2
        for left, right in zip(column_starts, column_starts[1:], strict=False)
    ]
    for start, value in segments:
        column_index = 0
        while column_index < len(boundaries) and start >= boundaries[column_index]:
            column_index += 1
        cells[column_index] = ' '.join(part for part in (cells[column_index], value) if part)
    return cells

def _merge_aligned_data_rows(rows: list[list[str]]) -> list[list[str]]:
    merged: list[list[str]] = []
    current: list[str] | None = None
    pending: list[list[str]] = []

    for row in rows:
        if not any(row):
            continue

        starts_new_row = bool(row[0])
        if starts_new_row:
            if current is not None:
                merged.append(current)
            current = [''] * len(row)
            for pending_row in pending:
                _append_aligned_cells(current, pending_row)
            pending = []
            _append_aligned_cells(current, row)
            continue

        starts_next_row_content = current is not None and _has_nonterminal_cell(row)
        if starts_next_row_content:
            merged.append(current)
            current = None
            pending = [row]
            continue

        if current is None:
            pending.append(row)
            continue
        _append_aligned_cells(current, row)

    if current is not None:
        merged.append(current)

    return [row for row in merged if any(cell.strip() for cell in row)]

def _append_aligned_cells(target: list[str], source: list[str]) -> None:
    for index, value in enumerate(source):
        if not value:
            continue
        target[index] = ' '.join(part for part in (target[index], value) if part)

def _has_nonterminal_cell(row: list[str]) -> bool:
    if len(row) <= 1:
        return any(row)
    return any(value for value in row[1:-1])

def table_to_row_chunks(
    table: TableBlock,
    *,
    start_index: int = 0,
) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    for row_index, row_values in enumerate(table.rows, start=1):
        row_obj = TableRow(
            headers=table.headers,
            values=row_values,
            row_index=row_index,
            table_id=table.table_id,
            table_title=table.title,
            page_number=table.page_number,
        )
        chunks.append(
            {
                "chunk_index": start_index + len(chunks),
                "content": row_obj.content_text,
                "metadata": {
                    "chunk_type": "table_row",
                    "chunk_mode": "table_aware",
                    "chunk_overlap": 0,
                    "overlap_applied": False,
                    "table_id": table.table_id,
                    "table_title": table.title,
                    "headers": list(table.headers),
                    "page_number": table.page_number,
                    "row_index": row_index,
                    "row_start": row_index,
                    "row_end": row_index,
                    "start_char": table.start_char,
                    "end_char": table.end_char,
                },
            }
        )
    return chunks


def table_to_supporting_chunks(
    table: TableBlock,
    *,
    start_index: int = 0,
    chunk_size: int = 1000,
) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    if table.title:
        title_record = build_table_title_record(
            table_id=table.table_id,
            title=table.title,
            page_number=table.page_number,
        )
        if title_record:
            chunks.append(
                {
                    "chunk_index": start_index + len(chunks),
                    "content": title_record,
                    "metadata": {
                        "chunk_type": "table_title",
                        "chunk_mode": "table_aware",
                        "table_id": table.table_id,
                        "table_title": table.title,
                        "headers": list(table.headers),
                        "page_number": table.page_number,
                        "start_char": table.start_char,
                        "end_char": table.end_char,
                    },
                }
            )

    if table.headers:
        header_line = f"{TABLE_HEADER_PREFIX} table_id={table.table_id}"
        if table.page_number is not None:
            header_line += f" page={table.page_number}"
        header_line += " | " + " | ".join(table.headers)
        chunks.append(
            {
                "chunk_index": start_index + len(chunks),
                "content": header_line,
                "metadata": {
                    "chunk_type": "table_header",
                    "chunk_mode": "table_aware",
                    "table_id": table.table_id,
                    "table_title": table.title,
                    "headers": list(table.headers),
                    "page_number": table.page_number,
                    "start_char": table.start_char,
                    "end_char": table.end_char,
                },
            }
        )

    chunks.extend(
        _table_to_block_chunks(
            table,
            start_index=start_index + len(chunks),
            chunk_size=chunk_size,
        )
    )
    return chunks


def _table_to_block_chunks(
    table: TableBlock,
    *,
    start_index: int,
    chunk_size: int,
) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    prefix_lines: list[str] = []
    if table.title:
        title_line = build_table_title_record(
            table_id=table.table_id,
            title=table.title,
            page_number=table.page_number,
        )
        if title_line:
            prefix_lines.append(title_line)
    if table.headers:
        header_line = f"{TABLE_HEADER_PREFIX} table_id={table.table_id}"
        if table.page_number is not None:
            header_line += f" page={table.page_number}"
        header_line += " | " + " | ".join(table.headers)
        prefix_lines.append(header_line)
    prefix = "\n".join(prefix_lines)

    row_chunks = table_to_row_chunks(table)
    current_rows: list[str] = []
    row_start = 1
    current_length = len(prefix)
    for row_chunk in row_chunks:
        row_line = row_chunk["content"]
        next_length = current_length + len(row_line) + (1 if current_rows or prefix else 0)
        if current_rows and next_length > chunk_size:
            blocks.append(
                _make_table_block_chunk(
                    table=table,
                    start_index=start_index + len(blocks),
                    prefix=prefix,
                    row_lines=current_rows,
                    row_start=row_start,
                )
            )
            row_start += len(current_rows)
            current_rows = []
            current_length = len(prefix)
        current_rows.append(row_line)
        current_length += len(row_line) + 1

    if current_rows:
        blocks.append(
            _make_table_block_chunk(
                table=table,
                start_index=start_index + len(blocks),
                prefix=prefix,
                row_lines=current_rows,
                row_start=row_start,
            )
        )
    return blocks


def _make_table_block_chunk(
    *,
    table: TableBlock,
    start_index: int,
    prefix: str,
    row_lines: list[str],
    row_start: int,
) -> dict[str, Any]:
    content_lines = [prefix] if prefix else []
    content_lines.extend(row_lines)
    row_end = row_start + len(row_lines) - 1
    return {
        "chunk_index": start_index,
        "content": "\n".join(line for line in content_lines if line),
        "metadata": {
            "chunk_type": "table_block",
            "chunk_mode": "table_aware",
            "table_id": table.table_id,
            "table_title": table.title,
            "headers": list(table.headers),
            "page_number": table.page_number,
            "row_start": row_start,
            "row_end": row_end,
            "start_char": table.start_char,
            "end_char": table.end_char,
        },
    }


def extract_entities_from_text(text: str) -> list[str]:
    entities: set[str] = set()

    words = [_clean_token(token) for token in text.split()]
    current: list[str] = []
    for word in words:
        if _is_title_token(word):
            if current and word in _ENTITY_STOP_WORDS:
                candidate = " ".join(current)
                if len(current) >= 2 and not _is_common_word(candidate):
                    entities.add(candidate)
                current = []
                continue
            current.append(word)
            continue
        if len(current) >= 2:
            candidate = " ".join(current)
            if not _is_common_word(candidate):
                entities.add(candidate)
        current = []
    if len(current) >= 2:
        candidate = " ".join(current)
        if not _is_common_word(candidate):
            entities.add(candidate)

    for match in _ABBREV_PATTERN.finditer(text):
        candidate = match.group(0).strip()
        if len(candidate) >= 2 and candidate not in _ENTITY_STOP_WORDS:
            entities.add(candidate)

    return sorted(entities)


def _clean_token(token: str) -> str:
    return token.strip(" ,.;:()[]{}<>!?\"'`")


def _is_title_token(token: str) -> bool:
    if not token:
        return False
    return token[0].isupper() and any(char.isalpha() for char in token)


def _is_common_word(word: str) -> bool:
    common = {
        "Bang",
        "Can",
        "Cau",
        "Chuong",
        "Dieu",
        "Hang",
        "How",
        "Khi",
        "Mot",
        "Neu",
        "Noi",
        "Phan",
        "Table",
        "Theo",
        "This",
        "What",
    }
    first = word.split()[0] if word else ""
    return first in common


def build_entity_index(row_chunks: list[dict[str, Any]]) -> EntityIndex:
    index = EntityIndex()
    for chunk in row_chunks:
        for entity in extract_entities_from_text(chunk["content"]):
            index.add(entity, chunk["chunk_index"])
    return index


def generate_entity_summary_chunks(
    row_chunks: list[dict[str, Any]],
    entity_index: EntityIndex,
    *,
    start_index: int = 0,
    min_rows: int = ENTITY_MIN_ROWS,
    chunk_size: int = 1000,
) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    chunk_by_index = {chunk["chunk_index"]: chunk for chunk in row_chunks}

    for entity, indices in sorted(entity_index.entities.items()):
        unique_indices = sorted(set(indices))
        if len(unique_indices) < min_rows:
            continue

        related_chunks = [
            chunk_by_index[index]
            for index in unique_indices
            if index in chunk_by_index
        ]
        if not related_chunks:
            continue
        if not _is_allowed_entity_summary(entity, related_chunks):
            continue

        table_ids = sorted(
            {
                str(chunk.get("metadata", {}).get("table_id"))
                for chunk in related_chunks
                if chunk.get("metadata", {}).get("table_id") is not None
            }
        )
        source_pages = sorted(
            {
                int(page_number)
                for chunk in related_chunks
                if (page_number := chunk.get("metadata", {}).get("page_number")) is not None
            }
        )
        current_rows: list[dict[str, Any]] = []
        current_length = len(f"ENTITY_SUMMARY entity={entity}\nRows:")
        for chunk in related_chunks:
            row_line = f"- {chunk['content']}"
            next_length = current_length + len(row_line) + 1
            if current_rows and next_length > chunk_size:
                summaries.append(
                    _make_entity_summary_chunk(
                        entity=entity,
                        related_chunks=current_rows,
                        start_index=start_index + len(summaries),
                        total_rows=len(related_chunks),
                    )
                )
                current_rows = []
                current_length = len(f"ENTITY_SUMMARY entity={entity}\nRows:")
            current_rows.append(chunk)
            current_length += len(row_line) + 1

        if current_rows:
            summaries.append(
                _make_entity_summary_chunk(
                    entity=entity,
                    related_chunks=current_rows,
                    start_index=start_index + len(summaries),
                    total_rows=len(related_chunks),
                    table_ids=table_ids,
                    page_numbers=source_pages,
                )
            )

    return summaries

def _is_allowed_entity_summary(entity: str, related_chunks: list[dict[str, Any]]) -> bool:
    normalized = normalize_metadata_value(entity)
    if not normalized or normalized in SUMMARY_ENTITY_STOPLIST:
        return False
    if entity.upper() in ALLOWED_DEPARTMENTS:
        return any(
            str(chunk.get("metadata", {}).get("lead_department") or "").upper()
            == entity.upper()
            for chunk in related_chunks
        )

    first_token = normalized.split()[0] if normalized.split() else ""
    if first_token.upper() in ALLOWED_DEPARTMENTS:
        return False

    for chunk in related_chunks:
        for staff in chunk.get("metadata", {}).get("staff", []) or []:
            if not isinstance(staff, dict):
                continue
            if staff.get("entity_type") != "person":
                continue
            if normalize_metadata_value(str(staff.get("name") or "")) == normalized:
                return True

    return is_valid_staff_name(entity)


def _make_entity_summary_chunk(
    *,
    entity: str,
    related_chunks: list[dict[str, Any]],
    start_index: int,
    total_rows: int,
    table_ids: list[str] | None = None,
    page_numbers: list[int] | None = None,
) -> dict[str, Any]:
    if table_ids is None:
        table_ids = sorted(
            {
                str(chunk.get("metadata", {}).get("table_id"))
                for chunk in related_chunks
                if chunk.get("metadata", {}).get("table_id") is not None
            }
        )
    if page_numbers is None:
        page_numbers = sorted(
            {
                int(page_number)
                for chunk in related_chunks
                if (page_number := chunk.get("metadata", {}).get("page_number")) is not None
            }
        )
    lines = [f"ENTITY_SUMMARY entity={entity}", "Rows:"]
    for chunk in related_chunks:
        lines.append(f"- {chunk['content']}")
    return {
        "chunk_index": start_index,
        "content": "\n".join(lines),
        "metadata": {
            "chunk_type": "entity_summary",
            "chunk_mode": "table_aware",
            "entity_name": entity,
            "entity_name_normalized": normalize_metadata_value(entity),
            "entity_type": _entity_summary_type(entity),
            "row_count": len(related_chunks),
            "entity_total_rows": total_rows,
            "table_ids": table_ids,
            "page_numbers": page_numbers,
        },
    }

def _entity_summary_type(entity: str) -> str:
    if entity.upper() in ALLOWED_DEPARTMENTS:
        return "department"
    if is_valid_staff_name(entity):
        return "person"
    return "unknown"


def table_aware_chunk_text(
    text: str,
    *,
    chunk_size: int = 1000,
    chunk_overlap: int = 150,
) -> tuple[list[dict[str, Any]], EntityIndex]:
    from app.services.chunking_service import RecursiveTextChunker

    tables = detect_tables_in_text(text)
    all_chunks: list[dict[str, Any]] = []
    table_regions: list[tuple[int, int]] = []

    layout_row_chunks, layout_region = _staff_area_layout_row_chunks(text)
    use_staff_area_semantic_text = bool(layout_row_chunks)
    if layout_row_chunks:
        all_chunks.extend(layout_row_chunks)
        if layout_region is not None:
            table_regions.append(layout_region)
    else:
        relationship_rows = parse_technology_area_rows_from_text(
            text,
            table_id="staff_area_text",
        )
        all_chunks.extend(
            row_to_chunk(row, chunk_index=len(all_chunks)) for row in relationship_rows
        )

    for table in tables:
        supporting_chunks = table_to_supporting_chunks(
            table,
            start_index=len(all_chunks),
            chunk_size=chunk_size,
        )
        all_chunks.extend(supporting_chunks)

        row_chunks = table_to_row_chunks(table, start_index=len(all_chunks))
        all_chunks.extend(row_chunks)
        table_regions.append((table.start_char, table.end_char))

    non_table_text_parts: list[tuple[int, str]] = []
    previous_end = 0
    for start, end in sorted(table_regions):
        if start > previous_end:
            non_table_text_parts.append((previous_end, text[previous_end:start]))
        previous_end = max(previous_end, end)
    if table_regions and previous_end < len(text):
        non_table_text_parts.append((previous_end, text[previous_end:]))
    if not table_regions:
        non_table_text_parts.append((0, text))

    chunker = RecursiveTextChunker(chunk_size=chunk_size, chunk_overlap=chunk_overlap)
    for offset, part in non_table_text_parts:
        if not part.strip():
            continue
        if use_staff_area_semantic_text:
            semantic_chunks = _staff_area_semantic_text_chunks(part, offset=offset)
            if semantic_chunks:
                for chunk in semantic_chunks:
                    chunk["chunk_index"] = len(all_chunks)
                    all_chunks.append(chunk)
                continue
        for text_chunk in chunker.chunk_text(part):
            all_chunks.append(
                {
                    "chunk_index": len(all_chunks),
                    "content": text_chunk.content,
                    "metadata": {
                        "chunk_type": "text",
                        "chunk_mode": "table_aware",
                        "start_char": offset + text_chunk.start_char,
                        "end_char": offset + text_chunk.end_char,
                    },
                }
            )

    row_chunks = [
        chunk
        for chunk in all_chunks
        if chunk.get("metadata", {}).get("chunk_type") == "table_row"
    ]
    entity_index = build_entity_index(row_chunks)
    entity_profiles = build_entity_profile_chunks(row_chunks, start_index=len(all_chunks))
    all_chunks.extend(entity_profiles)

    summaries = generate_entity_summary_chunks(
        row_chunks,
        entity_index,
        start_index=len(all_chunks),
        chunk_size=chunk_size,
    )
    all_chunks.extend(summaries)

    for index, chunk in enumerate(all_chunks):
        chunk["chunk_index"] = index

    return all_chunks, entity_index


def _staff_area_layout_row_chunks(text: str) -> tuple[list[dict[str, Any]], tuple[int, int] | None]:
    lines_with_offsets = _lines_with_offsets(text)
    table_start = _find_staff_area_table_start(lines_with_offsets)
    if table_start is None:
        return [], None

    note_start = _find_staff_area_note_start(lines_with_offsets, table_start)
    table_end = note_start if note_start is not None else len(lines_with_offsets)
    table_lines = lines_with_offsets[table_start:table_end]
    chunks: list[dict[str, Any]] = []

    for sequence in _staff_area_numbered_sequences(table_lines):
        parsed = _parse_staff_area_numbered_sequence(sequence)
        if parsed is not None:
            chunks.append(_make_staff_area_row_chunk(parsed, chunk_index=len(chunks)))

    for parsed in _parse_staff_area_generic_rows(table_lines):
        chunks.append(_make_staff_area_row_chunk(parsed, chunk_index=len(chunks)))

    chunks.sort(key=lambda chunk: int(str(chunk["metadata"].get("stt") or 0)))
    if len(chunks) < 4:
        return [], None
    for index, chunk in enumerate(chunks):
        chunk["chunk_index"] = index

    region_start = lines_with_offsets[table_start][0]
    region_end = (
        lines_with_offsets[table_end][0]
        if table_end < len(lines_with_offsets)
        else len(text)
    )
    return chunks, (region_start, region_end)

def _staff_area_semantic_text_chunks(part: str, *, offset: int) -> list[dict[str, Any]]:
    stripped = part.strip()
    if not stripped:
        return []
    note_match = re.search(r"(?mi)^\s*Điểm\s+cần\s+lưu\s+ý.*$", part)
    if note_match:
        content = part[note_match.start() :].strip()
        return [
            _make_staff_area_text_chunk(
                content=content,
                start_char=offset + note_match.start(),
                end_char=offset + note_match.start() + len(content),
                metadata={
                    "chunk_type": "note",
                    "section_title": content.splitlines()[0].strip(),
                },
            )
        ]

    matches = list(re.finditer(r"(?m)^\s*(?P<section_id>[1-9])\.\s+(?P<title>.+)$", part))
    if not matches:
        return []

    chunks: list[dict[str, Any]] = []
    if matches[0].start() > 0 and part[: matches[0].start()].strip():
        preamble = part[: matches[0].start()].strip()
        chunks.append(
            _make_staff_area_text_chunk(
                content=preamble,
                start_char=offset,
                end_char=offset + len(preamble),
                metadata={
                    "chunk_type": "overview",
                    "section_title": preamble.splitlines()[0].strip(),
                },
            )
        )

    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(part)
        content = part[match.start() : end].strip()
        if not content:
            continue
        section_id = match.group("section_id")
        section_title = _clean_staff_area_cell(match.group("title"))
        chunks.append(
            _make_staff_area_text_chunk(
                content=content,
                start_char=offset + match.start(),
                end_char=offset + match.start() + len(content),
                metadata={
                    "chunk_type": "section",
                    "section_id": section_id,
                    "section_title": section_title,
                },
            )
        )
    return chunks

def _make_staff_area_text_chunk(
    *,
    content: str,
    start_char: int,
    end_char: int,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    return {
        "chunk_index": 0,
        "content": content,
        "metadata": {
            **metadata,
            "chunk_mode": "table_aware",
            "start_char": start_char,
            "end_char": end_char,
        },
    }

def _lines_with_offsets(text: str) -> list[tuple[int, str]]:
    lines: list[tuple[int, str]] = []
    offset = 0
    for raw_line in text.splitlines(keepends=True):
        lines.append((offset, raw_line.rstrip("\r\n")))
        offset += len(raw_line)
    return lines

def _find_staff_area_table_start(lines: list[tuple[int, str]]) -> int | None:
    for index, (_offset, line) in enumerate(lines):
        normalized = _normalize_staff_area_layout_text(line)
        if STAFF_AREA_TABLE_MARKER_RE.search(normalized):
            return index
    return None

def _find_staff_area_note_start(
    lines: list[tuple[int, str]],
    table_start: int,
) -> int | None:
    for index in range(table_start, len(lines)):
        normalized = _normalize_staff_area_layout_text(lines[index][1])
        if STAFF_AREA_NOTE_MARKER_RE.search(normalized):
            return index
    return None

def _staff_area_numbered_sequences(
    lines: list[tuple[int, str]],
) -> list[list[tuple[int, str]]]:
    sequences: list[list[tuple[int, str]]] = []
    current: list[tuple[int, str]] = []
    for offset, line in lines:
        stripped = line.strip()
        if not stripped or _is_staff_area_header_line(stripped):
            continue
        if re.match(r"^1\.\s+", stripped):
            if current:
                sequences.append(current)
            current = [(offset, stripped)]
            continue
        if current:
            if _is_staff_area_generic_anchor(stripped):
                sequences.append(current)
                current = []
                continue
            if _is_staff_area_generic_assignment_line(stripped):
                sequences.append(current)
                current = []
                continue
            current.append((offset, stripped))
    if current:
        sequences.append(current)
    return sequences

def _parse_staff_area_numbered_sequence(
    sequence: list[tuple[int, str]],
) -> dict[str, Any] | None:
    anchor = _staff_area_anchor_from_lines(sequence)
    if anchor is None:
        return None
    anchor_index, anchor_match = anchor
    stt = anchor_match.group("stt")
    department = anchor_match.group("department")
    anchor_area = (anchor_match.group("area") or "").strip()
    area = anchor_area
    suspicious_fragments: list[str] = []
    if not area:
        area = _staff_area_from_context(sequence, anchor_index)
    else:
        suspicious_fragments = _next_row_fragment_candidates(sequence, anchor_index)
    staff_text = " ".join(_staff_item_text(line) for _offset, line in sequence)
    staff = parse_staff_members(staff_text)
    if not area or not staff:
        return None
    parsed: dict[str, Any] = {
        "stt": stt,
        "area": area,
        "lead_department": department,
        "staff": [member.to_dict() for member in staff],
        "page_number": _staff_area_page_number(stt),
        "raw_text": " ".join(line for _offset, line in sequence),
        "confidence": 0.95,
    }
    if suspicious_fragments:
        parsed["parse_warning"] = "raw_text_contains_next_row_fragment"
        parsed["excluded_raw_fragments"] = suspicious_fragments
    return parsed

def _staff_area_anchor_from_lines(
    lines: list[tuple[int, str]],
) -> tuple[int, re.Match[str]] | None:
    for index, (_offset, line) in enumerate(lines):
        match = STAFF_AREA_ANCHOR_RE.match(line.strip())
        if match and match.group("department") in STAFF_AREA_DEPARTMENTS:
            return index, match
    return None

def _staff_area_from_context(
    sequence: list[tuple[int, str]],
    anchor_index: int,
) -> str:
    area_lines: list[str] = []
    for index, (_offset, line) in enumerate(sequence):
        cleaned = line.strip()
        if not cleaned or STAFF_AREA_ITEM_RE.search(cleaned):
            continue
        if index == anchor_index:
            continue
        if _is_staff_area_header_line(cleaned):
            continue
        area_lines.append(cleaned)
    return _clean_staff_area_cell(" ".join(area_lines))

def _next_row_fragment_candidates(
    sequence: list[tuple[int, str]],
    anchor_index: int,
) -> list[str]:
    candidates: list[str] = []
    last_staff_index = -1
    for index, (_offset, line) in enumerate(sequence):
        if STAFF_AREA_ITEM_RE.search(line):
            last_staff_index = index
    if last_staff_index <= anchor_index:
        return candidates
    for index, (_offset, line) in enumerate(sequence):
        if index <= last_staff_index:
            continue
        stripped = line.strip()
        if stripped and not STAFF_AREA_ITEM_RE.search(stripped):
            candidates.append(stripped)
    return candidates

def _staff_item_text(line: str) -> str:
    parts = []
    for match in STAFF_AREA_ITEM_RE.finditer(line):
        parts.append(f"{match.group('index')}. {match.group('name').strip()}")
    return " ".join(parts)

def _parse_staff_area_generic_rows(lines: list[tuple[int, str]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, (_offset, line) in enumerate(lines):
        stripped = line.strip()
        match = STAFF_AREA_ANCHOR_RE.match(stripped)
        if not match or not _is_staff_area_generic_anchor(stripped):
            continue
        previous_line = lines[index - 1][1].strip() if index > 0 else ""
        next_line = lines[index + 1][1].strip() if index + 1 < len(lines) else ""
        parsed = _parse_staff_area_generic_row(
            stt=match.group("stt"),
            department=match.group("department"),
            previous_line=previous_line,
            next_line=next_line,
        )
        if parsed is not None:
            rows.append(parsed)
    return rows

def _parse_staff_area_generic_row(
    *,
    stt: str,
    department: str,
    previous_line: str,
    next_line: str,
) -> dict[str, Any] | None:
    department_marker = f"P.{department}"
    marker_index = previous_line.find("Phòng ")
    if marker_index < 0:
        return None
    area_prefix = previous_line[:marker_index].strip()
    staff_prefix = previous_line[marker_index:].strip()
    next_normalized = _normalize_staff_area_layout_text(next_line)
    suffix_index = next_normalized.find("khac do")
    area_suffix = next_line[:suffix_index].strip() if suffix_index >= 0 else next_line.strip()
    area = _clean_staff_area_cell(f"{area_prefix} {area_suffix}")
    generic_assignment_text = _clean_staff_area_cell(
        f"Phòng {department_marker} và các nhân sự khác do {department_marker} đề xuất"
    )
    raw_text = " ".join(part for part in (previous_line, f"{stt} {department}", next_line) if part)
    if not area or not staff_prefix:
        return None
    return {
        "stt": stt,
        "area": area,
        "lead_department": department,
        "staff": [],
        "page_number": _staff_area_page_number(stt),
        "raw_text": raw_text,
        "confidence": 0.75,
        "assignment_type": "generic_department_assignment",
        "has_specific_person": False,
        "generic_assignment_text": generic_assignment_text,
    }

def _make_staff_area_row_chunk(row: dict[str, Any], *, chunk_index: int) -> dict[str, Any]:
    assignment_type = str(row.get("assignment_type") or "specific_people")
    has_specific_person = bool(row.get("has_specific_person", assignment_type == "specific_people"))
    staff_items = [
        {
            **item,
            "name_normalized": normalize_metadata_value(str(item.get("name") or "")),
            "entity_type": "person",
        }
        for item in row.get("staff", [])
        if isinstance(item, dict) and item.get("name") and has_specific_person
    ]
    if has_specific_person:
        assignment_text = "; ".join(
            f"{item.get('name')} ({item.get('role_note')})"
            if item.get("role_note") else str(item.get("name"))
            for item in staff_items
            if item.get("name")
        )
    else:
        assignment_text = str(row.get("generic_assignment_text") or "").strip()
    content = (
        f"STT: {row['stt']}\n"
        f"Mảng công nghệ: {row['area']}\n"
        f"Phòng chủ trì: {row['lead_department']}\n"
        f"Nhân sự đề xuất: {assignment_text}"
    )
    source_row_id = f"{STAFF_AREA_LAYOUT_TABLE_ID}_row_{row['stt']}"
    metadata: dict[str, Any] = {
        "chunk_type": "table_row",
        "chunk_mode": "table_aware",
        "chunk_overlap": 0,
        "overlap_applied": False,
        "table_id": STAFF_AREA_LAYOUT_TABLE_ID,
        "table_title": STAFF_AREA_TABLE_TITLE,
        "headers": ["STT", "Mảng công nghệ", "Phòng chủ trì", "Nhân sự đề xuất"],
        "row_index": int(row["stt"]),
        "row_start": int(row["stt"]),
        "row_end": int(row["stt"]),
        "stt": row["stt"],
        "area": row["area"],
        "area_normalized": normalize_metadata_value(str(row["area"])),
        "lead_department": row["lead_department"],
        "lead_department_normalized": normalize_metadata_value(str(row["lead_department"])),
        "staff_names": (
            [str(item.get("name")) for item in staff_items if item.get("name")]
            if has_specific_person
            else []
        ),
        "staff": staff_items,
        "assignment_type": assignment_type,
        "has_specific_person": has_specific_person,
        "source_table": STAFF_AREA_TABLE_TITLE,
        "source_row_id": source_row_id,
        "relationship_type": "technology_area_staff",
        "confidence": row.get("confidence", 0.95),
        "canonical_text": content,
        "raw_text_clean": content,
        "text_content": content,
        "raw_text": row.get("raw_text"),
        "raw_text_original": row.get("raw_text"),
    }
    if not has_specific_person:
        metadata["generic_assignment_text"] = assignment_text
    if row.get("page_number") is not None:
        metadata["page_number"] = row["page_number"]
    if row.get("parse_warning"):
        metadata["parse_warning"] = row["parse_warning"]
    if row.get("excluded_raw_fragments"):
        metadata["excluded_raw_fragments"] = row["excluded_raw_fragments"]
    return {"chunk_index": chunk_index, "content": content, "metadata": metadata}

def _is_staff_area_header_line(line: str) -> bool:
    normalized = _normalize_staff_area_layout_text(line)
    return (
        not normalized
        or normalized in {"phong", "chu tri", "de xuat"}
        or "stt mang cong nghe" in normalized
        or STAFF_AREA_TABLE_MARKER_RE.search(normalized) is not None
    )

def _is_staff_area_generic_anchor(line: str) -> bool:
    return re.match(r"^(7\s+PM|8\s+VH)$", line.strip()) is not None

def _is_staff_area_generic_assignment_line(line: str) -> bool:
    return "Phòng P." in line and "nhân sự" in line

def _clean_staff_area_cell(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip(" -;:|/")

def _normalize_staff_area_layout_text(value: str) -> str:
    import unicodedata

    decomposed = unicodedata.normalize("NFD", value)
    stripped = "".join(char for char in decomposed if unicodedata.category(char) != "Mn")
    stripped = stripped.replace("Đ", "D").replace("đ", "d")
    return re.sub(r"\s+", " ", stripped).strip().lower()

def _staff_area_page_number(stt: str) -> int | None:
    return 6 if stt in {"8", "9"} else 5

def _to_int(value: str | None) -> int | None:
    if value is None:
        return None
    return int(value) if value.isdigit() else None
