from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID

from fastapi.testclient import TestClient

from app.api.dependencies.auth import get_current_user
from app.api.routes.documents import (
    get_auth_repository,
    get_document_log_repository,
    get_document_repository,
    get_document_service,
    get_graph_repository,
)
from app.main import app
from app.repositories.documents import DocumentListRow
from app.schemas.documents import DocumentUploadResponse
from app.services.document_service import UnsupportedDocumentTypeError

ORG_ID = UUID("10000000-0000-0000-0000-000000000001")
OTHER_ORG_ID = UUID("10000000-0000-0000-0000-000000000002")
USER_ID = UUID("20000000-0000-0000-0000-000000000001")
OTHER_USER_ID = UUID("20000000-0000-0000-0000-000000000002")
DOCUMENT_ID = UUID("30000000-0000-0000-0000-000000000001")
SECOND_DOCUMENT_ID = UUID("30000000-0000-0000-0000-000000000002")


def _user(*, role: str = "SUPER_ADMIN", organization_id: UUID = ORG_ID):
    return SimpleNamespace(
        id=USER_ID,
        username="tester",
        email="tester@example.com",
        full_name="Test User",
        organization_id=organization_id,
        organization=SimpleNamespace(
            id=organization_id,
            ma_dviqly="ORG",
            ma_dviqly_cha=None,
            ten_dviqly="Organization",
            dvi_level=2,
            parent_id=None,
        ),
        roles=[SimpleNamespace(name=role)],
        is_active=True,
    )


def _document(
    *,
    document_id: UUID,
    title: str,
    filename: str,
    status: str = "uploaded",
    visibility: str = "organization",
    organization_id: UUID = ORG_ID,
    uploaded_by_user_id: UUID = USER_ID,
    parsed_text: str | None = None,
):
    organization = SimpleNamespace(
        id=organization_id,
        ma_dviqly="ORG",
        ten_dviqly="Organization",
        dvi_level=2,
    )
    uploaded_by = SimpleNamespace(
        id=uploaded_by_user_id,
        username="tester",
        full_name="Test User",
    )
    file = SimpleNamespace(
        id=UUID("40000000-0000-0000-0000-000000000001"),
        filename=filename,
        mime_type="application/pdf",
        storage_path=f"documents/{document_id}/{filename}",
        file_size=1234,
        created_at=datetime(2026, 6, 8, tzinfo=UTC),
    )
    return SimpleNamespace(
        id=document_id,
        title=title,
        status=status,
        visibility=visibility,
        organization_id=organization_id,
        organization=organization,
        uploaded_by_user_id=uploaded_by_user_id,
        uploaded_by=uploaded_by,
        parsed_text=parsed_text,
        created_at=datetime(2026, 6, 8, 10, 0, tzinfo=UTC),
        updated_at=datetime(2026, 6, 8, 11, 0, tzinfo=UTC),
        files=[file],
    )


class FakeAuthRepository:
    async def get_descendant_organization_ids(self, organization_id: UUID) -> set[UUID]:
        return {organization_id, ORG_ID, OTHER_ORG_ID}


