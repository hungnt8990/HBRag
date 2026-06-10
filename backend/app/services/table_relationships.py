from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

STAFF_TABLE_REQUIRED_HEADERS = (
    "stt",
    "mang cong nghe",
    "phong chu tri",
    "nhan su de xuat",
)
DEPARTMENT_RE = re.compile(r"\b[A-ZĐ]{2,10}[A-Z0-9Đ/-]*\b")
ROW_START_RE = re.compile(r"^\s*(?P<stt>\d{1,3})(?!\.)\s+(?P<body>.+)$")
STAFF_ITEM_RE = re.compile(
    r"(?:^|\s)(?P<index>\d{1,2})\.\s+(?P<name>.+?)(?=(?:\s+\d{1,2}\.\s+)|$)",
    flags=re.DOTALL,
)
ROLE_NOTE_RE = re.compile(r"^(?P<name>.+?)\s*\((?P<note>[^)]+)\)\s*$")
CAPITALIZED_NAME_RE = re.compile(
    r"\b(?:[A-ZÀ-ỴĐ][\wÀ-ỹĐđ]+)(?:\s+[A-ZÀ-ỴĐ][\wÀ-ỹĐđ]+){1,5}\b"
)
YES_NO_CUES = ("đúng không", "co dung khong", "có đúng không", "tham gia")
PERSON_STOP_PREFIXES = {
    "co",
    "có",
    "danh",
    "mang",
    "mảng",
    "phong",
    "phòng",
    "xay",
    "xây",
}
SOURCE_TABLE_DEFAULT = "Danh sách nhân sự phụ trách từng mảng công nghệ lõi"


@dataclass(frozen=True)
class StaffMember:
    name: str
    role_note: str | None = None

    def to_dict(self) -> dict[str, str | None]:
        return {"name": self.name, "role_note": self.role_note}


@dataclass(frozen=True)
class TechnologyAreaRow:
    stt: str
    area: str
    lead_department: str
    proposed_staff: list[StaffMember]
    page_number: int | None = None
    source_table: str = SOURCE_TABLE_DEFAULT
    table_id: str | None = None
    raw_text: str | None = None
    confidence: float = 1.0

    @property
    def staff_names(self) -> list[str]:
        return [staff.name for staff in self.proposed_staff]

    def to_text(self) -> str:
        staff_text = "; ".join(
            f"{staff.name} ({staff.role_note})" if staff.role_note else staff.name
            for staff in self.proposed_staff
        )
        return (
            f"STT: {self.stt}\n"
            f"Mảng công nghệ: {self.area}\n"
            f"Phòng chủ trì: {self.lead_department}\n"
            f"Nhân sự đề xuất: {staff_text}"
        )

    def to_metadata(self) -> dict[str, Any]:
        metadata: dict[str, Any] = {
            "stt": self.stt,
            "area": self.area,
            "lead_department": self.lead_department,
            "staff_names": self.staff_names,
            "staff": [staff.to_dict() for staff in self.proposed_staff],
            "source_table": self.source_table,
            "confidence": self.confidence,
            "relationship_type": "technology_area_staff",
        }
        if self.table_id:
            metadata["table_id"] = self.table_id
        if self.page_number is not None:
            metadata["page_number"] = self.page_number
        if self.raw_text:
            metadata["raw_text"] = self.raw_text
        return metadata


@dataclass(frozen=True)
class PersonAreaMembershipQuery:
    intent: str
    person_candidate: str | None = None
    area_candidate: str | None = None


@dataclass
class _OpenRow:
    stt: str
    parts: list[str] = field(default_factory=list)


def looks_like_staff_area_table(text: str) -> bool:
    normalized = _normalize_for_match(text)
    header_hits = sum(1 for header in STAFF_TABLE_REQUIRED_HEADERS if header in normalized)
    return header_hits >= 3 or (
        "nhan su" in normalized and "mang cong nghe" in normalized and "phong" in normalized
    )


