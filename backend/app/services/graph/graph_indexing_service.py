from __future__ import annotations

from dataclasses import dataclass
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
            await self._neo4j_client.upsert_document(
                {
                    "id": str(document.id),
                    "title": document.title,
                    "organization_id": (
                        str(document.organization_id) if document.organization_id else None
                    ),
                    "created_at": document.created_at.isoformat(),
                }
            )

            for chunk in chunks:
                await self._neo4j_client.upsert_chunk(
                    {
                        "id": str(chunk.id),
                        "document_id": str(chunk.document_id),
                        "chunk_index": chunk.chunk_index,
                        "content_preview": chunk.content[:300],
                        "article_number": (chunk.chunk_metadata or {}).get("article_number"),
                        "article_title": (chunk.chunk_metadata or {}).get("article_title"),
                        "chapter_title": (chunk.chunk_metadata or {}).get("chapter_title"),
                    }
                )
                await self._neo4j_client.link_document_to_chunk(str(document.id), str(chunk.id))

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
