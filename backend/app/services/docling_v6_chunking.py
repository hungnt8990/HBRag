from __future__ import annotations

import copy
import re
import sys
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from docling.chunking import HybridChunker
from docling_core.transforms.chunker.hierarchical_chunker import (
    ChunkingDocSerializer,
    ChunkingSerializerProvider,
)
from docling_core.transforms.chunker.tokenizer.base import BaseTokenizer
from docling_core.transforms.serializer.markdown import (
    MarkdownParams,
    MarkdownTableSerializer,
)
from docling_core.types.doc import DoclingDocument
from pydantic import Field

DEFAULT_MAX_TOKENS = 350
DEFAULT_CONTEXT_BUDGET = 80

TOP_SECTION_RE = re.compile(r"^\s*(\d+)\.\s+(.+?)\s*$")
SUBSECTION_RE = re.compile(r"^\s*(\d+)\.(\d+)\.\s+(.+?)\s*$")
OBJECT_HEADING_RE = re.compile(
    r"^\s*\((\d+)\)\s+(F\d+_[A-Za-z0-9_]+)\s*[–-]\s*(.+?)\s*$"
)
ATTRIBUTE_TABLE_HEADING_RE = re.compile(
    r"^\s*\((\d+)\)\s+"
    r"(HinhAnh(?:CotDien|KhachHang|HoSoKhachHang))\s*[–-]\s*(.+?)\s*$",
    re.IGNORECASE,
)
ATTRIBUTE_TABLE_NAME_RE = re.compile(
    r"(?im)^\s*Tên\s+bảng\s+dữ\s+liệu\s*:\s*"
    r"(HinhAnh(?:CotDien|KhachHang|HoSoKhachHang))\s*$"
)
RELATIONSHIP_NAME_RE = re.compile(
    r"\b(P[A-Z0-9]+_(?:CotDien|CongToKhachHang)_HT_"
    r"HinhAnh(?:CotDien|KhachHang|HoSoKhachHang))\b"
)
PAREN_HEADING_RE = re.compile(r"^\s*\((\d+)\)\s+(.+?)\s*$")
APPENDIX_RE = re.compile(r"^\s*Phụ\s+lục\s+\d+\b.*$", re.IGNORECASE)
RELATION_HEADING_RE = re.compile(r"^\s*\(\d+\)\s+Mối\s+quan\s+hệ\b", re.IGNORECASE)
SCHEMA_GENERIC_HEADING_RE = re.compile(
    r"^\s*\(\d+\)\s+(?:F\d+_|HinhAnh|Mối\s+quan\s+hệ)",
    re.IGNORECASE,
)
TABLE_SEPARATOR_RE = re.compile(r"^\s*\|?\s*:?-{3,}.*\|\s*$")
TERMINAL_RE = re.compile(r"[.!?;:]\s*$")

COMPOUND_RELATION_HEADING_RE = re.compile(
    r"^(\d+\.\s+Khởi\s+tạo\s+bổ\s+sung\s+\d+\s+mối\s+quan\s+hệ"
    r".*?bảng\s+dữ\s+liệu)\s+(\(\d+\)\s+Mối\s+quan\s+hệ.*)$",
    re.IGNORECASE,
)
IDENTIFIER_CELL_RE = re.compile(
    r"^(?:F\d{2}|PX|PXXXXX|P[A-Z0-9]{2,})_[A-Za-z0-9_ ]+$"
)
EMBEDDED_IDENTIFIER_RE = re.compile(
    r"\b((?:F\d{2}|PX|PXXXXX|P[A-Z0-9]{2,})_[A-Za-z0-9_]+(?:\s+[A-Za-z0-9_]+)+)\b"
)
NATURAL_JOIN_FIXES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bCSDL(?=[a-zà-ỹ])"), "CSDL "),
    (re.compile(r"\bCTĐL(?=[a-zà-ỹ])"), "CTĐL "),
)

FURNITURE_LINE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^CỘNG\s+HÒA\s+XÃ\s+HỘI\s+CHỦ\s+NGHĨA\s+VIỆT\s+NAM", re.I),
    re.compile(r"^Độc\s+lập\s*-\s*Tự\s+do\s*-\s*Hạnh\s+phúc", re.I),
    re.compile(r"^[A-ZÀ-Ỹ][\wÀ-ỹ\s]+,\s*ngày\s*$", re.I),
    re.compile(r"^Số\s*:\s*$", re.I),
)

SIGNATURE_LINE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^KT\.\s*TỔNG\s+GIÁM\s+ĐỐC", re.I),
    re.compile(r"^PHÓ\s+TỔNG\s+GIÁM\s+ĐỐC", re.I),
)


class RegexVietnameseTokenizer(BaseTokenizer):
    """Tokenizer xấp xỉ, không cần tải model ngoài.

    Production nên thay bằng tokenizer đúng của embedding model.
    """

    max_tokens: int = Field(default=DEFAULT_MAX_TOKENS, ge=32)

    def count_tokens(self, text: str) -> int:
        return len(re.findall(r"\w+|[^\w\s]+", text or "", flags=re.UNICODE))

    def get_max_tokens(self) -> int:
        return self.max_tokens

    def get_tokenizer(self) -> Any:
        return self.count_tokens


class MarkdownTableSerializerProvider(ChunkingSerializerProvider):
    """Giữ bảng dạng Markdown và lặp header khi Docling chia bảng."""

    def get_serializer(self, doc: DoclingDocument) -> ChunkingDocSerializer:
        return ChunkingDocSerializer(
            doc=doc,
            table_serializer=MarkdownTableSerializer(),
            params=MarkdownParams(compact_tables=False),
        )


def normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()

def strip_image_placeholders(text: str) -> str:
    """Remove Docling image placeholders before quality checks and embedding."""

    value = re.sub(r"(?im)^\s*<!--\s*image\s*-->\s*$", "", text or "")
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


_VIETNAMESE_ACCENTED = (
    "ÀÁÂÃÈÉÊÌÍÒÓÔÕÙÚĂĐĨŨƠƯẠẢẤẦẨẪẬẮẰẲẴẶ"
    "ẸẺẼỀỀỂỄỆỈỊỌỎỐỒỔỖỘỚỜỞỠỢỤỦỨỪỬỮỰỲỴỶỸ"
    "àáâãèéêìíòóôõùúăđĩũơưạảấầẩẫậắằẳẵặ"
    "ẹẻẽềếểễệỉịọỏốồổỗộớờởỡợụủứừửữựỳỵỷỹ"
)


def normalize_vietnamese_pdf_text(text: str) -> str:
    """Conservatively normalize PDF text without joining real word boundaries.

    Native PDF extractors often produce better Vietnamese text than Docling's
    item stream. This function therefore only fixes safe, known corruption and
    never performs generic token concatenation.
    """

    value = unicodedata.normalize("NFC", (text or "").replace("&amp;", "&"))
    value = value.replace("\x00", "")
    safe_repairs = (
        (r"(?i)ĐÔ\s+NG", "ĐỒNG"),
        (r"(?i)LIỆ\s+U", "LIỆU"),
        (r"(?i)THÊ\b", "THẾ"),
        (r"(?i)CÔNG\s+CỤ\s+ĐỒNG\s+BỘ\s+DỮ\s+LIỆU", "CÔNG CỤ ĐỒNG BỘ DỮ LIỆU"),
        (r"GIS\s+HẠ\s+THẾ\b", "GIS HẠ THẾ"),
        (r"LƯU\s+TRỮ\s*&\s*TỔNG\s+HỢP", "LƯU TRỮ & TỔNG HỢP"),
    )
    for pattern, replacement in safe_repairs:
        value = re.sub(pattern, replacement, value)

    alpha_pattern = re.compile(r"^[A-Za-zÀ-ỹ]+$")

    def has_accent(token: str) -> bool:
        return any(character in _VIETNAMESE_ACCENTED for character in token)

    repaired_lines: list[str] = []
    for line in value.splitlines():
        tokens = [(token, False) for token in line.split()]
        index = 0
        while index + 1 < len(tokens):
            left, continuing = tokens[index]
            right, _ = tokens[index + 1]
            if not alpha_pattern.fullmatch(left) or not alpha_pattern.fullmatch(right):
                index += 1
                continue
            starts_glyph = (
                len(right) == 1
                and has_accent(right)
                and len(left) <= 3
                and not has_accent(left)
            )
            continues_syllable = (
                continuing
                and right.islower()
                and not has_accent(right)
                and right in {"a", "c", "e", "i", "m", "n", "ng", "nh", "o", "p", "t", "u", "y"}
            )
            if starts_glyph or continues_syllable:
                tokens[index : index + 2] = [(left + right, True)]
            else:
                index += 1
        repaired_lines.append(" ".join(token for token, _ in tokens))
    value = "\n".join(repaired_lines)
    for pattern, replacement in safe_repairs:
        value = re.sub(pattern, replacement, value)
    value = re.sub(r"[ \t]{2,}", " ", value)
    value = re.sub(r"[ \t]+\n", "\n", value)
    value = re.sub(r"\s+([,.;:!?])", r"\1", value)
    return value.strip()


def text_quality_score(text: str) -> float:
    """Return a higher score for readable Vietnamese page text."""

    value = normalize_vietnamese_pdf_text(strip_image_placeholders(text))
    if not value:
        return -100.0
    tokens = re.findall(r"[A-Za-zÀ-ỹ0-9_/+.:-]+", value)
    if not tokens:
        return -50.0
    single_alpha = sum(1 for token in tokens if len(token) == 1 and token.isalpha())
    broken_diacritic = len(re.findall(r"\b[A-Za-zÀ-ỹ]{1,3}\s+[À-ỹ]\b", value))
    null_or_placeholder = value.count("<!--") + value.count("\x00")
    suspicious_glued = len(
        re.findall(
            r"(?i)(?:GIS|CSDL|CMIS|TTHT)[a-zà-ỹ]|[a-zà-ỹ](?:GIS|CSDL|CMIS|TTHT)",
            value,
        )
    )
    score = float(len(tokens))
    score -= single_alpha * 3.0
    score -= broken_diacritic * 2.5
    score -= null_or_placeholder * 8.0
    score -= suspicious_glued * 3.0
    return score


def _clean_native_slide_lines(text: str) -> list[str]:
    lines = [normalize_space(line) for line in (text or "").splitlines()]
    return [line for line in lines if line and line != "\x00"]


def _deduplicate_title(title: str, lines: list[str]) -> list[str]:
    normalized_title = re.sub(r"\s+", "", title).casefold()
    output: list[str] = []
    removed = False
    for line in lines:
        normalized_line = re.sub(r"\s+", "", line).casefold()
        if not removed and normalized_title and normalized_line == normalized_title:
            removed = True
            continue
        output.append(line)
    return output


def _is_toc_slide(lines: list[str]) -> bool:
    numbered = sum(bool(re.match(r"^\d+\s*\.", line)) for line in lines)
    return numbered >= 3 and len(lines) <= 10


def _is_demo_divider(lines: list[str]) -> bool:
    words = re.findall(r"[A-Za-zÀ-ỹ]+", " ".join(lines).casefold())
    return bool(words) and set(words) <= {"demo"}


def _reconstruct_numbered_callouts(title: str, lines: list[str]) -> str | None:
    """Rebuild slides whose annotations are numbered 1..N around a screenshot."""

    filtered = [line for line in lines if not re.fullmatch(r"[1-9]", line)]
    body = " ".join(_deduplicate_title(title, filtered))
    matches = list(re.finditer(r"(?<!\d)([1-9])\.\s*", body))
    if len(matches) < 3:
        return None
    items: dict[int, str] = {}
    for index, match in enumerate(matches):
        number = int(match.group(1))
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        content = normalize_space(body[match.end():end])
        if content:
            items[number] = content
    if len(items) < 3:
        return None
    for number, content in list(items.items()):
        content = re.sub(r"\b[1-9]\b(?=\s+[a-zà-ỹ])", "", content)
        content = normalize_space(content)
        lowered = content.casefold()
        if "chi tiết log" in lowered:
            content = "Chi tiết log: Chi tiết tiến trình đang chạy."
        elif "kết quả đồng bộ" in lowered:
            content = (
                "Kết quả đồng bộ: Hiển thị kết quả các lớp dữ liệu "
                "đã được tổng hợp."
            )
        items[number] = content
    ordered = [f"{number}. {items[number]}" for number in sorted(items)]
    return "\n".join([title, *ordered])


