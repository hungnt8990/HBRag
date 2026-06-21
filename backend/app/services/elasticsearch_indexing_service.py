from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from app.repositories.documents import DocumentRepository
from app.services.elasticsearch_store import ElasticsearchConfigurationError, ElasticsearchStore
from app.services.rag_chunk import (
    RagChunk,
    qdrant_payload,
    rag_chunk_from_database,
    should_index_chunk,
)


class ElasticsearchIndexingError(RuntimeError):
    pass


@dataclass(frozen=True)
class ElasticsearchIndexResponse:
    document_id: UUID
    status: str
    indexed_chunk_count: int
    index_name: str
    skipped: bool = False


def normalize_for_keyword(value: object) -> str:
    text = str(value or "").casefold()
    normalized = unicodedata.normalize("NFD", text)
    stripped = "".join(char for char in normalized if unicodedata.category(char) != "Mn")
    stripped = stripped.replace("\u0111", "d").replace("\u0110", "D")
    stripped = re.sub(r"[_\-/\\\.]+", " ", stripped)
    stripped = re.sub(r"[^a-z0-9%\s]", " ", stripped)
    return re.sub(r"\s+", " ", stripped).strip()


def _as_list(value: object) -> list[Any]:
    if value is None or value == "":
        return []
    if isinstance(value, list | tuple | set):
        return [item for item in value if item not in (None, "")]
    return [value]


def _strings(value: object) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in _as_list(value):
        text = " ".join(str(item or "").split()).strip()
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(text)
    return result


def _join_values(value: object) -> str:
    return " | ".join(_strings(value))


def _collect_text_values(value: object, *, limit: int = 128) -> list[str]:
    values: list[str] = []
    seen: set[str] = set()

    def add(item: object) -> None:
        if len(values) >= limit:
            return
        if item is None:
            return
        if isinstance(item, dict):
            for key, nested in item.items():
                add(key)
                add(nested)
            return
        if isinstance(item, list | tuple | set):
            for nested in item:
                add(nested)
            return
        text = " ".join(str(item).split()).strip()
        if not text:
            return
        key = normalize_for_keyword(text)
        if not key or key in seen:
            return
        seen.add(key)
        values.append(text)

    add(value)
    return values


def _entity_values(chunk: RagChunk, payload: dict[str, Any]) -> list[str]:
    entity_sources = [
        chunk.document_title,
        chunk.source_file,
        chunk.section_path,
        chunk.chapter_title,
        chunk.article_title,
        chunk.table_name,
        chunk.table_description,
        chunk.table_columns,
        chunk.row_text,
        chunk.field_names,
        chunk.identifiers,
        chunk.doc_codes,
        chunk.dates,
        chunk.entity,
        chunk.relationship_name,
        chunk.area,
        chunk.lead_department,
        chunk.staff_names,
        chunk.person_name,
        chunk.source_systems,
        payload.get("entities"),
        payload.get("enrichment_keywords"),
        payload.get("enrichment_summary"),
        payload.get("document_code"),
        payload.get("issued_date"),
        payload.get("structure_path"),
    ]
    values: list[str] = []
    seen: set[str] = set()
    for source in entity_sources:
        for text in _collect_text_values(source):
            key = normalize_for_keyword(text)
            if not key or key in seen:
                continue
            seen.add(key)
            values.append(text)
    return values


