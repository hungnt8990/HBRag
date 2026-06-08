from app.services.parsers.base import DocumentParser, ParsedDocument


class TextParser(DocumentParser):
    supported_extensions = frozenset({".txt"})
    supported_mime_types = frozenset({"text/plain"})

    def parse(self, file_content: bytes) -> ParsedDocument:
        return ParsedDocument(text=_decode_text(file_content))


class MarkdownParser(DocumentParser):
    supported_extensions = frozenset({".md"})
    supported_mime_types = frozenset({"text/markdown", "text/x-markdown"})

    def parse(self, file_content: bytes) -> ParsedDocument:
        return ParsedDocument(text=_decode_text(file_content))


def _decode_text(file_content: bytes) -> str:
    try:
        return file_content.decode("utf-8-sig")
    except UnicodeDecodeError:
        return file_content.decode("latin-1")
