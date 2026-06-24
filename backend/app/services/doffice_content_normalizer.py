from __future__ import annotations

import hashlib
import html
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any

FOOTER_MARKER_PATTERN = re.compile(r"(?im)^\s*(Nơi nhận|KT\.\s*GIÁM ĐỐC|PHÓ GIÁM ĐỐC|Lưu:\s*VT)\b")
PAGE_MARKER_PATTERN = re.compile(r"(?im)^\s*---\s*Page\s+\d+\s*---\s*$")
APPENDIX_MARKER_PATTERN = re.compile(r"(?im)^\s*(PHỤ\s*LỤC|PHU\s*LUC|PHá»¤\s*Lá»¤C)\b")
ASCII_FOOTER_MARKER_PATTERN = re.compile(
    r"(?im)^\s*(Noi\s+nhan|N[ơo]i\s+nhận|KT\.\s*GIAM\s+DOC|PHO\s+GIAM\s+DOC|Luu:\s*VT)\b"
)
TABLE_PATTERN = re.compile(r"(?is)<table\b.*?</table>")
MARKDOWN_TABLE_SEPARATOR_CELL_PATTERN = re.compile(r"^:?-{3,}:?$")
DOC_CODE_PATTERN = re.compile(r"\b(?!\d{1,2}/\d{1,2}/\d{2,4}\b)(\d{1,6}/[A-ZÀ-ỸĐ0-9][A-ZÀ-ỸĐ0-9+._\-]{1,}(?:/[A-ZÀ-ỸĐ0-9+._\-]+)*)\b", re.UNICODE)
DATE_PATTERN = re.compile(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b")
MONTH_YEAR_PATTERN = re.compile(r"\b\d{1,2}/\d{4}\b")
VIETNAMESE_TEXT_DATE_PATTERN = re.compile(r"ngÃ y\s+(\d{1,2})\s+thÃ¡ng\s+(\d{1,2})\s+nÄƒm\s+(\d{4})", re.IGNORECASE)

SPACING_FIXES = {
    "Li \u00c3\u00aan": "Li\u00c3\u00aan",
    "Li \u00ean": "Li\u00ean",
    "Th anh": "Thanh",
    "T\u00c3\u00a0 i": "T\u00c3\u00a0i",
    "Tà i": "Tài",
    "Th anh toán": "Thanh toán",
    "H óa đơn": "Hóa đơn",
    "Tr a cứu": "Tra cứu",
    "H ướng dẫn": "Hướng dẫn",
    "Câ u hỏi": "Câu hỏi",
    "Bá o cáo": "Báo cáo",
    "D anh mục": "Danh mục",
    "Q uản lý": "Quản lý",
    "D ashboard": "Dashboard",
    "Tr a": "Tra",
    "Ư ớc tính": "Ước tính",
    "Th ông báo": "Thông báo",
    "D ịch vụ": "Dịch vụ",
    "D á»‹ch": "Dá»‹ch",
    "bÃ¡oc Ã¡o": "bÃ¡o cÃ¡o",
    "Ä‘á»ƒp/h": "Ä‘á»ƒ p/h",
}

MOJIBAKE_TEXT_FIXES = {
    "DÃ²ng": "Dòng",
    "Ngá»¯ cáº£nh hÃ ng": "Ngữ cảnh hàng",
    "Ná»™i dung cá»™t": "Nội dung cột",
    "Cá»™t báº£ng": "Cột bảng",
    "Báº£ng dá»¯ liá»‡u DOffice": "Bảng dữ liệu",
    "Ch\u053c\u0575 đổi sang GIS": "Chuyển đổi sang GIS",
    "Ch\u053c\u0575 đổi": "Chuyển đổi",
    "Ðiều": "Điều",
    "ÐIỀU": "ĐIỀU",
}

TABLE_GROUP_MAX_ROWS = 10
TABLE_PARENT_PREVIEW_ROWS = 5
TEXT_SECTION_MAX_CHARS = 3500
PARENT_SECTION_STANDALONE_MIN_CHARS = 300

SECTION_HEADING_PATTERN = re.compile(
    r"(?iu)^\s*(?:#{1,6}\s*)?"
    r"("
    r"(?:[IVXLCDM]{1,8}|[A-Z])\s*[\.\)]"
    r"|(?:\d+(?:\.\d+)*)(?:[\.\)]|\s)"
    r"|(?:Điều|Dieu)\s+\d+[.:]?"
    r"|Khoản\s+\d+[.:]?"
    r"|Mục\s+\d+[.:]?"
    r"|Phụ\s*lục\s*[0-9IVXLCDM]*[.:]?"
    r")"
    r"(?:\s+.+)?$"
)


@dataclass(frozen=True)
class NormalizedTableCell:
    text: str
    row_index: int
    col_index: int
    rowspan: int = 1
    colspan: int = 1
    header: bool = False


@dataclass(frozen=True)
class NormalizedTableRow:
    row_index: int
    values: list[str]
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class NormalizedTable:
    table_index: int
    headers: list[str]
    rows: list[NormalizedTableRow]
    markdown: str
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class NormalizedElement:
    element_type: str
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class NormalizedDofficeDocument:
    id_vb: str
    document_code: str | None
    title: str | None
    issued_date: str | None
    issuer: str | None
    signer: str | None
    raw_text: str
    clean_text: str
    markdown_text: str
    plain_text: str
    summary_text: str | None
    elements: list[NormalizedElement]
    tables: list[NormalizedTable]
    metadata: dict[str, Any]
    content_hash: str
    metadata_hash: str

    @property
    def table_rows(self) -> list[NormalizedTableRow]:
        return [row for table in self.tables for row in table.rows]


class DofficeHTMLTableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.raw_rows: list[list[dict[str, Any]]] = []
        self._current_row: list[dict[str, Any]] | None = None
        self._current_cell: dict[str, Any] | None = None
        self._cell_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        tag_name = tag.lower()
        attr_map = dict(attrs)
        if tag_name == "tr":
            self._finish_row()
            self._current_row = []
        elif tag_name in {"td", "th"}:
            self._finish_cell()
            self._current_cell = {
                "header": tag_name == "th",
                "rowspan": _positive_int(attr_map.get("rowspan"), default=1),
                "colspan": _positive_int(attr_map.get("colspan"), default=1),
            }
            self._cell_parts = []
        elif tag_name == "br" and self._current_cell is not None:
            self._cell_parts.append("\n")
        elif tag_name == "li" and self._current_cell is not None:
            self._cell_parts.append("\n- ")

    def handle_endtag(self, tag: str) -> None:
        tag_name = tag.lower()
        if tag_name in {"td", "th"}:
            self._finish_cell()
        elif tag_name == "tr":
            self._finish_row()

    def handle_data(self, data: str) -> None:
        if self._current_cell is not None:
            self._cell_parts.append(data)

    def close(self) -> None:
        self._finish_cell()
        self._finish_row()
        super().close()

    def _finish_cell(self) -> None:
        if self._current_cell is None:
            return
        cell = dict(self._current_cell)
        cell["text"] = clean_inline_text("".join(self._cell_parts))
        if self._current_row is None:
            self._current_row = []
        self._current_row.append(cell)
        self._current_cell = None
        self._cell_parts = []

    def _finish_row(self) -> None:
        if self._current_row is None:
            return
        if any(str(cell.get("text") or "").strip() for cell in self._current_row):
            self.raw_rows.append(self._current_row)
        self._current_row = None


class TextHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        tag_name = tag.lower()
        if tag_name in {"script", "style"}:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if tag_name == "br" or tag_name in {"p", "div", "tr", "li", "section", "h1", "h2", "h3", "h4", "h5", "h6"}:
            self._append_newline()

    def handle_endtag(self, tag: str) -> None:
        tag_name = tag.lower()
        if tag_name in {"script", "style"} and self._skip_depth:
            self._skip_depth -= 1
            return
        if self._skip_depth:
            return
        if tag_name in {"p", "div", "tr", "li", "section", "h1", "h2", "h3", "h4", "h5", "h6"}:
            self._append_newline()

    def handle_data(self, data: str) -> None:
        if not self._skip_depth and data:
            self.parts.append(data)

    def get_text(self) -> str:
        return "".join(self.parts)

    def _append_newline(self) -> None:
        if self.parts and not self.parts[-1].endswith("\n"):
            self.parts.append("\n")


def normalize_doffice_source(source: dict[str, Any]) -> NormalizedDofficeDocument:
    raw_text = str(source.get("noi_dung") or "")
    summary_text = compact_source_summary(str(source.get("tom_tat") or "").strip()) or None
    html_tables = parse_html_tables(raw_text)
    markdown_tables = parse_markdown_tables(raw_text, start_index=len(html_tables))
    tables = sorted(
        [*html_tables, *markdown_tables],
        key=lambda table: int(table.metadata.get("source_start", table.table_index)),
    )
    base_markdown_text = replace_tables(raw_text, tables, replacement="markdown")
    base_plain_text = html_to_plain_text(replace_tables(raw_text, tables, replacement="placeholder"))
    body_text, footer_text = split_footer_signature(base_plain_text)
    base_clean_text = normalize_lines(apply_spacing_fixes(strip_markdown_noise(body_text)))
    metadata = build_rule_metadata(source=source, clean_text=base_clean_text, tables=tables)
    metadata_preamble = build_doffice_metadata_preamble(source=source, metadata=metadata)
    metadata = {**metadata, "metadata_preamble": metadata_preamble}
    clean_text = prepend_metadata_preamble(base_clean_text, metadata_preamble)
    plain_text = prepend_metadata_preamble(base_plain_text, metadata_preamble)
    markdown_text = prepend_metadata_preamble(base_markdown_text, metadata_preamble)
    elements = build_elements(source=source, clean_text=base_clean_text, tables=tables, footer_text=footer_text, summary_text=summary_text, metadata=metadata)
    content_hash = sha256_text("\n\n".join(part for part in (metadata_preamble, raw_text) if part.strip()))
    metadata_hash = sha256_json({key: source.get(key) for key in sorted(source) if key != "noi_dung"})

    return NormalizedDofficeDocument(
        id_vb=str(source.get("id_vb") or "").strip(),
        document_code=metadata.get("document_code"),
        title=_optional_string(source.get("trich_yeu")),
        issued_date=metadata.get("issued_date"),
        issuer=_optional_string(source.get("noi_ban_hanh")),
        signer=_optional_string(source.get("nguoi_ky")),
        raw_text=raw_text,
        clean_text=clean_text,
        markdown_text=markdown_text,
        plain_text=plain_text,
        summary_text=summary_text,
        elements=elements,
        tables=tables,
        metadata=metadata,
        content_hash=content_hash,
        metadata_hash=metadata_hash,
    )


def build_doffice_metadata_preamble(*, source: dict[str, Any], metadata: dict[str, Any]) -> str:
    """Build authoritative document metadata text and prepend it to DOffice content.

    Some DOffice OCR text loses values in the body, for example ``Số: /EVNICT-TTPM``
    while the source properties still contain ``ky_hieu`` and ``ngay_vb``. The preamble
    makes those authoritative properties available to chunking, enrichment, embedding,
    BM25, and answer generation.
    """
    document_code = _optional_string(source.get("ky_hieu")) or _optional_string(metadata.get("document_code"))
    issued_date = normalize_date(source.get("ngay_vb")) or _optional_string(metadata.get("issued_date"))
    lines = ["THÔNG TIN VĂN BẢN DOFFICE"]
    for label, value in (
        ("ID_VB", source.get("id_vb") or metadata.get("id_vb")),
        ("Số/ký hiệu văn bản", document_code),
        ("Ngày văn bản", issued_date),
        ("Trích yếu", source.get("trich_yeu") or metadata.get("trich_yeu")),
        ("Nơi ban hành", source.get("noi_ban_hanh") or metadata.get("noi_ban_hanh") or metadata.get("issuer")),
        ("Người ký", source.get("nguoi_ky") or metadata.get("nguoi_ky")),
        ("Tên file", source.get("ten_file") or metadata.get("ten_file")),
        ("Đường dẫn", source.get("duong_dan") or metadata.get("duong_dan")),
        ("Năm", source.get("nam") or metadata.get("nam")),
        ("Tháng", source.get("thang") or metadata.get("thang")),
    ):
        clean_value = _optional_string(value)
        if clean_value:
            lines.append(f"{label}: {clean_value}")
    return "\n".join(lines)


def prepend_metadata_preamble(text: str, preamble: str) -> str:
    clean_text = normalize_lines(str(text or ""))
    clean_preamble = normalize_lines(str(preamble or ""))
    if not clean_preamble:
        return clean_text
    if clean_text.startswith(clean_preamble):
        return clean_text
    return "\n\n".join(part for part in (clean_preamble, clean_text) if part.strip())


def parse_html_tables(raw_text: str) -> list[NormalizedTable]:
    tables: list[NormalizedTable] = []
    for table_index, match in enumerate(TABLE_PATTERN.finditer(raw_text or "")):
        table_name = infer_table_name(raw_text or "", match.start(), table_index=table_index)
        parser = DofficeHTMLTableParser()
        parser.feed(match.group(0))
        parser.close()
        matrix, header_flags = expand_table_matrix(parser.raw_rows)
        if not matrix:
            continue
        headers = [clean_inline_text(value) for value in matrix[0]] if any(header_flags[0]) or len(matrix) > 1 else []
        data_rows = matrix[1:] if headers and len(matrix) > 1 else matrix
        rows = [NormalizedTableRow(row_index=index + 1, values=[clean_inline_text(value) for value in row], metadata=row_metadata(headers, row, table_name=table_name)) for index, row in enumerate(data_rows)]
        table_context = infer_table_context(raw_text or "", match.start())
        tables.append(
            NormalizedTable(
                table_index=table_index,
                headers=headers,
                rows=inherit_table_context(rows),
                markdown=table_to_markdown(headers, data_rows),
                text=table_to_text(headers, data_rows),
                metadata={
                    "table_index": table_index,
                    "table_name": table_name,
                    "section_title": table_name,
                    "table_context": table_context,
                    "source_start": match.start(),
                    "source_end": match.end(),
                    "source_format": "html_table",
                    "row_count": len(rows),
                    "column_count": max((len(row) for row in matrix), default=0),
                    "columns": headers,
                },
            )
        )
    return tables


def parse_markdown_tables(raw_text: str, *, start_index: int = 0) -> list[NormalizedTable]:
    text = raw_text or ""
    html_ranges = [(match.start(), match.end()) for match in TABLE_PATTERN.finditer(text)]
    lines = text.splitlines(keepends=True)
    offsets: list[int] = []
    cursor = 0
    for line in lines:
        offsets.append(cursor)
        cursor += len(line)

    tables: list[NormalizedTable] = []
    index = 0
    while index + 1 < len(lines):
        line_start = offsets[index]
        if _position_in_ranges(line_start, html_ranges):
            index += 1
            continue

        first = lines[index].strip()
        second = lines[index + 1].strip()
        if not (_is_markdown_table_line(first) and _is_markdown_table_separator(second)):
            index += 1
            continue

        start_line = index
        index += 2
        while index < len(lines):
            next_start = offsets[index]
            next_line = lines[index].strip()
            if _position_in_ranges(next_start, html_ranges) or not _is_markdown_table_line(next_line):
                break
            index += 1

        block = "".join(lines[start_line:index])
        headers, data_rows = _parse_markdown_table_block(block)
        if not headers and not data_rows:
            continue

        table_index = start_index + len(tables)
        source_start = offsets[start_line]
        source_end = offsets[index] if index < len(offsets) else len(text)
        table_name = infer_table_name(text, source_start, table_index=table_index)
        table_context = infer_table_context(text, source_start)
        rows = [
            NormalizedTableRow(
                row_index=row_index + 1,
                values=[clean_inline_text(value) for value in row],
                metadata=row_metadata(headers, row, table_name=table_name),
            )
            for row_index, row in enumerate(data_rows)
        ]
        tables.append(
            NormalizedTable(
                table_index=table_index,
                headers=headers,
                rows=inherit_table_context(rows),
                markdown=table_to_markdown(headers, data_rows),
                text=table_to_text(headers, data_rows),
                metadata={
                    "table_index": table_index,
                    "table_name": table_name,
                    "section_title": table_name,
                    "table_context": table_context,
                    "source_start": source_start,
                    "source_end": source_end,
                    "source_format": "markdown_table",
                    "row_count": len(rows),
                    "column_count": max([len(headers), *(len(row) for row in data_rows)], default=0),
                    "columns": headers,
                },
            )
        )
    return tables


def _parse_markdown_table_block(table_markdown: str) -> tuple[list[str], list[list[str]]]:
    raw_lines = [line.strip() for line in table_markdown.splitlines() if _is_markdown_table_line(line.strip())]
    if len(raw_lines) < 2:
        return [], []
    header_line = raw_lines[0]
    body_lines = [line for line in raw_lines[2:] if not _is_markdown_table_separator(line)]
    headers = [clean_inline_text(cell) for cell in _split_markdown_table_row(header_line)]
    rows = [
        [clean_inline_text(cell) for cell in _split_markdown_table_row(line)]
        for line in body_lines
    ]
    width = max([len(headers), *(len(row) for row in rows)], default=0)
    headers = headers + [""] * max(0, width - len(headers))
    rows = [row + [""] * max(0, width - len(row)) for row in rows]
    return headers, rows


def _split_markdown_table_row(line: str) -> list[str]:
    stripped = line.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]
    return [cell.strip() for cell in stripped.split("|")]


