from io import BytesIO

from docx import Document
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph

from app.services.parsers.parser_base import DocumentParser, ParsedDocument, ParsedElement
from app.services.parsers.parser_table_serialization import (
    infer_headers,
    maybe_table_title,
    serialize_table,
)
from app.services.chunkers.chunker_table_relationships import parse_technology_area_rows_from_table

BLOCK_SEPARATOR = "\n\n"


class DocxParser(DocumentParser):
    supported_extensions = frozenset({".docx"})
    supported_mime_types = frozenset(
        {"application/vnd.openxmlformats-officedocument.wordprocessingml.document"}
    )

    def parse(self, file_content: bytes) -> ParsedDocument:
        document = Document(BytesIO(file_content))
        blocks: list[str] = []
        elements: list[ParsedElement] = []
        heading_path: list[str] = []
        previous_paragraph_text: str | None = None
        table_counter = 0

        # Preserve source order so paragraph context remains adjacent to tables.
        for child in document.element.body.iterchildren():
            if child.tag == qn("w:p"):
                paragraph = Paragraph(child, document)
                text = paragraph.text.strip()
                if text:
                    blocks.append(text)
                    style_name = paragraph.style.name if paragraph.style else ""
                    heading_level = _heading_level(style_name)
                    if heading_level is not None:
                        heading_path = heading_path[: heading_level - 1] + [text]
                        elements.append(
                            ParsedElement(
                                element_type="title" if heading_level == 0 else "heading",
                                text=text,
                                section_title=text,
                                heading_path=list(heading_path),
                                metadata={"style": style_name, "level": heading_level},
                            )
                        )
                    else:
                        elements.append(
                            ParsedElement(
                                element_type="paragraph",
                                text=text,
                                section_title=heading_path[-1] if heading_path else None,
                                heading_path=list(heading_path),
                                metadata={"style": style_name},
                            )
                        )
                    previous_paragraph_text = text
            elif child.tag == qn("w:tbl"):
                table = Table(child, document)
                table_counter += 1
                table_id = f"docx_t{table_counter}"
                rendered = self._render_table(
                    table,
                    table_id=table_id,
                    title=maybe_table_title(previous_paragraph_text),
                )
                if rendered:
                    blocks.append(rendered)
                elements.extend(
                    _table_elements(
                        table,
                        table_id=table_id,
                        section_title=heading_path[-1] if heading_path else None,
                        heading_path=heading_path,
                    )
                )

        return ParsedDocument(
            text=BLOCK_SEPARATOR.join(blocks),
            metadata={"parser": "builtin_docx"},
            elements=elements,
        )

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


def _heading_level(style_name: str) -> int | None:
    normalized = style_name.lower().strip()
    if normalized == "title":
        return 0
    if normalized.startswith("heading "):
        suffix = normalized.rsplit(" ", 1)[-1]
        if suffix.isdigit():
            return max(1, int(suffix))
    return None


def _table_elements(
    table: Table,
    *,
    table_id: str,
    section_title: str | None,
    heading_path: list[str],
) -> list[ParsedElement]:
    rows = [
        [cell.text.strip() for cell in row.cells]
        for row in table.rows
        if any(cell.text.strip() for cell in row.cells)
    ]
    if not rows:
        return []
    relationship_rows = parse_technology_area_rows_from_table(
        rows,
        table_id=table_id,
    )
    if relationship_rows:
        table_text = "\n\n".join(row.to_text() for row in relationship_rows)
        elements = [
            ParsedElement(
                element_type="table",
                text=table_text,
                section_title=section_title,
                heading_path=list(heading_path),
                table_id=table_id,
                metadata={
                    "source_table": relationship_rows[0].source_table,
                    "relationship_type": "technology_area_staff",
                },
            )
        ]
        for fallback_index, row in enumerate(relationship_rows, start=1):
            elements.append(
                ParsedElement(
                    element_type="table_row",
                    text=row.to_text(),
                    section_title=section_title,
                    heading_path=list(heading_path),
                    table_id=table_id,
                    row_index=int(row.stt) if row.stt.isdigit() else fallback_index,
                    metadata=row.to_metadata(),
                )
            )
        return elements

    headers, data_rows, has_header = infer_headers(rows)
    elements = [
        ParsedElement(
            element_type="table",
            text=serialize_table(table_id=table_id, rows=rows),
            section_title=section_title,
            heading_path=list(heading_path),
            table_id=table_id,
            metadata={"headers": headers if has_header else []},
        )
    ]
    for row_index, values in enumerate(data_rows, start=1):
        row_text = " | ".join(
            f"{header}: {value}" for header, value in zip(headers, values, strict=False)
        )
        elements.append(
            ParsedElement(
                element_type="table_row",
                text=row_text,
                section_title=section_title,
                heading_path=list(heading_path),
                table_id=table_id,
                row_index=row_index,
                metadata={"headers": headers, "values": values},
            )
        )
    return elements