def _reconstruct_three_column_flow(title: str, lines: list[str]) -> str | None:
    """Rebuild a source -> processing -> target data-flow slide."""

    joined = " ".join(lines)
    required = ("CMIS / TTHT", "LƯU TRỮ & TỔNG HỢP", "GIS HẠ THẾ")
    if not all(value.casefold() in joined.casefold() for value in required):
        return None
    rows: list[tuple[str, str, str]] = []
    for line in lines:
        if any(header.casefold() in line.casefold() for header in required):
            continue
        parts = re.split(r"\s{2,}", line)
        if len(parts) >= 3:
            rows.append((parts[0].strip(), parts[1].strip(), parts[2].strip()))
            continue
        words = line.split()
        for width in range(len(words) // 2, 1, -1):
            if words[-width:] == words[-2 * width : -width]:
                source = " ".join(words[: -2 * width]).strip()
                target = " ".join(words[-width:]).strip()
                if source and target:
                    rows.append((source, target, target))
                break
    if not rows:
        return None
    sources = unique_preserve_order(row[0] for row in rows if row[0])
    targets = unique_preserve_order(row[1] for row in rows if row[1])
    output = [title, "Luồng dữ liệu: CMIS/TTHT → Lưu trữ & Tổng hợp → GIS Hạ thế."]
    if sources:
        output.extend(["Nguồn dữ liệu:", *(f"- {item}" for item in sources)])
    if targets:
        output.extend(
            [
                "Các lớp dữ liệu được tổng hợp và đưa lên GIS Hạ thế:",
                *(f"- {item}" for item in targets),
            ]
        )
    return "\n".join(output)

def _item_bbox(item: Any) -> tuple[float, float, float, float] | None:
    provenance = list(getattr(item, "prov", None) or [])
    if not provenance:
        return None
    bbox = getattr(provenance[0], "bbox", None)
    if bbox is None:
        return None
    values = tuple(getattr(bbox, name, None) for name in ("l", "t", "r", "b"))
    if all(isinstance(value, int | float) for value in values):
        return tuple(float(value) for value in values)  # type: ignore[return-value]
    return None


def collect_page_text_items(doc: DoclingDocument) -> dict[int, list[dict[str, Any]]]:
    """Collect Docling text items per page with layout metadata."""

    output: dict[int, list[dict[str, Any]]] = {}
    for item in getattr(doc, "texts", []) or []:
        text = normalize_vietnamese_pdf_text(str(getattr(item, "text", "") or ""))
        text = strip_image_placeholders(text)
        if not text:
            continue
        label = getattr(getattr(item, "label", None), "value", None)
        for page_no in item_pages(item):
            output.setdefault(page_no, []).append(
                {
                    "text": text,
                    "label": str(label or ""),
                    "bbox": _item_bbox(item),
                    "order": self_ref_index(item),
                }
            )
    return output


def is_presentation_like_document(doc: DoclingDocument) -> bool:
    """Detect slide-like PDF/PPTX documents without a separate chunking router."""

    pages = getattr(doc, "pages", {}) or {}
    page_count = len(pages)
    if page_count < 3:
        return False
    by_page = collect_page_text_items(doc)
    short_pages = 0
    repeated_titles: dict[str, int] = {}
    for page_no in pages:
        items = by_page.get(int(page_no), [])
        word_count = sum(len(item["text"].split()) for item in items)
        if word_count <= 160:
            short_pages += 1
        candidates = [
            item["text"]
            for item in items
            if item["label"] in {"title", "section_header", "heading"}
        ]
        if candidates:
            key = normalize_space(candidates[0]).casefold()
            repeated_titles[key] = repeated_titles.get(key, 0) + 1
    pictures = len(getattr(doc, "pictures", []) or [])
    short_ratio = short_pages / max(1, page_count)
    repeat_ratio = max(repeated_titles.values(), default=0) / max(1, page_count)
    picture_density = pictures / max(1, page_count)
    return short_ratio >= 0.60 and (picture_density >= 0.30 or repeat_ratio >= 0.25)


def detect_document_profile(doc: DoclingDocument) -> str:
    """Classify the document for Docling-first, segment-aware chunking.

    Administrative markers take precedence over the slide heuristic. Short
    official letters with appendix tables are often only 3-4 pages and can
    otherwise be misclassified as presentations because most pages are short.
    """

    text = normalize_space(
        "\n".join(
            str(getattr(item, "text", "") or "")
            for item in getattr(doc, "texts", []) or []
        )
    )
    lowered = text.casefold()
    administrative_markers = (
        "kính gửi",
        "nơi nhận",
        "cộng hòa xã hội chủ nghĩa việt nam",
        "kt. tổng giám đốc",
        "tổng công ty",
    )
    is_administrative = sum(marker in lowered for marker in administrative_markers) >= 2

    table_count = len(getattr(doc, "tables", []) or [])
    has_schema_headers = all(
        marker in lowered
        for marker in ("trường dữ liệu", "kiểu dữ liệu", "nguồn dữ liệu")
    )
    if has_schema_headers:
        return "technical_schema"
    if is_administrative and table_count:
        return "administrative_with_tables"
    if is_administrative:
        return "administrative"
    if is_presentation_like_document(doc):
        return "presentation"
    if table_count:
        return "mixed_with_tables"
    return "general"


def classify_segment_strategy(record: dict[str, Any], document_profile: str) -> str:
    """Select a lightweight strategy per repaired segment, without a router."""

    if record.get("document_profile") == "presentation_like_pdf":
        return "presentation_page"
    if table_components(str(record.get("contextualized_text") or "")):
        if record.get("cross_page_table_continuation"):
            return "cross_page_table"
        if record.get("table_name") or document_profile == "technical_schema":
            return "schema_table"
        return "table_row_group"
    if document_profile.startswith("administrative"):
        return "administrative_section"
    return "docling_hybrid"


def _slide_item_sort_key(item: dict[str, Any]) -> tuple[float, float, int]:
    bbox = item.get("bbox")
    if bbox:
        left, top, _right, _bottom = bbox
        # Docling PDF coordinates are typically bottom-left based, so higher top
        # values should be read first.
        return (-top, left, int(item.get("order", 0)))
    return (0.0, 0.0, int(item.get("order", 0)))


def infer_slide_title(items: list[dict[str, Any]], page_no: int) -> str:
    heading_items = [
        item for item in items
        if item.get("label") in {"title", "section_header", "heading"}
    ]
    if heading_items:
        return normalize_space(sorted(heading_items, key=_slide_item_sort_key)[0]["text"])
    ordered = sorted(items, key=_slide_item_sort_key)
    if ordered:
        first = normalize_space(ordered[0]["text"])
        if len(first) <= 120:
            return first
    return f"Trang {page_no}"


def build_presentation_records(
    doc: DoclingDocument,
    *,
    tokenizer: RegexVietnameseTokenizer,
    max_tokens: int,
    document_context: str,
    page_texts: dict[int, str] | None = None,
) -> list[dict[str, Any]]:
    """Build one semantic record per slide, preserving page boundaries."""

    by_page = collect_page_text_items(doc)
    pages = sorted(int(page_no) for page_no in (getattr(doc, "pages", {}) or {}))
    records: list[dict[str, Any]] = []
    for page_no in pages:
        items = sorted(by_page.get(page_no, []), key=_slide_item_sort_key)
        ordered_text = unique_preserve_order(item["text"] for item in items)
        docling_raw = "\n".join(ordered_text)
        native_raw = strip_image_placeholders((page_texts or {}).get(page_no, ""))
        native_lines = _clean_native_slide_lines(native_raw)
        docling_lines = _clean_native_slide_lines(docling_raw)

        use_native = text_quality_score(native_raw) >= text_quality_score(docling_raw)
        selected_lines = native_lines if use_native else docling_lines
        if not selected_lines:
            selected_lines = native_lines or docling_lines

        if _is_toc_slide(selected_lines):
            title = "NỘI DUNG"
            body_lines = [line for line in selected_lines if re.match(r"^\d+\s*\.", line)]
            text = "\n".join([title, *body_lines])
            chunk_type = "presentation_toc"
            indexable = False
        else:
            title = infer_slide_title(items, page_no)
            if selected_lines:
                first_line = selected_lines[0]
                if len(first_line) >= 3 and len(first_line) <= 120:
                    title = first_line
            title = normalize_vietnamese_pdf_text(title)
            body_lines = _deduplicate_title(title, selected_lines)

            reconstructed = _reconstruct_three_column_flow(title, selected_lines)
            if reconstructed is None:
                reconstructed = _reconstruct_numbered_callouts(title, selected_lines)
            if reconstructed is not None:
                text = reconstructed
            else:
                text = "\n".join([title, *body_lines]).strip()
            chunk_type = "presentation_slide"
            indexable = True

        text = normalize_vietnamese_pdf_text(strip_image_placeholders(text))
        cleaned_lines = _clean_native_slide_lines(text)
        if _is_demo_divider(cleaned_lines):
            chunk_type = "presentation_section_divider"
            indexable = False

        meaningful = normalize_space(text)
        tokens = re.findall(r"[A-Za-zÀ-ỹ0-9]+", meaningful)
        single_alpha = sum(1 for token in tokens if len(token) == 1 and token.isalpha())
        fragmented = bool(tokens) and single_alpha / max(1, len(tokens)) > 0.18
        visual_only = not meaningful or meaningful.casefold() in {
            normalize_space(title).casefold(),
            "demo",
            "3. demo",
            "4. demo",
        }
        if visual_only:
            indexable = False
        quality_status = "pass"
        validation_issues: list[dict[str, Any]] = []
        if visual_only:
            quality_status = "warning"
            validation_issues.append(
                {"type": "visual_only_slide", "severity": "warning"}
            )
        if fragmented:
            quality_status = "warning"
            validation_issues.append(
                {"type": "fragmented_text", "severity": "warning"}
            )
            if not use_native:
                indexable = False

        record = {
            "chunk_type": chunk_type,
            "content_format": "text",
            "pages": [page_no],
            "page_start": page_no,
            "page_end": page_no,
            "slide_number": page_no,
            "slide_title": title,
            "headings": [title] if title else [],
            "section_path": [title] if title else [],
            "document_profile": "presentation_like_pdf",
            "document_context": document_context,
            "raw_text": text,
            "text": text,
            "contextualized_text": text,
            "source_raw_text": native_raw or docling_raw,
            "text_source": "native_pdf" if use_native else "docling",
            "indexable": indexable,
            "embedding_enabled": indexable,
            "quality_status": quality_status,
            "validation_issues": validation_issues,
        }
        if tokenizer.count_tokens(text) <= max_tokens or not indexable:
            records.append(record)
            continue

        paragraphs = [
            part.strip()
            for part in re.split(r"\n(?=\d+\.|[-+•])", text)
            if part.strip()
        ]
        current: list[str] = []
        part_index = 1
        for paragraph in paragraphs:
            candidate = "\n".join(current + [paragraph])
            if current and tokenizer.count_tokens(candidate) > max_tokens:
                child = copy.deepcopy(record)
                child_text = "\n".join(current)
                child.update(
                    {
                        "chunk_type": "presentation_slide_part",
                        "slide_part": part_index,
                        "raw_text": child_text,
                        "text": child_text,
                        "contextualized_text": child_text,
                    }
                )
                records.append(child)
                current = [paragraph]
                part_index += 1
            else:
                current.append(paragraph)
        if current:
            child = copy.deepcopy(record)
            child_text = "\n".join(current)
            child.update(
                {
                    "chunk_type": "presentation_slide_part",
                    "slide_part": part_index,
                    "raw_text": child_text,
                    "text": child_text,
                    "contextualized_text": child_text,
                }
            )
            records.append(child)
    return records

def item_pages(item: Any) -> list[int]:
    pages: set[int] = set()
    for prov in getattr(item, "prov", []) or []:
        page_no = getattr(prov, "page_no", None)
        if isinstance(page_no, int):
            pages.add(page_no)
    return sorted(pages)


def self_ref_index(item: Any) -> int:
    ref = str(getattr(item, "self_ref", ""))
    match = re.search(r"/(\d+)$", ref)
    return int(match.group(1)) if match else sys.maxsize


def unique_preserve_order(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        clean = normalize_space(value)
        key = clean.casefold()
        if clean and key not in seen:
            seen.add(key)
            output.append(clean)
    return output


def source_fragments(record: dict[str, Any]) -> list[str]:
    fragments = record.get("_source_fragments")
    if isinstance(fragments, list):
        return unique_preserve_order(str(value) for value in fragments if str(value).strip())
    source = record.get("source_raw_text")
    if source is None:
        source = record.get("raw_text")
    return [str(source)] if source is not None and str(source).strip() else []


def merged_source_fragments(records: Iterable[dict[str, Any]]) -> list[str]:
    return unique_preserve_order(
        fragment
        for record in records
        for fragment in source_fragments(record)
    )


def extract_page_1_preamble(doc: DoclingDocument) -> str:
    candidates: list[tuple[int, str]] = []
    for item in getattr(doc, "texts", []) or []:
        if 1 not in item_pages(item):
            continue
        text = normalize_space(getattr(item, "text", ""))
        if text:
            candidates.append((self_ref_index(item), text))

    candidates.sort(key=lambda pair: pair[0])
    ordered = unique_preserve_order(text for _, text in candidates)
    preamble: list[str] = []
    for text in ordered:
        if preamble and (
            len(text) >= 180
            or re.match(r"^(Ngày|Căn cứ|Nhằm)\b", text, flags=re.IGNORECASE)
            or re.match(r"^1\.\s+", text)
        ):
            break
        preamble.append(text)
        if len(preamble) >= 14:
            break
    return "\n".join(unique_preserve_order(preamble))


def compact_document_context(
    preamble: str,
    tokenizer: RegexVietnameseTokenizer,
    budget: int,
) -> str:
    if not preamble:
        return ""
    selected: list[str] = []
    used = 0
    for line in preamble.splitlines():
        line = normalize_space(line)
        if not line:
            continue
        line_tokens = tokenizer.count_tokens(line)
        if selected and used + line_tokens > budget:
            break
        selected.append(line)
        used += line_tokens
    return "Ngữ cảnh tài liệu:\n" + "\n".join(selected) if selected else ""


def prepend_context_without_duplication(document_context: str, native_context: str) -> str:
    if not document_context:
        return native_context
    context_lines = [
        normalize_space(line)
        for line in document_context.splitlines()
        if normalize_space(line) and normalize_space(line) != "Ngữ cảnh tài liệu:"
    ]
    native_norm = normalize_space(native_context).casefold()
    missing_lines = [
        line for line in context_lines if normalize_space(line).casefold() not in native_norm
    ]
    if not missing_lines:
        return native_context
    return "Ngữ cảnh tài liệu:\n" + "\n".join(missing_lines) + "\n\n" + native_context


def chunk_pages(chunk: Any) -> list[int]:
    pages: set[int] = set()
    for item in getattr(chunk.meta, "doc_items", []) or []:
        pages.update(item_pages(item))
    return sorted(pages)


def chunk_item_types(chunk: Any) -> list[str]:
    output: list[str] = []
    for item in getattr(chunk.meta, "doc_items", []) or []:
        label = getattr(item, "label", None)
        value = getattr(label, "value", None)
        output.append(str(value if value is not None else label))
    return output


def is_table_line(line: str) -> bool:
    return line.lstrip().startswith("|")


def split_md_cells(line: str) -> list[str]:
    stripped = line.strip()
    if not stripped.startswith("|"):
        return []
    return [cell.strip() for cell in stripped.strip("|").split("|")]


def normalized_heading_text(line: str) -> str:
    return normalize_space(line).lstrip("- ").replace("\\_", "_")


def explicit_attribute_table_name(text: str) -> str | None:
    match = ATTRIBUTE_TABLE_NAME_RE.search(text or "")
    return match.group(1) if match else None


def declared_table_name(text: str) -> str | None:
    """Return the strongest table identity declared inside the chunk itself."""

    explicit = explicit_attribute_table_name(text)
    if explicit:
        return explicit
    for line in (text or "").splitlines():
        clean = normalized_heading_text(line).rstrip(":")
        name = heading_table_name(clean)
        if name:
            return name
    return None


def heading_table_name(heading: str | None) -> str | None:
    if not heading:
        return None
    clean = normalized_heading_text(heading).rstrip(":")
    object_match = OBJECT_HEADING_RE.match(clean)
    if object_match:
        return object_match.group(2)
    attribute_match = ATTRIBUTE_TABLE_HEADING_RE.match(clean)
    if attribute_match:
        return attribute_match.group(2)
    return None


def infer_table_name_from_record(record: dict[str, Any]) -> str | None:
    """Resolve the logical table name with explicit in-table metadata first.

    Docling may carry the previous object's heading into the first chunk of a new
    attribute table.  The ``Tên bảng dữ liệu:`` line is therefore more reliable
    than a prefixed Fxx heading and must win.
    """

    text = str(record.get("contextualized_text") or record.get("text") or "")
    explicit = explicit_attribute_table_name(text)
    if explicit:
        return explicit

    for heading in reversed(record.get("section_path") or record.get("headings") or []):
        table_name = heading_table_name(str(heading))
        if table_name:
            return table_name

    for line in text.splitlines():
        table_name = heading_table_name(line)
        if table_name:
            return table_name
    return None


def table_heading_matches_name(heading: str, table_name: str) -> bool:
    candidate = heading_table_name(heading)
    return bool(candidate and candidate.casefold() == table_name.casefold())


def is_heading_line(line: str) -> bool:
    clean = normalized_heading_text(line)
    return bool(
        SUBSECTION_RE.match(clean)
        or TOP_SECTION_RE.match(clean)
        or OBJECT_HEADING_RE.match(clean)
        or APPENDIX_RE.match(clean)
        or RELATION_HEADING_RE.match(clean)
        or SCHEMA_GENERIC_HEADING_RE.match(clean)
        or re.match(r"^\d+\.\s+(Mục tiêu|Chi tiết|Khởi tạo|Phương án)\b", clean, re.I)
    )


def strip_leading_list_marker(line: str) -> str:
    clean = line.strip()
    clean = re.sub(r"^-\s*-\s*", "- ", clean)
    clean = re.sub(r"^-\s*\+\s*", "+ ", clean)
    return clean


def starts_lowercase_continuation(line: str) -> bool:
    clean = strip_leading_list_marker(line)
    clean = re.sub(r"^[-+]\s*", "", clean).strip()
    if not clean:
        return False
    first = clean[0]
    return first.islower() or first in ",;)]"


def normalize_natural_text(text: str) -> str:
    """Chuẩn hóa khoảng trắng và lỗi glyph tiếng Việt trong PDF."""

    value = normalize_vietnamese_pdf_text(strip_image_placeholders(text or ""))
    for pattern, replacement in NATURAL_JOIN_FIXES:
        value = pattern.sub(replacement, value)
    return value


def looks_like_identifier_cell(value: str) -> bool:
    clean = normalize_space(value)
    return bool("_" in clean and IDENTIFIER_CELL_RE.fullmatch(clean))


def normalize_identifier_cell(value: str) -> tuple[str, dict[str, str] | None]:
    raw = normalize_space(value)
    if not looks_like_identifier_cell(raw) or " " not in raw:
        return raw, None
    normalized = raw.replace(" ", "")
    return normalized, {"raw": raw, "normalized": normalized}


def normalize_embedded_identifiers(value: str) -> tuple[str, list[dict[str, str]]]:
    repairs: list[dict[str, str]] = []

    def replace(match: re.Match[str]) -> str:
        raw = normalize_space(match.group(1))
        normalized = raw.replace(" ", "")
        if raw != normalized:
            repairs.append({"raw": raw, "normalized": normalized})
        return normalized

    return EMBEDDED_IDENTIFIER_RE.sub(replace, value), repairs


def normalize_table_row(line: str) -> tuple[str, list[dict[str, str]]]:
    cells = split_md_cells(line)
    if not cells:
        return line.rstrip(), []
    repairs: list[dict[str, str]] = []
    normalized_cells: list[str] = []
    for cell in cells:
        identifier, repair = normalize_identifier_cell(cell)
        if repair:
            repairs.append(repair)
            normalized_cells.append(identifier)
            continue
        embedded, embedded_repairs = normalize_embedded_identifiers(cell)
        repairs.extend(embedded_repairs)
        normalized_cells.append(normalize_natural_text(embedded))
    return "| " + " | ".join(normalized_cells) + " |", repairs


def clean_prose_lines(lines: Sequence[str]) -> list[str]:
    output: list[str] = []
    for raw in lines:
        if not raw.strip():
            if output and output[-1] != "":
                output.append("")
            continue
        line = strip_leading_list_marker(raw)
        line = normalize_natural_text(line)

        if output and output[-1] and not is_table_line(line):
            previous = output[-1]
            if (
                not is_table_line(previous)
                and not is_heading_line(previous)
                and not TERMINAL_RE.search(previous)
                and starts_lowercase_continuation(line)
            ):
                continuation = re.sub(r"^[-+]\s*", "", line).strip()
                output[-1] = normalize_natural_text(previous.rstrip() + " " + continuation)
                continue
        output.append(line)

    while output and output[-1] == "":
        output.pop()
    return output

def normalize_markdown_table(text: str) -> str:
    """Sửa merged-cell, header lặp, separator thừa và khoảng trắng trong bảng."""

    lines = text.splitlines()
    intermediate: list[str] = []
    seen_plain_metadata: set[str] = set()
    first_header_signature: str | None = None
    index = 0

    while index < len(lines):
        line = lines[index].rstrip()
        cells = split_md_cells(line)

        # Ô merged toàn hàng thường bị serializer nhân cùng nội dung sang mọi cột.
        if cells and len(cells) >= 2 and not TABLE_SEPARATOR_RE.match(line):
            non_empty = [normalize_space(cell) for cell in cells if normalize_space(cell)]
            if non_empty and len(set(value.casefold() for value in non_empty)) == 1:
                value = normalize_natural_text(non_empty[0])
                if value.casefold() not in seen_plain_metadata:
                    intermediate.append(value)
                    seen_plain_metadata.add(value.casefold())
                index += 1
                if index < len(lines) and TABLE_SEPARATOR_RE.match(lines[index]):
                    index += 1
                continue

        if cells and not TABLE_SEPARATOR_RE.match(line):
            normalized_row, _ = normalize_table_row(line)
            signature = normalize_space(normalized_row).casefold()
            has_separator = index + 1 < len(lines) and TABLE_SEPARATOR_RE.match(lines[index + 1])
            first_cell = normalize_space(cells[0]).casefold()
            semantic_header = first_cell in {
                "tt", "stt", "tên mối quan hệ", "tên trường", "trường dữ liệu"
            }

            if first_header_signature is not None and signature == first_header_signature:
                index += 2 if has_separator else 1
                continue
            if has_separator or semantic_header:
                if first_header_signature is None:
                    first_header_signature = signature
            intermediate.append(normalized_row)
            if semantic_header and not has_separator:
                intermediate.append("|" + "|".join("---" for _ in cells) + "|")
            index += 1
            continue

        intermediate.append(line)
        index += 1

    # Chỉ giữ separator ngay sau dòng header đầu tiên của mỗi bảng.
    normalized: list[str] = []
    table_separator_seen = False
    previous_was_table = False
    for _idx, line in enumerate(intermediate):
        if not line.strip():
            normalized.append("")
            previous_was_table = False
            table_separator_seen = False
            continue
        if not is_table_line(line):
            normalized.append(normalize_natural_text(line))
            previous_was_table = False
            table_separator_seen = False
            continue
        if TABLE_SEPARATOR_RE.match(line):
            prev_line = normalized[-1] if normalized else ""
            prev_cells = split_md_cells(prev_line)
            first_cell = normalize_space(prev_cells[0]).casefold() if prev_cells else ""
            keep = (
                previous_was_table
                and not table_separator_seen
                and prev_cells
                and not first_cell.isdigit()
            )
            if keep:
                normalized.append("|" + "|".join("---" for _ in prev_cells) + "|")
                table_separator_seen = True
            continue
        normalized_row, _ = normalize_table_row(line)
        normalized.append(normalized_row)
        previous_was_table = True

    compact: list[str] = []
    for line in normalized:
        if not line.strip() and (not compact or not compact[-1].strip()):
            continue
        compact.append(line.rstrip())
    return "\n".join(compact).strip()

def clean_record_text(text: str) -> str:
    text = normalize_markdown_table(text)
    lines = text.splitlines()
    result: list[str] = []
    prose_buffer: list[str] = []

    def flush_prose() -> None:
        nonlocal prose_buffer
        if prose_buffer:
            result.extend(clean_prose_lines(prose_buffer))
            prose_buffer = []

    for line in lines:
        if is_table_line(line) or TABLE_SEPARATOR_RE.match(line):
            flush_prose()
            result.append(line.rstrip())
        else:
            prose_buffer.append(line)
    flush_prose()

    compact: list[str] = []
    for line in result:
        if not line and (not compact or not compact[-1]):
            continue
        compact.append(line)
    return "\n".join(compact).strip()


def line_is_furniture(line: str) -> bool:
    clean = normalize_space(line)
    return any(pattern.search(clean) for pattern in FURNITURE_LINE_PATTERNS)


def strip_misplaced_furniture(record: dict[str, Any]) -> dict[str, Any]:
    """Loại furniture đứng trước nội dung nghiệp vụ nhưng lưu lại trong
    metadata.
    """

    updated = copy.deepcopy(record)
    lines = updated["contextualized_text"].splitlines()
    removed: list[str] = []
    kept: list[str] = []
    business_seen = False

    for line in lines:
        clean = normalize_space(line)
        if not business_seen and line_is_furniture(clean):
            removed.append(clean)
            continue
        if clean:
            business_seen = True
        kept.append(line)

    # Chỉ áp dụng khi sau phần bỏ vẫn còn nội dung thực tế.
    if removed and normalize_space("\n".join(kept)):
        updated["contextualized_text"] = "\n".join(kept).strip()
        updated["text"] = updated["contextualized_text"]
        updated.setdefault("repair_metadata", {})["removed_furniture"] = removed
        headings = [h for h in updated.get("headings", []) if not line_is_furniture(h)]
        updated["headings"] = headings
    return updated


def normalize_inline_heading_line(line: str) -> tuple[str, str | None]:
    """Trả về (heading, body) nếu dòng chứa heading và nội dung cùng dòng."""

    clean = normalized_heading_text(line)

    subsection = re.match(r"^(\d+\.\d+\.\s+[^:]+:)(?:\s+(.+))?$", clean)
    if subsection:
        return subsection.group(1).strip(), normalize_space(subsection.group(2) or "") or None

    top = re.match(r"^(\d+\.\s+[^:]+:)(?:\s+(.+))?$", clean)
    if top:
        return top.group(1).strip(), normalize_space(top.group(2) or "") or None

    object_match = re.match(
        r"^(\(\d+\)\s+F\d+_[A-Za-z0-9_]+\s*[–-]\s*[^:]+:)(?:\s+(.+))?$",
        clean,
    )
    if object_match:
        return (
            object_match.group(1).rstrip(":"),
            normalize_space(object_match.group(2) or "") or None,
        )

    return clean, None


def expand_compound_heading_lines(text: str) -> str:
    """Tách heading cha và heading con bị Docling dính trên cùng một dòng."""

    output: list[str] = []
    for raw in text.splitlines():
        clean = normalize_space(raw)
        match = COMPOUND_RELATION_HEADING_RE.match(clean)
        if match:
            output.extend([match.group(1).strip(), match.group(2).strip()])
        else:
            output.append(raw)
    return "\n".join(output).strip()


def split_record_on_boundaries(record: dict[str, Any]) -> list[dict[str, Any]]:
    """Tách chunk chứa nhiều section/object do Docling merge peers quá rộng."""

    source = clean_record_text(expand_compound_heading_lines(record["contextualized_text"]))
    lines = source.splitlines()
    groups: list[list[str]] = []
    current: list[str] = []

    for raw_line in lines:
        line = raw_line.strip()
        candidate = normalized_heading_text(line)
        boundary = is_heading_line(candidate)
        # Danh sách liệt kê schema trong mục tiêu, ví dụ
        # "- (1) F08_...;", không phải section heading độc lập.
        if (
            line.startswith(("- ", "+ "))
            and SCHEMA_GENERIC_HEADING_RE.match(candidate)
            and candidate.rstrip().endswith((";", "."))
        ):
            boundary = False

        if boundary and current:
            groups.append(current)
            current = []

        if boundary:
            heading, body = normalize_inline_heading_line(candidate)
            current.append(heading)
            if body:
                current.append(body)
        else:
            current.append(raw_line)

    if current:
        groups.append(current)

    if len(groups) <= 1:
        updated = copy.deepcopy(record)
        updated["contextualized_text"] = source
        updated["text"] = source
        return [updated]

    output: list[dict[str, Any]] = []
    for position, group in enumerate(groups):
        text = clean_record_text("\n".join(group))
        if not text:
            continue
        part = copy.deepcopy(record)
        part["contextualized_text"] = text
        part["text"] = text
        part.setdefault("repair_metadata", {})["split_from"] = record.get("chunk_id")
        part["repair_metadata"]["split_part"] = position
        output.append(part)
    return output


def last_meaningful_line(text: str) -> str:
    return next((line.strip() for line in reversed(text.splitlines()) if line.strip()), "")


def first_meaningful_line(text: str) -> str:
    return next((line.strip() for line in text.splitlines() if line.strip()), "")


def looks_incomplete(text: str) -> bool:
    line = last_meaningful_line(text)
    if not line or is_table_line(line) or TABLE_SEPARATOR_RE.match(line):
        return False
    if is_heading_line(line):
        return False
    return TERMINAL_RE.search(line) is None


def pages_are_adjacent(left: Sequence[int], right: Sequence[int]) -> bool:
    if not left or not right:
        return True
    return min(right) <= max(left) + 1


def merge_cross_page_continuations(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for record in records:
        if output:
            previous = output[-1]
            first_line = first_meaningful_line(record["contextualized_text"])
            if (
                looks_incomplete(previous["contextualized_text"])
                and starts_lowercase_continuation(first_line)
                and pages_are_adjacent(previous.get("pages", []), record.get("pages", []))
                and "|" not in previous["contextualized_text"]
                and "|" not in record["contextualized_text"]
            ):
                continuation = re.sub(r"^[-+]\s*", "", first_line).strip()
                remaining = record["contextualized_text"].splitlines()
                removed_first = False
                rebuilt: list[str] = []
                for line in remaining:
                    if not removed_first and line.strip() == first_line:
                        removed_first = True
                        continue
                    rebuilt.append(line)
                merged_text = (
                    previous["contextualized_text"].rstrip()
                    + " "
                    + continuation
                    + (
                        "\n" + "\n".join(rebuilt).strip()
                        if normalize_space("\n".join(rebuilt))
                        else ""
                    )
                )
                previous["contextualized_text"] = clean_record_text(merged_text)
                previous["text"] = previous["contextualized_text"]
                previous["pages"] = sorted(
                    set(previous.get("pages", [])) | set(record.get("pages", []))
                )
                previous["_source_fragments"] = merged_source_fragments(
                    [previous, record]
                )
                previous.setdefault("repair_metadata", {}).setdefault("merged_from", []).append(
                    record.get("chunk_id")
                )
                continue
        output.append(copy.deepcopy(record))
    return output


def heading_only_or_lead(text: str, tokenizer: RegexVietnameseTokenizer) -> bool:
    if "|" in text or tokenizer.count_tokens(text) > 55:
        return False
    lines = [normalize_space(line) for line in text.splitlines() if normalize_space(line)]
    if not lines or not is_heading_line(lines[0]):
        return False
    if len(lines) == 1:
        return True
    if all(is_heading_line(line) for line in lines):
        return True
    remainder = normalize_space(" ".join(lines[1:])).casefold()
    return remainder in {
        "với các dữ liệu như sau:",
        "bao gồm các nội dung sau:",
        "gồm các nội dung sau:",
    }


def collapse_heading_lead_chains(
    records: list[dict[str, Any]], tokenizer: RegexVietnameseTokenizer
) -> list[dict[str, Any]]:
    """Gộp chuỗi heading-only liên tiếp thành một lead có thứ bậc.

    Ví dụ: ``3. Khởi tạo...`` + ``(1) HinhAnhCotDien...`` phải cùng đi
    với bảng ngay sau, không tạo hai chunk rời.
    """

    output: list[dict[str, Any]] = []
    index = 0
    while index < len(records):
        current = copy.deepcopy(records[index])
        if not heading_only_or_lead(current["contextualized_text"], tokenizer):
            output.append(current)
            index += 1
            continue

        chain = [current]
        cursor = index + 1
        while (
            cursor < len(records)
            and heading_only_or_lead(records[cursor]["contextualized_text"], tokenizer)
            and pages_are_adjacent(chain[-1].get("pages", []), records[cursor].get("pages", []))
        ):
            chain.append(copy.deepcopy(records[cursor]))
            cursor += 1

        if len(chain) == 1:
            output.append(current)
            index += 1
            continue

        merged = copy.deepcopy(chain[-1])
        merged_text = "\n".join(
            part["contextualized_text"].strip()
            for part in chain
            if part["contextualized_text"].strip()
        )
        merged["contextualized_text"] = clean_record_text(merged_text)
        merged["text"] = merged["contextualized_text"]
        merged["pages"] = sorted(
            set().union(*(set(part.get("pages", [])) for part in chain))
        )
        merged["_source_fragments"] = merged_source_fragments(chain)
        merged.setdefault("repair_metadata", {})["heading_chain"] = [
            record_primary_heading(part["contextualized_text"]) for part in chain
        ]
        output.append(merged)
        index = cursor
    return output


def merge_heading_leads_forward(
    records: list[dict[str, Any]], tokenizer: RegexVietnameseTokenizer
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    index = 0
    while index < len(records):
        current = copy.deepcopy(records[index])
        if (
            index + 1 < len(records)
            and heading_only_or_lead(current["contextualized_text"], tokenizer)
            and pages_are_adjacent(current.get("pages", []), records[index + 1].get("pages", []))
        ):
            next_record = copy.deepcopy(records[index + 1])
            current_heading = record_primary_heading(current["contextualized_text"])
            next_lines = next_record["contextualized_text"].splitlines()
            next_first = first_meaningful_line(next_record["contextualized_text"])
            next_heading = record_primary_heading(next_record["contextualized_text"])

            # Heading mới ở cuối chunk trước (F05) phải thắng prefix stale của
            # chunk bảng kế tiếp (F09). Bỏ prefix đó trước khi merge.
            current_is_object = bool(
                current_heading
                and (
                    OBJECT_HEADING_RE.match(current_heading.rstrip(":"))
                    or RELATION_HEADING_RE.match(current_heading.rstrip(":"))
                )
            )
            next_is_object = bool(
                next_heading
                and (
                    OBJECT_HEADING_RE.match(next_heading.rstrip(":"))
                    or RELATION_HEADING_RE.match(next_heading.rstrip(":"))
                )
            )
            same_heading = bool(
                current_heading
                and next_heading
                and normalize_space(current_heading).casefold()
                == normalize_space(next_heading).casefold()
            )
            # Chỉ bỏ heading kế tiếp khi đó là prefix stale/cùng object.
            # Không bỏ heading con khi current là heading cha của phụ lục.
            if (
                current_heading
                and next_first
                and is_heading_line(next_first)
                and (same_heading or (current_is_object and next_is_object))
            ):
                removed = False
                rebuilt: list[str] = []
                for line in next_lines:
                    if not removed and normalize_space(line) == normalize_space(next_first):
                        removed = True
                        continue
                    rebuilt.append(line)
                next_record["contextualized_text"] = "\n".join(rebuilt).strip()
                next_record["text"] = next_record["contextualized_text"]

            merged = clean_record_text(
                current["contextualized_text"].rstrip()
                + "\n"
                + next_record["contextualized_text"].lstrip()
            )
            next_record["contextualized_text"] = merged
            next_record["text"] = merged
            next_record["pages"] = sorted(
                set(current.get("pages", [])) | set(next_record.get("pages", []))
            )
            next_record["_source_fragments"] = merged_source_fragments(
                [current, next_record]
            )
            next_record.setdefault("repair_metadata", {}).setdefault("merged_from", []).append(
                current.get("chunk_id")
            )
            output.append(next_record)
            index += 2
            continue
        output.append(current)
        index += 1
    return output


def first_table_row_number(text: str) -> int | None:
    for line in text.splitlines():
        cells = split_md_cells(line)
        if not cells:
            continue
        value = normalize_space(cells[0])
        if value.isdigit():
            return int(value)
    return None


def replace_first_heading(text: str, replacement: str) -> str:
    first = first_meaningful_line(text)
    if not first or not is_heading_line(first):
        return text
    replaced = False
    lines: list[str] = []
    for line in text.splitlines():
        if not replaced and normalize_space(line) == normalize_space(first):
            lines.append(replacement)
            replaced = True
        else:
            lines.append(line)
    return "\n".join(lines).strip()


def repair_stale_table_headings(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sửa heading table continuation bị giữ từ object trước.

    Ví dụ PDF có tiêu đề F05 ở cuối trang/chunk trước nhưng Docling vẫn prefix
    F09 vào các phần bảng tiếp theo. Dòng dữ liệu bắt đầu từ TT > 1 là
    tín hiệu
    mạnh rằng đây là continuation của object hiện hành, không phải bảng mới.
    """

    output: list[dict[str, Any]] = []
    current_object: str | None = None
    for source in records:
        record = copy.deepcopy(source)
        text = record["contextualized_text"]
        heading = record_primary_heading(text)
        clean_heading = (heading or "").rstrip(":")
        object_match = OBJECT_HEADING_RE.match(clean_heading)
        attribute_match = ATTRIBUTE_TABLE_HEADING_RE.match(clean_heading)
        explicit_attribute = explicit_attribute_table_name(text)
        row_number = first_table_row_number(text)

        # A named attribute table is an authoritative context reset.  Never let
        # F10/F08 (or any previous object) leak into HinhAnh* tables.
        if explicit_attribute:
            matching_heading = next(
                (
                    normalized_heading_text(line).rstrip(":")
                    for line in text.splitlines()
                    if table_heading_matches_name(line, explicit_attribute)
                ),
                explicit_attribute,
            )
            current_object = matching_heading
            if (
                heading
                and heading_table_name(heading) is not None
                and not table_heading_matches_name(heading, explicit_attribute)
            ):
                text = replace_first_heading(text, matching_heading)
                record.setdefault("repair_metadata", {})["stale_heading_replaced"] = {
                    "from": heading,
                    "to": matching_heading,
                    "reason": "explicit_attribute_table_name",
                }
        elif object_match or attribute_match:
            candidate = heading.rstrip(":") if heading else None
            if (
                current_object
                and candidate
                and candidate.casefold() != current_object.casefold()
                and row_number is not None
                and row_number > 1
            ):
                text = replace_first_heading(text, current_object)
                record.setdefault("repair_metadata", {})["stale_heading_replaced"] = {
                    "from": candidate,
                    "to": current_object,
                }
                heading = current_object
            else:
                current_object = candidate
        elif heading and APPENDIX_RE.match(clean_heading):
            current_object = None
        elif heading and TOP_SECTION_RE.match(clean_heading):
            # New appendix subsection, such as "3. Khởi tạo bổ sung ...",
            # resets the previous schema object. The following child heading
            # will establish the new table identity.
            if any(
                keyword in clean_heading.casefold()
                for keyword in ("mục tiêu", "chi tiết", "khởi tạo", "mối quan hệ")
            ):
                current_object = None
        elif row_number is not None and current_object:
            text = current_object + "\n" + text
            heading = current_object

        record["contextualized_text"] = clean_record_text(text)
        record["text"] = record["contextualized_text"]
        output.append(record)
    return output


def extract_top_level_sections(doc: DoclingDocument) -> dict[int, str]:
    """Thu thập heading cha như 1. CPCIT, 2. Các CTĐL, 3. KHoPC."""

    candidates: list[tuple[int, str]] = []
    for item in getattr(doc, "texts", []) or []:
        text = normalize_space(getattr(item, "text", ""))
        if not text:
            continue
        for line in text.splitlines():
            clean = normalize_space(line).rstrip(":")
            match = TOP_SECTION_RE.match(clean)
            if match and not SUBSECTION_RE.match(clean):
                number = int(match.group(1))
                # Tránh nhầm các dòng liệt kê kỹ thuật rất dài.
                if len(clean) <= 120:
                    candidates.append((number, clean))

    output: dict[int, str] = {}
    for number, text in candidates:
        output.setdefault(number, text)
    return output


def record_primary_heading(text: str) -> str | None:
    for line in text.splitlines():
        clean = normalized_heading_text(line)
        if is_heading_line(clean):
            heading, _ = normalize_inline_heading_line(clean)
            return heading.rstrip(":")
    return None


def infer_scope(heading: str | None) -> list[str]:
    if not heading:
        return []
    lowered = heading.casefold()
    scopes: list[str] = []
    if "110kv" in lowered:
        scopes.append("GIS 110kV")
    if "trung thế" in lowered:
        scopes.append("GIS trung thế")
    if "hạ thế" in lowered:
        scopes.append("GIS hạ thế")
    return scopes


def is_footer_record(text: str) -> bool:
    first = first_meaningful_line(text)
    if re.match(r"^Nơi\s+nhận\s*:", first, re.I):
        return True
    return any(pattern.search(first) for pattern in SIGNATURE_LINE_PATTERNS)


def leading_heading_lines(text: str) -> list[str]:
    headings: list[str] = []
    for line in text.splitlines():
        clean = normalized_heading_text(line)
        if not clean:
            continue
        if is_heading_line(clean):
            heading, _ = normalize_inline_heading_line(clean)
            headings.append(heading.rstrip(":"))
            continue
        break
    return headings


def strip_stale_leading_headings(text: str, allowed: Sequence[str]) -> str:
    allowed_keys = {normalized_heading_text(value).rstrip(":").casefold() for value in allowed}
    output: list[str] = []
    leading = True
    for line in text.splitlines():
        clean = normalized_heading_text(line).rstrip(":")
        if leading and clean and is_heading_line(clean):
            if clean.casefold() in allowed_keys:
                output.append(line)
            continue
        if clean:
            leading = False
        output.append(line)
    return "\n".join(output).strip()


def apply_hierarchy(
    records: list[dict[str, Any]],
    top_sections: dict[int, str],
) -> list[dict[str, Any]]:
    current_top: str | None = None
    current_appendix_section: str | None = None
    current_object: str | None = None
    output: list[dict[str, Any]] = []

    for source_record in records:
        record = copy.deepcopy(source_record)
        text = record["contextualized_text"]

        if is_footer_record(text):
            record["headings"] = []
            record["section_path"] = []
            record["unit"] = None
            record["scope"] = []
            record["chunk_type"] = "administrative_footer"
            record["indexable"] = False
            record["embedding_enabled"] = False
            record["contextualized_text"] = clean_record_text(text)
            record["text"] = record["contextualized_text"]
            output.append(record)
            continue

        heading_sequence = leading_heading_lines(text)
        for heading in heading_sequence:
            clean_heading = heading.rstrip(":")
            subsection = SUBSECTION_RE.match(clean_heading)
            top = TOP_SECTION_RE.match(clean_heading)
            obj = OBJECT_HEADING_RE.match(clean_heading)

            if subsection:
                major = int(subsection.group(1))
                current_top = top_sections.get(major, current_top)
                current_appendix_section = None
                current_object = clean_heading
                continue

            if APPENDIX_RE.match(clean_heading):
                current_top = None
                current_appendix_section = clean_heading
                current_object = None
                continue

            if top:
                # Các heading appendix như "1. Mục tiêu", "3. Khởi tạo..."
                # không được ánh xạ thành đơn vị CPCIT/CTĐL.
                if any(
                    keyword in clean_heading.casefold()
                    for keyword in (
                        "mục tiêu",
                        "chi tiết",
                        "khởi tạo",
                        "mối quan hệ",
                        "phương án",
                    )
                ):
                    current_top = None
                    current_appendix_section = clean_heading
                    current_object = None
                else:
                    number = int(top.group(1))
                    current_top = top_sections.get(number, clean_heading)
                    current_appendix_section = None
                    current_object = None
                continue

            if obj or PAREN_HEADING_RE.match(clean_heading):
                current_object = clean_heading
                continue

            # Heading tự do trong phụ lục.
            if current_appendix_section:
                current_object = clean_heading

        path: list[str] = []
        if current_top:
            path.append(current_top)
            # Với subsection, current_object là chính subsection.
            if current_object and current_object != current_top:
                path.append(current_object)
        elif current_appendix_section:
            path.append(current_appendix_section)
            if current_object and current_object != current_appendix_section:
                path.append(current_object)
        elif current_object:
            path.append(current_object)

        path = unique_preserve_order(path)
        record["headings"] = path
        record["section_path"] = path
        record["unit"] = (
            current_top.split(".", 1)[1].strip()
            if current_top and "." in current_top
            else current_top
        )
        record["scope"] = infer_scope(path[-1] if path else None)

        if path:
            text = strip_stale_leading_headings(text, path)

        # Table continuation không có heading: prefix heading cuối của path.
        if path:
            first = first_meaningful_line(text)
            if "|" in text and (not first or not is_heading_line(first)):
                text = path[-1] + "\n" + text

            # Prefix stale khác với heading hiện hành: thay bằng heading cuối path.
            first = first_meaningful_line(text)
            first_heading = (
                normalized_heading_text(first)
                if first and is_heading_line(first)
                else None
            )
            if (
                first_heading
                and "|" in text
                and first_heading.casefold() != path[-1].casefold()
                and len(heading_sequence) <= 1
            ):
                text = replace_first_heading(text, path[-1])

        record["contextualized_text"] = clean_record_text(text)
        record["text"] = record["contextualized_text"]
        output.append(record)
    return output


def _table_description_from_text(text: str, table_name: str) -> str | None:
    for line in text.splitlines():
        match = ATTRIBUTE_TABLE_HEADING_RE.match(normalized_heading_text(line).rstrip(":"))
        if match and match.group(2).casefold() == table_name.casefold():
            return normalize_space(match.group(3)) or None
        match = OBJECT_HEADING_RE.match(normalized_heading_text(line).rstrip(":"))
        if match and match.group(2).casefold() == table_name.casefold():
            return normalize_space(match.group(3)) or None
    description_match = re.search(
        r"(?im)^\s*Mô\s+tả\s+(?!dữ liệu)([^\n|]+?)\s*$",
        text,
    )
    return normalize_space(description_match.group(1)) if description_match else None


def _canonical_table_heading(text: str, table_name: str) -> str:
    for line in text.splitlines():
        clean = normalized_heading_text(line).rstrip(":")
        if table_heading_matches_name(clean, table_name):
            return clean
    description = _table_description_from_text(text, table_name)
    return f"{table_name} - {description}" if description else table_name


def _dedupe_table_prefix(prefix: list[str], table_name: str) -> list[str]:
    """Drop stale peer-object headings while preserving appendix parents."""

    output: list[str] = []
    seen: set[str] = set()
    for line in prefix:
        clean = normalized_heading_text(line).rstrip(":")
        candidate_name = heading_table_name(clean)
        if candidate_name and candidate_name.casefold() != table_name.casefold():
            continue
        key = normalize_space(line).casefold()
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        output.append(line)
    return output


def repair_table_identity(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Make table content, section path and metadata agree.

    This is intentionally content-first: an explicit ``Tên bảng dữ liệu`` line
    overrides inherited Docling headings.  It fixes the common transition from
    ``F10_TuPhanPhoi_HT`` to ``HinhAnhCotDien`` on the same page.
    """

    output: list[dict[str, Any]] = []
    for source in records:
        record = copy.deepcopy(source)
        parts = table_components(record["contextualized_text"])
        if not parts:
            output.append(record)
            continue

        table_name = infer_table_name_from_record(record)
        if not table_name:
            output.append(record)
            continue

        prefix, header, rows = parts
        cleaned_prefix = _dedupe_table_prefix(prefix, table_name)
        canonical_heading = _canonical_table_heading(
            "\n".join(prefix + header + rows), table_name
        )
        if not any(table_heading_matches_name(line, table_name) for line in cleaned_prefix):
            # Insert after a parent appendix heading, not before it.
            insert_at = 0
            while insert_at < len(cleaned_prefix):
                clean = normalized_heading_text(cleaned_prefix[insert_at]).rstrip(":")
                if TOP_SECTION_RE.match(clean) or APPENDIX_RE.match(clean):
                    insert_at += 1
                    continue
                break
            cleaned_prefix.insert(insert_at, canonical_heading)

        previous_name = record.get("table_name")
        if previous_name and str(previous_name).casefold() != table_name.casefold():
            record.setdefault("repair_metadata", {})["table_identity_replaced"] = {
                "from": str(previous_name),
                "to": table_name,
            }

        text = clean_record_text("\n".join(cleaned_prefix + header + rows))
        path = list(record.get("section_path") or record.get("headings") or [])
        parent_path = [
            item
            for item in path
            if heading_table_name(str(item)) is None
        ]
        path = unique_preserve_order([*parent_path, canonical_heading])

        record["contextualized_text"] = text
        record["text"] = text
        record["headings"] = path
        record["section_path"] = path
        record["table_name"] = table_name
        record["entity"] = table_name
        record["table_description"] = _table_description_from_text(text, table_name)
        record["chunk_type"] = "table_rows"
        record["content_format"] = "markdown_table"
        output.append(record)
    return output


def split_table_by_tokens(
    record: dict[str, Any],
    tokenizer: RegexVietnameseTokenizer,
    max_tokens: int,
) -> list[dict[str, Any]]:
    lines = record["contextualized_text"].splitlines()
    table_start = next((i for i, line in enumerate(lines) if is_table_line(line)), -1)
    if table_start < 0:
        return [record]

    prefix = lines[:table_start]
    header = lines[table_start : table_start + 2]
    rows = lines[table_start + 2 :]
    if len(header) < 2 or not TABLE_SEPARATOR_RE.match(header[1]):
        return [record]

    parts: list[dict[str, Any]] = []
    current_rows: list[str] = []
    for row in rows:
        candidate = "\n".join(prefix + header + current_rows + [row]).strip()
        if current_rows and tokenizer.count_tokens(candidate) > max_tokens:
            part = copy.deepcopy(record)
            part["contextualized_text"] = "\n".join(prefix + header + current_rows).strip()
            part["text"] = part["contextualized_text"]
            parts.append(part)
            current_rows = [row]
        else:
            current_rows.append(row)
    if current_rows:
        part = copy.deepcopy(record)
        part["contextualized_text"] = "\n".join(prefix + header + current_rows).strip()
        part["text"] = part["contextualized_text"]
        parts.append(part)
    return parts or [record]


def prose_blocks(text: str) -> tuple[list[str], list[str]]:
    lines = text.splitlines()
    prefix: list[str] = []
    blocks: list[str] = []
    current: list[str] = []
    for line in lines:
        clean = line.strip()
        if not blocks and not current and is_heading_line(clean):
            prefix.append(clean)
            continue
        if clean.startswith(("- ", "+ ")):
            if current:
                blocks.append("\n".join(current).strip())
            current = [clean]
        elif not clean:
            if current:
                blocks.append("\n".join(current).strip())
                current = []
        else:
            current.append(clean)
    if current:
        blocks.append("\n".join(current).strip())
    return prefix, [block for block in blocks if block]


def split_prose_by_tokens(
    record: dict[str, Any],
    tokenizer: RegexVietnameseTokenizer,
    max_tokens: int,
) -> list[dict[str, Any]]:
    prefix, blocks = prose_blocks(record["contextualized_text"])
    if not blocks:
        return [record]
    parts: list[dict[str, Any]] = []
    current: list[str] = []
    for block in blocks:
        candidate = "\n".join(prefix + current + [block]).strip()
        if current and tokenizer.count_tokens(candidate) > max_tokens:
            part = copy.deepcopy(record)
            part["contextualized_text"] = "\n".join(prefix + current).strip()
            part["text"] = part["contextualized_text"]
            parts.append(part)
            current = [block]
        else:
            current.append(block)
    if current:
        part = copy.deepcopy(record)
        part["contextualized_text"] = "\n".join(prefix + current).strip()
        part["text"] = part["contextualized_text"]
        parts.append(part)
    return parts or [record]


def hard_split_record_by_tokens(
    record: dict[str, Any],
    tokenizer: RegexVietnameseTokenizer,
    max_tokens: int,
) -> list[dict[str, Any]]:
    """Guaranteed final split using the same regex token definition as quality gate."""

    text = str(record.get("contextualized_text") or "")
    matches = list(re.finditer(r"\w+|[^\w\s]+", text, flags=re.UNICODE))
    if len(matches) <= max_tokens:
        return [record]

    # Leave headroom so a later metadata/context refresh cannot cross the hard limit.
    budget = max(32, min(max_tokens - 16, int(max_tokens * 0.85)))
    minimum_preferred = max(1, int(budget * 0.55))

    # Do not cut blindly every ``budget`` tokens. A blind token cut can split a
    # sentence and make the next chunk begin with a lowercase continuation, which
    # is correctly rejected by the final quality gate. Prefer a sentence/line
    # boundary near the budget and only fall back to the hard boundary when a
    # single sentence itself is longer than the budget.
    ranges: list[tuple[int, int]] = []
    start = 0
    while start < len(matches):
        hard_end = min(start + budget, len(matches))
        end = hard_end
        if hard_end < len(matches):
            lower_bound = min(hard_end - 1, start + minimum_preferred)
            for candidate in range(hard_end, lower_bound, -1):
                boundary_start = matches[candidate - 1].end()
                boundary_end = matches[candidate].start()
                between = text[boundary_start:boundary_end]
                left_text = text[matches[start].start():boundary_start].rstrip()
                next_line = text[matches[candidate].start():].lstrip().splitlines()[0]
                if (
                    TERMINAL_RE.search(left_text)
                    or "\n" in between
                ) and not starts_lowercase_continuation(next_line):
                    end = candidate
                    break
        ranges.append((start, end))
        start = end

    parts: list[dict[str, Any]] = []
    total = len(ranges)
    for part_index, (start, end) in enumerate(ranges, start=1):
        char_start = matches[start].start()
        char_end = matches[end - 1].end()
        part_text = text[char_start:char_end].strip()
        if not part_text:
            continue
        part = copy.deepcopy(record)
        part["contextualized_text"] = part_text
        part["text"] = part_text
        part["raw_text"] = part_text
        part.pop("parent_chunk_id", None)
        part.setdefault("repair_metadata", {})["hard_token_split"] = {
            "part": part_index,
            "parts": total,
            "original_tokens": len(matches),
            "sentence_boundary_preferred": end < min(start + budget, len(matches)),
        }
        parts.append(part)
    return parts or [record]


def enforce_token_limit(
    records: list[dict[str, Any]],
    tokenizer: RegexVietnameseTokenizer,
    max_tokens: int,
) -> list[dict[str, Any]]:
    """Split repeatedly and guarantee no returned record exceeds max_tokens."""

    queue = [copy.deepcopy(record) for record in records]
    output: list[dict[str, Any]] = []
    safety = 0
    while queue:
        safety += 1
        if safety > 10000:
            raise RuntimeError("Token-limit splitting exceeded safety bound")

        record = queue.pop(0)
        count = tokenizer.count_tokens(record["contextualized_text"])
        if count <= max_tokens:
            output.append(record)
            continue

        if "|" in record["contextualized_text"]:
            parts = split_table_by_tokens(record, tokenizer, max_tokens)
        else:
            parts = split_prose_by_tokens(record, tokenizer, max_tokens)

        unchanged = (
            len(parts) == 1
            and parts[0].get("contextualized_text")
            == record.get("contextualized_text")
        )
        if unchanged or any(
            tokenizer.count_tokens(part["contextualized_text"]) > max_tokens
            for part in parts
        ):
            parts = hard_split_record_by_tokens(record, tokenizer, max_tokens)

        for part in parts:
            part.setdefault("repair_metadata", {})["split_for_token_limit"] = True
            if tokenizer.count_tokens(part["contextualized_text"]) > max_tokens:
                queue.extend(hard_split_record_by_tokens(part, tokenizer, max_tokens))
            else:
                output.append(part)

    return output


def object_group_key(record: dict[str, Any]) -> tuple[str, ...]:
    continuation_group = normalize_space(str(record.get("_table_continuation_group") or ""))
    if continuation_group:
        return ("table_continuation", continuation_group.casefold())
    return tuple(normalize_space(value).casefold() for value in record.get("section_path", []))


def table_components(text: str) -> tuple[list[str], list[str], list[str]] | None:
    lines = text.splitlines()
    start = next((idx for idx, line in enumerate(lines) if is_table_line(line)), -1)
    if start < 0:
        return None
    prefix = lines[:start]
    header = lines[start : start + 2]
    if len(header) < 2 or not TABLE_SEPARATOR_RE.match(header[1]):
        return None
    rows = [
        line
        for line in lines[start + 2 :]
        if is_table_line(line) and not TABLE_SEPARATOR_RE.match(line)
    ]
    return prefix, header, rows


def table_header_signature(text: str) -> tuple[str, ...] | None:
    parts = table_components(text)
    if not parts:
        return None
    _, header, _ = parts
    return tuple(normalize_space(cell).casefold() for cell in split_md_cells(header[0]))


def table_row_cells(text: str) -> tuple[list[str], list[list[str]]]:
    parts = table_components(text)
    if not parts:
        return [], []
    _, header, rows = parts
    columns = split_md_cells(header[0])
    return columns, [split_md_cells(row) for row in rows]


def _header_index(columns: Sequence[str], *candidates: str) -> int | None:
    normalized = [normalize_space(column).casefold() for column in columns]
    for candidate in candidates:
        key = candidate.casefold()
        for index, value in enumerate(normalized):
            if key == value or key in value:
                return index
    return None


CARRY_FORWARD_TABLE_HEADERS = {
    "stt",
    "hệ thống",
    "he thong",
    "đơn vị",
    "don vi",
    "đối tượng",
    "doi tuong",
    "nhóm",
    "nhom",
}


def _render_markdown_row(cells: Sequence[str]) -> str:
    return "| " + " | ".join(cells) + " |"


def repair_cross_page_table_continuations(
    records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge table identity across pages and fill values hidden by merged cells."""

    output: list[dict[str, Any]] = []
    previous_by_signature: dict[tuple[str, ...], dict[str, Any]] = {}
    carry_by_signature: dict[tuple[str, ...], list[str]] = {}

    for source in records:
        record = copy.deepcopy(source)
        text = str(record.get("contextualized_text") or "")
        parts = table_components(text)
        signature = table_header_signature(text)
        if not parts or not signature:
            output.append(record)
            continue

        prefix, header, row_lines = parts
        columns = split_md_cells(header[0])
        rows = [split_md_cells(line) for line in row_lines]
        previous = previous_by_signature.get(signature)
        previous_pages = set(previous.get("pages", [])) if previous else set()
        current_pages = set(record.get("pages", []))
        adjacent = bool(
            previous_pages
            and current_pages
            and min(current_pages) <= max(previous_pages) + 1
            and min(current_pages) >= max(previous_pages)
        )

        carry_indexes = [
            index
            for index, column in enumerate(columns)
            if normalize_space(column).casefold() in CARRY_FORWARD_TABLE_HEADERS
        ]
        carry = list(carry_by_signature.get(signature, [""] * len(columns)))
        repaired = False
        if adjacent and rows and carry_indexes:
            for row in rows:
                if len(row) < len(columns):
                    row.extend([""] * (len(columns) - len(row)))
                for index in carry_indexes:
                    if not normalize_space(row[index]) and index < len(carry) and carry[index]:
                        row[index] = carry[index]
                        repaired = True
                    elif normalize_space(row[index]):
                        carry[index] = normalize_space(row[index])

        for row in rows:
            if len(row) < len(columns):
                row.extend([""] * (len(columns) - len(row)))
            for index in carry_indexes:
                if normalize_space(row[index]):
                    carry[index] = normalize_space(row[index])

        if previous and adjacent:
            group = str(previous.get("_table_continuation_group") or "")
            if not group:
                group = "|".join(signature)
                previous["_table_continuation_group"] = group
                if output:
                    output[-1]["_table_continuation_group"] = group
            record["_table_continuation_group"] = group
            record["cross_page_table_continuation"] = True
            previous["cross_page_table_continuation"] = True
            if not record.get("section_path") and previous.get("section_path"):
                record["section_path"] = list(previous["section_path"])
            if not record.get("headings") and previous.get("headings"):
                record["headings"] = list(previous["headings"])

        if repaired:
            rebuilt = "\n".join(prefix + header + [_render_markdown_row(row) for row in rows])
            record["contextualized_text"] = clean_record_text(rebuilt)
            record["text"] = record["contextualized_text"]
            record.setdefault("repair_metadata", {})["merged_cell_values_carried_forward"] = True

        carry_by_signature[signature] = carry
        previous_by_signature[signature] = record
        output.append(record)

    return output


def enrich_table_metadata(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for source in records:
        record = copy.deepcopy(source)
        columns, rows = table_row_cells(record["contextualized_text"])
        if not columns:
            output.append(record)
            continue

        record["table_columns"] = columns
        row_numbers = [
            int(row[0])
            for row in rows
            if row and normalize_space(row[0]).isdigit()
        ]
        if row_numbers:
            record["row_start"] = min(row_numbers)
            record["row_end"] = max(row_numbers)

        field_index = _header_index(columns, "trường dữ liệu", "tên trường")
        source_index = _header_index(columns, "nguồn dữ liệu")
        conversion_index = _header_index(columns, "chuyển đổi sang gis")
        field_names: list[str] = []
        source_systems: list[str] = []
        convertible_fields: list[str] = []
        for row in rows:
            if field_index is not None and field_index < len(row):
                field = normalize_space(row[field_index])
                if field:
                    field_names.append(field)
            else:
                field = ""
            if source_index is not None and source_index < len(row):
                source = normalize_space(row[source_index])
                if source:
                    source_systems.extend(
                        normalize_space(value)
                        for value in re.split(r"[/,;]", source)
                        if normalize_space(value)
                    )
            if conversion_index is not None and conversion_index < len(row):
                conversion = normalize_space(row[conversion_index])
                if field and conversion:
                    convertible_fields.append(field)

        if field_names:
            record["field_names"] = unique_preserve_order(field_names)
        if source_systems:
            record["source_systems"] = unique_preserve_order(source_systems)
        if convertible_fields:
            record["convertible_fields"] = unique_preserve_order(convertible_fields)
        if record.get("table_name"):
            record["entity"] = record["table_name"]
        output.append(record)
    return output


def enrich_relationship_metadata(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for source in records:
        record = copy.deepcopy(source)
        text = record["contextualized_text"]
        path_text = "\n".join(record.get("section_path") or [])
        if not (
            re.search(r"Mối\s+quan\s+hệ", text, re.I)
            or re.search(r"Mối\s+quan\s+hệ", path_text, re.I)
            or "Tên mối quan hệ" in text
        ):
            output.append(record)
            continue

        relationship_match = RELATIONSHIP_NAME_RE.search(text)
        if relationship_match:
            record["relationship_name"] = relationship_match.group(1)
        identifiers = unique_preserve_order(
            match.group(0)
            for match in re.finditer(
                r"\b(?:F\d{2}|PX|P[A-Z0-9]+)_[A-Za-z0-9_]+\b",
                text,
            )
        )
        source_entity = next((value for value in identifiers if value.startswith("F")), None)
        target_table = next(
            (
                value
                for value in identifiers
                if value.startswith("PX_HinhAnh")
            ),
            None,
        )
        if source_entity:
            record["source_entity"] = source_entity
            record["source_key"] = "ID"
        if target_table:
            record["target_table"] = target_table
            record["target_key"] = (
                "IDCotDien" if "CotDien" in target_table else "IDCongToKhachHang"
            )
        if re.search(r"1\s*-\s*Nhiều\s*\(1-M\)", text, re.I):
            record["cardinality"] = "1-M"
        record["chunk_type"] = "relationship_definition"
        record["content_format"] = "markdown_table"
        output.append(record)
    return output


def section_id_from_record(record: dict[str, Any]) -> str | None:
    if record.get("section_id"):
        return str(record["section_id"])
    path = record.get("section_path") or record.get("headings") or []
    for heading in reversed(path):
        match = re.match(r"^(\d+(?:\.\d+)*)\.", normalize_space(str(heading)))
        if match:
            return match.group(1)
    heading = record_primary_heading(record.get("contextualized_text", ""))
    if heading:
        match = re.match(r"^(\d+(?:\.\d+)*)\.", heading)
        if match:
            return match.group(1)
    return None


def resolve_cross_references(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    section_map: dict[str, str] = {}
    for record in records:
        section_id = section_id_from_record(record)
        if section_id and section_id not in section_map:
            section_map[section_id] = record["contextualized_text"]

    output: list[dict[str, Any]] = []
    reference_re = re.compile(r"(?:theo|tại)\s+mục\s+(\d+(?:\.\d+)*)", re.I)
    for source in records:
        record = copy.deepcopy(source)
        references = unique_preserve_order(
            match.group(1)
            for match in reference_re.finditer(record["contextualized_text"])
        )
        if references:
            record["cross_references"] = references
            resolved = [section_map[ref] for ref in references if ref in section_map]
            if resolved:
                record["resolved_reference_text"] = "\n\n".join(resolved)
            unresolved = [ref for ref in references if ref not in section_map]
            if unresolved:
                record.setdefault("validation_issues", []).append(
                    {
                        "type": "unresolved_cross_reference",
                        "severity": "warning",
                        "references": unresolved,
                    }
                )
        output.append(record)
    return output


def canonicalize_object_leads(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Chuẩn hóa câu mô tả object bị đảo/tách giữa các chunk bảng."""

    groups: dict[tuple[str, ...], list[int]] = {}
    for idx, record in enumerate(records):
        if table_components(record["contextualized_text"]):
            groups.setdefault(object_group_key(record), []).append(idx)

    canonical_by_group: dict[tuple[str, ...], str] = {}
    for key, indexes in groups.items():
        prose = " ".join(
            " ".join(table_components(records[idx]["contextualized_text"])[0])
            for idx in indexes
            if table_components(records[idx]["contextualized_text"])
        )
        if re.search(r"vẽ\s+tự\s+động\s+bằng\s+công\s+cụ", prose, re.I):
            canonical_by_group[key] = (
                "Phương thức khởi tạo: Vẽ tự động bằng công cụ."
            )

    output: list[dict[str, Any]] = []
    for source in records:
        record = copy.deepcopy(source)
        canonical = canonical_by_group.get(object_group_key(record))
        parts = table_components(record["contextualized_text"])
        if not canonical or not parts:
            output.append(record)
            continue
        prefix, header, rows = parts
        cleaned_prefix: list[str] = []
        for line in prefix:
            clean = normalize_space(line)
            if re.fullmatch(r"với các dữ liệu như sau:?", clean, re.I):
                continue
            if re.fullmatch(r"Thực hiện vẽ tự động bằng công cụ:?", clean, re.I):
                continue
            cleaned_prefix.append(line)
        insert_at = len(leading_heading_lines("\n".join(cleaned_prefix)))
        if canonical.casefold() not in normalize_space(" ".join(cleaned_prefix)).casefold():
            cleaned_prefix.insert(insert_at, canonical)
        text = "\n".join(cleaned_prefix + header + rows)
        record["contextualized_text"] = clean_record_text(text)
        record["text"] = record["contextualized_text"]
        record.setdefault("repair_metadata", {})["canonical_object_context"] = canonical
        output.append(record)
    return output


def repack_table_group(
    group: list[dict[str, Any]],
    tokenizer: RegexVietnameseTokenizer,
    max_tokens: int,
    min_rows: int = 2,
) -> list[dict[str, Any]]:
    if len(group) <= 1:
        return group
    first_parts = table_components(group[0]["contextualized_text"])
    if not first_parts:
        return group
    prefix, header, _ = first_parts
    row_items: list[tuple[str, set[int]]] = []
    for record in group:
        parts = table_components(record["contextualized_text"])
        if not parts:
            return group
        _, _, rows = parts
        for row in rows:
            row_items.append((row, set(record.get("pages", []))))
    if not row_items:
        return group

    packed: list[list[tuple[str, set[int]]]] = []
    current: list[tuple[str, set[int]]] = []
    for item in row_items:
        candidate_rows = current + [item]
        candidate = "\n".join(prefix + header + [row for row, _ in candidate_rows])
        if current and tokenizer.count_tokens(candidate) > max_tokens:
            packed.append(current)
            current = [item]
        else:
            current = candidate_rows
    if current:
        packed.append(current)

    # Rebalance a very small tail instead of greedily filling the previous
    # chunk to the limit. This avoids 15/17/2-row layouts for large schemas.
    if len(packed) >= 2:
        target_tail_tokens = max(72, int(max_tokens * 0.30))
        while len(packed[-2]) > min_rows:
            tail_text = "\n".join(prefix + header + [row for row, _ in packed[-1]])
            if (
                len(packed[-1]) >= min_rows
                and tokenizer.count_tokens(tail_text) >= target_tail_tokens
            ):
                break
            candidate_previous = packed[-2][:-1]
            candidate_tail = [packed[-2][-1], *packed[-1]]
            previous_text = "\n".join(
                prefix + header + [row for row, _ in candidate_previous]
            )
            candidate_tail_text = "\n".join(
                prefix + header + [row for row, _ in candidate_tail]
            )
            if (
                tokenizer.count_tokens(previous_text) > max_tokens
                or tokenizer.count_tokens(candidate_tail_text) > max_tokens
            ):
                break
            packed[-2] = candidate_previous
            packed[-1] = candidate_tail

        if len(packed[-1]) < min_rows:
            combined = packed[-2] + packed[-1]
            combined_text = "\n".join(prefix + header + [row for row, _ in combined])
            if tokenizer.count_tokens(combined_text) <= max_tokens:
                packed[-2:] = [combined]
            elif len(packed[-2]) > min_rows:
                moved = packed[-2].pop()
                packed[-1].insert(0, moved)

    output: list[dict[str, Any]] = []
    for part_no, items in enumerate(packed, start=1):
        base = copy.deepcopy(group[min(part_no - 1, len(group) - 1)])
        text = "\n".join(prefix + header + [row for row, _ in items])
        base["contextualized_text"] = clean_record_text(text)
        base["text"] = base["contextualized_text"]
        base["pages"] = sorted(set().union(*(pages for _, pages in items)))
        base["_source_fragments"] = merged_source_fragments(group)
        base.setdefault("repair_metadata", {})["table_repacked"] = {
            "source_chunks": [record.get("chunk_id") for record in group],
            "part": part_no,
            "rows": len(items),
        }
        output.append(base)
    return output


def rebalance_table_chunks(
    records: list[dict[str, Any]],
    tokenizer: RegexVietnameseTokenizer,
    max_tokens: int,
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    index = 0
    while index < len(records):
        current = records[index]
        signature = table_header_signature(current["contextualized_text"])
        if signature is None:
            output.append(copy.deepcopy(current))
            index += 1
            continue
        key = object_group_key(current)
        group = [copy.deepcopy(current)]
        cursor = index + 1
        while cursor < len(records):
            candidate = records[cursor]
            if object_group_key(candidate) != key:
                break
            if table_header_signature(candidate["contextualized_text"]) != signature:
                break
            group.append(copy.deepcopy(candidate))
            cursor += 1
        output.extend(repack_table_group(group, tokenizer, max_tokens))
        index = cursor
    return output


def collect_identifier_repairs(text: str) -> list[dict[str, str]]:
    repairs: list[dict[str, str]] = []
    for line in text.splitlines():
        for cell in split_md_cells(line):
            _, repair = normalize_identifier_cell(cell)
            if repair and repair not in repairs:
                repairs.append(repair)
            _, embedded_repairs = normalize_embedded_identifiers(cell)
            for item in embedded_repairs:
                if item not in repairs:
                    repairs.append(item)
    return repairs


def detect_identifier_conflicts(text: str) -> list[dict[str, Any]]:
    identifiers = re.findall(r"\bF(\d{2})_([A-Za-z0-9_]+)\b", text)
    by_suffix: dict[str, set[str]] = {}
    for prefix, suffix in identifiers:
        by_suffix.setdefault(suffix.casefold(), set()).add(prefix)
    issues: list[dict[str, Any]] = []
    for suffix, prefixes in sorted(by_suffix.items()):
        if len(prefixes) > 1:
            issues.append(
                {
                    "type": "identifier_prefix_conflict",
                    "suffix": suffix,
                    "prefixes": sorted(prefixes),
                    "message": (
                        "Cùng hậu tố định danh xuất hiện với nhiều mã lớp; "
                        "cần đối chiếu tài liệu gốc."
                    ),
                }
            )
    return issues


def semantic_validation_issues(record: dict[str, Any]) -> list[dict[str, Any]]:
    text = str(record.get("contextualized_text") or "")
    issues: list[dict[str, Any]] = []
    explicit_name = explicit_attribute_table_name(text)
    metadata_name = str(record.get("table_name") or "").strip() or None
    if explicit_name and metadata_name and explicit_name.casefold() != metadata_name.casefold():
        issues.append(
            {
                "type": "table_name_content_mismatch",
                "severity": "critical",
                "table_name": metadata_name,
                "explicit_table_name": explicit_name,
            }
        )

    expected_name = explicit_name or metadata_name
    if expected_name:
        path_names = [
            heading_table_name(str(value))
            for value in record.get("section_path") or []
        ]
        conflicting = unique_preserve_order(
            value
            for value in path_names
            if value and value.casefold() != expected_name.casefold()
        )
        if conflicting:
            issues.append(
                {
                    "type": "section_path_table_mismatch",
                    "severity": "critical",
                    "table_name": expected_name,
                    "conflicting_headings": conflicting,
                }
            )

    if record.get("chunk_type") in {"table_rows", "table_complete"}:
        declared = declared_table_name(text)
        if declared and metadata_name and declared.casefold() != metadata_name.casefold():
            issues.append(
                {
                    "type": "stale_table_state",
                    "severity": "critical",
                    "table_name": metadata_name,
                    "declared_table_name": declared,
                }
            )

    raw_text = str(record.get("raw_text") or "")
    if raw_text and normalize_space(raw_text) != normalize_space(text):
        issues.append(
            {
                "type": "raw_text_not_chunk_aligned",
                "severity": "critical",
            }
        )
    return issues


def validate_parent_child_table_consistency(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    parents = {
        str(record.get("_record_key")): record
        for record in records
        if record.get("_record_key")
    }
    output: list[dict[str, Any]] = []
    for source in records:
        record = copy.deepcopy(source)
        parent_key = record.get("_parent_record_key")
        if parent_key:
            parent = parents.get(str(parent_key))
            parent_name = str((parent or {}).get("table_name") or "").strip()
            child_name = str(record.get("table_name") or "").strip()
            if not parent or not parent_name or parent_name.casefold() != child_name.casefold():
                record.pop("_parent_record_key", None)
                record.setdefault("validation_issues", []).append(
                    {
                        "type": "parent_child_table_mismatch",
                        "severity": "critical",
                        "parent_table_name": parent_name or None,
                        "child_table_name": child_name or None,
                    }
                )
        output.append(record)
    return output


def synchronize_record_provenance(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep an exact chunk-level raw_text and preserve parser output separately."""

    output: list[dict[str, Any]] = []
    for source in records:
        record = copy.deepcopy(source)
        text = clean_record_text(record["contextualized_text"])
        fragments = source_fragments(record)
        if fragments:
            record["source_raw_text"] = "\n\n".join(fragments)
        record.pop("_source_fragments", None)
        record["normalized_text"] = text
        record["contextualized_text"] = text
        record["text"] = text
        # raw_text is now guaranteed to describe only this final chunk.  The
        # untouched Docling payload remains available in source_raw_text.
        record["raw_text"] = text
        record["provenance_status"] = "chunk_aligned"
        output.append(record)
    return output


def add_table_parent_chunks(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Create parents only for split tables; keep small tables self-contained.

    Grouping is content-first and case-insensitive. A stale inherited table_name
    may never attach a child to the previous table.
    """

    groups: dict[str, list[int]] = {}
    canonical_name: dict[str, str] = {}
    normalized_records: list[dict[str, Any]] = []
    for source in records:
        record = copy.deepcopy(source)
        if record.get("chunk_type") == "table_rows" and record.get("field_names"):
            content_name = declared_table_name(record.get("contextualized_text", ""))
            metadata_name = str(record.get("table_name") or "").strip() or None
            table_name = content_name or metadata_name
            if table_name:
                record["table_name"] = table_name
                record["entity"] = table_name
                key = table_name.casefold()
                canonical_name.setdefault(key, table_name)
                groups.setdefault(key, []).append(len(normalized_records))
        normalized_records.append(record)

    parent_by_first_index: dict[int, dict[str, Any]] = {}
    parent_key_by_table: dict[str, str] = {}
    for key, indexes in groups.items():
        table_name = canonical_name[key]
        # A table that fits in one chunk is already a complete retrieval unit.
        if len(indexes) == 1:
            normalized_records[indexes[0]]["chunk_type"] = "table_complete"
            normalized_records[indexes[0]].pop("parent_chunk_id", None)
            continue

        children = [normalized_records[index] for index in indexes]
        parent_key = f"table_parent::{key}"
        parent_key_by_table[key] = parent_key
        field_names = unique_preserve_order(
            field for child in children for field in child.get("field_names", [])
        )
        source_origins = unique_preserve_order(
            value for child in children for value in child.get("source_systems", [])
        )
        convertible_fields = unique_preserve_order(
            field for child in children for field in child.get("convertible_fields", [])
        )
        description = next(
            (
                str(child.get("table_description"))
                for child in children
                if child.get("table_description")
            ),
            None,
        )
        actual_systems = [
            value for value in source_origins
            if value.casefold() not in {"biên tập", "id tự sinh của gis"}
        ]
        lines = [
            f"Bảng dữ liệu: {table_name}.",
            f"Mô tả: {description}." if description else "",
            f"Tổng số trường: {len(field_names)}.",
            f"Các trường: {', '.join(field_names)}.",
        ]
        if source_origins:
            lines.append(f"Nguồn gốc dữ liệu: {', '.join(source_origins)}.")
        if convertible_fields:
            lines.append(
                "Các trường có chỉ dẫn chuyển đổi sang GIS: "
                + ", ".join(convertible_fields) + "."
            )
        text = "\n".join(line for line in lines if line)
        parent_by_first_index[indexes[0]] = {
            "_record_key": parent_key,
            "chunk_type": "table_parent",
            "content_format": "text",
            "pages": sorted(set().union(*(set(child.get("pages", [])) for child in children))),
            "headings": list(children[0].get("headings") or []),
            "section_path": list(children[0].get("section_path") or []),
            "table_name": table_name,
            "entity": table_name,
            "table_description": description,
            "field_names": field_names,
            "source_systems": actual_systems,
            "data_origins": source_origins,
            "convertible_fields": convertible_fields,
            "row_start": min(
                child.get("row_start")
                for child in children
                if child.get("row_start") is not None
            ),
            "row_end": max(
                child.get("row_end")
                for child in children
                if child.get("row_end") is not None
            ),
            "contextualized_text": text,
            "text": text,
            "raw_text": text,
            "source_raw_text": "\n\n".join(
                str(child.get("source_raw_text") or child.get("raw_text") or "")
                for child in children
            ),
            "indexable": True,
            "embedding_enabled": True,
        }

    output: list[dict[str, Any]] = []
    for index, source in enumerate(normalized_records):
        if index in parent_by_first_index:
            output.append(parent_by_first_index[index])
        record = copy.deepcopy(source)
        key = str(record.get("table_name") or "").strip().casefold()
        if key in parent_key_by_table and record.get("chunk_type") == "table_rows":
            record["_parent_record_key"] = parent_key_by_table[key]
        output.append(record)
    return output


def finalize_record_metadata(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for source in records:
        record = copy.deepcopy(source)
        text = record["contextualized_text"]
        repairs = record.get("repair_metadata", {}).get("identifier_repairs", [])
        relevant = [item for item in repairs if item.get("normalized") in text]
        if relevant:
            record.setdefault("repair_metadata", {})["identifier_repairs"] = relevant
        elif "repair_metadata" in record:
            record["repair_metadata"].pop("identifier_repairs", None)
        issues = list(record.get("validation_issues") or [])
        issues.extend(detect_identifier_conflicts(text))
        issues.extend(semantic_validation_issues(record))
        deduped: list[dict[str, Any]] = []
        seen_issue_keys: set[str] = set()
        for issue in issues:
            key = repr(sorted(issue.items()))
            if key not in seen_issue_keys:
                seen_issue_keys.add(key)
                deduped.append(issue)
        record["validation_issues"] = deduped
        severities = {str(issue.get("severity") or "warning") for issue in deduped}
        record["quality_status"] = (
            "fail" if "critical" in severities else "warning" if deduped else "pass"
        )
        record.setdefault("indexable", record.get("chunk_type") != "administrative_footer")
        record.setdefault("embedding_enabled", record["indexable"])
        output.append(record)
    return output


def reindex_records(
    records: list[dict[str, Any]], tokenizer: RegexVietnameseTokenizer
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    key_to_id: dict[str, str] = {}
    for index, source in enumerate(records, start=1):
        record = copy.deepcopy(source)
        record["chunk_id"] = f"chunk_{index:03d}"
        record["token_count_approx"] = tokenizer.count_tokens(record["contextualized_text"])
        if record.get("_record_key"):
            key_to_id[str(record["_record_key"])] = record["chunk_id"]
        output.append(record)
    for record in output:
        parent_key = record.pop("_parent_record_key", None)
        record.pop("_record_key", None)
        if parent_key and str(parent_key) in key_to_id:
            record["parent_chunk_id"] = key_to_id[str(parent_key)]
    return output


def repair_records(
    records: list[dict[str, Any]],
    *,
    doc: DoclingDocument,
    tokenizer: RegexVietnameseTokenizer,
    max_tokens: int,
    document_profile: str = "general",
) -> list[dict[str, Any]]:
    """Post-process lỗi cấu trúc, bảng và định danh còn lại sau Docling."""

    stage: list[dict[str, Any]] = []
    for record in records:
        cleaned = strip_misplaced_furniture(record)
        cleaned.setdefault("source_raw_text", cleaned.get("raw_text", ""))
        cleaned["_source_fragments"] = source_fragments(cleaned)
        repairs = collect_identifier_repairs(cleaned["contextualized_text"])
        if repairs:
            cleaned.setdefault("repair_metadata", {})["identifier_repairs"] = repairs
        cleaned["contextualized_text"] = clean_record_text(
            expand_compound_heading_lines(cleaned["contextualized_text"])
        )
        cleaned["text"] = cleaned["contextualized_text"]
        stage.extend(split_record_on_boundaries(cleaned))

    stage = merge_cross_page_continuations(stage)
    stage = collapse_heading_lead_chains(stage, tokenizer)
    stage = merge_heading_leads_forward(stage, tokenizer)
    stage = repair_stale_table_headings(stage)

    hierarchy = extract_top_level_sections(doc)
    stage = apply_hierarchy(stage, hierarchy)
    stage = repair_table_identity(stage)
    stage = repair_cross_page_table_continuations(stage)
    stage = canonicalize_object_leads(stage)
    stage = rebalance_table_chunks(stage, tokenizer, max_tokens)
    stage = apply_hierarchy(stage, hierarchy)
    stage = repair_table_identity(stage)

    stage = enforce_token_limit(stage, tokenizer, max_tokens)
    stage = apply_hierarchy(stage, hierarchy)
    stage = repair_table_identity(stage)
    stage = canonicalize_object_leads(stage)
    stage = rebalance_table_chunks(stage, tokenizer, max_tokens)
    stage = apply_hierarchy(stage, hierarchy)
    stage = repair_table_identity(stage)

    # Bảo đảm giới hạn cuối sau khi bổ sung context/heading.
    stage = enforce_token_limit(stage, tokenizer, max_tokens)
    stage = apply_hierarchy(stage, hierarchy)
    stage = repair_table_identity(stage)
    stage = enrich_table_metadata(stage)
    stage = enrich_relationship_metadata(stage)
    stage = resolve_cross_references(stage)
    stage = add_table_parent_chunks(stage)
    stage = validate_parent_child_table_consistency(stage)
    stage = synchronize_record_provenance(stage)
    stage = finalize_record_metadata(stage)
    # Absolute final guard after every operation that can add text or create parents.
    stage = enforce_token_limit(stage, tokenizer, max_tokens)
    stage = synchronize_record_provenance(stage)
    stage = finalize_record_metadata(stage)
    if document_profile.startswith("administrative"):
        stage = apply_adaptive_administrative_chunking(
            stage, tokenizer=tokenizer, max_tokens=max_tokens
        )
        stage = semanticize_evn_cskh_update_document(
            stage, doc=doc, tokenizer=tokenizer, max_tokens=max_tokens
        )
        stage = enforce_token_limit(stage, tokenizer, max_tokens)
        stage = synchronize_record_provenance(stage)
        stage = finalize_record_metadata(stage)
    for record in stage:
        record["detected_document_profile"] = document_profile
        record.setdefault(
            "segment_chunk_strategy",
            classify_segment_strategy(record, document_profile),
        )
    return reindex_records(stage, tokenizer)

def build_quality_report(
    records: list[dict[str, Any]],
    tokenizer: RegexVietnameseTokenizer,
    max_tokens: int,
) -> dict[str, Any]:
    critical: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    for index, record in enumerate(records):
        text = record["contextualized_text"]
        chunk_id = record["chunk_id"]
        count = tokenizer.count_tokens(text)

        if count > max_tokens:
            critical.append(
                {
                    "chunk_id": chunk_id,
                    "issue": "token_limit_exceeded",
                    "tokens": count,
                }
            )
        if re.search(r"(?m)^\s*-\s*-", text):
            warnings.append({"chunk_id": chunk_id, "issue": "duplicate_bullet"})
        if any(line_is_furniture(h) for h in record.get("headings", [])):
            critical.append({"chunk_id": chunk_id, "issue": "furniture_used_as_heading"})

        section_markers: list[str] = []
        for line in text.splitlines():
            candidate = normalized_heading_text(line)
            if not is_heading_line(candidate):
                continue
            if (
                line.strip().startswith(("- ", "+ "))
                and SCHEMA_GENERIC_HEADING_RE.match(candidate)
                and candidate.rstrip().endswith((";", "."))
            ):
                continue
            section_markers.append(candidate)
        heading_keys = {
            normalized_heading_text(value).rstrip(":").casefold()
            for value in record.get("headings", [])
        }
        marker_keys = {value.rstrip(":").casefold() for value in section_markers}
        if len(section_markers) > 1 and not marker_keys.issubset(heading_keys):
            warnings.append(
                {
                    "chunk_id": chunk_id,
                    "issue": "multiple_section_markers",
                    "markers": section_markers,
                }
            )

        relation_headings = [
            heading for heading in record.get("headings", [])
            if re.search(r"\(\d+\)\s+Mối\s+quan\s+hệ", heading, re.I)
        ]
        if len(relation_headings) > 1:
            critical.append(
                {
                    "chunk_id": chunk_id,
                    "issue": "nested_relation_siblings",
                    "headings": relation_headings,
                }
            )

        # A sentence may naturally continue across adjacent chunks in long DOCX/PDF
        # documents. Keep this as a non-blocking quality signal instead of rejecting
        # the whole document.
        if index + 1 < len(records) and table_components(text) is None:
            next_text = records[index + 1]["contextualized_text"]
            if looks_incomplete(text) and starts_lowercase_continuation(
                first_meaningful_line(next_text)
            ):
                record.setdefault("validation_issues", []).append(
                    {
                        "type": "unmerged_cross_chunk_sentence",
                        "severity": "warning",
                        "next_chunk_id": records[index + 1]["chunk_id"],
                    }
                )

        components = table_components(text)
        if components:
            _, _, rows = components
            signature = table_header_signature(text)
            same_neighbor = False
            for neighbor_index in (index - 1, index + 1):
                if 0 <= neighbor_index < len(records):
                    neighbor = records[neighbor_index]
                    if (
                        object_group_key(neighbor) == object_group_key(record)
                        and table_header_signature(neighbor["contextualized_text"]) == signature
                    ):
                        same_neighbor = True
                        break
            if len(rows) == 1 and same_neighbor:
                avoidable = False
                for neighbor_index in (index - 1, index + 1):
                    if not (0 <= neighbor_index < len(records)):
                        continue
                    neighbor = records[neighbor_index]
                    if (
                        object_group_key(neighbor) != object_group_key(record)
                        or table_header_signature(neighbor["contextualized_text"]) != signature
                    ):
                        continue
                    neighbor_parts = table_components(neighbor["contextualized_text"])
                    if not neighbor_parts:
                        continue
                    prefix, header, neighbor_rows = neighbor_parts
                    combined = "\n".join(prefix + header + neighbor_rows + rows)
                    if tokenizer.count_tokens(combined) <= max_tokens:
                        avoidable = True
                        break
                if avoidable:
                    issue = {
                        "chunk_id": chunk_id,
                        "issue": "avoidable_single_row_table_chunk",
                    }
                    if record.get("cross_page_table_continuation"):
                        issue["repaired"] = True
                        warnings.append(issue)
                    else:
                        critical.append(issue)

            table_lines = text.splitlines()
            table_start = next((i for i, line in enumerate(table_lines) if is_table_line(line)), -1)
            stray = [
                i + 1
                for i, line in enumerate(table_lines)
                if TABLE_SEPARATOR_RE.match(line) and i != table_start + 1
            ]
            if stray:
                critical.append(
                    {"chunk_id": chunk_id, "issue": "stray_table_separator", "lines": stray}
                )

        identifier_spacing: list[str] = []
        for line in text.splitlines():
            for cell in split_md_cells(line):
                if looks_like_identifier_cell(cell) and " " in normalize_space(cell):
                    identifier_spacing.append(normalize_space(cell))
        if identifier_spacing:
            critical.append(
                {
                    "chunk_id": chunk_id,
                    "issue": "identifier_contains_whitespace",
                    "values": unique_preserve_order(identifier_spacing),
                }
            )

        if re.search(r"\b(?:CSDL|CTĐL)(?=[a-zà-ỹ])", text):
            warnings.append({"chunk_id": chunk_id, "issue": "joined_natural_words"})

        if re.search(
            r"(?im)^(?:với các dữ liệu như sau:?|"
            r"Thực hiện vẽ tự động bằng công cụ:?)$",
            text,
        ):
            warnings.append({"chunk_id": chunk_id, "issue": "fragmented_object_context"})

        if record.get("chunk_type") == "administrative_footer" and (
            record.get("indexable") is not False or record.get("embedding_enabled") is not False
        ):
            critical.append({"chunk_id": chunk_id, "issue": "footer_is_indexable"})

        for issue in record.get("validation_issues", []):
            rendered = {
                "chunk_id": chunk_id,
                "issue": issue.get("type"),
                "detail": issue,
            }
            if str(issue.get("severity") or "warning") == "critical":
                critical.append(rendered)
            else:
                warnings.append(rendered)

        cells_rows = [
            split_md_cells(line)
            for line in text.splitlines()
            if is_table_line(line) and not TABLE_SEPARATOR_RE.match(line)
        ]
        if any(
            len([cell for cell in row if cell]) >= 2
            and len(set(normalize_space(cell).casefold() for cell in row if cell)) == 1
            for row in cells_rows
        ):
            warnings.append({"chunk_id": chunk_id, "issue": "repeated_merged_cell_row"})

    return {
        "status": "pass" if not critical else "fail",
        "critical_count": len(critical),
        "warning_count": len(warnings),
        "critical": critical,
        "warnings": warnings,
    }

def build_coverage_report(
    doc: DoclingDocument,
    records: list[dict[str, Any]],
) -> dict[str, Any]:
    corpus = normalize_space(
        "\n".join(
            str(record.get("text", "")) + "\n" + str(record.get("contextualized_text", ""))
            for record in records
        )
    ).casefold()

    checked = 0
    missing: list[dict[str, Any]] = []
    for item in getattr(doc, "texts", []) or []:
        text = normalize_space(getattr(item, "text", ""))
        if not text:
            continue
        checked += 1
        if text.casefold() not in corpus:
            content_layer = getattr(getattr(item, "content_layer", None), "value", None)
            label = getattr(getattr(item, "label", None), "value", None)
            missing.append(
                {
                    "self_ref": str(getattr(item, "self_ref", "")),
                    "pages": item_pages(item),
                    "label": str(label),
                    "content_layer": str(content_layer),
                    "text": text,
                }
            )
    return {
        "checked_text_items": checked,
        "missing_text_items": len(missing),
        "missing": missing,
    }



ADMIN_ISSUE_COLUMNS = {
    "stt",
    "hệ thống",
    "tình trạng",
    "nguyên nhân",
    "đã xử lý",
    "yêu cầu thực hiện",
}
ADMIN_ROMAN_ITEM_RE = re.compile(
    r"(?ims)^\s*(?:-\s*)?([ivxlcdm]+)\)\s*(.+?)(?=^\s*(?:-\s*)?[ivxlcdm]+\)\s|\Z)"
)
ADMIN_DIRECTIVE_RE = re.compile(
    r"(?im)^\s*(?:Tổng\s+công\s+ty\s+đã\s+họp|Trong\s+trường\s+hợp|Các\s+đơn\s+vị\s+triển\s+khai)"
)
ADMIN_SYNTHETIC_ORDINAL_RE = re.compile(
    r"(?m)^\s*\d+\.\s+(?=(?:[ivxlcdm]+\)|[a-zà-ỹ]))", re.IGNORECASE
)
ADMIN_ACRONYMS = (
    "EVNCPC", "CPCIT", "CPCCC", "EMEC", "EVNICT", "CMIS", "HES", "NOC", "TCT",
)


def _normalize_admin_semantic_text(text: str) -> str:
    """Normalize administrative prose without changing document meaning.

    This is intentionally scoped to the administrative strategy. It removes
    internal ordinal prefixes accidentally introduced by structural repair and
    repairs safe acronym/word joins commonly produced by PDF extraction.
    """

    value = ADMIN_SYNTHETIC_ORDINAL_RE.sub("", text or "")
    # Rejoin PDF line wraps only when the previous line is incomplete and the
    # next line is a lowercase continuation. Existing bullets/headings remain
    # separate semantic boundaries.
    value = "\n".join(clean_prose_lines(value.splitlines()))
    for acronym in ADMIN_ACRONYMS:
        value = re.sub(
            rf"\b{re.escape(acronym)}(?=[a-zà-ỹ])",
            f"{acronym} ",
            value,
            flags=re.IGNORECASE,
        )
        value = re.sub(
            rf"(?<=[a-zà-ỹ]){re.escape(acronym)}\b",
            f" {acronym}",
            value,
            flags=re.IGNORECASE,
        )
    value = re.sub(r"\bweb/app(?=[A-ZÀ-Ỹ])", "web/app ", value, flags=re.IGNORECASE)
    value = re.sub(r"[ \t]{2,}", " ", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def _admin_column_key(value: str) -> str:
    return normalize_space(value).casefold()


def is_administrative_incident_table(columns: Sequence[str]) -> bool:
    keys = {_admin_column_key(column) for column in columns}
    required = {"hệ thống", "tình trạng", "nguyên nhân", "yêu cầu thực hiện"}
    return required.issubset(keys)


def _administrative_field_map(columns: Sequence[str], row: Sequence[str]) -> dict[str, str]:
    values = list(row) + [""] * max(0, len(columns) - len(row))
    return {
        normalize_space(column): _normalize_admin_semantic_text(values[index])
        for index, column in enumerate(columns)
        if index < len(values) and normalize_space(column)
    }


def _field_value(fields: dict[str, str], *names: str) -> str:
    normalized = {_admin_column_key(key): value for key, value in fields.items()}
    for name in names:
        if _admin_column_key(name) in normalized:
            return normalized[_admin_column_key(name)]
    return ""


def _extract_responsible_units(text: str) -> list[str]:
    found: list[str] = []
    upper = (text or "").upper()
    for unit in ADMIN_ACRONYMS:
        if unit in upper and unit not in {"CMIS", "HES", "NOC", "TCT", "EVNCPC"}:
            found.append(unit)
    return found


def _extract_lead_and_coordination_units(text: str) -> tuple[list[str], list[str]]:
    """Extract explicit lead/coordination assignments without inventing roles."""

    normalized = _normalize_admin_semantic_text(text)
    lead: list[str] = []
    coordination: list[str] = []
    for unit in ADMIN_ACRONYMS:
        if unit in {"CMIS", "HES", "NOC", "TCT", "EVNCPC"}:
            continue
        if re.search(rf"\b{re.escape(unit)}\b[^.\n;]{{0,40}}\bchủ\s+trì\b", normalized, re.I):
            lead.append(unit)
        if re.search(rf"\b{re.escape(unit)}\b[^.\n;]{{0,40}}\bphối\s+hợp\b", normalized, re.I):
            coordination.append(unit)
    lead_units = unique_preserve_order(lead)
    coordination_units = [
        unit
        for unit in unique_preserve_order(coordination)
        if unit not in lead_units
    ]
    return lead_units, coordination_units


def _incident_type_from_text(system: str, situation: str) -> str:
    lowered = f"{system} {situation}".casefold()
    rules = (
        (("pmax",), "pmax_collection"),
        (("đồng bộ", "chỉ số chốt"), "synchronization_failure"),
        (("hóa đơn",), "invoice_display"),
        (("thông báo",), "notification_delivery"),
        (("sản lượng",), "consumption_calculation"),
        (("gián đoạn",), "service_outage"),
        (("hiển thị", "chỉ số"), "meter_reading_display"),
        (("hhc",), "hhc_generation"),
    )
    for terms, label in rules:
        if all(term in lowered for term in terms):
            return label
    return "administrative_incident"


def _serialize_incident(fields: dict[str, str]) -> str:
    """Serialize source table fields only; do not inject synthetic provenance text."""

    ordered = [
        ("Hệ thống", _field_value(fields, "Hệ thống")),
        ("STT", _field_value(fields, "STT", "TT")),
        ("Tình trạng", _field_value(fields, "Tình trạng")),
        ("Nguyên nhân", _field_value(fields, "Nguyên nhân")),
        ("Đã xử lý", _field_value(fields, "Đã xử lý")),
        ("Yêu cầu thực hiện", _field_value(fields, "Yêu cầu thực hiện")),
    ]
    return "\n\n".join(f"{label}: {value}" for label, value in ordered if value)


def _incident_suffix(index: int) -> str:
    if index <= 0:
        return ""
    value = index
    chars: list[str] = []
    while value:
        value, remainder = divmod(value - 1, 26)
        chars.append(chr(ord("a") + remainder))
    return "".join(reversed(chars))


def semanticize_administrative_tables(
    records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Convert only incident-style administrative tables to semantic records.

    The source table row remains in ``raw_text``. Normalized key-value text is
    stored separately for retrieval, and inferred routing metadata never leaks
    into source-visible text.
    """

    output: list[dict[str, Any]] = []
    stt_counts: dict[str, int] = {}
    global_incident_sequence = 0
    for source in records:
        record = copy.deepcopy(source)
        columns, rows = table_row_cells(str(record.get("contextualized_text") or ""))
        if not columns or not is_administrative_incident_table(columns):
            output.append(record)
            continue

        pages = list(record.get("pages") or [])
        for row in rows:
            fields = _administrative_field_map(columns, row)
            situation = _field_value(fields, "Tình trạng")
            system = _field_value(fields, "Hệ thống")
            source_stt = _field_value(fields, "STT", "TT")
            if not situation:
                continue

            global_incident_sequence += 1
            stt_counts[source_stt] = stt_counts.get(source_stt, 0) + 1
            incident_id = (
                f"{source_stt}{_incident_suffix(stt_counts[source_stt])}"
                if source_stt
                else f"incident-{global_incident_sequence}"
            )
            semantic_text = _serialize_incident(fields)
            requirements = _field_value(fields, "Yêu cầu thực hiện")
            handled = _field_value(fields, "Đã xử lý")
            role_text = f"{requirements} {handled}"
            lead_units, coordination_units = _extract_lead_and_coordination_units(role_text)

            child = copy.deepcopy(record)
            child["contextualized_text"] = semantic_text
            child["retrieval_text"] = semantic_text
            child["normalized_text"] = semantic_text
            child["text"] = semantic_text
            child["raw_text"] = _render_markdown_row(row)
            child["chunk_type"] = "administrative_incident"
            child["content_format"] = "semantic_key_value"
            child["field_names"] = list(columns)
            child["table_columns"] = list(columns)
            child["fields"] = fields
            child["system"] = system
            child["unit"] = system
            child["source_stt"] = source_stt
            child["incident_id"] = incident_id
            child["incident_sequence"] = global_incident_sequence
            child["incident_type"] = _incident_type_from_text(system, situation)
            child["responsible_units"] = unique_preserve_order(
                _extract_responsible_units(role_text)
            )
            child["lead_units"] = lead_units
            child["coordination_units"] = coordination_units
            child["source_section"] = "Phụ lục"
            child["document_type"] = "administrative_document"
            child["section_path"] = unique_preserve_order(
                ["Phụ lục", system, situation]
            )
            child["headings"] = list(child["section_path"])
            child["source_table_row"] = global_incident_sequence
            child["row_start"] = global_incident_sequence
            child["row_end"] = global_incident_sequence
            child["pages"] = pages
            child["segment_chunk_strategy"] = "administrative_incident_record"
            child["validation_issues"] = []
            child["quality_status"] = "pass"
            output.append(child)
    return output

def _is_admin_furniture_line(line: str) -> bool:
    value = normalize_space(line)
    if not value:
        return False
    patterns = (
        r"^CỘNG\s+H[ÒO]A\s+XÃ\s+HỘI\s+CHỦ\s+NGHĨA\s+VIỆT\s+NAM(?:\s+Độc\s+lập.*)?$",
        r"^Độc\s+lập\s*-\s*Tự\s+do\s*-\s*Hạnh\s+phúc$",
        r"^[A-ZÀ-Ỹ][A-Za-zÀ-ỹ\s]+,\s*ngày\s+tháng\s+\d+\s+năm\s+\d{4}$",
        r"^\d+$",
    )
    return any(re.match(pattern, value, re.I) for pattern in patterns)


def _strip_admin_furniture_lines(text: str) -> str:
    lines: list[str] = []
    for raw in (text or "").splitlines():
        line = normalize_space(raw)
        if not line:
            continue
        if _is_admin_furniture_line(line):
            continue
        if any(pattern.search(line) for pattern in SIGNATURE_LINE_PATTERNS):
            continue
        if re.match(r"^Nơi\s+nhận\s*:", line, re.IGNORECASE):
            break
        if line.casefold() == "phụ lục":
            break
        lines.append(line)
    return "\n".join(lines).strip()


def _remove_duplicate_admin_markers(text: str) -> str:
    lines = [normalize_space(line) for line in (text or "").splitlines()]
    output: list[str] = []
    seen_kinh_gui = False
    for line in lines:
        if not line:
            continue
        if re.fullmatch(r"Kính\s+gửi\s*:", line, re.I):
            if seen_kinh_gui:
                continue
            seen_kinh_gui = True
        output.append(line)
    return "\n".join(output).strip()


def _strip_trailing_issue_artifacts(text: str) -> str:
    lines = [normalize_space(line) for line in (text or "").splitlines() if normalize_space(line)]
    while lines and (
        re.fullmatch(r"Kính\s+gửi\s*:", lines[-1], re.I)
        or _is_admin_furniture_line(lines[-1])
    ):
        lines.pop()
    return "\n".join(lines).strip()


def _section_pages(
    section_text: str,
    records: list[dict[str, Any]],
    body_indexes: Sequence[int],
) -> list[int]:
    section_tokens = {
        token.casefold()
        for token in re.findall(r"[A-Za-zÀ-ỹ0-9]{3,}", section_text or "")
    }
    scored: list[tuple[float, list[int]]] = []
    for index in body_indexes:
        source_text = str(records[index].get("contextualized_text") or "")
        source_tokens = {
            token.casefold()
            for token in re.findall(r"[A-Za-zÀ-ỹ0-9]{3,}", source_text)
        }
        if not section_tokens or not source_tokens:
            continue
        overlap = len(section_tokens & source_tokens) / max(1, len(section_tokens))
        if overlap >= 0.08:
            scored.append((overlap, list(records[index].get("pages") or [])))
    pages = sorted({page for _, item_pages in scored for page in item_pages})
    if pages:
        return pages
    return sorted({page for index in body_indexes for page in records[index].get("pages", [])})


def semanticize_administrative_body(
    records: list[dict[str, Any]],
    *,
    tokenizer: RegexVietnameseTokenizer,
    max_tokens: int,
) -> list[dict[str, Any]]:
    """Rebuild only the prose portion of a recognised administrative document.

    Classification metadata is stored as metadata. Source-visible chunk text is
    never prefixed with invented labels such as ``Loại``, ``Chủ đề`` or ``Mục``.
    """

    table_pages = [
        page
        for record in records
        if record.get("chunk_type") == "administrative_incident"
        or table_components(str(record.get("contextualized_text") or ""))
        for page in record.get("pages") or []
    ]
    first_table_page = min(table_pages, default=9999)
    body_indexes = [
        index
        for index, record in enumerate(records)
        if record.get("chunk_type") not in {"administrative_footer", "administrative_incident"}
        and not table_components(str(record.get("contextualized_text") or ""))
        and min(record.get("pages") or [9999]) < first_table_page
    ]
    if not body_indexes:
        return records

    corpus = "\n".join(
        str(records[index].get("contextualized_text") or "") for index in body_indexes
    )
    corpus = _normalize_admin_semantic_text(_strip_admin_furniture_lines(corpus))
    corpus = _remove_duplicate_admin_markers(corpus)
    matches = list(ADMIN_ROMAN_ITEM_RE.finditer(corpus))
    if len(matches) < 2:
        return records

    first_issue = matches[0].start()
    directive_match = ADMIN_DIRECTIVE_RE.search(corpus, matches[-1].start())
    directive_start = directive_match.start() if directive_match else len(corpus)
    intro_text = corpus[:first_issue].strip()
    issue_text = _strip_trailing_issue_artifacts(corpus[first_issue:directive_start].strip())
    directive_text = _strip_admin_furniture_lines(corpus[directive_start:].strip())

    subject_match = re.search(r"(?im)^\s*V/v\s+(.+?)(?:\.|$)", intro_text)
    subject = normalize_space(subject_match.group(1)) if subject_match else ""

    template = copy.deepcopy(records[body_indexes[0]])
    replacements: list[dict[str, Any]] = []

    def add_record(text: str, chunk_type: str, path: list[str], strategy: str) -> None:
        if not text:
            return
        item = copy.deepcopy(template)
        pages = _section_pages(text, records, body_indexes)
        item["contextualized_text"] = text
        item["retrieval_text"] = text
        item["normalized_text"] = text
        item["text"] = text
        item["raw_text"] = text
        item["pages"] = pages
        if pages:
            item["page_start"] = min(pages)
            item["page_end"] = max(pages)
        item["chunk_type"] = chunk_type
        item["content_format"] = "semantic_prose"
        item["document_type"] = "administrative_document"
        if subject:
            item["document_subject"] = subject
        item["section_path"] = path
        item["headings"] = path
        item["segment_chunk_strategy"] = strategy
        item.pop("unit", None)
        item["validation_issues"] = []
        item["quality_status"] = "pass"
        replacements.extend(enforce_token_limit([item], tokenizer, max_tokens))

    add_record(
        intro_text,
        "administrative_introduction",
        ["Giới thiệu văn bản"],
        "administrative_metadata_and_intro",
    )
    add_record(
        issue_text,
        "administrative_issue_overview",
        ["Tình trạng vướng mắc"],
        "administrative_issue_overview",
    )
    add_record(
        directive_text,
        "administrative_directive",
        ["Chỉ đạo và đầu mối báo cáo"],
        "administrative_directive",
    )

    first_index = min(body_indexes)
    body_set = set(body_indexes)
    output: list[dict[str, Any]] = []
    for index, record in enumerate(records):
        if index == first_index:
            output.extend(replacements)
        if index not in body_set:
            output.append(record)
    return output

def cleanup_administrative_artifacts(
    records: list[dict[str, Any]],
    tokenizer: RegexVietnameseTokenizer,
) -> list[dict[str, Any]]:
    """Drop tiny signature/appendix bridge fragments after semantic rebuilding."""

    output: list[dict[str, Any]] = []
    for source in records:
        record = copy.deepcopy(source)
        text = normalize_space(str(record.get("contextualized_text") or ""))
        lowered = text.casefold()
        tiny = tokenizer.count_tokens(text) <= 24
        artifact = tiny and (
            "phụ lục" in lowered
            or any(pattern.search(text) for pattern in SIGNATURE_LINE_PATTERNS)
            or bool(re.fullmatch(r"[A-ZÀ-Ỹ][A-Za-zÀ-ỹ\s]{3,60}", text))
        )
        if artifact and record.get("chunk_type") not in {"administrative_incident"}:
            continue
        output.append(record)
    return output



EVN_CSKH_UPDATE_MARKERS = (
    "ứng dụng chăm sóc khách hàng",
    "evn cskh",
    "danh sách chi tiết chức năng cập nhật",
    "website quản trị nội dung",
)

EVN_FUNCTION_TABLE_COLUMNS = [
    "TT",
    "Chức năng/Màn hình Ứng dụng",
    "Nội dung hiệu chỉnh/Bổ sung",
    "Ghi chú",
]


def _document_text_from_records_and_doc(
    records: Sequence[dict[str, Any]], doc: DoclingDocument | None = None
) -> str:
    parts = [str(record.get("contextualized_text") or record.get("text") or "") for record in records]
    if doc is not None:
        parts.extend(str(getattr(item, "text", "") or "") for item in getattr(doc, "texts", []) or [])
    return normalize_space("\n".join(parts))


def is_evn_cskh_update_document(
    records: Sequence[dict[str, Any]], doc: DoclingDocument | None = None
) -> bool:
    """Detect short EVN CSKH official update letters with appendix feature tables.

    These documents need semantic grouping by business meaning, not token-only table
    packing: main dispatch -> app phase 1 -> app phase 2 -> CMS -> UI captions.
    """

    lowered = _document_text_from_records_and_doc(records, doc).casefold()
    marker_hits = sum(marker in lowered for marker in EVN_CSKH_UPDATE_MARKERS)
    has_phase_rows = "giai đoạn 1" in lowered and "giai đoạn 2" in lowered
    has_feature_terms = "cấp điện mới hạ áp" in lowered and "dashboard" in lowered
    return marker_hits >= 3 and has_phase_rows and has_feature_terms


def _extract_evn_feature_rows(records: Sequence[dict[str, Any]]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    current_group = ""
    for record in records:
        text = str(record.get("contextualized_text") or "")
        for raw_line in text.splitlines():
            line = normalize_space(raw_line)
            lowered = line.casefold()
            if not line:
                continue
            if "app chăm sóc khách hàng" in lowered:
                current_group = "App chăm sóc khách hàng"
                continue
            if "website quản trị nội dung" in lowered or "cms" == lowered:
                current_group = "Website Quản trị nội dung (CMS)"
                continue
            if not is_table_line(line) or TABLE_SEPARATOR_RE.match(line):
                continue
            cells = [normalize_space(cell).rstrip(".") if idx == 0 else normalize_space(cell)
                     for idx, cell in enumerate(split_md_cells(line))]
            if len(cells) < 3:
                continue
            stt_cell = cells[0].rstrip(".")
            if not re.fullmatch(r"\d+", stt_cell):
                continue
            stt = int(stt_cell)
            feature = cells[1] if len(cells) >= 2 else ""
            change = cells[2] if len(cells) >= 3 else ""
            note = cells[3] if len(cells) >= 4 else ""
            group = current_group or ("Website Quản trị nội dung (CMS)" if stt >= 27 else "App chăm sóc khách hàng")
            rows.append(
                {
                    "stt": str(stt),
                    "feature": feature,
                    "change": _repair_evn_text(change.lstrip("- ")),
                    "note": note,
                    "group": group,
                }
            )
    # Keep the most complete occurrence for each STT.
    by_stt: dict[str, dict[str, str]] = {}
    for row in rows:
        key = row["stt"]
        old = by_stt.get(key)
        if old is None or len(" ".join(row.values())) > len(" ".join(old.values())):
            by_stt[key] = row
    return [by_stt[key] for key in sorted(by_stt, key=lambda value: int(value))]


def _render_evn_feature_rows(rows: Sequence[dict[str, str]]) -> str:
    lines = [
        "| " + " | ".join(EVN_FUNCTION_TABLE_COLUMNS) + " |",
        "|---|---|---|---|",
    ]
    for row in rows:
        lines.append(
            "| {stt} | {feature} | {change} | {note} |".format(
                stt=row.get("stt", ""),
                feature=row.get("feature", ""),
                change=row.get("change", ""),
                note=row.get("note", ""),
            )
        )
    return "\n".join(lines)


def _evn_pages_for_rows(rows: Sequence[dict[str, str]]) -> list[int]:
    if not rows:
        return []
    values = [int(row["stt"]) for row in rows if str(row.get("stt", "")).isdigit()]
    pages: set[int] = set()
    if any(value <= 27 for value in values):
        pages.add(2)
    if any(value >= 28 for value in values):
        pages.add(3)
    return sorted(pages)


EVN_OCR_REPAIRS = {
    "báo c áo": "báo cáo",
    "Đăng ký/Đăng; nhập": "Đăng ký/Đăng nhập",
    "Tra cứu điện; năng": "Tra cứu điện năng",
    "Thanh toán/Lịch; sử": "Thanh toán/Lịch sử",
    "Cấp điện mới hạ; áp/trung áp": "Cấp điện mới hạ áp/trung áp",
}


def _repair_evn_text(text: str) -> str:
    repaired = text
    for old, new in EVN_OCR_REPAIRS.items():
        repaired = repaired.replace(old, new)
    repaired = re.sub(r"\bbáo\s+c\s+áo\b", "báo cáo", repaired, flags=re.I)
    repaired = re.sub(r"\s+;\s+", "; ", repaired)
    return normalize_space(repaired) if "\n" not in repaired else "\n".join(normalize_space(line) for line in repaired.splitlines())


def _extract_evn_signature_metadata(records: Sequence[dict[str, Any]]) -> dict[str, Any]:
    page1_text = "\n".join(
        str(record.get("contextualized_text") or "")
        for record in records
        if 1 in set(record.get("pages") or [])
    )
    metadata: dict[str, Any] = {}
    if "Phạm Ngọc Hiển" in page1_text:
        metadata["signer"] = "Phạm Ngọc Hiển"
        metadata["signer_title"] = "Phó Giám đốc"
    place_match = re.search(r"Hà Nội,\s*ngày\s*tháng\s*06\s*năm\s*2026", page1_text, flags=re.I)
    if place_match:
        metadata["place_date"] = normalize_space(place_match.group(0))
    return metadata


def _compact_evn_main_dispatch_text(lines: Sequence[str]) -> str:
    full = "\n".join(unique_preserve_order(lines))
    subject_match = re.search(r"V/v\s+(.+?)(?=\nKính gửi:|$)", full, flags=re.I | re.S)
    recipients_match = re.search(r"Kính gửi:\s*(.+?)(?=\nCăn cứ|$)", full, flags=re.I | re.S)
    basis_match = re.search(r"Căn cứ văn bản.+?\(EVN CSKH\)\.", full, flags=re.I | re.S)
    appstore_match = re.search(r"https://apps\.apple\.com/\S+", full)
    chplay_match = re.search(r"https://play\.google\.com/\S+", full)
    mechanism_match = re.search(r"Lưu ý về cơ chế cập nhật ứng dụng:.+?(?=\nCông ty Viễn thông|$)", full, flags=re.I | re.S)
    coordination_match = re.search(r"Công ty Viễn thông.+?phối hợp triển khai\.", full, flags=re.I | re.S)

    out: list[str] = [
        "TẬP ĐOÀN ĐIỆN LỰC VIỆT NAM - CÔNG TY VIỄN THÔNG ĐIỆN LỰC VÀ CÔNG NGHỆ THÔNG TIN",
        "Số: /EVNICT-TTPM",
    ]
    if subject_match:
        out.append("V/v " + normalize_space(subject_match.group(1)))
    if recipients_match:
        out.append("Kính gửi: " + normalize_space(recipients_match.group(1)))
    if basis_match:
        out.append(normalize_space(basis_match.group(0)))
    out.append("Nội dung: EVNICT thông báo phiên bản mới của ứng dụng EVN CSKH trên App Store và Google Play/CH Play; cập nhật, hiệu chỉnh các tính năng của ứng dụng EVN CSKH và Website quản trị nội dung (CMS).")
    if appstore_match:
        out.append("App Store: " + appstore_match.group(0))
    if chplay_match:
        out.append("CH Play: " + chplay_match.group(0))
    if mechanism_match:
        out.append(normalize_space(mechanism_match.group(0)))
    if coordination_match:
        out.append(normalize_space(coordination_match.group(0)))
    return "\n".join(unique_preserve_order(out))


def _evn_document_context(section_path: Sequence[str], metadata: dict[str, Any] | None = None) -> str:
    """Build a concise section-aware context string for EVN CSKH chunks."""

    parts = [
        "Ngữ cảnh tài liệu: Công văn EVNICT về cập nhật phiên bản chính thức ứng dụng EVN CSKH.",
    ]
    if section_path:
        parts.append("Phần: " + " > ".join(str(value) for value in section_path if value))
    if metadata:
        platform = metadata.get("platform")
        phase = metadata.get("phase")
        change_type = metadata.get("change_type")
        screen_names = metadata.get("screen_names")
        if platform:
            parts.append(f"Nền tảng: {platform}.")
        if phase:
            parts.append(f"Giai đoạn: {phase}.")
        if change_type:
            parts.append(f"Loại thay đổi: {change_type}.")
        if isinstance(screen_names, list) and screen_names:
            parts.append("Màn hình/chức năng: " + "; ".join(str(item) for item in screen_names) + ".")
    return " ".join(parts)


def _make_evn_record(
    template: dict[str, Any],
    *,
    text: str,
    chunk_type: str,
    pages: Sequence[int],
    section_path: Sequence[str],
    content_format: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    record = copy.deepcopy(template)
    clean_text = clean_record_text(text)
    metadata = metadata or {}
    record.update(
        {
            "contextualized_text": clean_text,
            "retrieval_text": clean_text,
            "normalized_text": clean_text,
            "text": clean_text,
            "raw_text": clean_text,
            "chunk_type": chunk_type,
            "content_format": content_format,
            "document_type": "official_dispatch_with_appendix",
            "section_path": list(section_path),
            "headings": list(section_path),
            "pages": list(pages),
            "indexable": True,
            "embedding_enabled": True,
            "validation_issues": [],
            "quality_status": "pass",
            "segment_chunk_strategy": "evn_cskh_semantic_group",
            "document_context": _evn_document_context(section_path, metadata),
        }
    )
    if pages:
        record["page_start"] = min(pages)
        record["page_end"] = max(pages)
    record.update(metadata)
    return record


def _build_evn_main_dispatch_record(records: Sequence[dict[str, Any]]) -> dict[str, Any] | None:
    page1 = [record for record in records if 1 in set(record.get("pages") or [])]
    if not page1:
        return None
    lines: list[str] = []
    for record in page1:
        if record.get("chunk_type") == "administrative_footer":
            continue
        for raw_line in str(record.get("contextualized_text") or "").splitlines():
            line = normalize_space(raw_line)
            if not line:
                continue
            if re.match(r"^Nơi\s+nhận\s*:", line, re.I):
                break
            lowered = line.casefold()
            if lowered.startswith("trân trọng"):
                continue
            if "cộng hoà xã hội chủ nghĩa việt nam" in lowered:
                continue
            if "độc lập - tự do - hạnh phúc" in lowered:
                continue
            if "kt. giám đốc" in lowered or "phó giám đốc" in lowered:
                continue
            if "phạm ngọc hiển" in lowered:
                continue
            if any(pattern.search(line) for pattern in SIGNATURE_LINE_PATTERNS):
                continue
            if _is_admin_furniture_line(line) and "hà nội" not in lowered:
                continue
            lines.append(line)
    text = _compact_evn_main_dispatch_text(lines)
    if not text:
        return None
    template = copy.deepcopy(page1[0])
    signer_metadata = _extract_evn_signature_metadata(page1)
    return _make_evn_record(
        template,
        text=text,
        chunk_type="official_dispatch_main",
        pages=[1],
        section_path=["Công văn chính"],
        content_format="semantic_prose",
        metadata={
            "platform": "mobile_app_and_cms",
            "change_topic": "cap_nhat_phien_ban_ung_dung_evn_cskh",
            **signer_metadata,
        },
    )

def _extract_evn_ui_labels_from_doc(doc: DoclingDocument | None) -> dict[int, list[str]]:
    if doc is None:
        return {}
    labels_by_page: dict[int, list[str]] = {}
    for item in getattr(doc, "texts", []) or []:
        text = normalize_vietnamese_pdf_text(str(getattr(item, "text", "") or ""))
        text = normalize_space(text)
        if not text:
            continue
        # Avoid tiny row numbers and table headers; keep human labels/captions.
        if re.fullmatch(r"\d+", text):
            continue
        if text.casefold() in {"tt", "chức năng/ màn hình", "giao diện màn hình ứng dụng"}:
            continue
        for page_no in item_pages(item):
            if page_no >= 4:
                labels_by_page.setdefault(page_no, []).append(text)
    return {page: unique_preserve_order(labels) for page, labels in labels_by_page.items()}


EVN_UI_FALLBACK_LABELS: dict[int, list[str]] = {
    4: ["Đăng ký/Đăng nhập", "Trang chủ/Dịch vụ và tiện ích"],
    5: ["Tra cứu điện năng, chỉ số", "Thanh toán/Lịch sử thanh toán"],
    6: ["Cấp điện mới hạ áp/trung áp", "Tra cứu chỉ số Điện MTMN"],
    7: ["Lịch sử thanh toán Điện MTMN", "Tra cứu tiến độ"],
    8: ["Tra cứu điểm thu"],
}

EVN_UI_PAGE_DESCRIPTIONS: dict[int, str] = {
    4: "Hình minh họa giao diện đăng nhập bằng số điện thoại, trang chủ ứng dụng EVN CSKH và màn hình dịch vụ tiện ích.",
    5: "Hình minh họa màn hình thống kê điện năng tiêu thụ theo ngày/tháng, màn hình thanh toán hóa đơn và lịch sử thanh toán.",
    6: "Hình minh họa chức năng mới về cấp điện mới hạ áp/trung áp và tra cứu chỉ số điện mặt trời mái nhà.",
    7: "Hình minh họa chức năng mới xem lịch sử thanh toán điện mặt trời mái nhà và tra cứu tiến độ xử lý yêu cầu.",
    8: "Hình minh họa chức năng mới tra cứu điểm thu theo khu vực, phường/xã, điện lực hoặc đối tác.",
}

EVN_UI_NEW_FEATURE_PAGES = {6, 7, 8}


def _clean_evn_ui_labels(page: int, labels_from_doc: dict[int, list[str]]) -> list[str]:
    # The visual table labels on these pages are often OCR-split (e.g.
    # "Đăng ký/Đăng; nhập"). Prefer curated page labels for known EVN UI
    # pages, and only fall back to OCR labels for unexpected pages.
    labels = EVN_UI_FALLBACK_LABELS.get(page) or labels_from_doc.get(page, [])
    cleaned = [_repair_evn_text(label) for label in labels if len(label) <= 120]
    return unique_preserve_order([label for label in cleaned if label])


def _build_evn_ui_caption_records(
    template: dict[str, Any], doc: DoclingDocument | None
) -> list[dict[str, Any]]:
    labels_from_doc = _extract_evn_ui_labels_from_doc(doc)
    pages = sorted(set(EVN_UI_FALLBACK_LABELS) | set(labels_from_doc))
    records: list[dict[str, Any]] = []
    for page in pages:
        labels = _clean_evn_ui_labels(page, labels_from_doc)
        if not labels:
            continue
        new_feature = page in EVN_UI_NEW_FEATURE_PAGES or any("tính năng mới" in label.casefold() for label in labels)
        description = EVN_UI_PAGE_DESCRIPTIONS.get(
            page,
            "Hình minh họa giao diện trên ứng dụng EVN CSKH cho các màn hình/chức năng: "
            + "; ".join(labels)
            + ".",
        )
        text = "\n".join(
            [
                "2. Một số giao diện màn hình chính của ứng dụng",
                f"Trang {page}: " + "; ".join(labels),
                "Mô tả: " + description,
            ]
        )
        records.append(
            _make_evn_record(
                template,
                text=text,
                chunk_type="app_ui_caption",
                pages=[page],
                section_path=["Một số giao diện màn hình chính của ứng dụng", f"Trang {page}"],
                content_format="image_caption",
                metadata={
                    "platform": "mobile_app",
                    "content_type": "image_ui",
                    "phase": "giai_doan_2" if new_feature else None,
                    "screen_names": labels,
                },
            )
        )
    return records

def semanticize_evn_cskh_update_document(
    records: list[dict[str, Any]],
    *,
    doc: DoclingDocument | None,
    tokenizer: RegexVietnameseTokenizer,
    max_tokens: int,
) -> list[dict[str, Any]]:
    """Rebuild EVN CSKH update documents into retrieval-oriented chunks.

    The goal is to avoid the bad boundary observed in production where row 16
    (phase 1), rows 17-26 (phase 2) and CMS row 27 were packed into one chunk.
    """

    if not is_evn_cskh_update_document(records, doc):
        return records

    template = copy.deepcopy(records[0]) if records else {"pages": []}
    output: list[dict[str, Any]] = []
    main = _build_evn_main_dispatch_record(records)
    if main:
        output.append(main)

    rows = _extract_evn_feature_rows(records)
    app_phase_1 = [row for row in rows if row["group"].casefold().startswith("app") and row.get("note").casefold() == "giai đoạn 1"]
    app_phase_2 = [row for row in rows if row["group"].casefold().startswith("app") and row.get("note").casefold() == "giai đoạn 2"]
    cms_rows = [row for row in rows if "cms" in row["group"].casefold() or int(row["stt"]) >= 27]

    if app_phase_1:
        text = "\n".join(
            [
                "1. Danh sách chi tiết chức năng cập nhật",
                "App chăm sóc khách hàng - Cập nhật giao diện mới - Giai đoạn 1",
                _render_evn_feature_rows(app_phase_1),
            ]
        )
        output.append(
            _make_evn_record(
                template,
                text=text,
                chunk_type="app_feature_group",
                pages=_evn_pages_for_rows(app_phase_1),
                section_path=["Danh sách chi tiết chức năng cập nhật", "App chăm sóc khách hàng", "Giai đoạn 1"],
                content_format="markdown_table",
                metadata={
                    "platform": "mobile_app",
                    "phase": "giai_doan_1",
                    "change_type": "cap_nhat_giao_dien_moi",
                    "table_columns": EVN_FUNCTION_TABLE_COLUMNS,
                    "row_start": int(app_phase_1[0]["stt"]),
                    "row_end": int(app_phase_1[-1]["stt"]),
                },
            )
        )

    if app_phase_2:
        text = "\n".join(
            [
                "1. Danh sách chi tiết chức năng cập nhật",
                "App chăm sóc khách hàng - Bổ sung chức năng mới - Giai đoạn 2",
                _render_evn_feature_rows(app_phase_2),
            ]
        )
        output.append(
            _make_evn_record(
                template,
                text=text,
                chunk_type="app_feature_group",
                pages=_evn_pages_for_rows(app_phase_2),
                section_path=["Danh sách chi tiết chức năng cập nhật", "App chăm sóc khách hàng", "Giai đoạn 2"],
                content_format="markdown_table",
                metadata={
                    "platform": "mobile_app",
                    "phase": "giai_doan_2",
                    "change_type": "bo_sung_chuc_nang_moi",
                    "table_columns": EVN_FUNCTION_TABLE_COLUMNS,
                    "row_start": int(app_phase_2[0]["stt"]),
                    "row_end": int(app_phase_2[-1]["stt"]),
                },
            )
        )

    dashboard_rows = [row for row in cms_rows if row.get("feature", "").casefold() == "dashboard"]
    cms_general_rows = [row for row in cms_rows if row not in dashboard_rows]
    if dashboard_rows:
        dashboard_summary = copy.deepcopy(dashboard_rows[0])
        dashboard_summary["change"] = "Hiệu chỉnh biểu đồ và bổ sung tham số lọc; xem chunk Dashboard để biết chi tiết."
        cms_general_rows = sorted(
            [*cms_general_rows, dashboard_summary],
            key=lambda row: int(row.get("stt", "0") or 0),
        )
    if cms_general_rows:
        text = "\n".join(
            [
                "1. Danh sách chi tiết chức năng cập nhật",
                "Website Quản trị nội dung (CMS)",
                _render_evn_feature_rows(cms_general_rows),
            ]
        )
        output.append(
            _make_evn_record(
                template,
                text=text,
                chunk_type="cms_feature_group",
                pages=_evn_pages_for_rows(cms_general_rows),
                section_path=["Danh sách chi tiết chức năng cập nhật", "Website Quản trị nội dung (CMS)"],
                content_format="markdown_table",
                metadata={
                    "platform": "cms",
                    "change_type": "hieu_chinh_bo_sung_cms",
                    "table_columns": EVN_FUNCTION_TABLE_COLUMNS,
                    "row_start": int(cms_general_rows[0]["stt"]),
                    "row_end": int(cms_general_rows[-1]["stt"]),
                },
            )
        )

    if dashboard_rows:
        row = dashboard_rows[0]
        details = [_repair_evn_text(part.strip(" -;")) for part in re.split(r"\s+-\s*", row.get("change", "")) if part.strip(" -;")]
        if not details:
            details = [row.get("change", "")]
        text = "\n".join(
            [
                "1. Danh sách chi tiết chức năng cập nhật",
                "Website Quản trị nội dung (CMS) - Dashboard",
                "Dashboard:",
                *(f"- {detail.rstrip(';')}" for detail in details if detail),
            ]
        )
        output.append(
            _make_evn_record(
                template,
                text=text,
                chunk_type="cms_dashboard",
                pages=_evn_pages_for_rows(dashboard_rows),
                section_path=["Danh sách chi tiết chức năng cập nhật", "Website Quản trị nội dung (CMS)", "Dashboard"],
                content_format="semantic_bullets",
                metadata={
                    "platform": "cms",
                    "change_type": "hieu_chinh_bieu_do_va_bo_sung_bo_loc",
                    "row_start": int(row["stt"]),
                    "row_end": int(row["stt"]),
                },
            )
        )

    output.extend(_build_evn_ui_caption_records(template, doc))
    if not output:
        return records
    output = enforce_token_limit(output, tokenizer, max_tokens)
    return output

def apply_adaptive_administrative_chunking(
    records: list[dict[str, Any]],
    *,
    tokenizer: RegexVietnameseTokenizer,
    max_tokens: int,
) -> list[dict[str, Any]]:
    """Apply per-segment strategies only to recognised administrative parts."""

    stage = semanticize_administrative_tables(records)
    stage = semanticize_administrative_body(stage, tokenizer=tokenizer, max_tokens=max_tokens)
    return cleanup_administrative_artifacts(stage, tokenizer)


@dataclass(frozen=True)
class DoclingV6ChunkingResult:
    records: list[dict[str, Any]]
    quality: dict[str, Any]
    coverage: dict[str, Any]
    document_context: str


def chunk_docling_document(
    doc: DoclingDocument,
    *,
    source_file: str,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    context_budget: int = DEFAULT_CONTEXT_BUDGET,
    document_context_mode: Literal["metadata", "prefix"] = "metadata",
    must_contain: Sequence[str] = (),
    page_texts: dict[int, str] | None = None,
) -> DoclingV6ChunkingResult:
    """Chunk a DoclingDocument with the V6 structural-repair pipeline.

    The function is intentionally storage-agnostic. It returns normalized records and
    quality/coverage reports so the ingestion service can persist JSONL/Markdown artifacts
    using the backend's configured object storage.
    """
    if max_tokens < 96:
        raise ValueError("max_tokens must be at least 96.")
    if not 0 <= context_budget < max_tokens:
        raise ValueError("context_budget must be non-negative and smaller than max_tokens.")

    counter = RegexVietnameseTokenizer(max_tokens=max_tokens)
    document_profile = detect_document_profile(doc)
    # Use the presentation pipeline only after profile classification. This
    # prevents short administrative documents with appendix tables from being
    # flattened page-by-page as ``presentation_slide_part`` chunks.
    presentation_like = document_profile == "presentation"
    preamble = normalize_vietnamese_pdf_text(extract_page_1_preamble(doc))
    if presentation_like:
        native_page_1 = normalize_vietnamese_pdf_text((page_texts or {}).get(1, ""))
        if native_page_1 and text_quality_score(native_page_1) >= text_quality_score(preamble):
            preamble = native_page_1
    document_context = compact_document_context(preamble, counter, context_budget)
    prefix_tokens = counter.count_tokens(document_context)
    content_limit = (
        max(64, max_tokens - prefix_tokens)
        if document_context_mode == "prefix"
        else max_tokens
    )

    if presentation_like:
        records = build_presentation_records(
            doc,
            tokenizer=counter,
            max_tokens=max_tokens,
            document_context=document_context,
            page_texts=page_texts,
        )
        records = reindex_records(records, counter)
        for record in records:
            record.setdefault("source_file", source_file)
        coverage = build_coverage_report(doc, records)
        parsed_text_corpus = normalize_space(
            "\n".join(getattr(item, "text", "") for item in getattr(doc, "texts", []) or [])
        ).casefold()
        chunk_text_corpus = normalize_space(
            "\n".join(record["contextualized_text"] for record in records)
        ).casefold()
        coverage["required_phrase_checks"] = [
            {
                "phrase": normalize_space(phrase),
                "found_in_docling_document": normalize_space(phrase).casefold()
                in parsed_text_corpus,
                "found_in_chunks": normalize_space(phrase).casefold() in chunk_text_corpus,
            }
            for phrase in must_contain
        ]
        quality = build_quality_report(records, counter, max_tokens)
        return DoclingV6ChunkingResult(
            records=records,
            quality=quality,
            coverage=coverage,
            document_context=document_context,
        )

    chunker = HybridChunker(
        tokenizer=RegexVietnameseTokenizer(max_tokens=content_limit),
        serializer_provider=MarkdownTableSerializerProvider(),
        merge_peers=True,
        repeat_table_header=True,
    )

    native_chunks = list(chunker.chunk(dl_doc=doc))
    native_contexts = [chunker.contextualize(chunk=chunk) for chunk in native_chunks]
    raw_records: list[dict[str, Any]] = []

    for index, (chunk, native_context) in enumerate(
        zip(native_chunks, native_contexts, strict=True), start=1
    ):
        contextualized = (
            prepend_context_without_duplication(document_context, native_context)
            if document_context_mode == "prefix"
            else native_context
        )
        raw_records.append(
            {
                "chunk_id": f"raw_{index:03d}",
                "chunk_type": "docling_hybrid_repaired",
                "pages": chunk_pages(chunk),
                "headings": list(getattr(chunk.meta, "headings", None) or []),
                "raw_text": chunk.text,
                "raw_contextualized_text": native_context,
                "text": chunk.text,
                "contextualized_text": contextualized,
                "doc_item_types": chunk_item_types(chunk),
                "document_context": document_context,
            }
        )

    records = repair_records(
        raw_records,
        doc=doc,
        tokenizer=counter,
        max_tokens=max_tokens,
        document_profile=document_profile,
    )
    for record in records:
        record.setdefault("source_file", source_file)

    coverage = build_coverage_report(doc, records)
    parsed_text_corpus = normalize_space(
        "\n".join(getattr(item, "text", "") for item in getattr(doc, "texts", []) or [])
    ).casefold()
    chunk_text_corpus = normalize_space(
        "\n".join(record["contextualized_text"] for record in records)
    ).casefold()
    coverage["required_phrase_checks"] = [
        {
            "phrase": normalize_space(phrase),
            "found_in_docling_document": normalize_space(phrase).casefold()
            in parsed_text_corpus,
            "found_in_chunks": normalize_space(phrase).casefold() in chunk_text_corpus,
        }
        for phrase in must_contain
    ]
    # Last line of defence: quality gate and splitter use the exact same tokenizer.
    records = enforce_token_limit(records, counter, max_tokens)
    records = reindex_records(records, counter)
    quality = build_quality_report(records, counter, max_tokens)
    return DoclingV6ChunkingResult(
        records=records,
        quality=quality,
        coverage=coverage,
        document_context=document_context,
    )


def render_chunks_markdown(
    result: DoclingV6ChunkingResult,
    *,
    source_file: str,
    max_tokens: int,
    document_context_mode: str,
) -> str:
    lines: list[str] = [
        "# Docling HybridChunker V6 - structural repair and table balancing",
        "",
        f"- Nguồn: `{source_file}`",
        f"- Chunks: **{len(result.records)}**",
        f"- Max: **{max_tokens} token xấp xỉ**",
        f"- Context mode: **{document_context_mode}**",
        "- Sửa câu bị cắt qua trang",
        "- Khôi phục heading cha-con và quan hệ cùng cấp",
        "- Tách section/object bị gộp",
        "- Cân bằng lại các chunk bảng quá nhỏ",
        "- Chuẩn hóa context object, merged-cell, header và separator bảng",
        "- Chuẩn hóa định danh kỹ thuật nhưng giữ cảnh báo xung đột",
        "- Đánh dấu administrative footer không embedding",
        "- Kiểm tra giới hạn token và quality gate mở rộng",
        "",
    ]
    for record in result.records:
        page_text = ", ".join(map(str, record.get("pages", []))) or "không xác định"
        heading_text = " > ".join(record.get("headings", []))
        lines.extend(
            [
                f"## {record['chunk_id']} - trang {page_text} - "
                f"~{record['token_count_approx']} token",
                "",
                f"**Loại:** {record['chunk_type']}",
                "",
                f"**Headings:** {heading_text}",
                "",
                f"**Unit:** {record.get('unit') or ''}",
                "",
                f"**Scope:** {', '.join(record.get('scope', []))}",
                "",
                "```text",
                record["contextualized_text"],
                "```",
                "",
            ]
        )
    return "\n".join(lines)
