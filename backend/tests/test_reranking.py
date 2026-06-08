import asyncio
from types import SimpleNamespace
from uuid import UUID

from fastapi.testclient import TestClient

from app.api.routes.search import get_reranking_service
from app.main import app
from app.schemas.documents import (
    HybridSearchResponse,
    HybridSearchResult,
    KeywordSearchResponse,
    KeywordSearchResult,
    RerankSearchResponse,
    RerankSearchResult,
    VectorSearchResponse,
    VectorSearchResult,
)
from app.services.hybrid_search import HybridSearchRun
from app.services.rerankers import FakeReranker
from app.services.reranking_service import RerankingService

DOCUMENT_ID = UUID("99999999-9999-9999-9999-999999999999")
RELEVANT_CHUNK_ID = UUID("11111111-1111-1111-1111-111111111111")
UNRELATED_CHUNK_ID = UUID("22222222-2222-2222-2222-222222222222")


class FakeRerankingService:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def search(
        self,
        *,
        query: str,
        top_k: int,
        candidate_k: int,
    ) -> RerankSearchResponse:
        self.calls.append({"query": query, "top_k": top_k, "candidate_k": candidate_k})
        return RerankSearchResponse(
            query=query,
            top_k=top_k,
            candidate_k=candidate_k,
            results=[
                RerankSearchResult(
                    chunk_id=RELEVANT_CHUNK_ID,
                    document_id=DOCUMENT_ID,
                    rerank_score=1.0,
                    fused_score=0.02,
                    vector_score=0.91,
                    keyword_score=0.45,
                    content_preview="python rag search result",
                    metadata={"start_char": 0},
                    source_flags=["vector", "keyword"],
                )
            ],
        )


class FakeHybridSearchService:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def run_search(
        self,
        *,
        query: str,
        top_k: int,
        vector_weight: float,
        keyword_weight: float,
        save_log: bool,
    ) -> HybridSearchRun:
        self.calls.append(
            {
                "query": query,
                "top_k": top_k,
                "vector_weight": vector_weight,
                "keyword_weight": keyword_weight,
                "save_log": save_log,
            }
        )
        vector_response = VectorSearchResponse(
            query=query,
            top_k=top_k * 3,
            results=[
                VectorSearchResult(
                    chunk_id=UNRELATED_CHUNK_ID,
                    document_id=DOCUMENT_ID,
                    score=0.95,
                    content_preview="unrelated finance result",
                    metadata={"source": "vector"},
                ),
                VectorSearchResult(
                    chunk_id=RELEVANT_CHUNK_ID,
                    document_id=DOCUMENT_ID,
                    score=0.70,
                    content_preview="python rag search result",
                    metadata={"source": "vector"},
                ),
            ],
        )
        keyword_response = KeywordSearchResponse(
            query=query,
            top_k=top_k * 3,
            results=[
                KeywordSearchResult(
                    chunk_id=RELEVANT_CHUNK_ID,
                    document_id=DOCUMENT_ID,
                    score=0.45,
                    content_preview="python rag search result",
                    metadata={"source": "keyword"},
                )
            ],
        )
        hybrid_response = HybridSearchResponse(
            query=query,
            top_k=top_k,
            vector_weight=vector_weight,
            keyword_weight=keyword_weight,
            results=[
                HybridSearchResult(
                    chunk_id=UNRELATED_CHUNK_ID,
                    document_id=DOCUMENT_ID,
                    fused_score=0.03,
                    vector_score=0.95,
                    keyword_score=None,
                    content_preview="unrelated finance result",
                    metadata={"source": "vector"},
                    source_flags=["vector"],
                ),
                HybridSearchResult(
                    chunk_id=RELEVANT_CHUNK_ID,
                    document_id=DOCUMENT_ID,
                    fused_score=0.02,
                    vector_score=0.70,
                    keyword_score=0.45,
                    content_preview="python rag search result",
                    metadata={"source": "keyword"},
                    source_flags=["vector", "keyword"],
                ),
            ],
        )
        return HybridSearchRun(
            vector_response=vector_response,
            keyword_response=keyword_response,
            hybrid_response=hybrid_response,
        )


