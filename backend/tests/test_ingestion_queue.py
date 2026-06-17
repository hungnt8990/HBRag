from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

from app.services.ingestion_queue import PIPELINE_STEPS, IngestionJob, IngestionQueue


def test_ingestion_pipeline_has_enrich_between_chunk_and_index() -> None:
    assert PIPELINE_STEPS == ("upload", "parse", "chunk", "enrich", "index")


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
