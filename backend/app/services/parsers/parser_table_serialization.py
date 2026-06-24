from __future__ import annotations

import re
from collections.abc import Iterable

MAX_TITLE_LENGTH = 200
TITLE_PREFIX = "TABLE_TITLE"
HEADER_PREFIX = "TABLE_HEADER"
ROW_PREFIX = "TABLE_ROW"


def normalize_cell_text(value: str) -> str:
    return " ".join((value or "").replace("\r", " ").replace("\n", " ").split())


def build_table_title_record(
    *,
    table_id: str,
    title: str,
    page_number: int | None = None,
) -> str:
    normalized = normalize_cell_text(title)
    if not normalized:
        return ""
    page_fragment = f" page={page_number}" if page_number is not None else ""
    return f"{TITLE_PREFIX} table_id={table_id}{page_fragment}: {normalized}"


def build_table_header_record(
    *,
    table_id: str,
    headers: list[str],
    page_number: int | None = None,
) -> str:
    page_fragment = f" page={page_number}" if page_number is not None else ""
    header_text = " | ".join(normalize_cell_text(header) for header in headers)
    return f"{HEADER_PREFIX} table_id={table_id}{page_fragment} | {header_text}"


def build_table_row_record(
    *,
    table_id: str,
    row_index: int,
    headers: list[str],
    values: list[str],
    page_number: int | None = None,
) -> str:
    page_fragment = f" page={page_number}" if page_number is not None else ""
    width = max(len(headers), len(values))
    safe_headers = ensure_headers(headers, width)
    safe_values = values + [""] * (width - len(values))
    fields = [
        f"{header}: {normalize_cell_text(value)}"
        for header, value in zip(safe_headers, safe_values, strict=True)
    ]
    return (
        f"{ROW_PREFIX} table_id={table_id}{page_fragment} row={row_index} | "
        + " | ".join(fields)
    )


def normalize_table_rows(rows: Iterable[Iterable[str]]) -> list[list[str]]:
    normalized_rows = [
        [normalize_cell_text(cell) for cell in row]
        for row in rows
    ]
    normalized_rows = [row for row in normalized_rows if any(cell for cell in row)]
    if not normalized_rows:
        return []

    width = max(len(row) for row in normalized_rows)
    return [row + [""] * (width - len(row)) for row in normalized_rows]


def ensure_headers(headers: list[str], width: int) -> list[str]:
    safe_headers = [
        normalize_cell_text(header) or f"cell_{index + 1}"
        for index, header in enumerate(headers[:width])
    ]
    if len(safe_headers) < width:
        safe_headers.extend(f"cell_{index + 1}" for index in range(len(safe_headers), width))
    return safe_headers


def infer_headers(
    rows: Iterable[Iterable[str]],
) -> tuple[list[str], list[list[str]], bool]:
    padded_rows = normalize_table_rows(rows)
    if not padded_rows:
        return [], [], False

    width = max(len(row) for row in padded_rows)
    first_row = padded_rows[0]
    has_header = _looks_like_header(first_row, padded_rows[1:])
    if has_header:
        headers = ensure_headers(first_row, width)
        return headers, padded_rows[1:], True

    headers = ensure_headers([], width)
    return headers, padded_rows, False


def serialize_table(
    *,
    table_id: str,
    rows: Iterable[Iterable[str]],
    title: str | None = None,
    page_number: int | None = None,
) -> str:
    headers, data_rows, has_header = infer_headers(rows)
    if not headers or not data_rows:
        return ""

    lines: list[str] = []
    if title:
        title_record = build_table_title_record(
            table_id=table_id,
            title=title,
            page_number=page_number,
        )
        if title_record:
            lines.append(title_record)

    if has_header:
        lines.append(
            build_table_header_record(
                table_id=table_id,
                headers=headers,
                page_number=page_number,
            )
        )

    for row_index, row_values in enumerate(data_rows, start=1):
        lines.append(
            build_table_row_record(
                table_id=table_id,
                row_index=row_index,
                headers=headers,
                values=row_values,
                page_number=page_number,
            )
        )

    return "\n".join(lines)


def maybe_table_title(candidate: str | None) -> str | None:
    normalized = normalize_cell_text(candidate or "")
    if not normalized:
        return None
    if len(normalized) > MAX_TITLE_LENGTH:
        return None
    if re.search(r"\b(row|cell|table_row|table_title)\b", normalized, flags=re.IGNORECASE):
        return None
    return normalized


def rewrite_text_with_serialized_tables(
    *,
    text: str,
    page_number: int | None,
    table_id_prefix: str,
) -> str:
    from app.services.chunkers.chunker_table_aware_chunking import detect_tables_in_text

    tables = detect_tables_in_text(text)
    if not tables:
        return text.strip()

    parts: list[str] = []
    previous_end = 0
    for index, table in enumerate(sorted(tables, key=lambda item: item.start_char), start=1):
        if table.start_char > previous_end:
            leading = text[previous_end:table.start_char].strip()
            if leading:
                parts.append(leading)

        table_text = serialize_table(
            table_id=f"{table_id_prefix}_{index}",
            rows=[table.headers, *table.rows],
            title=table.title,
            page_number=page_number,
        )
        if table_text:
            parts.append(table_text)
        previous_end = max(previous_end, table.end_char)

    trailing = text[previous_end:].strip()
    if trailing:
        parts.append(trailing)

    return "\n\n".join(part for part in parts if part.strip())


def _looks_like_header(first_row: list[str], other_rows: list[list[str]]) -> bool:
    non_empty = [cell for cell in first_row if cell]
    if len(non_empty) < 2 or len(non_empty) != len(first_row):
        return False
    if all(_is_numericish(cell) for cell in non_empty):
        return False
    if not other_rows:
        return False

    comparison_rows = other_rows[: min(3, len(other_rows))]
    alpha_ratio = sum(any(char.isalpha() for char in cell) for cell in first_row) / len(first_row)
    if alpha_ratio < 0.5:
        return False

    differing_columns = 0
    for column_index, header_cell in enumerate(first_row):
        column_values = [
            row[column_index]
            for row in comparison_rows
            if column_index < len(row) and row[column_index]
        ]
        if not column_values:
            continue
        if all(
            normalize_cell_text(value) != normalize_cell_text(header_cell)
            for value in column_values
        ):
            differing_columns += 1

    return differing_columns >= max(1, len(first_row) // 2)


def _is_numericish(value: str) -> bool:
    compact = value.replace(",", "").replace(".", "").replace("%", "").strip()
    return bool(compact) and compact.isdigit()
