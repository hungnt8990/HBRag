from __future__ import annotations

import json
import re

from app.services.structured.schemas import StructuredEvidence, StructuredRow
from app.services.table_relationships import normalize_metadata_value

ADMINISTRATIVE_FIELDS = {
    "article_number",
    "article_title",
    "case_code",
    "chunk_type",
    "document_title",
    "relationship_type",
    "row_id",
    "source_date",
    "source_file",
    "source_label",
    "table_name",
}
PRIMARY_FIELDS = {
    "area",
    "case_name",
    "person",
    "proposed_staff",
    "staff",
    "subject",
    "topic",
}
DETAIL_FIELDS = {
    "additional_benefit",
    "base_benefit",
    "collective_agreement_benefit",
    "condition",
    "labor_code_benefit",
    "measure_text",
    "row_text",
    "total_benefit",
    "total_leave_benefit",
}


def tokenize(text: str) -> set[str]:
    normalized = normalize_metadata_value(text)
    return set(re.findall(r"[a-z0-9]+", normalized))




def _stringify_field_value(value: object) -> str:
    """Convert metadata values to stable text for scoring.

    Structured metadata can contain nested lists/dicts (for example table cells,
    extracted fields, or parsed row objects). Scoring should never assume a list
    contains only strings.
    """

    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, dict):
        parts: list[str] = []
        for key in sorted(value, key=lambda item: str(item)):
            key_text = _stringify_field_value(key)
            value_text = _stringify_field_value(value.get(key))
            if key_text and value_text:
                parts.append(f"{key_text}: {value_text}")
            elif value_text:
                parts.append(value_text)
            elif key_text:
                parts.append(key_text)
        return "; ".join(part for part in parts if part)
    if isinstance(value, (list, tuple, set)):
        return "; ".join(
            part for item in value if (part := _stringify_field_value(item))
        )
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except TypeError:
        return str(value)


def _field_weight(field_name: str) -> float:
    if field_name in PRIMARY_FIELDS:
        return 2.0
    if field_name in DETAIL_FIELDS:
        return 0.75
    if field_name in ADMINISTRATIVE_FIELDS:
        return 0.0
    return 0.5


def _candidate_texts(value_text: str) -> list[str]:
    candidates = [value_text]
    for part in re.split(r"[;,]", value_text):
        part = part.strip()
        if part and len(tokenize(part)) >= 3 and part not in candidates:
            candidates.append(part)
    candidates.extend(_expanded_composite_candidates(value_text))
    return candidates

def _expanded_composite_candidates(value_text: str) -> list[str]:
    """Expand compact alternatives like ``A, B event`` into ``A event``.

    This keeps scoring generic while handling common table shorthand where a
    suffix applies to several comma-separated subjects in the same cell.
    """

    expanded: list[str] = []
    for segment in re.split(r";", value_text):
        parts = [part.strip() for part in segment.split(",") if part.strip()]
        if len(parts) <= 1:
            continue
        last_tokens = parts[-1].split()
        if len(last_tokens) < 2:
            continue
        suffix = " ".join(last_tokens[1:])
        suffix_norm = normalize_metadata_value(suffix)
        if not suffix_norm:
            continue
        for part in parts[:-1]:
            part_norm = normalize_metadata_value(part)
            if suffix_norm in part_norm:
                continue
            expanded.append(f"{part} {suffix}")
    return expanded


def _score_text_against_query(
    *,
    query_tokens: set[str],
    normalized_query: str,
    value_text: str,
    weight: float,
) -> float:
    value_tokens = tokenize(value_text)
    if not value_tokens:
        return 0.0

    overlap = query_tokens & value_tokens
    if not overlap:
        return 0.0

    coverage = len(overlap) / max(len(value_tokens), 1)
    recall = len(overlap) / max(len(query_tokens), 1)
    overlap_count = len(overlap)
    field_score = weight * ((coverage * 0.45) + (recall * 0.25) + (overlap_count * 0.3))

    normalized_value = normalize_metadata_value(value_text)
    if normalized_value and normalized_value in normalized_query:
        exact_token_count = len(tokenize(normalized_value))
        field_score += (2.5 if exact_token_count >= 3 else 0.5) * weight
    elif normalized_query and normalized_query in normalized_value:
        field_score += 0.25 * weight

    return field_score


def score_structured_row(query: str, row: StructuredRow) -> StructuredEvidence:
    query_tokens = tokenize(query)
    if not query_tokens:
        return StructuredEvidence(row=row, score=0.0)

    matched_fields: list[str] = []
    total_score = 0.0
    normalized_query = normalize_metadata_value(query)
    seen_values: set[str] = set()

    for field_name, value in row.fields.items():
        weight = _field_weight(field_name)
        if weight <= 0:
            continue

        value_text = _stringify_field_value(value)
        normalized_value = normalize_metadata_value(value_text)
        if not normalized_value or normalized_value in seen_values:
            continue
        seen_values.add(normalized_value)

        best_field_score = max(
            _score_text_against_query(
                query_tokens=query_tokens,
                normalized_query=normalized_query,
                value_text=candidate,
                weight=weight,
            )
            for candidate in _candidate_texts(value_text)
        )
        if best_field_score <= 0:
            continue

        total_score += best_field_score
        matched_fields.append(field_name)

    raw_tokens = tokenize(row.raw_text)
    raw_overlap = query_tokens & raw_tokens
    if raw_overlap:
        total_score += 0.2 * (len(raw_overlap) / max(len(query_tokens), 1))

    return StructuredEvidence(
        row=row,
        score=total_score,
        matched_fields=matched_fields,
    )
