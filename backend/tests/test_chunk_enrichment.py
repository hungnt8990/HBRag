import asyncio
import json
from types import SimpleNamespace
from uuid import UUID

from fastapi.testclient import TestClient

from app.api.routes.documents import get_document_repository, get_llm_provider
from app.main import app
from app.services.chunk_enrichment_service import ChunkEnrichmentService

DOCUMENT_ID = UUID("dddddddd-4444-4444-4444-dddddddddddd")
CHUNK_ID = UUID("eeeeeeee-5555-5555-5555-eeeeeeeeeeee")
ORGANIZATION_ID = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")


class FakeDocumentRepository:
    def __init__(self, *, status: str = "chunked") -> None:
        self.document = SimpleNamespace(
            id=DOCUMENT_ID,
            title="Quyết định vận hành nội bộ",
            status=status,
            organization_id=ORGANIZATION_ID,
            document_metadata={"parser": "docling", "department": "CPCIT"},
        )
        self.chunks = [
            SimpleNamespace(
                id=CHUNK_ID,
                document_id=DOCUMENT_ID,
                chunk_index=0,
                content="Số 123/QĐ-CPCIT ngày 01/02/2024 về quy trình vận hành.",
                token_count=20,
                chunk_metadata={"chunk_id": "chunk_000", "keep": "original"},
                enriched_content=None,
            )
        ]
        self.committed = False
        self.rolled_back = False

    async def get_document(self, document_id: UUID):
        return self.document if document_id == DOCUMENT_ID else None

    async def list_chunks_for_document(self, document_id: UUID):
        return self.chunks if document_id == DOCUMENT_ID else []

    async def update_chunk_enrichment(
        self,
        chunk_id: UUID,
        *,
        enrichment_metadata: dict,
        enriched_content: str | None,
    ):
        chunk = next(item for item in self.chunks if item.id == chunk_id)
        metadata = dict(chunk.chunk_metadata or {})
        metadata["enrichment"] = {
            **dict(metadata.get("enrichment") or {}),
            **enrichment_metadata,
        }
        chunk.chunk_metadata = metadata
        chunk.enriched_content = enriched_content
        return chunk

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        self.rolled_back = True


class QueueLLM:
    def __init__(self, *responses: str) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, str]] = []

    async def generate(self, *, system_prompt: str, user_prompt: str) -> str:
        self.calls.append({"system": system_prompt, "user": user_prompt})
        return self.responses.pop(0)


class FailingLLM:
    async def generate(self, *, system_prompt: str, user_prompt: str) -> str:
        raise RuntimeError("LLM provider unavailable")


def _valid_enrichment_json() -> str:
    return json.dumps(
        {
            "summary": "Chunk nêu số hiệu và ngày ban hành quyết định vận hành.",
            "keywords": ["quyết định", "vận hành"],
            "aliases": ["QĐ vận hành"],
            "document_type": "quyết định",
            "issuing_org": "CPCIT",
            "document_code": "123/QĐ-CPCIT",
            "issued_date": "01/02/2024",
            "effective_date": None,
            "expiry_date": None,
            "legal_refs": [],
            "structure_path": "Quy trình vận hành",
            "entities": ["CPCIT"],
            "obligations": [],
            "permissions": [],
            "prohibitions": [],
            "table_context": None,
            "confidence": 0.9,
        },
        ensure_ascii=False,
    )


def test_chunk_enrichment_service_updates_metadata_and_enriched_content() -> None:
    repository = FakeDocumentRepository()
    service = ChunkEnrichmentService(
        repository=repository,
        llm_provider=QueueLLM(_valid_enrichment_json()),
        enabled=True,
        model="test-model",
        version="test-v1",
    )

    response = asyncio.run(service.enrich_document(DOCUMENT_ID, force=True))

    chunk = repository.chunks[0]
    enrichment = chunk.chunk_metadata["enrichment"]
    assert response.status == "enriched"
    assert response.enriched_count == 1
    assert response.failed_count == 0
    assert enrichment["status"] == "success"
    assert enrichment["model"] == "test-model"
    assert enrichment["version"] == "test-v1"
    assert enrichment["document_code"] == "123/QĐ-CPCIT"
    assert enrichment["keywords"] == ["quyết định", "vận hành"]
    assert chunk.chunk_metadata["keep"] == "original"
    assert chunk.enriched_content.startswith(chunk.content)
    assert "Tóm tắt: Chunk nêu số hiệu" in chunk.enriched_content
    assert repository.committed is True
    assert repository.rolled_back is False


def test_chunk_enrichment_service_marks_invalid_json_failed_without_crashing() -> None:
    repository = FakeDocumentRepository()
    service = ChunkEnrichmentService(
        repository=repository,
        llm_provider=QueueLLM("not json"),
        enabled=True,
    )

    response = asyncio.run(service.enrich_document(DOCUMENT_ID, force=True))

    enrichment = repository.chunks[0].chunk_metadata["enrichment"]
    assert response.status == "failed"
    assert response.enriched_count == 0
    assert response.failed_count == 1
    assert enrichment["status"] == "failed"
    assert "valid JSON" in enrichment["error"]
    assert repository.chunks[0].enriched_content is None
    assert repository.committed is True
    assert repository.rolled_back is False


def test_chunk_enrichment_service_marks_llm_error_failed_without_crashing() -> None:
    repository = FakeDocumentRepository()
    service = ChunkEnrichmentService(
        repository=repository,
        llm_provider=FailingLLM(),
        enabled=True,
    )

    response = asyncio.run(service.enrich_document(DOCUMENT_ID, force=True))

    enrichment = repository.chunks[0].chunk_metadata["enrichment"]
    assert response.status == "failed"
    assert response.enriched_count == 0
    assert response.failed_count == 1
    assert enrichment["status"] == "failed"
    assert enrichment["summary"] is None
    assert enrichment["keywords"] == []
    assert "LLM provider unavailable" in enrichment["error"]
    assert repository.chunks[0].enriched_content is None
    assert repository.committed is True
    assert repository.rolled_back is False


def test_enrich_endpoint_runs_service_and_returns_counts() -> None:
    repository = FakeDocumentRepository()
    llm = QueueLLM(_valid_enrichment_json())
    app.dependency_overrides[get_document_repository] = lambda: repository
    app.dependency_overrides[get_llm_provider] = lambda: llm

    try:
        response = TestClient(app).post(
            f"/api/documents/{DOCUMENT_ID}/enrich",
            json={"force": True},
        )
    finally:
        app.dependency_overrides.clear()

    payload = response.json()
    assert response.status_code == 200
    assert payload["document_id"] == str(DOCUMENT_ID)
    assert payload["status"] == "enriched"
    assert payload["enriched_count"] == 1
    assert payload["failed_count"] == 0
    assert payload["skipped_count"] == 0
    assert payload["preview"][0]["summary"].startswith("Chunk nêu số hiệu")
    assert repository.chunks[0].chunk_metadata["enrichment"]["status"] == "success"