def parse_technology_area_rows_from_table(
    rows: list[list[str]],
    *,
    page_number: int | None = None,
    table_id: str | None = None,
    source_table: str | None = None,
) -> list[TechnologyAreaRow]:
    if len(rows) < 2:
        return []

    header_index = _find_header_row_index(rows)
    if header_index is None:
        return []

    headers = rows[header_index]
    column_map = _column_map(headers)
    if not {"stt", "area", "lead_department", "staff"}.issubset(column_map):
        return []

    parsed_rows: list[TechnologyAreaRow] = []
    for raw_row in rows[header_index + 1 :]:
        stt = _cell(raw_row, column_map["stt"])
        area = _cell(raw_row, column_map["area"])
        lead_department = _cell(raw_row, column_map["lead_department"])
        staff_text = _cell(raw_row, column_map["staff"])
        if not stt or not area or not lead_department or not staff_text:
            continue
        staff = parse_staff_members(staff_text)
        if not staff:
            continue
        raw_text = " | ".join(_clean_text(cell) for cell in raw_row if _clean_text(cell))
        parsed_rows.append(
            TechnologyAreaRow(
                stt=stt,
                area=area,
                lead_department=lead_department,
                proposed_staff=staff,
                page_number=page_number,
                source_table=source_table or SOURCE_TABLE_DEFAULT,
                table_id=table_id,
                raw_text=raw_text,
                confidence=0.95,
            )
        )
    return parsed_rows


def parse_technology_area_rows_from_text(
    text: str,
    *,
    page_number: int | None = None,
    table_id: str | None = None,
    source_table: str | None = None,
) -> list[TechnologyAreaRow]:
    if not looks_like_staff_area_table(text):
        return []

    lines = [_clean_text(line) for line in text.splitlines()]
    lines = [line for line in lines if line]
    header_index = _find_text_header_index(lines)
    if header_index is None:
        return []

    title = source_table or _infer_source_table(lines, header_index)
    open_rows: list[_OpenRow] = []
    current: _OpenRow | None = None
    for line in lines[header_index + 1 :]:
        row_match = ROW_START_RE.match(line)
        if row_match:
            if current is not None:
                open_rows.append(current)
            current = _OpenRow(stt=row_match.group("stt"), parts=[row_match.group("body")])
            continue
        if current is not None:
            current.parts.append(line)
    if current is not None:
        open_rows.append(current)

    parsed_rows: list[TechnologyAreaRow] = []
    for open_row in open_rows:
        parsed = _parse_open_text_row(
            open_row,
            page_number=page_number,
            table_id=table_id,
            source_table=title,
        )
        if parsed is not None:
            parsed_rows.append(parsed)
    return parsed_rows


def parse_staff_members(staff_text: str) -> list[StaffMember]:
    normalized = _clean_text(staff_text)
    members: list[StaffMember] = []
    matches = list(STAFF_ITEM_RE.finditer(normalized))
    if matches:
        for match in matches:
            member = _parse_staff_member(match.group("name"))
            if member is not None:
                members.append(member)
        return members

    for part in re.split(r"[;\n]+", staff_text):
        member = _parse_staff_member(part)
        if member is not None:
            members.append(member)
    return members


def build_entity_profile_chunks(
    row_chunks: list[dict[str, Any]],
    *,
    start_index: int = 0,
) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row_chunk in row_chunks:
        metadata = dict(row_chunk.get("metadata", {}) or {})
        for staff in metadata.get("staff", []) or []:
            if not isinstance(staff, dict):
                continue
            name = str(staff.get("name") or "").strip()
            if name:
                grouped.setdefault(name, []).append(row_chunk)

    chunks: list[dict[str, Any]] = []
    for person_name, related_rows in sorted(grouped.items()):
        areas: list[dict[str, Any]] = []
        source_tables: set[str] = set()
        table_ids: set[str] = set()
        page_numbers: set[int] = set()
        lines = [
            f"Nhân sự: {person_name}.",
            f"{person_name} được đề xuất tham gia các mảng công nghệ:",
        ]
        seen_area_keys: set[tuple[str, str, str]] = set()
        for row_chunk in related_rows:
            metadata = dict(row_chunk.get("metadata", {}) or {})
            area = str(metadata.get("area") or "").strip()
            department = str(metadata.get("lead_department") or "").strip()
            stt = str(metadata.get("stt") or "").strip()
            role_note = _role_note_for_person(metadata, person_name)
            key = (stt, area, department)
            if key in seen_area_keys or not area:
                continue
            seen_area_keys.add(key)
            area_payload: dict[str, Any] = {
                "area": area,
                "lead_department": department,
                "stt": stt,
            }
            if role_note:
                area_payload["role_note"] = role_note
            if metadata.get("table_id") is not None:
                area_payload["table_id"] = metadata.get("table_id")
                table_ids.add(str(metadata.get("table_id")))
            if metadata.get("page_number") is not None:
                page_number = int(metadata["page_number"])
                area_payload["page_number"] = page_number
                page_numbers.add(page_number)
            areas.append(area_payload)
            role_fragment = f"; ghi chú: {role_note}" if role_note else ""
            lines.append(f"- {area}; phòng chủ trì: {department}{role_fragment}.")
            if metadata.get("source_table"):
                source_tables.add(str(metadata["source_table"]))

        if not areas:
            continue
        metadata: dict[str, Any] = {
            "chunk_type": "entity_profile",
            "chunk_mode": "table_aware",
            "entity_type": "person",
            "person_name": person_name,
            "areas": areas,
            "source_table": sorted(source_tables)[0] if source_tables else SOURCE_TABLE_DEFAULT,
            "table_ids": sorted(table_ids),
            "page_numbers": sorted(page_numbers),
        }
        if page_numbers:
            metadata["page_range"] = [min(page_numbers), max(page_numbers)]
        chunks.append(
            {
                "chunk_index": start_index + len(chunks),
                "content": "\n".join(lines),
                "metadata": metadata,
            }
        )
    return chunks


