from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies.auth import get_current_user
from app.api.routes.documents import (
    get_document_repository,
    get_knowledge_base_repository,
    get_storage_client,
    get_vector_store,
)
from app.main import app

DOCUMENT_ID = UUID("11111111-1111-1111-1111-111111111111")
USER_ID = UUID("22222222-2222-2222-2222-222222222222")
OTHER_USER_ID = UUID("33333333-3333-3333-3333-333333333333")
ORG_ID = UUID("44444444-4444-4444-4444-444444444444")
KNOWLEDGE_BASE_ID = UUID("55555555-5555-5555-5555-555555555555")


class FakeDocumentRepository:
    def __init__(self) -> None:
        self.documents: list[SimpleNamespace] = []
        self.document_files: list[SimpleNamespace] = []
        self.committed = False
        self.rolled_back = False

    async def create_document(
        self,
        *,
        title: str,
        source_type: str,
        status: str = "uploaded",
        uploaded_by_user_id=None,
        organization_id=None,
        knowledge_base_id=None,
        visibility: str = "organization",
    ) -> SimpleNamespace:
        document = SimpleNamespace(
            id=DOCUMENT_ID,
            title=title,
            source_type=source_type,
            status=status,
            uploaded_by_user_id=uploaded_by_user_id,
            organization_id=organization_id,
            knowledge_base_id=knowledge_base_id,
            visibility=visibility,
            files=[],
        )
        self.documents.append(document)
        return document

    async def create_document_file(
        self,
        *,
        document_id: UUID,
        filename: str,
        mime_type: str,
        storage_path: str,
        file_size: int,
    ) -> SimpleNamespace:
        document_file = SimpleNamespace(
            document_id=document_id,
            filename=filename,
            mime_type=mime_type,
            storage_path=storage_path,
            file_size=file_size,
        )
        self.document_files.append(document_file)
        return document_file

    async def get_document(self, document_id: UUID) -> SimpleNamespace | None:
        return next((document for document in self.documents if document.id == document_id), None)

    async def delete_document(self, document: SimpleNamespace) -> None:
        self.documents = [item for item in self.documents if item.id != document.id]

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        self.rolled_back = True


class FakeStorageClient:
    def __init__(self) -> None:
        self.uploads: list[dict[str, object]] = []
        self.deleted: list[str] = []

    async def put_file(
        self,
        *,
        object_name: str,
        data: object,
        length: int,
        content_type: str,
    ) -> str:
        self.uploads.append(
            {
                "object_name": object_name,
                "length": length,
                "content_type": content_type,
            }
        )
        return object_name

    async def delete_file(self, *, object_name: str) -> None:
        self.deleted.append(object_name)

class FakeVectorStore:
    def __init__(self) -> None:
        self.deleted_document_ids: list[UUID | str] = []

    async def delete_points_for_document(self, document_id: UUID | str) -> None:
        self.deleted_document_ids.append(document_id)

class FakeKnowledgeBaseRepository:
    def __init__(self, *, owner_user_id: UUID = USER_ID) -> None:
        self.knowledge_base = SimpleNamespace(
            id=KNOWLEDGE_BASE_ID,
            organization_id=ORG_ID,
            owner_user_id=owner_user_id,
            visibility="organization",
            is_active=True,
            members=[],
        )

    async def get_by_id(self, knowledge_base_id: UUID):
        if knowledge_base_id == KNOWLEDGE_BASE_ID:
            return self.knowledge_base
        return None

    async def get_or_create_default(self, **kwargs):
        return self.knowledge_base

def _user(*, role: str = "UNIT_USER"):
    return SimpleNamespace(
        id=USER_ID,
        organization_id=ORG_ID,
        roles=[SimpleNamespace(name=role)],
        is_active=True,
    )


