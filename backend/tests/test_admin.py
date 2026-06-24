from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID

from fastapi.testclient import TestClient

from app.api.routes import admin as admin_routes
from app.api.routes.admin import get_document_repository, get_ingestion_queue, get_vector_store
from app.core.config import Settings
from app.main import app
from app.services.ingestion.ingestion_queue import IngestionJob
from app.services.vector.vector_store import VectorStoreCollectionInfo


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
        self.upload_access: dict[str, object] | None = None
        self.organization_id: UUID | None = None

    def enqueue_upload(
        self,
        *,
        filename: str,
        content_type: str | None,
        content: bytes,
        organization_id: UUID | None = None,
        access: dict[str, object] | None = None,
    ) -> IngestionJob:
        self.job.filename = filename
        self.job.content_type = content_type
        self.organization_id = organization_id
        self.upload_access = access
        return self.job

    def get_job(self, job_id: object) -> IngestionJob | None:
        if str(job_id) == str(self.job.job_id):
            return self.job
        return None

    def list_jobs(self) -> list[IngestionJob]:
        return [self.job]

    def remove_job(self, job_id: object) -> bool:
        return str(job_id) == str(self.job.job_id)

    async def run_job(self, job_id: object) -> None:
        self.run_calls.append(job_id)

class FakeDocumentRepository:
    def __init__(self, duplicate_file: object | None = None) -> None:
        self.duplicate_file = duplicate_file

    async def find_document_file_by_signature(self, *, filename: str, file_size: int):
        return self.duplicate_file

class FakeRagRuntimeConfigRepository:
    def __init__(self, config: dict[str, object]) -> None:
        self.config = config

    async def seed_missing_configs(self, configs) -> None:
        return None

    async def get_config(self, config_name: str):
        return SimpleNamespace(config_name=config_name, config=self.config)

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None


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
        chunk_enrichment_enabled=True,
        retrieval_enrichment_enabled=True,
        enrichment_force_on_reingest=False,
        enrichment_update_keyword_search_vector=False,
        chunk_enrichment_provider="openai_compatible",
        chunk_enrichment_base_url="http://chunk-enrich.test/v1",
        chunk_enrichment_model="enrich-model",
        chunk_enrichment_max_chars=4096,
        chunk_enrichment_version="v2",
        embedding_enrichment_provider="openai_compatible",
        embedding_enrichment_base_url="http://embed-enrich.test/v1",
        embedding_enrichment_model="embed-enrich-model",
        embedding_enrichment_max_chars=7000,
        embedding_enrichment_version="v3",
        reingest_enrichment_provider="openai_compatible",
        reingest_enrichment_base_url="http://reingest-enrich.test/v1",
        reingest_enrichment_model="reingest-enrich-model",
        reingest_enrichment_max_chars=9000,
        reingest_enrichment_version="v4",
        enable_offline_enrichment=True,
        enable_query_enrichment=True,
        enable_context_expansion=True,
        enable_completeness_check=False,
        enable_second_retrieval=False,
        max_second_retrieval_rounds=1,
        overview_top_k=12,
        raw_top_k=0,
        summary_top_k=6,
        table_top_k=10,
        max_context_chars=20000,
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
    app.dependency_overrides[admin_routes.get_rag_runtime_config_repository] = lambda: FakeRagRuntimeConfigRepository(
        {
            "enable_chunk_enrichment_at_ingest": True,
            "enable_chunk_enrichment_at_retrieval": True,
            "enable_knowledge_artifact_compilation": True,
            "enable_llm_artifact_extraction": False,
            "enable_artifact_first_retrieval": True,
            "enable_chunk_fallback": True,
            "enable_neighbor_expansion": True,
            "enable_graph_expansion": True,
            "artifact_confidence_threshold": 0.45,
            "retrieval_token_budget": 6000,
            "max_artifacts": 6,
            "max_chunks": 8,
        }
    )

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
        "chunk_enrichment_enabled": True,
        "retrieval_enrichment_enabled": True,
        "enrichment_force_on_reingest": False,
        "enrichment_update_keyword_search_vector": False,
        "chunk_enrichment_provider": "openai_compatible",
        "chunk_enrichment_base_url": "http://chunk-enrich.test/v1",
        "chunk_enrichment_model": "enrich-model",
        "chunk_enrichment_max_chars": 4096,
        "chunk_enrichment_version": "v2",
        "embedding_enrichment_provider": "openai_compatible",
        "embedding_enrichment_base_url": "http://embed-enrich.test/v1",
        "embedding_enrichment_model": "embed-enrich-model",
        "embedding_enrichment_max_chars": 7000,
        "embedding_enrichment_version": "v3",
        "reingest_enrichment_provider": "openai_compatible",
        "reingest_enrichment_base_url": "http://reingest-enrich.test/v1",
        "reingest_enrichment_model": "reingest-enrich-model",
        "reingest_enrichment_max_chars": 9000,
        "reingest_enrichment_version": "v4",
        "enable_offline_enrichment": True,
        "enable_query_enrichment": True,
        "enable_context_expansion": True,
        "enable_completeness_check": False,
        "enable_second_retrieval": False,
        "max_second_retrieval_rounds": 1,
        "overview_top_k": 12,
        "raw_top_k": 0,
        "summary_top_k": 6,
        "table_top_k": 10,
        "max_context_chars": 20000,
        "enable_chunk_enrichment_at_ingest": True,
        "enable_chunk_enrichment_at_retrieval": True,
        "enable_knowledge_artifact_compilation": True,
        "enable_llm_artifact_extraction": False,
        "enable_artifact_first_retrieval": True,
        "enable_chunk_fallback": True,
        "enable_neighbor_expansion": True,
        "enable_graph_expansion": True,
        "artifact_confidence_threshold": 0.45,
        "retrieval_token_budget": 6000,
        "max_artifacts": 6,
        "max_chunks": 8,
        "rag_runtime_config_source": "PostgreSQL",
        "chunk_enrichment_enablement_source": "PostgreSQL",
        "vector_collection_name": "configured_chunks",
        "artifact_vector_collection_name": "hbrag_artifacts_v1",
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
    app.dependency_overrides[get_document_repository] = lambda: FakeDocumentRepository()

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
    assert [step["name"] for step in payload["steps"]] == [
        "upload",
        "parse",
        "chunk",
        "compile_artifacts",
        "enrich",
        "index",
    ]
    assert payload["steps"][0]["name"] == "upload"
    assert payload["logs"] == []
    assert queue.run_calls == [queue.job.job_id]

