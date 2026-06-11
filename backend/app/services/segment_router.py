from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from typing import Any, Literal

from app.services.gis_chunking import (
    extract_deadlines,
    parse_attribute_tables,
    parse_procedure_rows,
    parse_relationship_schemas,
    parse_schema_field_line,
    parse_schema_objects,
    robust_normalize,
)
from app.services.parsers import ParsedElement
from app.services.table_relationships import normalize_metadata_value

logger = logging.getLogger(__name__)

SegmentType = Literal[
    "administrative_header",
    "administrative_dispatch",
    "dispatch_intro",
    "assignment_section",
    "assignment_subsection",
    "appendix_title",
    "appendix_prose",
    "procedure_table",
    "schema_appendix",
    "schema_intro",
    "schema_object_section",
    "schema_table",
    "schema_field_row",
    "signature_footer",
    "recipients_footer",
    "footer",
    "unknown_text",
]

SegmentChunkStrategy = Literal[
    "admin_dispatch_chunker",
    "assignment_chunker",
    "prose_section_chunker",
    "procedure_table_chunker",
    "schema_table_chunker",
    "schema_object_chunker",
    "schema_field_row_chunker",
    "footer_chunker",
    "semantic_text_chunker",
]

ADMIN_ASSIGNMENT_RE = re.compile(
    r"^\s*(?P<section>\d+(?:\.\d+)*)\.\s+(?P<title>.+?)\s*$",
    re.MULTILINE,
)
APPENDIX_01_RE = re.compile(r"(?mi)^\s*Phụ\s*lục\s*0?1\b")
APPENDIX_02_RE = re.compile(r"(?mi)^\s*Phụ\s*lục\s*0?2\b")
APPENDIX_01_RE = re.compile(r"(?mi)^\s*(?:Phụ\s*lục|Phá»¥\s*lá»¥c)\s*0?1\s*$")
APPENDIX_02_RE = re.compile(r"(?mi)^\s*(?:Phụ\s*lục|Phá»¥\s*lá»¥c)\s*0?2\s*$")
SCHEMA_OBJECT_RE = re.compile(
    r"(?m)^\s*\((?P<order>\d+)\)\s+(?P<object_code>F\d+_[A-Za-z0-9_]+)\s*[–-]\s*(?P<object_name>.+?)\s*$"
)
PRIORITY_OBJECT_CODE_RE = re.compile(r"\bF\d{2}_[A-Za-z0-9_]+\b")
SCHEMA_FIELD_START_RE = re.compile(r"^\s*\d{1,3}\s+\S+")
TABLE_ROW_START_RE = re.compile(r"^\s*\d{1,3}\s+\S+\s+\S+")
SIGNATURE_NAME_RE = re.compile(r"^[A-ZÀ-ỴĐ][\wÀ-ỹĐđ]+(?:\s+[A-ZÀ-ỴĐ][\wÀ-ỹĐđ]+){2,4}$")
DATA_TYPE_TOKENS = {
    "Text",
    "Number",
    "Date",
    "Datetime",
    "Double",
    "Float",
    "Integer",
    "Long",
    "Short",
    "Boolean",
}


@dataclass(frozen=True)
class ChunkingPlanSegment:
    segment_id: str
    page_range: list[int]
    start_char: int
    end_char: int
    segment_type: SegmentType
    chunk_strategy: SegmentChunkStrategy
    confidence: float
    reason: str
    text: str = ""
    metadata: dict[str, Any] = dataclass_field(default_factory=dict)


@dataclass(frozen=True)
class SegmentRoutingPlan:
    document_profile: str
    chunk_strategy: str
    segments: list[ChunkingPlanSegment]


@dataclass(frozen=True)
class HeadingDecision:
    is_heading: bool
    reason: str


@dataclass(frozen=True)
class _PageBlock:
    page_number: int
    start_char: int
    end_char: int
    text: str


