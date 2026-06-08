from io import BytesIO

from pypdf import PdfReader

from app.services.parsers.base import DocumentParser, ParsedDocument
from app.services.parsers.table_serialization import rewrite_text_with_serialized_tables


class PdfParser(DocumentParser):
    supported_extensions = frozenset({".pdf"})
    supported_mime_types = frozenset({"application/pdf"})

    def parse(self, file_content: bytes) -> ParsedDocument:
        reader = PdfReader(BytesIO(file_content))
        pages: list[str] = []
        for page_index, page in enumerate(reader.pages, start=1):
            raw_text = (
                page.extract_text(extraction_mode="layout")
                or page.extract_text()
                or ""
            )
            if not raw_text.strip():
                continue
            pages.append(
                rewrite_text_with_serialized_tables(
                    text=raw_text,
                    page_number=page_index,
                    table_id_prefix=f"pdf_p{page_index}",
                )
            )
        return ParsedDocument(text="\n\n".join(page for page in pages if page.strip()))
