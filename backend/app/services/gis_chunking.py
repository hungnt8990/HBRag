from __future__ import annotations

import re
from typing import Any

from app.services.parsers import ParsedElement
from app.services.table_relationships import normalize_metadata_value

GIS_OBJECT_RE = re.compile(
    r"^\s*\((?P<order>\d+)\)\s+(?P<object_code>F\d+_[A-Za-z0-9_]+)\s*[–-]\s*(?P<object_name>.+?)\s*[.;]?\s*$"
)
OBJECT_CODE_RE = re.compile(r"\bF\d{2}_[A-Za-z0-9_]+\b")
ATTRIBUTE_TABLE_NAMES = ("HinhAnhCotDien", "HinhAnhKhachHang", "HinhAnhHoSoKhachHang")
RELATIONSHIP_NAMES = (
    "PXXXXX_CotDien_HT_HinhAnhCotDien",
    "PXXXXX_CongToKhachHang_HT_HinhAnhKhachHang",
    "PXXXXX_CongToKhachHang_HT_HinhAnhHoSoKhachHang",
)
STRUCTURED_NO_OVERLAP_CHUNK_TYPES = {
    "procedure_table_row",
    "schema_field_row",
    "schema_object_summary",
    "attribute_table_schema",
    "gis_relationship_schema",
}

DATA_TYPE_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Short Integer", ("short", "integer")),
    ("Long Integer", ("long", "integer")),
    ("Double", ("double",)),
    ("Float", ("float",)),
    ("Number", ("number",)),
    ("Integer", ("integer",)),
    ("Date", ("date",)),
    ("Datetime", ("datetime",)),
    ("Boolean", ("boolean",)),
    ("Text", ("text",)),
)
SOURCE_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("ID tự sinh của GIS", ("id", "tu", "sinh", "cua", "gis")),
    ("PMIS/Biên tập", ("pmis", "bien", "tap")),
    ("Biên tập", ("bien", "tap")),
    ("TTHT", ("ttht",)),
    ("CMIS", ("cmis",)),
    ("PMIS", ("pmis",)),
)


def robust_normalize(value: str) -> str:
    text = normalize_metadata_value(value)
    replacements = {
        "phá»¥": "phu",
        "lá»¥c": "luc",
        "dá»¯": "du",
        "liá»‡u": "lieu",
        "trÆ°á»ng": "truong",
        "mÃ´": "mo",
        "táº£": "ta",
        "kiá»ƒu": "kieu",
        "miá»n": "mien",
        "giÃ¡": "gia",
        "trá»‹": "tri",
        "Ä‘á»™": "do",
        "rá»™ng": "rong",
        "nguá»“n": "nguon",
        "chuyá»ƒn": "chuyen",
        "Ä‘á»•i": "doi",
        "sang": "sang",
        "thÃ¡ng": "thang",
        "quÃ½": "quy",
        "trÆ°á»›c": "truoc",
        "ngÃ y": "ngay",
        "biÃªn": "bien",
        "táº­p": "tap",
        "háº¡": "ha",
        "tháº¿": "the",
        "trung tháº¿": "trung the",
    }
    for bad, good in replacements.items():
        text = text.replace(bad, good)
    return " ".join(text.split())


def normalize_code(value: str) -> str:
    return robust_normalize(value).replace(" ", "_")


def extract_deadlines(text: str) -> list[str]:
    patterns = (
        r"tháng\s+0?\d{1,2}/\d{4}",
        r"trước\s+ngày\s+\d{1,2}/\d{1,2}/\d{4}",
        r"quý\s+0?\d{1,2}/\d{4}",
        r"thÃ¡ng\s+0?\d{1,2}/\d{4}",
        r"trÆ°á»›c\s+ngÃ y\s+\d{1,2}/\d{1,2}/\d{4}",
        r"quÃ½\s+0?\d{1,2}/\d{4}",
    )
    seen: set[str] = set()
    deadlines: list[str] = []
    for pattern in patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            value = " ".join(match.group(0).split())
            key = robust_normalize(value)
            if key not in seen:
                seen.add(key)
                deadlines.append(value)
    return deadlines


def parse_procedure_rows(
    text: str,
    page_range: list[int],
    parsed_elements: list[ParsedElement] | None = None,
) -> list[dict[str, Any]]:
    rows = _procedure_rows_from_elements(parsed_elements or [], page_range)
    if rows:
        return rows

    rows = []
    for line in text.splitlines():
        cells = _split_pipe_line(line)
        if len(cells) >= 4 and cells[0].isdigit():
            rows.append(_procedure_row(cells[0], cells[1], cells[2], cells[3], page_range))
    if rows:
        return rows

    return _procedure_rows_from_text_state_machine(text, page_range)


