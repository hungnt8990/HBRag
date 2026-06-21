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
from app.services.embeddings.sparse import HashingSparseEmbeddingProvider
from app.services.vector_store import VectorSearchResult

DOCUMENT_ID = UUID("44444444-4444-4444-4444-444444444444")
CHUNK_ID = UUID("55555555-5555-5555-5555-555555555555")
FOOTER_ID = UUID("55555555-5555-5555-5555-555555555556")
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
            title="Kế hoạch GIS",
            status=status,
            organization_id=ORGANIZATION_ID,
            knowledge_base_id=KNOWLEDGE_BASE_ID,
            uploaded_by_user_id=USER_ID,
            visibility="organization",
            document_metadata={
                "parser": "docling",
                "parsed_metadata": {"parser_version": "2.x"},
                "document_version": "v1",
            },
        )
        self.file = SimpleNamespace(
            filename="6515.pdf",
            storage_path="documents/6515/original.pdf",
        )
        self.chunks = chunks if chunks is not None else [self._chunk()]
        self.committed = False
        self.rolled_back = False

    async def get_document(self, document_id: UUID) -> SimpleNamespace | None:
        return self.document if document_id == DOCUMENT_ID else None

    async def get_primary_document_file(self, document_id: UUID):
        return self.file if document_id == DOCUMENT_ID else None

    async def list_chunks_for_document(self, document_id: UUID) -> list[SimpleNamespace]:
        return self.chunks if document_id == DOCUMENT_ID else []

    async def update_document_status(self, document: SimpleNamespace, status: str):
        document.status = status
        return document

    async def update_document_metadata(self, document: SimpleNamespace, metadata: dict):
        document.document_metadata = {**document.document_metadata, **metadata}
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
            content="1.1. GIS 110kV, GIS trung thế:\n- Hiệu chỉnh PMISToGIS.",
            token_count=40,
            chunk_metadata={
                "chunk_id": "chunk_002",
                "chunk_type": "assignment_section",
                "headings": ["1. CPCIT", "1.1. GIS 110kV, GIS trung thế"],
                "unit": "CPCIT",
                "scope": ["GIS 110kV", "GIS trung thế"],
                "pages": [1],
                "indexable": True,
                "embedding_enabled": True,
                "quality_status": "pass",
                "chunker": "docling_hybrid_v6",
                "chunker_version": "6",
                "source_type": "doffice_elasticsearch",
                "id_vb": "1459570",
                "document_code": "907/EVNICT-TTPM",
                "trich_yeu": "Cap nhat ung dung EVN CSKH",
                "platform": "Website Quan tri noi dung (CMS)",
                "feature_name": "Dashboard",
                "change_content": "Y 1 Y 2 Y 3 Y 4",
                "phase": "Giai doan 2",
            },
        )


class FakeVectorStore:
    vector_size = 384
    collection_name = "hbrag_chunks_v2"
    dense_vector_name = "dense"
    sparse_vector_name = "sparse"

    def __init__(self) -> None:
        self.upserted_points: list[object] = []
        self.searches: list[dict[str, object]] = []
        self.deleted_documents: list[str] = []

    async def upsert_chunks(self, points: list[object]) -> None:
        self.upserted_points = points

    async def delete_points_for_document(self, document_id, *, tenant_id=None) -> None:
        self.deleted_documents.append(str(document_id))

    async def search(self, **kwargs) -> list[VectorSearchResult]:
        self.searches.append(kwargs)
        return [
            VectorSearchResult(
                chunk_id=str(CHUNK_ID),
                document_id=str(DOCUMENT_ID),
                score=0.91,
                content="This is a long chunk content returned from vector search.",
                metadata={"page_start": 1, "section_path": ["1. CPCIT"]},
            )
        ]

    def build_point(self, *, point_id, payload, vector, sparse_vector=None):
        return SimpleNamespace(
            id=str(point_id),
            vector={"dense": vector, "sparse": sparse_vector},
            payload=payload,
        )


