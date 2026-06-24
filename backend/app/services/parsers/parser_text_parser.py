import re

from app.services.parsers.parser_base import DocumentParser, ParsedDocument, ParsedElement

HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$")
TEXT_HEADING_RE = re.compile(
    r"^(CHÆ¯Æ NG\s+\S+|Äiá»u\s+\d+\b|[IVXLCDM]+\.|[A-Z0-9][A-Z0-9\s\-/]{3,80})"
)


class TextParser(DocumentParser):
    supported_extensions = frozenset({".txt"})
    supported_mime_types = frozenset({"text/plain"})

    def parse(self, file_content: bytes) -> ParsedDocument:
        text = _decode_text(file_content)
        return ParsedDocument(
            text=text,
            metadata={"parser": "builtin_text"},
            elements=_parse_plain_text_elements(text),
        )


class MarkdownParser(DocumentParser):
    supported_extensions = frozenset({".md"})
    supported_mime_types = frozenset({"text/markdown", "text/x-markdown"})

    def parse(self, file_content: bytes) -> ParsedDocument:
        text = _decode_text(file_content)
        return ParsedDocument(
            text=text,
            metadata={"parser": "builtin_markdown"},
            elements=_parse_markdown_elements(text),
        )


def _decode_text(file_content: bytes) -> str:
    try:
        return file_content.decode("utf-8-sig")
    except UnicodeDecodeError:
        return file_content.decode("latin-1")


def _parse_markdown_elements(text: str) -> list[ParsedElement]:
    elements: list[ParsedElement] = []
    heading_path: list[str] = []
    paragraph_lines: list[str] = []
    in_code_block = False
    code_lines: list[str] = []

    def flush_paragraph() -> None:
        nonlocal paragraph_lines
        paragraph = "\n".join(paragraph_lines).strip()
        if paragraph:
            elements.append(
                ParsedElement(
                    element_type="paragraph",
                    text=paragraph,
                    section_title=heading_path[-1] if heading_path else None,
                    heading_path=list(heading_path),
                )
            )
        paragraph_lines = []

    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            if in_code_block:
                elements.append(
                    ParsedElement(
                        element_type="code",
                        text="\n".join(code_lines),
                        section_title=heading_path[-1] if heading_path else None,
                        heading_path=list(heading_path),
                    )
                )
                code_lines = []
            else:
                flush_paragraph()
            in_code_block = not in_code_block
            continue
        if in_code_block:
            code_lines.append(line)
            continue
        heading_match = HEADING_RE.match(stripped)
        if heading_match:
            flush_paragraph()
            level = len(heading_match.group(1))
            heading = heading_match.group(2).strip()
            heading_path = heading_path[: level - 1] + [heading]
            elements.append(
                ParsedElement(
                    element_type="heading",
                    text=heading,
                    section_title=heading,
                    heading_path=list(heading_path),
                    metadata={"level": level},
                )
            )
            continue
        if stripped.startswith(("- ", "* ", "+ ")):
            flush_paragraph()
            elements.append(
                ParsedElement(
                    element_type="list_item",
                    text=stripped[2:].strip(),
                    section_title=heading_path[-1] if heading_path else None,
                    heading_path=list(heading_path),
                )
            )
            continue
        if stripped:
            paragraph_lines.append(line)
        else:
            flush_paragraph()
    flush_paragraph()
    return elements


def _parse_plain_text_elements(text: str) -> list[ParsedElement]:
    elements: list[ParsedElement] = []
    heading_path: list[str] = []
    paragraph_lines: list[str] = []

    def flush_paragraph() -> None:
        nonlocal paragraph_lines
        paragraph = "\n".join(paragraph_lines).strip()
        if paragraph:
            elements.append(
                ParsedElement(
                    element_type="paragraph",
                    text=paragraph,
                    section_title=heading_path[-1] if heading_path else None,
                    heading_path=list(heading_path),
                )
            )
        paragraph_lines = []

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            flush_paragraph()
            continue
        if _looks_like_text_heading(stripped):
            flush_paragraph()
            heading_path = [stripped]
            elements.append(
                ParsedElement(
                    element_type="heading",
                    text=stripped,
                    section_title=stripped,
                    heading_path=list(heading_path),
                )
            )
        else:
            paragraph_lines.append(line)
    flush_paragraph()
    return elements


def _looks_like_text_heading(line: str) -> bool:
    if len(line) > 100 or line.endswith((".", ",", ";")):
        return False
    return bool(TEXT_HEADING_RE.match(line))
