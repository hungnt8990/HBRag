from types import SimpleNamespace
from uuid import UUID

from fastapi.testclient import TestClient

from app.api.routes.documents import get_document_repository
from app.main import app
from app.repositories.documents import ChunkCreate
from app.services.document_profiles import (
    FALLBACK_CONFIG,
    PROFILE_CONFIGS,
    detect_profile,
    profile_config,
    resolve_profile,
)

DOCUMENT_ID = UUID("55555555-5555-5555-5555-555555555555")

LEGAL_TEXT = (
    "CHƯƠNG I QUY ĐỊNH CHUNG\n"
    "Điều 1. Phạm vi điều chỉnh\nNội dung điều 1.\n\n"
    "Điều 2. Đối tượng áp dụng\nNội dung điều 2.\n\n"
    "Điều 3. Giải thích từ ngữ\nNội dung điều 3.\n"
)
GENERAL_TEXT = (
    "Đây là một tài liệu mô tả chung về quy trình làm việc của nhóm. "
    "Nội dung trình bày các bước thực hiện và lưu ý liên quan."
)
SPREADSHEET_TEXT = "\n".join(
    f"Cột A {i} | Cột B {i} | Cột C {i}" for i in range(20)
)


class FakeDocumentRepository:
    def __init__(self, *, parsed_text: str, document_profile: str = "auto") -> None:
        self.document = SimpleNamespace(
            id=DOCUMENT_ID,
            status="parsed",
            parsed_text=parsed_text,
            document_profile=document_profile,
        )
        self.created_chunks: list[ChunkCreate] = []
        self.committed = False

    async def get_document(self, document_id: UUID):
        if document_id != DOCUMENT_ID:
            return None
        return self.document

    async def delete_chunks_for_document(self, document_id: UUID) -> None:
        return None

    async def create_chunks(self, *, document_id: UUID, chunks: list[ChunkCreate]):
        self.created_chunks = list(chunks)
        return []

    async def update_document_status(self, document, status: str):
        document.status = status
        return document

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        return None


def test_detect_profile_legal_admin() -> None:
    assert detect_profile(LEGAL_TEXT) == "legal_admin"


def test_detect_profile_general_and_spreadsheet() -> None:
    assert detect_profile(GENERAL_TEXT) == "general"
    assert detect_profile(SPREADSHEET_TEXT) == "spreadsheet"


def test_resolve_profile_auto_uses_detection() -> None:
    assert resolve_profile("auto", text=LEGAL_TEXT) == "legal_admin"
    assert resolve_profile("technical", text=LEGAL_TEXT) == "technical"
    assert resolve_profile("unknown", text=LEGAL_TEXT) == "general"


def test_profile_config_returns_fallback_for_unknown() -> None:
    assert profile_config("legal_admin") == PROFILE_CONFIGS["legal_admin"]
    assert profile_config(None) == FALLBACK_CONFIG


def test_auto_profile_uses_legal_article_for_legal_document() -> None:
    repository = FakeDocumentRepository(parsed_text=LEGAL_TEXT, document_profile="auto")
    app.dependency_overrides[get_document_repository] = lambda: repository

    try:
        client = TestClient(app)
        response = client.post(f"/api/documents/{DOCUMENT_ID}/chunk")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert repository.document.document_profile == "legal_admin"
    assert repository.created_chunks
    for chunk in repository.created_chunks:
        assert chunk.metadata["chunk_mode"] == "legal_article"
        assert chunk.metadata["document_profile"] == "legal_admin"


def test_auto_profile_uses_recursive_for_general_document() -> None:
    repository = FakeDocumentRepository(parsed_text="a" * 2000, document_profile="auto")
    app.dependency_overrides[get_document_repository] = lambda: repository

    try:
        client = TestClient(app)
        response = client.post(f"/api/documents/{DOCUMENT_ID}/chunk")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert repository.document.document_profile == "general"
    for chunk in repository.created_chunks:
        assert chunk.metadata["chunk_mode"] == "recursive"
        assert chunk.metadata["document_profile"] == "general"


def test_explicit_profile_overrides_detection() -> None:
    repository = FakeDocumentRepository(parsed_text="a" * 2000, document_profile="auto")
    app.dependency_overrides[get_document_repository] = lambda: repository

    try:
        client = TestClient(app)
        response = client.post(
            f"/api/documents/{DOCUMENT_ID}/chunk",
            json={"profile": "legal_admin"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert repository.document.document_profile == "legal_admin"
    for chunk in repository.created_chunks:
        assert chunk.metadata["chunk_mode"] == "legal_article"


def test_admin_profiles_endpoint_returns_configs() -> None:
    client = TestClient(app)
    response = client.get("/api/admin/profiles")

    assert response.status_code == 200
    payload = response.json()
    assert payload["default_profile"] == "auto"
    assert "legal_admin" in payload["profiles"]
    assert payload["configs"]["legal_admin"]["chunk_mode"] == "legal_article"
    assert payload["configs"]["legal_admin"]["answer_style"] == "policy_explainer"
    assert payload["configs"]["general"]["chunk_mode"] == "recursive"
    assert payload["configs"]["general"]["answer_style"] == "detailed"
