from types import SimpleNamespace
from uuid import UUID

from fastapi.testclient import TestClient

from app.api.routes.search import (
    get_knowledge_base_repository,
    get_search_repository,
    get_vector_search_service,
)
from app.main import app
from app.schemas.documents import VectorSearchResponse, VectorSearchResult

DOC_A = UUID("11111111-1111-1111-1111-111111111111")
DOC_B = UUID("22222222-2222-2222-2222-222222222222")
KB_A = UUID("33333333-3333-3333-3333-333333333333")
KB_B = UUID("44444444-4444-4444-4444-444444444444")
CHUNK_ID = UUID("55555555-5555-5555-5555-555555555555")


class FakeKnowledgeBaseRepository:
    def __init__(self) -> None:
        self.knowledge_bases = {
            KB_A: SimpleNamespace(
                id=KB_A,
                organization_id=None,
                owner_user_id=None,
                visibility="global",
                is_active=True,
                members=[],
            ),
            KB_B: SimpleNamespace(
                id=KB_B,
                organization_id=None,
                owner_user_id=None,
                visibility="global",
                is_active=True,
                members=[],
            ),
        }

    async def get_by_ids(self, knowledge_base_ids):
        return [self.knowledge_bases[knowledge_base_id] for knowledge_base_id in knowledge_base_ids]


class FakeVectorSearchService:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def search(self, *, query: str, top_k: int, document_ids=None) -> VectorSearchResponse:
        self.calls.append(
            {"query": query, "top_k": top_k, "document_ids": document_ids}
        )
        if not document_ids:
            return VectorSearchResponse(query=query, top_k=top_k, results=[])
        return VectorSearchResponse(
            query=query,
            top_k=top_k,
            results=[
                VectorSearchResult(
                    chunk_id=CHUNK_ID,
                    document_id=DOC_A,
                    score=0.99,
                    content_preview="Scoped result",
                    metadata={},
                )
            ],
        )


class FakeSearchRepository:
    async def list_documents_for_permission_check(self, *, knowledge_base_ids=None):
        docs = [
            SimpleNamespace(
                id=DOC_A,
                knowledge_base_id=KB_A,
                organization_id=None,
                uploaded_by_user_id=None,
                visibility="global",
            ),
            SimpleNamespace(
                id=DOC_B,
                knowledge_base_id=KB_B,
                organization_id=None,
                uploaded_by_user_id=None,
                visibility="global",
            ),
        ]
        if knowledge_base_ids is None:
            return docs
        return [doc for doc in docs if doc.knowledge_base_id in knowledge_base_ids]


def test_vector_search_scopes_to_requested_knowledge_base() -> None:
    service = FakeVectorSearchService()
    app.dependency_overrides[get_vector_search_service] = lambda: service
    app.dependency_overrides[get_knowledge_base_repository] = lambda: FakeKnowledgeBaseRepository()
    app.dependency_overrides[get_search_repository] = lambda: FakeSearchRepository()

    try:
        client = TestClient(app)
        response = client.post(
            "/api/search/vector",
            json={
                "query": "scoped",
                "top_k": 5,
                "knowledge_base_ids": [str(KB_A)],
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert [item["document_id"] for item in payload["results"]] == [str(DOC_A)]
    assert service.calls[0]["document_ids"] == {str(DOC_A)}


def test_vector_search_empty_knowledge_base_scope_returns_empty() -> None:
    service = FakeVectorSearchService()
    app.dependency_overrides[get_vector_search_service] = lambda: service
    app.dependency_overrides[get_knowledge_base_repository] = lambda: FakeKnowledgeBaseRepository()
    app.dependency_overrides[get_search_repository] = lambda: FakeSearchRepository()

    try:
        client = TestClient(app)
        response = client.post(
            "/api/search/vector",
            json={
                "query": "scoped",
                "top_k": 5,
                "knowledge_base_ids": [],
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["results"] == []
    assert service.calls[0]["document_ids"] == set()
