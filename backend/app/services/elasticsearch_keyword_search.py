from __future__ import annotations

import json
import logging
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from typing import Any
from uuid import UUID

import httpx

from app.core.config import settings
from app.schemas.documents import KeywordSearchResponse, KeywordSearchResult
from app.services.access_control import AccessFilter
from app.services.keyword_search import KeywordSearchService
from app.services.rag_chunk import RagChunk, qdrant_payload

logger = logging.getLogger(__name__)

CONTENT_PREVIEW_LIMIT = 300

ROOT_KEYWORD_FIELDS = (
    "chunk_id",
    "semantic_chunk_id",
    "document_id",
    "document_version",
    "tenant_id",
    "organization_id",
    "knowledge_base_id",
    "visibility",
    "chunk_type",
    "content_format",
    "source_type",
    "source_name",
    "source_file",
    "id_vb",
    "ky_hieu",
    "doc_code",
    "document_code",
    "noi_ban_hanh",
    "issuing_org",
    "issuer",
    "nguoi_ky",
    "signer",
    "trich_yeu",
    "subject",
    "ngay_vb",
    "nam",
    "thang",
    "table_name",
    "column_name",
    "section_id",
    "parent_section_id",
    "article_number",
    "article_title",
    "chapter_number",
    "chapter_title",
    "unit",
    "platform",
    "feature_name",
    "column_context_headers",
    "screen_name",
    "phase",
    "change_type",
    "change_topic",
    "content_type",
    "person_name",
    "area",
    "lead_department",
    "relationship_type",
)

ARRAY_KEYWORD_FIELDS = (
    "identifiers",
    "doc_codes",
    "dates",
    "scope",
    "section_path",
    "screen_names",
    "business_domains",
    "project_codes",
    "staff_names",
)

TEXT_SEARCH_FIELDS = (
    "content^5",
    "enriched_content^3",
    "searchable_metadata^2",
    "document_title^3",
    "trich_yeu^3",
    "subject^3",
    "ky_hieu^6",
    "doc_code^6",
    "id_vb^6",
    "identifiers^8",
    "doc_codes^8",
    "table_name^2",
    "section_path^2",
    "feature_name^2",
    "column_name^3",
    "screen_name^2",
)


class ElasticsearchKeywordError(RuntimeError):
    pass


@dataclass(frozen=True)
class ElasticsearchChunkIndexResult:
    indexed_count: int
    index_name: str