class SegmentLevelChunkRouter:
    def plan(
        self,
        *,
        parsed_text: str,
        parsed_elements: list[ParsedElement] | None = None,
    ) -> SegmentRoutingPlan:
        pages = _page_blocks(parsed_text, parsed_elements or [])
        if not looks_like_mixed_gis_document(parsed_text):
            return SegmentRoutingPlan(
                document_profile="general",
                chunk_strategy="single_strategy",
                segments=[],
            )

        segments: list[ChunkingPlanSegment] = []
        app1 = APPENDIX_01_RE.search(parsed_text)
        app2 = APPENDIX_02_RE.search(parsed_text)
        app1_start = app1.start() if app1 else len(parsed_text)
        app2_start = app2.start() if app2 else len(parsed_text)

        admin_text = parsed_text[: min(app1_start, app2_start)].strip()
        if admin_text:
            admin_start = parsed_text.find(admin_text)
            admin_end = admin_start + len(admin_text)
            segments.append(
                ChunkingPlanSegment(
                    segment_id="seg_admin_dispatch",
                    page_range=_page_range_for_span(pages, admin_start, admin_end),
                    start_char=admin_start,
                    end_char=admin_end,
                    segment_type="administrative_dispatch",
                    chunk_strategy="admin_dispatch_chunker",
                    confidence=0.9,
                    reason="administrative_dispatch_before_appendices",
                    text=admin_text,
                    metadata={"document_part": "administrative_dispatch"},
                )
            )

            for index, section in enumerate(_assignment_spans(admin_text), start=1):
                start = admin_start + section["start"]
                end = admin_start + section["end"]
                segments.append(
                    ChunkingPlanSegment(
                        segment_id=f"seg_assignment_{section['section_id'].replace('.', '_')}",
                        page_range=_page_range_for_span(pages, start, end),
                        start_char=start,
                        end_char=end,
                        segment_type=(
                            "assignment_subsection"
                            if "." in section["section_id"]
                            else "assignment_section"
                        ),
                        chunk_strategy="assignment_chunker",
                        confidence=0.9,
                        reason="numbered_assignment_heading",
                        text=admin_text[section["start"] : section["end"]].strip(),
                        metadata={
                            "section_id": section["section_id"],
                            "unit": section["unit"],
                            "scope": section["scope"],
                            "ordinal": index,
                        },
                    )
                )

            footer = _footer_span(admin_text)
            if footer is not None:
                start = admin_start + footer[0]
                end = admin_start + footer[1]
                segments.append(
                    ChunkingPlanSegment(
                        segment_id="seg_admin_footer",
                        page_range=_page_range_for_span(pages, start, end),
                        start_char=start,
                        end_char=end,
                        segment_type="footer",
                        chunk_strategy="footer_chunker",
                        confidence=0.85,
                        reason="recipients_or_signature_footer",
                        text=admin_text[footer[0] : footer[1]].strip(),
                        metadata={"chunk_type": "administrative_footer"},
                    )
                )

        if app1 is not None:
            end = app2_start if app2_start > app1_start else len(parsed_text)
            segments.append(
                ChunkingPlanSegment(
                    segment_id="seg_appendix_01_procedure_table",
                    page_range=_page_range_for_span(pages, app1_start, end),
                    start_char=app1_start,
                    end_char=end,
                    segment_type="procedure_table",
                    chunk_strategy="procedure_table_chunker",
                    confidence=0.95,
                    reason="appendix_01_procedure_table",
                    text=parsed_text[app1_start:end].strip(),
                    metadata={"appendix_id": "01"},
                )
            )

        if app2 is not None:
            segments.append(
                ChunkingPlanSegment(
                    segment_id="seg_appendix_02_schema",
                    page_range=_page_range_for_span(pages, app2_start, len(parsed_text)),
                    start_char=app2_start,
                    end_char=len(parsed_text),
                    segment_type="schema_appendix",
                    chunk_strategy="schema_table_chunker",
                    confidence=0.95,
                    reason="appendix_02_schema_tables",
                    text=parsed_text[app2_start:].strip(),
                    metadata={"appendix_id": "02"},
                )
            )
            for match in SCHEMA_OBJECT_RE.finditer(parsed_text[app2_start:]):
                start = app2_start + match.start()
                segments.append(
                    ChunkingPlanSegment(
                        segment_id=f"seg_schema_{match.group('object_code')}",
                        page_range=_page_range_for_span(pages, start, start + len(match.group(0))),
                        start_char=start,
                        end_char=start + len(match.group(0)),
                        segment_type="schema_table",
                        chunk_strategy="schema_object_chunker",
                        confidence=0.95,
                        reason="schema_object_heading",
                        text=match.group(0).strip(),
                        metadata={
                            "appendix_id": "02",
                            "object_code": match.group("object_code"),
                            "object_name": match.group("object_name").strip(),
                        },
                    )
                )

        logger.debug(
            "segment-router detected %s segments: %s",
            len(segments),
            [
                (segment.segment_id, segment.segment_type, segment.page_range)
                for segment in segments
            ],
        )
        return SegmentRoutingPlan(
            document_profile="mixed_administrative_technical",
            chunk_strategy="adaptive_segmented",
            segments=sorted(segments, key=lambda segment: (segment.start_char, segment.segment_id)),
        )


