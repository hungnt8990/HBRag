import asyncio
from types import SimpleNamespace
from uuid import UUID, uuid4

from app.core.config import Settings
from app.schemas.documents import GraphIndexRequest, HybridSearchResponse, HybridSearchResult
from app.services.graph.extractors.fake_extractor import FakeGraphExtractor
from app.services.graph.graph_indexing_service import GraphIndexingService
from app.services.graph.graph_merge_service import GraphMergeService
from app.services.graph.models import GraphChunkCandidate
from app.services.rerankers.base import RerankScore
from app.services.reranking_service import RerankingService


def test_fake_graph_extractor_returns_deterministic_entities() -> None:
    async def run_test() -> None:
        extractor = FakeGraphExtractor()
        result = await extractor.extract(
            content="Điều 10 quy định EVNCPC và NLĐ.",
            max_entities=10,
            max_relations=10,
        )

        names = [(entity.name, entity.type) for entity in result.entities]
        assert ("Điều 10", "legal_article") in names
        assert ("EVNCPC", "organization") in names
        assert any(relation.type == "lien_quan_den" for relation in result.relationships)

    asyncio.run(run_test())


def test_graph_merge_service_deduplicates_aliases() -> None:
    from app.services.graph.models import ExtractedEntity

    service = GraphMergeService()
    merged = service.merge_entities(
        [
            ExtractedEntity(
                name="NLĐ",
                normalized_name="NLĐ",
                type="person",
                confidence=0.6,
                evidence="NLĐ",
            ),
            ExtractedEntity(
                name="người lao động",
                normalized_name="người lao động",
                type="person",
                confidence=0.9,
                evidence="người lao động",
            ),
        ]
    )

    assert len(merged) == 1
    assert merged[0].normalized_name == "người lao động"
    assert merged[0].confidence == 0.9


def test_graph_indexing_service_updates_status_and_logs(monkeypatch) -> None:
    document_id = uuid4()
    chunk_id = uuid4()

    class FakeDocumentRepository:
        async def get_document(self, requested_document_id):
            if requested_document_id != document_id:
                return None
            return SimpleNamespace(
                id=document_id,
                title="Test document",
                organization_id=None,
                created_at=SimpleNamespace(isoformat=lambda: "2026-06-08T00:00:00+00:00"),
            )

        async def list_chunks_for_document(self, requested_document_id):
            assert requested_document_id == document_id
            return [
                SimpleNamespace(
                    id=chunk_id,
                    document_id=document_id,
                    chunk_index=0,
                    content="Điều 10 quy định EVNCPC và NLĐ.",
                    chunk_metadata={"article_number": "10"},
                )
            ]

    class FakeGraphRepository:
        def __init__(self) -> None:
            self.logs = []
            self.status_updates = []
            self.committed = False

        async def delete_extraction_logs(self, *, document_id):
            return None

        async def create_extraction_log(self, **kwargs):
            self.logs.append(kwargs)
            return SimpleNamespace(**kwargs)

        async def upsert_document_status(self, **kwargs):
            self.status_updates.append(kwargs)
            return SimpleNamespace(**kwargs)

        async def commit(self):
            self.committed = True

    class FakeNeo4jClient:
        async def verify_connectivity(self):
            return None

        async def create_constraints(self):
            return None

        async def reset_document_graph(self, document_id: str):
            return None

        async def upsert_document(self, payload):
            return None

        async def upsert_chunk(self, payload):
            return None

        async def link_document_to_chunk(self, document_id: str, chunk_id: str):
            return None

        async def upsert_entity(self, payload):
            return None

        async def link_chunk_to_entity(self, **kwargs):
            return None

        async def link_entity_to_supporting_chunk(self, **kwargs):
            return None

        async def upsert_relationship(self, **kwargs):
            return None

    graph_repository = FakeGraphRepository()
    service = GraphIndexingService(
        document_repository=FakeDocumentRepository(),  # type: ignore[arg-type]
        graph_repository=graph_repository,  # type: ignore[arg-type]
        neo4j_client=FakeNeo4jClient(),  # type: ignore[arg-type]
        extractor=FakeGraphExtractor(),
        merge_service=GraphMergeService(),
    )
    monkeypatch.setattr(
        "app.services.graph.graph_indexing_service.settings",
        Settings(_env_file=None, graph_enabled=True, graph_min_relation_confidence=0.0),
    )

    response = asyncio.run(
        service.index_document(
            document_id,
            GraphIndexRequest(
                force_rebuild=False,
                extractor_provider="fake",
                max_entities_per_chunk=30,
                max_relations_per_chunk=40,
            ),
        )
    )

    assert response.status == "graph_indexed"
    assert response.chunks_processed == 1
    assert response.entities_extracted >= 2
    assert graph_repository.committed is True
    assert len(graph_repository.logs) == 1
    assert graph_repository.status_updates[-1]["graph_indexed"] is True


def test_reranking_service_merges_graph_candidates_with_hybrid_results() -> None:
    hybrid_chunk_id = UUID("11111111-1111-1111-1111-111111111111")
    graph_chunk_id = UUID("22222222-2222-2222-2222-222222222222")
    document_id = UUID("33333333-3333-3333-3333-333333333333")

    class FakeHybridSearchService:
        async def run_search(self, **kwargs):
            return SimpleNamespace(
                vector_response=SimpleNamespace(model_dump=lambda mode="json": {"results": []}),
                keyword_response=SimpleNamespace(model_dump=lambda mode="json": {"results": []}),
                hybrid_response=HybridSearchResponse(
                    query="graph test",
                    top_k=5,
                    vector_weight=1.0,
                    keyword_weight=1.0,
                    results=[
                        HybridSearchResult(
                            chunk_id=hybrid_chunk_id,
                            document_id=document_id,
                            fused_score=1.0,
                            vector_score=0.9,
                            keyword_score=0.8,
                            content_preview="hybrid preview",
                            metadata={},
                            source_flags=["vector", "keyword"],
                        )
                    ],
                ),
            )

    class FakeGraphRetrievalService:
        async def expand(self, **kwargs):
            return [
                GraphChunkCandidate(
                    chunk_id=str(graph_chunk_id),
                    document_id=str(document_id),
                    score=0.7,
                    content_preview="graph preview",
                    metadata={},
                    matched_entities=["EVNCPC"],
                    relations=["lien_quan_den"],
                    source_flags=["graph"],
                )
            ]

    class FakeReranker:
        async def rerank(self, *, query, candidates):
            return [RerankScore(chunk_id=candidate.chunk_id, score=1.0) for candidate in candidates]

    class FakeLogRepository:
        async def save_log(self, **kwargs):
            return SimpleNamespace(**kwargs)

        async def commit(self):
            return None

        async def rollback(self):
            return None

    service = RerankingService(
        hybrid_search_service=FakeHybridSearchService(),  # type: ignore[arg-type]
        reranker=FakeReranker(),  # type: ignore[arg-type]
        retrieval_log_repository=FakeLogRepository(),  # type: ignore[arg-type]
        graph_retrieval_service=FakeGraphRetrievalService(),  # type: ignore[arg-type]
    )

    response = asyncio.run(
        service.search(
            query="graph test",
            top_k=5,
            candidate_k=5,
            use_graph=True,
        )
    )

    assert {str(result.chunk_id) for result in response.results} == {
        str(hybrid_chunk_id),
        str(graph_chunk_id),
    }
    graph_result = next(result for result in response.results if str(result.chunk_id) == str(graph_chunk_id))
    assert graph_result.source_flags == ["graph"]