class ElasticsearchKeywordStore:
    """Chunk-level Elasticsearch storage for exact keyword search and BM25.

    The canonical text/chunk metadata remains in PostgreSQL.  This store is a
    retrieval index only: one Elasticsearch document per RAG chunk, with stable
    `_id = chunk database UUID` so re-indexing is idempotent.
    """

    def __init__(
        self,
        *,
        url: str | None = None,
        index_name: str | None = None,
        timeout_seconds: int | float | None = None,
        batch_size: int | None = None,
    ) -> None:
        self.url = (url or settings.resolved_elasticsearch_url).rstrip("/")
        self.index_name = (index_name or settings.elasticsearch_index_name).strip()
        self.timeout_seconds = float(
            timeout_seconds
            if timeout_seconds is not None
            else settings.elasticsearch_timeout_seconds
        )
        self.batch_size = int(batch_size or settings.elasticsearch_index_batch_size)
        self.headers = _auth_headers()
        self._index_ready = False

    async def ensure_index(self) -> None:
        if self._index_ready:
            return
        if not settings.elasticsearch_enabled:
            return
        if not self.url or not self.index_name:
            raise ElasticsearchKeywordError("Elasticsearch URL/index is not configured.")

        async with httpx.AsyncClient(timeout=self.timeout_seconds, verify=settings.elasticsearch_verify_ssl, headers=self.headers) as client:
            response = await client.head(f"{self.url}/{self.index_name}")
            if response.status_code == 404:
                create_response = await client.put(
                    f"{self.url}/{self.index_name}",
                    json=self._index_definition(),
                )
                if create_response.status_code not in {200, 201}:
                    # Handle a harmless race where another worker creates it.
                    if create_response.status_code != 400 or "resource_already_exists" not in create_response.text:
                        raise ElasticsearchKeywordError(
                            f"Failed to create Elasticsearch index {self.index_name}: "
                            f"HTTP {create_response.status_code} {create_response.text[:500]}"
                        )
            elif response.status_code >= 400:
                raise ElasticsearchKeywordError(
                    f"Failed to check Elasticsearch index {self.index_name}: HTTP {response.status_code}"
                )
        self._index_ready = True

    async def upsert_chunks(
        self,
        *,
        chunks: list[RagChunk],
        embedding_texts: list[str] | None = None,
    ) -> ElasticsearchChunkIndexResult:
        if not settings.elasticsearch_enabled or not chunks:
            return ElasticsearchChunkIndexResult(indexed_count=0, index_name=self.index_name)
        await self.ensure_index()
        texts = embedding_texts or [None] * len(chunks)
        indexed = 0
        async with httpx.AsyncClient(timeout=self.timeout_seconds, verify=settings.elasticsearch_verify_ssl, headers=self.headers) as client:
            for batch_start in range(0, len(chunks), max(1, self.batch_size)):
                batch_chunks = chunks[batch_start : batch_start + self.batch_size]
                batch_texts = texts[batch_start : batch_start + self.batch_size]
                body_lines: list[str] = []
                for chunk, embedding_text in zip(batch_chunks, batch_texts, strict=False):
                    doc = self._chunk_document(chunk, embedding_text=embedding_text)
                    body_lines.append(
                        json.dumps(
                            {
                                "index": {
                                    "_index": self.index_name,
                                    "_id": doc["chunk_id"],
                                }
                            },
                            ensure_ascii=False,
                        )
                    )
                    body_lines.append(json.dumps(doc, ensure_ascii=False, default=str))
                response = await client.post(
                    f"{self.url}/_bulk",
                    content="\n".join(body_lines) + "\n",
                    headers={**self.headers, "Content-Type": "application/x-ndjson"},
                )
                if response.status_code >= 400:
                    raise ElasticsearchKeywordError(
                        f"Elasticsearch bulk index failed: HTTP {response.status_code} {response.text[:500]}"
                    )
                payload = response.json()
                if payload.get("errors"):
                    failures = [
                        item
                        for item in payload.get("items", [])
                        if item.get("index", {}).get("error")
                    ][:5]
                    raise ElasticsearchKeywordError(
                        f"Elasticsearch bulk index had item errors: {failures}"
                    )
                indexed += len(batch_chunks)
        return ElasticsearchChunkIndexResult(indexed_count=indexed, index_name=self.index_name)

    async def delete_points_for_document(
        self,
        document_id: UUID | str,
        *,
        tenant_id: UUID | str | None = None,
    ) -> None:
        if not settings.elasticsearch_enabled:
            return
        await self.ensure_index()
        filters: list[dict[str, Any]] = [
            {"term": {"document_id": str(document_id)}},
        ]
        if tenant_id is not None:
            filters.append({"term": {"tenant_id": str(tenant_id)}})
        query = {"query": {"bool": {"filter": filters}}}
        async with httpx.AsyncClient(timeout=self.timeout_seconds, verify=settings.elasticsearch_verify_ssl, headers=self.headers) as client:
            response = await client.post(
                f"{self.url}/{self.index_name}/_delete_by_query",
                json=query,
                params={"conflicts": "proceed", "refresh": "true"},
            )
            if response.status_code == 404:
                return
            if response.status_code >= 400:
                raise ElasticsearchKeywordError(
                    f"Elasticsearch delete_by_query failed: HTTP {response.status_code} {response.text[:500]}"
                )

    def _chunk_document(self, chunk: RagChunk, *, embedding_text: str | None) -> dict[str, Any]:
        payload = qdrant_payload(chunk, store_raw_text=False)
        chunk_id = str(payload.get("chunk_id") or chunk.database_chunk_id or chunk.chunk_id)
        document_title = chunk.document_title or str(payload.get("document_title") or "")
        searchable_metadata = _metadata_text(payload)
        doc: dict[str, Any] = {
            "chunk_id": chunk_id,
            "semantic_chunk_id": str(payload.get("semantic_chunk_id") or chunk.chunk_id),
            "document_id": str(chunk.document_id),
            "document_title": document_title,
            "tenant_id": chunk.tenant_id,
            "organization_id": chunk.organization_id,
            "knowledge_base_id": chunk.knowledge_base_id,
            "visibility": chunk.visibility,
            "chunk_index": chunk.chunk_index,
            "content": chunk.text,
            "enriched_content": chunk.embedding_text or "",
            "embedding_text": embedding_text or "",
            "searchable_metadata": searchable_metadata,
            "metadata": _json_safe(payload),
            "metadata_json": json.dumps(_json_safe(payload), ensure_ascii=False, default=str),
            "content_length": len(chunk.text or ""),
            "token_count": chunk.token_count,
        }
        for key in ROOT_KEYWORD_FIELDS:
            value = payload.get(key)
            if value in (None, "", []):
                continue
            doc[key] = _scalar_or_list(value)
            doc[f"{key}_normalized"] = _normalize_for_exact(value)
        for key in ARRAY_KEYWORD_FIELDS:
            values = _as_list(payload.get(key))
            if not values:
                continue
            doc[key] = values
            doc[f"{key}_normalized"] = [_normalize_text(value) for value in values if value]
        return {key: value for key, value in doc.items() if value not in (None, "", [])}

    @staticmethod
    def _index_definition() -> dict[str, Any]:
        keyword_props = {
            key: {"type": "keyword"}
            for key in (*ROOT_KEYWORD_FIELDS, *ARRAY_KEYWORD_FIELDS)
        }
        normalized_props = {
            f"{key}_normalized": {"type": "keyword"}
            for key in (*ROOT_KEYWORD_FIELDS, *ARRAY_KEYWORD_FIELDS)
        }
        return {
            "settings": {
                "analysis": {
                    "filter": {
                        "vi_ascii_folding": {
                            "type": "asciifolding",
                            "preserve_original": True,
                        }
                    },
                    "analyzer": {
                        "vi_bm25": {
                            "type": "custom",
                            "tokenizer": "standard",
                            "filter": ["lowercase", "vi_ascii_folding"],
                        },
                        "vi_exact": {
                            "type": "custom",
                            "tokenizer": "keyword",
                            "filter": ["lowercase", "vi_ascii_folding"],
                        },
                    },
                }
            },
            "mappings": {
                "dynamic": True,
                "properties": {
                    **keyword_props,
                    **normalized_props,
                    "chunk_index": {"type": "integer"},
                    "content_length": {"type": "integer"},
                    "token_count": {"type": "integer"},
                    "content": {
                        "type": "text",
                        "analyzer": "vi_bm25",
                        "search_analyzer": "vi_bm25",
                        "fields": {"exact": {"type": "text", "analyzer": "vi_exact"}},
                    },
                    "enriched_content": {"type": "text", "analyzer": "vi_bm25"},
                    "embedding_text": {"type": "text", "analyzer": "vi_bm25", "index": False},
                    "document_title": {"type": "text", "analyzer": "vi_bm25"},
                    "searchable_metadata": {"type": "text", "analyzer": "vi_bm25"},
                    "metadata_json": {"type": "text", "analyzer": "vi_bm25", "index": False},
                    "metadata": {"type": "object", "enabled": False},
                },
            },
        }


