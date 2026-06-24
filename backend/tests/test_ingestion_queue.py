import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID, uuid4

from app.services import ingestion_queue as ingestion_queue_module
from app.services.ingestion.ingestion_queue import PIPELINE_STEPS, IngestionJob, IngestionQueue

DOCUMENT_ID = UUID("99999999-9999-9999-9999-999999999999")


def test_ingestion_pipeline_has_enrich_between_chunk_and_index() -> None:
    assert PIPELINE_STEPS == ("upload", "parse", "chunk", "compile_artifacts", "enrich", "index")


def test_ingestion_queue_logs_enrichment_failure_counts_without_failing_job() -> None:
    queue = IngestionQueue()
    job = IngestionJob(
        job_id=uuid4(),
        filename="sample.pdf",
        status="running",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    response = SimpleNamespace(
        status="failed",
        enriched_count=0,
        failed_count=3,
        skipped_count=1,
    )

    queue._log_enrichment_summary(job, response)

    assert job.status == "running"
    assert job.logs[-1].step == "enrich"
    assert job.logs[-1].level == "error"
    assert "failed_count=3" in job.logs[-1].message
    assert "skipped_count=1" in job.logs[-1].message

def test_ingestion_run_uses_enabled_enrichment_config(monkeypatch) -> None:
    job, enrich_calls, index_calls = _run_reingest_job_with_config(
        monkeypatch,
        {},
        runtime_settings={
            "chunk_enrichment_enabled": True,
            "retrieval_enrichment_enabled": True,
            "enrichment_force_on_reingest": True,
            "enrichment_update_keyword_search_vector": False,
            "reingest_enrichment_provider": "openai_compatible",
            "reingest_enrichment_base_url": "http://reingest-enrich.test/v1",
            "reingest_enrichment_model": "gpt-reingest",
            "reingest_enrichment_max_chars": 9000,
            "reingest_enrichment_version": "v3",
        },
    )

    assert job.status == "succeeded"
    assert enrich_calls == [
        {
            "enabled": True,
            "force": True,
            "update_keyword_search_vector": False,
            "provider": "openai_compatible",
            "model": "gpt-reingest",
            "max_chars": 9000,
            "version": "v3",
        }
    ]
    assert index_calls == [True]
    assert job.steps["enrich"].output["embedding_enrichment_enabled"] is True
    assert job.steps["enrich"].output["enrichment_step"] == "reingest"
    assert job.steps["enrich"].output["chunk_enrichment_provider"] == "openai_compatible"
    assert job.steps["enrich"].output["chunk_enrichment_base_url"] == "http://reingest-enrich.test/v1"
    assert job.steps["enrich"].output["chunk_enrichment_model"] == "gpt-reingest"
    assert job.steps["index"].output["use_enriched_content_for_embedding"] is True


def test_ingestion_run_uses_embedding_model_when_reingest_force_is_off(monkeypatch) -> None:
    job, enrich_calls, _index_calls = _run_reingest_job_with_config(
        monkeypatch,
        {},
        runtime_settings={
            "chunk_enrichment_enabled": True,
            "retrieval_enrichment_enabled": True,
            "enrichment_force_on_reingest": False,
            "enrichment_update_keyword_search_vector": True,
            "embedding_enrichment_provider": "openai_compatible",
            "embedding_enrichment_base_url": "http://embed-enrich.test/v1",
            "embedding_enrichment_model": "gpt-embed-enrich",
            "embedding_enrichment_max_chars": 7000,
            "embedding_enrichment_version": "v2",
            "reingest_enrichment_provider": "fake",
            "reingest_enrichment_model": "gpt-reingest",
        },
    )

    assert job.status == "succeeded"
    assert enrich_calls == [
        {
            "enabled": True,
            "force": False,
            "update_keyword_search_vector": True,
            "provider": "openai_compatible",
            "model": "gpt-embed-enrich",
            "max_chars": 7000,
            "version": "v2",
        }
    ]
    assert job.steps["enrich"].output["enrichment_step"] == "embedding"
    assert job.steps["enrich"].output["chunk_enrichment_base_url"] == "http://embed-enrich.test/v1"

def test_ingestion_run_skips_enrichment_and_indexes_original_content(monkeypatch) -> None:
    job, enrich_calls, index_calls = _run_reingest_job_with_config(
        monkeypatch,
        {},
        runtime_settings={
            "chunk_enrichment_enabled": False,
            "retrieval_enrichment_enabled": False,
            "enrichment_force_on_reingest": True,
            "enrichment_update_keyword_search_vector": True,
        },
    )

    assert job.status == "succeeded"
    assert enrich_calls == [
        {
            "enabled": False,
            "force": True,
            "update_keyword_search_vector": True,
            "provider": None,
            "model": None,
            "max_chars": 6000,
            "version": "v1",
        }
    ]
    assert index_calls == [False]
    assert job.steps["enrich"].output["status"] == "skipped"


def test_ingestion_run_honors_offline_enrichment_gate(monkeypatch) -> None:
    job, enrich_calls, index_calls = _run_reingest_job_with_config(
        monkeypatch,
        {},
        runtime_settings={
            "enable_offline_enrichment": False,
            "chunk_enrichment_enabled": True,
            "retrieval_enrichment_enabled": True,
            "enrichment_force_on_reingest": False,
        },
    )

    assert job.status == "succeeded"
    assert enrich_calls[0]["enabled"] is False
    assert index_calls == [False]
    assert job.steps["enrich"].output["enable_offline_enrichment"] is False
    assert job.steps["enrich"].output["chunk_enrichment_enabled"] is False

def _run_reingest_job_with_config(
    monkeypatch,
    config: dict[str, object],
    *,
    runtime_settings: dict[str, object] | None = None,
):
    enrich_calls: list[dict[str, object]] = []
    index_calls: list[bool] = []

    class FakeSessionContext:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, exc_type, exc, traceback):
            return None

    class FakeDocumentRepository:
        def __init__(self, _session) -> None:
            self.document = SimpleNamespace(
                id=DOCUMENT_ID,
                title="sample.pdf",
                status="chunked",
                parsed_text="general text",
                document_profile="general",
                document_metadata={},
            )

        async def get_document(self, document_id):
            return self.document if document_id == DOCUMENT_ID else None

        async def get_primary_document_file(self, document_id):
            return SimpleNamespace(
                filename="sample.pdf",
                storage_path="documents/sample.pdf",
                mime_type="application/pdf",
            )

        async def update_document_metadata(self, document, metadata):
            document.document_metadata = {**document.document_metadata, **metadata}
            return document

        async def commit(self) -> None:
            return None

    class FakeParserService:
        def __init__(self, **_kwargs) -> None:
            pass

        async def parse_document(self, document_id, *, force_reparse=False):
            return SimpleNamespace(
                document_id=document_id,
                status="parsed",
                character_count=12,
                preview="preview",
            )

    class FakeChunkingService:
        def __init__(self, **_kwargs) -> None:
            pass

        async def chunk_document(self, document_id, *, profile):
            return SimpleNamespace(
                document_id=document_id,
                status="chunked",
                chunk_count=1,
                preview=[],
            )

    class FakeEnrichmentService:
        def __init__(self, *, enabled, **_kwargs) -> None:
            self.enabled = enabled

        async def enrich_document(
            self,
            document_id,
            *,
            force=False,
            update_keyword_search_vector=True,
            provider=None,
            model=None,
            max_chars=None,
            version=None,
        ):
            enrich_calls.append(
                {
                    "enabled": self.enabled,
                    "force": force,
                    "update_keyword_search_vector": update_keyword_search_vector,
                    "provider": provider,
                    "model": model,
                    "max_chars": max_chars,
                    "version": version,
                }
            )
            status = "enriched" if self.enabled else "skipped"
            return SimpleNamespace(
                document_id=document_id,
                status=status,
                enriched_count=1 if self.enabled else 0,
                failed_count=0,
                skipped_count=0 if self.enabled else 1,
                preview=[],
            )

    class FakeVectorIndexingService:
        def __init__(self, **_kwargs) -> None:
            pass

        async def index_document(
            self,
            document_id,
            *,
            use_enriched_content_for_embedding=None,
        ):
            index_calls.append(bool(use_enriched_content_for_embedding))
            return SimpleNamespace(
                document_id=document_id,
                status="indexed",
                indexed_chunk_count=1,
            )

    class FakeIngestionProfileRepository:
        def __init__(self, _session) -> None:
            pass

        async def commit(self) -> None:
            return None

    async def fake_load_profile_configs(_repository):
        return {"general": dict(config)}

    fake_settings_values = {
        "chunk_enrichment_provider": None,
        "chunk_enrichment_base_url": None,
        "chunk_enrichment_model": None,
        "chunk_enrichment_max_chars": 6000,
        "chunk_enrichment_version": "v1",
        "enable_offline_enrichment": True,
        "chunk_enrichment_enabled": False,
        "retrieval_enrichment_enabled": False,
        "enrichment_force_on_reingest": True,
        "enrichment_update_keyword_search_vector": True,
        "embedding_enrichment_provider": None,
        "embedding_enrichment_base_url": None,
        "embedding_enrichment_model": None,
        "embedding_enrichment_max_chars": 6000,
        "embedding_enrichment_version": "v1",
        "reingest_enrichment_provider": None,
        "reingest_enrichment_base_url": None,
        "reingest_enrichment_model": None,
        "reingest_enrichment_max_chars": 6000,
        "reingest_enrichment_version": "v1",
        **dict(runtime_settings or {}),
    }
    fake_settings = SimpleNamespace(**fake_settings_values)

    monkeypatch.setattr(ingestion_queue_module, "AsyncSessionLocal", lambda: FakeSessionContext())
    monkeypatch.setattr(ingestion_queue_module, "settings", fake_settings)
    monkeypatch.setattr(ingestion_queue_module, "DocumentRepository", FakeDocumentRepository)
    monkeypatch.setattr(ingestion_queue_module, "DocumentParserService", FakeParserService)
    monkeypatch.setattr(ingestion_queue_module, "ChunkingService", FakeChunkingService)
    monkeypatch.setattr(ingestion_queue_module, "ChunkEnrichmentService", FakeEnrichmentService)
    monkeypatch.setattr(ingestion_queue_module, "VectorIndexingService", FakeVectorIndexingService)
    monkeypatch.setattr(
        ingestion_queue_module,
        "IngestionProfileRepository",
        FakeIngestionProfileRepository,
    )
    monkeypatch.setattr(ingestion_queue_module, "load_profile_configs", fake_load_profile_configs)
    monkeypatch.setattr(ingestion_queue_module, "profile_config", lambda _profile: dict(config))
    monkeypatch.setattr(ingestion_queue_module, "get_storage_client", lambda: object())
    monkeypatch.setattr(
        ingestion_queue_module,
        "build_llm_provider_or_error",
        lambda *, provider=None, base_url=None, model=None: SimpleNamespace(
            provider=provider,
            base_url=base_url,
            model=model,
        ),
    )
    monkeypatch.setattr(ingestion_queue_module, "get_embedding_provider", lambda: object())
    monkeypatch.setattr(ingestion_queue_module, "get_sparse_embedding_provider", lambda: object())
    monkeypatch.setattr(ingestion_queue_module, "get_vector_store", lambda: object())

    queue = IngestionQueue()
    job = queue.enqueue_document_reingestion(
        document_id=DOCUMENT_ID,
        filename="sample.pdf",
        content_type="application/pdf",
        ingestion_profile="general",
    )
    asyncio.run(queue.run_job(job.job_id))
    return job, enrich_calls, index_calls