def elasticsearch_chunk_document(chunk: RagChunk) -> dict[str, Any]:
    payload = qdrant_payload(chunk, store_raw_text=False)
    section_path = _join_values(chunk.section_path)
    table_headers = _join_values(chunk.table_columns or payload.get("table_headers"))
    table_context = _join_values(
        [
            chunk.table_description,
            payload.get("table_context"),
            payload.get("source_table"),
        ]
    )
    field_names = _strings(chunk.field_names or payload.get("field_names"))
    entities = _entity_values(chunk, payload)

    document: dict[str, Any] = {
        "chunk_id": chunk.database_chunk_id or chunk.chunk_id,
        "semantic_chunk_id": chunk.chunk_id,
        "document_id": chunk.document_id,
        "organization_id": chunk.organization_id,
        "knowledge_base_id": chunk.knowledge_base_id,
        "uploaded_by_user_id": chunk.uploaded_by_user_id,
        "visibility": chunk.visibility,
        "chunk_index": chunk.chunk_index,
        "chunk_type": chunk.chunk_type,
        "content_format": chunk.content_format,
        "parser": chunk.parser,
        "chunker": chunk.chunker,
        "source_file": chunk.source_file,
        "source_file_norm": normalize_for_keyword(chunk.source_file),
        "document_title": chunk.document_title,
        "document_title_norm": normalize_for_keyword(chunk.document_title),
        "content": chunk.text,
        "content_norm": normalize_for_keyword(chunk.text),
        "section_path": section_path,
        "section_path_norm": normalize_for_keyword(section_path),
        "section_id": chunk.section_id,
        "parent_section_id": chunk.parent_section_id,
        "chapter_number": chunk.chapter_number,
        "chapter_title": chunk.chapter_title,
        "article_number": chunk.article_number,
        "article_title": chunk.article_title,
        "table_name": chunk.table_name,
        "table_name_norm": normalize_for_keyword(chunk.table_name),
        "table_description": chunk.table_description,
        "table_headers": table_headers,
        "table_headers_norm": normalize_for_keyword(table_headers),
        "table_context": table_context,
        "table_context_norm": normalize_for_keyword(table_context),
        "row_text": chunk.row_text or payload.get("row_text"),
        "row_text_norm": normalize_for_keyword(chunk.row_text or payload.get("row_text")),
        "row_start": chunk.row_start,
        "row_end": chunk.row_end,
        "field_names": field_names,
        "field_names_norm": normalize_for_keyword(" ".join(field_names)),
        "identifiers": _strings(chunk.identifiers),
        "doc_codes": _strings(chunk.doc_codes),
        "dates": _strings(chunk.dates),
        "entities": entities,
        "entities_norm": normalize_for_keyword(" ".join(entities)),
        "metadata": payload,
    }
    return {key: value for key, value in document.items() if value not in (None, "", [])}


class ElasticsearchIndexingService:
    def __init__(
        self,
        *,
        repository: DocumentRepository,
        store: ElasticsearchStore,
    ) -> None:
        self._repository = repository
        self._store = store

    async def index_document(self, document_id: UUID) -> ElasticsearchIndexResponse:
        if not self._store.is_configured:
            return ElasticsearchIndexResponse(
                document_id=document_id,
                status="skipped",
                indexed_chunk_count=0,
                index_name=self._store.index_name,
                skipped=True,
            )

        document = await self._repository.get_document(document_id)
        if document is None:
            raise ElasticsearchIndexingError("Document not found.")
        if str(document.status) not in {"chunked", "indexed"}:
            raise ElasticsearchIndexingError(
                "Only chunked or indexed documents can be indexed into Elasticsearch."
            )

        try:
            chunks = await self._repository.list_chunks_for_document(document_id)
            if not chunks:
                raise ElasticsearchIndexingError("Document has no chunks to index.")
            document_file = await self._repository.get_primary_document_file(document_id)
            source_file = str(getattr(document_file, "filename", "document"))
            source_uri = str(getattr(document_file, "storage_path", "")) or None
            rag_chunks = [
                rag_chunk_from_database(
                    chunk,
                    document=document,
                    source_file=source_file,
                    source_uri=source_uri,
                )
                for chunk in chunks
            ]
            indexable_chunks = [chunk for chunk in rag_chunks if should_index_chunk(chunk)]
            documents = [elasticsearch_chunk_document(chunk) for chunk in indexable_chunks]

            await self._store.ensure_index()
            await self._store.delete_document(str(document_id))
            await self._store.bulk_index(documents)
            await self._store.refresh()
            await self._repository.update_document_metadata(
                document,
                {
                    "elasticsearch_index": self._store.index_name,
                    "elasticsearch_indexed_chunk_count": len(documents),
                    "elasticsearch_indexed_at": datetime.now(UTC).isoformat(),
                },
            )
            await self._repository.commit()
        except ElasticsearchConfigurationError:
            raise
        except Exception as exc:
            await self._repository.rollback()
            raise ElasticsearchIndexingError(
                f"Failed to index document into Elasticsearch: {exc}"
            ) from exc

        return ElasticsearchIndexResponse(
            document_id=document_id,
            status="indexed",
            indexed_chunk_count=len(documents),
            index_name=self._store.index_name,
        )