class ElasticsearchKeywordSearchService:
    """Keyword/BM25 search service backed by Elasticsearch with optional SQL fallback."""

    def __init__(
        self,
        *,
        store: ElasticsearchKeywordStore,
        fallback_service: KeywordSearchService | None = None,
    ) -> None:
        self._store = store
        self._fallback_service = fallback_service

    async def search(
        self,
        *,
        query: str,
        top_k: int,
        document_ids: set[UUID] | set[str] | None = None,
        access_filter: AccessFilter | None = None,
        retrieval_enrichment_enabled: bool = False,
    ) -> KeywordSearchResponse:
        if document_ids is not None and not document_ids:
            return KeywordSearchResponse(query=query, top_k=top_k, results=[])
        try:
            await self._store.ensure_index()
            payload = self._build_query(query=query, top_k=top_k, document_ids=document_ids)
            async with httpx.AsyncClient(timeout=self._store.timeout_seconds, verify=settings.elasticsearch_verify_ssl, headers=self._store.headers) as client:
                response = await client.post(
                    f"{self._store.url}/{self._store.index_name}/_search",
                    json=payload,
                )
                if response.status_code >= 400:
                    raise ElasticsearchKeywordError(
                        f"Elasticsearch search failed: HTTP {response.status_code} {response.text[:500]}"
                    )
                data = response.json()
        except Exception as exc:
            if self._fallback_service is not None and settings.elasticsearch_fallback_to_postgres:
                logger.exception("Elasticsearch keyword search failed; falling back to PostgreSQL keyword search.")
                return await self._fallback_service.search(
                    query=query,
                    top_k=top_k,
                    document_ids={UUID(str(item)) for item in document_ids} if document_ids else None,
                    access_filter=access_filter,
                    retrieval_enrichment_enabled=retrieval_enrichment_enabled,
                )
            raise ElasticsearchKeywordError("Failed to run Elasticsearch keyword search.") from exc

        exact_terms = KeywordSearchService._extract_exact_terms(query)
        hits = data.get("hits", {}).get("hits", []) if isinstance(data, dict) else []
        results: list[KeywordSearchResult] = []
        for hit in hits:
            source = dict(hit.get("_source") or {})
            metadata = dict(source.get("metadata") or {})
            metadata["keyword_backend"] = "elasticsearch"
            metadata["elasticsearch_index"] = self._store.index_name
            metadata["exact_match_terms"] = _matched_terms(
                exact_terms,
                " ".join(
                    str(part or "")
                    for part in (
                        source.get("content"),
                        source.get("searchable_metadata"),
                        source.get("metadata_json"),
                    )
                ),
            )
            results.append(
                KeywordSearchResult(
                    chunk_id=source.get("chunk_id") or hit.get("_id"),
                    document_id=source.get("document_id"),
                    score=float(hit.get("_score") or 0.0),
                    content_preview=str(source.get("content") or "")[:CONTENT_PREVIEW_LIMIT],
                    metadata={key: value for key, value in metadata.items() if value not in (None, "", [])},
                )
            )
        return KeywordSearchResponse(query=query, top_k=top_k, results=results)

    @staticmethod
    def _build_query(
        *,
        query: str,
        top_k: int,
        document_ids: set[UUID] | set[str] | None,
    ) -> dict[str, Any]:
        clean_query = " ".join(str(query or "").split()).strip()
        exact_terms = KeywordSearchService._extract_exact_terms(clean_query)
        should: list[dict[str, Any]] = [
            {
                "multi_match": {
                    "query": clean_query,
                    "fields": list(TEXT_SEARCH_FIELDS),
                    "type": "best_fields",
                    "operator": "or",
                    "boost": 2.0,
                }
            },
            {
                "simple_query_string": {
                    "query": clean_query,
                    "fields": list(TEXT_SEARCH_FIELDS),
                    "default_operator": "and",
                    "boost": 1.5,
                }
            },
        ]
        for term in exact_terms:
            normalized = _normalize_text(term)
            should.extend(
                [
                    {"match_phrase": {"content": {"query": term, "slop": 0, "boost": 18.0}}},
                    {"match_phrase": {"enriched_content": {"query": term, "slop": 0, "boost": 10.0}}},
                    {"match_phrase": {"searchable_metadata": {"query": term, "slop": 0, "boost": 12.0}}},
                    {"term": {"identifiers": {"value": term, "boost": 40.0}}},
                    {"term": {"doc_codes": {"value": term, "boost": 40.0}}},
                    {"term": {"identifiers_normalized": {"value": normalized, "boost": 35.0}}},
                    {"term": {"doc_codes_normalized": {"value": normalized, "boost": 35.0}}},
                    {"term": {"id_vb_normalized": {"value": normalized, "boost": 35.0}}},
                    {"term": {"ky_hieu_normalized": {"value": normalized, "boost": 35.0}}},
                ]
            )
        filters: list[dict[str, Any]] = []
        if document_ids is not None:
            filters.append({"terms": {"document_id": [str(item) for item in document_ids]}})
        return {
            "size": top_k,
            "track_scores": True,
            "query": {
                "bool": {
                    "filter": filters,
                    "should": should,
                    "minimum_should_match": 1,
                }
            },
            "sort": ["_score", {"document_id": "asc"}, {"chunk_index": "asc"}],
        }


