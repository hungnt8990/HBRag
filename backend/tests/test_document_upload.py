from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.api.routes.documents import get_document_repository, get_storage_client
from app.main import app

DOCUMENT_ID = UUID("11111111-1111-1111-1111-111111111111")


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
    ) -> SimpleNamespace:
        document = SimpleNamespace(
            id=DOCUMENT_ID,
            title=title,
            source_type=source_type,
            status=status,
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
