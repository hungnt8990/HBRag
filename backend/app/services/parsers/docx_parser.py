from io import BytesIO

from docx import Document
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph

from app.services.parsers.base import DocumentParser, ParsedDocument
from app.services.parsers.table_serialization import maybe_table_title, serialize_table

BLOCK_SEPARATOR = "\n\n"


class DocxParser(DocumentParser):
    supported_extensions = frozenset({".docx"})
    supported_mime_types = frozenset(
        {"application/vnd.openxmlformats-officedocument.wordprocessingml.document"}
    )

    def parse(self, file_content: bytes) -> ParsedDocument:
        document = Document(BytesIO(file_content))
        blocks: list[str] = []
        previous_paragraph_text: str | None = None
        table_counter = 0

        # Preserve source order so paragraph context remains adjacent to tables.
        for child in document.element.body.iterchildren():
            if child.tag == qn("w:p"):
                paragraph = Paragraph(child, document)
                text = paragraph.text.strip()
                if text:
                    blocks.append(text)
                    previous_paragraph_text = text
            elif child.tag == qn("w:tbl"):
                table = Table(child, document)
                table_counter += 1
                rendered = self._render_table(
                    table,
                    table_id=f"docx_t{table_counter}",
                    title=maybe_table_title(previous_paragraph_text),
                )
                if rendered:
                    blocks.append(rendered)

        return ParsedDocument(text=BLOCK_SEPARATOR.join(blocks))

    @staticmethod
    def _render_table(
        table: Table,
        *,
        table_id: str,
        title: str | None,
    ) -> str:
        rows: list[list[str]] = []
        for row in table.rows:
            cells = [cell.text for cell in row.cells]
            if any(cell.strip() for cell in cells):
                rows.append(cells)
        return serialize_table(table_id=table_id, rows=rows, title=title)