def _as_list(value: Any) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, list | tuple | set):
        return [str(item) for item in value if str(item).strip()]
    return [str(value)]


def _scalar_or_list(value: Any) -> str | list[str] | int | float | bool:
    if isinstance(value, list | tuple | set):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, bool | int | float):
        return value
    return str(value)


def _normalize_for_exact(value: Any) -> str | list[str]:
    values = _as_list(value)
    if len(values) == 1:
        return _normalize_text(values[0])
    return [_normalize_text(item) for item in values]


def _normalize_text(value: Any) -> str:
    normalized = unicodedata.normalize("NFD", str(value or ""))
    normalized = "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")
    normalized = normalized.replace("\u0110", "D").replace("\u0111", "d")
    normalized = re.sub(r"\s+", " ", normalized.casefold()).strip()
    return normalized


def _metadata_text(metadata: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in (*ROOT_KEYWORD_FIELDS, *ARRAY_KEYWORD_FIELDS, "document_title", "source_summary", "answer_text", "row_text"):
        value = metadata.get(key)
        if isinstance(value, list | tuple | set):
            parts.extend(str(item) for item in value if str(item).strip())
        elif value not in (None, ""):
            parts.append(str(value))
    return "\n".join(dict.fromkeys(parts))[:10_000]


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list | tuple | set):
        return [_json_safe(item) for item in value]
    if isinstance(value, str | int | float | bool) or value is None:
        return value
    return str(value)


def _matched_terms(terms: list[str], searchable_text: str) -> list[str]:
    normalized_text = _normalize_text(searchable_text)
    matched = []
    for term in terms:
        if _normalize_text(term) in normalized_text:
            matched.append(term)
    return matched


def _auth_headers() -> dict[str, str]:
    if settings.elasticsearch_api_key:
        return {"Authorization": f"ApiKey {settings.elasticsearch_api_key}"}
    return {}


@lru_cache
def get_elasticsearch_keyword_store() -> ElasticsearchKeywordStore:
    return ElasticsearchKeywordStore()