def test_admin_enqueue_ingestion_job_accepts_access_fields() -> None:
    queue = FakeIngestionQueue()
    organization_id = UUID("bbbbbbbb-2222-2222-2222-bbbbbbbbbbbb")
    app.dependency_overrides[get_ingestion_queue] = lambda: queue
    app.dependency_overrides[get_document_repository] = lambda: FakeDocumentRepository()

    try:
        client = TestClient(app)
        response = client.post(
            "/api/admin/ingestion-jobs",
            data={
                "organization_id": str(organization_id),
                "access_scope": "explicit_acl",
                "classification": "restricted",
                "allowed_org_ids": "org-a,org-b",
                "allowed_role_names": "COMPANY_ADMIN|UNIT_USER",
                "allowed_group_codes": "ai-team;legal-team",
            },
            files={"file": ("sample.pdf", b"%PDF-1.4 test", "application/pdf")},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 202
    assert queue.organization_id == organization_id
    assert queue.upload_access == {
        "owner_org_id": str(organization_id),
        "scope": "explicit_acl",
        "classification": "restricted",
        "allowed_org_ids": ["org-a", "org-b"],
        "allowed_role_names": ["COMPANY_ADMIN", "UNIT_USER"],
        "allowed_group_codes": ["ai-team", "legal-team"],
    }

def test_admin_enqueue_ingestion_job_rejects_duplicate_file() -> None:
    queue = FakeIngestionQueue()
    duplicate = type(
        "DuplicateFile",
        (),
        {
            "document_id": UUID("bbbbbbbb-1111-1111-1111-bbbbbbbbbbbb"),
            "document": None,
        },
    )()
    app.dependency_overrides[get_ingestion_queue] = lambda: queue
    app.dependency_overrides[get_document_repository] = lambda: FakeDocumentRepository(duplicate)

    try:
        client = TestClient(app)
        response = client.post(
            "/api/admin/ingestion-jobs",
            files={"file": ("sample.pdf", b"%PDF-1.4 test", "application/pdf")},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 409
    assert "Duplicate file" in response.json()["detail"]
    assert queue.run_calls == []

def test_admin_delete_ingestion_job_removes_monitor_entry() -> None:
    queue = FakeIngestionQueue()
    app.dependency_overrides[get_ingestion_queue] = lambda: queue

    try:
        client = TestClient(app)
        response = client.delete(f"/api/admin/ingestion-jobs/{queue.job.job_id}")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {"job_id": str(queue.job.job_id), "deleted": True}
