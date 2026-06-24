from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from typing import Any

from app.services.ingestion.ingestion_profiles import get_profile_config

_ROW_RE = re.compile(r"^\s*(?P<row_no>\d+\.\d+)\s+(?P<body>.+?)\s*$")
_GROUP_NUMBER_RE = re.compile(r"^\s*(?P<group_no>\d{1,2})\s+(?P<title>[^\d].+?)\s*$")
_PAGE_MARKER_RE = re.compile(r"^\s*\[?Trang\s+(?P<page>\d+)\]?\s*$", re.I)
_HEADER_HINTS = {
    "tt",
    "thanh phan cong nghe",
    "cong cu su dung",
    "hang san xuat",
    "nha cung cap",
    "muc dich su dung",
    "staging",
    "production",
}
_GROUP_TITLE_HINTS = {
    "version control",
    "message queue",
    "ci/cd",
    "ha tang",
    "infrastructure",
    "cong cu thiet ke prototype",
    "design",
    "library",
    "quan ly tai lieu",
    "kiem thu tu dong",
    "quan ly cong viec",
    "database",
    "caching",
    "web frontend",
    "backend",
    "app server",
    "mobile",
}


def _normalize(value: str) -> str:
    decomposed = unicodedata.normalize("NFD", value or "")
    stripped = "".join(char for char in decomposed if unicodedata.category(char) != "Mn")
    return " ".join(stripped.casefold().split())


def _clean_line(line: str) -> str:
    return " ".join(str(line or "").replace("\u00a0", " ").split())


def _apply_aliases(value: str, aliases: dict[str, str]) -> str:
    output = value
    for wrong, correct in aliases.items():
        output = re.sub(re.escape(str(wrong)), str(correct), output, flags=re.I)
    return output


def _looks_like_header(line: str) -> bool:
    normalized = _normalize(line)
    return not normalized or normalized in _HEADER_HINTS


def _looks_like_catalog_document(text: str, source_file: str | None = None) -> bool:
    config = get_profile_config("catalog_table")
    detect_rules = dict(config.get("detect_rules") or {})
    normalized_text = _normalize(f"{source_file or ''}\n{text}")
    score = 0
    for keyword in detect_rules.get("title_keywords") or []:
        if isinstance(keyword, str) and _normalize(keyword) in normalized_text:
            score += 2
    for header in detect_rules.get("table_headers") or []:
        if isinstance(header, str) and _normalize(header) in normalized_text:
            score += 2
    min_score = detect_rules.get("min_score")
    threshold = min_score if isinstance(min_score, int) else 6
    return score >= threshold


def is_catalog_table_document(text: str, source_file: str | None = None) -> bool:
    return _looks_like_catalog_document(text, source_file=source_file)


def _page_for_line(line_index: int, page_ranges: list[tuple[int, int, int]]) -> int | None:
    for start, end, page in page_ranges:
        if start <= line_index < end:
            return page
    return None


def _prepare_lines(page_texts: dict[int, str]) -> tuple[list[str], list[tuple[int, int, int]]]:
    lines: list[str] = []
    page_ranges: list[tuple[int, int, int]] = []
    for page, text in sorted(page_texts.items()):
        start = len(lines)
        lines.extend(str(text or "").splitlines())
        end = len(lines)
        page_ranges.append((start, end, int(page)))
    return lines, page_ranges


def _extract_group_title(line: str) -> str | None:
    cleaned = _clean_line(line)
    if not cleaned or _looks_like_header(cleaned):
        return None
    match = _GROUP_NUMBER_RE.match(cleaned)
    if match:
        title = match.group("title").strip(" .:-")
        if title and _normalize(title) not in _HEADER_HINTS:
            return title
    normalized = _normalize(cleaned)
    if normalized in _GROUP_TITLE_HINTS:
        return cleaned
    # The PDF extraction often splits group number and group title into two lines.
    if cleaned.isdigit():
        return None
    if len(cleaned) <= 70 and not _ROW_RE.match(cleaned):
        if normalized not in _HEADER_HINTS and not normalized.startswith("su dung "):
            return cleaned
    return None


