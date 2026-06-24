from __future__ import annotations

import html
import re
from dataclasses import dataclass
from html.parser import HTMLParser

PAGE_MARKER_PATTERN = re.compile(r"(?im)^\s*---\s*Page\s+(?P<page>\d+)\s*---\s*$")
TABLE_PATTERN = re.compile(r"(?is)<table\b.*?</table>")
BLOCK_TAGS = {
    "address",
    "article",
    "aside",
    "blockquote",
    "div",
    "figcaption",
    "figure",
    "footer",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "header",
    "li",
    "main",
    "p",
    "section",
    "tr",
}


@dataclass(frozen=True)
class DofficePage:
    page_number: int
    raw_text: str
    clean_text: str


class _TextHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        tag_name = tag.lower()
        if tag_name in {"script", "style"}:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if tag_name == "br" or tag_name in BLOCK_TAGS:
            self._append_newline()

    def handle_endtag(self, tag: str) -> None:
        tag_name = tag.lower()
        if tag_name in {"script", "style"} and self._skip_depth:
            self._skip_depth -= 1
            return
        if self._skip_depth:
            return
        if tag_name in BLOCK_TAGS:
            self._append_newline()

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if data:
            self._parts.append(data)

    def get_text(self) -> str:
        return "".join(self._parts)

    def _append_newline(self) -> None:
        if self._parts and not self._parts[-1].endswith("\n"):
            self._parts.append("\n")


class _TableHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self._current_row: list[str] | None = None
        self._current_cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs) -> None:
        tag_name = tag.lower()
        if tag_name == "tr":
            self._finish_row()
            self._current_row = []
        elif tag_name in {"td", "th"}:
            self._finish_cell()
            self._current_cell = []
        elif tag_name == "br" and self._current_cell is not None:
            self._current_cell.append(" ")

    def handle_endtag(self, tag: str) -> None:
        tag_name = tag.lower()
        if tag_name in {"td", "th"}:
            self._finish_cell()
        elif tag_name == "tr":
            self._finish_row()

    def handle_data(self, data: str) -> None:
        if self._current_cell is not None:
            self._current_cell.append(data)

    def close(self) -> None:
        self._finish_cell()
        self._finish_row()
        super().close()

    def _finish_cell(self) -> None:
        if self._current_cell is None:
            return
        text = _normalize_inline_text("".join(self._current_cell))
        if self._current_row is None:
            self._current_row = []
        self._current_row.append(text)
        self._current_cell = None

    def _finish_row(self) -> None:
        if self._current_row is None:
            return
        row = [cell for cell in self._current_row if cell]
        if row:
            self.rows.append(row)
        self._current_row = None


def clean_doffice_markdown_to_text(raw: str) -> str:
    text = str(raw or "")
    if not text.strip():
        return ""

    text = html.unescape(text)
    text = re.sub(r"(?i)\[\s*image\s*]", " ", text)
    text = PAGE_MARKER_PATTERN.sub("\n", text)
    text = TABLE_PATTERN.sub(lambda match: "\n" + _render_html_table(match.group(0)) + "\n", text)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = _strip_html(text)
    text = html.unescape(text)
    text = re.sub(r"(?m)^\s{0,3}#{1,6}\s+", "", text)
    text = re.sub(r"(?m)^(\s*)[-*+]\s+[-*+]\s+", r"\1- ", text)
    text = re.sub(r"(?m)^\s*[-*_]{3,}\s*$", "", text)
    return _normalize_lines(text)


def split_doffice_pages(raw: str) -> list[DofficePage]:
    text = str(raw or "")
    matches = list(PAGE_MARKER_PATTERN.finditer(text))
    if not matches:
        clean_text = clean_doffice_markdown_to_text(text)
        return [DofficePage(page_number=1, raw_text=text, clean_text=clean_text)] if clean_text else []

    pages: list[DofficePage] = []
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        page_number = int(match.group("page"))
        raw_page = text[start:end]
        clean_page = clean_doffice_markdown_to_text(raw_page)
        if clean_page:
            pages.append(
                DofficePage(
                    page_number=page_number,
                    raw_text=raw_page,
                    clean_text=clean_page,
                )
            )
    return pages


def _render_html_table(table_html: str) -> str:
    parser = _TableHTMLParser()
    parser.feed(table_html)
    parser.close()
    rows = parser.rows
    if not rows:
        return ""

    header = rows[0]
    data_rows = rows[1:] if len(rows) > 1 else rows
    rendered: list[str] = []
    if len(rows) > 1:
        rendered.append(" | ".join(header))
    for row in data_rows:
        if len(rows) > 1 and len(header) == len(row):
            rendered.append(" | ".join(f"{header[index]}: {cell}" for index, cell in enumerate(row)))
        else:
            rendered.append(" | ".join(row))
    return "\n".join(line for line in rendered if line.strip())


def _strip_html(value: str) -> str:
    parser = _TextHTMLParser()
    parser.feed(value)
    parser.close()
    return parser.get_text()


def _normalize_inline_text(value: str) -> str:
    return " ".join(html.unescape(value or "").split()).strip()


def _normalize_lines(value: str) -> str:
    lines = []
    for raw_line in value.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = re.sub(r"[\t ]+", " ", raw_line).strip()
        lines.append(line)

    compact: list[str] = []
    blank_seen = False
    for line in lines:
        if not line:
            if not blank_seen and compact:
                compact.append("")
            blank_seen = True
            continue
        compact.append(line)
        blank_seen = False
    return "\n".join(compact).strip()