def _is_markdown_table_line(line: str) -> bool:
    stripped = line.strip()
    return "|" in stripped and stripped.count("|") >= 2 and not stripped.casefold().startswith("<table")


def _is_markdown_table_separator(line: str) -> bool:
    cells = [cell.strip() for cell in _split_markdown_table_row(line) if cell.strip()]
    return bool(cells) and all(MARKDOWN_TABLE_SEPARATOR_CELL_PATTERN.fullmatch(cell) for cell in cells)


def _position_in_ranges(position: int, ranges: list[tuple[int, int]]) -> bool:
    return any(start <= position < end for start, end in ranges)


def infer_table_name(raw_text: str, table_start: int, *, table_index: int) -> str:
    before = html_to_plain_text((raw_text or "")[:table_start])
    lines = [line.strip(" :-") for line in before.splitlines() if line.strip(" :-")]
    for line in reversed(lines[-8:]):
        lowered = line.casefold()
        normalized = strip_vietnamese_accents(lowered)
        if normalized.startswith("bang ") or normalized.startswith("phu luc"):
            return clean_inline_text(line)
        if "danh" in lowered and ("chuc" in lowered or "chức" in lowered):
            return clean_inline_text(line)
        if "giao" in lowered and "man" in lowered:
            return clean_inline_text(line)
    return f"Bảng {table_index + 1}"


