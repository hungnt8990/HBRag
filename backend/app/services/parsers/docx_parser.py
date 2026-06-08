from io import BytesIO

from docx import Document
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph

from app.services.parsers.base import DocumentParser, ParsedDocument

CELL_SEPARATOR = " | "
ROW_SEPARATOR = "\n"
BLOCK_SEPARATOR = "\n\n"


class DocxParser(DocumentParser):
    supported_extensions = frozenset({".docx"})
    supported_mime_types = frozenset(
        {"application/vnd.openxmlformats-officedocument.wordprocessingml.document"}
    )

    def parse(self, file_content: bytes) -> ParsedDocument:
        document = Document(BytesIO(file_content))
        blocks: list[str] = []

        # Iterate the document body in source order so that paragraphs and
        # tables are preserved interleaved (Vietnamese legal/admin documents
        # frequently put substantive content in tables).
        for child in document.element.body.iterchildren():
            if child.tag == qn("w:p"):
                paragraph = Paragraph(child, document)
                text = paragraph.text.strip()
                if text:
                    blocks.append(text)
            elif child.tag == qn("w:tbl"):
                table = Table(child, document)
                rendered = self._render_table(table)
                if rendered:
                    blocks.append(rendered)

        return ParsedDocument(text=BLOCK_SEPARATOR.join(blocks))

    @staticmethod
    def _render_table(table: Table) -> str:
        rows: list[str] = []
        for row in table.rows:
            # Keep each row on a single line so chunkers do not break between
            # a label and its adjacent value.
            cells = [cell.text.replace("\n", " ").strip() for cell in row.cells]
            cells = [cell for cell in cells if cell]
            if cells:
                rows.append(CELL_SEPARATOR.join(cells))
        return ROW_SEPARATOR.join(rows)