def analyze_person_area_membership_query(query: str) -> PersonAreaMembershipQuery | None:
    normalized = _normalize_for_match(query)
    if not any(cue in normalized for cue in YES_NO_CUES):
        return None

    person = _extract_person_candidate(query)
    area = _extract_area_candidate(query, person)
    if not person or not area:
        return None
    return PersonAreaMembershipQuery(
        intent="person_area_membership",
        person_candidate=person,
        area_candidate=area,
    )


def score_person_area_membership_match(
    query: PersonAreaMembershipQuery | None,
    *,
    content: str,
    metadata: dict[str, Any],
) -> float:
    if query is None:
        return 0.0

    person = query.person_candidate or ""
    area = query.area_candidate or ""
    person_norm = _normalize_for_match(person)
    area_norm = _normalize_for_match(area)
    content_norm = _normalize_for_match(content)
    metadata_norm = _normalize_for_match(_metadata_search_text(metadata))

    person_match = bool(person_norm) and (
        person_norm in content_norm or person_norm in metadata_norm
    )
    area_match = bool(area_norm) and (
        _loose_contains(content_norm, area_norm)
        or _loose_contains(metadata_norm, area_norm)
    )
    if not person_match and not area_match:
        return 0.0

    chunk_type = str(metadata.get("chunk_type") or "")
    score = 0.0
    if person_match:
        score += 2.0
    if area_match:
        score += 2.0
    if person_match and area_match:
        score += 6.0
    if chunk_type == "entity_profile":
        score += 4.0
    elif chunk_type == "table_row":
        score += 3.0
    elif chunk_type in {"table_block", "entity_summary"}:
        score += 1.0
    return score


def row_to_chunk(row: TechnologyAreaRow, *, chunk_index: int = 0) -> dict[str, Any]:
    metadata = row.to_metadata()
    metadata["chunk_type"] = "table_row"
    metadata["chunk_mode"] = "table_aware"
    return {"chunk_index": chunk_index, "content": row.to_text(), "metadata": metadata}


def _parse_open_text_row(
    row: _OpenRow,
    *,
    page_number: int | None,
    table_id: str | None,
    source_table: str,
) -> TechnologyAreaRow | None:
    raw_text = _clean_text(" ".join(row.parts))
    staff_start = _first_staff_marker(raw_text)
    prefix = raw_text[:staff_start].strip() if staff_start is not None else raw_text
    staff_text = raw_text[staff_start:].strip() if staff_start is not None else ""
    department_match = _last_department_match(prefix)
    if department_match is None:
        return None

    area = prefix[: department_match.start()].strip(" -;:|/")
    lead_department = department_match.group(0).strip()
    if staff_start is None:
        staff_text = prefix[department_match.end() :].strip()
    staff = parse_staff_members(staff_text)
    confidence = 0.85 if area and lead_department and staff else 0.55
    if not area or not lead_department or not staff:
        return None
    return TechnologyAreaRow(
        stt=row.stt,
        area=area,
        lead_department=lead_department,
        proposed_staff=staff,
        page_number=page_number,
        source_table=source_table,
        table_id=table_id,
        raw_text=f"{row.stt} {raw_text}",
        confidence=confidence,
    )


def _find_header_row_index(rows: list[list[str]]) -> int | None:
    for index, row in enumerate(rows[:5]):
        normalized = _normalize_for_match(" ".join(row))
        if sum(1 for header in STAFF_TABLE_REQUIRED_HEADERS if header in normalized) >= 3:
            return index
    return None