def infer_table_context(raw_text: str, table_start: int, *, max_chars: int = 2500) -> str | None:
    before = html_to_plain_text((raw_text or "")[:table_start])
    lines: list[str] = []
    for line in before.splitlines():
        clean = clean_inline_text(line).strip(" :-")
        if not clean:
            continue
        if _is_markdown_table_line(clean) or _is_markdown_table_separator(clean):
            continue
        if clean.startswith("[[TABLE_"):
            continue
        lines.append(clean)
    context = normalize_lines("\n".join(table_context_lines(lines, max_chars=max_chars)))
    if not context:
        return None
    return context


def table_context_lines(lines: list[str], *, max_chars: int) -> list[str]:
    if not lines:
        return []
    start_index = nearest_context_start_index(lines)
    selected = lines[start_index:]
    context = normalize_lines("\n".join(selected))
    if len(context) <= max_chars:
        return selected

    prefix: list[str] = []
    prefix_chars = 0
    for line in selected:
        prefix.append(line)
        prefix_chars += len(line) + 1
        if prefix_chars >= max_chars // 2:
            break

    suffix: list[str] = []
    suffix_chars = 0
    for line in reversed(selected[len(prefix) :]):
        suffix.insert(0, line)
        suffix_chars += len(line) + 1
        if prefix_chars + suffix_chars >= max_chars:
            break
    return [*prefix, "...", *suffix] if suffix else prefix


def nearest_context_start_index(lines: list[str]) -> int:
    appendix_indexes = [index for index, line in enumerate(lines) if is_appendix_heading(line)]
    if appendix_indexes:
        return appendix_indexes[-1]
    heading_indexes = [index for index, line in enumerate(lines) if is_section_heading(line)]
    if heading_indexes:
        return heading_indexes[-1]
    return max(0, len(lines) - 20)


def expand_table_matrix(raw_rows: list[list[dict[str, Any]]]) -> tuple[list[list[str]], list[list[bool]]]:
    matrix: list[list[str]] = []
    header_flags: list[list[bool]] = []
    occupied: dict[tuple[int, int], tuple[str, bool]] = {}
    for raw_row_index, raw_row in enumerate(raw_rows):
        row: list[str] = []
        flags: list[bool] = []
        col_index = 0
        for cell in raw_row:
            while (raw_row_index, col_index) in occupied:
                value, is_header = occupied[(raw_row_index, col_index)]
                row.append(value)
                flags.append(is_header)
                col_index += 1
            text = str(cell.get("text") or "").strip()
            rowspan = int(cell.get("rowspan") or 1)
            colspan = int(cell.get("colspan") or 1)
            is_header = bool(cell.get("header"))
            for row_offset in range(rowspan):
                for col_offset in range(colspan):
                    target = (raw_row_index + row_offset, col_index + col_offset)
                    if row_offset == 0:
                        row.append(text)
                        flags.append(is_header)
                    else:
                        occupied[target] = (text, is_header)
            col_index += colspan
        while (raw_row_index, col_index) in occupied:
            value, is_header = occupied[(raw_row_index, col_index)]
            row.append(value)
            flags.append(is_header)
            col_index += 1
        if row:
            matrix.append(row)
            header_flags.append(flags)
    width = max((len(row) for row in matrix), default=0)
    return ([row + [""] * (width - len(row)) for row in matrix], [flags + [False] * (width - len(flags)) for flags in header_flags])


def inherit_table_context(rows: list[NormalizedTableRow]) -> list[NormalizedTableRow]:
    inherited: dict[str, Any] = {}
    output: list[NormalizedTableRow] = []
    for row in rows:
        metadata = dict(row.metadata)
        platform_before = inherited.get("platform")
        platform_value = _optional_string(metadata.get("platform"))
        if platform_value and platform_before and platform_value != platform_before:
            inherited.pop("change_content", None)
            inherited.pop("phase", None)
        for key in ("platform", "change_content", "phase"):
            raw_value = metadata.get(key)
            value = raw_value if isinstance(raw_value, list) and raw_value else _optional_string(raw_value)
            if value:
                inherited[key] = value
            elif inherited.get(key):
                metadata[key] = inherited[key]
        output.append(NormalizedTableRow(row_index=row.row_index, values=row.values, metadata=metadata))
    return output


def row_metadata(headers: list[str], values: list[str], *, table_name: str | None = None) -> dict[str, Any]:
    normalized = {_normalize_key(header): values[index] if index < len(values) else "" for index, header in enumerate(headers)}
    metadata: dict[str, Any] = {}
    if table_name:
        metadata["table_name"] = table_name
        metadata["section_title"] = table_name
    for key, candidates in {
        "row_number": ("stt", "tt"),
        "platform": ("nen_tang", "doi_tuong", "ung_dung", "he_thong"),
        "feature_name": ("chuc_nang", "chuc_nang_man_hinh", "chuc_nang_man_hinh_ung_dung", "man_hinh", "ten_chuc_nang", "module"),
        "screen_name": ("man_hinh", "chuc_nang_man_hinh", "giao_dien_man_hinh_ung_dung", "giao_dien_man_hinh"),
        "change_content": ("noi_dung", "noi_dung_hieu_chinh", "hieu_chinh_bo_sung", "mo_ta"),
        "phase": ("giai_doan", "phase"),
    }.items():
        value = next((_optional_string(normalized.get(candidate)) for candidate in candidates if _optional_string(normalized.get(candidate))), None)
        if value:
            metadata[key] = value
    if "feature_name" not in metadata:
        metadata["feature_name"] = _first_meaningful(values)
    if "change_content" not in metadata and len(values) >= 3:
        metadata["change_content"] = clean_inline_text(values[-2])
    if len(values) >= 2 and "\n" in str(values[-2] or ""):
        metadata["change_content"] = clean_inline_text(values[-2])
    change_content = metadata.get("change_content")
    if isinstance(change_content, str) and "\n" in change_content:
        parts = [part.strip(" -;.") for part in change_content.splitlines() if part.strip(" -;.") and part.strip(" -;.").casefold() != "p"]
        if len(parts) > 1:
            metadata["change_content"] = parts
        elif parts:
            metadata["change_content"] = parts[0]
    if "phase" not in metadata and values:
        phase = next((value for value in values if re.search(r"giai\s*đoạn\s*\d+", value, flags=re.IGNORECASE)), None)
        if phase:
            metadata["phase"] = phase
    if "platform" not in metadata:
        platform = next((value for value in values if "cms" in value.casefold() or "app" in value.casefold() or "ứng dụng" in value.casefold()), None)
        if platform:
            metadata["platform"] = platform
    metadata = _compact_table_row_metadata(headers=headers, values=values, metadata=metadata)
    row_data = _row_data_from_headers(headers, values)
    if row_data:
        metadata["row_data"] = row_data
    field_name = _field_name_from_row_data(row_data) or metadata.get("feature_name")
    if field_name:
        metadata["field_name"] = field_name
    return {key: value for key, value in metadata.items() if value not in (None, "", [])}