def _extract_rows(lines: list[str], page_ranges: list[tuple[int, int, int]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    current_group: str | None = None
    pending_group_number: str | None = None
    index = 0
    while index < len(lines):
        line = _clean_line(lines[index])
        if not line:
            index += 1
            continue

        page_marker = _PAGE_MARKER_RE.match(line)
        if page_marker:
            index += 1
            continue

        if line.isdigit():
            pending_group_number = line
            index += 1
            continue

        row_match = _ROW_RE.match(line)
        if row_match:
            row_no = row_match.group("row_no")
            body_parts = [row_match.group("body")]
            page = _page_for_line(index, page_ranges)
            index += 1
            while index < len(lines):
                next_line = _clean_line(lines[index])
                if not next_line:
                    index += 1
                    continue
                if next_line.isdigit() or _ROW_RE.match(next_line):
                    break
                group_title = _extract_group_title(next_line)
                if group_title and _normalize(next_line) in _GROUP_TITLE_HINTS:
                    break
                if _looks_like_header(next_line):
                    index += 1
                    continue
                body_parts.append(next_line)
                index += 1
            rows.append(
                {
                    "row_no": row_no,
                    "group": current_group or "Danh mục",
                    "raw_text": " ".join(body_parts),
                    "page": page,
                }
            )
            continue

        group_title = _extract_group_title(line)
        if group_title:
            current_group = group_title
            pending_group_number = None
        elif pending_group_number:
            pending_group_number = None
        index += 1
    return rows


def _catalog_item_name(row_body: str) -> str:
    # Keep the item name approximate. Retrieval mainly needs the full row text;
    # this compact name is only metadata/display sugar.
    body = _clean_line(row_body)
    if not body:
        return ""
    stop_markers = [
        " Microsoft ",
        " GitLab ",
        " Vmware ",
        " Apache ",
        " Docker ",
        " Cloud Native ",
        " Grafana ",
        " Atlassian ",
        " Oracle ",
        " PostgreSQL ",
        " MongoDB",
        " MinIO",
        " Redis ",
        " Google ",
        " Facebook ",
        " OpenJS ",
        " F5 ",
        " themeforest ",
        " Jenkins ",
        " Katalon",
        " Selenium",
    ]
    first_stop = min((body.find(marker) for marker in stop_markers if body.find(marker) > 0), default=-1)
    if first_stop > 0:
        return body[:first_stop].strip(" -")
    words = body.split()
    return " ".join(words[:4])


def _build_record(
    *,
    chunk_id: str,
    chunk_type: str,
    text: str,
    headings: list[str],
    pages: list[int],
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    metadata = dict(metadata or {})
    return {
        **metadata,
        "chunk_id": chunk_id,
        "chunk_type": chunk_type,
        "content_format": "text",
        "document_type": "catalog_table_document",
        "chunk_strategy": "catalog_table_v1",
        "segment_chunk_strategy": "catalog_table_v1",
        "text": text,
        "content": text,
        "contextualized_text": text,
        "retrieval_text": text,
        "raw_text": metadata.get("raw_text") or text,
        "source_raw_text": metadata.get("source_raw_text") or metadata.get("raw_text") or text,
        "headings": headings,
        "section_path": headings,
        "pages": pages,
        "page_range": pages,
        "quality_status": "pass",
        "indexable": True,
        "embedding_enabled": True,
    }


def build_catalog_table_records_from_page_texts(
    *,
    page_texts: dict[int, str],
    source_file: str,
    max_tokens: int | None = None,
) -> list[dict[str, Any]]:
    if not page_texts:
        return []

    full_text = "\n\n".join(str(text or "") for _page, text in sorted(page_texts.items()))
    if not is_catalog_table_document(full_text, source_file=source_file):
        return []

    config = get_profile_config("catalog_table")
    aliases = {
        str(key): str(value)
        for key, value in dict(config.get("aliases") or {}).items()
        if str(key).strip() and str(value).strip()
    }
    lines, page_ranges = _prepare_lines(page_texts)
    rows = _extract_rows(lines, page_ranges)
    if not rows:
        return []

    records: list[dict[str, Any]] = []
    by_group: dict[str, list[dict[str, Any]]] = defaultdict(list)
    seen: set[tuple[str, str, str]] = set()
    for row in rows:
        row_no = str(row["row_no"])
        group = _apply_aliases(str(row["group"]), aliases)
        raw_text = _apply_aliases(str(row["raw_text"]), aliases)
        key = (row_no, group, raw_text)
        if key in seen:
            continue
        seen.add(key)
        item_name = _catalog_item_name(raw_text)
        page = row.get("page")
        pages = [int(page)] if page is not None and str(page).isdigit() else []
        content = (
            "Dòng danh mục công nghệ. "
            f"Nhóm: {group}. STT: {row_no}. "
            f"Công nghệ/Công cụ: {item_name or raw_text}. "
            f"Thông tin dòng: {raw_text}."
        )
        metadata = {
            "catalog_group": group,
            "catalog_row_no": row_no,
            "catalog_item": item_name or None,
            "row_text": raw_text,
            "raw_text": str(row.get("raw_text") or raw_text),
            "table_name": "Danh mục công nghệ / platform / framework dùng chung",
            "relationship_type": "catalog_item",
            "confidence": 0.88,
        }
        records.append(
            _build_record(
                chunk_id=f"catalog_row_{len(records):04d}",
                chunk_type="catalog_row",
                text=content,
                headings=["Danh mục công nghệ dùng chung", group],
                pages=pages,
                metadata=metadata,
            )
        )
        by_group[group].append({"row_no": row_no, "item": item_name or raw_text, "text": raw_text})

    for group, group_rows in by_group.items():
        items = "; ".join(
            f"{row['row_no']} {row['item']}" for row in group_rows[:30]
        )
        content = f"Nhóm danh mục công nghệ: {group}. Các công nghệ/công cụ trong nhóm gồm: {items}."
        records.append(
            _build_record(
                chunk_id=f"catalog_group_{len(records):04d}",
                chunk_type="catalog_group",
                text=content,
                headings=["Danh mục công nghệ dùng chung", group],
                pages=[],
                metadata={
                    "catalog_group": group,
                    "catalog_items": [row["item"] for row in group_rows],
                    "table_name": "Danh mục công nghệ / platform / framework dùng chung",
                    "relationship_type": "catalog_group",
                    "confidence": 0.9,
                },
            )
        )

    group_summaries = []
    for group, group_rows in by_group.items():
        group_summaries.append(
            f"{group}: " + ", ".join(str(row["item"]) for row in group_rows[:12])
        )
    summary_text = (
        "Tài liệu này là danh mục công nghệ/platform/framework/ngôn ngữ lập trình "
        "được ban hành để dùng chung trong hệ thống phần mềm. "
        "Các nhóm danh mục chính gồm: "
        + "; ".join(group_summaries)
        + "."
    )
    records.insert(
        0,
        _build_record(
            chunk_id="catalog_summary_0000",
            chunk_type="catalog_summary",
            text=summary_text,
            headings=["Danh mục công nghệ dùng chung"],
            pages=sorted({int(page) for _start, _end, page in page_ranges}),
            metadata={
                "catalog_groups": list(by_group.keys()),
                "table_name": "Danh mục công nghệ / platform / framework dùng chung",
                "relationship_type": "catalog_summary",
                "confidence": 0.92,
            },
        ),
    )
    return records
