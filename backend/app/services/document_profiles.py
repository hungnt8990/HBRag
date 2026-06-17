from __future__ import annotations

import unicodedata
from pathlib import Path
from typing import Any, Literal, TypedDict

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
    "staff_technology_matrix",
    "general",
    "spreadsheet",
    "slide",
]

class ProfileDetectionResult(TypedDict):
    profile: str
    mode: str
    score: int
    evidence: list[dict[str, Any]]
    candidates: list[dict[str, Any]]

__all__ = [
    "FALLBACK_CONFIG",
    "PROFILE_CONFIGS",
    "PROFILE_NAMES",
    "detect_profile",
    "detect_profile_with_evidence",
    "profile_config",
    "resolve_profile",
    "resolve_profile_with_evidence",
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

    score, _ = _detect_rule_matches(sample, config)
    return score

def _detect_rule_matches(sample: str, config: dict[str, Any]) -> tuple[int, list[dict[str, str]]]:
    detect_rules = config.get("detect_rules")
    if not isinstance(detect_rules, dict):
        return 0, []

    normalized_sample = _normalize_for_detection(sample)
    score = 0
    matches: list[dict[str, str]] = []
    for rule_key, weight in (("title_keywords", 2), ("table_headers", 2)):
        for value in detect_rules.get(rule_key) or []:
            if not isinstance(value, str):
                continue
            if _normalize_for_detection(value) in normalized_sample:
                score += weight
                matches.append({"rule": rule_key, "value": value})

    min_score = detect_rules.get("min_score")
    if isinstance(min_score, int) and score < min_score:
        return 0, []
    return score, matches

def _profile_score_details(
    sample: str,
    profile: str,
    config: dict[str, Any],
) -> tuple[int, list[dict[str, Any]]]:
    heading_score = _score_profile_headings(sample, profile)
    detect_score, matches = _detect_rule_matches(sample, config)
    evidence: list[dict[str, Any]] = []
    if heading_score > 0:
        evidence.append({"source": "heading_rules", "score": heading_score})
    if detect_score > 0:
        evidence.append(
            {
                "source": "detect_rules",
                "score": detect_score,
                "matches": matches,
            }
        )
    return heading_score + detect_score, evidence


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
        score, _ = _profile_score_details(sample, profile, config)
        if score > 0:
            scores[profile] = score
    return scores

def _score_all_configured_profiles_with_evidence(sample: str) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for profile, config in get_profile_configs().items():
        if profile in {"general", "spreadsheet", "slide"}:
            continue
        score, evidence = _profile_score_details(sample, profile, config)
        if score > 0:
            candidates.append(
                {
                    "profile": profile,
                    "score": score,
                    "evidence": evidence,
                }
            )
    return sorted(candidates, key=lambda item: (item["score"], item["profile"]), reverse=True)

def detect_profile_with_evidence(
    text: str | None,
    *,
    filename: str | None = None,
    content_type: str | None = None,
) -> ProfileDetectionResult:
    file_profile = _profile_from_file_type(filename=filename, content_type=content_type)
    if file_profile is not None:
        return {
            "profile": file_profile,
            "mode": "file_type",
            "score": 100,
            "evidence": [
                {
                    "source": "file_type",
                    "filename": filename,
                    "content_type": content_type,
                }
            ],
            "candidates": [],
        }

    if not text or not text.strip():
        return {
            "profile": "general",
            "mode": "empty_text_fallback",
            "score": 0,
            "evidence": [{"source": "fallback", "reason": "empty_text"}],
            "candidates": [],
        }

    sample = text[:_DETECTION_SAMPLE]
    candidates = _score_all_configured_profiles_with_evidence(sample)
    if candidates:
        best = candidates[0]
        return {
            "profile": str(best["profile"]),
            "mode": "configured_rules",
            "score": int(best["score"]),
            "evidence": list(best["evidence"]),
            "candidates": candidates,
        }

    if _looks_like_serialized_table(sample):
        return {
            "profile": "spreadsheet",
            "mode": "serialized_table_heuristic",
            "score": 1,
            "evidence": [{"source": "serialized_table_heuristic"}],
            "candidates": [],
        }

    return {
        "profile": "general",
        "mode": "fallback",
        "score": 0,
        "evidence": [{"source": "fallback", "reason": "no_profile_rules_matched"}],
        "candidates": [],
    }


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

    return detect_profile_with_evidence(
        text,
        filename=filename,
        content_type=content_type,
    )["profile"]

def resolve_profile_with_evidence(
    profile: str | None,
    *,
    text: str | None = None,
    filename: str | None = None,
    content_type: str | None = None,
) -> ProfileDetectionResult:
    normalized = (profile or DEFAULT_PROFILE).lower().strip()
    if normalized == "auto":
        return detect_profile_with_evidence(
            text,
            filename=filename,
            content_type=content_type,
        )
    if normalized in get_profile_configs():
        return {
            "profile": normalized,
            "mode": "explicit",
            "score": 100,
            "evidence": [{"source": "explicit_profile", "profile": normalized}],
            "candidates": [],
        }
    return {
        "profile": "general",
        "mode": "unknown_profile_fallback",
        "score": 0,
        "evidence": [{"source": "fallback", "reason": "unknown_profile", "profile": normalized}],
        "candidates": [],
    }


def resolve_profile(
    profile: str | None,
    *,
    text: str | None = None,
    filename: str | None = None,
    content_type: str | None = None,
) -> str:
    """Resolve a possibly-``auto`` profile to a concrete profile name."""
    return resolve_profile_with_evidence(
        profile,
        text=text,
        filename=filename,
        content_type=content_type,
    )["profile"]


def profile_config(profile: str | None) -> dict[str, Any]:
    """Return the settings map for a concrete profile name."""
    return get_profile_config(profile)