class FakeRetrievalLogRepository:
    def __init__(self) -> None:
        self.saved_logs: list[dict[str, object]] = []
        self.committed = False
        self.rolled_back = False

    async def save_log(self, **kwargs: object) -> SimpleNamespace:
        self.saved_logs.append(dict(kwargs))
        return SimpleNamespace(id=UUID("33333333-3333-3333-3333-333333333333"))

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        self.rolled_back = True


def test_fake_reranker_prefers_token_overlap_content() -> None:
    async def run_test() -> None:
        reranker = FakeReranker()

        scores = await reranker.rerank(
            query="python rag search",
            candidates=[
                SimpleNamespace(
                    chunk_id=str(UNRELATED_CHUNK_ID),
                    content="finance report summary",
                ),
                SimpleNamespace(
                    chunk_id=str(RELEVANT_CHUNK_ID),
                    content="python rag search result",
                ),
            ],
        )

        by_chunk_id = {score.chunk_id: score.score for score in scores}
        assert by_chunk_id[str(RELEVANT_CHUNK_ID)] > by_chunk_id[str(UNRELATED_CHUNK_ID)]
        assert by_chunk_id[str(RELEVANT_CHUNK_ID)] == 1.0

    asyncio.run(run_test())


def test_rerank_endpoint_rejects_empty_query() -> None:
    service = FakeRerankingService()
    app.dependency_overrides[get_reranking_service] = lambda: service

    try:
        client = TestClient(app)
        response = client.post(
            "/api/search/rerank",
            json={"query": "   ", "top_k": 5, "candidate_k": 20},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422
    assert service.calls == []


def test_rerank_endpoint_returns_expected_schema() -> None:
    service = FakeRerankingService()
    app.dependency_overrides[get_reranking_service] = lambda: service

    try:
        client = TestClient(app)
        response = client.post(
            "/api/search/rerank",
            json={"query": "python rag", "top_k": 1, "candidate_k": 10},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload == {
        "query": "python rag",
        "top_k": 1,
        "candidate_k": 10,
        "results": [
            {
                "chunk_id": str(RELEVANT_CHUNK_ID),
                "document_id": str(DOCUMENT_ID),
                "rerank_score": 1.0,
                "fused_score": 0.02,
                "vector_score": 0.91,
                "keyword_score": 0.45,
                "content_preview": "python rag search result",
                "metadata": {"start_char": 0},
                "source_flags": ["vector", "keyword"],
            }
        ],
    }
    assert service.calls == [{"query": "python rag", "top_k": 1, "candidate_k": 10}]


def test_reranked_results_saved_in_retrieval_log() -> None:
    async def run_test() -> None:
        hybrid_service = FakeHybridSearchService()
        log_repository = FakeRetrievalLogRepository()
        service = RerankingService(
            hybrid_search_service=hybrid_service,  # type: ignore[arg-type]
            reranker=FakeReranker(),
            retrieval_log_repository=log_repository,  # type: ignore[arg-type]
        )

        response = await service.search(query="python rag search", top_k=1, candidate_k=2)

        assert response.results[0].chunk_id == RELEVANT_CHUNK_ID
        assert response.results[0].rerank_score == 1.0
        assert hybrid_service.calls == [
            {
                "query": "python rag search",
                "top_k": 2,
                "vector_weight": 1.0,
                "keyword_weight": 1.0,
                "save_log": False,
            }
        ]
        assert log_repository.committed is True
        assert log_repository.rolled_back is False
        assert len(log_repository.saved_logs) == 1
        saved_log = log_repository.saved_logs[0]
        assert saved_log["query"] == "python rag search"
        assert saved_log["hybrid_results"]["top_k"] == 2
        assert saved_log["reranked_results"]["top_k"] == 1
        assert saved_log["reranked_results"]["candidate_k"] == 2
        assert saved_log["reranked_results"]["results"][0]["chunk_id"] == str(RELEVANT_CHUNK_ID)

    asyncio.run(run_test())
