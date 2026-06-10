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


def parsed_element_to_dict(element: ParsedElement) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "element_type": element.element_type,
        "text": element.text,
    }
    optional_values: dict[str, Any] = {
        "page_number": element.page_number,
        "section_title": element.section_title,
        "heading_path": element.heading_path,
        "table_id": element.table_id,
        "row_index": element.row_index,
        "bbox": list(element.bbox) if element.bbox is not None else None,
        "metadata": element.metadata,
    }
    for key, value in optional_values.items():
        if value is None or value == [] or value == {}:
            continue
        payload[key] = value
    return payload


def parsed_element_from_dict(payload: dict[str, Any]) -> ParsedElement:
    bbox = payload.get("bbox")
    return ParsedElement(
        element_type=payload.get("element_type", "unknown"),
        text=str(payload.get("text") or ""),
        page_number=payload.get("page_number"),
        section_title=payload.get("section_title"),
        heading_path=list(payload.get("heading_path") or []),
        table_id=payload.get("table_id"),
        row_index=payload.get("row_index"),
        bbox=tuple(bbox) if isinstance(bbox, list | tuple) and len(bbox) == 4 else None,
        metadata=dict(payload.get("metadata") or {}),
    )


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
