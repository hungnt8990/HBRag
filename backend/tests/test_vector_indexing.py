from types import SimpleNamespace
from uuid import UUID

from fastapi.testclient import TestClient

from app.api.routes.documents import (
    get_document_repository,
    get_embedding_provider,
    get_vector_store,
)
from app.api.routes.search import (
    get_embedding_provider as get_search_embedding_provider,
)
from app.api.routes.search import (
    get_vector_store as get_search_vector_store,
)
from app.main import app
from app.services.embeddings.fake_provider import FakeEmbeddingProvider
from app.services.vector_store import VectorSearchResult

DOCUMENT_ID = UUID("44444444-4444-4444-4444-444444444444")
CHUNK_ID = UUID("55555555-5555-5555-5555-555555555555")
ORGANIZATION_ID = UUID("66666666-6666-6666-6666-666666666666")
KNOWLEDGE_BASE_ID = UUID("77777777-7777-7777-7777-777777777777")
USER_ID = UUID("88888888-8888-8888-8888-888888888888")


class FakeDocumentRepository:
    def __init__(
        self,
        *,
        status: str = "chunked",
        chunks: list[SimpleNamespace] | None = None,
    ) -> None:
        self.document = SimpleNamespace(
            id=DOCUMENT_ID,
            status=status,
            organization_id=ORGANIZATION_ID,
            knowledge_base_id=KNOWLEDGE_BASE_ID,
            uploaded_by_user_id=USER_ID,
            visibility="organization",
        )
        self.chunks = chunks if chunks is not None else [self._chunk()]
        self.committed = False
        self.rolled_back = False

    async def get_document(self, document_id: UUID) -> SimpleNamespace | None:
        if document_id != DOCUMENT_ID:
            return None
        return self.document

    async def list_chunks_for_document(self, document_id: UUID) -> list[SimpleNamespace]:
        if document_id != DOCUMENT_ID:
            return []
        return self.chunks

    async def update_document_status(
        self,
        document: SimpleNamespace,
        status: str,
    ) -> SimpleNamespace:
        document.status = status
        return document

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        self.rolled_back = True

    @staticmethod
    def _chunk() -> SimpleNamespace:
        return SimpleNamespace(
            id=CHUNK_ID,
            document_id=DOCUMENT_ID,
            chunk_index=0,
            content="Chunk content for indexing",
            chunk_metadata={"start_char": 0, "end_char": 26},
        )


class FakeVectorStore:
    def __init__(self) -> None:
        self.collection_ensured = False
        self.upserted_points: list[object] = []
        self.searches: list[dict[str, object]] = []

    async def ensure_collection(self) -> None:
        self.collection_ensured = True

    async def upsert_chunks(self, points: list[object]) -> None:
        self.collection_ensured = True
        self.upserted_points = points

    async def search(
        self,
        *,
        query_vector: list[float],
        top_k: int,
        document_ids=None,
    ) -> list[VectorSearchResult]:
        self.collection_ensured = True
        self.searches.append({"query_vector": query_vector, "top_k": top_k})
        return [
            VectorSearchResult(
                chunk_id=str(CHUNK_ID),
                document_id=str(DOCUMENT_ID),
                score=0.91,
                content="This is a long chunk content returned from vector search.",
                metadata={"start_char": 0, "end_char": 55},
            )
        ]

    @staticmethod
    def build_point(
        *,
        chunk_id: UUID,
        document_id: UUID,
        chunk_index: int,
        content: str,
        metadata: dict[str, object],
        vector: list[float],
        organization_id=None,
        knowledge_base_id=None,
        uploaded_by_user_id=None,
        visibility=None,
    ) -> SimpleNamespace:
        return SimpleNamespace(
            id=str(chunk_id),
            vector=vector,
            payload={
                "chunk_id": str(chunk_id),
                "document_id": str(document_id),
                "chunk_index": chunk_index,
                "content": content,
                "metadata": metadata,
                "organization_id": str(organization_id) if organization_id else None,
                "knowledge_base_id": str(knowledge_base_id) if knowledge_base_id else None,
                "uploaded_by_user_id": str(uploaded_by_user_id) if uploaded_by_user_id else None,
                "visibility": visibility,
            },
        )


def test_fake_embedding_is_deterministic() -> None:
    provider = FakeEmbeddingProvider()

    first = provider._embed("same text")
    second = provider._embed("same text")
    different = provider._embed("different text")

    assert first == second
    assert first != different
    assert len(first) == 384


