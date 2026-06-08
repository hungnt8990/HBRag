from __future__ import annotations

import re
from typing import Any, Literal

ProfileName = Literal[
    "auto",
    "legal_admin",
    "general",
    "technical",
    "faq",
    "spreadsheet",
]

DEFAULT_PROFILE = "auto"
PROFILE_NAMES: tuple[str, ...] = (
    "auto",
    "legal_admin",
    "general",
    "technical",
    "faq",
    "spreadsheet",
)

# Settings applied when no profile is selected and no document profile exists.
# Preserves the historical chat defaults so behavior is unchanged by default.
FALLBACK_CONFIG: dict[str, Any] = {
    "chunk_mode": "recursive",
    "chunk_size": 1000,
    "chunk_overlap": 150,
    "top_k": 5,
    "candidate_k": 20,
    "answer_mode": "hybrid",
    "answer_style": "policy_explainer",
    "max_context_chars": 6000,
}

PROFILE_CONFIGS: dict[str, dict[str, Any]] = {
    "legal_admin": {
        "chunk_mode": "legal_article",
        "chunk_size": 2500,
        "chunk_overlap": 300,
        "top_k": 8,
        "candidate_k": 40,
        "answer_mode": "hybrid",
        "answer_style": "policy_explainer",
        "max_context_chars": 8000,
    },
    "general": {
        "chunk_mode": "recursive",
        "chunk_size": 1000,
        "chunk_overlap": 150,
        "top_k": 5,
        "candidate_k": 20,
        "answer_mode": "hybrid",
        "answer_style": "detailed",
        "max_context_chars": 6000,
    },
    "technical": {
        "chunk_mode": "recursive",
        "chunk_size": 1200,
        "chunk_overlap": 200,
        "top_k": 6,
        "candidate_k": 30,
        "answer_mode": "hybrid",
        "answer_style": "detailed",
        "max_context_chars": 7000,
    },
    "faq": {
        "chunk_mode": "recursive",
        "chunk_size": 700,
        "chunk_overlap": 100,
        "top_k": 5,
        "candidate_k": 20,
        "answer_mode": "hybrid",
        "answer_style": "concise",
        "max_context_chars": 5000,
    },
    "spreadsheet": {
        "chunk_mode": "table_aware",
        "chunk_size": 1800,
        "chunk_overlap": 200,
        "top_k": 8,
        "candidate_k": 40,
        "answer_mode": "extractive",
        "answer_style": "table_qa",
        "max_context_chars": 9000,
    },
}

_ARTICLE_RE = re.compile(r"(?m)^\s*Điều\s+\d+")
_CHAPTER_RE = re.compile(r"(?mi)^\s*CHƯƠNG\s+")
_FAQ_RE = re.compile(r"(?mi)^\s*(câu hỏi|hỏi|trả lời|đáp án|đáp|q:|a:)\b")
_TECH_RE = re.compile(
    r"```|\bAPI\b|\bHTTP\b|\bSELECT\b|\bfunction\b|\bclass\s|\bdef\s|\bendpoint\b",
)
_DETECTION_SAMPLE = 20000


def detect_profile(text: str | None) -> str:
    """Heuristically detect a document profile from parsed text."""
    if not text or not text.strip():
        return "general"

    sample = text[:_DETECTION_SAMPLE]

    article_count = len(_ARTICLE_RE.findall(sample))
    chapter_count = len(_CHAPTER_RE.findall(sample))
    if article_count >= 3 or (article_count >= 1 and chapter_count >= 1):
        return "legal_admin"

    lines = [line for line in sample.splitlines() if line.strip()]
    if lines:
        serialized_row_lines = sum(1 for line in lines if line.startswith("TABLE_ROW "))
        pipe_lines = sum(1 for line in lines if " | " in line)
        if serialized_row_lines >= 2:
            return "spreadsheet"
        if pipe_lines / len(lines) >= 0.3:
            return "spreadsheet"

    if len(_FAQ_RE.findall(sample)) >= 3:
        return "faq"

    if len(_TECH_RE.findall(sample)) >= 3:
        return "technical"

    return "general"


def resolve_profile(profile: str | None, *, text: str | None = None) -> str:
    """Resolve a possibly-``auto`` profile to a concrete profile name."""
    normalized = (profile or DEFAULT_PROFILE).lower().strip()
    if normalized == "auto":
        return detect_profile(text)
    if normalized in PROFILE_CONFIGS:
        return normalized
    return "general"


def profile_config(profile: str | None) -> dict[str, Any]:
    """Return the settings map for a concrete profile name."""
    if profile and profile in PROFILE_CONFIGS:
        return dict(PROFILE_CONFIGS[profile])
    return dict(FALLBACK_CONFIG)
