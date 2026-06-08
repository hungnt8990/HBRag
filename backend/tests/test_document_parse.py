from datetime import datetime
from types import SimpleNamespace
from uuid import UUID

from fastapi.testclient import TestClient

from app.api.routes.documents import get_document_repository, get_storage_client
from app.main import app
from app.services.document_parser_service import DocumentParserService
from app.services.parsers import MarkdownParser, PdfParser, TextParser
from app.services.parsers.pdf_parser import PdfParser as PdfParserImpl

DOCUMENT_ID = UUID("22222222-2222-2222-2222-222222222222")


class FakeDocumentRepository:
    def __init__(
        self,
        *,
        filename: str = "sample.txt",
        mime_type: str = "text/plain",
        storage_path: str = "documents/sample.txt",
        document_status: str = "uploaded",
    ) -> None:
        self.document = SimpleNamespace(id=DOCUMENT_ID, status=document_status)
        self.document_file = SimpleNamespace(
            filename=filename,
            mime_type=mime_type,
            storage_path=storage_path,
        )
        self.committed = False
        self.rolled_back = False

    async def get_document(self, document_id: UUID) -> SimpleNamespace | None:
        if document_id != DOCUMENT_ID:
            return None
        return self.document

    async def get_primary_document_file(self, document_id: UUID) -> SimpleNamespace | None:
        if document_id != DOCUMENT_ID:
            return None
        return self.document_file

    async def update_document_status(
        self,
        document: SimpleNamespace,
        status: str,
    ) -> SimpleNamespace:
        document.status = status
        return document

    async def update_document_parsed_content(
        self,
        document: SimpleNamespace,
        *,
        parsed_text: str,
        parsed_at: datetime,
        status: str = "parsed",
    ) -> SimpleNamespace:
        document.parsed_text = parsed_text
        document.parsed_at = parsed_at
        document.status = status
        return document

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        self.rolled_back = True


class FakeStorageClient:
    def __init__(self, files: dict[str, bytes]) -> None:
        self.files = files
        self.downloads: list[str] = []

    async def put_file(self, **_: object) -> str:
        raise AssertionError("Upload should not be called by parse tests.")

    async def get_file(self, *, object_name: str) -> bytes:
        self.downloads.append(object_name)
        return self.files[object_name]

    async def delete_file(self, **_: object) -> None:
        raise AssertionError("Delete should not be called by parse tests.")


def test_parse_txt_document() -> None:
    repository = FakeDocumentRepository(
        filename="sample.txt",
        mime_type="text/plain",
        storage_path="documents/sample.txt",
    )
    storage = FakeStorageClient({"documents/sample.txt": b"Hello parser\nSecond line"})
    app.dependency_overrides[get_document_repository] = lambda: repository
    app.dependency_overrides[get_storage_client] = lambda: storage

    try:
        client = TestClient(app)
        response = client.post(f"/api/documents/{DOCUMENT_ID}/parse")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload["document_id"] == str(DOCUMENT_ID)
    assert payload["status"] == "parsed"
    assert payload["character_count"] == len("Hello parser\nSecond line")
    assert payload["preview"] == "Hello parser\nSecond line"
    assert repository.document.status == "parsed"
    assert repository.document.parsed_text == "Hello parser\nSecond line"
    assert isinstance(repository.document.parsed_at, datetime)
    assert repository.committed is True
    assert repository.rolled_back is False
    assert storage.downloads == ["documents/sample.txt"]


def test_parse_md_document() -> None:
    repository = FakeDocumentRepository(
        filename="notes.md",
        mime_type="text/markdown",
        storage_path="documents/notes.md",
    )
    storage = FakeStorageClient({"documents/notes.md": b"# Heading\n\nSome notes"})
    app.dependency_overrides[get_document_repository] = lambda: repository
    app.dependency_overrides[get_storage_client] = lambda: storage

    try:
        client = TestClient(app)
        response = client.post(f"/api/documents/{DOCUMENT_ID}/parse")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "parsed"
    assert payload["character_count"] == len("# Heading\n\nSome notes")
    assert payload["preview"] == "# Heading\n\nSome notes"
    assert repository.committed is True


def test_parse_rejects_unsupported_document_file() -> None:
    repository = FakeDocumentRepository(
        filename="spreadsheet.xlsx",
        mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        storage_path="documents/spreadsheet.xlsx",
    )
    storage = FakeStorageClient({"documents/spreadsheet.xlsx": b"not parsed"})
    app.dependency_overrides[get_document_repository] = lambda: repository
    app.dependency_overrides[get_storage_client] = lambda: storage

    try:
        client = TestClient(app)
        response = client.post(f"/api/documents/{DOCUMENT_ID}/parse")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 415
    assert repository.document.status == "uploaded"
    assert repository.committed is False
    assert repository.rolled_back is False
    assert storage.downloads == []


