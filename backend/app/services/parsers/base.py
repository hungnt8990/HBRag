from dataclasses import dataclass, field
from typing import Any, Literal

ParsedElementType = Literal[
    "title",
    "heading",
    "paragraph",
    "list_item",
    "table",
    "table_row",
    "image",
    "figure",
    "page",
    "slide",
    "code",
    "unknown",
]


@dataclass(frozen=True)
class ParsedElement:
    element_type: ParsedElementType
    text: str
    page_number: int | None = None
    section_title: str | None = None
    heading_path: list[str] = field(default_factory=list)
    table_id: str | None = None
    row_index: int | None = None
    bbox: tuple[float, float, float, float] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ParsedDocument:
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)
    elements: list[ParsedElement] = field(default_factory=list)


class DocumentParser:
    supported_extensions: frozenset[str] = frozenset()
    supported_mime_types: frozenset[str] = frozenset()

    def parse(self, file_content: bytes) -> ParsedDocument:
        raise NotImplementedError


    def supports(self, *, filename: str, mime_type: str | None) -> bool:
        extension = self._extension_from_filename(filename)
        normalized_mime_type = (mime_type or "").lower()
        return (
            extension in self.supported_extensions
            or normalized_mime_type in self.supported_mime_types
        )

    @staticmethod
    def _extension_from_filename(filename: str) -> str:
        name = filename.lower()
        if "." not in name:
            return ""
        return f".{name.rsplit('.', 1)[1]}"
