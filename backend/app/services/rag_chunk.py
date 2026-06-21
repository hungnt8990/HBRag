from __future__ import annotations

import hashlib
import html
import re
import unicodedata
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field

from app.services.access_control import flatten_access_payload, normalize_access_payload
from app.services.table_relationships import (
    parse_technology_area_rows_from_text,
    validate_technology_area_row,
)

NON_INDEXABLE_CHUNK_TYPES = {
    "administrative_footer",
    "header_footer",
    "empty",
    "parse_error",
    "footer",
}
FAILED_QUALITY_STATUSES = {"fail", "failed", "rejected"}


class RagChunk(BaseModel):
    model_config = ConfigDict(extra="allow")

    chunk_id: str
    document_id: str
    document_version: str | None = None
    tenant_id: str | None = None

    chunk_type: str = "text"
    content_format: str = "text"

    text: str
    raw_text: str | None = None
    source_raw_text: str | None = None
    normalized_text: str | None = None
    provenance_status: str | None = None

    section_path: list[str] = Field(default_factory=list)
    section_id: str | None = None
    parent_section_id: str | None = None
    parent_chunk_id: str | None = None

    # Legal/article-aware chunk metadata. These fields are intentionally stored
    # in JSONB/Qdrant payloads rather than SQL columns, so split articles can be
    # grouped back together during retrieval without changing the database schema.
    chapter_number: str | None = None
    chapter_title: str | None = None
    article_number: str | None = None
    article_title: str | None = None
    article_part: int | None = None
    article_part_total: int | None = None
    subchunk_index: int | None = None
    subchunk_total: int | None = None

    unit: str | None = None
    scope: list[str] = Field(default_factory=list)

    pages: list[int] = Field(default_factory=list)
    page_start: int | None = None
    page_end: int | None = None

    table_name: str | None = None
    table_description: str | None = None
    row_start: int | None = None
    row_end: int | None = None
    table_columns: list[str] = Field(default_factory=list)
    case_code: str | None = None
    case_name: str | None = None
    labor_code_benefit: str | None = None
    collective_agreement_benefit: str | None = None
    total_leave_benefit: str | None = None
    total_leave_days: int | None = None
    benefit_source: str | None = None
    row_text: str | None = None
    entity: str | None = None
    field_names: list[str] = Field(default_factory=list)
    source_systems: list[str] = Field(default_factory=list)
    convertible_fields: list[str] = Field(default_factory=list)

    relationship_name: str | None = None
    relationship_type: str | None = None
    stt: str | None = None
    area: str | None = None
    area_normalized: str | None = None
    lead_department: str | None = None
    lead_department_normalized: str | None = None
    staff_names: list[str] = Field(default_factory=list)
    staff: list[dict[str, Any]] = Field(default_factory=list)
    assignment_type: str | None = None
    has_specific_person: bool | None = None
    source_table: str | None = None
    confidence: float | None = None
    person_name: str | None = None
    person_name_normalized: str | None = None
    areas: list[dict[str, Any]] = Field(default_factory=list)
    answer_text: str | None = None
    source_row_id: str | None = None
    canonical_text: str | None = None
    source_entity: str | None = None
    source_key: str | None = None
    target_table: str | None = None
    target_key: str | None = None
    cardinality: str | None = None

    cross_references: list[str] = Field(default_factory=list)
    resolved_reference_text: str | None = None

    source_file: str
    source_uri: str | None = None
    document_title: str | None = None
    issuer: str | None = None

    parser: str = "unknown"
    parser_version: str | None = None
    chunker: str = "unknown"
    chunker_version: str | None = None

    token_count: int | None = None
    quality_status: str = "pass"
    validation_issues: list[dict[str, Any]] = Field(default_factory=list)

    indexable: bool = True
    embedding_enabled: bool = True
    content_hash: str

    chunk_index: int | None = None
    database_chunk_id: str | None = None
    organization_id: str | None = None
    knowledge_base_id: str | None = None
    uploaded_by_user_id: str | None = None
    visibility: str | None = None
    access: dict[str, Any] = Field(default_factory=dict)
    classification: str | None = None
    owner_org_id: str | None = None
    owner_org_path: str | None = None
    business_domains: list[str] = Field(default_factory=list)
    project_codes: list[str] = Field(default_factory=list)
    allowed_org_paths: list[str] = Field(default_factory=list)
    allowed_role_names: list[str] = Field(default_factory=list)
    allowed_group_codes: list[str] = Field(default_factory=list)
    allowed_user_ids: list[str] = Field(default_factory=list)
    denied_org_paths: list[str] = Field(default_factory=list)
    denied_role_names: list[str] = Field(default_factory=list)
    denied_group_codes: list[str] = Field(default_factory=list)
    denied_user_ids: list[str] = Field(default_factory=list)
    inherit_permission: bool = True

    # Search/embedding enrichment fields. They are persisted into Qdrant payload
    # and can also live inside chunks.metadata JSONB without a SQL schema change.
    identifiers: list[str] = Field(default_factory=list)
    doc_codes: list[str] = Field(default_factory=list)
    dates: list[str] = Field(default_factory=list)
    platform: str | None = None
    phase: str | None = None
    change_type: str | None = None
    content_type: str | None = None
    change_topic: str | None = None
    screen_names: list[str] = Field(default_factory=list)
    embedding_text: str | None = None
    enriched: bool = False
    enrichment_summary: str | None = None
    enrichment_keywords: list[str] = Field(default_factory=list)
    document_code: str | None = None
    issued_date: str | None = None
    document_type: str | None = None
    structure_path: str | None = None


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def normalize_section_path(value: Any) -> list[str]:
    if isinstance(value, str):
        return [part.strip() for part in value.split(">") if part.strip()]
    if isinstance(value, list | tuple):
        return [str(part).strip() for part in value if str(part).strip()]
    return []