class FakeDocumentRepository:
    def __init__(self) -> None:
        self.documents = {
            DOCUMENT_ID: _document(
                document_id=DOCUMENT_ID,
                title="Labor Policy",
                filename="labor-policy.pdf",
                status="indexed",
                parsed_text="A" * 640,
            ),
            SECOND_DOCUMENT_ID: _document(
                document_id=SECOND_DOCUMENT_ID,
                title="Private Note",
                filename="private-note.pdf",
                status="uploaded",
                visibility="private",
                organization_id=OTHER_ORG_ID,
                uploaded_by_user_id=OTHER_USER_ID,
                parsed_text=None,
            ),
        }
        self.commits = 0

    async def list_documents(self, **kwargs):
        return [
            DocumentListRow(
                document=self.documents[DOCUMENT_ID],
                filename="labor-policy.pdf",
                chunk_count=8,
                parsed_character_count=640,
                vector_indexed_count=8,
                pipeline_logs_count=3,
                graph_indexed=True,
            ),
            DocumentListRow(
                document=self.documents[SECOND_DOCUMENT_ID],
                filename="private-note.pdf",
                chunk_count=0,
                parsed_character_count=0,
                vector_indexed_count=None,
                pipeline_logs_count=1,
                graph_indexed=False,
            ),
        ]

    async def get_document(self, document_id: UUID):
        return self.documents.get(document_id)

    async def count_chunks_for_document(self, document_id: UUID) -> int:
        return 8 if document_id == DOCUMENT_ID else 0

    async def list_chunks_for_document(self, document_id: UUID):
        if document_id != DOCUMENT_ID:
            return []
        return [
            SimpleNamespace(
                id=UUID("50000000-0000-0000-0000-000000000001"),
                chunk_index=0,
                content="First chunk content",
                token_count=3,
                chunk_metadata={"page_number": 1},
                created_at=datetime(2026, 6, 8, 10, 45, tzinfo=UTC),
            )
        ]

    async def commit(self) -> None:
        self.commits += 1


class FakeDocumentLogRepository:
    def __init__(self) -> None:
        self.pipeline_log_calls = []
        self.access_log_calls = []
        self.rollbacks = 0

    async def create_pipeline_log(self, **kwargs):
        self.pipeline_log_calls.append(kwargs)
        return SimpleNamespace(**kwargs)

    async def create_access_log(self, **kwargs):
        self.access_log_calls.append(kwargs)
        return SimpleNamespace(**kwargs)

    async def latest_pipeline_logs(self, **kwargs):
        return [
            SimpleNamespace(
                action="index_vector",
                status="success",
                message="Indexed 8 chunks.",
                log_metadata={"indexed_chunk_count": 8},
                created_at=datetime(2026, 6, 8, 11, 30, tzinfo=UTC),
            ),
            SimpleNamespace(
                action="parse",
                status="success",
                message="Parsed 640 characters.",
                log_metadata={"character_count": 640},
                created_at=datetime(2026, 6, 8, 10, 30, tzinfo=UTC),
            ),
        ]

    async def count_pipeline_logs(self, **kwargs) -> int:
        return 2

    async def access_log_summary(self, **kwargs):
        return {"view": 4, "chat": 2}

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        self.rollbacks += 1


class FakeGraphRepository:
    async def get_document_status(self, document_id: UUID):
        if document_id != DOCUMENT_ID:
            return None
        return SimpleNamespace(
            graph_indexed=True,
            chunks_processed=8,
            entity_count=12,
            relation_count=7,
            last_indexed_at=datetime(2026, 6, 8, 11, 45, tzinfo=UTC),
            error_message=None,
        )

    async def list_extraction_logs(self, **kwargs):
        return [
            SimpleNamespace(
                status="success",
                entity_count=12,
                relation_count=7,
                merged_entity_count=10,
                merged_relation_count=6,
                error_message=None,
                log_metadata={"extractor_provider": "fake"},
                created_at=datetime(2026, 6, 8, 11, 45, tzinfo=UTC),
            )
        ]


class FakeBatchDocumentService:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def upload_document(self, upload_file, **kwargs):
        self.calls.append(upload_file.filename or "unknown")
        if upload_file.filename == "bad.exe":
            raise UnsupportedDocumentTypeError(
                "Unsupported file type. Supported types: PDF, DOCX, TXT, MD."
            )
        return DocumentUploadResponse(
            document_id=DOCUMENT_ID if upload_file.filename == "alpha.pdf" else SECOND_DOCUMENT_ID,
            filename=upload_file.filename or "unknown",
            status="uploaded",
            storage_path=f"documents/{upload_file.filename}",
        )


