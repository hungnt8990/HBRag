from dataclasses import dataclass


@dataclass(frozen=True)
class ParsedDocument:
    text: str


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