def normalize_scope(value: Any) -> list[str]:
    if isinstance(value, str):
        return [part.strip() for part in re.split(r"[,;]", value) if part.strip()]
    if isinstance(value, list | tuple | set):
        return [str(part).strip() for part in value if str(part).strip()]
    return []


def infer_content_format(text: str, metadata: dict[str, Any]) -> str:
    chunk_type = str(metadata.get("chunk_type") or "").lower()
    if metadata.get("table_name") or chunk_type in {
        "table",
        "table_block",
        "table_rows",
        "gis_table",
        "schema_table",
        "docling_table",
    }:
        return "markdown_table"
    table_lines = [line for line in text.splitlines() if line.lstrip().startswith("|")]
    if len(table_lines) >= 2:
        return "markdown_table"
    return "text"


def infer_table_columns(text: str) -> list[str]:
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if cells and not all(re.fullmatch(r":?-{3,}:?", cell or "") for cell in cells):
            return cells
    return []



def infer_table_metadata(
    text: str,
    section_path: list[str],
) -> tuple[str | None, str | None, int | None, int | None]:
    combined = "\n".join([*section_path, text[:1000]])
    table_name: str | None = None
    for pattern in (
        r"Tên bảng dữ liệu:\s*([A-Za-z][A-Za-z0-9_]+)",
        r"\b(HinhAnh(?:CotDien|KhachHang|HoSoKhachHang))\b",
        r"\b(F\d+_[A-Za-z0-9_]+)\b",
    ):
        match = re.search(pattern, combined)
        if match:
            table_name = match.group(1)
            break

    description: str | None = None
    if section_path:
        leaf = section_path[-1]
        if " - " in leaf:
            description = leaf.split(" - ", 1)[1].strip() or None
    if description is None:
        match = re.search(r"Mô tả\s*:?\s*([^\n|]+)", text, flags=re.IGNORECASE)
        if match:
            description = " ".join(match.group(1).split()).strip() or None

    row_numbers: list[int] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if cells and cells[0].isdigit():
            row_numbers.append(int(cells[0]))
    return (
        table_name,
        description,
        min(row_numbers) if row_numbers else None,
        max(row_numbers) if row_numbers else None,
    )