class AdaptiveSegmentChunker:
    def chunk_text(
        self,
        text: str,
        *,
        parsed_elements: list[ParsedElement] | None = None,
    ) -> list[dict[str, Any]]:
        plan = SegmentLevelChunkRouter().plan(parsed_text=text, parsed_elements=parsed_elements)
        if plan.chunk_strategy != "adaptive_segmented":
            return []

        chunks: list[dict[str, Any]] = []
        admin_segment = next(
            (
                segment
                for segment in plan.segments
                if segment.segment_type == "administrative_dispatch"
            ),
            None,
        )
        if admin_segment is not None:
            chunks.extend(_admin_dispatch_chunks(admin_segment, start_index=len(chunks)))

        procedure_segment = next(
            (segment for segment in plan.segments if segment.segment_type == "procedure_table"),
            None,
        )
        if procedure_segment is not None:
            chunks.extend(
                _procedure_table_chunks(
                    procedure_segment,
                    parsed_elements=parsed_elements or [],
                    start_index=len(chunks),
                )
            )

        schema_segment = next(
            (segment for segment in plan.segments if segment.segment_type == "schema_appendix"),
            None,
        )
        if schema_segment is not None:
            chunks.extend(
                _schema_appendix_chunks(
                    schema_segment,
                    parsed_elements=parsed_elements or [],
                    start_index=len(chunks),
                )
            )

        for index, chunk in enumerate(chunks):
            chunk["chunk_index"] = index
        return chunks


def looks_like_mixed_gis_document(text: str) -> bool:
    normalized = robust_normalize(text[:60000])
    has_appendix_01 = "phu luc 01" in normalized or "phu luc 1" in normalized
    has_appendix_02 = "phu luc 02" in normalized or "phu luc 2" in normalized
    has_plan = (
        "v/v ke hoach xay dung he thong gis" in normalized
        or "ke hoach xay dung he thong gis" in normalized
        or "gis evncpc" in normalized
    )
    has_appendix_titles = (
        "phuong an sap nhap du lieu gis" in normalized and "mo ta du lieu khoi tao" in normalized
    )
    object_hits = len(set(PRIORITY_OBJECT_CODE_RE.findall(text)))
    has_schema = (
        object_hits >= 2 or bool(SCHEMA_OBJECT_RE.search(text)) or "truong du lieu" in normalized
    )
    has_gis = "gis" in normalized and ("evncpc" in normalized or has_schema or has_plan)
    return has_gis and has_appendix_01 and has_appendix_02 and (has_schema or has_appendix_titles)


