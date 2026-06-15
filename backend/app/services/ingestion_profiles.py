from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

PROFILE_CONFIG_PATH = Path("data/ingestion_profiles.json")
DEFAULT_PROFILE = "auto"

# These values are bootstrap defaults only. At runtime the admin UI can persist
# profile config to data/ingestion_profiles.json, and chunking code reads the
# saved config instead of embedding document-language rules in chunkers.
BOOTSTRAP_PROFILE_CONFIGS: dict[str, dict[str, Any]] = {
    "legal_admin": {
        "chunk_mode": "legal_article",
        "chunk_size": 2500,
        "chunk_overlap": 300,
        "top_k": 8,
        "candidate_k": 40,
        "answer_mode": "hybrid",
        "answer_style": "policy_explainer",
        "max_context_chars": 8000,
        "heading_rules": [
            {
                "name": "chapter",
                "level": 1,
                "pattern": r"^[ \t]*(?P<label>CHƯƠNG)\s+(?P<number>[IVXLCDM]+|\d+)\s*[\.\:\-]?[ \t]*(?P<title>.*)$",
                "metadata_key": "chapter_title",
                "metadata_value": "display_text",
                "boundary": False,
                "enabled": True,
            },
            {
                "name": "article",
                "level": 2,
                "pattern": r"^[ \t]*(?P<label>Điều)\s+(?P<number>\d+)\s*[\.\:\-]?[ \t]*(?P<title>.*)$",
                "metadata_key": "article_title",
                "number_metadata_key": "article_number",
                "boundary": True,
                "enabled": True,
            },
        ],
    },
    "catalog_table": {
        "chunk_mode": "table_aware",
        "chunk_size": 1800,
        "chunk_overlap": 120,
        "top_k": 12,
        "candidate_k": 60,
        "answer_mode": "hybrid",
        "answer_style": "table_qa",
        "max_context_chars": 10000,
        "heading_rules": [],
        "detect_rules": {
            "title_keywords": [
                "danh mục",
                "ngôn ngữ lập trình",
                "platform",
                "framework",
                "công nghệ dùng chung",
                "công cụ sử dụng",
            ],
            "table_headers": [
                "TT",
                "Thành phần công nghệ",
                "Công cụ sử dụng",
                "Hãng sản xuất",
                "Nhà cung cấp",
                "Mục đích sử dụng",
                "Staging",
                "Production",
            ],
            "min_score": 6,
        },
        "semantic_chunk_types": [
            "catalog_summary",
            "catalog_group_chunk",
            "catalog_row_chunk",
        ],
        "aliases": {
            "RabitMQ": "RabbitMQ",
            "Habor": "Harbor",
            "Gafana": "Grafana",
            "Azure Piplelines": "Azure Pipelines",
            "Springboot": "Spring Boot",
            ".Net Core": ".NET Core",
        },
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
        "heading_rules": [],
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
        "heading_rules": [],
    },
    "slide": {
        "chunk_mode": "slide_page",
        "chunk_size": 1200,
        "chunk_overlap": 0,
        "top_k": 8,
        "candidate_k": 40,
        "answer_mode": "hybrid",
        "answer_style": "detailed",
        "max_context_chars": 8000,
        "heading_rules": [],
    },
}

FALLBACK_CONFIG: dict[str, Any] = {
    "chunk_mode": "recursive",
    "chunk_size": 1000,
    "chunk_overlap": 150,
    "top_k": 5,
    "candidate_k": 20,
    "answer_mode": "hybrid",
    "answer_style": "policy_explainer",
    "max_context_chars": 6000,
    "heading_rules": [],
}


def _load_file_configs() -> dict[str, dict[str, Any]] | None:
    if not PROFILE_CONFIG_PATH.exists():
        return None
    try:
        data = json.loads(PROFILE_CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    profiles = data.get("profiles") if isinstance(data, dict) else None
    if not isinstance(profiles, dict):
        return None
    configs: dict[str, dict[str, Any]] = {}
    for name, config in profiles.items():
        if isinstance(name, str) and isinstance(config, dict):
            configs[name] = config
    return configs or None


def get_profile_configs() -> dict[str, dict[str, Any]]:
    configs = _load_file_configs()
    if configs is None:
        configs = BOOTSTRAP_PROFILE_CONFIGS
    merged: dict[str, dict[str, Any]] = {}
    for name, config in configs.items():
        merged[name] = {**FALLBACK_CONFIG, **copy.deepcopy(config)}
    return merged


def get_profile_names() -> tuple[str, ...]:
    return ("auto", *tuple(get_profile_configs().keys()))


def get_profile_config(profile: str | None) -> dict[str, Any]:
    configs = get_profile_configs()
    if profile and profile in configs:
        return copy.deepcopy(configs[profile])
    return copy.deepcopy(FALLBACK_CONFIG)


def save_profile_config(profile: str, config: dict[str, Any]) -> dict[str, Any]:
    normalized = profile.strip().lower()
    if not normalized or normalized == "auto":
        raise ValueError("profile must be a concrete profile name")
    configs = get_profile_configs()
    configs[normalized] = {**FALLBACK_CONFIG, **copy.deepcopy(config)}
    PROFILE_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    PROFILE_CONFIG_PATH.write_text(
        json.dumps({"default_profile": DEFAULT_PROFILE, "profiles": configs}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return copy.deepcopy(configs[normalized])