DOC_CODE_PATTERN = re.compile(
    r"\b(?P<number>\d{2,6})\s*/\s*(?P<suffix>[A-ZĐƠƯÂÊÔĂÁÀẢÃẠÉÈẺẼẸÍÌỈĨỊÓÒỎÕỌÚÙỦŨỤÝỲỶỸỴ0-9][A-ZĐƠƯÂÊÔĂÁÀẢÃẠÉÈẺẼẸÍÌỈĨỊÓÒỎÕỌÚÙỦŨỤÝỲỶỸỴ0-9\-_/]{2,})\b",
    flags=re.IGNORECASE,
)
DATE_PATTERN = re.compile(r"\b\d{1,2}/\d{1,2}/\d{4}\b")
BARE_IDENTIFIER_PATTERN = re.compile(r"\b\d{3,8}\b")
HTML_BLOCK_BOUNDARY_PATTERN = re.compile(
    r"(?i)<br\s*/?>|</p>|</div>|</tr>|</li>|</td>|</th>|</h[1-6]>"
)
HTML_TAG_PATTERN = re.compile(r"<[^>]+>")


def _unique_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for value in values:
        clean = " ".join(str(value or "").split()).strip().strip(".,;:()[]{}")
        if not clean:
            continue
        key = clean.casefold()
        if key in seen:
            continue
        seen.add(key)
        unique.append(clean)
    return unique


def _as_string_list(value: Any) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, str):
        return _unique_preserve_order([part for part in re.split(r"[,;|]", value) if part.strip()])
    if isinstance(value, list | tuple | set):
        return _unique_preserve_order([str(item) for item in value if str(item).strip()])
    return _unique_preserve_order([str(value)])


def extract_search_metadata(text: str, record: dict[str, Any]) -> dict[str, list[str]]:
    """Extract exact-match search keys from chunk text/metadata.

    Dense embeddings are weak for bare identifiers such as ``3113``.  We still
    enrich the embedding input with these keys, but the same values are also
    stored as Qdrant payload / JSONB metadata for exact lookup and boosting.
    """
    combined_parts = [text]
    for key in (
        "raw_text",
        "document_context",
        "resolved_reference_text",
        "source_file",
        "document_title",
    ):
        value = record.get(key)
        if isinstance(value, str):
            combined_parts.append(value)
    combined = "\n".join(combined_parts)

    doc_codes = _as_string_list(record.get("doc_codes") or record.get("document_codes"))
    identifiers = _as_string_list(record.get("identifiers") or record.get("identifier"))
    dates = _as_string_list(record.get("dates") or record.get("document_dates"))

    for match in DOC_CODE_PATTERN.finditer(combined):
        number = match.group("number")
        suffix = match.group("suffix").replace(" ", "")
        if not re.search(r"[A-ZĐƠƯÂÊÔĂÁÀẢÃẠÉÈẺẼẸÍÌỈĨỊÓÒỎÕỌÚÙỦŨỤÝỲỶỸỴ]", suffix, flags=re.IGNORECASE):
            continue
        code = f"{number}/{suffix}"
        doc_codes.append(code)
        identifiers.extend([code, number, suffix])

    # Only add bare numbers when they appear near official-document words. This
    # avoids polluting payloads with page numbers/table rows while still catching
    # questions such as "3113" when the code appears in prose.
    date_spans = [match.span() for match in DATE_PATTERN.finditer(combined)]
    for match in BARE_IDENTIFIER_PATTERN.finditer(combined):
        number = match.group(0)
        if any(start <= match.start() < end for start, end in date_spans):
            continue
        window = combined[max(0, match.start() - 40) : match.end() + 80].casefold()
        if any(token in window for token in ("số", "văn bản", "công văn", "quyết định")):
            identifiers.append(number)

    dates.extend(DATE_PATTERN.findall(combined))
    return {
        "identifiers": _unique_preserve_order(identifiers),
        "doc_codes": _unique_preserve_order(doc_codes),
        "dates": _unique_preserve_order(dates),
    }


def _join_search_values(values: list[str]) -> str | None:
    clean = _unique_preserve_order(values)
    return " | ".join(clean) if clean else None


def normalize_embedding_input(text: str) -> str:
    if "<" not in text or ">" not in text:
        return text

    normalized = HTML_BLOCK_BOUNDARY_PATTERN.sub("\n", text)
    normalized = HTML_TAG_PATTERN.sub(" ", normalized)
    normalized = html.unescape(normalized)
    lines = [" ".join(line.split()) for line in normalized.splitlines()]
    clean = "\n".join(line for line in lines if line).strip()
    return clean or text


def _normalize_query_text(value: str) -> str:
    normalized = unicodedata.normalize("NFD", value or "")
    stripped = "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")
    stripped = stripped.replace("Đ", "D").replace("đ", "d")
    return re.sub(r"\s+", " ", stripped.casefold()).strip()