def classify_heading_line(line: str) -> HeadingDecision:
    stripped = " ".join(line.strip().split())
    normalized = normalize_metadata_value(stripped)
    if not stripped:
        return _heading_decision(False, "empty_line", stripped)
    if len(stripped) <= 4 or normalized in {"so", "so:"}:
        return _heading_decision(False, "too_short_fragment", stripped)
    if SCHEMA_FIELD_START_RE.match(stripped) and _parse_schema_field_row(stripped, {}, None):
        return _heading_decision(False, "schema_field_row", stripped)
    if TABLE_ROW_START_RE.match(stripped) and not ADMIN_ASSIGNMENT_RE.match(stripped):
        return _heading_decision(False, "table_row_like", stripped)
    if stripped.endswith(",") or stripped.endswith(";"):
        return _heading_decision(False, "paragraph_continuation", stripped)
    if SIGNATURE_NAME_RE.match(stripped):
        return _heading_decision(False, "signature_name", stripped)
    if re.match(r"^\d+(?:\.\d+)*\.\s+.+:$", stripped):
        return _heading_decision(True, "numbered_administrative_heading", stripped)
    if re.match(r"(?i)^phụ\s*lục\s*\d+", stripped):
        return _heading_decision(True, "appendix_title", stripped)
    if SCHEMA_OBJECT_RE.match(stripped):
        return _heading_decision(True, "schema_object_heading", stripped)
    if stripped.isupper() and len(stripped) >= 12:
        return _heading_decision(True, "uppercase_title", stripped)
    if "ngày" in normalized and "hdtv" in normalized:
        return _heading_decision(False, "sentence_fragment", stripped)
    if "hdtv" in normalized and "ve viec" in normalized:
        return _heading_decision(False, "sentence_fragment", stripped)
    return _heading_decision(False, "not_heading", stripped)


def _heading_decision(is_heading: bool, reason: str, line: str) -> HeadingDecision:
    logger.debug(
        "heading classification: is_heading=%s reason=%s line=%r",
        is_heading,
        reason,
        line[:160],
    )
    return HeadingDecision(is_heading, reason)


def schema_or_procedure_metadata_boost(query: str, metadata: dict[str, Any]) -> float:
    normalized_query = normalize_metadata_value(query)
    chunk_type = str(metadata.get("chunk_type") or "")
    boost = 0.0
    object_code = str(metadata.get("object_code") or "")
    field_name = str(metadata.get("field_name") or "")
    appendix_id = str(metadata.get("appendix_id") or "")
    data_type = str(metadata.get("data_type") or "")
    source_data = str(metadata.get("source_data") or "")
    relationship_name = str(metadata.get("relationship_name") or "")
    table_name = str(metadata.get("table_name") or "")

    if object_code and normalize_metadata_value(object_code) in normalized_query:
        boost += 8.0
    if field_name and normalize_metadata_value(field_name) in normalized_query:
        boost += 10.0
    appendix_number = re.escape(appendix_id.lstrip("0") or appendix_id)
    if appendix_id and re.search(rf"phu luc\s*0?{appendix_number}\b", normalized_query):
        boost += 4.0
    if source_data and normalize_metadata_value(source_data) in normalized_query:
        boost += 2.0
    if data_type and normalize_metadata_value(data_type) in normalized_query:
        boost += 1.0
    if relationship_name and normalize_metadata_value(relationship_name) in normalized_query:
        boost += 9.0
    if table_name and normalize_metadata_value(table_name) in normalized_query:
        boost += 5.0
    if chunk_type == "schema_field_row":
        boost += 4.0
    elif chunk_type == "schema_object_summary":
        boost += 3.0
    elif chunk_type == "procedure_table_row":
        boost += 4.0
        data_type_value = str(metadata.get("data_type") or "")
        if data_type_value and normalize_metadata_value(data_type_value) in normalized_query:
            boost += 6.0
    elif chunk_type == "deadline_index":
        if any(
            term in normalized_query for term in ("deadline", "thoi han", "khi nao", "hoan thanh")
        ):
            boost += 8.0
    elif chunk_type == "assignment_section" and any(
        term in normalized_query for term in ("deadline", "thoi han", "khi nao", "hoan thanh")
    ):
        boost += 3.0
    elif chunk_type == "gis_relationship_schema":
        boost += 5.0
    elif chunk_type == "attribute_table_schema":
        boost += 2.0
    return boost


