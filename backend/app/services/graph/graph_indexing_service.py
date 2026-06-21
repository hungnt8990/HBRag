from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from app.core.config import settings
from app.repositories.documents import DocumentRepository
from app.repositories.graph import GraphRepository
from app.schemas.documents import GraphIndexRequest, GraphIndexResponse
from app.services.graph.extractors.base import GraphExtractor
from app.services.graph.graph_merge_service import GraphMergeService
from app.services.graph.neo4j_client import Neo4jClient


class GraphIndexingDisabledError(RuntimeError):
    pass


class GraphIndexingError(RuntimeError):
    pass


class GraphDocumentChunksMissingError(ValueError):
    pass


@dataclass
class _RunningTotals:
    chunks_processed: int = 0
    entities_extracted: int = 0
    relations_extracted: int = 0
    merged_entities: int = 0
    merged_relations: int = 0


class GraphIndexingService:
    def __init__(
        self,
        *,
        document_repository: DocumentRepository,
        graph_repository: GraphRepository,
        neo4j_client: Neo4jClient,
        extractor: GraphExtractor,
        merge_service: GraphMergeService,
    ) -> None:
        self._document_repository = document_repository
        self._graph_repository = graph_repository
        self._neo4j_client = neo4j_client
        self._extractor = extractor
        self._merge_service = merge_service

    async def index_document(
        self,
        document_id: UUID,
        request: GraphIndexRequest,
    ) -> GraphIndexResponse:
        if not settings.graph_enabled:
            raise GraphIndexingDisabledError("GraphRAG is disabled. Set GRAPH_ENABLED=true.")

        document = await self._document_repository.get_document(document_id)
        if document is None:
            raise GraphIndexingError("Document not found.")

        chunks = await self._document_repository.list_chunks_for_document(document_id)
        chunks = [
            chunk
            for chunk in chunks
            if (chunk.chunk_metadata or {}).get("indexable", True)
            and (chunk.chunk_metadata or {}).get("embedding_enabled", True)
            and (chunk.chunk_metadata or {}).get("chunk_type")
            not in {"administrative_footer", "header_footer", "footer", "parse_error"}
        ]
        if not chunks:
            raise GraphDocumentChunksMissingError("Document has no chunks to graph index.")

        totals = _RunningTotals()
        merged_entities: dict[tuple[str, str], object] = {}
        merged_relations: dict[tuple[str, str, str], object] = {}

        await self._neo4j_client.verify_connectivity()
        await self._neo4j_client.create_constraints()
        if request.force_rebuild:
            await self._neo4j_client.reset_document_graph(str(document_id))
            await self._graph_repository.delete_extraction_logs(document_id=document_id)

        try:
            document_metadata = dict(getattr(document, "document_metadata", None) or {})
            organization_id = getattr(document, "organization_id", None)
            knowledge_base_id = getattr(document, "knowledge_base_id", None)
            await self._neo4j_client.upsert_document(
                {
                    "id": str(document.id),
                    "title": document.title,
                    "source_type": getattr(document, "source_type", None),
                    "status": getattr(document, "status", None),
                    "visibility": getattr(document, "visibility", None),
                    "organization_id": (
                        str(organization_id) if organization_id else None
                    ),
                    "knowledge_base_id": (
                        str(knowledge_base_id) if knowledge_base_id else None
                    ),
                    "id_vb": document_metadata.get("id_vb"),
                    "ky_hieu": document_metadata.get("ky_hieu") or document_metadata.get("doc_code"),
                    "trich_yeu": document_metadata.get("trich_yeu") or document_metadata.get("subject"),
                    "noi_ban_hanh": document_metadata.get("noi_ban_hanh") or document_metadata.get("issuing_org"),
                    "nguoi_ky": document_metadata.get("nguoi_ky"),
                    "ngay_vb": document_metadata.get("ngay_vb"),
                    "nam": document_metadata.get("nam"),
                    "thang": document_metadata.get("thang"),
                    "created_at": document.created_at.isoformat(),
                }
            )
            deterministic_entity_keys = await self._index_document_metadata_entities(
                document_id=str(document.id),
                metadata=document_metadata,
            )

            for chunk in chunks:
                chunk_metadata = dict(chunk.chunk_metadata or {})
                await self._neo4j_client.upsert_chunk(
                    {
                        "id": str(chunk.id),
                        "document_id": str(chunk.document_id),
                        "chunk_index": chunk.chunk_index,
                        "content_preview": chunk.content[:300],
                        "chunk_type": chunk_metadata.get("chunk_type"),
                        "content_format": chunk_metadata.get("content_format"),
                        "section_path": _safe_list(chunk_metadata.get("section_path")),
                        "table_name": chunk_metadata.get("table_name"),
                        "row_start": chunk_metadata.get("row_start"),
                        "row_end": chunk_metadata.get("row_end"),
                        "article_number": chunk_metadata.get("article_number"),
                        "article_title": chunk_metadata.get("article_title"),
                        "chapter_title": chunk_metadata.get("chapter_title"),
                        "id_vb": chunk_metadata.get("id_vb") or document_metadata.get("id_vb"),
                        "ky_hieu": chunk_metadata.get("ky_hieu") or document_metadata.get("ky_hieu"),
                        "feature_name": chunk_metadata.get("feature_name"),
                        "screen_name": chunk_metadata.get("screen_name"),
                        "phase": chunk_metadata.get("phase"),
                        "change_type": chunk_metadata.get("change_type"),
                    }
                )
                await self._neo4j_client.link_document_to_chunk(str(document.id), str(chunk.id))
                deterministic_entity_keys.update(
                    await self._index_chunk_metadata_entities(
                        document_id=str(document.id),
                        chunk_id=str(chunk.id),
                        metadata={**document_metadata, **chunk_metadata},
                    )
                )

                extraction = await self._extractor.extract(
                    content=chunk.content,
                    max_entities=request.max_entities_per_chunk,
                    max_relations=request.max_relations_per_chunk,
                )
                filtered_entities = [
                    entity
                    for entity in extraction.entities
                    if entity.confidence >= settings.graph_min_entity_confidence
                ]
                filtered_relations = [
                    relation
                    for relation in extraction.relationships
                    if relation.confidence >= settings.graph_min_relation_confidence
                ]

                entity_batch = list(merged_entities.values()) + filtered_entities
                relation_batch = list(merged_relations.values()) + filtered_relations
                merged_entity_list = self._merge_service.merge_entities(entity_batch)
                entity_lookup = {
                    entity.name: entity.normalized_name for entity in merged_entity_list
                }
                merged_relation_list = self._merge_service.merge_relations(
                    relation_batch,
                    entity_lookup=entity_lookup,
                )
                merged_entities = {
                    (entity.normalized_name, entity.type): entity for entity in merged_entity_list
                }
                merged_relations = {
                    (relation.source, relation.target, relation.type): relation
                    for relation in merged_relation_list
                }

                for entity in filtered_entities:
                    normalized_name = self._merge_service.normalize_entity_name(
                        entity.normalized_name or entity.name
                    )
                    entity_key = f"{normalized_name}::{entity.type}"
                    await self._neo4j_client.upsert_entity(
                        {
                            "entity_key": entity_key,
                            "name": entity.name,
                            "normalized_name": normalized_name,
                            "entity_type": entity.type,
                        }
                    )
                    await self._neo4j_client.link_chunk_to_entity(
                        chunk_id=str(chunk.id),
                        entity_key=entity_key,
                        weight=entity.confidence,
                        confidence=entity.confidence,
                    )
                    await self._neo4j_client.link_entity_to_supporting_chunk(
                        entity_key=entity_key,
                        chunk_id=str(chunk.id),
                    )

                for relation in filtered_relations:
                    source_name = self._merge_service.normalize_entity_name(relation.source)
                    target_name = self._merge_service.normalize_entity_name(relation.target)
                    source_entity = next(
                        (
                            item
                            for item in merged_entity_list
                            if item.normalized_name == source_name
                        ),
                        None,
                    )
                    target_entity = next(
                        (
                            item
                            for item in merged_entity_list
                            if item.normalized_name == target_name
                        ),
                        None,
                    )
                    if source_entity is None or target_entity is None:
                        continue
                    await self._neo4j_client.upsert_relationship(
                        source_key=f"{source_entity.normalized_name}::{source_entity.type}",
                        target_key=f"{target_entity.normalized_name}::{target_entity.type}",
                        relation_type=relation.type,
                        description=relation.description,
                        confidence=relation.confidence,
                        weight=relation.confidence,
                        source_chunk_id=str(chunk.id),
                    )

                totals.chunks_processed += 1
                totals.entities_extracted += len(filtered_entities)
                totals.relations_extracted += len(filtered_relations)
                totals.merged_entities = len(merged_entities)
                totals.merged_relations = len(merged_relations)

                await self._graph_repository.create_extraction_log(
                    document_id=document_id,
                    chunk_id=chunk.id,
                    status="success",
                    entity_count=len(filtered_entities),
                    relation_count=len(filtered_relations),
                    merged_entity_count=totals.merged_entities,
                    merged_relation_count=totals.merged_relations,
                    metadata={
                        "extractor_provider": request.extractor_provider,
                        "chunk_index": chunk.chunk_index,
                    },
                )

            totals.merged_entities = max(totals.merged_entities, len(deterministic_entity_keys))
            await self._graph_repository.upsert_document_status(
                document_id=document_id,
                graph_indexed=True,
                chunks_processed=totals.chunks_processed,
                entity_count=totals.merged_entities,
                relation_count=totals.merged_relations,
                error_message=None,
            )
            await self._graph_repository.commit()
        except Exception as exc:
            await self._graph_repository.create_extraction_log(
                document_id=document_id,
                chunk_id=None,
                status="failed",
                error_message=str(exc),
                metadata={"extractor_provider": request.extractor_provider},
            )
            await self._graph_repository.upsert_document_status(
                document_id=document_id,
                graph_indexed=False,
                chunks_processed=totals.chunks_processed,
                entity_count=totals.merged_entities,
                relation_count=totals.merged_relations,
                error_message=str(exc),
            )
            await self._graph_repository.commit()
            raise GraphIndexingError("Failed to index document graph.") from exc

        return GraphIndexResponse(
            document_id=document_id,
            chunks_processed=totals.chunks_processed,
            entities_extracted=totals.entities_extracted,
            relations_extracted=totals.relations_extracted,
            merged_entities=totals.merged_entities,
            merged_relations=totals.merged_relations,
            status="graph_indexed",
        )
    async def _index_document_metadata_entities(
        self,
        *,
        document_id: str,
        metadata: dict[str, Any],
    ) -> set[str]:
        keys: set[str] = set()
        for spec in _document_entity_specs(metadata):
            entity_key = _entity_key(spec["name"], spec["entity_type"])
            await self._neo4j_client.upsert_entity({**spec, "entity_key": entity_key})
            await self._neo4j_client.link_document_to_entity(
                document_id=document_id,
                entity_key=entity_key,
                relation_type=str(spec.get("document_relation") or "MENTIONS"),
                weight=1.0,
                confidence=1.0,
            )
            keys.add(entity_key)
        return keys

    async def _index_chunk_metadata_entities(
        self,
        *,
        document_id: str,
        chunk_id: str,
        metadata: dict[str, Any],
    ) -> set[str]:
        keys: set[str] = set()
        for spec in _chunk_entity_specs(metadata):
            entity_key = _entity_key(spec["name"], spec["entity_type"])
            await self._neo4j_client.upsert_entity({**spec, "entity_key": entity_key})
            await self._neo4j_client.link_document_to_entity(
                document_id=document_id,
                entity_key=entity_key,
                relation_type=str(spec.get("document_relation") or "MENTIONS"),
                weight=1.0,
                confidence=1.0,
            )
            await self._neo4j_client.link_chunk_to_entity(
                chunk_id=chunk_id,
                entity_key=entity_key,
                weight=1.0,
                confidence=1.0,
            )
            await self._neo4j_client.link_entity_to_supporting_chunk(
                entity_key=entity_key,
                chunk_id=chunk_id,
            )
            keys.add(entity_key)
        return keys