def _technology_profile_metadata_from_text(text: str) -> dict[str, Any]:
    person_match = re.search(r"(?im)^\s*Nhân sự\s*:\s*(?P<name>[^.\n]+)", text or "")
    if person_match is None:
        return {}

    person_name = " ".join(person_match.group("name").split()).strip(" .;:")
    if not person_name:
        return {}

    areas: list[dict[str, Any]] = []
    for line in (text or "").splitlines():
        match = re.match(
            r"\s*-\s*(?P<area>.+?)\s*;\s*phòng chủ trì\s*:\s*"
            r"(?P<department>[^.;\n]+)(?:\s*;\s*ghi chú\s*:\s*(?P<note>[^.\n]+))?\.??\s*$",
            line,
            flags=re.IGNORECASE,
        )
        if match is None:
            continue
        area = " ".join(match.group("area").split()).strip(" .;:")
        department = " ".join(match.group("department").split()).strip(" .;:")
        if not area or not department:
            continue
        payload: dict[str, Any] = {
            "area": area,
            "area_normalized": _normalize_query_text(area),
            "lead_department": department,
            "lead_department_normalized": _normalize_query_text(department),
        }
        note = match.group("note")
        if note:
            payload["role_note"] = " ".join(note.split()).strip(" .;:")
        areas.append(payload)

    if not areas:
        return {}

    area_names = [str(area["area"]) for area in areas]
    if len(area_names) == 1:
        joined_areas = area_names[0]
    else:
        joined_areas = ", ".join(area_names[:-1]) + " và " + area_names[-1]

    return {
        "chunk_type": "entity_profile",
        "entity_type": "person",
        "person_name": person_name,
        "person_name_normalized": _normalize_query_text(person_name),
        "areas": areas,
        "relationship_type": "technology_area_staff",
        "confidence": 0.95,
        "source_table": "Danh sách nhân sự phụ trách từng mảng công nghệ lõi",
        "answer_text": (
            f"{person_name} được đề xuất tham gia {len(areas):02d} mảng công nghệ: "
            f"{joined_areas}."
        ),
    }

def _recover_technology_area_metadata(text: str, record: dict[str, Any]) -> dict[str, Any]:
    """Recover trusted staff-area metadata from canonical text-only chunks."""

    if record.get("relationship_type") == "technology_area_staff":
        return {}

    chunk_type = str(record.get("chunk_type") or "")
    if chunk_type == "entity_profile" or re.search(r"(?im)^\s*Nhân sự\s*:", text or ""):
        return _technology_profile_metadata_from_text(text)

    if chunk_type != "table_row" and not re.search(r"(?im)^\s*STT\s*:\s*\d+", text or ""):
        return {}

    page_number = _optional_int(record.get("page_number") or record.get("page_start"))
    rows = parse_technology_area_rows_from_text(
        text,
        page_number=page_number,
        table_id=str(record.get("table_id") or "") or None,
        source_kind="pdf",
    )
    if len(rows) != 1:
        return {}
    row = rows[0]
    if not validate_technology_area_row(row):
        return {}
    metadata = row.to_metadata()
    metadata["chunk_type"] = "table_row"
    metadata["chunk_mode"] = record.get("chunk_mode") or "table_aware"
    metadata["chunk_overlap"] = 0
    metadata["overlap_applied"] = False
    return metadata


def build_query_embedding_text(query: str) -> str:
    """Build query embedding input without language/domain intent keywords.

    Identifier lookups keep an identifier-focused expansion because short codes
    are otherwise ambiguous for embedding models. All other queries are embedded
    exactly as written; structured context completion runs independently and is
    governed by evidence scoring, not by hardcoded Vietnamese intent phrases.
    """

    clean = " ".join(str(query or "").split()).strip()
    if not clean:
        return clean
    if DOC_CODE_PATTERN.search(clean) or re.fullmatch(r"\d{2,8}", clean):
        return "\n".join(
            [
                f"Mã tra cứu / số hiệu văn bản: {clean}",
                f"Identifier exact lookup: {clean}",
                clean,
            ]
        )
    return clean


