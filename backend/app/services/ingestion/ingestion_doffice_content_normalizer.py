from __future__ import annotations

import hashlib
import html
import logging
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any

FOOTER_MARKER_PATTERN = re.compile(r"(?im)^\s*(Nơi nhận|KT\.\s*GIÁM ĐỐC|PHÓ GIÁM ĐỐC|Lưu:\s*VT)\b")
PAGE_MARKER_PATTERN = re.compile(r"(?im)^\s*---\s*Page\s+\d+\s*---\s*$")
APPENDIX_MARKER_PATTERN = re.compile(r"(?im)^\s*(PHỤ\s*LỤC|PHU\s*LUC)\b")
# Cửa sổ HTML (ký tự) ngay TRƯỚC mỗi bảng để suy tên bảng — đủ lấy vài dòng heading,
# tránh chuyển toàn bộ nội dung trước bảng (O(n²) trên văn bản lớn).
_TABLE_NAME_WINDOW = 8000
ASCII_FOOTER_MARKER_PATTERN = re.compile(
    r"(?im)^\s*(Noi\s+nhan|N[ơo]i\s+nhận|KT\.\s*GIAM\s+DOC|PHO\s+GIAM\s+DOC|Luu:\s*VT)\b"
)
TABLE_PATTERN = re.compile(r"(?is)<table\b.*?</table>")
DOC_CODE_PATTERN = re.compile(r"\b(?!\d{1,2}/\d{1,2}/\d{2,4}\b)(\d{1,6}/[A-ZÀ-ỸĐ0-9][A-ZÀ-ỸĐ0-9+._\-]{1,}(?:/[A-ZÀ-ỸĐ0-9+._\-]+)*)\b", re.UNICODE)
DATE_PATTERN = re.compile(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b")
MONTH_YEAR_PATTERN = re.compile(r"\b\d{1,2}/\d{4}\b")
VIETNAMESE_TEXT_DATE_PATTERN = re.compile(r"ngày\s+(\d{1,2})\s+tháng\s+(\d{1,2})\s+năm\s+(\d{4})", re.IGNORECASE)
EMAIL_PATTERN = re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b")
PHONE_PATTERN = re.compile(r"(?<!\d)(?:\+?84|0)\d(?:[\s.\-]?\d){7,10}(?!\d)")
LONG_LIST_LINE_PATTERN = re.compile(r"^\s*(?:[-+*]|\d+[.)])\s+\S+")

logger = logging.getLogger(__name__)

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
    "D ịch": "Dịch",
    "báoc áo": "báo cáo",
    "đểp/h": "để p/h",
}


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
    tables = parse_html_tables(raw_text)
    base_markdown_text = replace_tables(raw_text, tables, replacement="markdown")
    base_plain_text = html_to_plain_text(replace_tables(raw_text, tables, replacement="placeholder"))
    body_text, footer_text = split_footer_signature(base_plain_text)
    appendix_text, appendix_span = extract_appendix_text(base_plain_text)
    # Footer bị tách khỏi body nên không có vị trí -> tìm lại trong base_plain_text để
    # chunk footer sắp ĐÚNG thứ tự đọc (cuối thân thư, TRƯỚC phụ lục), không bị reorder
    # đẩy xuống sau phụ lục. Dùng cùng hệ tọa độ với appendix/table (base_plain_text).
    footer_span = (
        _source_span_for_text(base_plain_text, footer_text, chunk_type="footer_signature")
        if footer_text
        else None
    )
    # reading_pos của mỗi bảng = vị trí placeholder [[TABLE_N]] trong base_plain_text.
    # Đây là hệ tọa độ NHẤT QUÁN với body/footer/appendix (span gốc của bảng theo
    # raw_text HTML không so sánh được) -> chunk sắp đúng thứ tự đọc khi reorder.
    for _ph_index, _ph_match in enumerate(re.finditer(r"\[\[TABLE_\d+]]", base_plain_text)):
        if _ph_index < len(tables):
            tables[_ph_index].metadata["reading_pos"] = _ph_match.start()
    base_clean_text = normalize_lines(apply_spacing_fixes(strip_markdown_noise(body_text)))
    metadata = build_rule_metadata(source=source, clean_text=base_clean_text, tables=tables)
    # FIX 3A: ``ngay_vb`` thường null và ngày ban hành nằm ở khối chữ ký (đã bị
    # ``split_footer_signature`` tách khỏi body). Thử bắt lại từ plain text đầy đủ
    # (gồm cả footer) khi vẫn thiếu issued_date.
    if not metadata.get("issued_date"):
        fallback_issued_date = parse_issued_date_from_text(base_plain_text)
        if fallback_issued_date:
            metadata = {**metadata, "issued_date": fallback_issued_date}
    summary_text = build_document_summary(source=source, clean_text=base_clean_text, metadata=metadata)
    metadata_preamble = build_doffice_metadata_preamble(source=source, metadata=metadata)
    metadata = {**metadata, "metadata_preamble": metadata_preamble}
    clean_text = prepend_metadata_preamble(base_clean_text, metadata_preamble)
    plain_text = prepend_metadata_preamble(base_plain_text, metadata_preamble)
    markdown_text = prepend_metadata_preamble(base_markdown_text, metadata_preamble)
    elements = build_elements(source=source, clean_text=base_clean_text, tables=tables, footer_text=footer_text, footer_span=footer_span, summary_text=summary_text, metadata=metadata, appendix_text=appendix_text, appendix_span=appendix_span)
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


def build_document_summary(*, source: dict[str, Any], clean_text: str, metadata: dict[str, Any]) -> str | None:
    candidates = [
        _optional_string(source.get("trich_yeu")),
        _optional_string(source.get("tom_tat")),
    ]
    for line in clean_text.splitlines():
        clean = clean_inline_text(line)
        if not clean or _summary_line_is_noise(clean):
            continue
        lowered = clean.casefold()
        if any(token in lowered for token in ("quyết định", "quyet dinh", "đào tạo", "dao tao", "kinh phí", "kinh phi", "thời gian", "thoi gian", "địa điểm", "dia diem")):
            candidates.append(clean)
    # Chỉ thêm issuer vào phần tóm tắt dạng văn xuôi. KHÔNG thêm issued_date thô:
    # ``_sanitize_summary_text`` (DOC_CODE_PATTERN.sub) sẽ băm "11/08/2025" thành
    # "11/". Ngày ban hành đã có ở trường metadata + preamble cấu trúc.
    candidates.append(_optional_string(metadata.get("issuer")))
    summary = " ".join(_dedupe_sentences(candidates))
    summary = _sanitize_summary_text(summary)
    return _limit_words(summary, max_words=200) or None