def _find_text_header_index(lines: list[str]) -> int | None:
    for index, line in enumerate(lines):
        if looks_like_staff_area_table(line):
            return index
    return None


def _column_map(headers: list[str]) -> dict[str, int]:
    mapping: dict[str, int] = {}
    for index, header in enumerate(headers):
        normalized = _normalize_for_match(header)
        if "stt" in normalized:
            mapping["stt"] = index
        elif "mang" in normalized and "cong nghe" in normalized:
            mapping["area"] = index
        elif "phong" in normalized and "chu tri" in normalized:
            mapping["lead_department"] = index
        elif "nhan su" in normalized and "de xuat" in normalized:
            mapping["staff"] = index
    return mapping


def _cell(row: list[str], index: int) -> str:
    if index >= len(row):
        return ""
    return _clean_text(row[index])


def _clean_text(value: str) -> str:
    return " ".join(str(value or "").replace("\r", " ").replace("\n", " ").split())


def _normalize_for_match(value: str) -> str:
    decomposed = unicodedata.normalize("NFD", str(value or ""))
    without_marks = "".join(char for char in decomposed if unicodedata.category(char) != "Mn")
    return " ".join(without_marks.replace("Đ", "D").replace("đ", "d").casefold().split())


def _first_staff_marker(text: str) -> int | None:
    match = re.search(r"(?:^|\s)1\.\s+", text)
    return match.start() if match else None


def _last_department_match(text: str) -> re.Match[str] | None:
    matches = list(DEPARTMENT_RE.finditer(text))
    return matches[-1] if matches else None


def _parse_staff_member(value: str) -> StaffMember | None:
    cleaned = _clean_text(value).strip(" .;,-")
    if not cleaned:
        return None
    role_match = ROLE_NOTE_RE.match(cleaned)
    if role_match:
        return StaffMember(
            name=_clean_text(role_match.group("name")).strip(" .;,-"),
            role_note=_clean_text(role_match.group("note")) or None,
        )
    return StaffMember(name=cleaned, role_note=None)


def _infer_source_table(lines: list[str], header_index: int) -> str:
    for line in reversed(lines[:header_index]):
        normalized = _normalize_for_match(line)
        if "danh sach" in normalized and "nhan su" in normalized:
            return line
    return SOURCE_TABLE_DEFAULT


def _role_note_for_person(metadata: dict[str, Any], person_name: str) -> str | None:
    for staff in metadata.get("staff", []) or []:
        if not isinstance(staff, dict):
            continue
        if _normalize_for_match(str(staff.get("name") or "")) == _normalize_for_match(person_name):
            note = staff.get("role_note")
            return str(note) if note else None
    return None


def _extract_person_candidate(query: str) -> str | None:
    before_membership = re.split(r"\btham\s+gia\b", query, maxsplit=1, flags=re.IGNORECASE)[0]
    candidates = CAPITALIZED_NAME_RE.findall(before_membership or query)
    for candidate in candidates:
        normalized = _normalize_for_match(candidate)
        first = normalized.split()[0] if normalized else ""
        if first not in PERSON_STOP_PREFIXES:
            return _clean_text(candidate).strip(" ?!.,;:")
    return None


def _extract_area_candidate(query: str, person: str | None) -> str | None:
    area = query
    if person:
        area = re.sub(re.escape(person), "", area, count=1, flags=re.IGNORECASE)
    area = re.sub(r"\btham\s+gia\b", "", area, flags=re.IGNORECASE)
    area = re.sub(r"\b(có|co)\s+đúng\s+không\b", "", area, flags=re.IGNORECASE)
    area = re.sub(r"\b(đúng|dung)\s+không\b", "", area, flags=re.IGNORECASE)
    area = _clean_text(area).strip(" ?!.,;:")
    return area if len(area) >= 8 else None


def _metadata_search_text(metadata: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in ("person_name", "area", "lead_department", "source_table"):
        value = metadata.get(key)
        if value is not None:
            parts.append(str(value))
    for name in metadata.get("staff_names", []) or []:
        parts.append(str(name))
    for area in metadata.get("areas", []) or []:
        if isinstance(area, dict):
            parts.extend(str(area.get(key) or "") for key in ("area", "lead_department"))
    return "\n".join(parts)


def _loose_contains(haystack: str, needle: str) -> bool:
    if needle in haystack:
        return True
    needle_terms = [term for term in needle.split() if len(term) >= 3]
    if not needle_terms:
        return False
    matches = sum(1 for term in needle_terms if term in haystack)
    return matches >= max(2, int(len(needle_terms) * 0.6))