def _document_entity_specs(metadata: dict[str, Any]) -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []
    _add_specs(specs, metadata.get("noi_ban_hanh") or metadata.get("issuing_org") or metadata.get("issuer"), "organization", "ISSUED_BY")
    _add_specs(specs, metadata.get("nguoi_ky"), "person", "SIGNED_BY")
    _add_specs(specs, metadata.get("ky_hieu") or metadata.get("doc_code"), "document_code", "HAS_CODE")
    _add_specs(specs, metadata.get("id_vb"), "document_identifier", "HAS_CODE")
    _add_specs(specs, metadata.get("trich_yeu") or metadata.get("subject"), "topic", "HAS_TOPIC")
    _add_specs(specs, metadata.get("ngay_vb"), "date", "MENTIONS")
    for value in _safe_list(metadata.get("doc_codes")):
        _add_specs(specs, value, "document_code", "HAS_CODE")
    for value in _safe_list(metadata.get("identifiers")):
        _add_specs(specs, value, "document_identifier", "HAS_CODE")
    return _dedupe_specs(specs)


def _chunk_entity_specs(metadata: dict[str, Any]) -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []
    for key, entity_type in (
        ("table_name", "table"),
        ("feature_name", "feature"),
        ("screen_name", "screen"),
        ("platform", "platform"),
        ("phase", "phase"),
        ("change_type", "change_type"),
        ("change_topic", "topic"),
        ("content_type", "content_type"),
        ("person_name", "person"),
        ("area", "technology_area"),
        ("lead_department", "department"),
        ("unit", "organization"),
        ("article_number", "legal_article"),
        ("article_title", "legal_article_title"),
    ):
        _add_specs(specs, metadata.get(key), entity_type, "MENTIONS")
    for value in _safe_list(metadata.get("screen_names")):
        _add_specs(specs, value, "screen", "MENTIONS")
    for value in _safe_list(metadata.get("staff_names")):
        _add_specs(specs, value, "person", "MENTIONS")
    for value in _safe_list(metadata.get("doc_codes")):
        _add_specs(specs, value, "document_code", "HAS_CODE")
    for value in _safe_list(metadata.get("identifiers")):
        _add_specs(specs, value, "document_identifier", "HAS_CODE")
    for value in _safe_list(metadata.get("dates")):
        _add_specs(specs, value, "date", "MENTIONS")
    for value in _safe_list(metadata.get("section_path")):
        _add_specs(specs, value, "section", "MENTIONS")
    return _dedupe_specs(specs)


def _add_specs(specs: list[dict[str, Any]], value: Any, entity_type: str, relation: str) -> None:
    for item in _safe_list(value):
        clean = " ".join(str(item).split()).strip()
        if not clean:
            continue
        specs.append(
            {
                "name": clean,
                "normalized_name": _normalize_entity_name(clean),
                "entity_type": entity_type,
                "document_relation": relation,
            }
        )


def _dedupe_specs(specs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for spec in specs:
        key = _entity_key(spec["name"], spec["entity_type"])
        if key in seen:
            continue
        seen.add(key)
        out.append(spec)
    return out


def _entity_key(name: Any, entity_type: Any) -> str:
    return f"{_normalize_entity_name(str(name))}::{str(entity_type or 'other').strip().casefold() or 'other'}"


def _normalize_entity_name(value: str) -> str:
    normalized = unicodedata.normalize("NFD", value or "")
    normalized = "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")
    normalized = normalized.replace("Đ", "D").replace("đ", "d")
    normalized = re.sub(r"\s+", " ", normalized.casefold()).strip()
    return normalized


def _safe_list(value: Any) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, list | tuple | set):
        return [str(item) for item in value if str(item).strip()]
    return [str(value)]