def _page_blocks(text: str, elements: list[ParsedElement]) -> list[_PageBlock]:
    page_elements = [element for element in elements if element.element_type == "page"]
    if not page_elements:
        return [_PageBlock(page_number=1, start_char=0, end_char=len(text), text=text)]
    blocks: list[_PageBlock] = []
    cursor = 0
    for fallback_index, element in enumerate(page_elements, start=1):
        page_text = element.text.strip()
        if not page_text:
            continue
        start = text.find(page_text, cursor)
        if start < 0:
            start = cursor
        end = start + len(page_text)
        cursor = end
        blocks.append(
            _PageBlock(
                page_number=element.page_number or fallback_index,
                start_char=start,
                end_char=end,
                text=page_text,
            )
        )
    return blocks or [_PageBlock(page_number=1, start_char=0, end_char=len(text), text=text)]


def _page_range_for_span(pages: list[_PageBlock], start: int, end: int) -> list[int]:
    hits = [page.page_number for page in pages if page.end_char >= start and page.start_char <= end]
    if not hits:
        return [1, 1]
    return [min(hits), max(hits)]


def _assignment_spans(text: str) -> list[dict[str, Any]]:
    matches = [
        match
        for match in ADMIN_ASSIGNMENT_RE.finditer(text)
        if classify_heading_line(match.group(0)).is_heading
    ]
    spans: list[dict[str, Any]] = []
    current_unit = ""
    for index, match in enumerate(matches):
        section_id = match.group("section")
        title = match.group("title").strip(" :")
        if "." not in section_id:
            current_unit = title
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        unit = current_unit if "." in section_id else title
        spans.append(
            {
                "section_id": section_id,
                "title": title,
                "unit": unit,
                "scope": title if "." in section_id else None,
                "start": match.start(),
                "end": end,
            }
        )
    return spans


def _footer_span(text: str) -> tuple[int, int] | None:
    matches = [
        match.start()
        for match in re.finditer(r"(?mi)^\s*(Nơi nhận|KT\.|GIÁM ĐỐC|Lê Hoàng Anh Dũng)\b", text)
    ]
    if not matches:
        return None
    return min(matches), len(text)


def _admin_dispatch_chunks(
    segment: ChunkingPlanSegment, *, start_index: int
) -> list[dict[str, Any]]:
    text = segment.text
    chunks: list[dict[str, Any]] = []
    first_assignment = next(iter(_assignment_spans(text)), None)
    overview_text = text[: first_assignment["start"]].strip() if first_assignment else text.strip()
    if overview_text:
        chunks.append(
            _chunk(
                start_index + len(chunks),
                overview_text,
                {
                    "chunk_type": "administrative_overview",
                    "segment_type": "administrative_dispatch",
                    "chunk_strategy": "admin_dispatch_chunker",
                    "document_profile": "mixed_administrative_technical",
                    "page_range": segment.page_range,
                    "retrieval_priority": "medium",
                },
            )
        )

    for assignment in _assignment_spans(text):
        content = text[assignment["start"] : assignment["end"]].strip()
        deadlines = extract_deadlines(content)
        chunks.append(
            _chunk(
                start_index + len(chunks),
                content,
                {
                    "chunk_type": "assignment_section",
                    "segment_type": "assignment_section",
                    "chunk_strategy": "assignment_chunker",
                    "section_id": assignment["section_id"],
                    "unit": assignment["unit"],
                    "unit_normalized": robust_normalize(assignment["unit"]),
                    "scope": assignment["scope"],
                    "scope_normalized": robust_normalize(assignment["scope"] or ""),
                    "deadline": deadlines,
                    "document_profile": "mixed_administrative_technical",
                    "page_range": segment.page_range,
                    "retrieval_priority": "high",
                },
            )
        )

    deadline_index = extract_deadlines(text)
    if deadline_index:
        chunks.append(
            _chunk(
                start_index + len(chunks),
                "\n".join(f"- {deadline}" for deadline in deadline_index),
                {
                    "chunk_type": "deadline_index",
                    "segment_type": "administrative_dispatch",
                    "chunk_strategy": "assignment_chunker",
                    "deadlines": deadline_index,
                    "document_profile": "mixed_administrative_technical",
                    "page_range": segment.page_range,
                    "retrieval_priority": "high",
                },
            )
        )

    footer = _footer_span(text)
    if footer is not None:
        chunks.append(
            _chunk(
                start_index + len(chunks),
                text[footer[0] : footer[1]].strip(),
                {
                    "chunk_type": "administrative_footer",
                    "segment_type": "footer",
                    "chunk_strategy": "footer_chunker",
                    "document_profile": "mixed_administrative_technical",
                    "page_range": segment.page_range,
                    "retrieval_priority": "low",
                },
            )
        )
    return chunks


