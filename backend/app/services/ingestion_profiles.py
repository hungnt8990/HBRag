from __future__ import annotations

import copy
from typing import Any

from app.repositories.ingestion_profiles import IngestionProfileRepository

DEFAULT_PROFILE = "auto"

ENRICHMENT_RUNTIME_CONFIG_KEYS: tuple[str, ...] = tuple(
    f"{prefix}_enrichment_{suffix}"
    for prefix in ("chunk", "embedding", "reingest")
    for suffix in ("provider", "base_url", "model", "max_chars", "version")
)

ENRICHMENT_CONFIG_KEYS: tuple[str, ...] = ENRICHMENT_RUNTIME_CONFIG_KEYS + (
    "chunk_enrichment_enabled",
    "embedding_enrichment_enabled",
    "retrieval_enrichment_enabled",
    "enrichment_force_on_reingest",
    "enrichment_update_keyword_search_vector",
)

# These values are seed defaults only. Runtime edits are stored in Postgres and
# cached in-process so sync chunkers can keep their existing call contract.
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
    "staff_technology_matrix": {
        "chunk_mode": "table_aware",
        "chunk_size": 1600,
        "chunk_overlap": 120,
        "top_k": 12,
        "candidate_k": 80,
        "answer_mode": "hybrid",
        "answer_style": "table_qa",
        "max_context_chars": 10000,
        "heading_rules": [],
        "detect_rules": {
            "title_keywords": [
                "nhiệm vụ các mảng công nghệ",
                "mảng công nghệ nền tảng ai",
                "danh sách nhân sự",
                "nhân sự phụ trách",
                "nhân sự đề xuất",
            ],
            "table_headers": [
                "STT",
                "Mảng công nghệ",
                "Phòng chủ trì",
                "Nhân sự đề xuất",
                "Nhân sự tham gia",
                "Mục tiêu",
            ],
            "min_score": 6,
        },
        "semantic_chunk_types": [
            "person_technology_assignment",
            "technology_area_summary",
            "staff_matrix_row",
        ],
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


_PROFILE_CONFIG_CACHE: dict[str, dict[str, Any]] | None = None


def _normalize_profile_name(profile: str | None) -> str:
    return str(profile or "").strip().lower()

def _normalize_config(config: dict[str, Any]) -> dict[str, Any]:
    raw_config = copy.deepcopy(config)
    normalized = {**FALLBACK_CONFIG, **raw_config}
    for key in ENRICHMENT_CONFIG_KEYS:
        normalized.pop(key, None)
    return normalized


def _merge_profile_configs(
    configs: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for name, config in configs.items():
        if not isinstance(name, str) or not isinstance(config, dict):
            continue
        normalized = _normalize_profile_name(name)
        if not normalized or normalized == DEFAULT_PROFILE:
            continue
        merged[normalized] = _normalize_config(config)
    return merged


def set_profile_configs(configs: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    global _PROFILE_CONFIG_CACHE
    _PROFILE_CONFIG_CACHE = _merge_profile_configs(configs)
    return get_profile_configs()


async def load_profile_configs(
    repository: IngestionProfileRepository,
) -> dict[str, dict[str, Any]]:
    await repository.seed_missing_profile_configs(BOOTSTRAP_PROFILE_CONFIGS)
    configs = await repository.list_profile_configs()
    return set_profile_configs(configs)


async def save_profile_config_to_database(
    repository: IngestionProfileRepository,
    profile: str,
    config: dict[str, Any],
) -> dict[str, Any]:
    normalized = _normalize_profile_name(profile)
    if not normalized or normalized == DEFAULT_PROFILE:
        raise ValueError("profile must be a concrete profile name")
    merged = _normalize_config(config)
    await repository.seed_missing_profile_configs(BOOTSTRAP_PROFILE_CONFIGS)
    await repository.upsert_profile_config(normalized, merged)
    configs = await repository.list_profile_configs()
    set_profile_configs(configs)
    return copy.deepcopy(get_profile_config(normalized))


def get_profile_configs() -> dict[str, dict[str, Any]]:
    if _PROFILE_CONFIG_CACHE is not None:
        return copy.deepcopy(_PROFILE_CONFIG_CACHE)
    return _merge_profile_configs(copy.deepcopy(BOOTSTRAP_PROFILE_CONFIGS))


def get_profile_names() -> tuple[str, ...]:
    return ("auto", *tuple(get_profile_configs().keys()))


def get_profile_config(profile: str | None) -> dict[str, Any]:
    configs = get_profile_configs()
    if profile and profile in configs:
        return copy.deepcopy(configs[profile])
    return copy.deepcopy(FALLBACK_CONFIG)


def save_profile_config(profile: str, config: dict[str, Any]) -> dict[str, Any]:
    normalized = _normalize_profile_name(profile)
    if not normalized or normalized == DEFAULT_PROFILE:
        raise ValueError("profile must be a concrete profile name")
    configs = get_profile_configs()
    configs[normalized] = _normalize_config(config)
    set_profile_configs(configs)
    return copy.deepcopy(configs[normalized])