def parse_schema_objects(
    text: str,
    page_range: list[int],
    parsed_elements: list[ParsedElement] | None = None,
) -> list[dict[str, Any]]:
    objects = _schema_objects_from_text(text, page_range)
    element_objects = _schema_objects_from_elements(text, parsed_elements or [], page_range)
    merged = {obj["object_code"]: obj for obj in objects}
    for obj in element_objects:
        existing = merged.get(obj["object_code"])
        if existing is None or len(obj["fields"]) > len(existing["fields"]):
            merged[obj["object_code"]] = obj
    return list(merged.values())


def parse_attribute_tables(text: str, page_range: list[int]) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    for index, table_name in enumerate(ATTRIBUTE_TABLE_NAMES):
        start = _find_table_name_start(text, table_name)
        if start < 0:
            continue
        next_starts = [
            _find_table_name_start(text, candidate)
            for candidate in ATTRIBUTE_TABLE_NAMES[index + 1 :]
        ]
        next_starts = [candidate for candidate in next_starts if candidate > start]
        rel_start = _find_normalized(text, "4. khoi tao bo sung 03 moi quan he")
        if rel_start > start:
            next_starts.append(rel_start)
        end = min(next_starts) if next_starts else len(text)
        section = text[start:end]
        description = _extract_attribute_description(section, table_name)
        fields = _parse_attribute_fields(section)
        chunks.append(
            {
                "table_name": table_name,
                "description": description,
                "fields": fields,
                "page_range": page_range,
            }
        )
    return chunks


def parse_relationship_schemas(text: str, page_range: list[int]) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    for name in RELATIONSHIP_NAMES:
        start = text.find(name)
        if start < 0:
            continue
        section = text[start:] if start >= 0 else ""
        source_layer = _relationship_source_layer(name, section)
        target_table = _relationship_target_table(name, section)
        target_key = _relationship_target_key(target_table, section)
        chunks.append(
            {
                "relationship_name": name,
                "source_layer": source_layer,
                "source_key": "ID",
                "target_table": target_table,
                "target_key": target_key,
                "cardinality": "1-M",
                "page_range": page_range,
            }
        )
    return chunks


