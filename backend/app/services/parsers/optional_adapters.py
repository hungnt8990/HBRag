from __future__ import annotations

from importlib.util import find_spec

from app.services.parsers.base import DocumentParser, ParsedDocument


class OptionalDependencyParser(DocumentParser):
    dependency_module: str = ""
    parser_name: str = "optional"

    def is_available(self) -> bool:
        return bool(self.dependency_module and find_spec(self.dependency_module) is not None)

    def is_implemented(self) -> bool:
        return False

    def parse(self, file_content: bytes) -> ParsedDocument:
        return ParsedDocument(
            text="",
            metadata={
                "parser": self.parser_name,
                "fallback_reason": "optional parser is not implemented",
            },
        )


class DoclingParser(OptionalDependencyParser):
    supported_extensions = frozenset({".pdf", ".pptx", ".docx"})
    supported_mime_types = frozenset({
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    })
    dependency_module = "docling"
    parser_name = "docling"


class UnstructuredParser(OptionalDependencyParser):
    supported_extensions = frozenset({".pdf", ".docx", ".pptx"})
    supported_mime_types = frozenset({
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    })
    dependency_module = "unstructured"
    parser_name = "unstructured"
