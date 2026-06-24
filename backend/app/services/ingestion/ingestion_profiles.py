from __future__ import annotations

import copy
import re
import unicodedata
from typing import Any

from app.repositories.ingestion_profiles import IngestionProfileRepository
from app.services.queries.query_intent_rules import (
    DEFAULT_QUERY_INTENT_RULES,
    normalize_query_intent_rules,
)

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

DEFAULT_CHUNK_METADATA_RULES: list[dict[str, Any]] = [
    {
        "name": "structural_schema_overview",
        "enabled": True,
        "retrieval_role": "structural_schema_overview",
        "priority": 0,
        "match": {
            "chunk_types": [
                "attribute_table_schema",
                "gis_relationship_schema",
                "relationship_definition",
                "schema_object_summary",
            ],
            "metadata_keys": ["relationship_name"],
            "text_terms": [
                "bảng dữ liệu thuộc tính",
                "lớp dữ liệu GIS",
                "mối quan hệ",
                "relationship",
                "schema overview",
                "object summary",
            ],
        },
        "set_metadata": {"schema_overview": True},
    },
    {
        "name": "schema_field_detail",
        "enabled": True,
        "retrieval_role": "field_level_schema",
        "priority": 90,
        "match": {
            "chunk_types": ["schema_field_row"],
            "metadata_keys": ["field_name"],
        },
        "set_metadata": {"field_level_schema": True},
    },
    {
        "name": "schema_field_collection",
        "enabled": True,
        "retrieval_role": "field_level_schema",
        "priority": 80,
        "match": {
            "chunk_types": ["table_parent", "table_complete", "table_rows"],
            "metadata_keys": ["field_names"],
        },
        "set_metadata": {"field_level_schema": True},
    },
]

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
    "query_intent_rules": copy.deepcopy(DEFAULT_QUERY_INTENT_RULES),
    "chunk_metadata_rules": copy.deepcopy(DEFAULT_CHUNK_METADATA_RULES),
}


_PROFILE_CONFIG_CACHE: dict[str, dict[str, Any]] | None = None


def _normalize_profile_name(profile: str | None) -> str:
    return str(profile or "").strip().lower()

def _normalize_config(config: dict[str, Any]) -> dict[str, Any]:
    raw_config = copy.deepcopy(config)
    normalized = {**FALLBACK_CONFIG, **raw_config}
    normalized["query_intent_rules"] = normalize_query_intent_rules(
        normalized.get("query_intent_rules")
    )
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

def apply_chunk_metadata_rules(
    metadata: dict[str, Any],
    *,
    content: str,
    config: dict[str, Any],
) -> dict[str, Any]:
    """Apply profile-configured metadata rules to one chunk record.

    RAG retrieval should consume stable metadata signals instead of re-scanning
    source text for domain phrases at answer time. The phrases and matching
    conditions live in ingestion profile config so admins can edit them and
    reingest documents when a document family needs a different policy.
    """

    updated = copy.deepcopy(metadata)
    rules = config.get("chunk_metadata_rules")
    if not isinstance(rules, list):
        return updated

    roles = _string_list(updated.get("retrieval_roles"))
    priority = _optional_int(updated.get("schema_coverage_priority"))
    for raw_rule in rules:
        if not isinstance(raw_rule, dict) or raw_rule.get("enabled") is False:
            continue
        if not _chunk_metadata_rule_matches(raw_rule, updated, content=content):
            continue

        role = str(raw_rule.get("retrieval_role") or raw_rule.get("name") or "").strip()
        if role and role not in roles:
            roles.append(role)
        set_metadata = raw_rule.get("set_metadata")
        if isinstance(set_metadata, dict):
            for key, value in set_metadata.items():
                if isinstance(key, str) and key:
                    updated[key] = copy.deepcopy(value)
        rule_priority = _optional_int(raw_rule.get("priority"))
        if rule_priority is not None:
            priority = rule_priority if priority is None else min(priority, rule_priority)

    if roles:
        updated["retrieval_roles"] = roles
    if priority is not None:
        updated["schema_coverage_priority"] = priority
    return updated

def _chunk_metadata_rule_matches(
    rule: dict[str, Any],
    metadata: dict[str, Any],
    *,
    content: str,
) -> bool:
    matcher = rule.get("match")
    if not isinstance(matcher, dict):
        return False

    chunk_type = str(metadata.get("chunk_type") or "").strip()
    chunk_types = set(_string_list(matcher.get("chunk_types")))
    if chunk_type and chunk_type in chunk_types:
        return True

    for key in _string_list(matcher.get("metadata_keys")):
        value = metadata.get(key)
        if value not in (None, "", [], {}):
            return True

    normalized_text = _normalize_rule_text(
        "\n".join(
            str(part or "")
            for part in (
                content,
                metadata.get("section_title"),
                metadata.get("section_path"),
                metadata.get("headings"),
                metadata.get("document_context"),
            )
        )
    )
    return any(
        (term_key := _normalize_rule_text(term)) and term_key in normalized_text
        for term in _string_list(matcher.get("text_terms"))
    )

def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        values = [value]
    elif isinstance(value, (list, tuple, set)):
        values = [str(item) for item in value]
    else:
        values = [str(value)]
    return [item.strip() for item in values if item.strip()]

def _optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None

def _normalize_rule_text(value: Any) -> str:
    decomposed = unicodedata.normalize("NFD", str(value or ""))
    stripped = "".join(char for char in decomposed if unicodedata.category(char) != "Mn")
    stripped = stripped.replace("Đ", "D").replace("đ", "d")
    return re.sub(r"\s+", " ", stripped.casefold()).strip()