def _summary_line_is_noise(line: str) -> bool:
    clean = clean_inline_text(line)
    if not clean:
        return True
    if clean.startswith("|") or "|" in clean:
        return True
    if EMAIL_PATTERN.search(clean) or PHONE_PATTERN.search(clean):
        return True
    if LONG_LIST_LINE_PATTERN.match(clean):
        return True
    lowered = clean.casefold()
    return any(token in lowered for token in ("stt", "số điện thoại", "so dien thoai", "email", "@")) and len(clean) < 160


def _sanitize_summary_text(value: str) -> str:
    text = clean_inline_text(value)
    text = EMAIL_PATTERN.sub("", text)
    text = PHONE_PATTERN.sub("", text)
    text = DOC_CODE_PATTERN.sub("", text)
    text = re.sub(r"\bID[_\s-]*VB\s*[:=]?\s*\d+\b", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+([,.;:])", r"\1", text)
    text = re.sub(r"\s{2,}", " ", text)
    return text.strip(" ,;:")


def _dedupe_sentences(values: list[Any]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        clean = clean_inline_text(value)
        if not clean or _summary_line_is_noise(clean):
            continue
        key = clean.casefold()
        if key in seen:
            continue
        seen.add(key)
        output.append(clean.rstrip(".;") + ".")
    return output


def _limit_words(value: str, *, max_words: int) -> str:
    words = value.split()
    if len(words) <= max_words:
        return value.strip()
    return " ".join(words[:max_words]).rstrip(" ,;:") + "..."


def _source_span_for_text(clean_text: str, text: str, *, chunk_type: str) -> dict[str, int]:
    haystack = clean_text or ""
    needle = clean_inline_text(text)
    if needle:
        index = haystack.find(needle)
        if index >= 0:
            return {"start": index, "end": index + len(needle)}
    # Fallback BÌNH THƯỜNG cho chunk tổng hợp (summary/header/footer) -> debug, không
    # spam WARNING ra console job.
    logger.debug("Unable to determine exact DOffice source_span for %s; using document-scope fallback.", chunk_type)
    return {"start": 0, "end": len(haystack)}


def parse_html_tables(raw_text: str) -> list[NormalizedTable]:
    tables: list[NormalizedTable] = []
    # Tính 1 LẦN cho cả văn bản (thay vì search trong infer_table_name mỗi bảng).
    has_appendix = bool(APPENDIX_MARKER_PATTERN.search(raw_text or ""))
    for table_index, match in enumerate(TABLE_PATTERN.finditer(raw_text or "")):
        table_name = infer_table_name(
            raw_text or "", match.start(), table_index=table_index, has_appendix=has_appendix
        )
        parser = DofficeHTMLTableParser()
        parser.feed(match.group(0))
        parser.close()
        matrix, header_flags = expand_table_matrix(parser.raw_rows)
        if not matrix:
            continue
        headers, data_rows = normalize_table_header_and_rows(matrix, header_flags)
        rows = [
            NormalizedTableRow(
                row_index=index + 1,
                values=[clean_inline_text(value) for value in row],
                metadata={
                    **row_metadata(headers, row, table_name=table_name),
                    "row_cells": _row_cells(headers, row),
                    "row_key": _row_key(headers, row, index=index + 1),
                },
            )
            for index, row in enumerate(data_rows)
            if _is_meaningful_table_data_row(headers, row)
        ]
        # Bảng bị tách trang (phần 2+) không có heading riêng -> rơi về "Bảng DOffice N".
        # Nếu là phần tiếp của bảng phụ lục liền trước (cùng cấu trúc cột, đánh số tiếp)
        # -> kế thừa tên cha + "(tiếp theo)" để không mất ngữ cảnh phụ lục.
        if table_name.startswith("Bảng DOffice ") and tables:
            prev_name = str(tables[-1].metadata.get("table_name") or "")
            if (
                prev_name
                and not prev_name.startswith("Bảng DOffice ")
                and _is_continuation_table(list(tables[-1].headers), headers, data_rows)
            ):
                base_name = re.sub(r"\s*\(tiếp theo\)\s*$", "", prev_name)
                table_name = f"{base_name} (tiếp theo)"
        logical_table_id = _logical_table_id(table_name=table_name, headers=headers)
        table_id = f"{logical_table_id}:physical:{table_index + 1}"
        tables.append(
            NormalizedTable(
                table_index=table_index,
                headers=headers,
                rows=inherit_table_context(rows),
                markdown=table_to_markdown(headers, data_rows),
                text=table_to_text(headers, data_rows),
                metadata={
                    "table_id": table_id,
                    "logical_table_id": logical_table_id,
                    "table_index": table_index,
                    "physical_table_index": table_index,
                    "physical_tables": [table_index],
                    "table_name": table_name,
                    "table_title": table_name,
                    "table_kind": _table_kind(headers),
                    "section_title": table_name,
                    "row_count": len(rows),
                    "column_count": max((len(row) for row in matrix), default=0),
                    "columns": _canonical_table_columns(headers),
                    "source_span": {"start": match.start(), "end": match.end()},
                },
            )
        )
    return tables


def _logical_table_id(*, table_name: str, headers: list[str]) -> str:
    identity = "|".join(
        [
            "doffice-table",
            clean_inline_text(table_name).casefold(),
            ",".join(clean_inline_text(header).casefold() for header in headers),
        ]
    )
    digest = hashlib.sha1(identity.encode("utf-8")).hexdigest()[:12]
    return f"ltbl_{digest}"


def _table_kind(headers: list[str]) -> str:
    normalized = {_normalize_key(header) for header in headers}
    if {"nhan_su", "nhan_su_de_xuat", "nguoi_phu_trach"} & normalized:
        return "assignment"
    if {"deadline", "thoi_han", "han_xu_ly"} & normalized:
        return "deadline"
    if {"muc_huong", "so_ngay", "che_do"} & normalized:
        return "legal_benefit"
    if {"stt", "tt"} & normalized:
        return "data_table"
    return "table"


def _row_cells(headers: list[str], row: list[str]) -> dict[str, str]:
    cells: dict[str, str] = {}
    for index, header in enumerate(headers):
        key = clean_inline_text(header)
        if not key or index >= len(row):
            continue
        value = clean_inline_text(row[index])
        if value:
            cells[key] = value
    return cells


def _row_key(headers: list[str], row: list[str], *, index: int) -> str:
    normalized_headers = [_normalize_key(header) for header in headers]
    clean_row = [clean_inline_text(value) for value in row]
    for candidates in (
        {"ho_ten", "ten_can_bo", "can_bo", "nhan_su", "nguoi_phu_trach", "nguoi_tham_gia", "person_name", "staff"},
        {"email", "e_mail", "mail"},
        {"so_dien_thoai", "dien_thoai", "sdt", "phone"},
    ):
        value = _first_header_value(normalized_headers, clean_row, candidates)
        if value:
            return value[:120]
    for value in row:
        clean = clean_inline_text(value)
        if clean:
            return clean[:80]
    row_number = _first_header_value(normalized_headers, clean_row, {"stt", "tt"})
    if row_number:
        return row_number
    return str(index)

# Heading mục/bảng trong phụ lục. LƯU Ý: KHÔNG dùng "\b" sau "(\d+)" — sau ")" là
# dấu cách (cả hai non-word) nên "\b" luôn fail, khiến heading "(7) F10_..." trượt.
_APPENDIX_HEADING_NUM = re.compile(r"^\(\d+\)\s*\S")  # "(1) ...", "(7) F10_..."
_APPENDIX_HEADING_KW = re.compile(
    r"(?iu)^(?:phụ\s*lục|phu\s*luc|appendix|tên\s+bảng|ten\s+bang|mối\s+quan\s+hệ|"
    r"moi\s+quan\s+he|danh\s+sách|danh\s+sach|\d+(?:\.\d+)*[.)]\s)"
)
_FEATURE_CLASS_RE = re.compile(r"\bF\d{2}_[A-Za-zÀ-ỹ]")  # mã lớp dữ liệu F08_/F10_...


def _appendix_heading_name(line: str) -> str | None:
    """Tên heading phụ lục nếu dòng là tiêu đề mục/bảng (đã gỡ markdown ``#``/``>``).

    Heading OCR đôi khi có tiền tố ``####`` (vd ``#### (1) Mối quan hệ...``) -> phải gỡ
    để bắt được ``(N) ...``; nếu không sẽ khớp nhầm dòng caption ngay phía trên.
    """
    clean = re.sub(r"^[#>\s]+", "", clean_inline_text(line)).strip(" :-")
    if not clean or len(clean) > 160 or "|" in clean:
        return None
    if (
        _APPENDIX_HEADING_NUM.match(clean)
        or _APPENDIX_HEADING_KW.match(clean)
        or _FEATURE_CLASS_RE.search(clean)
    ):
        return clean
    return None


def _is_continuation_table(prev_headers: list[str], headers: list[str], data_rows: list[list[str]]) -> bool:
    """True nếu bảng hiện tại là PHẦN TIẾP của bảng liền trước (do tách trang).

    Dấu hiệu: cùng số cột, cột đầu là TT/STT ở cả hai, và dòng dữ liệu đầu của bảng
    hiện tại đánh số > 1 (vd 23, 9, 20 — tiếp nối; bảng mới sẽ bắt đầu từ 1).
    """
    if not headers or not prev_headers or len(prev_headers) != len(headers):
        return False
    if _normalize_key(headers[0]) not in {"tt", "stt"} or _normalize_key(prev_headers[0]) not in {"tt", "stt"}:
        return False
    first = next((clean_inline_text(row[0]) for row in data_rows if row and clean_inline_text(row[0])), "")
    return bool(re.fullmatch(r"\d+", first)) and int(first) > 1


def infer_table_name(raw_text: str, table_start: int, *, table_index: int, has_appendix: bool = False) -> str:
    # Chỉ chuyển CỬA SỔ ~8000 ký tự HTML ngay trước bảng sang plain text (đủ lấy ~8 dòng
    # heading). Trước đây chuyển TOÀN BỘ ``[:table_start]`` cho MỖI bảng -> O(bảng × kích
    # thước) = O(n²): văn bản 3MB / 80 bảng mất ~45s. Cửa sổ -> O(n), nhanh ~160 lần.
    window_start = max(0, table_start - _TABLE_NAME_WINDOW)
    before = html_to_plain_text((raw_text or "")[window_start:table_start])
    lines = [line.strip(" :-") for line in before.splitlines() if line.strip(" :-")]
    for line in reversed(lines[-8:]):
        lowered = line.casefold()
        if "danh sách" in lowered or "danh sach" in lowered:
            return clean_inline_text(line)
        if "danh" in lowered and ("chuc" in lowered or "chức" in lowered):
            return clean_inline_text(line)
        if "giao" in lowered and "man" in lowered:
            return clean_inline_text(line)
    # Bảng trong PHỤ LỤC thường có heading riêng (Phụ lục NN, (N) F0X_..., (N) Mối quan
    # hệ..., Tên bảng dữ liệu...) -> lấy heading gần nhất làm tên bảng thay vì rơi về
    # "Bảng DOffice N". Gate ``has_appendix`` tính 1 LẦN ở parse_html_tables (tránh search
    # toàn bộ mỗi bảng); heading cụ thể vẫn tìm trong cửa sổ ``before``.
    if has_appendix:
        window = lines[-6:]
        for idx in range(len(window) - 1, -1, -1):
            name = _appendix_heading_name(window[idx])
            if not name:
                continue
            # Tiêu đề "Phụ lục NN" có dòng mô tả ngay dưới (vd "PHƯƠNG ÁN SÁP NHẬP...")
            # -> gộp để tên bảng đầy đủ: "Phụ lục 01 — Phương án sáp nhập...".
            if re.match(r"(?iu)^(phụ\s*lục|phu\s*luc|appendix)\b", name):
                subtitle: list[str] = []
                for follow in window[idx + 1:]:
                    clean = re.sub(r"^[#>\s]+", "", clean_inline_text(follow)).strip(" :-")
                    if not clean or "|" in clean or _appendix_heading_name(clean):
                        break
                    subtitle.append(clean)
                if subtitle:
                    name = f"{name} — {' '.join(subtitle)}"
            return name
    return f"Bảng DOffice {table_index + 1}"


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



def normalize_table_header_and_rows(
    matrix: list[list[str]],
    header_flags: list[list[bool]],
) -> tuple[list[str], list[list[str]]]:
    """Return semantically useful headers and data rows for DOffice HTML tables.

    DOffice/PDF extraction often expands multi-row table headers into ordinary
    rows. In procurement tables this produced bogus rows such as ``hiện``,
    ``gói``, ``thầu`` and ``(*)`` under the STT column. The normalizer keeps
    the true header, discards short header fragments, and realigns data rows
    that start with footnote markers before the actual STT value.
    """
    clean_matrix = [[clean_inline_text(value) for value in row] for row in matrix]
    if not clean_matrix:
        return [], []

    first_row = clean_matrix[0]
    first_flags = header_flags[0] if header_flags else []
    has_header = bool(any(first_flags)) or len(clean_matrix) > 1 or _row_looks_like_header(first_row)
    if not has_header:
        return [], [_trim_empty_tail(row) for row in clean_matrix]

    header_rows = [first_row]
    data_start = 1
    has_stt_header = any(_normalize_key(cell) in {"stt", "tt"} for cell in first_row)
    if has_stt_header:
        while data_start < len(clean_matrix) and _looks_like_continued_header_row(clean_matrix[data_start]):
            header_rows.append(clean_matrix[data_start])
            data_start += 1

    headers = _merge_table_header_rows(header_rows)
    data_rows = [_align_table_data_row(headers, row) for row in clean_matrix[data_start:]]
    return headers, [_trim_empty_tail(row) for row in data_rows]


def _row_looks_like_header(row: list[str]) -> bool:
    normalized = {_normalize_key(cell) for cell in row if clean_inline_text(cell)}
    return bool(normalized & {"stt", "tt", "ten_nha_thau", "ma_so_thue", "chuc_nang", "man_hinh"})


def _looks_like_continued_header_row(row: list[str]) -> bool:
    values = [clean_inline_text(value) for value in row if clean_inline_text(value)]
    if not values:
        return True
    if _row_has_numbered_stt(row):
        return False
    # Keep category/group rows such as "Website Quản trị nội dung (CMS)" as data.
    if any(_looks_like_platform(value) for value in values):
        return False
    long_values = [value for value in values if len(value) > 24]
    if long_values:
        return False
    short_header_fragments = 0
    for value in values:
        lowered = value.casefold().strip()
        if re.fullmatch(r"\(?\*+\)?|\(?\*{1,3}\)?", lowered):
            short_header_fragments += 1
            continue
        if re.fullmatch(r"[a-zà-ỹđ0-9()*/.,\-\s]{1,24}", lowered, flags=re.IGNORECASE):
            short_header_fragments += 1
    return short_header_fragments == len(values)


def _row_has_numbered_stt(row: list[str]) -> bool:
    for value in row[:5]:
        clean = clean_inline_text(value)
        if not clean:
            continue
        if re.fullmatch(r"\d{1,4}[.)]?", clean):
            return True
    return False


def _merge_table_header_rows(header_rows: list[list[str]]) -> list[str]:
    width = max((len(row) for row in header_rows), default=0)
    headers: list[str] = []
    for index in range(width):
        parts: list[str] = []
        for row in header_rows:
            value = clean_inline_text(row[index]) if index < len(row) else ""
            if not value or value in parts:
                continue
            if parts and _should_skip_header_continuation(parts[0], value):
                continue
            if _looks_like_header_footnote(value):
                if parts and not _should_skip_header_continuation(parts[0], value):
                    parts[-1] = f"{parts[-1]} {value}"
                continue
            parts.append(value)
        header = clean_inline_text(" ".join(parts))
        header = re.sub(r"\s*-\s*", " ", header)
        headers.append(header)
    return _trim_empty_tail(headers)


def _looks_like_header_footnote(value: str) -> bool:
    return bool(re.fullmatch(r"\(?\*{1,3}\)?", clean_inline_text(value)))


def _should_skip_header_continuation(base_header: str, value: str) -> bool:
    base = _normalize_key(base_header)
    clean = clean_inline_text(value)
    if not clean:
        return True
    stable_headers = {
        "stt",
        "tt",
        "ten_nha_thau",
        "ma_so_thue",
        "gia_du_thau",
        "gia_du_thau_vnd",
        "gia_du_thau_vnđ",
        "gia_trung_thau",
        "noi_dung_khac",
    }
    if base in stable_headers and len(clean) <= 24:
        return True
    if base.startswith("gia_") and len(clean) <= 24:
        return True
    return False


def _align_table_data_row(headers: list[str], row: list[str]) -> list[str]:
    clean_row = [clean_inline_text(value) for value in row]
    normalized_headers = [_normalize_key(header) for header in headers]
    if normalized_headers and normalized_headers[0] in {"stt", "tt"}:
        for index, value in enumerate(clean_row[: min(len(clean_row), 5)]):
            if re.fullmatch(r"\d{1,4}[.)]?", value or ""):
                if index > 0:
                    clean_row = clean_row[index:]
                break
    return clean_row


def _trim_empty_tail(values: list[str]) -> list[str]:
    output = list(values)
    while output and not clean_inline_text(output[-1]):
        output.pop()
    return output


def _is_meaningful_table_data_row(headers: list[str], row: list[str]) -> bool:
    clean_row = [clean_inline_text(value) for value in row]
    if not any(clean_row):
        return False
    normalized_headers = [_normalize_key(header) for header in headers]
    if normalized_headers and normalized_headers[0] in {"stt", "tt"}:
        first = next((value for value in clean_row if value), "")
        if not re.fullmatch(r"\d{1,4}[.)]?", first or "") and _looks_like_continued_header_row(clean_row):
            return False
    return True


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
    if not _is_feature_change_table(headers):
        return _generic_table_row_metadata(headers=headers, values=values, metadata=metadata)
    metadata["table_schema"] = "feature_change"
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
    return {key: value for key, value in metadata.items() if value not in (None, "", [])}



def _is_feature_change_table(headers: list[str]) -> bool:
    normalized = {_normalize_key(header) for header in headers}
    return bool(
        ({"stt", "tt"} & normalized)
        and any("chuc_nang" in header or "man_hinh" in header for header in normalized)
    )


def _generic_table_row_metadata(*, headers: list[str], values: list[str], metadata: dict[str, Any]) -> dict[str, Any]:
    compact = dict(metadata)
    compact["table_schema"] = "generic_table"
    clean_values = [clean_inline_text(value) for value in values]
    normalized_headers = [_normalize_key(header) for header in headers]
    for index, header in enumerate(normalized_headers):
        value = clean_values[index] if index < len(clean_values) else ""
        if not value:
            continue
        field_key = _canonical_table_field_key(header, index=index)
        if field_key and field_key not in compact:
            compact[field_key] = value
    if "row_number" not in compact:
        row_number = _first_header_value(normalized_headers, clean_values, {"stt", "tt"})
        if row_number:
            compact["row_number"] = row_number
    if "feature_name" in compact and not _is_feature_change_table(headers):
        compact.pop("feature_name", None)
    row_entities = unique_strings(
        [
            str(compact.get(key) or "")
            for key in ("person_name", "department", "position", "email", "phone")
            if compact.get(key)
        ]
    )
    if row_entities:
        compact["row_entities"] = row_entities
    return {key: value for key, value in compact.items() if value not in (None, "", [])}


def _canonical_table_field_key(header: str, *, index: int) -> str | None:
    if header in {"stt", "tt"}:
        return "row_number"
    if "ten_nha_thau" in header or header == "nha_thau":
        return "contractor_name"
    if "ma_so_thue" in header or header in {"mst", "tax_code"}:
        return "tax_code"
    if "gia_du_thau_sau_giam" in header or ("gia_du_thau" in header and "giam" in header):
        return "discounted_bid_price"
    if "gia_du_thau" in header:
        return "bid_price"
    if "gia_trung_thau" in header:
        return "winning_price"
    if "thoi_gian" in header and "hop_dong" in header:
        return "contract_execution_time"
    if "thoi_gian" in header and "goi_thau" in header:
        return "package_execution_time"
    if "thoi_gian_thuc" in header:
        return "package_execution_time" if index == 6 else "contract_execution_time"
    if "noi_dung_khac" in header or "ly_do" in header or "khong_trung" in header:
        return "other_content"
    if header in {"ho_ten", "ten_can_bo", "can_bo", "nhan_su", "nguoi_phu_trach", "nguoi_tham_gia"}:
        return "person_name"
    if header in {"chuc_vu", "vi_tri", "position"}:
        return "position"
    if header in {"phong", "phong_ban", "don_vi", "bo_phan", "department", "unit"}:
        return "department"
    if header in {"so_dien_thoai", "dien_thoai", "sdt", "phone"}:
        return "phone"
    if header in {"email", "e_mail", "mail"}:
        return "email"
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
    return bool(re.search(r"giai\s*(?:doan|Ä‘oạn)\s*\d+", str(value or ""), flags=re.IGNORECASE))

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


def build_elements(*, source: dict[str, Any], clean_text: str, tables: list[NormalizedTable], footer_text: str | None, footer_span: dict[str, int] | None = None, summary_text: str | None, metadata: dict[str, Any], appendix_text: str = "", appendix_span: dict[str, int] | None = None) -> list[NormalizedElement]:
    elements: list[NormalizedElement] = []
    if summary_text:
        elements.append(
            NormalizedElement(
                "document_summary",
                summary_text,
                {
                    "source_summary": True,
                    "chunk_type": "document_summary",
                    "summary": summary_text,
                    "source_span": _source_span_for_text(clean_text, summary_text, chunk_type="document_summary"),
                    "indexable": True,
                },
            )
        )
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
        elements.append(
            NormalizedElement(
                "document_header",
                header_text,
                {
                    "chunk_type": "document_header",
                    "source_span": _source_span_for_text(clean_text, header_text, chunk_type="document_header"),
                    "indexable": True,
                },
            )
        )
    body_without_tables = _body_without_tables_or_appendix(clean_text)
    if body_without_tables.strip():
        elements.append(
            NormalizedElement(
                "document_body",
                body_without_tables.strip(),
                {
                    "chunk_type": "document_body",
                    "source_span": _source_span_for_text(clean_text, body_without_tables.strip(), chunk_type="document_body"),
                    "indexable": True,
                },
            )
        )
    # Prose phụ lục (tiêu đề Phụ lục, "Mục tiêu", heading từng lớp dữ liệu, mô tả mối
    # quan hệ...) trước đây bị ``split_footer_signature`` cắt bỏ. Đưa vào dưới dạng
    # ``document_body`` để heading-aware chunker chia theo mục và đính ngữ cảnh văn bản.
    if appendix_text and appendix_text.strip():
        elements.append(
            NormalizedElement(
                "document_body",
                appendix_text.strip(),
                {
                    "chunk_type": "document_body",
                    "artifact_type": "appendix",
                    "section_title": "Phụ lục",
                    "source_span": appendix_span
                    or _source_span_for_text(clean_text, appendix_text.strip(), chunk_type="document_body"),
                    "indexable": True,
                },
            )
        )
    for table in tables:
        elements.append(
            NormalizedElement(
                "table_parent",
                table_parent_text(table),
                {
                    **table.metadata,
                    "chunk_type": "table_parent",
                    "table_id": table.metadata.get("table_id"),
                    "logical_table_id": table.metadata.get("logical_table_id"),
                    "table_index": table.table_index,
                    "table_title": table.metadata.get("table_name"),
                    "table_headers": list(table.headers),
                    "columns": list(table.metadata.get("columns") or _canonical_table_columns(table.headers)),
                    "indexable": True,
                },
            )
        )
        is_feature_table = _is_feature_change_table(table.headers)
        groups: dict[tuple[str | None, str | None], list[NormalizedTableRow]] = {}
        data_rows: list[NormalizedTableRow] = []
        for row in table.rows:
            if row.metadata.get("is_table_marker"):
                continue
            data_rows.append(row)
            elements.append(
                NormalizedElement(
                    "table_row",
                    table_row_text(source=source, table=table, row=row),
                    {
                        **row.metadata,
                        **table.metadata,
                        "chunk_type": "table_row",
                        "table_id": table.metadata.get("table_id"),
                        "logical_table_id": table.metadata.get("logical_table_id"),
                        "table_index": table.table_index,
                        "table_title": table.metadata.get("table_name"),
                        "table_headers": list(table.headers),
                        "row_index": row.row_index,
                        "row_key": row.metadata.get("row_key"),
                        "row_cells": row.metadata.get("row_cells"),
                        "columns": list(table.metadata.get("columns") or _canonical_table_columns(table.headers)),
                        "source_span": row.metadata.get("source_span") or table.metadata.get("source_span"),
                        "is_table_row": True,
                        "indexable": True,
                    },
                )
            )
            if is_feature_table:
                groups.setdefault((row.metadata.get("platform"), row.metadata.get("phase")), []).append(row)
        for (platform, phase), group_rows in groups.items():
            if len(group_rows) < 2:
                continue
            group_text = table_group_text(table=table, platform=platform, phase=phase, rows=group_rows)
            elements.append(
                NormalizedElement(
                    "table_group",
                    group_text,
                    {
                        "chunk_type": "table_group",
                        **table.metadata,
                        "table_id": table.metadata.get("table_id"),
                        "logical_table_id": table.metadata.get("logical_table_id"),
                        "table_index": table.table_index,
                        "table_title": table.metadata.get("table_name"),
                        "table_headers": list(table.headers),
                        "platform": platform,
                        "phase": phase,
                        "group_name": " - ".join(str(value) for value in (platform, phase) if value),
                        "row_count": len(group_rows),
                        "columns": list(table.metadata.get("columns") or _canonical_table_columns(table.headers)),
                        "source_span": table.metadata.get("source_span"),
                        "indexable": True,
                    },
                )
            )
        if not groups:
            for group_index, group_rows in enumerate(_table_row_windows(data_rows, size=10), start=1):
                if len(group_rows) < 2:
                    continue
                row_start = int(group_rows[0].row_index)
                row_end = int(group_rows[-1].row_index)
                elements.append(
                    NormalizedElement(
                        "table_group",
                        table_row_window_group_text(table=table, group_index=group_index, rows=group_rows),
                        {
                            "chunk_type": "table_group",
                            **table.metadata,
                            "table_id": table.metadata.get("table_id"),
                            "logical_table_id": table.metadata.get("logical_table_id"),
                            "table_index": table.table_index,
                            "table_title": table.metadata.get("table_name"),
                            "table_headers": list(table.headers),
                            "columns": list(table.metadata.get("columns") or _canonical_table_columns(table.headers)),
                            "group_name": f"Rows {row_start}-{row_end}",
                            "row_start": row_start,
                            "row_end": row_end,
                            "row_count": len(group_rows),
                            "source_span": table.metadata.get("source_span"),
                            "indexable": True,
                        },
                    )
                )
        for column_index, header in enumerate(table.headers):
            column_values = _table_column_values(table=table, column_index=column_index, rows=data_rows)
            if not column_values:
                continue
            elements.append(
                NormalizedElement(
                    "table_column",
                    table_column_text(table=table, column_index=column_index, rows=data_rows),
                    {
                        "chunk_type": "table_column",
                        **table.metadata,
                        "table_id": table.metadata.get("table_id"),
                        "logical_table_id": table.metadata.get("logical_table_id"),
                        "table_index": table.table_index,
                        "table_title": table.metadata.get("table_name"),
                        "table_headers": list(table.headers),
                        "columns": list(table.metadata.get("columns") or _canonical_table_columns(table.headers)),
                        "column_name": clean_inline_text(header),
                        "table_column": clean_inline_text(header),
                        "column_index": column_index,
                        "column_values": column_values[:50],
                        "row_context_headers": _table_column_context_headers(table.headers, column_index),
                        "row_start": int(data_rows[0].row_index) if data_rows else None,
                        "row_end": int(data_rows[-1].row_index) if data_rows else None,
                        "row_count": len(column_values),
                        "source_span": table.metadata.get("source_span"),
                        "indexable": True,
                    },
                )
            )
    if footer_text:
        elements.append(NormalizedElement("footer_signature", footer_text, {"chunk_type": "footer_signature", "is_footer_or_signature": True, "indexable": False, "embedding_enabled": False, "source_span": footer_span}))
    return elements


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
    appendix_match = APPENDIX_MARKER_PATTERN.search(body)
    if appendix_match:
        body = body[: appendix_match.start()]
    return normalize_lines(body)

def table_parent_text(table: NormalizedTable) -> str:
    is_feature_table = _is_feature_change_table(table.headers)
    groups = unique_strings(
        [
            " - ".join(str(value) for value in (row.metadata.get("platform"), row.metadata.get("phase")) if value)
            for row in table.rows
            if is_feature_table and not row.metadata.get("is_table_marker")
        ]
    )
    columns = _canonical_table_columns(table.headers)
    lines = [
        f"Bảng: {table.metadata.get('table_name') or 'Bảng dữ liệu DOffice'}",
        f"Số dòng: {sum(1 for row in table.rows if not row.metadata.get('is_table_marker'))}",
    ]
    if columns:
        lines.append("Các cột chuẩn hóa: " + ", ".join(columns))
    if groups:
        lines.append("Nhóm chính: " + "; ".join(groups))
    return "\n".join(lines)

def table_group_text(*, table: NormalizedTable, platform: str | None, phase: str | None, rows: list[NormalizedTableRow]) -> str:
    features = unique_strings([str(row.metadata.get("feature_name") or "") for row in rows if not row.metadata.get("is_table_marker")])
    changes = unique_strings([str(row.metadata.get("change_content") or "") for row in rows if row.metadata.get("change_content") and not isinstance(row.metadata.get("change_content"), list)])
    lines = [
        "Nhóm: " + " - ".join(str(value) for value in (platform, phase) if value),
        f"Bảng: {table.metadata.get('table_name') or 'Bảng dữ liệu DOffice'}",
    ]
    if changes:
        lines.append("Nội dung hiệu chỉnh: " + "; ".join(changes[:3]))
    if features:
        lines.append("Các chức năng: " + "; ".join(features[:20]))
    return "\n".join(line for line in lines if line.strip())


def _table_row_windows(rows: list[NormalizedTableRow], *, size: int) -> list[list[NormalizedTableRow]]:
    safe_size = max(1, size)
    return [rows[index : index + safe_size] for index in range(0, len(rows), safe_size)]


def table_row_window_group_text(*, table: NormalizedTable, group_index: int, rows: list[NormalizedTableRow]) -> str:
    row_start = rows[0].row_index if rows else 0
    row_end = rows[-1].row_index if rows else 0
    lines = [
        f"Bảng: {table.metadata.get('table_name') or 'Bảng dữ liệu DOffice'}",
        f"Nhóm dòng {group_index}: Rows {row_start}-{row_end}",
    ]
    if table.headers:
        lines.append("Header: " + " | ".join(clean_inline_text(header) for header in table.headers if clean_inline_text(header)))
    lines.append("Các dòng liên quan:")
    for row in rows:
        details = " | ".join(
            f"{table.headers[index]}: {value}"
            for index, value in enumerate(row.values)
            if value and index < len(table.headers)
        )
        if details:
            lines.append(f"- Dòng {row.row_index}: {details}")
    return "\n".join(line for line in lines if line.strip())


def _table_column_context_indices(headers: list[str], column_index: int) -> list[int]:
    preferred = {"stt", "tt", "ho_ten", "ten", "nhan_su", "nguoi_phu_trach", "noi_dung", "du_lieu", "chuc_nang", "man_hinh"}
    indices: list[int] = []
    for index, header in enumerate(headers):
        if index == column_index:
            continue
        normalized = _normalize_key(header)
        if normalized in preferred or any(term in normalized for term in ("ten", "noi_dung", "du_lieu", "chuc_nang", "man_hinh")):
            indices.append(index)
        if len(indices) >= 2:
            break
    if not indices and headers and column_index != 0:
        indices.append(0)
    return indices


def _table_column_context_headers(headers: list[str], column_index: int) -> list[str]:
    return [headers[index] for index in _table_column_context_indices(headers, column_index) if index < len(headers)]


def _table_column_values(*, table: NormalizedTable, column_index: int, rows: list[NormalizedTableRow]) -> list[str]:
    values: list[str] = []
    for row in rows:
        if column_index >= len(row.values):
            continue
        value = clean_inline_text(row.values[column_index])
        if value:
            values.append(value)
    return unique_strings(values)


def table_column_text(*, table: NormalizedTable, column_index: int, rows: list[NormalizedTableRow]) -> str:
    column_name = clean_inline_text(table.headers[column_index]) if column_index < len(table.headers) else f"Cột {column_index + 1}"
    context_indices = _table_column_context_indices(table.headers, column_index)
    context_headers = [table.headers[index] for index in context_indices if index < len(table.headers)]
    lines = [
        f"Bảng: {table.metadata.get('table_name') or 'Bảng dữ liệu DOffice'}",
        f"Cột bảng: {column_name}",
        "Cột dùng làm ngữ cảnh hàng: " + ", ".join(context_headers) if context_headers else "",
        "| Dòng | Ngữ cảnh hàng | Nội dung cột |",
        "| --- | --- | --- |",
    ]
    for row in rows:
        if column_index >= len(row.values):
            continue
        value = clean_inline_text(row.values[column_index])
        if not value:
            continue
        context_parts = []
        for context_index in context_indices:
            if context_index >= len(row.values) or context_index >= len(table.headers):
                continue
            context_value = clean_inline_text(row.values[context_index])
            if context_value:
                context_parts.append(f"{table.headers[context_index]}: {context_value}")
        context = "; ".join(context_parts) if context_parts else f"Row {row.row_index}"
        lines.append(f"| {row.row_index} | {context} | {value} |")
    return "\n".join(line for line in lines if line.strip())


def _canonical_table_columns(headers: list[str]) -> list[str]:
    normalized = {_normalize_key(header) for header in headers}
    if {"stt", "tt"} & normalized and any("chuc_nang" in header or "man_hinh" in header for header in normalized):
        return ["STT", "nền tảng", "chức năng/màn hình", "nội dung hiệu chỉnh/bổ sung", "giai đoạn"]
    if any("ten_nha_thau" in header or header == "nha_thau" for header in normalized):
        labels = []
        normalized_list = [_normalize_key(header) for header in headers]
        for index, header in enumerate(normalized_list):
            field_key = _canonical_table_field_key(header, index=index)
            labels.append(
                {
                    "row_number": "STT",
                    "contractor_name": "Tên nhà thầu",
                    "tax_code": "Mã số thuế",
                    "bid_price": "Giá dự thầu",
                    "discounted_bid_price": "Giá dự thầu sau giảm giá",
                    "winning_price": "Giá trúng thầu",
                    "package_execution_time": "Thời gian thực hiện gói thầu",
                    "contract_execution_time": "Thời gian thực hiện hợp đồng",
                    "other_content": "Nội dung khác",
                }.get(field_key)
                or clean_inline_text(headers[index])
            )
        return [label for label in labels if label]
    return [clean_inline_text(header) for header in headers if clean_inline_text(header)]


def table_row_text(*, source: dict[str, Any], table: NormalizedTable, row: NormalizedTableRow) -> str:
    metadata = row.metadata
    lines = [
        f"Văn bản: {source.get('ky_hieu') or source.get('id_vb') or ''} - {source.get('trich_yeu') or ''}".strip(" -"),
        f"Phụ lục: {metadata.get('section_title') or table.metadata.get('section_title') or table.metadata.get('table_name') or ''}".strip(" :"),
        f"Bảng: {table.metadata.get('table_name') or 'Bảng dữ liệu DOffice'}",
    ]
    if metadata.get("table_schema") == "feature_change" or _is_feature_change_table(table.headers):
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
    else:
        for label, key in (
            ("STT", "row_number"),
            ("Họ tên", "person_name"),
            ("Chức vụ", "position"),
            ("Phòng/Đơn vị", "department"),
            ("Điện thoại", "phone"),
            ("Email", "email"),
            ("Nhà thầu", "contractor_name"),
            ("Mã số thuế", "tax_code"),
            ("Giá dự thầu", "bid_price"),
            ("Giá dự thầu sau giảm giá", "discounted_bid_price"),
            ("Giá trúng thầu", "winning_price"),
            ("Thời gian thực hiện gói thầu", "package_execution_time"),
            ("Thời gian thực hiện hợp đồng", "contract_execution_time"),
            ("Nội dung khác", "other_content"),
        ):
            value = metadata.get(key)
            if value:
                lines.append(f"{label}: {value}")
    if table.headers and row.values:
        details = " | ".join(
            f"{table.headers[index]}: {value}"
            for index, value in enumerate(row.values)
            if value and index < len(table.headers)
        )
        if details:
            lines.append("Chi tiết: " + details)
    return "\n".join(line for line in lines if line.strip())


def replace_tables(raw_text: str, tables: list[NormalizedTable], *, replacement: str) -> str:
    iterator = iter(tables)

    def _replace(_match: re.Match[str]) -> str:
        table = next(iterator, None)
        if table is None:
            return "\n"
        if replacement == "markdown":
            content = table.markdown
        elif replacement == "text":
            content = table.text
        else:
            content = f"[[TABLE_{table.table_index + 1}]]"
        return "\n" + content + "\n"

    return TABLE_PATTERN.sub(_replace, raw_text or "")


def html_to_plain_text(value: str) -> str:
    text = html.unescape(str(value or "")).replace("\\n", "\n").replace("\xa0", " ")
    parser = TextHTMLParser()
    parser.feed(text)
    parser.close()
    return normalize_lines(apply_spacing_fixes(strip_markdown_noise(parser.get_text())))


def clean_light_text(value: str) -> str:
    return normalize_lines(apply_spacing_fixes(strip_markdown_noise(html.unescape(str(value or "")).replace("\\n", "\n"))))


def compact_source_summary(value: str, *, max_chars: int = 900) -> str | None:
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
        body = text[: appendix_match.start()] if appendix_match else text
        return body.strip(), None
    body_end = min(position for position in (match.start(), appendix_match.start() if appendix_match else None) if position is not None)
    footer_end = appendix_match.start() if appendix_match and appendix_match.start() > match.start() else len(text)
    body = text[:body_end].strip()
    footer = text[match.start() : footer_end].strip() or None
    return body, footer


def extract_appendix_text(plain_text: str) -> tuple[str, dict[str, int] | None]:
    """Trích phần PROSE của phụ lục (sau marker "PHỤ LỤC") để không mất ngữ cảnh.

    ``split_footer_signature`` cắt body tại marker phụ lục nên trước đây toàn bộ tiêu
    đề/đoạn mô tả của phụ lục (vd "Phụ lục 02 ... 1. Mục tiêu ...", "(1) F08_CotDien_HT
    – Lớp cột điện", "Mối quan hệ ...") bị bỏ khỏi mọi chunk. Bảng trong phụ lục được
    chunk riêng từ :func:`parse_html_tables`; ở đây chỉ giữ phần văn xuôi: gỡ placeholder
    bảng ``[[TABLE_n]]`` và cắt khối chữ ký nếu bị chèn lẫn (thứ tự body→phụ lục→footer
    không đồng nhất giữa các văn bản OCR).

    Trả về ``(text, source_span)`` hoặc ``("", None)`` nếu không có phụ lục.
    """

    text = plain_text or ""
    match = APPENDIX_MARKER_PATTERN.search(text)
    if not match:
        return "", None
    region = re.sub(r"\[\[TABLE_\d+]]", "\n", text[match.start():])
    footer_match = FOOTER_MARKER_PATTERN.search(region) or ASCII_FOOTER_MARKER_PATTERN.search(region)
    if footer_match:
        region = region[: footer_match.start()]
    cleaned = normalize_lines(apply_spacing_fixes(strip_markdown_noise(region)))
    if not cleaned.strip():
        return "", None
    return cleaned, {"start": match.start(), "end": len(text)}


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
        return "\n".join(" | ".join(row) for row in rows)
    canonical_headers = _canonical_table_columns(headers)
    if canonical_headers != [clean_inline_text(header) for header in headers if clean_inline_text(header)] and len(canonical_headers) == 5:
        output = ["| " + " | ".join(canonical_headers) + " |", "| " + " | ".join("---" for _ in canonical_headers) + " |"]
        for row in rows:
            metadata = _compact_table_row_metadata(headers=headers, values=row, metadata={})
            if metadata.get("is_table_marker"):
                continue
            output.append(
                "| "
                + " | ".join(
                    str(metadata.get(key) or "")
                    for key in ("row_number", "platform", "feature_name", "change_content", "phase")
                )
                + " |"
            )
        return "\n".join(output)
    output = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    output.extend("| " + " | ".join(row[: len(headers)] + [""] * max(0, len(headers) - len(row))) + " |" for row in rows)
    return "\n".join(output)


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
        # Fallback: ngày dạng số có tiền tố "ngày" (vd "Ngày 11/8/2025, ...").
        # Yêu cầu tiền tố "ngày" để tránh khớp nhầm các số ngày/tháng bất kỳ.
        match = re.search(
            r"ng\S*y\s+(\d{1,2})/(\d{1,2})/(\d{4})",
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
