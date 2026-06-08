from io import BytesIO

from pypdf import PdfReader

from app.services.parsers.base import DocumentParser, ParsedDocument


class PdfParser(DocumentParser):
    supported_extensions = frozenset({".pdf"})
    supported_mime_types = frozenset({"application/pdf"})

    def parse(self, file_content: bytes) -> ParsedDocument:
        reader = PdfReader(BytesIO(file_content))
        pages = [page.extract_text() or "" for page in reader.pages]
        return ParsedDocument(text="\n\n".join(page for page in pages if page))