def should_index_chunk(chunk: RagChunk) -> bool:
    return bool(
        chunk.indexable
        and chunk.embedding_enabled
        and chunk.quality_status.lower() not in FAILED_QUALITY_STATUSES
        and chunk.chunk_type.lower() not in NON_INDEXABLE_CHUNK_TYPES
        and chunk.text.strip()
    )


def build_embedding_text(chunk: RagChunk) -> str:
    """Build enriched dense/sparse embedding input for one chunk.

    The visible answer should still cite/use ``chunk.text``.  This embedding
    input deliberately adds compact labels for identifiers, document codes,
    dates and business metadata because dense embeddings are weak for short
    administrative codes such as ``3113``.
    """
    context_lines: list[str] = []
    text_prefix = " ".join(chunk.text.split())[:1800].casefold()

    def add_context(label: str, value: str | None) -> None:
        clean = " ".join(str(value or "").split()).strip()
        if not clean:
            return
        rendered = f"{label}: {clean}"
        # Do not drop identifier/date lines just because they already appear in
        # body text; repeated exact tokens are useful for sparse/dense retrieval.
        force_keep_labels = {
            "Số hiệu/mã",
            "Văn bản",
            "Ngày",
            "Màn hình",
            "Mảng công nghệ",
            "Nhân sự",
            "Hồ sơ nhân sự",
            "Các mảng công nghệ",
        }
        if label not in force_keep_labels and clean.casefold() in text_prefix:
            return
        if rendered.casefold() not in {line.casefold() for line in context_lines}:
            context_lines.append(rendered)

    add_context("Tài liệu", chunk.document_title)
    add_context("Cơ quan", chunk.issuer)
    add_context("Đơn vị", chunk.unit)
    add_context("Phạm vi", ", ".join(chunk.scope) if chunk.scope else None)
    add_context("Số hiệu/mã", _join_search_values(chunk.identifiers))
    add_context("Văn bản", _join_search_values(chunk.doc_codes))
    add_context("Ngày", _join_search_values(chunk.dates))
    add_context("Nền tảng", chunk.platform)
    add_context("Giai đoạn", chunk.phase)
    add_context("Loại thay đổi", chunk.change_type)
    add_context("Chủ đề thay đổi", chunk.change_topic)
    add_context("Loại nội dung", chunk.content_type)
    add_context("Màn hình", _join_search_values(chunk.screen_names))

    missing_headings = [
        heading
        for heading in chunk.section_path
        if " ".join(heading.split()).casefold() not in text_prefix
    ]
    add_context("Mục", " > ".join(missing_headings) if missing_headings else None)
    add_context("Bảng", chunk.table_name)
    add_context("Mô tả bảng", chunk.table_description)
    add_context("Cột bảng", ", ".join(chunk.table_columns) if chunk.table_columns else None)
    add_context("Thực thể", chunk.entity)
    add_context("Quan hệ", chunk.relationship_name)
    add_context("Mảng công nghệ", chunk.area)
    add_context("Phòng chủ trì", chunk.lead_department)
    add_context("Nhân sự", _join_search_values(chunk.staff_names))
    add_context("Hồ sơ nhân sự", chunk.person_name)
    if chunk.areas:
        area_labels = []
        for area_payload in chunk.areas:
            if not isinstance(area_payload, dict):
                continue
            area = str(area_payload.get("area") or "").strip()
            department = str(area_payload.get("lead_department") or "").strip()
            if not area:
                continue
            area_labels.append(f"{area} - {department}" if department else area)
        add_context("Các mảng công nghệ", _join_search_values(area_labels))
    add_context("Nguồn dữ liệu", ", ".join(chunk.source_systems) if chunk.source_systems else None)
    add_context("Tham chiếu đã giải quyết", chunk.resolved_reference_text)

    if chunk.row_start is not None or chunk.row_end is not None:
        start = chunk.row_start if chunk.row_start is not None else "?"
        end = chunk.row_end if chunk.row_end is not None else start
        add_context("Phạm vi hàng", f"{start}-{end}")

    embedding_text = chunk.embedding_text.strip() if chunk.embedding_text else None
    body = normalize_embedding_input(embedding_text or chunk.text.strip())
    if not context_lines:
        return body
    return "\n".join([*context_lines, "", body])