def test_list_documents_returns_summary_counts() -> None:
    repository = FakeDocumentRepository()
    app.dependency_overrides[get_current_user] = lambda: _user()
    app.dependency_overrides[get_auth_repository] = lambda: FakeAuthRepository()
    app.dependency_overrides[get_document_repository] = lambda: repository

    try:
        client = TestClient(app)
        response = client.get("/api/documents")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 2
    assert payload["limit"] == 50
    assert payload["offset"] == 0
    assert payload["items"][0]["document_id"] == str(DOCUMENT_ID)
    assert payload["items"][0]["chunk_count"] == 8
    assert payload["items"][0]["parsed_character_count"] == 640
    assert payload["items"][0]["vector_indexed_count"] == 8
    assert payload["items"][0]["pipeline_logs_count"] == 3
    assert payload["items"][0]["graph_indexed"] is True


def test_document_list_permissions_hide_inaccessible_documents() -> None:
    repository = FakeDocumentRepository()
    app.dependency_overrides[get_current_user] = lambda: _user(role="VIEWER")
    app.dependency_overrides[get_auth_repository] = lambda: FakeAuthRepository()
    app.dependency_overrides[get_document_repository] = lambda: repository

    try:
        client = TestClient(app)
        response = client.get("/api/documents")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert [item["document_id"] for item in payload["items"]] == [str(DOCUMENT_ID)]


def test_document_detail_returns_logs_and_preview() -> None:
    repository = FakeDocumentRepository()
    log_repository = FakeDocumentLogRepository()
    app.dependency_overrides[get_current_user] = lambda: _user()
    app.dependency_overrides[get_auth_repository] = lambda: FakeAuthRepository()
    app.dependency_overrides[get_document_repository] = lambda: repository
    app.dependency_overrides[get_document_log_repository] = lambda: log_repository
    app.dependency_overrides[get_graph_repository] = lambda: FakeGraphRepository()

    try:
        client = TestClient(app)
        response = client.get(f"/api/documents/{DOCUMENT_ID}")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload["document_id"] == str(DOCUMENT_ID)
    assert payload["parsed_character_count"] == 640
    assert payload["chunk_count"] == 8
    assert payload["preview_text"] == "A" * 640
    assert payload["chunks"][0]["chunk_index"] == 0
    assert payload["chunks"][0]["content"] == "First chunk content"
    assert payload["chunks"][0]["metadata"] == {"page_number": 1}
    assert payload["pipeline_logs"][0]["action"] == "index_vector"
    assert payload["access_logs_summary"] == {"view": 4, "chat": 2}
    assert payload["graph_status"]["graph_indexed"] is True
    assert payload["files"][0]["filename"] == "labor-policy.pdf"
    assert log_repository.access_log_calls[0]["action"] == "view"


def test_upload_batch_uploads_multiple_files_and_keeps_partial_failures() -> None:
    service = FakeBatchDocumentService()
    repository = FakeDocumentRepository()
    log_repository = FakeDocumentLogRepository()
    app.dependency_overrides[get_current_user] = lambda: _user()
    app.dependency_overrides[get_auth_repository] = lambda: FakeAuthRepository()
    app.dependency_overrides[get_document_service] = lambda: service
    app.dependency_overrides[get_document_repository] = lambda: repository
    app.dependency_overrides[get_document_log_repository] = lambda: log_repository

    try:
        client = TestClient(app)
        response = client.post(
            "/api/documents/upload-batch",
            files=[
                ("files", ("alpha.pdf", b"%PDF-1.4 alpha", "application/pdf")),
                ("files", ("bad.exe", b"bad", "application/octet-stream")),
                ("files", ("beta.txt", b"hello", "text/plain")),
            ],
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 201
    payload = response.json()
    assert payload["success_count"] == 2
    assert payload["failed_count"] == 1
    assert [item["success"] for item in payload["items"]] == [True, False, True]
    assert payload["items"][1]["filename"] == "bad.exe"
    assert "Unsupported file type" in payload["items"][1]["error"]
    assert service.calls == ["alpha.pdf", "bad.exe", "beta.txt"]
    assert len(log_repository.pipeline_log_calls) == 2
