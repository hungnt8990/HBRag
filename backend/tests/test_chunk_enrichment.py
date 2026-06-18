import asyncio
import json
from types import SimpleNamespace
from uuid import UUID

from fastapi.testclient import TestClient

from app.api.routes import documents as documents_routes
from app.api.routes.documents import get_document_repository
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
        self.update_search_vector_calls: list[bool] = []

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
        update_search_vector: bool = True,
    ):
        self.update_search_vector_calls.append(update_search_vector)
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
            "article_number": "1",
            "article_title": "Quy trình vận hành",
            "clause_number": "2",
            "point_number": None,
            "appendix": None,
            "section_title": "Quy định chung",
            "parent_structure": "Chương I",
            "signer": "Nguyễn Văn A",
            "recipients": "Các đơn vị liên quan",
            "applies_to": ["CPCIT"],
            "responsible_unit": ["Phòng vận hành"],
            "deadline": None,
            "effective_scope": "Nội bộ",
            "supersedes": [],
            "amends": [],
            "referenced_documents": ["456/QĐ-CPCIT"],
            "table_name": None,
            "row_keys": [],
            "is_table_row": "false",
            "is_footer_or_signature": False,
            "answerable_facts": ["Quyết định có số 123/QĐ-CPCIT."],
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
        provider="openai_compatible",
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
    assert enrichment["provider"] == "openai_compatible"
    assert enrichment["model"] == "test-model"
    assert enrichment["version"] == "test-v1"
    assert enrichment["document_code"] == "123/QĐ-CPCIT"
    assert enrichment["keywords"] == ["quyết định", "vận hành"]
    assert enrichment["article_number"] == "1"
    assert enrichment["recipients"] == ["Các đơn vị liên quan"]
    assert enrichment["is_table_row"] is False
    assert enrichment["answerable_facts"] == ["Quyết định có số 123/QĐ-CPCIT."]
    assert chunk.chunk_metadata["keep"] == "original"
    assert chunk.enriched_content.startswith(chunk.content)
    assert "Tóm tắt: Chunk nêu số hiệu" in chunk.enriched_content
    assert "Fact trả lời trực tiếp: Quyết định có số 123/QĐ-CPCIT." in chunk.enriched_content
    assert repository.update_search_vector_calls == [True]
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

def test_force_enrich_failure_preserves_existing_success_metadata() -> None:
    repository = FakeDocumentRepository()
    repository.chunks[0].chunk_metadata["enrichment"] = {
        "status": "success",
        "summary": "Bản enrich cũ.",
        "keywords": ["cũ"],
    }
    repository.chunks[0].enriched_content = "Nội dung enrich cũ"
    service = ChunkEnrichmentService(
        repository=repository,
        llm_provider=FailingLLM(),
        enabled=True,
    )

    response = asyncio.run(service.enrich_document(DOCUMENT_ID, force=True))

    enrichment = repository.chunks[0].chunk_metadata["enrichment"]
    assert response.status == "failed"
    assert response.failed_count == 1
    assert enrichment["status"] == "success"
    assert enrichment["summary"] == "Bản enrich cũ."
    assert enrichment["last_attempt_status"] == "failed"
    assert "LLM provider unavailable" in enrichment["last_error"]
    assert repository.chunks[0].enriched_content == "Nội dung enrich cũ"


def test_chunk_enrichment_service_skips_when_disabled_without_force() -> None:
    repository = FakeDocumentRepository()
    llm = QueueLLM(_valid_enrichment_json())
    service = ChunkEnrichmentService(
        repository=repository,
        llm_provider=llm,
        enabled=False,
    )

    response = asyncio.run(service.enrich_document(DOCUMENT_ID))

    assert response.status == "skipped"
    assert response.enriched_count == 0
    assert response.failed_count == 0
    assert response.skipped_count == 1
    assert llm.calls == []
    assert "enrichment" not in repository.chunks[0].chunk_metadata
    assert repository.chunks[0].enriched_content is None
    assert repository.committed is False
    assert repository.rolled_back is False


def test_enrich_endpoint_runs_service_and_returns_counts(monkeypatch) -> None:
    repository = FakeDocumentRepository()
    llm = QueueLLM(_valid_enrichment_json())
    app.dependency_overrides[get_document_repository] = lambda: repository
    monkeypatch.setattr(
        documents_routes,
        "build_llm_provider_or_error",
        lambda *, provider=None, base_url=None, model=None: llm,
    )
    monkeypatch.setattr(
        documents_routes,
        "settings",
        SimpleNamespace(
            chunk_enrichment_enabled=True,
            enrichment_update_keyword_search_vector=True,
            chunk_enrichment_provider="openai_compatible",
            chunk_enrichment_base_url="http://chunk-enrich.test/v1",
            chunk_enrichment_model="endpoint-model",
            chunk_enrichment_max_chars=128,
            chunk_enrichment_version="endpoint-v2",
            embedding_enrichment_provider=None,
            embedding_enrichment_base_url=None,
            embedding_enrichment_model=None,
            embedding_enrichment_max_chars=6000,
            embedding_enrichment_version="v1",
        ),
    )

    try:
        response = TestClient(app).post(
            f"/api/documents/{DOCUMENT_ID}/enrich",
            json={
                "force": True,
                "enabled": True,
            },
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
    assert repository.chunks[0].chunk_metadata["enrichment"]["provider"] == "openai_compatible"
    assert repository.chunks[0].chunk_metadata["enrichment"]["model"] == "endpoint-model"
    assert repository.chunks[0].chunk_metadata["enrichment"]["version"] == "endpoint-v2"