def _row_data_from_headers(headers: list[str], values: list[str]) -> dict[str, str]:
    row_data: dict[str, str] = {}
    width = max(len(headers), len(values))
    for index in range(width):
        header = clean_inline_text(headers[index]) if index < len(headers) else ""
        key = header or f"column_{index + 1}"
        value = clean_inline_text(values[index]) if index < len(values) else ""
        if value:
            row_data[key] = value
    return row_data


def _field_name_from_row_data(row_data: dict[str, str]) -> str | None:
    for key, value in row_data.items():
        normalized_key = _normalize_key(key)
        if normalized_key in {"ten_truong", "field", "field_name", "ten_thuoc_tinh", "chuc_nang", "man_hinh"}:
            return _optional_string(value)
    return None


def _compact_table_row_metadata(*, headers: list[str], values: list[str], metadata: dict[str, Any]) -> dict[str, Any]:
    compact = dict(metadata)
    clean_values = [clean_inline_text(value) for value in values]
    normalized_headers = [_normalize_key(header) for header in headers]
    row_number = _first_header_value(normalized_headers, clean_values, {"stt", "tt"}) or compact.get("row_number")
    if row_number:
        compact["row_number"] = str(row_number)
    platform = (
        _first_header_value(normalized_headers, clean_values, {"nen_tang", "doi_tuong", "ung_dung", "he_thong"})
        or _platform_from_header(headers)
        or compact.get("platform")
    )
    if not platform:
        platform = next((_optional_string(value) for value in clean_values if _looks_like_platform(value)), None)
    if platform:
        compact["platform"] = platform
    feature = _first_header_value(
        normalized_headers,
        clean_values,
        {
            "chuc_nang",
            "chuc_nang_man_hinh",
            "chuc_nang_man_hinh_ung_dung",
            "man_hinh",
            "ten_chuc_nang",
            "module",
            "giao_dien_man_hinh_ung_dung",
            "giao_dien_man_hinh",
        },
    ) or compact.get("feature_name")
    if not feature:
        feature = _feature_from_values(
            clean_values,
            row_number=str(row_number) if row_number else None,
            platform=str(platform) if platform else None,
        )
    if feature:
        compact["feature_name"] = feature
        if any("man_hinh" in header for header in normalized_headers):
            compact["screen_name"] = feature
    change = _first_header_value(
        normalized_headers,
        clean_values,
        {"noi_dung", "noi_dung_hieu_chinh", "hieu_chinh_bo_sung", "mo_ta"},
    ) or compact.get("change_content")
    if not change:
        change = _change_content_from_values(clean_values, row_number=str(row_number) if row_number else None, platform=str(platform) if platform else None, feature_name=str(feature) if feature else None)
    compact_change = _compact_multiline_cell(change)
    if compact_change:
        compact["change_content"] = compact_change
    phase = _first_header_value(normalized_headers, clean_values, {"giai_doan", "phase"}) or compact.get("phase")
    if not phase:
        phase = next((value for value in clean_values if _looks_like_phase(value)), None)
    if phase:
        compact["phase"] = phase
    if platform and not feature and not compact.get("change_content"):
        compact["is_table_marker"] = True
        compact["indexable"] = False
        compact["embedding_enabled"] = False
    return compact

def _first_header_value(headers: list[str], values: list[str], candidates: set[str]) -> str | None:
    for index, header in enumerate(headers):
        if header in candidates and index < len(values):
            value = _optional_string(values[index])
            if value:
                return value
    return None

def _platform_from_header(headers: list[str]) -> str | None:
    for header in headers:
        clean = clean_inline_text(header)
        lowered = clean.casefold()
        if "app" in lowered and "khach" in lowered:
            return "App cham soc khach hang"
        if "cms" in lowered or "website" in lowered:
            return "Website Quan tri noi dung (CMS)"
    return None

def _looks_like_platform(value: str | None) -> bool:
    lowered = str(value or "").casefold()
    return "cms" in lowered or "website" in lowered or ("app" in lowered and "khach" in lowered)

def _looks_like_phase(value: str | None) -> bool:
    return bool(re.search(r"giai\s*(?:doan|Ä‘oáº¡n)\s*\d+", str(value or ""), flags=re.IGNORECASE))

def _feature_from_values(values: list[str], *, row_number: str | None, platform: str | None) -> str | None:
    for value in values:
        clean = _optional_string(value)
        if not clean or clean == row_number or clean == platform:
            continue
        if _looks_like_phase(clean) or _looks_like_platform(clean):
            continue
        return clean
    return None

def _change_content_from_values(values: list[str], *, row_number: str | None, platform: str | None, feature_name: str | None) -> str | None:
    for value in reversed(values):
        clean = _optional_string(value)
        if not clean or clean in {row_number, platform, feature_name}:
            continue
        if _looks_like_phase(clean) or _looks_like_platform(clean):
            continue
        return clean
    return None

def _compact_multiline_cell(value: Any) -> str | list[str] | None:
    if isinstance(value, list):
        parts = [str(item).strip(" -;.") for item in value if str(item).strip(" -;.") and str(item).strip(" -;.").casefold() != "p"]
        return parts if len(parts) > 1 else (parts[0] if parts else None)
    text = clean_inline_text(str(value or ""))
    text = re.sub(r"(?i)^\s*p\s+", "", text).strip()
    parts = [part.strip(" -;.") for part in text.splitlines() if part.strip(" -;.") and part.strip(" -;.").casefold() != "p"]
    if len(parts) <= 1:
        compact_labels = re.findall(r"\bY\s+\d+\b", text)
        if len(compact_labels) > 1:
            parts = compact_labels
    unique = unique_strings(parts)
    if len(unique) > 1:
        return unique
    return unique[0] if unique else None


def build_elements(*, source: dict[str, Any], clean_text: str, tables: list[NormalizedTable], footer_text: str | None, summary_text: str | None, metadata: dict[str, Any]) -> list[NormalizedElement]:
    elements: list[NormalizedElement] = []
    if summary_text:
        elements.append(NormalizedElement("document_summary", summary_text, {"source_summary": True, "chunk_type": "document_summary", "indexable": True}))
    header_text = "\n".join(
        part
        for part in (
            f"Số/ký hiệu: {metadata.get('document_code') or source.get('ky_hieu') or ''}",
            f"Ngày văn bản: {metadata.get('issued_date') or source.get('ngay_vb') or ''}",
            f"Trích yếu: {source.get('trich_yeu') or ''}",
            f"Nơi ban hành: {source.get('noi_ban_hanh') or ''}",
            f"Người ký: {source.get('nguoi_ky') or ''}",
        )
        if part.rsplit(":", 1)[-1].strip()
    )
    if header_text:
        elements.append(NormalizedElement("document_header", header_text, {"chunk_type": "document_header", "indexable": True}))
    body_without_tables = _body_without_tables_or_appendix(clean_text)
    if body_without_tables.strip():
        elements.extend(section_elements_from_body(body_without_tables))
    for table in tables:
        elements.append(
            NormalizedElement(
                "table_parent",
                table_parent_text(table),
                {**table.metadata, "chunk_type": "table_parent", "table_index": table.table_index, "indexable": True},
            )
        )
        for row in table.rows:
            if row.metadata.get("is_table_marker"):
                continue
            elements.append(
                NormalizedElement(
                    "table_row",
                    table_row_text(source=source, table=table, row=row),
                    {
                        **row.metadata,
                        **table.metadata,
                        "chunk_type": "table_row",
                        "table_index": table.table_index,
                        "row_index": row.row_index,
                        "is_table_row": True,
                        "indexable": True,
                    },
                )
            )
        for group_metadata, group_rows in table_group_entries(table):
            if len(group_rows) < 2:
                continue
            group_text = table_group_text(table=table, rows=group_rows, group_name=group_metadata.get("group_name"), platform=group_metadata.get("platform"), phase=group_metadata.get("phase"))
            elements.append(
                NormalizedElement(
                    "table_group",
                    group_text,
                    {
                        "chunk_type": "table_group",
                        **table.metadata,
                        "table_index": table.table_index,
                        **group_metadata,
                        "row_count": len(group_rows),
                        "indexable": True,
                    },
                )
            )
        for column_metadata, column_rows in table_column_entries(table):
            column_text = table_column_text(table=table, column_metadata=column_metadata, rows=column_rows)
            elements.append(
                NormalizedElement(
                    "table_column",
                    column_text,
                    {
                        "chunk_type": "table_column",
                        **table.metadata,
                        "table_index": table.table_index,
                        **column_metadata,
                        "row_count": len(column_rows),
                        "is_table_column": True,
                        "indexable": True,
                    },
                )
            )
    if footer_text:
        elements.append(NormalizedElement("footer_signature", footer_text, {"chunk_type": "footer_signature", "is_footer_or_signature": True, "indexable": False, "embedding_enabled": False}))
    return elements


