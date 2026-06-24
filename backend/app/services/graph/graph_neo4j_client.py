from __future__ import annotations

from functools import lru_cache
from typing import Any

from neo4j import AsyncGraphDatabase

from app.core.config import settings
from app.services.graph.graph_models import GraphChunkCandidate


class Neo4jClient:
    def __init__(
        self,
        *,
        uri: str | None = None,
        username: str | None = None,
        password: str | None = None,
    ) -> None:
        self._driver = AsyncGraphDatabase.driver(
            uri or settings.neo4j_uri,
            auth=(username or settings.neo4j_username, password or settings.neo4j_password),
        )

    async def verify_connectivity(self) -> None:
        await self._driver.verify_connectivity()

    async def close(self) -> None:
        await self._driver.close()

    async def create_constraints(self) -> None:
        statements = [
            "CREATE CONSTRAINT document_id_unique IF NOT EXISTS "
            "FOR (d:Document) REQUIRE d.id IS UNIQUE",
            "CREATE CONSTRAINT chunk_id_unique IF NOT EXISTS "
            "FOR (c:Chunk) REQUIRE c.id IS UNIQUE",
            "CREATE CONSTRAINT entity_key_unique IF NOT EXISTS "
            "FOR (e:Entity) REQUIRE e.entity_key IS UNIQUE",
            "CREATE INDEX entity_normalized_name IF NOT EXISTS "
            "FOR (e:Entity) ON (e.normalized_name)",
            "CREATE INDEX entity_type IF NOT EXISTS "
            "FOR (e:Entity) ON (e.entity_type)",
            "CREATE INDEX chunk_document_id IF NOT EXISTS "
            "FOR (c:Chunk) ON (c.document_id)",
        ]
        async with self._driver.session() as session:
            for statement in statements:
                await session.run(statement)

    async def reset_document_graph(self, document_id: str) -> None:
        query = """
        MATCH (d:Document {id: $document_id})
        OPTIONAL MATCH (d)-[:HAS_CHUNK]->(c:Chunk)
        DETACH DELETE c
        WITH d
        DETACH DELETE d
        """
        async with self._driver.session() as session:
            await session.run(query, document_id=document_id)

    async def upsert_document(self, payload: dict[str, Any]) -> None:
        properties = {key: value for key, value in payload.items() if key != "id" and value is not None}
        query = """
        MERGE (d:Document {id: $id})
        SET d += $properties
        """
        async with self._driver.session() as session:
            await session.run(query, id=payload["id"], properties=properties)

    async def upsert_chunk(self, payload: dict[str, Any]) -> None:
        properties = {key: value for key, value in payload.items() if key != "id" and value is not None}
        query = """
        MERGE (c:Chunk {id: $id})
        SET c += $properties
        """
        async with self._driver.session() as session:
            await session.run(query, id=payload["id"], properties=properties)

    async def link_document_to_chunk(self, document_id: str, chunk_id: str) -> None:
        query = """
        MATCH (d:Document {id: $document_id}), (c:Chunk {id: $chunk_id})
        MERGE (d)-[:HAS_CHUNK]->(c)
        """
        async with self._driver.session() as session:
            await session.run(query, document_id=document_id, chunk_id=chunk_id)

    async def link_document_to_entity(
        self,
        *,
        document_id: str,
        entity_key: str,
        relation_type: str = "MENTIONS",
        weight: float = 1.0,
        confidence: float = 1.0,
    ) -> None:
        # Relationship types cannot be parametrized in Cypher. Keep a small allowlist
        # and use a generic edge for everything else.
        safe_relation_type = relation_type if relation_type in {"MENTIONS", "ISSUED_BY", "SIGNED_BY", "HAS_CODE", "HAS_TOPIC"} else "MENTIONS"
        query = f"""
        MATCH (d:Document {{id: $document_id}}), (e:Entity {{entity_key: $entity_key}})
        MERGE (d)-[r:{safe_relation_type}]->(e)
        SET r.weight = $weight,
            r.confidence = $confidence
        """
        async with self._driver.session() as session:
            await session.run(
                query,
                document_id=document_id,
                entity_key=entity_key,
                weight=weight,
                confidence=confidence,
            )

    async def upsert_entity(self, payload: dict[str, Any]) -> None:
        query = """
        MERGE (e:Entity {entity_key: $entity_key})
        SET e.name = $name,
            e.normalized_name = $normalized_name,
            e.entity_type = $entity_type
        """
        async with self._driver.session() as session:
            await session.run(query, **payload)

    async def link_chunk_to_entity(
        self,
        *,
        chunk_id: str,
        entity_key: str,
        weight: float,
        confidence: float,
    ) -> None:
        query = """
        MATCH (c:Chunk {id: $chunk_id}), (e:Entity {entity_key: $entity_key})
        MERGE (c)-[r:MENTIONS]->(e)
        SET r.weight = $weight,
            r.confidence = $confidence
        """
        async with self._driver.session() as session:
            await session.run(
                query,
                chunk_id=chunk_id,
                entity_key=entity_key,
                weight=weight,
                confidence=confidence,
            )

    async def link_entity_to_supporting_chunk(self, *, entity_key: str, chunk_id: str) -> None:
        query = """
        MATCH (e:Entity {entity_key: $entity_key}), (c:Chunk {id: $chunk_id})
        MERGE (e)-[r:SUPPORTED_BY]->(c)
        SET r.chunk_id = $chunk_id
        """
        async with self._driver.session() as session:
            await session.run(query, entity_key=entity_key, chunk_id=chunk_id)

    async def upsert_relationship(
        self,
        *,
        source_key: str,
        target_key: str,
        relation_type: str,
        description: str,
        confidence: float,
        weight: float,
        source_chunk_id: str,
    ) -> None:
        query = """
        MATCH (s:Entity {entity_key: $source_key}), (t:Entity {entity_key: $target_key})
        MERGE (s)-[r:RELATED_TO {
            relation_type: $relation_type,
            source_chunk_id: $source_chunk_id
        }]->(t)
        SET r.description = $description,
            r.confidence = $confidence,
            r.weight = $weight
        """
        async with self._driver.session() as session:
            await session.run(
                query,
                source_key=source_key,
                target_key=target_key,
                relation_type=relation_type,
                description=description,
                confidence=confidence,
                weight=weight,
                source_chunk_id=source_chunk_id,
            )

    async def expand_related_chunks(
        self,
        *,
        query_terms: list[str],
        seed_chunk_ids: list[str],
        visible_document_ids: list[str],
        depth: int,
        limit: int,
    ) -> list[GraphChunkCandidate]:
        if not query_terms and not seed_chunk_ids:
            return []
        query = """
        WITH $query_terms AS query_terms, $seed_chunk_ids AS seed_chunk_ids
        MATCH (entity:Entity)
        WHERE any(term IN query_terms WHERE entity.normalized_name CONTAINS term)
           OR EXISTS {
             MATCH (seed:Chunk)-[:MENTIONS]->(entity)
             WHERE seed.id IN seed_chunk_ids
           }
        CALL {
          WITH entity
          MATCH path=(entity)-[:RELATED_TO*0..$depth]-(related:Entity)
          RETURN collect(DISTINCT related) + [entity] AS related_entities
        }
        UNWIND related_entities AS related_entity
        MATCH (related_entity)-[support:SUPPORTED_BY]->(chunk:Chunk)
        WHERE chunk.document_id IN $visible_document_ids
        RETURN chunk.id AS chunk_id,
               chunk.document_id AS document_id,
               chunk.content_preview AS content_preview,
               chunk.article_number AS article_number,
               chunk.article_title AS article_title,
               chunk.chapter_title AS chapter_title,
               collect(DISTINCT related_entity.name) AS matched_entities,
               collect(DISTINCT support.chunk_id) AS support_chunk_ids,
               count(DISTINCT related_entity) AS score
        ORDER BY score DESC, chunk.chunk_index ASC
        LIMIT $limit
        """
        async with self._driver.session() as session:
            records = await session.run(
                query,
                query_terms=query_terms,
                seed_chunk_ids=seed_chunk_ids,
                visible_document_ids=visible_document_ids,
                depth=max(0, depth),
                limit=max(1, limit),
            )
            rows = await records.data()
        return [
            GraphChunkCandidate(
                chunk_id=row["chunk_id"],
                document_id=row["document_id"],
                score=float(row["score"]),
                content_preview=row.get("content_preview") or "",
                metadata={
                    "article_number": row.get("article_number"),
                    "article_title": row.get("article_title"),
                    "chapter_title": row.get("chapter_title"),
                },
                matched_entities=[item for item in row.get("matched_entities", []) if item],
                relations=[item for item in row.get("support_chunk_ids", []) if item],
                source_flags=["graph"],
            )
            for row in rows
        ]


@lru_cache
def get_neo4j_client() -> Neo4jClient:
    return Neo4jClient()