def _procedure_table_chunks(
    segment: ChunkingPlanSegment,
    *,
    parsed_elements: list[ParsedElement],
    start_index: int,
) -> list[dict[str, Any]]:
    rows = parse_procedure_rows(segment.text, segment.page_range, parsed_elements)
    title = _appendix_title(segment.text) or "Phương án sáp nhập dữ liệu GIS 110kV, GIS trung thế"
    chunks = [
        _chunk(
            start_index,
            title,
            {
                "chunk_type": "appendix_title",
                "segment_type": "procedure_table",
                "chunk_strategy": "procedure_table_chunker",
                "appendix_id": "01",
                "title": title,
                "page_range": segment.page_range,
                "retrieval_priority": "medium",
            },
        )
    ]
    for row in rows:
        content = (
            f"TT {row['tt']} - {row['data_type']}\n"
            f"CPCIT: {row['cpcit']}\n"
            f"Các CTĐL (trừ KHoPC): {row['ctdl']}"
        )
        chunks.append(
            _chunk(
                start_index + len(chunks),
                content,
                {
                    "chunk_type": "procedure_table_row",
                    "segment_type": "procedure_table",
                    "chunk_strategy": "procedure_table_chunker",
                    "appendix_id": "01",
                    "table_name": title,
                    "tt": row["tt"],
                    "data_type": row["data_type"],
                    "data_type_normalized": row.get("data_type_normalized")
                    or robust_normalize(row["data_type"]),
                    "cpcit": row["cpcit"],
                    "ctdl": row["ctdl"],
                    "responsible_columns": ["CPCIT", "Các CTĐL (trừ KHoPC)"],
                    "page_range": row.get("page_range") or segment.page_range,
                    "extraction_source": "pdf_layout_table",
                    "confidence": 0.95,
                    "retrieval_priority": "high",
                },
            )
        )
    return chunks