def section_elements_from_body(body_text: str) -> list[NormalizedElement]:
    sections = fold_short_parent_sections(split_body_into_sections(body_text))
    if not sections:
        return [
            NormalizedElement(
                "document_body",
                body_text.strip(),
                {"chunk_type": "document_body", "indexable": True},
            )
        ]

    elements: list[NormalizedElement] = []
    for section_index, section in enumerate(sections, start=1):
        section_text = ensure_parent_headings_in_section_text(section)
        parts = split_long_section(section_text, max_chars=TEXT_SECTION_MAX_CHARS)
        for part_index, part in enumerate(parts, start=1):
            metadata: dict[str, Any] = {
                "chunk_type": "document_body",
                "indexable": True,
                "section_index": section_index,
                "section_title": section.get("title"),
                "heading_path": section.get("heading_path") or [],
            }
            if len(parts) > 1:
                metadata["section_part"] = part_index
            elements.append(NormalizedElement("document_body", part, metadata))
    return elements


def ensure_parent_headings_in_section_text(section: dict[str, Any]) -> str:
    text = normalize_lines(str(section.get("text") or ""))
    heading_path = [str(value).strip() for value in section.get("heading_path") or [] if str(value).strip()]
    if len(heading_path) <= 1:
        return text
    missing_ancestors = [heading for heading in heading_path[:-1] if heading not in text.splitlines()[: len(heading_path)]]
    if not missing_ancestors:
        return text
    return normalize_lines("\n".join([*missing_ancestors, text]))


def split_body_into_sections(body_text: str) -> list[dict[str, Any]]:
    lines = normalize_lines(body_text).splitlines()
    sections: list[dict[str, Any]] = []
    current_lines: list[str] = []
    current_title: str | None = None
    heading_stack: list[str] = []
    saw_heading = False

    def flush() -> None:
        nonlocal current_lines, current_title
        text = normalize_lines("\n".join(current_lines))
        if text.strip():
            sections.append(
                {
                    "title": current_title,
                    "heading_path": list(heading_stack),
                    "text": text,
                }
            )
        current_lines = []

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            if current_lines:
                current_lines.append("")
            continue
        if is_section_heading(line):
            saw_heading = True
            if current_lines:
                flush()
            current_title = line
            heading_stack = update_heading_stack(heading_stack, line)
            current_lines = [line]
            continue
        if not current_lines:
            current_title = "Mở đầu"
        current_lines.append(raw_line)

    if current_lines:
        flush()

    if not saw_heading:
        return []
    return sections