def test_upload_document_creates_document_and_file_rows() -> None:
    repository = FakeDocumentRepository()
    storage = FakeStorageClient()
    app.dependency_overrides[get_document_repository] = lambda: repository
    app.dependency_overrides[get_storage_client] = lambda: storage

    try:
        client = TestClient(app)
        response = client.post(
            "/api/documents/upload",
            files={"file": ("sample.pdf", b"%PDF-1.4 test", "application/pdf")},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 201
    payload = response.json()
    assert payload["document_id"] == str(DOCUMENT_ID)
    assert payload["filename"] == "sample.pdf"
    assert payload["status"] == "uploaded"
    assert payload["storage_path"].startswith(f"documents/{DOCUMENT_ID}/original/")
    assert payload["storage_path"].endswith(".pdf")

    assert repository.committed is True
    assert repository.rolled_back is False
    assert len(repository.documents) == 1
    assert repository.documents[0].title == "sample"
    assert repository.documents[0].source_type == "pdf"
    assert repository.documents[0].status == "uploaded"
    assert len(repository.document_files) == 1
    assert repository.document_files[0].filename == "sample.pdf"
    assert repository.document_files[0].mime_type == "application/pdf"
    assert repository.document_files[0].file_size == len(b"%PDF-1.4 test")
    assert repository.document_files[0].storage_path == payload["storage_path"]
    assert storage.uploads == [
        {
            "object_name": payload["storage_path"],
            "length": len(b"%PDF-1.4 test"),
            "content_type": "application/pdf",
        }
    ]

def test_upload_document_to_owned_knowledge_base_sets_document_scope() -> None:
    repository = FakeDocumentRepository()
    storage = FakeStorageClient()
    app.dependency_overrides[get_current_user] = lambda: _user()
    app.dependency_overrides[get_document_repository] = lambda: repository
    app.dependency_overrides[get_storage_client] = lambda: storage
    app.dependency_overrides[get_knowledge_base_repository] = (
        lambda: FakeKnowledgeBaseRepository(owner_user_id=USER_ID)
    )

    try:
        client = TestClient(app)
        response = client.post(
            "/api/documents/upload",
            data={"knowledge_base_id": str(KNOWLEDGE_BASE_ID)},
            files={"file": ("sample.pdf", b"%PDF-1.4 test", "application/pdf")},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 201
    assert repository.documents[0].knowledge_base_id == KNOWLEDGE_BASE_ID

def test_upload_document_to_knowledge_base_without_editor_permission_returns_403() -> None:
    repository = FakeDocumentRepository()
    storage = FakeStorageClient()
    app.dependency_overrides[get_current_user] = lambda: _user()
    app.dependency_overrides[get_document_repository] = lambda: repository
    app.dependency_overrides[get_storage_client] = lambda: storage
    app.dependency_overrides[get_knowledge_base_repository] = (
        lambda: FakeKnowledgeBaseRepository(owner_user_id=OTHER_USER_ID)
    )

    try:
        client = TestClient(app)
        response = client.post(
            "/api/documents/upload",
            data={"knowledge_base_id": str(KNOWLEDGE_BASE_ID)},
            files={"file": ("sample.pdf", b"%PDF-1.4 test", "application/pdf")},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 403
    assert repository.documents == []
    assert storage.uploads == []


def test_upload_document_rejects_unsupported_file_type() -> None:
    repository = FakeDocumentRepository()
    storage = FakeStorageClient()
    app.dependency_overrides[get_document_repository] = lambda: repository
    app.dependency_overrides[get_storage_client] = lambda: storage

    try:
        client = TestClient(app)
        response = client.post(
            "/api/documents/upload",
            files={"file": ("malware.exe", b"not allowed", "application/octet-stream")},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 415
    assert repository.documents == []
    assert repository.document_files == []
    assert storage.uploads == []


@pytest.mark.parametrize(
    ("filename", "content_type"),
    [
        ("sample.pdf", "application/pdf"),
        (
            "sample.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ),
        ("sample.txt", "text/plain"),
        ("sample.md", "text/markdown"),
    ],
)
def test_upload_document_accepts_supported_file_types(
    filename: str,
    content_type: str,
) -> None:
    repository = FakeDocumentRepository()
    storage = FakeStorageClient()
    app.dependency_overrides[get_document_repository] = lambda: repository
    app.dependency_overrides[get_storage_client] = lambda: storage

    try:
        client = TestClient(app)
        response = client.post(
            "/api/documents/upload",
            files={"file": (filename, b"file content", content_type)},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 201
    assert response.json()["filename"] == filename
    assert repository.documents[0].source_type == filename.rsplit(".", 1)[1]
    assert storage.uploads[0]["content_type"] == content_type

def test_delete_document_removes_minio_qdrant_and_database_rows() -> None:
    repository = FakeDocumentRepository()
    document = SimpleNamespace(
        id=DOCUMENT_ID,
        title="sample",
        status="indexed",
        uploaded_by_user_id=None,
        organization_id=None,
        visibility="organization",
        files=[
            SimpleNamespace(storage_path="documents/sample-1.pdf"),
            SimpleNamespace(storage_path="documents/sample-2.pdf"),
        ],
    )
    repository.documents.append(document)
    storage = FakeStorageClient()
    vector_store = FakeVectorStore()
    app.dependency_overrides[get_document_repository] = lambda: repository
    app.dependency_overrides[get_storage_client] = lambda: storage
    app.dependency_overrides[get_vector_store] = lambda: vector_store

    try:
        client = TestClient(app)
        response = client.delete(f"/api/documents/{DOCUMENT_ID}")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {
        "document_id": str(DOCUMENT_ID),
        "deleted": True,
        "deleted_files": 2,
        "vector_points_deleted": True,
    }
    assert vector_store.deleted_document_ids == [DOCUMENT_ID]
    assert storage.deleted == ["documents/sample-1.pdf", "documents/sample-2.pdf"]
    assert repository.documents == []
    assert repository.committed is True
    assert repository.rolled_back is False