def _schema_appendix_chunks(
    segment: ChunkingPlanSegment,
    *,
    parsed_elements: list[ParsedElement],
    start_index: int,
) -> list[dict[str, Any]]:
    objects = parse_schema_objects(segment.text, segment.page_range, parsed_elements)
    schema_object_codes = [obj["object_code"] for obj in objects]
    priority_object_codes = _extract_priority_object_codes(segment.text)
    object_codes = priority_object_codes or schema_object_codes
    title = _appendix_title(segment.text) or "Mô tả dữ liệu khởi tạo và chuyển đổi"
    chunks = [
        _chunk(
            start_index,
            segment.text[: min(len(segment.text), 1200)].strip(),
            {
                "chunk_type": "schema_appendix_overview",
                "segment_type": "schema_appendix",
                "chunk_strategy": "schema_table_chunker",
                "appendix_id": "02",
                "title": title,
                "object_codes": object_codes,
                "schema_object_codes": schema_object_codes,
                "priority_object_codes": priority_object_codes,
                "priority_object_count": len(object_codes),
                "page_range": segment.page_range,
                "retrieval_priority": "medium",
            },
        )
    ]
    for obj in objects:
        fields = obj["fields"]
        converted_fields = [field["field_name"] for field in fields if field["convert_to_gis"]]
        source_systems = sorted({field["source_data"] for field in fields if field["source_data"]})
        summary_content = (
            f"Object: {obj['object_code']} - {obj['object_name']}\n"
            f"Số lượng trường: {obj['field_count']}\n"
            f"Nguồn dữ liệu chính: {', '.join(source_systems)}\n"
            f"Các trường chuyển đổi sang GIS: {', '.join(converted_fields)}"
        )
        chunks.append(
            _chunk(
                start_index + len(chunks),
                summary_content,
                {
                    "chunk_type": "schema_object_summary",
                    "segment_type": "schema_object_section",
                    "chunk_strategy": "schema_object_chunker",
                    "appendix_id": "02",
                    "object_code": obj["object_code"],
                    "object_code_normalized": robust_normalize(obj["object_code"]),
                    "object_name": obj["object_name"],
                    "object_name_normalized": robust_normalize(obj["object_name"]),
                    "field_count": obj["field_count"],
                    "converted_fields": converted_fields,
                    "source_systems": source_systems,
                    "page_range": obj["page_range"],
                    "retrieval_priority": "high",
                },
            )
        )
        for field in fields:
            content = (
                f"Trường dữ liệu: {field['field_name']}\n"
                f"Object: {obj['object_code']} - {obj['object_name']}\n"
                f"Mô tả: {field['description']}\n"
                f"Kiểu dữ liệu: {field['data_type']}\n"
                f"Độ rộng: {field.get('width') or ''}\n"
                f"Nguồn dữ liệu: {field['source_data']}\n"
                f"Chuyển đổi sang GIS: {'Có' if field['convert_to_gis'] else 'Không'}"
            )
            chunks.append(
                _chunk(
                    start_index + len(chunks),
                    content,
                    {
                        "chunk_type": "schema_field_row",
                        "segment_type": "schema_field_row",
                        "chunk_strategy": "schema_field_row_chunker",
                        "appendix_id": "02",
                        "object_code": obj["object_code"],
                        "object_code_normalized": robust_normalize(obj["object_code"]),
                        "object_name": obj["object_name"],
                        "object_name_normalized": robust_normalize(obj["object_name"]),
                        **field,
                        "field_name_normalized": robust_normalize(field["field_name"]),
                        "page_range": field.get("page_range") or obj["page_range"],
                        "extraction_source": "pdf_layout_table",
                        "confidence": 0.95,
                        "retrieval_priority": "high",
                    },
                )
            )
    for table in parse_attribute_tables(segment.text, segment.page_range):
        content = (
            f"Bảng dữ liệu thuộc tính: {table['table_name']}\n"
            f"Mô tả: {table['description']}\n"
            f"Trường dữ liệu: {', '.join(field['field_name'] for field in table['fields'])}"
        )
        chunks.append(
            _chunk(
                start_index + len(chunks),
                content,
                {
                    "chunk_type": "attribute_table_schema",
                    "segment_type": "schema_appendix",
                    "chunk_strategy": "schema_table_chunker",
                    "appendix_id": "02",
                    "table_name": table["table_name"],
                    "description": table["description"],
                    "fields": table["fields"],
                    "page_range": table["page_range"],
                    "retrieval_priority": "medium",
                },
            )
        )
    for relationship in parse_relationship_schemas(segment.text, segment.page_range):
        content = (
            f"Mối quan hệ GIS: {relationship['relationship_name']}\n"
            f"Lớp nguồn: {relationship['source_layer']} qua {relationship['source_key']}\n"
            f"Bảng đích: {relationship['target_table']} qua {relationship['target_key']}\n"
            f"Kiểu quan hệ: {relationship['cardinality']}"
        )
        chunks.append(
            _chunk(
                start_index + len(chunks),
                content,
                {
                    "chunk_type": "gis_relationship_schema",
                    "segment_type": "schema_appendix",
                    "chunk_strategy": "schema_table_chunker",
                    "appendix_id": "02",
                    **relationship,
                    "retrieval_priority": "high",
                },
            )
        )
    return chunks


