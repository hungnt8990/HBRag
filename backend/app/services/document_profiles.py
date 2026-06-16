from __future__ import annotations

import unicodedata
from pathlib import Path
from typing import Any, Literal

from app.services.heading_rule_engine import (
    heading_rules_from_config,
    score_heading_rules,
)
from app.services.ingestion_profiles import (
    DEFAULT_PROFILE,
    FALLBACK_CONFIG,
    get_profile_config,
    get_profile_configs,
    get_profile_names,
)

ProfileName = Literal[
    "auto",
    "legal_admin",
    "catalog_table",
    "general",
    "spreadsheet",
    "slide",
]

__all__ = [
    "FALLBACK_CONFIG",
    "PROFILE_CONFIGS",
    "PROFILE_NAMES",
    "detect_profile",
    "profile_config",
    "resolve_profile",
]

PROFILE_CONFIGS: dict[str, dict[str, Any]] = get_profile_configs()
PROFILE_NAMES: tuple[str, ...] = get_profile_names()
_DETECTION_SAMPLE = 20000
_SPREADSHEET_EXTENSIONS = {".csv", ".ods", ".xls", ".xlsm", ".xlsx"}
_SLIDE_EXTENSIONS = {".odp", ".ppt", ".pptx"}
_SPREADSHEET_MIME_HINTS = (
    "csv",
    "excel",
    "spreadsheet",
    "vnd.ms-excel",
    "sheet",
)
_SLIDE_MIME_HINTS = (
    "presentation",
    "powerpoint",
    "vnd.ms-powerpoint",
    "vnd.openxmlformats-officedocument.presentationml",
)


def _normalize_for_detection(value: str) -> str:
    decomposed = unicodedata.normalize("NFD", value or "")
    stripped = "".join(char for char in decomposed if unicodedata.category(char) != "Mn")
    return " ".join(stripped.casefold().split())


def _score_profile_headings(sample: str, profile: str) -> int:
    config = get_profile_config(profile)
    rules = heading_rules_from_config(config)
    return score_heading_rules(sample, rules)


def _score_profile_detect_rules(sample: str, config: dict[str, Any]) -> int:
    """Score config-driven keyword/header rules for non-heading profiles.

    This keeps document detection editable from ingestion profile config instead
    of hardcoding labels for every new document family in the detector.
    """

    detect_rules = config.get("detect_rules")
    if not isinstance(detect_rules, dict):
        return 0

    normalized_sample = _normalize_for_detection(sample)
    score = 0
    for keyword in detect_rules.get("title_keywords") or []:
        if isinstance(keyword, str) and _normalize_for_detection(keyword) in normalized_sample:
            score += 2
    for header in detect_rules.get("table_headers") or []:
        if isinstance(header, str) and _normalize_for_detection(header) in normalized_sample:
            score += 2

    min_score = detect_rules.get("min_score")
    if isinstance(min_score, int) and score < min_score:
        return 0
    return score


def _profile_from_file_type(
    *,
    filename: str | None = None,
    content_type: str | None = None,
) -> str | None:
    suffix = Path(filename or "").suffix.casefold()
    mime = (content_type or "").casefold()
    if suffix in _SPREADSHEET_EXTENSIONS or any(hint in mime for hint in _SPREADSHEET_MIME_HINTS):
        return "spreadsheet"
    if suffix in _SLIDE_EXTENSIONS or any(hint in mime for hint in _SLIDE_MIME_HINTS):
        return "slide"
    return None


def _looks_like_serialized_table(sample: str) -> bool:
    lines = [line for line in sample.splitlines() if line.strip()]
    if not lines:
        return False
    serialized_row_lines = sum(1 for line in lines if line.startswith("TABLE_ROW "))
    pipe_lines = sum(1 for line in lines if " | " in line)
    return serialized_row_lines >= 2 or pipe_lines / len(lines) >= 0.3


def _score_all_configured_profiles(sample: str) -> dict[str, int]:
    scores: dict[str, int] = {}
    for profile, config in get_profile_configs().items():
        if profile in {"general", "spreadsheet", "slide"}:
            continue
        score = _score_profile_headings(sample, profile)
        score += _score_profile_detect_rules(sample, config)
        if score > 0:
            scores[profile] = score
    return scores


def detect_profile(
    text: str | None,
    *,
    filename: str | None = None,
    content_type: str | None = None,
) -> str:
    """Detect a document profile from file hints and saved profile configs.

    Heading labels, catalog keywords, and table headers live in ingestion profile
    config. The detector only executes those saved rules and falls back to a
    generic profile when no structure is clear.
    """

    file_profile = _profile_from_file_type(filename=filename, content_type=content_type)
    if file_profile is not None:
        return file_profile

    if not text or not text.strip():
        return "general"

    sample = text[:_DETECTION_SAMPLE]
    scores = _score_all_configured_profiles(sample)
    if scores:
        best_profile, best_score = max(scores.items(), key=lambda item: (item[1], item[0]))
        if best_score > 0:
            return best_profile

    if _looks_like_serialized_table(sample):
        return "spreadsheet"

    return "general"


def resolve_profile(
    profile: str | None,
    *,
    text: str | None = None,
    filename: str | None = None,
    content_type: str | None = None,
) -> str:
    """Resolve a possibly-``auto`` profile to a concrete profile name."""
    normalized = (profile or DEFAULT_PROFILE).lower().strip()
    if normalized == "auto":
        return detect_profile(text, filename=filename, content_type=content_type)
    if normalized in get_profile_configs():
        return normalized
    return "general"


def profile_config(profile: str | None) -> dict[str, Any]:
    """Return the settings map for a concrete profile name."""
    return get_profile_config(profile)