def fold_short_parent_sections(sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not sections:
        return []

    folded: list[dict[str, Any]] = []
    pending_parents: list[dict[str, Any]] = []
    for index, section in enumerate(sections):
        next_section = sections[index + 1] if index + 1 < len(sections) else None
        heading_path = [str(value) for value in section.get("heading_path") or [] if value]
        next_heading_path = [str(value) for value in next_section.get("heading_path") or [] if value] if next_section else []
        has_child_after = bool(next_heading_path[: len(heading_path)] == heading_path and len(next_heading_path) > len(heading_path))

        if has_child_after and not parent_section_has_standalone_content(section):
            pending_parents.append(section)
            continue

        if pending_parents and heading_path:
            pending_parents = [
                parent
                for parent in pending_parents
                if _heading_path_startswith(heading_path, [str(value) for value in parent.get("heading_path") or [] if value])
            ]
            inherited = list(pending_parents)
            if inherited:
                section = with_parent_context(section, inherited)
        if pending_parents and not heading_path:
            folded.extend(pending_parents)
            pending_parents = []
        folded.append(section)

    return folded


def with_parent_context(section: dict[str, Any], parents: list[dict[str, Any]]) -> dict[str, Any]:
    parent_blocks = [parent_context_text(parent) for parent in parents]
    parent_blocks = [block for block in parent_blocks if block]
    if not parent_blocks:
        return section
    section_text = str(section.get("text") or "").strip()
    separator = "\n" if all(is_compact_parent_context(block) for block in parent_blocks) else "\n\n"
    text = separator.join([*parent_blocks, section_text])
    return {**section, "text": normalize_lines(text)}


def is_compact_parent_context(value: str) -> bool:
    text = normalize_lines(value)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if lines and is_article_heading(lines[0]):
        return True
    return len(lines) <= 2 and len(text) <= 120


def parent_context_text(section: dict[str, Any]) -> str:
    text = normalize_lines(str(section.get("text") or ""))
    lines = text.splitlines()
    title = str(section.get("title") or "").strip()
    if not lines:
        return title
    if title and lines[0].strip() == title:
        body = normalize_lines("\n".join(lines[1:]))
        if body:
            return normalize_lines(f"{title}\n{body}")
        return title
    return text


def parent_section_has_standalone_content(section: dict[str, Any]) -> bool:
    text = normalize_lines(str(section.get("text") or ""))
    title = str(section.get("title") or "").strip()
    content = text
    if title and content.startswith(title):
        content = normalize_lines(content[len(title) :])
    content = content.strip()
    if not content:
        return False
    if len(content) >= PARENT_SECTION_STANDALONE_MIN_CHARS:
        return True
    paragraphs = [part for part in re.split(r"\n{2,}", content) if part.strip()]
    if len(paragraphs) >= 2:
        return True
    lines = [line.strip() for line in content.splitlines() if line.strip()]
    if any(re.match(r"^[-+*•]\s+", line) for line in lines):
        return True
    if re.search(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b|\b\d{1,4}(?:[.,]\d{3})+\b|\b\d+\s*(?:ngày|tháng|năm|VNĐ|%)\b", content, flags=re.IGNORECASE):
        return True
    if re.search(r"(?iu)\b(phải|yêu cầu|điều kiện|thời hạn|hoàn thành|trước ngày|chậm nhất)\b", content):
        return True
    return False


def _heading_path_startswith(path: list[str], prefix: list[str]) -> bool:
    return bool(prefix) and path[: len(prefix)] == prefix


def split_long_section(text: str, *, max_chars: int) -> list[str]:
    clean = normalize_lines(text)
    if len(clean) <= max_chars:
        return [clean]

    paragraphs = re.split(r"\n{2,}", clean)
    parts: list[str] = []
    current: list[str] = []
    current_length = 0
    for paragraph in paragraphs:
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        if len(paragraph) > max_chars:
            if current:
                parts.append("\n\n".join(current).strip())
                current = []
                current_length = 0
            parts.extend(split_oversized_paragraph(paragraph, max_chars=max_chars))
            continue
        projected = current_length + len(paragraph) + (2 if current else 0)
        if current and projected > max_chars:
            parts.append("\n\n".join(current).strip())
            current = [paragraph]
            current_length = len(paragraph)
        else:
            current.append(paragraph)
            current_length = projected
    if current:
        parts.append("\n\n".join(current).strip())
    return [part for part in parts if part.strip()] or [clean]


def split_oversized_paragraph(paragraph: str, *, max_chars: int) -> list[str]:
    sentences = re.split(r"(?<=[.!?。！？])\s+", paragraph)
    if len(sentences) <= 1:
        return [paragraph[index : index + max_chars].strip() for index in range(0, len(paragraph), max_chars)]
    parts: list[str] = []
    current = ""
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        if current and len(current) + len(sentence) + 1 > max_chars:
            parts.append(current.strip())
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        parts.append(current.strip())
    return parts


def is_section_heading(line: str) -> bool:
    clean = clean_inline_text(line).strip()
    if not clean or len(clean) > 220:
        return False
    if clean.startswith(("-", "+", "*", "|")):
        return False
    return bool(SECTION_HEADING_PATTERN.match(normalize_heading_for_matching(clean)))


def update_heading_stack(current: list[str], heading: str) -> list[str]:
    level = heading_level(heading)
    if current and is_appendix_heading(current[0]) and level == 1 and not is_appendix_heading(heading):
        level = 2
    clean = clean_inline_text(heading).strip()
    is_simple_numeric = bool(re.match(r"^\d+[\.\)]", clean))
    has_dieu_ancestor = any(is_article_heading(h) for h in current)
    if is_simple_numeric and has_dieu_ancestor and level == 1:
        level = 2
    stack = list(current)
    if level <= 0:
        return [heading]
    stack = stack[: max(0, level - 1)]
    stack.append(heading)
    return stack


def is_appendix_heading(heading: str) -> bool:
    normalized = strip_vietnamese_accents(clean_inline_text(heading).strip()).casefold()
    return normalized.startswith("phu luc")


def is_article_heading(heading: str) -> bool:
    clean = normalize_heading_for_matching(clean_inline_text(heading).strip())
    return bool(re.match(r"(?iu)^(?:Điều|Dieu)\s+\d+", clean))


def normalize_heading_for_matching(value: str) -> str:
    return str(value or "").replace("Ð", "Đ").replace("ð", "đ")


def heading_level(heading: str) -> int:
    clean = normalize_heading_for_matching(clean_inline_text(heading).strip())
    if re.match(r"(?iu)^(?:#{1,6}\s*)?(?:Phụ\s*lục|[IVXLCDM]{1,8}\s*[\.\)]|[A-Z]\s*[\.\)])", clean):
        return 1
    numeric = re.match(r"^\s*(\d+(?:\.\d+)*)", clean)
    if numeric:
        return numeric.group(1).count(".") + 1
    if is_article_heading(clean):
        return 1
    if re.match(r"(?iu)^(Khoản|Mục)\s+\d+", clean):
        return 2
    return 1


def build_rule_metadata(*, source: dict[str, Any], clean_text: str, tables: list[NormalizedTable]) -> dict[str, Any]:
    ky_hieu = _optional_string(source.get("ky_hieu"))
    doc_codes = [code for code in unique_strings([ky_hieu, *DOC_CODE_PATTERN.findall(clean_text)]) if _is_document_code(code)]
    id_vb = _optional_string(source.get("id_vb"))
    short_codes = [code.split("/", 1)[0] for code in doc_codes if "/" in code]
    identifiers = [value for value in unique_strings([id_vb, *doc_codes, *short_codes]) if _is_identifier_value(value)]
    issued_date = normalize_date(source.get("ngay_vb")) or parse_issued_date_from_text(clean_text)
    return {
        "source_type": "doffice_elasticsearch",
        "id_vb": id_vb,
        "ky_hieu": ky_hieu,
        "document_code": ky_hieu or (doc_codes[0] if doc_codes else None),
        "doc_code": ky_hieu or (doc_codes[0] if doc_codes else None),
        "doc_codes": doc_codes,
        "identifiers": identifiers,
        "trich_yeu": _optional_string(source.get("trich_yeu")),
        "issuer": _optional_string(source.get("noi_ban_hanh")),
        "issuing_org": _optional_string(source.get("noi_ban_hanh")),
        "noi_ban_hanh": _optional_string(source.get("noi_ban_hanh")),
        "nguoi_ky": _optional_string(source.get("nguoi_ky")),
        "signer": _optional_string(source.get("nguoi_ky")),
        "ten_file": _optional_string(source.get("ten_file")),
        "duong_dan": _optional_string(source.get("duong_dan")),
        "ngay_vb": _optional_string(source.get("ngay_vb")),
        "issued_date": issued_date,
        "ngay_tao": _optional_string(source.get("ngay_tao")),
        "ngay_capnhat": _optional_string(source.get("ngay_capnhat")),
        "nam": _optional_int(source.get("nam")),
        "thang": _optional_int(source.get("thang")),
        "source_summary": _optional_string(source.get("tom_tat")),
        "referenced_documents": referenced_documents(clean_text, primary_code=ky_hieu),
        "table_count": len(tables),
        "document_profile": "doffice_admin",
    }


def _body_without_tables_or_appendix(clean_text: str) -> str:
    body = re.sub(r"\[\[TABLE_\d+]]", "\n", clean_text or "")
    return normalize_lines(body)

def table_parent_text(table: NormalizedTable) -> str:
    groups = unique_strings(
        [
            " - ".join(str(value) for value in (row.metadata.get("platform"), row.metadata.get("phase")) if value)
            for row in table.rows
            if not row.metadata.get("is_table_marker")
        ]
    )
    columns = _canonical_table_columns(table.headers)
    lines = [
        f"Bảng: {table.metadata.get('table_name') or f'Bảng {table.table_index + 1}'}",
        f"Số dòng: {sum(1 for row in table.rows if not row.metadata.get('is_table_marker'))}",
    ]
    table_context = table.metadata.get("table_context")
    if table_context:
        lines.append("Ngữ cảnh bảng: " + str(table_context))
    if columns:
        lines.append("Các cột chuẩn hóa: " + ", ".join(columns))
    if groups:
        lines.append("Nhóm chính: " + "; ".join(groups))
    preview_rows = [row for row in table.rows if not row.metadata.get("is_table_marker")][:TABLE_PARENT_PREVIEW_ROWS]
    if preview_rows:
        lines.append("Bảng Markdown xem trước:")
        lines.append(table_rows_to_markdown(table=table, rows=preview_rows))
        if len([row for row in table.rows if not row.metadata.get("is_table_marker")]) > len(preview_rows):
            lines.append(f"Chỉ hiển thị {len(preview_rows)} dòng đầu trong bảng tổng quan.")
    return "\n".join(lines)

def table_group_entries(table: NormalizedTable) -> list[tuple[dict[str, Any], list[NormalizedTableRow]]]:
    rows = [row for row in table.rows if not row.metadata.get("is_table_marker")]
    if not rows:
        return []

    has_logical_groups = any(row.metadata.get("platform") or row.metadata.get("phase") for row in rows)
    if has_logical_groups:
        grouped: dict[tuple[str | None, str | None], list[NormalizedTableRow]] = {}
        for row in rows:
            grouped.setdefault((row.metadata.get("platform"), row.metadata.get("phase")), []).append(row)
        return [
            (
                {
                    "platform": platform,
                    "phase": phase,
                    "group_name": " - ".join(str(value) for value in (platform, phase) if value),
                    "row_start": group_rows[0].row_index,
                    "row_end": group_rows[-1].row_index,
                },
                group_rows,
            )
            for (platform, phase), group_rows in grouped.items()
        ]

    ranged_groups: list[tuple[dict[str, Any], list[NormalizedTableRow]]] = []
    for start in range(0, len(rows), TABLE_GROUP_MAX_ROWS):
        group_rows = rows[start : start + TABLE_GROUP_MAX_ROWS]
        row_start = group_rows[0].row_index
        row_end = group_rows[-1].row_index
        ranged_groups.append(
            (
                {
                    "group_name": f"Rows {row_start}-{row_end}",
                    "row_start": row_start,
                    "row_end": row_end,
                },
                group_rows,
            )
        )
    return ranged_groups


def table_group_text(*, table: NormalizedTable, rows: list[NormalizedTableRow], group_name: str | None = None, platform: str | None = None, phase: str | None = None) -> str:
    features = unique_strings([str(row.metadata.get("feature_name") or "") for row in rows if not row.metadata.get("is_table_marker")])
    changes = unique_strings([str(row.metadata.get("change_content") or "") for row in rows if row.metadata.get("change_content") and not isinstance(row.metadata.get("change_content"), list)])
    resolved_group_name = group_name or " - ".join(str(value) for value in (platform, phase) if value)
    lines = [
        "Nhóm: " + (resolved_group_name or f"Dòng {rows[0].row_index}-{rows[-1].row_index}"),
        f"Bảng: {table.metadata.get('table_name') or f'Bảng {table.table_index + 1}'}",
    ]
    table_context = table.metadata.get("table_context")
    if table_context:
        lines.append("Ngữ cảnh bảng: " + str(table_context))
    if changes:
        lines.append("Nội dung hiệu chỉnh: " + "; ".join(changes[:3]))
    if features:
        lines.append("Các chức năng: " + "; ".join(features[:20]))
    lines.append("Các dòng trong nhóm (Markdown table):")
    lines.append(table_rows_to_markdown(table=table, rows=rows))
    return "\n".join(line for line in lines if line.strip())


def table_column_entries(table: NormalizedTable) -> list[tuple[dict[str, Any], list[NormalizedTableRow]]]:
    rows = [row for row in table.rows if not row.metadata.get("is_table_marker")]
    headers = [clean_inline_text(header) for header in table.headers]
    if not headers or not rows:
        return []

    entries: list[tuple[dict[str, Any], list[NormalizedTableRow]]] = []
    for index, header in enumerate(headers):
        if not header:
            continue
        column_rows = [row for row in rows if index < len(row.values) and clean_inline_text(row.values[index])]
        if not column_rows:
            continue
        context_indexes = _row_context_column_indexes(headers, target_index=index)
        context_headers = [headers[item] for item in context_indexes if item != index and item < len(headers)]
        entries.append(
            (
                {
                    "column_name": header,
                    "column_index": index + 1,
                    "column_value_count": len(column_rows),
                    "column_context_headers": context_headers,
                },
                column_rows,
            )
        )
    return entries


def table_column_text(*, table: NormalizedTable, column_metadata: dict[str, Any], rows: list[NormalizedTableRow]) -> str:
    column_name = str(column_metadata.get("column_name") or "").strip()
    column_index = int(column_metadata.get("column_index") or 0) - 1
    lines = [
        f"Cột bảng: {column_name}",
        f"Bảng: {table.metadata.get('table_name') or f'Bảng {table.table_index + 1}'}",
    ]
    table_context = table.metadata.get("table_context")
    if table_context:
        lines.append("Ngữ cảnh bảng: " + str(table_context))
    context_headers = column_metadata.get("column_context_headers")
    if context_headers:
        lines.append("Cột dùng làm ngữ cảnh hàng: " + ", ".join(str(value) for value in context_headers if value))
    lines.append("Nội dung cột theo từng dòng (Markdown table):")
    lines.append(table_column_to_markdown(table=table, rows=rows, column_index=column_index))
    return "\n".join(line for line in lines if line.strip())


def _row_context_column_indexes(headers: list[str], *, target_index: int) -> list[int]:
    if target_index <= 0:
        return [1] if len(headers) > 1 else []
    return list(range(min(target_index, 2)))


def _canonical_table_columns(headers: list[str]) -> list[str]:
    normalized = {_normalize_key(header) for header in headers}
    if {"stt", "tt"} & normalized and any("chuc_nang" in header or "man_hinh" in header for header in normalized):
        return ["STT", "nền tảng", "chức năng/màn hình", "nội dung hiệu chỉnh/bổ sung", "giai đoạn"]
    return [clean_inline_text(header) for header in headers if clean_inline_text(header)]


def table_row_text(*, source: dict[str, Any], table: NormalizedTable, row: NormalizedTableRow) -> str:
    metadata = row.metadata
    lines: list[str] = []
    table_context = table.metadata.get("table_context")
    if table_context:
        lines.append("Ngữ cảnh bảng: " + str(table_context))
    for label, key in (
        ("Nền tảng", "platform"),
        ("STT", "row_number"),
        ("Chức năng/Màn hình", "feature_name"),
        ("Nội dung hiệu chỉnh/Bổ sung", "change_content"),
        ("Giai đoạn", "phase"),
    ):
        value = metadata.get(key)
        if not value:
            continue
        if isinstance(value, list):
            lines.append(f"{label}:")
            lines.extend(f"- {item}" for item in value if item)
        else:
            lines.append(f"{label}: {value}")
    if table.headers and row.values:
        details = row_to_key_value_markdown(headers=table.headers, values=row.values)
        if details:
            lines.append("Dữ liệu dòng (Markdown table):")
            lines.append(details)
    return "\n".join(line for line in lines if line.strip())


def replace_tables(raw_text: str, tables: list[NormalizedTable], *, replacement: str) -> str:
    ranged_tables = sorted(
        [
            table
            for table in tables
            if table.metadata.get("source_start") is not None and table.metadata.get("source_end") is not None
        ],
        key=lambda table: int(table.metadata.get("source_start") or 0),
    )
    if ranged_tables:
        output: list[str] = []
        cursor = 0
        text = raw_text or ""
        for table in ranged_tables:
            start = int(table.metadata.get("source_start") or 0)
            end = int(table.metadata.get("source_end") or start)
            if start < cursor:
                continue
            output.append(text[cursor:start])
            output.append("\n" + _table_replacement_text(table, replacement=replacement) + "\n")
            cursor = max(cursor, end)
        output.append(text[cursor:])
        return "".join(output)

    iterator = iter(tables)

    def _replace(_match: re.Match[str]) -> str:
        table = next(iterator, None)
        if table is None:
            return "\n"
        content = _table_replacement_text(table, replacement=replacement)
        return "\n" + content + "\n"

    return TABLE_PATTERN.sub(_replace, raw_text or "")


def _table_replacement_text(table: NormalizedTable, *, replacement: str) -> str:
    if replacement == "markdown":
        return table.markdown
    if replacement == "text":
        return table.text
    return f"[[TABLE_{table.table_index + 1}]]"


def html_to_plain_text(value: str) -> str:
    text = html.unescape(str(value or "")).replace("\\n", "\n").replace("\xa0", " ")
    parser = TextHTMLParser()
    parser.feed(text)
    parser.close()
    return normalize_lines(apply_spacing_fixes(strip_markdown_noise(parser.get_text())))


def clean_light_text(value: str) -> str:
    return normalize_lines(apply_spacing_fixes(strip_markdown_noise(html.unescape(str(value or "")).replace("\\n", "\n"))))


def compact_source_summary(value: str, *, max_chars: int = 3000) -> str | None:
    text = clean_light_text(value)
    if not text:
        return None
    text = re.split(r"(?i)chi\s+ti[eế]t\s+n[oộ]i\s+dung\s+hi[eệ]u\s+ch[iỉ]nh", text, maxsplit=1)[0]
    lines: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            if lines and lines[-1]:
                lines.append("")
            continue
        if stripped.startswith("|") or re.fullmatch(r"[:\-\s|]+", stripped):
            continue
        lines.append(stripped)
    compact = normalize_lines("\n".join(lines))
    if len(compact) <= max_chars:
        return compact or None
    return compact[:max_chars].rsplit(" ", 1)[0].rstrip(" ,;:") + "..."


def clean_inline_text(value: str) -> str:
    text = html.unescape(str(value or "")).replace("\xa0", " ").replace("&nbsp;", " ")
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    return normalize_lines(apply_spacing_fixes(strip_markdown_noise(text)))


def strip_markdown_noise(value: str) -> str:
    text = PAGE_MARKER_PATTERN.sub("\n", str(value or ""))
    text = re.sub(r"\*\*(.*?)\*\*", r"\1", text)
    text = re.sub(r"(?i)\[\s*image\s*]", " ", text)
    text = re.sub(r"(?m)^\s{0,3}#{1,6}\s+", "", text)
    text = re.sub(r"(?m)^(\s*)[-*+]\s+[-*+]\s+", r"\1- ", text)
    return text


def apply_spacing_fixes(value: str) -> str:
    text = str(value or "")
    for broken, fixed in SPACING_FIXES.items():
        text = re.sub(re.escape(broken), fixed, text, flags=re.IGNORECASE)
    for broken, fixed in MOJIBAKE_TEXT_FIXES.items():
        text = text.replace(broken, fixed)
    text = re.sub(r"\b([A-Z])\s+(?=[^\W\d_])", r"\1", text, flags=re.UNICODE)
    return text


def split_footer_signature(value: str) -> tuple[str, str | None]:
    text = value or ""
    appendix_match = APPENDIX_MARKER_PATTERN.search(text)
    footer_search_text = text
    match = (
        FOOTER_MARKER_PATTERN.search(footer_search_text)
        or ASCII_FOOTER_MARKER_PATTERN.search(footer_search_text)
        or re.search(r"(?im)^\s*(Noi\s*nhan|KT\.\s*GIAM\s+DOC|PHO\s+GIAM\s+DOC)\b", footer_search_text)
    )
    if not match:
        return text.strip(), None
    body_end = match.start()
    footer_end = appendix_match.start() if appendix_match and appendix_match.start() > match.start() else len(text)
    body = text[:body_end].strip()
    footer = text[match.start() : footer_end].strip() or None
    return body, footer


def normalize_lines(value: str) -> str:
    lines = [re.sub(r"[\t ]+", " ", line).strip() for line in str(value or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    compact: list[str] = []
    blank = False
    for line in lines:
        if not line:
            if compact and not blank:
                compact.append("")
            blank = True
            continue
        compact.append(line)
        blank = False
    return "\n".join(compact).strip()


def table_to_markdown(headers: list[str], rows: list[list[str]]) -> str:
    if not headers:
        return "\n".join(" | ".join(_markdown_table_cell(value) for value in row) for row in rows)
    canonical_headers = _canonical_table_columns(headers)
    if canonical_headers != [clean_inline_text(header) for header in headers if clean_inline_text(header)] and len(canonical_headers) == 5:
        output = ["| " + " | ".join(_markdown_table_cell(header) for header in canonical_headers) + " |", "| " + " | ".join("---" for _ in canonical_headers) + " |"]
        for row in rows:
            metadata = _compact_table_row_metadata(headers=headers, values=row, metadata={})
            if metadata.get("is_table_marker"):
                continue
            output.append(
                "| "
                + " | ".join(
                    _markdown_table_cell(metadata.get(key) or "")
                    for key in ("row_number", "platform", "feature_name", "change_content", "phase")
                )
                + " |"
            )
        return "\n".join(output)
    output = ["| " + " | ".join(_markdown_table_cell(header) for header in headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    output.extend(
        "| " + " | ".join(_markdown_table_cell(value) for value in row[: len(headers)] + [""] * max(0, len(headers) - len(row))) + " |"
        for row in rows
    )
    return "\n".join(output)


def table_rows_to_markdown(*, table: NormalizedTable, rows: list[NormalizedTableRow]) -> str:
    return table_to_markdown(table.headers, [row.values for row in rows if not row.metadata.get("is_table_marker")])


def table_column_to_markdown(*, table: NormalizedTable, rows: list[NormalizedTableRow], column_index: int) -> str:
    headers = [clean_inline_text(header) for header in table.headers]
    context_indexes = [index for index in _row_context_column_indexes(headers, target_index=column_index) if index != column_index]
    output = ["| Dòng | Ngữ cảnh hàng | Nội dung cột |", "| --- | --- | --- |"]
    for row in rows:
        if row.metadata.get("is_table_marker") or column_index >= len(row.values):
            continue
        value = clean_inline_text(row.values[column_index])
        if not value:
            continue
        context_parts = []
        for context_index in context_indexes:
            if context_index >= len(row.values):
                continue
            header = headers[context_index] if context_index < len(headers) else ""
            context_value = clean_inline_text(row.values[context_index])
            if header and context_value:
                context_parts.append(f"{header}: {context_value}")
            elif context_value:
                context_parts.append(context_value)
        row_label = row.metadata.get("row_number") or row.row_index
        output.append(
            "| "
            + " | ".join(
                (
                    _markdown_table_cell(row_label),
                    _markdown_table_cell("; ".join(context_parts)),
                    _markdown_table_cell(value),
                )
            )
            + " |"
        )
    return "\n".join(output) if len(output) > 2 else ""


def row_to_key_value_markdown(*, headers: list[str], values: list[str]) -> str:
    rows: list[str] = ["| Cột | Nội dung |", "| --- | --- |"]
    for index, value in enumerate(values):
        if index >= len(headers):
            continue
        header = clean_inline_text(headers[index])
        clean_value = clean_inline_text(value)
        if not header or not clean_value:
            continue
        rows.append(f"| {_markdown_table_cell(header)} | {_markdown_table_cell(clean_value)} |")
    return "\n".join(rows) if len(rows) > 2 else ""


def _markdown_table_cell(value: Any) -> str:
    if isinstance(value, list):
        text = "<br>".join(clean_inline_text(item) for item in value if clean_inline_text(item))
    else:
        text = clean_inline_text(str(value or ""))
    text = text.replace("|", "\\|")
    return re.sub(r"\s*\n\s*", "<br>", text)


def table_to_text(headers: list[str], rows: list[list[str]]) -> str:
    output: list[str] = []
    for row in rows:
        if headers:
            output.append(" | ".join(f"{headers[index]}: {value}" for index, value in enumerate(row) if value and index < len(headers)))
        else:
            output.append(" | ".join(value for value in row if value))
    return "\n".join(line for line in output if line.strip())


def _legacy_referenced_documents(text: str, *, primary_code: str | None) -> list[str]:
    refs: list[str] = []
    for match in DOC_CODE_PATTERN.finditer(text or ""):
        code = match.group(1)
        if primary_code and code == primary_code:
            continue
        if DATE_PATTERN.fullmatch(code) or re.fullmatch(r"\d{1,2}/\d{4}", code):
            continue
        window = text[match.end() : match.end() + 40]
        date_match = DATE_PATTERN.search(window)
        refs.append(f"{code} ngày {date_match.group(0)}" if date_match else code)
    return unique_strings(refs)


def referenced_documents(text: str, *, primary_code: str | None) -> list[dict[str, str | None]]:  # type: ignore[no-redef]
    refs: list[dict[str, str | None]] = []
    seen: set[str] = set()
    for match in DOC_CODE_PATTERN.finditer(text or ""):
        code = match.group(1)
        if primary_code and code == primary_code:
            continue
        if not _is_document_code(code):
            continue
        if code.casefold() in seen:
            continue
        seen.add(code.casefold())
        window = text[match.end() : match.end() + 220]
        date_match = DATE_PATTERN.search(window)
        subject = _optional_string(re.sub(r"^[,.;:\s-]+", "", window.split(".", 1)[0]))
        refs.append(
            {
                "document_code": code,
                "date": date_match.group(0) if date_match else None,
                "issuer": None,
                "subject": subject,
            }
        )
    return refs


def parse_issued_date_from_text(text: str) -> str | None:
    clean = _optional_string(text)
    if not clean:
        return None
    match = VIETNAMESE_TEXT_DATE_PATTERN.search(clean) or re.search(
        r"ng\S*y\s+(\d{1,2})\s+th\S*ng\s+(\d{1,2})\s+n\S*m\s+(\d{4})",
        clean,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    day, month, year = match.groups()
    return f"{int(day):02d}/{int(month):02d}/{year}"


def normalize_date(value: Any) -> str | None:
    clean = _optional_string(value)
    if not clean:
        return None
    match = DATE_PATTERN.search(clean)
    if match:
        day, month, year = match.group(0).split("/")
        return f"{int(day):02d}/{int(month):02d}/{year}"
    iso_match = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", clean)
    if iso_match:
        year, month, day = iso_match.groups()
        return f"{int(day):02d}/{int(month):02d}/{year}"
    return clean


def sha256_text(value: str) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def sha256_json(value: Any) -> str:
    return sha256_text(repr(value))


def unique_strings(values: list[Any]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        clean = _optional_string(value)
        if not clean:
            continue
        key = clean.casefold()
        if key in seen:
            continue
        seen.add(key)
        output.append(clean)
    return output


def _is_document_code(value: Any) -> bool:
    code = _optional_string(value)
    if not code:
        return False
    if DATE_PATTERN.fullmatch(code) or MONTH_YEAR_PATTERN.fullmatch(code):
        return False
    if re.fullmatch(r"\d{4}", code):
        return False
    if "/" in code:
        suffix = code.split("/", 1)[1]
        return any(char.isalpha() for char in suffix)
    return True

def _is_identifier_value(value: Any) -> bool:
    clean = _optional_string(value)
    if not clean:
        return False
    if clean.casefold() in {"nam", "tin"}:
        return False
    if DATE_PATTERN.fullmatch(clean) or MONTH_YEAR_PATTERN.fullmatch(clean):
        return False
    if clean.isdigit() and len(clean) < 3:
        return False
    return True

def _normalize_key(value: str) -> str:
    text = re.sub(r"[^\wÀ-ỹĐđ]+", "_", strip_vietnamese_accents(value).casefold(), flags=re.UNICODE)
    return re.sub(r"_+", "_", text).strip("_")


def strip_vietnamese_accents(value: str) -> str:
    import unicodedata

    normalized = unicodedata.normalize("NFD", value or "")
    return "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn").replace("Đ", "D").replace("đ", "d")


def _positive_int(value: Any, *, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _optional_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    clean = " ".join(str(value).split()).strip()
    return clean or None


def _first_meaningful(values: list[str]) -> str | None:
    return next((value for value in values if value and not re.fullmatch(r"\d+\.?", value.strip())), None)
