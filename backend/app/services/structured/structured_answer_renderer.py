from __future__ import annotations

import re
from typing import Any

from app.services.structured.structured_schemas import StructuredEvidence, StructuredRow
from app.services.chunkers.chunker_table_relationships import normalize_metadata_value

PREFERRED_LABELS = {
    "subject": "Ná»™i dung",
    "topic": "Ná»™i dung",
    "area": "Máº£ng/Ná»™i dung",
    "department": "ÄÆ¡n vá»‹ phá»¥ trÃ¡ch",
    "person": "NhÃ¢n sá»±",
    "measure_value": "GiÃ¡ trá»‹",
    "measure_text": "ThÃ´ng tin",
    "condition": "Äiá»u kiá»‡n",
}


def _clean_text(value: Any) -> str:
    if isinstance(value, dict):
        text = "; ".join(
            f"{key}: {_clean_text(item)}"
            for key, item in value.items()
            if item not in (None, "", [], {})
        )
    elif isinstance(value, (list, tuple, set)):
        text = ", ".join(_clean_text(item) for item in value if item not in (None, ""))
    else:
        text = str(value or "")
    text = text.replace("NSDLÄ", "ngÆ°á»i sá»­ dá»¥ng lao Ä‘á»™ng")
    text = re.sub(r"\bNSDLD\b", "ngÆ°á»i sá»­ dá»¥ng lao Ä‘á»™ng", text, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", text).strip(" .;:")


def _lowercase_initial(text: str) -> str:
    text = text.strip()
    if not text:
        return text
    return text[0].lower() + text[1:]


def _norm(text: str) -> str:
    return normalize_metadata_value(text)


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", _norm(text)))


def _row_subject(row: StructuredRow) -> str:
    for key in ("subject", "topic", "area", "case_name"):
        value = _clean_text(row.fields.get(key))
        if value:
            return value
    return _clean_text(row.raw_text[:160]) or "thÃ´ng tin Ä‘Æ°á»£c truy xuáº¥t"


def _source_label(row: StructuredRow) -> str:
    return _clean_text(row.source_title) or "dá»¯ liá»‡u Ä‘Æ°á»£c truy xuáº¥t"


def _subject_variants(subject: str) -> list[str]:
    """Split a composite subject into displayable variants without domain rules."""

    cleaned = _clean_text(subject)
    if not cleaned:
        return []

    variants: list[str] = []
    for segment in re.split(r";", cleaned):
        segment = _clean_text(segment)
        if not segment:
            continue
        if segment not in variants:
            variants.append(segment)
        comma_parts = [_clean_text(part) for part in segment.split(",") if _clean_text(part)]
        if len(comma_parts) > 1:
            for part in comma_parts:
                if part not in variants:
                    variants.append(part)
    return variants or [cleaned]


def _select_subject_for_query(query: str, subject: str) -> tuple[str, list[str], bool]:
    variants = _subject_variants(subject)
    if not variants:
        return subject, [], False

    query_tokens = _tokens(query)
    subject_norm = _norm(subject)
    scored: list[tuple[float, int, str]] = []
    for index, variant in enumerate(variants):
        variant_tokens = _tokens(variant)
        overlap = query_tokens & variant_tokens
        if not variant_tokens:
            score = 0.0
        else:
            score = (len(overlap) / len(variant_tokens)) + (len(overlap) / max(len(query_tokens), 1))
        if _norm(variant) and _norm(variant) in _norm(query):
            score += 1.0
        scored.append((score, -index, variant))

    scored.sort(reverse=True)
    selected = scored[0][2]
    selected_norm = _norm(selected)
    selected_is_full_subject = selected_norm == subject_norm
    alternatives = [
        variant
        for variant in variants
        if _norm(variant) != selected_norm
        and (selected_is_full_subject or _norm(variant) != subject_norm)
    ]
    return selected, alternatives, selected_is_full_subject


def _extract_day_count(value: Any) -> int | None:
    text = _norm(_clean_text(value))
    match = re.search(r"(?:nghi\s+)?(\d+)\s+ngay", text)
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def _row_days(row: StructuredRow) -> int | None:
    for key in ("measure_value", "total_days", "total_leave_days"):
        value = row.fields.get(key)
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.strip().isdigit():
            return int(value.strip())
        extracted = _extract_day_count(value)
        if extracted is not None:
            return extracted
    for key in ("measure_text", "total_benefit", "total_leave_benefit"):
        extracted = _extract_day_count(row.fields.get(key))
        if extracted is not None:
            return extracted
    return None


def _row_detail(row: StructuredRow) -> str:
    for key in ("measure_text", "total_benefit", "total_leave_benefit"):
        value = _clean_text(row.fields.get(key))
        if value:
            return value

    measure_value = _clean_text(row.fields.get("measure_value"))
    if measure_value:
        return measure_value

    visible_parts: list[str] = []
    for key, value in row.fields.items():
        if key in {
            "case_code",
            "case_name",
            "chunk_type",
            "relationship_type",
            "row_id",
            "source_file",
            "subject",
        }:
            continue
        text = _clean_text(value)
        if text:
            label = PREFERRED_LABELS.get(key, key.replace("_", " ").title())
            visible_parts.append(f"{label}: {text}")
        if len(visible_parts) >= 3:
            break
    return "; ".join(visible_parts)


def _row_condition_suffix(row: StructuredRow) -> str:
    candidates = [
        row.fields.get("condition"),
        row.fields.get("conditions"),
        row.fields.get("collective_agreement_benefit"),
        row.fields.get("additional_benefit"),
        row.raw_text,
    ]
    for candidate in candidates:
        text = _clean_text(candidate)
        match = re.search(r"(?i)(pháº£i\s+thÃ´ng\s+bÃ¡o\b.*|phai\s+thong\s+bao\b.*)", text)
        if match:
            condition = _clean_text(match.group(1))
            return f" vÃ  {_lowercase_initial(condition)}"
    return ""


def _component_breakdown(row: StructuredRow) -> tuple[int | None, int | None]:
    base_days = None
    additional_days = None
    for key in ("base_benefit", "labor_code_benefit"):
        base_days = _extract_day_count(row.fields.get(key))
        if base_days is not None:
            break
    for key in ("additional_benefit", "collective_agreement_benefit"):
        additional_days = _extract_day_count(row.fields.get(key))
        if additional_days is not None:
            break
    return base_days, additional_days


def _row_bullet(row: StructuredRow) -> str | None:
    subject = _row_subject(row)
    days = _row_days(row)
    detail = _row_detail(row)
    if not subject and days is None and not detail:
        return None
    condition = _row_condition_suffix(row)
    citation = f" [{row.citation}]" if row.citation else ""
    if days is not None:
        return f"- **{subject}:** ÄÆ°á»£c hÆ°á»Ÿng **{days:02d} ngÃ y**{condition}.{citation}"
    if detail:
        return f"- **{subject}:** {detail}{condition}.{citation}"
    return f"- **{subject}**{condition}.{citation}"


def _should_include_related_rows(
    *,
    selected_subject: str,
    selected_is_full_subject: bool,
    best: StructuredRow,
    candidate: StructuredRow,
) -> bool:
    if not selected_is_full_subject:
        return False
    if best.table_id and candidate.table_id and best.table_id != candidate.table_id:
        return False
    selected_tokens = _tokens(selected_subject)
    candidate_tokens = _tokens(_row_subject(candidate))
    if not selected_tokens or not candidate_tokens:
        return False
    return selected_tokens.issubset(candidate_tokens)


def _query_directness_score(
    query: str, evidence: StructuredEvidence
) -> tuple[float, int, float]:
    """Rank rows by how directly their subject answers the user query."""

    row = evidence.row
    subject = _row_subject(row)
    selected_subject, _, _ = _select_subject_for_query(query, subject)
    selected_norm = _norm(selected_subject)
    query_norm = _norm(query)
    selected_tokens = _tokens(selected_subject)
    query_tokens = _tokens(query)
    if not selected_tokens:
        return (0.0, 0, evidence.score)

    overlap = selected_tokens & query_tokens
    missing = selected_tokens - query_tokens
    score = (len(overlap) / len(selected_tokens)) + (
        len(overlap) / max(len(query_tokens), 1)
    )
    if selected_norm and selected_norm in query_norm:
        score += 2.0
    score -= 0.12 * len(missing)

    return (score, -len(selected_tokens), evidence.score)


def _select_best_evidence(
    query: str, evidences: list[StructuredEvidence]
) -> StructuredEvidence:
    return max(evidences, key=lambda evidence: _query_directness_score(query, evidence))


def render_structured_answer(
    *,
    query: str,
    evidences: list[StructuredEvidence],
    max_rows: int = 5,
) -> str | None:
    if not evidences:
        return None

    best_evidence = _select_best_evidence(query, evidences[:max_rows])
    top = [best_evidence, *(evidence for evidence in evidences if evidence is not best_evidence)][
        :max_rows
    ]
    best = best_evidence.row
    source_label = _source_label(best)
    subject = _row_subject(best)
    selected_subject, alternatives, selected_is_full_subject = _select_subject_for_query(
        query, subject
    )
    selected_display = _lowercase_initial(selected_subject)
    detail = _row_detail(best)
    days = _row_days(best)
    citation = f" [{best.citation}]" if best.citation else ""

    if days is not None:
        lines = [
            f"Theo {source_label}, trÆ°á»ng há»£p **{selected_display}** Ä‘Æ°á»£c ghi nháº­n trong dá»¯ liá»‡u Ä‘Æ°á»£c truy xuáº¥t.",
            "",
            f"- **Tá»•ng sá»‘ ngÃ y:** **{days:02d} ngÃ y** hÆ°á»Ÿng nguyÃªn lÆ°Æ¡ng.{citation}",
        ]
        base_days, additional_days = _component_breakdown(best)
        if base_days is not None and additional_days is not None:
            lines.append(
                "- **Chi tiáº¿t thÃ nh pháº§n:** Bao gá»“m "
                f"**{base_days:02d} ngÃ y** theo quy Ä‘á»‹nh ná»n vÃ  "
                f"**{additional_days:02d} ngÃ y** theo quy Ä‘á»‹nh bá»• sung trong tÃ i liá»‡u."
            )
        else:
            condition = _row_condition_suffix(best)
            if condition:
                lines[-1] = lines[-1].rstrip(".") + f"{condition}."
    elif detail:
        lines = [
            f"Theo {source_label}, khi **{selected_display}**: {detail}.{citation}"
        ]
    else:
        lines = [f"Theo {source_label}, ná»™i dung phÃ¹ há»£p nháº¥t lÃ  **{subject}**.{citation}"]

    if alternatives and not selected_is_full_subject:
        alternatives_text = "; ".join(_lowercase_initial(item) for item in alternatives)
        lines.extend(
            [
                "",
                f"ThÃ´ng tin nÃ y cÅ©ng náº±m cÃ¹ng dÃ²ng dá»¯ liá»‡u vá»›i: {alternatives_text}.",
            ]
        )

    related_bullets: list[str] = []
    seen_subjects = {subject.casefold()}
    for evidence in top[1:]:
        row = evidence.row
        if not _should_include_related_rows(
            selected_subject=selected_subject,
            selected_is_full_subject=selected_is_full_subject,
            best=best,
            candidate=row,
        ):
            continue
        row_subject = _row_subject(row)
        key = row_subject.casefold()
        if not key or key in seen_subjects:
            continue
        bullet = _row_bullet(row)
        if bullet:
            seen_subjects.add(key)
            related_bullets.append(bullet)

    if related_bullets:
        lines.extend(
            [
                "",
                (
                    f"NgoÃ i trÆ°á»ng há»£p **{_lowercase_initial(selected_subject)}**, "
                    "tÃ i liá»‡u cÃ²n quy Ä‘á»‹nh cÃ¡c trÆ°á»ng há»£p liÃªn quan trong cÃ¹ng báº£ng nhÆ° sau:"
                ),
                "",
                *related_bullets,
            ]
        )

    return "\n".join(lines).strip() or None