def stable_point_id(chunk: RagChunk) -> str:
    identity = ":".join(
        [
            chunk.tenant_id or "global",
            chunk.document_id,
            chunk.document_version or "v1",
            chunk.chunk_id,
            chunk.chunker_version or chunk.chunker,
            chunk.content_hash,
        ]
    )
    return str(uuid5(NAMESPACE_URL, identity))


def rag_chunk_from_record(
    record: dict[str, Any],
    *,
    document_id: UUID | str,
    source_file: str,
    source_uri: str | None,
    document_title: str | None,
    document_version: str | None,
    tenant_id: UUID | str | None,
    parser: str,
    parser_version: str | None,
    chunker: str,
    chunker_version: str | None,
    chunk_index: int,
) -> RagChunk:
    text = str(record.get("text") or record.get("content") or "").strip()
    raw_text = record.get("raw_text")
    recovered_metadata = _recover_technology_area_metadata(text, dict(record))
    metadata = {**recovered_metadata, **dict(record)}
    record = metadata
    headings = normalize_section_path(record.get("headings") or record.get("section_path"))
    pages = sorted(
        {
            int(page)
            for page in (record.get("pages") or record.get("page_range") or [])
            if isinstance(page, int) or str(page).isdigit()
        }
    )
    content_format = str(
        record.get("content_format") or infer_content_format(text, metadata)
    )
    inferred_table_name, inferred_table_description, inferred_row_start, inferred_row_end = (
        infer_table_metadata(text, headings)
        if content_format == "markdown_table"
        else (None, None, None, None)
    )
    table_name = record.get("table_name") or record.get("object_code") or inferred_table_name
    search_metadata = extract_search_metadata(text, record)
    chunk_type = str(record.get("chunk_type") or "text")
    if content_format == "markdown_table" and chunk_type == "docling_hybrid_repaired":
        chunk_type = "table_rows"
    indexable = bool(record.get("indexable", chunk_type not in NON_INDEXABLE_CHUNK_TYPES))
    embedding_enabled = bool(
        record.get("embedding_enabled", chunk_type not in NON_INDEXABLE_CHUNK_TYPES)
    )
    section_id = record.get("section_id")
    if section_id is None and headings:
        match = re.match(r"^(\d+(?:\.\d+)*)\.", headings[-1])
        section_id = match.group(1) if match else None
    parent_section_id = record.get("parent_section_id")
    if parent_section_id is None and isinstance(section_id, str) and "." in section_id:
        parent_section_id = section_id.rsplit(".", 1)[0]

    return RagChunk(
        chunk_id=str(record.get("chunk_id") or f"chunk_{chunk_index:04d}"),
        document_id=str(document_id),
        document_version=document_version,
        tenant_id=str(tenant_id) if tenant_id else None,
        chunk_type=chunk_type,
        content_format=content_format,
        text=text,
        raw_text=str(raw_text) if raw_text is not None else None,
        source_raw_text=(
            str(record["source_raw_text"]) if record.get("source_raw_text") is not None else None
        ),
        normalized_text=(
            str(record["normalized_text"]) if record.get("normalized_text") is not None else None
        ),
        provenance_status=str(record.get("provenance_status") or "").strip() or None,
        section_path=headings,
        section_id=str(section_id) if section_id is not None else None,
        parent_section_id=(
            str(parent_section_id) if parent_section_id is not None else None
        ),
        parent_chunk_id=(
            str(record["parent_chunk_id"]) if record.get("parent_chunk_id") else None
        ),
        chapter_number=str(record.get("chapter_number") or "").strip() or None,
        chapter_title=str(record.get("chapter_title") or "").strip() or None,
        article_number=str(record.get("article_number") or "").strip() or None,
        article_title=str(record.get("article_title") or "").strip() or None,
        article_part=_optional_int(record.get("article_part")),
        article_part_total=_optional_int(record.get("article_part_total")),
        subchunk_index=_optional_int(record.get("subchunk_index")),
        subchunk_total=_optional_int(record.get("subchunk_total")),
        unit=str(record.get("unit") or "").strip() or None,
        scope=normalize_scope(record.get("scope")),
        pages=pages,
        page_start=min(pages) if pages else None,
        page_end=max(pages) if pages else None,
        table_name=str(table_name).strip() if table_name else None,
        table_description=(
            str(
                record.get("table_description")
                or record.get("description")
                or inferred_table_description
                or ""
            ).strip()
            or None
        ),
        row_start=_optional_int(record.get("row_start")) or inferred_row_start,
        row_end=_optional_int(record.get("row_end")) or inferred_row_end,
        table_columns=list(record.get("table_columns") or infer_table_columns(text)),
        case_code=str(record.get("case_code") or "").strip() or None,
        case_name=str(record.get("case_name") or "").strip() or None,
        labor_code_benefit=str(record.get("labor_code_benefit") or "").strip() or None,
        collective_agreement_benefit=(
            str(record.get("collective_agreement_benefit") or "").strip() or None
        ),
        total_leave_benefit=str(record.get("total_leave_benefit") or "").strip() or None,
        total_leave_days=_optional_int(record.get("total_leave_days")),
        benefit_source=str(record.get("benefit_source") or "").strip() or None,
        row_text=str(record.get("row_text") or "").strip() or None,
        entity=str(record.get("entity") or "").strip() or None,
        field_names=[str(value) for value in record.get("field_names") or []],
        source_systems=[str(value) for value in record.get("source_systems") or []],
        convertible_fields=[
            str(value) for value in record.get("convertible_fields") or []
        ],
        relationship_name=str(record.get("relationship_name") or "").strip() or None,
        relationship_type=str(record.get("relationship_type") or "").strip() or None,
        stt=str(record.get("stt") or "").strip() or None,
        area=str(record.get("area") or "").strip() or None,
        area_normalized=str(record.get("area_normalized") or "").strip() or None,
        lead_department=str(record.get("lead_department") or "").strip() or None,
        lead_department_normalized=(
            str(record.get("lead_department_normalized") or "").strip() or None
        ),
        staff_names=[str(value) for value in record.get("staff_names") or []],
        staff=[dict(value) for value in record.get("staff") or [] if isinstance(value, dict)],
        assignment_type=str(record.get("assignment_type") or "").strip() or None,
        has_specific_person=(
            bool(record.get("has_specific_person"))
            if record.get("has_specific_person") is not None
            else None
        ),
        source_table=str(record.get("source_table") or "").strip() or None,
        confidence=_optional_float(record.get("confidence")),
        person_name=str(record.get("person_name") or "").strip() or None,
        person_name_normalized=(
            str(record.get("person_name_normalized") or "").strip() or None
        ),
        areas=[dict(value) for value in record.get("areas") or [] if isinstance(value, dict)],
        answer_text=str(record.get("answer_text") or "").strip() or None,
        source_row_id=str(record.get("source_row_id") or "").strip() or None,
        canonical_text=str(record.get("canonical_text") or "").strip() or None,
        source_entity=str(record.get("source_entity") or "").strip() or None,
        source_key=str(record.get("source_key") or "").strip() or None,
        target_table=str(record.get("target_table") or "").strip() or None,
        target_key=str(record.get("target_key") or "").strip() or None,
        cardinality=str(record.get("cardinality") or "").strip() or None,
        cross_references=[str(value) for value in record.get("cross_references") or []],
        resolved_reference_text=(
            str(record["resolved_reference_text"])
            if record.get("resolved_reference_text") is not None
            else None
        ),
        source_file=source_file,
        source_uri=source_uri,
        document_title=document_title,
        issuer=str(record.get("issuer") or "").strip() or None,
        identifiers=search_metadata["identifiers"],
        doc_codes=search_metadata["doc_codes"],
        dates=search_metadata["dates"],
        platform=str(record.get("platform") or "").strip() or None,
        phase=str(record.get("phase") or "").strip() or None,
        change_type=str(record.get("change_type") or "").strip() or None,
        content_type=str(record.get("content_type") or "").strip() or None,
        change_topic=str(record.get("change_topic") or "").strip() or None,
        screen_names=_as_string_list(record.get("screen_names") or record.get("screens")),
        embedding_text=(
            str(record["embedding_text"]).strip()
            if record.get("embedding_text") is not None
            else None
        ),
        enriched=bool(record.get("enriched")),
        enrichment_summary=str(record.get("enrichment_summary") or "").strip() or None,
        enrichment_keywords=_as_string_list(record.get("enrichment_keywords")),
        document_code=str(record.get("document_code") or "").strip() or None,
        issued_date=str(record.get("issued_date") or "").strip() or None,
        document_type=str(record.get("document_type") or "").strip() or None,
        structure_path=str(record.get("structure_path") or "").strip() or None,
        parser=parser,
        parser_version=parser_version,
        chunker=chunker,
        chunker_version=chunker_version,
        token_count=_optional_int(
            record.get("token_count") or record.get("token_count_approx")
        ),
        quality_status=str(record.get("quality_status") or "pass"),
        validation_issues=list(record.get("validation_issues") or []),
        indexable=indexable,
        embedding_enabled=embedding_enabled,
        content_hash=sha256_text(text),
        chunk_index=chunk_index,
    )


