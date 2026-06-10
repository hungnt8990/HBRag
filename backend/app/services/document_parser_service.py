from __future__ import annotations

import logging
from datetime import UTC, datetime
from uuid import UUID

import anyio

from app.core.config import settings
from app.repositories.documents import DocumentRepository
from app.schemas.documents import DocumentParseResponse
from app.services.parsers import (
    DoclingParser,
    DocumentParser,
    DocxParser,
    MarkdownParser,
    PdfParser,
    TextParser,
    UnstructuredParser,
    parsed_element_to_dict,
)
from app.services.storage import StorageClient

logger = logging.getLogger(__name__)

DEFAULT_PARSERS: tuple[DocumentParser, ...] = (
    TextParser(),
    MarkdownParser(),
    PdfParser(),
    DocxParser(),
)
PREVIEW_LIMIT = 500


class DocumentNotFoundError(LookupError):
    pass


class DocumentFileNotFoundError(LookupError):
    pass


class DocumentParseStatusError(ValueError):
    pass


class UnsupportedDocumentParserError(ValueError):
    pass


class DocumentParsingError(RuntimeError):
    pass


class DocumentParserService:
    def __init__(
        self,
        *,
        repository: DocumentRepository,
        storage: StorageClient,
        parsers: tuple[DocumentParser, ...] | None = None,
    ) -> None:
        self._repository = repository
        self._storage = storage
        self._parsers = parsers or build_default_parsers()

    async def parse_document(self, document_id: UUID) -> DocumentParseResponse:
        document = await self._repository.get_document(document_id)
        if document is None:
            raise DocumentNotFoundError("Document not found.")
        if document.status != "uploaded":
            raise DocumentParseStatusError("Only uploaded documents can be parsed.")

        document_file = await self._repository.get_primary_document_file(document_id)
        if document_file is None:
            raise DocumentFileNotFoundError("Document file metadata not found.")

        parser = self.select_parser(
            filename=document_file.filename,
            mime_type=document_file.mime_type,
        )

        try:
            file_content = await self._storage.get_file(object_name=document_file.storage_path)
            parsed = await anyio.to_thread.run_sync(lambda: parser.parse(file_content))
            parsed_text = _sanitize_parsed_text(parsed.text)
            logger.info(
                "parsed document=%s parser=%s file_bytes=%d parsed_chars=%d",
                document.id,
                type(parser).__name__,
                len(file_content),
                len(parsed_text),
            )
            await self._repository.update_document_parsed_content(
                document,
                parsed_text=parsed_text,
                parsed_at=datetime.now(UTC),
                status="parsed",
            )
            await self._update_structured_parse_metadata(
                document=document,
                parser=parser,
                parsed_metadata=parsed.metadata,
                parsed_elements=parsed.elements,
            )
            await self._repository.commit()
        except Exception as exc:
            await self._repository.rollback()
            raise DocumentParsingError("Failed to parse document.") from exc

        text = parsed_text
        logger.info(
            "parse-response document=%s stored_chars=%d preview_chars=%d",
            document.id,
            len(text),
            min(len(text), PREVIEW_LIMIT),
        )
        return DocumentParseResponse(
            document_id=document.id,
            status=document.status,
            character_count=len(text),
            preview=text[:PREVIEW_LIMIT],
        )

    def select_parser(self, *, filename: str, mime_type: str | None) -> DocumentParser:
        for parser in self._parsers:
            if parser.supports(filename=filename, mime_type=mime_type):
                return parser

        raise UnsupportedDocumentParserError("No parser available for this document type.")

    async def _update_structured_parse_metadata(
        self,
        *,
        document,
        parser: DocumentParser,
        parsed_metadata: dict,
        parsed_elements: list,
    ) -> None:
        updater = getattr(self._repository, "update_document_metadata", None)
        if updater is None:
            return

        parser_name = _parser_storage_name(parser)
        await updater(
            document,
            {
                "parser": parser_name,
                "parsed_metadata": parsed_metadata,
                "parsed_elements": [
                    parsed_element_to_dict(element) for element in parsed_elements
                ],
            },
        )

def _sanitize_parsed_text(text: str) -> str:
    return text.replace("\x00", "")

def _parser_storage_name(parser: DocumentParser) -> str:
    name = type(parser).__name__
    if name in {"PdfParser", "DocxParser", "TextParser", "MarkdownParser"}:
        return f"builtin_{name.removesuffix('Parser').lower()}"
    return name.removesuffix("Parser").lower()

def build_default_parsers() -> tuple[DocumentParser, ...]:
    provider = settings.document_parser_provider.lower().strip()
    parsers: list[DocumentParser] = []

    if provider in {"auto", "docling"} and settings.enable_docling:
        parser = DoclingParser()
        if parser.is_available() and parser.is_implemented():
            parsers.append(parser)
        else:
            logger.warning(
                "Docling parser enabled but unavailable or not implemented; "
                "using fallback parsers."
            )

    if provider in {"auto", "unstructured"} and settings.enable_unstructured:
        parser = UnstructuredParser()
        if parser.is_available() and parser.is_implemented():
            parsers.append(parser)
        else:
            logger.warning(
                "Unstructured parser enabled but unavailable or not implemented; "
                "using fallback parsers."
            )

    if provider in {"auto", "builtin", "docling", "unstructured"}:
        parsers.extend(DEFAULT_PARSERS)

    return tuple(parsers or DEFAULT_PARSERS)
