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

_PIPE_LINE = re.compile(r"^[^|]+(?:\|[^|]+){1,}$")
_TABLE_TITLE_LINE = re.compile(
    r"^TABLE_TITLE\s+table_id=(?P<table_id>\S+)(?:\s+page=(?P<page>\d+))?:\s*(?P<title>.*)$"
)
_TABLE_ROW_LINE = re.compile(
    r"^TABLE_ROW\s+table_id=(?P<table_id>\S+)(?:\s+page=(?P<page>\d+))?\s+row=(?P<row>\d+)\s+\|\s*(?P<fields>.+)$"
)
_ABBREV_PATTERN = re.compile(r"\b[A-Z0-9][A-Z0-9._/-]{1,}\b")

MIN_TABLE_ROWS = 2
PIPE_SEPARATOR = " | "
ENTITY_MIN_ROWS = 2
TABLE_HEADER_PREFIX = "TABLE_HEADER"


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
            values.append(field.strip())
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

    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
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
            continue

        parts = [part for part in separator_re.split(stripped) if part.strip()]
        if len(parts) >= 2:
            current_block.append((index, line))
            continue

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

    if len(current_block) >= MIN_TABLE_ROWS:
        table = _aligned_block_to_table(
            text,
            current_block,
            separator_re,
            table_index=len(tables) + 1,
        )
        if table is not None:
            tables.append(table)

    return tables


def _aligned_block_to_table(
    full_text: str,
    block: list[tuple[int, str]],
    separator_re: re.Pattern[str],
    *,
    table_index: int,
) -> TableBlock | None:
    parsed_rows = [
        [part.strip() for part in separator_re.split(line.strip()) if part.strip()]
        for _, line in block
    ]
    parsed_rows = [row for row in parsed_rows if row]
    if len(parsed_rows) < MIN_TABLE_ROWS:
        return None

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
        if len(candidate) >= 2:
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
        lines = [f"ENTITY_SUMMARY entity={entity}", "Rows:"]
        for chunk in related_chunks:
            lines.append(f"- {chunk['content']}")

        summaries.append(
            {
                "chunk_index": start_index + len(summaries),
                "content": "\n".join(lines),
                "metadata": {
                    "chunk_type": "entity_summary",
                    "chunk_mode": "table_aware",
                    "entity_name": entity,
                    "row_count": len(related_chunks),
                    "table_ids": table_ids,
                    "page_numbers": source_pages,
                },
            }
        )

    return summaries


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
    summaries = generate_entity_summary_chunks(
        row_chunks,
        entity_index,
        start_index=len(all_chunks),
    )
    all_chunks.extend(summaries)

    for index, chunk in enumerate(all_chunks):
        chunk["chunk_index"] = index

    return all_chunks, entity_index


def _to_int(value: str | None) -> int | None:
    if value is None:
        return None
    return int(value) if value.isdigit() else None