def test_vector_index_endpoint_upserts_chunk_vectors() -> None:
    repository = FakeDocumentRepository()
    vector_store = FakeVectorStore()
    provider = FakeEmbeddingProvider()
    app.dependency_overrides[get_document_repository] = lambda: repository
    app.dependency_overrides[get_embedding_provider] = lambda: provider
    app.dependency_overrides[get_vector_store] = lambda: vector_store

    try:
        client = TestClient(app)
        response = client.post(f"/api/documents/{DOCUMENT_ID}/index-vector")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload == {
        "document_id": str(DOCUMENT_ID),
        "status": "indexed",
        "indexed_chunk_count": 1,
    }
    assert repository.document.status == "indexed"
    assert repository.committed is True
    assert len(vector_store.upserted_points) == 1
    point = vector_store.upserted_points[0]
    assert point.id == str(CHUNK_ID)
    assert len(point.vector) == 384
    assert point.payload["chunk_id"] == str(CHUNK_ID)
    assert point.payload["document_id"] == str(DOCUMENT_ID)
    assert point.payload["chunk_index"] == 0
    assert point.payload["content"] == "Chunk content for indexing"
    assert point.payload["metadata"] == {"start_char": 0, "end_char": 26}
    assert point.payload["organization_id"] == str(ORGANIZATION_ID)
    assert point.payload["knowledge_base_id"] == str(KNOWLEDGE_BASE_ID)
    assert point.payload["uploaded_by_user_id"] == str(USER_ID)
    assert point.payload["visibility"] == "organization"


def test_vector_index_endpoint_allows_reindexing_indexed_document() -> None:
    repository = FakeDocumentRepository(status="indexed")
    vector_store = FakeVectorStore()
    app.dependency_overrides[get_document_repository] = lambda: repository
    app.dependency_overrides[get_embedding_provider] = lambda: FakeEmbeddingProvider()
    app.dependency_overrides[get_vector_store] = lambda: vector_store

    try:
        client = TestClient(app)
        response = client.post(f"/api/documents/{DOCUMENT_ID}/index-vector")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {
        "document_id": str(DOCUMENT_ID),
        "status": "indexed",
        "indexed_chunk_count": 1,
    }
    assert len(vector_store.upserted_points) == 1
    assert repository.committed is True


def test_vector_index_rejects_document_without_chunks() -> None:
    repository = FakeDocumentRepository(chunks=[])
    vector_store = FakeVectorStore()
    app.dependency_overrides[get_document_repository] = lambda: repository
    app.dependency_overrides[get_embedding_provider] = lambda: FakeEmbeddingProvider()
    app.dependency_overrides[get_vector_store] = lambda: vector_store

    try:
        client = TestClient(app)
        response = client.post(f"/api/documents/{DOCUMENT_ID}/index-vector")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 409
    assert repository.document.status == "chunked"
    assert repository.committed is False
    assert vector_store.upserted_points == []


def test_vector_index_rejects_non_chunked_document() -> None:
    repository = FakeDocumentRepository(status="parsed")
    vector_store = FakeVectorStore()
    app.dependency_overrides[get_document_repository] = lambda: repository
    app.dependency_overrides[get_embedding_provider] = lambda: FakeEmbeddingProvider()
    app.dependency_overrides[get_vector_store] = lambda: vector_store

    try:
        client = TestClient(app)
        response = client.post(f"/api/documents/{DOCUMENT_ID}/index-vector")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 409
    assert vector_store.upserted_points == []


def test_vector_search_endpoint_schema() -> None:
    vector_store = FakeVectorStore()
    app.dependency_overrides[get_search_embedding_provider] = lambda: FakeEmbeddingProvider()
    app.dependency_overrides[get_search_vector_store] = lambda: vector_store

    try:
        client = TestClient(app)
        response = client.post(
            "/api/search/vector",
            json={"query": "find this", "top_k": 3},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload["query"] == "find this"
    assert payload["top_k"] == 3
    assert len(payload["results"]) == 1
    assert payload["results"][0] == {
        "chunk_id": str(CHUNK_ID),
        "document_id": str(DOCUMENT_ID),
        "score": 0.91,
        "content_preview": "This is a long chunk content returned from vector search.",
        "metadata": {"start_char": 0, "end_char": 55},
    }
    assert len(vector_store.searches[0]["query_vector"]) == 384
    assert vector_store.searches[0]["top_k"] == 3
