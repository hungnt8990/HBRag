from types import SimpleNamespace
from uuid import UUID

from fastapi.testclient import TestClient

from app.api.routes.documents import get_document_repository
from app.main import app
from app.repositories.documents import ChunkCreate
from app.services.chunking_service import RecursiveTextChunker

DOCUMENT_ID = UUID("33333333-3333-3333-3333-333333333333")


class FakeDocumentRepository:
    def __init__(
        self,
        *,
        status: str = "parsed",
        parsed_text: str | None = "Parsed text",
    ) -> None:
        self.document = SimpleNamespace(
            id=DOCUMENT_ID,
            status=status,
            parsed_text=parsed_text,
        )
        self.deleted_chunks = False
        self.created_chunks: list[ChunkCreate] = []
        self.committed = False
        self.rolled_back = False

    async def get_document(self, document_id: UUID) -> SimpleNamespace | None:
        if document_id != DOCUMENT_ID:
            return None
        return self.document

    async def delete_chunks_for_document(self, document_id: UUID) -> None:
        assert document_id == DOCUMENT_ID
        self.deleted_chunks = True

    async def create_chunks(
        self,
        *,
        document_id: UUID,
        chunks: list[ChunkCreate],
    ) -> list[SimpleNamespace]:
        assert document_id == DOCUMENT_ID
        self.created_chunks = list(chunks)
        return [
            SimpleNamespace(
                document_id=document_id,
                chunk_index=chunk.chunk_index,
                content=chunk.content,
                token_count=chunk.token_count,
                chunk_metadata=chunk.metadata,
            )
            for chunk in chunks
        ]

    async def update_document_status(
        self,
        document: SimpleNamespace,
        status: str,
    ) -> SimpleNamespace:
        document.status = status
        return document

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        self.rolled_back = True


def test_chunker_overlap_behavior() -> None:
    chunker = RecursiveTextChunker(chunk_size=10, chunk_overlap=3)

    chunks = chunker.chunk_text("abcdefghijklmnopqrstuvwxyz")

    assert chunks[0].content == "abcdefghij"
    assert chunks[0].start_char == 0
    assert chunks[0].end_char == 10
    assert chunks[1].content == "hijklmnopq"
    assert chunks[1].start_char == 7
    assert chunks[1].end_char == 17


def test_chunker_ignores_split_boundary_inside_overlap() -> None:
    chunker = RecursiveTextChunker(chunk_size=1000, chunk_overlap=150)
    text = f"{'a' * 900}\n\n{'b' * 1000}\n\n{'c' * 300}"

    chunks = chunker.chunk_text(text)

    assert chunks[0].end_char == 902
    assert chunks[1].start_char == 752
    assert chunks[1].end_char - chunks[1].start_char > 500
    assert chunks[1].content != "\n\n"

def test_chunker_avoids_early_split_boundary() -> None:
    chunker = RecursiveTextChunker(chunk_size=1000, chunk_overlap=150)
    text = f"{'a' * 560}\n\n{'b' * 700}"

    chunks = chunker.chunk_text(text)

    assert chunks[0].end_char == 1000
    assert chunks[1].start_char == 850


def test_chunk_endpoint_rejects_unparsed_document() -> None:
    repository = FakeDocumentRepository(status="uploaded", parsed_text="content")
    app.dependency_overrides[get_document_repository] = lambda: repository

    try:
        client = TestClient(app)
        response = client.post(f"/api/documents/{DOCUMENT_ID}/chunk")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 409
    assert repository.deleted_chunks is False
    assert repository.created_chunks == []
    assert repository.committed is False


def test_chunk_endpoint_creates_chunks_from_parsed_text() -> None:
    parsed_text = "a" * 1200
    repository = FakeDocumentRepository(status="parsed", parsed_text=parsed_text)
    app.dependency_overrides[get_document_repository] = lambda: repository

    try:
        client = TestClient(app)
        response = client.post(f"/api/documents/{DOCUMENT_ID}/chunk")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload["document_id"] == str(DOCUMENT_ID)
    assert payload["status"] == "chunked"
    assert payload["chunk_count"] == 2
    assert len(payload["preview"]) == 2
    assert repository.deleted_chunks is True
    assert repository.committed is True
    assert repository.document.status == "chunked"
    assert [chunk.chunk_index for chunk in repository.created_chunks] == [0, 1]
    assert repository.created_chunks[0].metadata == {
        "chunk_size": 1000,
        "chunk_overlap": 150,
        "chunk_mode": "recursive",
        "document_profile": "general",
        "start_char": 0,
        "end_char": 1000,
    }
    assert repository.created_chunks[1].metadata == {
        "chunk_size": 1000,
        "chunk_overlap": 150,
        "chunk_mode": "recursive",
        "document_profile": "general",
        "start_char": 850,
        "end_char": 1200,
    }


def test_rechunk_deletes_old_chunks() -> None:
    repository = FakeDocumentRepository(status="chunked", parsed_text="b" * 1100)
    app.dependency_overrides[get_document_repository] = lambda: repository

    try:
        client = TestClient(app)
        response = client.post(f"/api/documents/{DOCUMENT_ID}/chunk")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert repository.deleted_chunks is True
    assert len(repository.created_chunks) == 2
    assert repository.document.status == "chunked"