class RecordingEmbeddingProvider(FakeEmbeddingProvider):
    def __init__(self) -> None:
        super().__init__()
        self.embedded_texts: list[str] = []

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        self.embedded_texts = list(texts)
        return await super().embed_texts(texts)


def test_fake_embedding_is_deterministic() -> None:
    provider = FakeEmbeddingProvider()
    assert provider._embed("same text") == provider._embed("same text")
    assert provider._embed("same text") != provider._embed("different text")


def test_vector_index_endpoint_stores_one_chunk_per_qdrant_point() -> None:
    repository = FakeDocumentRepository()
    vector_store = FakeVectorStore()
    provider = RecordingEmbeddingProvider()
    app.dependency_overrides[get_document_repository] = lambda: repository
    app.dependency_overrides[get_embedding_provider] = lambda: provider
    app.dependency_overrides[get_vector_store] = lambda: vector_store

    try:
        response = TestClient(app).post(
            f"/api/documents/{DOCUMENT_ID}/index-vector",
            json={"use_enriched_content_for_embedding": True},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["indexed_chunk_count"] == 1
    assert vector_store.deleted_documents == [str(DOCUMENT_ID)]
    point = vector_store.upserted_points[0]
    assert point.id != str(CHUNK_ID)  # stable UUID5 point id is separate from DB chunk id
    assert len(point.vector["dense"]) == 384
    assert point.vector["sparse"] is not None
    assert point.payload["chunk_id"] == str(CHUNK_ID)
    assert point.payload["semantic_chunk_id"] == "chunk_002"
    assert point.payload["text"].startswith("1.1. GIS 110kV")
    assert point.payload["unit"] == "CPCIT"
    assert point.payload["pages"] == [1]
    assert point.payload["source_type"] == "doffice_elasticsearch"
    assert point.payload["id_vb"] == "1459570"
    assert point.payload["document_code"] == "907/EVNICT-TTPM"
    assert point.payload["trich_yeu"] == "Cap nhat ung dung EVN CSKH"
    assert point.payload["platform"] == "Website Quan tri noi dung (CMS)"
    assert point.payload["feature_name"] == "Dashboard"
    assert point.payload["change_content"] == "Y 1 Y 2 Y 3 Y 4"
    assert point.payload["phase"] == "Giai doan 2"
    assert "Tài liệu: Kế hoạch GIS" in provider.embedded_texts[0]
    assert repository.document.document_metadata["chunk_count_indexed"] == 1


def test_vector_index_uses_enriched_content_for_embedding_payload_keeps_original() -> None:
    chunk = FakeDocumentRepository._chunk()
    chunk.enriched_content = (
        f"{chunk.content}\n\n"
        "LLM enrichment:\n"
        "Tóm tắt: Chunk nói về hiệu chỉnh PMISToGIS.\n"
        "Từ khóa: PMISToGIS; GIS"
    )
    chunk.chunk_metadata = {
        **chunk.chunk_metadata,
        "enrichment": {
            "status": "success",
            "summary": "Chunk nói về hiệu chỉnh PMISToGIS.",
            "keywords": ["PMISToGIS", "GIS"],
            "document_code": "123/QĐ-CPCIT",
            "issued_date": "01/02/2024",
            "document_type": "quyết định",
            "structure_path": "1. CPCIT > 1.1. GIS",
        },
    }
    repository = FakeDocumentRepository(chunks=[chunk])
    vector_store = FakeVectorStore()
    provider = RecordingEmbeddingProvider()
    app.dependency_overrides[get_document_repository] = lambda: repository
    app.dependency_overrides[get_embedding_provider] = lambda: provider
    app.dependency_overrides[get_vector_store] = lambda: vector_store

    try:
        response = TestClient(app).post(
            f"/api/documents/{DOCUMENT_ID}/index-vector",
            json={"use_enriched_content_for_embedding": True},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert "LLM enrichment" in provider.embedded_texts[0]
    assert "Tóm tắt: Chunk nói về hiệu chỉnh PMISToGIS" in provider.embedded_texts[0]
    point = vector_store.upserted_points[0]
    assert point.payload["text"] == chunk.content
    assert point.payload["enriched"] is True
    assert point.payload["enrichment_summary"] == "Chunk nói về hiệu chỉnh PMISToGIS."
    assert point.payload["enrichment_keywords"] == ["PMISToGIS", "GIS"]
    assert point.payload["document_code"] == "123/QĐ-CPCIT"
    assert point.payload["issued_date"] == "01/02/2024"
    assert point.payload["document_type"] == "quyết định"
    assert point.payload["structure_path"] == "1. CPCIT > 1.1. GIS"
    assert "embedding_text" not in point.payload


def test_vector_payload_excludes_heavy_document_and_debug_fields() -> None:
    chunk = FakeDocumentRepository._chunk()
    chunk.chunk_metadata = {
        **chunk.chunk_metadata,
        "noi_dung_raw": "raw" * 100,
        "plain_text": "plain" * 100,
        "markdown_text": "| a | b |",
        "tom_tat": "summary" * 100,
        "parsed_elements": [{"text": "debug"}],
        "raw_source_metadata": {"full": "source"},
        "raw_payload": {"full": "payload"},
        "raw_cells": ["a", "b"],
        "access": {"scope": "corp_wide", "allowed_user_ids": [str(USER_ID)]},
        "enrichment": {"status": "failed", "raw_response": "not json" * 100},
    }
    repository = FakeDocumentRepository(chunks=[chunk])
    vector_store = FakeVectorStore()
    app.dependency_overrides[get_document_repository] = lambda: repository
    app.dependency_overrides[get_embedding_provider] = lambda: FakeEmbeddingProvider()
    app.dependency_overrides[get_vector_store] = lambda: vector_store

    try:
        response = TestClient(app).post(f"/api/documents/{DOCUMENT_ID}/index-vector")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = vector_store.upserted_points[0].payload
    forbidden = {
        "noi_dung_raw",
        "plain_text",
        "markdown_text",
        "tom_tat",
        "parsed_elements",
        "raw_source_metadata",
        "raw_payload",
        "raw_cells",
        "enrichment",
        "access",
    }
    assert forbidden.isdisjoint(payload)
    assert payload["scope"] == "corp_wide"
    assert payload["allowed_user_ids"] == [str(USER_ID)]

def test_vector_index_can_disable_enriched_content_for_embedding() -> None:
    chunk = FakeDocumentRepository._chunk()
    chunk.enriched_content = f"{chunk.content}\n\nLLM enrichment:\nTừ khóa: enriched-only"
    chunk.chunk_metadata = {
        **chunk.chunk_metadata,
        "enrichment": {
            "status": "success",
            "summary": "Bản làm giàu.",
            "keywords": ["enriched-only"],
            "document_code": "123/QĐ-CPCIT",
        },
    }
    repository = FakeDocumentRepository(chunks=[chunk])
    vector_store = FakeVectorStore()
    provider = RecordingEmbeddingProvider()
    app.dependency_overrides[get_document_repository] = lambda: repository
    app.dependency_overrides[get_embedding_provider] = lambda: provider
    app.dependency_overrides[get_vector_store] = lambda: vector_store

    try:
        response = TestClient(app).post(
            f"/api/documents/{DOCUMENT_ID}/index-vector",
            json={"use_enriched_content_for_embedding": False},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert "LLM enrichment" not in provider.embedded_texts[0]
    assert "enriched-only" not in provider.embedded_texts[0]
    point = vector_store.upserted_points[0]
    assert point.payload["text"] == chunk.content
    assert point.payload["enriched"] is True
    assert point.payload["enrichment_keywords"] == ["enriched-only"]


def test_vector_index_filters_administrative_footer() -> None:
    footer = SimpleNamespace(
        id=FOOTER_ID,
        document_id=DOCUMENT_ID,
        chunk_index=1,
        content="Nơi nhận: ...",
        token_count=10,
        chunk_metadata={
            "chunk_id": "chunk_009",
            "chunk_type": "administrative_footer",
            "pages": [3],
            "indexable": False,
            "embedding_enabled": False,
        },
    )
    repository = FakeDocumentRepository(chunks=[FakeDocumentRepository._chunk(), footer])
    vector_store = FakeVectorStore()
    app.dependency_overrides[get_document_repository] = lambda: repository
    app.dependency_overrides[get_embedding_provider] = lambda: FakeEmbeddingProvider()
    app.dependency_overrides[get_vector_store] = lambda: vector_store

    try:
        response = TestClient(app).post(f"/api/documents/{DOCUMENT_ID}/index-vector")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["indexed_chunk_count"] == 1
    assert len(vector_store.upserted_points) == 1
    assert "Nơi nhận" not in vector_store.upserted_points[0].payload["text"]


def test_vector_index_is_idempotent_for_same_chunk_content() -> None:
    repository = FakeDocumentRepository(status="indexed")
    vector_store = FakeVectorStore()
    provider = FakeEmbeddingProvider()
    app.dependency_overrides[get_document_repository] = lambda: repository
    app.dependency_overrides[get_embedding_provider] = lambda: provider
    app.dependency_overrides[get_vector_store] = lambda: vector_store

    try:
        client = TestClient(app)
        first = client.post(f"/api/documents/{DOCUMENT_ID}/index-vector")
        first_id = vector_store.upserted_points[0].id
        second = client.post(f"/api/documents/{DOCUMENT_ID}/index-vector")
        second_id = vector_store.upserted_points[0].id
    finally:
        app.dependency_overrides.clear()

    assert first.status_code == 200
    assert second.status_code == 200
    assert first_id == second_id
    assert vector_store.deleted_documents == [str(DOCUMENT_ID), str(DOCUMENT_ID)]


def test_vector_index_rejects_document_without_chunks() -> None:
    repository = FakeDocumentRepository(chunks=[])
    vector_store = FakeVectorStore()
    app.dependency_overrides[get_document_repository] = lambda: repository
    app.dependency_overrides[get_embedding_provider] = lambda: FakeEmbeddingProvider()
    app.dependency_overrides[get_vector_store] = lambda: vector_store

    try:
        response = TestClient(app).post(f"/api/documents/{DOCUMENT_ID}/index-vector")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 409
    assert vector_store.upserted_points == []


def test_vector_index_rejects_non_chunked_document() -> None:
    repository = FakeDocumentRepository(status="parsed")
    vector_store = FakeVectorStore()
    app.dependency_overrides[get_document_repository] = lambda: repository
    app.dependency_overrides[get_embedding_provider] = lambda: FakeEmbeddingProvider()
    app.dependency_overrides[get_vector_store] = lambda: vector_store

    try:
        response = TestClient(app).post(f"/api/documents/{DOCUMENT_ID}/index-vector")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 409


def test_vector_search_endpoint_returns_text_source_metadata() -> None:
    vector_store = FakeVectorStore()
    app.dependency_overrides[get_search_embedding_provider] = lambda: FakeEmbeddingProvider()
    app.dependency_overrides[get_search_vector_store] = lambda: vector_store

    try:
        response = TestClient(app).post(
            "/api/search/vector",
            json={"query": "find this", "top_k": 3},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    result = response.json()["results"][0]
    assert result["chunk_id"] == str(CHUNK_ID)
    assert result["content_preview"].startswith("This is a long chunk")
    assert result["metadata"]["page_start"] == 1
    assert len(vector_store.searches[0]["query_vector"]) == 384
    assert vector_store.searches[0]["sparse_query"] is not None


def test_hashing_sparse_embedding_preserves_technical_identifiers() -> None:
    provider = HashingSparseEmbeddingProvider(dimensions=4096)

    import asyncio

    vector = asyncio.run(provider.embed_query("F08_CotDien_HT MaTramBienAp PMISToGIS"))

    assert vector.indices
    assert len(vector.indices) == len(vector.values)