def test_parser_selection_uses_extension_or_mime_type() -> None:
    service = DocumentParserService(
        repository=FakeDocumentRepository(),
        storage=FakeStorageClient({}),
    )

    assert isinstance(
        service.select_parser(filename="sample.txt", mime_type="application/octet-stream"),
        TextParser,
    )
    assert isinstance(
        service.select_parser(filename="README", mime_type="text/markdown"),
        MarkdownParser,
    )
    assert isinstance(
        service.select_parser(filename="report.pdf", mime_type=None),
        PdfParser,
    )


def _build_docx_with_tables(target_chars: int) -> bytes:
    from io import BytesIO

    from docx import Document

    doc = Document()
    doc.add_paragraph("CHUONG I QUY DINH CHUNG")
    doc.add_paragraph("Dieu 1. Pham vi dieu chinh")
    doc.add_paragraph("Van ban nay quy dinh noi dung mo dau.")

    table = doc.add_table(rows=0, cols=2)
    cell_text = "Noi dung dong bang dai hon de dat so ky tu can thiet. " * 4
    while True:
        row = table.add_row().cells
        row[0].text = "Tieu chi"
        row[1].text = cell_text
        accumulated = sum(len(paragraph.text) for paragraph in doc.paragraphs)
        for current_table in doc.tables:
            for current_row in current_table.rows:
                for cell in current_row.cells:
                    accumulated += len(cell.text)
        if accumulated >= target_chars:
            break

    buffer = BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


def test_parse_docx_extracts_tables_and_does_not_truncate() -> None:
    docx_bytes = _build_docx_with_tables(target_chars=4500)
    repository = FakeDocumentRepository(
        filename="legal.docx",
        mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        storage_path="documents/legal.docx",
    )
    storage = FakeStorageClient({"documents/legal.docx": docx_bytes})
    app.dependency_overrides[get_document_repository] = lambda: repository
    app.dependency_overrides[get_storage_client] = lambda: storage

    try:
        client = TestClient(app)
        response = client.post(f"/api/documents/{DOCUMENT_ID}/parse")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload["character_count"] > 4000
    assert len(payload["preview"]) <= 500
    assert repository.document.parsed_text is not None
    assert len(repository.document.parsed_text) == payload["character_count"]
    assert "TABLE_ROW table_id=docx_t1" in repository.document.parsed_text
    assert "Tieu chi" in repository.document.parsed_text


def test_parse_docx_keeps_empty_cells_with_generic_headers() -> None:
    from io import BytesIO

    from docx import Document

    doc = Document()
    table = doc.add_table(rows=2, cols=3)
    table.cell(0, 0).text = "Alice"
    table.cell(0, 1).text = ""
    table.cell(0, 2).text = "Platform"
    table.cell(1, 0).text = "Bob"
    table.cell(1, 1).text = "QA"
    table.cell(1, 2).text = ""

    buffer = BytesIO()
    doc.save(buffer)

    repository = FakeDocumentRepository(
        filename="matrix.docx",
        mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        storage_path="documents/matrix.docx",
    )
    storage = FakeStorageClient({"documents/matrix.docx": buffer.getvalue()})
    app.dependency_overrides[get_document_repository] = lambda: repository
    app.dependency_overrides[get_storage_client] = lambda: storage

    try:
        client = TestClient(app)
        response = client.post(f"/api/documents/{DOCUMENT_ID}/parse")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert "cell_1: Alice" in repository.document.parsed_text
    assert "cell_2:" in repository.document.parsed_text
    assert "cell_3: Platform" in repository.document.parsed_text


def test_parse_pdf_serializes_detected_tables(monkeypatch) -> None:
    class FakePage:
        def extract_text(self, extraction_mode=None):
            return (
                "Name    Area\n"
                "Nguyen Quang Lam    Infrastructure\n"
                "Tran Van An    QA\n"
            )

    class FakeReader:
        def __init__(self, _stream):
            self.pages = [FakePage()]

    monkeypatch.setattr("app.services.parsers.pdf_parser.PdfReader", FakeReader)

    parsed = PdfParserImpl().parse(b"%PDF-1.4 fake")

    assert "TABLE_ROW table_id=pdf_p1_1 page=1 row=1" in parsed.text
    assert "Name: Nguyen Quang Lam" in parsed.text
    assert "Area: Infrastructure" in parsed.text
