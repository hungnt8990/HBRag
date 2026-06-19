from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from app.core.config import settings
from app.repositories.rag_runtime_config import RagRuntimeConfigRepository

DEFAULT_RAG_CONFIG_NAME = "default"


@dataclass(frozen=True)
class RagRuntimeConfigValues:
    enable_chunk_enrichment_at_ingest: bool
    enable_chunk_enrichment_at_retrieval: bool
    enable_knowledge_artifact_compilation: bool
    enable_llm_artifact_extraction: bool
    enable_artifact_first_retrieval: bool
    enable_chunk_fallback: bool
    enable_neighbor_expansion: bool
    enable_graph_expansion: bool
    artifact_confidence_threshold: float
    retrieval_token_budget: int
    max_artifacts: int
    max_chunks: int

    def model_dump(self) -> dict[str, Any]:
        return asdict(self)


def default_rag_runtime_config() -> RagRuntimeConfigValues:
    return RagRuntimeConfigValues(
        enable_chunk_enrichment_at_ingest=bool(
            getattr(settings, "enable_chunk_enrichment_at_ingest", settings.chunk_enrichment_enabled)
            or settings.chunk_enrichment_enabled
        ),
        enable_chunk_enrichment_at_retrieval=bool(
            getattr(settings, "enable_chunk_enrichment_at_retrieval", settings.retrieval_enrichment_enabled)
            or settings.retrieval_enrichment_enabled
        ),
        enable_knowledge_artifact_compilation=bool(settings.enable_knowledge_artifact_compilation),
        enable_llm_artifact_extraction=bool(settings.enable_llm_artifact_extraction),
        enable_artifact_first_retrieval=bool(settings.enable_artifact_first_retrieval),
        enable_chunk_fallback=bool(settings.enable_chunk_fallback),
        enable_neighbor_expansion=bool(
            getattr(settings, "enable_neighbor_expansion", settings.enable_context_expansion)
            and settings.enable_context_expansion
        ),
        enable_graph_expansion=bool(
            getattr(settings, "enable_graph_expansion", settings.graph_expansion_enabled)
            and settings.graph_expansion_enabled
        ),
        artifact_confidence_threshold=_bounded_float(
            settings.artifact_confidence_threshold,
            default=0.45,
            minimum=0.0,
            maximum=1.0,
        ),
        retrieval_token_budget=_positive_int(settings.retrieval_token_budget, default=6000),
        max_artifacts=_positive_int(settings.max_artifacts, default=6),
        max_chunks=_positive_int(settings.max_chunks, default=8),
    )


async def load_rag_runtime_config(
    repository: RagRuntimeConfigRepository,
    *,
    config_name: str = DEFAULT_RAG_CONFIG_NAME,
) -> RagRuntimeConfigValues:
    defaults = default_rag_runtime_config().model_dump()
    await repository.seed_missing_configs({config_name: defaults})
    model = await repository.get_config(config_name)
    db_config = dict(getattr(model, "config", None) or {}) if model is not None else {}
    return normalize_rag_runtime_config({**defaults, **db_config})


async def save_rag_runtime_config(
    repository: RagRuntimeConfigRepository,
    config: dict[str, Any],
    *,
    config_name: str = DEFAULT_RAG_CONFIG_NAME,
) -> RagRuntimeConfigValues:
    normalized = normalize_rag_runtime_config(
        {**default_rag_runtime_config().model_dump(), **dict(config or {})}
    )
    await repository.upsert_config(config_name, normalized.model_dump())
    return normalized


def normalize_rag_runtime_config(config: dict[str, Any]) -> RagRuntimeConfigValues:
    defaults = default_rag_runtime_config()
    return RagRuntimeConfigValues(
        enable_chunk_enrichment_at_ingest=_bool_value(
            config.get("enable_chunk_enrichment_at_ingest"),
            default=defaults.enable_chunk_enrichment_at_ingest,
        ),
        enable_chunk_enrichment_at_retrieval=_bool_value(
            config.get("enable_chunk_enrichment_at_retrieval"),
            default=defaults.enable_chunk_enrichment_at_retrieval,
        ),
        enable_knowledge_artifact_compilation=_bool_value(
            config.get("enable_knowledge_artifact_compilation"),
            default=defaults.enable_knowledge_artifact_compilation,
        ),
        enable_llm_artifact_extraction=_bool_value(
            config.get("enable_llm_artifact_extraction"),
            default=defaults.enable_llm_artifact_extraction,
        ),
        enable_artifact_first_retrieval=_bool_value(
            config.get("enable_artifact_first_retrieval"),
            default=defaults.enable_artifact_first_retrieval,
        ),
        enable_chunk_fallback=_bool_value(
            config.get("enable_chunk_fallback"),
            default=defaults.enable_chunk_fallback,
        ),
        enable_neighbor_expansion=_bool_value(
            config.get("enable_neighbor_expansion"),
            default=defaults.enable_neighbor_expansion,
        ),
        enable_graph_expansion=_bool_value(
            config.get("enable_graph_expansion"),
            default=defaults.enable_graph_expansion,
        ),
        artifact_confidence_threshold=_bounded_float(
            config.get("artifact_confidence_threshold"),
            default=defaults.artifact_confidence_threshold,
            minimum=0.0,
            maximum=1.0,
        ),
        retrieval_token_budget=_positive_int(
            config.get("retrieval_token_budget"),
            default=defaults.retrieval_token_budget,
        ),
        max_artifacts=_positive_int(config.get("max_artifacts"), default=defaults.max_artifacts),
        max_chunks=_positive_int(config.get("max_chunks"), default=defaults.max_chunks),
    )


def _bool_value(value: Any, *, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in {"1", "true", "yes", "on", "enabled"}:
            return True
        if normalized in {"0", "false", "no", "off", "disabled"}:
            return False
    return bool(value)


def _positive_int(value: Any, *, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _bounded_float(value: Any, *, default: float, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    if parsed < minimum:
        return minimum
    if parsed > maximum:
        return maximum
    return parsed

