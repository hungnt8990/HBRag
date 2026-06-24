import asyncio
import json
from types import SimpleNamespace
from uuid import UUID

from fastapi.testclient import TestClient

from app.api.routes import documents as documents_routes
from app.api.routes.documents import get_document_repository
from app.main import app
from app.services.chunkers.chunker_chunk_enrichment_service import ChunkEnrichmentService, should_llm_enrich

DOCUMENT_ID = UUID("dddddddd-4444-4444-4444-dddddddddddd")
CHUNK_ID = UUID("eeeeeeee-5555-5555-5555-eeeeeeeeeeee")
ORGANIZATION_ID = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")


class FakeDocumentRepository:
    def __init__(self, *, status: str = "chunked") -> None:
        self.document = SimpleNamespace(
            id=DOCUMENT_ID,
            title="Quyáº¿t Ä‘á»‹nh váº­n hÃ nh ná»™i bá»™",
            status=status,
            organization_id=ORGANIZATION_ID,
            document_metadata={"parser": "docling", "department": "CPCIT"},
        )
        self.chunks = [
            SimpleNamespace(
                id=CHUNK_ID,
                document_id=DOCUMENT_ID,
                chunk_index=0,
                content="Sá»‘ 123/QÄ-CPCIT ngÃ y 01/02/2024 vá» quy trÃ¬nh váº­n hÃ nh.",
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
        rule_enrichment: dict | None = None,
        update_search_vector: bool = True,
    ):
        self.update_search_vector_calls.append(update_search_vector)
        chunk = next(item for item in self.chunks if item.id == chunk_id)
        metadata = dict(chunk.chunk_metadata or {})
        metadata["enrichment"] = {
            **dict(metadata.get("enrichment") or {}),
            **enrichment_metadata,
        }
        if rule_enrichment is not None:
            metadata["rule_enrichment"] = {
                **dict(metadata.get("rule_enrichment") or {}),
                **rule_enrichment,
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

class OrderedSlowLLM:
    def __init__(self) -> None:
        self.calls: list[int] = []

    async def generate(self, *, system_prompt: str, user_prompt: str) -> str:
        marker = '"chunk_index": '
        index = int(user_prompt.split(marker, 1)[1].split(",", 1)[0])
        self.calls.append(index)
        await asyncio.sleep(0.01 * (3 - index))
        return json.dumps(
            {
                "summary": f"Chunk {index}",
                "keywords": [f"kw-{index}"],
                "entities": [],
                "aliases": [],
                "answerable_facts": [],
                "possible_queries": [],
                "table_context": f"row {index}",
                "legal_context": None,
                "confidence": 0.9,
            },
            ensure_ascii=False,
        )


def _valid_enrichment_json() -> str:
    return json.dumps(
        {
            "summary": "Chunk nÃªu sá»‘ hiá»‡u vÃ  ngÃ y ban hÃ nh quyáº¿t Ä‘á»‹nh váº­n hÃ nh.",
            "keywords": ["quyáº¿t Ä‘á»‹nh", "váº­n hÃ nh"],
            "aliases": ["QÄ váº­n hÃ nh"],
            "document_type": "quyáº¿t Ä‘á»‹nh",
            "issuing_org": "CPCIT",
            "document_code": "123/QÄ-CPCIT",
            "issued_date": "01/02/2024",
            "effective_date": None,
            "expiry_date": None,
            "legal_refs": [],
            "structure_path": "Quy trÃ¬nh váº­n hÃ nh",
            "entities": ["CPCIT"],
            "obligations": [],
            "permissions": [],
            "prohibitions": [],
            "table_context": None,
            "article_number": "1",
            "article_title": "Quy trÃ¬nh váº­n hÃ nh",
            "clause_number": "2",
            "point_number": None,
            "appendix": None,
            "section_title": "Quy Ä‘á»‹nh chung",
            "parent_structure": "ChÆ°Æ¡ng I",
            "signer": "Nguyá»…n VÄƒn A",
            "recipients": "CÃ¡c Ä‘Æ¡n vá»‹ liÃªn quan",
            "applies_to": ["CPCIT"],
            "responsible_unit": ["PhÃ²ng váº­n hÃ nh"],
            "deadline": None,
            "effective_scope": "Ná»™i bá»™",
            "supersedes": [],
            "amends": [],
            "referenced_documents": ["456/QÄ-CPCIT"],
            "table_name": None,
            "row_keys": [],
            "is_table_row": "false",
            "is_footer_or_signature": False,
            "answerable_facts": ["Quyáº¿t Ä‘á»‹nh cÃ³ sá»‘ 123/QÄ-CPCIT."],
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
    assert enrichment["document_code"] == "123/QÄ-CPCIT"
    assert enrichment["keywords"] == ["quyáº¿t Ä‘á»‹nh", "váº­n hÃ nh"]
    assert enrichment["article_number"] == "1"
    assert enrichment["recipients"] == ["CÃ¡c Ä‘Æ¡n vá»‹ liÃªn quan"]
    assert enrichment["is_table_row"] is False
    assert enrichment["answerable_facts"] == ["Quyáº¿t Ä‘á»‹nh cÃ³ sá»‘ 123/QÄ-CPCIT."]
    assert chunk.chunk_metadata["keep"] == "original"
    assert chunk.enriched_content is not None
    assert not chunk.enriched_content.startswith(chunk.content)
    assert "TÃ³m táº¯t: Chunk nÃªu sá»‘ hiá»‡u" in chunk.enriched_content
    assert "Fact tráº£ lá»i trá»±c tiáº¿p: Quyáº¿t Ä‘á»‹nh cÃ³ sá»‘ 123/QÄ-CPCIT." in chunk.enriched_content
    assert chunk.chunk_metadata["rule_enrichment"]["document_code"] == "123/QÄ-CPCIT"
    assert repository.update_search_vector_calls == [True]
    assert repository.committed is True
    assert repository.rolled_back is False


def test_chunk_enrichment_deduplicates_and_validates_entities() -> None:
    repository = FakeDocumentRepository()
    payload = json.dumps(
        {
            "summary": "Chunk nÃªu sá»‘ hiá»‡u vÃ  Ä‘Æ¡n vá»‹ CPCIT.",
            "keywords": [],
            "entities": ["CPCIT", "CPCIT", "ThÆ° Viá»‡n Quá»‘c Gia HÃ  Ná»™i"],
            "aliases": [],
            "answerable_facts": [],
            "possible_queries": [],
            "table_context": None,
            "legal_context": None,
            "confidence": 0.9,
        },
        ensure_ascii=False,
    )
    service = ChunkEnrichmentService(repository=repository, llm_provider=QueueLLM(payload), enabled=True)

    response = asyncio.run(service.enrich_document(DOCUMENT_ID, force=True))

    assert response.status == "enriched"
    assert repository.chunks[0].chunk_metadata["enrichment"]["entities"] == ["CPCIT"]
def test_clear_prose_with_section_path_uses_rule_enrichment_without_llm() -> None:
    repository = FakeDocumentRepository()
    repository.chunks[0].content = (
        "Quy trÃ¬nh váº­n hÃ nh há»‡ thá»‘ng Ä‘Æ°á»£c thá»±c hiá»‡n theo cÃ¡c bÆ°á»›c kiá»ƒm tra, "
        "phÃª duyá»‡t vÃ  ghi nháº­n káº¿t quáº£ trÃªn pháº§n má»m ná»™i bá»™. Ná»™i dung nÃ y Ä‘Ã£ "
        "nÃªu rÃµ pháº¡m vi Ã¡p dá»¥ng, Ä‘Æ¡n vá»‹ phá»‘i há»£p vÃ  trÃ¡ch nhiá»‡m chung cá»§a cÃ¡c "
        "bá»™ pháº­n liÃªn quan trong quÃ¡ trÃ¬nh xá»­ lÃ½ cÃ´ng viá»‡c háº±ng ngÃ y. "
        "CÃ¡c bÆ°á»›c Ä‘Æ°á»£c mÃ´ táº£ Ä‘áº§y Ä‘á»§, cÃ³ ngá»¯ cáº£nh má»¥c rÃµ rÃ ng vÃ  khÃ´ng chá»©a "
        "dÃ²ng báº£ng, sá»‘ hiá»‡u vÄƒn báº£n, ngÃ y thÃ¡ng hoáº·c mÃ£ Ä‘á»‹nh danh cáº§n suy diá»…n."
    )
    repository.chunks[0].chunk_metadata = {
        "chunk_id": "chunk_000",
        "section_path": ["Quy trÃ¬nh", "Váº­n hÃ nh"],
    }
    llm = QueueLLM(_valid_enrichment_json())
    service = ChunkEnrichmentService(repository=repository, llm_provider=llm, enabled=True)

    response = asyncio.run(service.enrich_document(DOCUMENT_ID))

    assert response.status == "skipped"
    assert response.skipped_count == 1
    assert llm.calls == []
    enrichment = repository.chunks[0].chunk_metadata["enrichment"]
    assert enrichment["status"] == "skipped"
    assert enrichment["last_skip_reason"]["skip"] == "clear_prose_with_section_path"
    assert repository.chunks[0].chunk_metadata["rule_enrichment"]["section_path"] == "Quy trÃ¬nh > Váº­n hÃ nh"

def test_table_row_calls_llm() -> None:
    repository = FakeDocumentRepository()
    repository.chunks[0].chunk_metadata = {
        "chunk_id": "chunk_000",
        "chunk_type": "table_row",
        "table_name": "BangPhanCong",
        "table_columns": ["STT", "NhÃ¢n sá»±", "Máº£ng cÃ´ng nghá»‡"],
    }
    llm = QueueLLM(_valid_enrichment_json())
    service = ChunkEnrichmentService(repository=repository, llm_provider=llm, enabled=True)

    response = asyncio.run(service.enrich_document(DOCUMENT_ID))

    assert response.status == "enriched"
    assert len(llm.calls) == 1
    assert repository.chunks[0].chunk_metadata["enrichment"]["llm_reason"]["trigger"] == "structured_chunk_type"

def test_short_chunk_with_document_code_calls_llm() -> None:
    repository = FakeDocumentRepository()
    llm = QueueLLM(_valid_enrichment_json())
    service = ChunkEnrichmentService(repository=repository, llm_provider=llm, enabled=True)

    response = asyncio.run(service.enrich_document(DOCUMENT_ID))

    assert response.status == "enriched"
    assert len(llm.calls) == 1
    assert repository.chunks[0].chunk_metadata["enrichment"]["llm_reason"]["trigger"] == "short_with_codes"

def test_existing_enrichment_with_matching_input_hash_skips_llm() -> None:
    repository = FakeDocumentRepository()
    first = ChunkEnrichmentService(
        repository=repository,
        llm_provider=QueueLLM(_valid_enrichment_json()),
        enabled=True,
        model="cache-model",
        version="cache-v1",
    )
    asyncio.run(first.enrich_document(DOCUMENT_ID))
    input_hash = repository.chunks[0].chunk_metadata["enrichment"]["input_hash"]
    llm = QueueLLM(_valid_enrichment_json())
    second = ChunkEnrichmentService(
        repository=repository,
        llm_provider=llm,
        enabled=True,
        model="cache-model",
        version="cache-v1",
    )

    response = asyncio.run(second.enrich_document(DOCUMENT_ID))

    assert response.status == "skipped"
    assert response.skipped_count == 1
    assert llm.calls == []
    assert repository.chunks[0].chunk_metadata["enrichment"]["input_hash"] == input_hash
    assert repository.chunks[0].chunk_metadata["enrichment"]["last_skip_reason"]["skip"] == "cache_hit"

def test_content_change_invalidates_hash_and_enriches_again() -> None:
    repository = FakeDocumentRepository()
    first = ChunkEnrichmentService(
        repository=repository,
        llm_provider=QueueLLM(_valid_enrichment_json()),
        enabled=True,
        model="cache-model",
        version="cache-v1",
    )
    asyncio.run(first.enrich_document(DOCUMENT_ID))
    input_hash = repository.chunks[0].chunk_metadata["enrichment"]["input_hash"]
    repository.chunks[0].content = "Sá»‘ 999/QÄ-CPCIT ngÃ y 02/03/2024 vá» quy trÃ¬nh má»›i."
    llm = QueueLLM(_valid_enrichment_json())
    second = ChunkEnrichmentService(
        repository=repository,
        llm_provider=llm,
        enabled=True,
        model="cache-model",
        version="cache-v1",
    )

    response = asyncio.run(second.enrich_document(DOCUMENT_ID))

    assert response.status == "enriched"
    assert len(llm.calls) == 1
    assert repository.chunks[0].chunk_metadata["enrichment"]["input_hash"] != input_hash


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

def test_one_chunk_failure_does_not_fail_entire_document() -> None:
    repository = FakeDocumentRepository()
    repository.chunks = [
        SimpleNamespace(
            id=UUID("eeeeeeee-5555-5555-5555-eeeeeeeeeee1"),
            document_id=DOCUMENT_ID,
            chunk_index=0,
            content="STT: 1\nNhÃ¢n sá»±: A\nMáº£ng cÃ´ng nghá»‡: GIS",
            token_count=12,
            chunk_metadata={"chunk_id": "chunk_000", "chunk_type": "table_row"},
            enriched_content=None,
        ),
        SimpleNamespace(
            id=UUID("eeeeeeee-5555-5555-5555-eeeeeeeeeee2"),
            document_id=DOCUMENT_ID,
            chunk_index=1,
            content="STT: 2\nNhÃ¢n sá»±: B\nMáº£ng cÃ´ng nghá»‡: DMS",
            token_count=12,
            chunk_metadata={"chunk_id": "chunk_001", "chunk_type": "table_row"},
            enriched_content=None,
        ),
    ]
    service = ChunkEnrichmentService(
        repository=repository,
        llm_provider=QueueLLM(_valid_enrichment_json(), "not-json"),
        enabled=True,
    )

    response = asyncio.run(service.enrich_document(DOCUMENT_ID))

    assert response.status == "partial"
    assert response.enriched_count == 1
    assert response.failed_count == 1
    assert repository.committed is True
    assert repository.rolled_back is False
    assert repository.chunks[0].chunk_metadata["enrichment"]["status"] == "success"
    assert repository.chunks[1].chunk_metadata["enrichment"]["status"] == "failed"

def test_concurrent_enrichment_preserves_preview_order_and_counts(monkeypatch) -> None:
    repository = FakeDocumentRepository()
    repository.chunks = [
        SimpleNamespace(
            id=UUID(f"eeeeeeee-5555-5555-5555-eeeeeeeeeee{index}"),
            document_id=DOCUMENT_ID,
            chunk_index=index,
            content=f"STT: {index}\nNhÃ¢n sá»±: NgÆ°á»i {index}\nMáº£ng cÃ´ng nghá»‡: GIS",
            token_count=12,
            chunk_metadata={"chunk_id": f"chunk_00{index}", "chunk_type": "table_row"},
            enriched_content=None,
        )
        for index in range(3)
    ]
    monkeypatch.setattr(
        "app.services.chunkers.chunker_chunk_enrichment_service.settings.chunk_enrichment_concurrency",
        3,
    )
    llm = OrderedSlowLLM()
    service = ChunkEnrichmentService(repository=repository, llm_provider=llm, enabled=True)

    response = asyncio.run(service.enrich_document(DOCUMENT_ID))

    assert response.status == "enriched"
    assert response.enriched_count == 3
    assert [item.chunk_index for item in response.preview] == [0, 1, 2]
    assert [chunk.chunk_metadata["enrichment"]["summary"] for chunk in repository.chunks] == [
        "Chunk 0",
        "Chunk 1",
        "Chunk 2",
    ]

def test_should_llm_enrich_skips_footer() -> None:
    document = SimpleNamespace(title="Doc", document_metadata={})
    chunk = SimpleNamespace(
        content="NÆ¡i nháº­n: NhÆ° trÃªn; LÆ°u VT.",
        chunk_metadata={"chunk_type": "administrative_footer"},
    )

    should_call, reason = should_llm_enrich(chunk, document, {"chunk_enrichment_mode": "selective"})

    assert should_call is False
    assert reason["skip"] == "footer_or_non_indexable"

def test_force_enrich_failure_preserves_existing_success_metadata() -> None:
    repository = FakeDocumentRepository()
    repository.chunks[0].chunk_metadata["enrichment"] = {
        "status": "success",
        "summary": "Báº£n enrich cÅ©.",
        "keywords": ["cÅ©"],
    }
    repository.chunks[0].enriched_content = "Ná»™i dung enrich cÅ©"
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
    assert enrichment["summary"] == "Báº£n enrich cÅ©."
    assert enrichment["last_attempt_status"] == "failed"
    assert "LLM provider unavailable" in enrichment["last_error"]
    assert repository.chunks[0].enriched_content == "Ná»™i dung enrich cÅ©"


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
    assert payload["preview"][0]["summary"].startswith("Chunk nÃªu sá»‘ hiá»‡u")
    assert repository.chunks[0].chunk_metadata["enrichment"]["status"] == "success"
    assert repository.chunks[0].chunk_metadata["enrichment"]["provider"] == "openai_compatible"
    assert repository.chunks[0].chunk_metadata["enrichment"]["model"] == "endpoint-model"
    assert repository.chunks[0].chunk_metadata["enrichment"]["version"] == "endpoint-v2"
