from datetime import UTC, datetime
from uuid import UUID

from fastapi.testclient import TestClient

from app.api.routes import admin as admin_routes
from app.api.routes.admin import get_ingestion_queue, get_vector_store
from app.core.config import Settings
from app.main import app
from app.services.ingestion_queue import IngestionJob
from app.services.vector_store import VectorStoreCollectionInfo


class FakeVectorStore:
    async def recreate_collection(self) -> VectorStoreCollectionInfo:
        return VectorStoreCollectionInfo(
            collection_name="test_chunks",
            exists=True,
            vector_size=768,
            expected_vector_size=768,
            distance="Cosine",
            expected_distance="Cosine",
            matches_config=True,
            recreated=True,
        )


class FakeIngestionQueue:
    def __init__(self) -> None:
        self.job = IngestionJob(
            job_id=UUID("aaaaaaaa-1111-1111-1111-aaaaaaaaaaaa"),
            filename="sample.pdf",
            content_type="application/pdf",
            status="queued",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        self.run_calls: list[object] = []

    def enqueue_upload(
        self,
        *,
        filename: str,
        content_type: str | None,
        content: bytes,
    ) -> IngestionJob:
        self.job.filename = filename
        self.job.content_type = content_type
        return self.job

    def get_job(self, job_id: object) -> IngestionJob | None:
        if str(job_id) == str(self.job.job_id):
            return self.job
        return None

    def list_jobs(self) -> list[IngestionJob]:
        return [self.job]

    async def run_job(self, job_id: object) -> None:
        self.run_calls.append(job_id)


def test_admin_recreate_vector_store_endpoint_returns_collection_info() -> None:
    app.dependency_overrides[get_vector_store] = lambda: FakeVectorStore()

    try:
        client = TestClient(app)
        response = client.post("/api/admin/recreate-vector-store")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {
        "collection_name": "test_chunks",
        "exists": True,
        "vector_size": 768,
        "expected_vector_size": 768,
        "distance": "Cosine",
        "expected_distance": "Cosine",
        "matches_config": True,
        "recreated": True,
    }


def test_admin_runtime_config_returns_safe_non_secret_settings(monkeypatch) -> None:
    configured_settings = Settings(
        _env_file=None,
        qdrant_collection_name="configured_chunks",
        auto_recreate_collection=True,
        embedding_provider="openai_compatible",
        embedding_base_url="http://embedding.test/v1",
        embedding_api_key="embedding-secret",
        embedding_model="embedding-model",
        embedding_dimension=768,
        reranker_provider="openai_compatible",
        reranker_base_url="http://reranker.test/v1",
        reranker_api_key="reranker-secret",
        reranker_model="reranker-model",
        llm_provider="openai_compatible",
        llm_base_url="http://llm.test/v1",
        llm_api_key="llm-secret",
        llm_model="llm-model",
        graph_enabled=True,
        graph_provider="neo4j",
        neo4j_uri="bolt://neo4j.test:7687",
        neo4j_username="graph-user",
        neo4j_password="graph-password",
        graph_expansion_enabled=True,
        graph_expansion_depth=2,
        graph_expansion_limit=15,
    )
    monkeypatch.setattr(admin_routes, "settings", configured_settings)

    client = TestClient(app)
    response = client.get("/api/admin/runtime-config")

    assert response.status_code == 200
    payload = response.json()
    assert payload == {
        "embedding_provider": "openai_compatible",
        "embedding_base_url": "http://embedding.test/v1",
        "embedding_model": "embedding-model",
        "embedding_dimension": 768,
        "reranker_provider": "openai_compatible",
        "reranker_base_url": "http://reranker.test/v1",
        "reranker_model": "reranker-model",
        "llm_provider": "openai_compatible",
        "llm_base_url": "http://llm.test/v1",
        "llm_model": "llm-model",
        "vector_collection_name": "configured_chunks",
        "auto_recreate_collection": True,
        "default_chunk_size": 1000,
        "default_chunk_overlap": 150,
        "graph_enabled": True,
        "graph_provider": "neo4j",
        "graph_expansion_enabled": True,
        "graph_expansion_depth": 2,
        "graph_expansion_limit": 15,
        "streaming_supported": True,
    }
    assert "embedding_api_key" not in payload
    assert "reranker_api_key" not in payload
    assert "llm_api_key" not in payload
    assert "neo4j_password" not in payload
    assert "embedding-secret" not in response.text
    assert "reranker-secret" not in response.text
    assert "llm-secret" not in response.text
    assert "graph-password" not in response.text


def test_admin_enqueue_ingestion_job_returns_queued_job() -> None:
    queue = FakeIngestionQueue()
    app.dependency_overrides[get_ingestion_queue] = lambda: queue

    try:
        client = TestClient(app)
        response = client.post(
            "/api/admin/ingestion-jobs",
            files={"file": ("sample.pdf", b"%PDF-1.4 test", "application/pdf")},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 202
    payload = response.json()
    assert payload["filename"] == "sample.pdf"
    assert payload["content_type"] == "application/pdf"
    assert payload["status"] == "queued"
    assert payload["steps"][0]["name"] == "upload"
    assert payload["logs"] == []
    assert queue.run_calls == [queue.job.job_id]