def _procedure_rows_from_elements(
    elements: list[ParsedElement], page_range: list[int]
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for element in elements:
        if element.element_type != "table_row":
            continue
        metadata = element.metadata or {}
        headers = [robust_normalize(str(header)) for header in metadata.get("headers") or []]
        values = [str(value or "") for value in metadata.get("values") or []]
        if len(headers) < 4 or len(values) < 4:
            continue
        if not any("du lieu" in header for header in headers):
            continue
        if not any("cpcit" in header for header in headers):
            continue
        if not any("ctdl" in header or "kho" in header for header in headers):
            continue
        row = _row_by_headers(headers, values)
        tt = row.get("tt") or values[0]
        data_type = row.get("du lieu") or values[1]
        cpcit = row.get("cpcit") or values[2]
        ctdl = row.get("cac ctdl") or row.get("ctdl") or values[3]
        rows.append(
            _procedure_row(
                tt,
                data_type,
                cpcit,
                ctdl,
                [element.page_number or page_range[0], element.page_number or page_range[-1]],
            )
        )
    return rows


def _procedure_rows_from_text_state_machine(
    text: str, page_range: list[int]
) -> list[dict[str, Any]]:
    matches = list(re.finditer(r"(?m)^\s*(?P<tt>[12])\s+GIS\s+(?P<body>.+)$", text))
    rows: list[dict[str, Any]] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        body = " ".join(text[match.start() : end].split())
        if match.group("tt") == "1":
            data_type = "GIS 110kV"
        else:
            data_type = "GIS trung thế"
        rows.append(_procedure_row(match.group("tt"), data_type, body, body, page_range))
    return rows


def _procedure_row(
    tt: str, data_type: str, cpcit: str, ctdl: str, page_range: list[int]
) -> dict[str, Any]:
    clean_type = _clean_data_type(data_type)
    return {
        "tt": str(tt).strip(),
        "data_type": clean_type,
        "data_type_normalized": robust_normalize(clean_type),
        "cpcit": " ".join(str(cpcit).split()),
        "ctdl": " ".join(str(ctdl).split()),
        "page_range": page_range,
    }


def _clean_data_type(value: str) -> str:
    text = " ".join(str(value or "").split()).strip(" :-")
    normalized = robust_normalize(text)
    if "gis 110kv" in normalized:
        return "GIS 110kV"
    if "gis trung" in normalized:
        return "GIS trung thế"
    return re.sub(r"^(Dữ liệu|Dá»¯ liá»‡u)\s+", "", text, flags=re.IGNORECASE)


def _schema_objects_from_text(text: str, page_range: list[int]) -> list[dict[str, Any]]:
    lines = _schema_detail_lines(text)
    objects: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    current_rows: list[str] = []

    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("TABLE_"):
            continue
        object_match = GIS_OBJECT_RE.match(line)
        if object_match:
            if current is not None:
                current["fields"] = _parse_schema_row_lines(current_rows, page_range)
                current["field_count"] = current.get("field_count") or len(current["fields"])
                objects.append(current)
            object_name = _clean_object_name(object_match.group("object_name"))
            current = {
                "object_code": object_match.group("object_code"),
                "object_name": object_name,
                "field_count": None,
                "fields": [],
                "page_range": page_range,
            }
            current_rows = []
            continue
        if current is None:
            continue
        count_match = re.search(
            r"(Số lượng trường|Sá»‘ lÆ°á»£ng trÆ°á»ng)\s*[:：]?\s*(\d{1,3})",
            line,
            flags=re.IGNORECASE,
        )
        if count_match:
            current["field_count"] = int(count_match.group(2))
            continue
        if _is_schema_header(line):
            continue
        if _looks_like_section_boundary(line):
            current["fields"] = _parse_schema_row_lines(current_rows, page_range)
            current["field_count"] = current.get("field_count") or len(current["fields"])
            objects.append(current)
            current = None
            current_rows = []
            continue
        if _starts_numbered_row(line):
            current_rows.append(line)
        elif current_rows and not _looks_like_section_boundary(line):
            current_rows[-1] = f"{current_rows[-1]} {line}"

    if current is not None:
        current["fields"] = _parse_schema_row_lines(current_rows, page_range)
        current["field_count"] = current.get("field_count") or len(current["fields"])
        objects.append(current)
    return _dedupe_objects(objects)


def _schema_objects_from_elements(
    text: str, elements: list[ParsedElement], page_range: list[int]
) -> list[dict[str, Any]]:
    page_objects = _page_object_context(text, elements)
    grouped: dict[str, dict[str, Any]] = {}
    for element in elements:
        if element.element_type != "table_row":
            continue
        metadata = element.metadata or {}
        headers = [robust_normalize(str(header)) for header in metadata.get("headers") or []]
        values = [str(value or "") for value in metadata.get("values") or []]
        if not _is_schema_headers(headers) or not values:
            continue
        context = page_objects.get(element.page_number or 0)
        if context is None:
            continue
        parsed = _schema_field_from_cells(
            headers,
            values,
            [element.page_number or page_range[0], element.page_number or page_range[-1]],
        )
        if parsed is None:
            continue
        obj = grouped.setdefault(
            context[0],
            {
                "object_code": context[0],
                "object_name": context[1],
                "field_count": 0,
                "fields": [],
                "page_range": [
                    element.page_number or page_range[0],
                    element.page_number or page_range[-1],
                ],
            },
        )
        obj["fields"].append(parsed)
        obj["field_count"] = max(int(obj.get("field_count") or 0), len(obj["fields"]))
    return list(grouped.values())


def _schema_detail_lines(text: str) -> list[str]:
    lines = text.splitlines()
    start_index = 0
    for index, line in enumerate(lines):
        normalized = robust_normalize(line)
        if normalized.startswith("2. chi tiet du lieu"):
            start_index = index
            break
    return lines[start_index:]


def _parse_schema_row_lines(lines: list[str], page_range: list[int]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for line in lines:
        parsed = parse_schema_field_line(line, page_range)
        if parsed is None:
            continue
        key = (parsed["tt"], parsed["field_name"])
        if key in seen:
            continue
        seen.add(key)
        rows.append(parsed)
    return rows


def parse_schema_field_line(
    line: str, page_range: list[int] | None = None
) -> dict[str, Any] | None:
    cells = _split_pipe_line(line)
    if len(cells) >= 8 and cells[0].isdigit():
        return _schema_field_from_cells(
            ["tt", "field", "description", "data_type", "domain", "width", "source", "convert"],
            cells,
            page_range,
        )

    parts = line.split()
    if len(parts) < 4 or not parts[0].isdigit():
        return None
    data_type_match = _find_data_type(parts, start=2)
    if data_type_match is None:
        return None
    data_type, data_start, data_end = data_type_match
    tt = parts[0]
    field_name = parts[1]
    description = " ".join(parts[2:data_start])
    tail = parts[data_end:]
    convert_to_gis = bool(tail and robust_normalize(tail[-1]) in {"x", "co", "yes", "true", "1"})
    if convert_to_gis:
        tail = tail[:-1]
    source_match = _find_source(tail)
    source_data = ""
    before_source = tail
    if source_match is not None:
        source_data, source_start, _source_end = source_match
        before_source = tail[:source_start]
    width = None
    domain_tokens = before_source
    for index in range(len(before_source) - 1, -1, -1):
        token = before_source[index]
        if token.isdigit():
            width = token
            domain_tokens = before_source[:index]
            break
    domain = " ".join(domain_tokens).strip() or None
    return _schema_field(
        tt=tt,
        field_name=field_name,
        description=description,
        data_type=data_type,
        domain=domain,
        width=width,
        source_data=source_data,
        convert_to_gis=convert_to_gis,
        page_range=page_range,
    )


def _schema_field_from_cells(
    headers: list[str], values: list[str], page_range: list[int] | None
) -> dict[str, Any] | None:
    row = _row_by_headers(headers, values)
    tt = row.get("tt") or (values[0] if values else "")
    field_name = row.get("truong du lieu") or row.get("ten truong") or row.get("field")
    if not tt or not str(tt).strip().isdigit() or not field_name:
        return None
    source_data = row.get("nguon du lieu") or row.get("source") or ""
    convert_value = row.get("chuyen doi sang gis") or row.get("convert") or ""
    return _schema_field(
        tt=tt,
        field_name=field_name,
        description=row.get("mo ta") or row.get("description") or "",
        data_type=row.get("kieu du lieu") or row.get("data_type") or "",
        domain=row.get("mien gia tri") or row.get("domain") or None,
        width=row.get("do rong") or row.get("width") or None,
        source_data=source_data,
        convert_to_gis=_truthy(convert_value),
        page_range=page_range,
    )


def _schema_field(
    *,
    tt: str,
    field_name: str,
    description: str,
    data_type: str,
    domain: str | None,
    width: str | None,
    source_data: str,
    convert_to_gis: bool,
    page_range: list[int] | None,
) -> dict[str, Any]:
    return {
        "tt": str(tt).strip(),
        "field_name": str(field_name).strip(),
        "description": " ".join(str(description).split()),
        "data_type": " ".join(str(data_type).split()),
        "data_type_normalized": robust_normalize(str(data_type)),
        "domain": " ".join(str(domain).split()) if domain else None,
        "width": str(width).strip() if width else None,
        "source_data": " ".join(str(source_data).split()),
        "source_data_normalized": robust_normalize(str(source_data)),
        "convert_to_gis": convert_to_gis,
        "page_range": page_range,
    }


def _find_data_type(parts: list[str], *, start: int) -> tuple[str, int, int] | None:
    normalized_parts = [robust_normalize(part) for part in parts]
    for index in range(start, len(parts)):
        for display, pattern in DATA_TYPE_PATTERNS:
            end = index + len(pattern)
            if tuple(normalized_parts[index:end]) == pattern:
                return display, index, end
    return None


def _find_source(parts: list[str]) -> tuple[str, int, int] | None:
    normalized_parts = [robust_normalize(part).strip("/.,;:") for part in parts]
    joined = " ".join(normalized_parts)
    for display, pattern in SOURCE_PATTERNS:
        phrase = " ".join(pattern)
        match = re.search(rf"\b{re.escape(phrase)}\b", joined)
        if match is None:
            continue
        prefix = joined[: match.start()].split()
        return display, len(prefix), len(prefix) + len(pattern)
    return None


def _row_by_headers(headers: list[str], values: list[str]) -> dict[str, str]:
    row: dict[str, str] = {}
    for index, value in enumerate(values):
        header = headers[index] if index < len(headers) else f"cell_{index + 1}"
        if header in row and not row[header]:
            row[header] = str(value or "")
        elif header not in row:
            row[header] = str(value or "")
    return row


def _page_object_context(text: str, elements: list[ParsedElement]) -> dict[int, tuple[str, str]]:
    page_texts = {
        element.page_number or 0: element.text
        for element in elements
        if element.element_type == "page"
    }
    contexts: dict[int, tuple[str, str]] = {}
    for page, page_text in page_texts.items():
        matches = [
            match
            for match in (GIS_OBJECT_RE.match(line.strip()) for line in page_text.splitlines())
            if match
        ]
        if matches:
            match = matches[-1]
            contexts[page] = (
                match.group("object_code"),
                _clean_object_name(match.group("object_name")),
            )
    return contexts


def _is_schema_headers(headers: list[str]) -> bool:
    return any("truong du lieu" in header or "ten truong" in header for header in headers) and any(
        "chuyen doi" in header for header in headers
    )


def _is_schema_header(line: str) -> bool:
    normalized = robust_normalize(line)
    return normalized.startswith("tt ") and (
        "truong du lieu" in normalized or "ten truong" in normalized
    )


def _starts_numbered_row(line: str) -> bool:
    return bool(re.match(r"^\s*\d{1,3}\s+\S+", line))


def _looks_like_section_boundary(line: str) -> bool:
    normalized = robust_normalize(line)
    return bool(GIS_OBJECT_RE.match(line.strip())) or normalized.startswith(("3. ", "4. "))


def _clean_object_name(value: str) -> str:
    text = " ".join(value.split()).strip(" .;-")
    if ":" in text:
        text = text.split(":", 1)[0].strip()
    return text


def _dedupe_objects(objects: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for obj in objects:
        code = obj["object_code"]
        if code in seen:
            continue
        seen.add(code)
        result.append(obj)
    return result


def _parse_attribute_fields(section: str) -> list[dict[str, Any]]:
    fields: list[dict[str, Any]] = []
    for line in section.splitlines():
        line = line.strip()
        if not _starts_numbered_row(line) or _is_schema_header(line):
            continue
        parsed = _parse_attribute_field_line(line)
        if parsed is not None:
            fields.append(parsed)
    return fields


def _parse_attribute_field_line(line: str) -> dict[str, Any] | None:
    parts = line.split()
    if len(parts) < 4 or not parts[0].isdigit():
        return None
    data_type_match = _find_data_type(parts, start=2)
    if data_type_match is None:
        return None
    data_type, data_start, data_end = data_type_match
    tail = parts[data_end:]
    width = next((token for token in tail if token.isdigit()), None)
    domain_tokens = [token for token in tail if token != width]
    return {
        "tt": parts[0],
        "field_name": parts[1],
        "description": " ".join(parts[2:data_start]),
        "data_type": data_type,
        "domain": " ".join(domain_tokens) or None,
        "width": width,
    }


def _extract_attribute_description(section: str, table_name: str) -> str:
    lines = [line.strip() for line in section.splitlines() if line.strip()]
    for index, line in enumerate(lines):
        if robust_normalize(line).startswith("mo ta"):
            return line
        if table_name in line and index + 1 < len(lines):
            candidate = lines[index + 1]
            if not _starts_numbered_row(candidate) and not _is_schema_header(candidate):
                return candidate
    return ""


def _find_table_name_start(text: str, table_name: str) -> int:
    match = re.search(rf"(?m)^.*\b{re.escape(table_name)}\b.*$", text)
    return match.start() if match else -1


def _relationship_source_layer(name: str, section: str) -> str:
    if "CotDien" in name:
        return "F08_PXXXXX_CotDien_HT"
    return "F05_PXXXXX_CongToKhachHang_HT"


def _relationship_target_table(name: str, section: str) -> str:
    suffix = name.split("_HT_", 1)[-1]
    match = re.search(rf"\bP[X]+_{re.escape(suffix)}\b", section)
    if match:
        return match.group(0)
    return f"PX_{suffix}"


def _relationship_target_key(target_table: str, section: str) -> str:
    if "HinhAnhCotDien" in target_table:
        return "IDCotDien"
    return "IDCongToKhachHang"


def _find_normalized(text: str, needle: str) -> int:
    normalized_lines = []
    cursor = 0
    for line in text.splitlines(keepends=True):
        normalized_lines.append((cursor, robust_normalize(line)))
        cursor += len(line)
    for start, normalized in normalized_lines:
        if needle in normalized:
            return start
    return -1


def _truthy(value: str) -> bool:
    normalized = robust_normalize(value)
    return normalized in {"x", "co", "yes", "true", "1"}


def _split_pipe_line(line: str) -> list[str]:
    if "|" not in line:
        return []
    cleaned = line.strip()
    if cleaned.startswith("|"):
        cleaned = cleaned[1:]
    return [cell.strip() for cell in cleaned.split("|")]