def _parse_procedure_rows(text: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for line in text.splitlines():
        cells = _split_table_line(line)
        if len(cells) >= 4 and cells[0].isdigit():
            rows.append(
                {"tt": cells[0], "data_type": cells[1], "cpcit": cells[2], "ctdl": cells[3]}
            )
    if rows:
        return rows

    matches = list(re.finditer(r"(?m)^\s*(?P<tt>[12])\s+(?P<data>Dữ liệu GIS[^\n]+)$", text))
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        body = " ".join(text[match.end() : end].split())
        rows.append(
            {"tt": match.group("tt"), "data_type": match.group("data"), "cpcit": body, "ctdl": body}
        )
    return rows


def _extract_priority_object_codes(text: str) -> list[str]:
    seen: set[str] = set()
    codes: list[str] = []
    intro_end_match = SCHEMA_OBJECT_RE.search(text)
    intro_text = text[: intro_end_match.start()] if intro_end_match else text
    for match in PRIORITY_OBJECT_CODE_RE.finditer(intro_text):
        code = match.group(0)
        if code not in seen:
            seen.add(code)
            codes.append(code)
    logger.debug("schema appendix priority object codes detected: %s", codes)
    return codes


def _parse_schema_objects(text: str, default_page_range: list[int]) -> list[dict[str, Any]]:
    matches = list(SCHEMA_OBJECT_RE.finditer(text))
    objects: list[dict[str, Any]] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        body = text[match.end() : end]
        fields = _parse_schema_field_rows(body, default_page_range)
        explicit_count = _extract_field_count(body)
        objects.append(
            {
                "object_code": match.group("object_code"),
                "object_name": match.group("object_name").strip(),
                "field_count": explicit_count or len(fields),
                "fields": fields,
                "page_range": default_page_range,
            }
        )
        logger.debug(
            "schema object detected %s with %s rows", match.group("object_code"), len(fields)
        )
    return objects


def _parse_schema_field_rows(body: str, page_range: list[int]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line or _is_schema_header_line(line):
            continue
        parsed = _parse_schema_field_row(line, {}, page_range)
        if parsed is not None:
            rows.append(parsed)
            logger.debug("schema field row reconstructed %s", parsed["field_name"])
    return rows


def _parse_schema_field_row(
    line: str,
    defaults: dict[str, Any] | None = None,
    page_range: list[int] | None = None,
) -> dict[str, Any] | None:
    return parse_schema_field_line(line, page_range)


def _split_table_line(line: str) -> list[str]:
    if "|" not in line:
        return []
    cleaned = line.strip()
    if cleaned.startswith("|"):
        cleaned = cleaned[1:]
    return [cell.strip() for cell in cleaned.split("|")]


def _is_schema_header_line(line: str) -> bool:
    normalized = normalize_metadata_value(line)
    return (
        "truong du lieu" in normalized or "ten truong" in normalized or normalized.startswith("tt ")
    )


def _truthy_convert_marker(value: str) -> bool:
    return normalize_metadata_value(value) in {"x", "co", "yes", "true", "1"}


def _extract_field_count(text: str) -> int | None:
    match = re.search(r"(?i)số\s+lượng\s+trường\s*[:：]?\s*(\d{1,3})", text)
    return int(match.group(1)) if match else None


def _appendix_title(text: str) -> str | None:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return None
    if len(lines) >= 2 and re.match(r"(?i)^phụ\s*lục", lines[0]):
        return lines[1]
    return lines[0]


def _chunk(index: int, content: str, metadata: dict[str, Any]) -> dict[str, Any]:
    return {"chunk_index": index, "content": content, "metadata": metadata}