def rag_chunk_from_database(
    chunk: Any,
    *,
    document: Any,
    source_file: str,
    source_uri: str | None,
) -> RagChunk:
    metadata = dict(getattr(chunk, "chunk_metadata", None) or {})
    enrichment = dict(metadata.get("enrichment") or {})
    enriched_content = str(getattr(chunk, "enriched_content", "") or "").strip()
    record = {
        **metadata,
        "chunk_id": metadata.get("chunk_id") or f"chunk_{chunk.chunk_index:04d}",
        "text": str(getattr(chunk, "content", "")),
        "token_count": getattr(chunk, "token_count", None),
        "enriched": bool(enriched_content),
        "enrichment_summary": enrichment.get("summary"),
        "enrichment_keywords": enrichment.get("keywords"),
        "document_code": enrichment.get("document_code"),
        "issued_date": enrichment.get("issued_date"),
        "document_type": enrichment.get("document_type"),
        "structure_path": enrichment.get("structure_path"),
    }
    if enriched_content:
        record["embedding_text"] = enriched_content
    document_metadata = dict(getattr(document, "document_metadata", None) or {})
    parsed_metadata = dict(document_metadata.get("parsed_metadata") or {})
    if not record.get("issuer") and document_metadata.get("issuer"):
        record["issuer"] = document_metadata.get("issuer")
    rag_chunk = rag_chunk_from_record(
        record,
        document_id=document.id,
        source_file=source_file,
        source_uri=source_uri,
        document_title=getattr(document, "title", None),
        document_version=str(document_metadata.get("document_version") or "v1"),
        tenant_id=getattr(document, "organization_id", None),
        parser=str(document_metadata.get("parser") or metadata.get("parser") or "unknown"),
        parser_version=(
            parsed_metadata.get("parser_version")
            or document_metadata.get("parser_version")
        ),
        chunker=str(metadata.get("chunker") or metadata.get("chunk_strategy") or "unknown"),
        chunker_version=metadata.get("chunker_version"),
        chunk_index=int(getattr(chunk, "chunk_index", 0)),
    )
    flat_access = flatten_access_payload(
        normalize_access_payload(metadata.get("access") if isinstance(metadata, dict) else {})
    )
    flat_access.pop("scope", None)
    return rag_chunk.model_copy(
        update={
            "database_chunk_id": str(chunk.id),
            "organization_id": (
                str(document.organization_id)
                if getattr(document, "organization_id", None)
                else None
            ),
            "knowledge_base_id": (
                str(document.knowledge_base_id)
                if getattr(document, "knowledge_base_id", None)
                else None
            ),
            "uploaded_by_user_id": (
                str(document.uploaded_by_user_id)
                if getattr(document, "uploaded_by_user_id", None)
                else None
            ),
            "visibility": getattr(document, "visibility", None),
            **flat_access,
            "access": normalize_access_payload(
                metadata.get("access") if isinstance(metadata, dict) else {}
            ),
        }
    )


def qdrant_payload(chunk: RagChunk, *, store_raw_text: bool = False) -> dict[str, Any]:
    payload = chunk.model_dump(
        mode="json",
        exclude={"raw_text", "source_raw_text", "normalized_text", "embedding_text"},
        exclude_none=True,
    )
    if store_raw_text and chunk.raw_text is not None:
        payload["raw_text"] = chunk.raw_text
    payload["chunk_id"] = chunk.database_chunk_id or chunk.chunk_id
    payload["semantic_chunk_id"] = chunk.chunk_id
    access = normalize_access_payload(payload.get("access") or {})
    payload["access"] = access
    payload.update(flatten_access_payload(access))
    return payload


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None

def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
