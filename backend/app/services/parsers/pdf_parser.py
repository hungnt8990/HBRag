from io import BytesIO
from typing import Any

from pypdf import PdfReader

from app.services.parsers.base import DocumentParser, ParsedDocument
from app.services.parsers.table_serialization import (
    rewrite_text_with_serialized_tables,
    serialize_table,
)

PDFPLUMBER_TABLE_SETTINGS = (
    {
        "vertical_strategy": "lines",
        "horizontal_strategy": "lines",
        "snap_tolerance": 3,
        "join_tolerance": 3,
        "intersection_tolerance": 5,
    },
    {
        "vertical_strategy": "text",
        "horizontal_strategy": "text",
        "snap_tolerance": 3,
        "join_tolerance": 3,
        "intersection_tolerance": 5,
    },
)


class PdfParser(DocumentParser):
    supported_extensions = frozenset({".pdf"})
    supported_mime_types = frozenset({"application/pdf"})

    def parse(self, file_content: bytes) -> ParsedDocument:
        pdfplumber_text = self._parse_with_pdfplumber(file_content)
        if pdfplumber_text is not None:
            return ParsedDocument(text=pdfplumber_text)
        return ParsedDocument(text=self._parse_with_pypdf(file_content))

    def _parse_with_pdfplumber(self, file_content: bytes) -> str | None:
        try:
            import pdfplumber
        except ImportError:
            return None

        try:
            with pdfplumber.open(BytesIO(file_content)) as pdf:
                pages: list[str] = []
                extracted_table = False
                for page_index, page in enumerate(pdf.pages, start=1):
                    page_parts: list[str] = []
                    page_text = (page.extract_text() or "").strip()
                    if page_text:
                        page_parts.append(page_text)

                    for table_index, table in enumerate(
                        self._extract_pdfplumber_tables(page),
                        start=1,
                    ):
                        serialized = serialize_table(
                            table_id=f"pdf_p{page_index}_{table_index}",
                            rows=table,
                            page_number=page_index,
                        )
                        if serialized:
                            extracted_table = True
                            page_parts.append(serialized)

                    if page_parts:
                        pages.append("\n\n".join(page_parts))
        except Exception:
            return None

        if not extracted_table:
            return None
        return "\n\n".join(page for page in pages if page.strip())

    def _parse_with_pypdf(self, file_content: bytes) -> str:
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
        return "\n\n".join(page for page in pages if page.strip())

    @staticmethod
    def _extract_pdfplumber_tables(page: Any) -> list[list[list[str]]]:
        for table_settings in PDFPLUMBER_TABLE_SETTINGS:
            raw_tables = page.extract_tables(table_settings=table_settings) or []
            tables = [
                PdfParser._normalize_pdfplumber_table(table)
                for table in raw_tables
            ]
            tables = [table for table in tables if table]
            if tables:
                return tables
        return []

    @staticmethod
    def _normalize_pdfplumber_table(table: list[list[Any]]) -> list[list[str]]:
        normalized_rows: list[list[str]] = []
        for row in table:
            normalized_rows.append(
                ["" if cell is None else str(cell) for cell in row]
            )
        return normalized_rows