def test_chunk_endpoint_accepts_custom_chunk_size_and_overlap() -> None:
    repository = FakeDocumentRepository(status="parsed", parsed_text="a" * 1200)
    app.dependency_overrides[get_document_repository] = lambda: repository

    try:
        client = TestClient(app)
        response = client.post(
            f"/api/documents/{DOCUMENT_ID}/chunk",
            json={"chunk_size": 500, "chunk_overlap": 100},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert repository.created_chunks[0].metadata["chunk_size"] == 500
    assert repository.created_chunks[0].metadata["chunk_overlap"] == 100
    assert repository.created_chunks[0].metadata["start_char"] == 0
    assert repository.created_chunks[0].metadata["end_char"] == 500


def test_chunk_endpoint_rejects_invalid_overlap() -> None:
    repository = FakeDocumentRepository(status="parsed", parsed_text="a" * 1200)
    app.dependency_overrides[get_document_repository] = lambda: repository

    try:
        client = TestClient(app)
        response = client.post(
            f"/api/documents/{DOCUMENT_ID}/chunk",
            json={"chunk_size": 500, "chunk_overlap": 400},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422
    assert repository.created_chunks == []
    assert repository.committed is False


def test_chunk_endpoint_rejects_chunk_size_out_of_range() -> None:
    repository = FakeDocumentRepository(status="parsed", parsed_text="a" * 1200)
    app.dependency_overrides[get_document_repository] = lambda: repository

    try:
        client = TestClient(app)
        response = client.post(
            f"/api/documents/{DOCUMENT_ID}/chunk",
            json={"chunk_size": 100},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422
    assert repository.created_chunks == []


def test_legal_article_short_article_kept_as_one_chunk() -> None:
    text = (
        "CHƯƠNG I QUY ĐỊNH CHUNG\n"
        "Điều 1. Phạm vi điều chỉnh\n"
        "Văn bản này quy định về quyền và nghĩa vụ của các bên.\n\n"
        "Điều 10. Nghỉ kết hôn\n"
        "Khi kết hôn, người lao động được nghỉ 03 ngày có hưởng lương.\n"
    )
    repository = FakeDocumentRepository(status="parsed", parsed_text=text)
    app.dependency_overrides[get_document_repository] = lambda: repository

    try:
        client = TestClient(app)
        response = client.post(
            f"/api/documents/{DOCUMENT_ID}/chunk",
            json={"chunk_size": 1000, "chunk_overlap": 0, "chunk_mode": "legal_article"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert len(repository.created_chunks) >= 2

    article_10 = next(
        (chunk for chunk in repository.created_chunks if "Điều 10" in chunk.content),
        None,
    )
    assert article_10 is not None
    assert article_10.metadata["chunk_mode"] == "legal_article"
    assert article_10.metadata["article_number"] == "10"
    assert article_10.metadata["article_title"] == "Nghỉ kết hôn"
    assert article_10.metadata["chapter_title"] == "CHƯƠNG I QUY ĐỊNH CHUNG"
    # Whole article fits, must remain a single semantic chunk.
    assert "03 ngày có hưởng lương" in article_10.content


def test_legal_article_long_article_is_split_with_metadata() -> None:
    body_lines = "\n".join(f"- Điểm {index}: nội dung dài dòng." for index in range(1, 60))
    text = (
        "CHƯƠNG II QUY ĐỊNH CỤ THỂ\n"
        "Điều 5. Quy định mở rộng\n"
        f"{body_lines}\n"
    )
    repository = FakeDocumentRepository(status="parsed", parsed_text=text)
    app.dependency_overrides[get_document_repository] = lambda: repository

    try:
        client = TestClient(app)
        response = client.post(
            f"/api/documents/{DOCUMENT_ID}/chunk",
            json={"chunk_size": 400, "chunk_overlap": 60, "chunk_mode": "legal_article"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    article_chunks = [
        chunk for chunk in repository.created_chunks
        if chunk.metadata.get("article_number") == "5"
    ]
    assert len(article_chunks) >= 2
    subchunk_indices = [chunk.metadata.get("subchunk_index") for chunk in article_chunks]
    assert subchunk_indices == list(range(len(article_chunks)))
    for chunk in article_chunks:
        assert chunk.metadata["chunk_mode"] == "legal_article"
        assert chunk.metadata["article_title"] == "Quy định mở rộng"
        assert chunk.metadata["chapter_title"].startswith("CHƯƠNG II")


def test_recursive_mode_metadata_records_chunk_mode() -> None:
    repository = FakeDocumentRepository(status="parsed", parsed_text="a" * 1200)
    app.dependency_overrides[get_document_repository] = lambda: repository

    try:
        client = TestClient(app)
        response = client.post(
            f"/api/documents/{DOCUMENT_ID}/chunk",
            json={"chunk_size": 1000, "chunk_overlap": 150, "chunk_mode": "recursive"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    for chunk in repository.created_chunks:
        assert chunk.metadata["chunk_mode"] == "recursive"
        assert "article_number" not in chunk.metadata
