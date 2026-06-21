from __future__ import annotations

import logging
import re
from typing import Any
from uuid import UUID

import httpx

from app.schemas.documents import KeywordSearchResponse, KeywordSearchResult
from app.services.access_control import AccessFilter
from app.services.elasticsearch_indexing_service import normalize_for_keyword
from app.services.elasticsearch_store import (
    ElasticsearchConfigurationError,
    ElasticsearchStore,
    ElasticsearchStoreError,
)
from app.services.keyword_search import KeywordSearchError, KeywordSearchService

logger = logging.getLogger(__name__)

SOURCE_FIELDS = [
    "chunk_id",
    "semantic_chunk_id",
    "document_id",
    "organization_id",
    "knowledge_base_id",
    "visibility",
    "chunk_index",
    "chunk_type",
    "source_file",
    "document_title",
    "content",
    "section_path",
    "section_id",
    "parent_section_id",
    "chapter_number",
    "chapter_title",
    "article_number",
    "article_title",
    "table_name",
    "table_description",
    "table_headers",
    "table_context",
    "row_text",
    "row_start",
    "row_end",
    "field_names",
    "identifiers",
    "doc_codes",
    "dates",
    "entities",
    "metadata",
]

CONTENT_PREVIEW_LIMIT = 300
MAX_EXACT_TERMS = 8

QUERY_STOPWORDS = {
    "anh",
    "ban",
    "cac",
    "cho",
    "co",
    "cua",
    "duoc",
    "gi",
    "hay",
    "hoi",
    "khong",
    "khi",
    "la",
    "lam",
    "nao",
    "nay",
    "neu",
    "nhu",
    "noi",
    "sao",
    "theo",
    "thi",
    "toi",
    "trong",
    "va",
    "ve",
    "voi",
    "xin",
    "bao",
    "nhieu",
    "may",
    "what",
    "which",
    "how",
    "many",
    "the",
    "and",
    "of",
}


def _extract_exact_terms(query: str) -> list[str]:
    terms: list[str] = []
    seen: set[str] = set()

    quoted = re.findall(r'"([^"]+)"|\'([^\']+)\'', query or "")
    candidates = [item for pair in quoted for item in pair if item]
    candidates.extend(re.findall(r"\b[A-Z0-9][A-Z0-9._/-]{1,}\b", query or "", flags=re.IGNORECASE))

    normalized = normalize_for_keyword(query)
    tokens = [
        token
        for token in normalized.split()
        if len(token) > 1 and token not in QUERY_STOPWORDS
    ]
    for size in range(min(5, len(tokens)), 1, -1):
        for index in range(0, len(tokens) - size + 1):
            candidates.append(" ".join(tokens[index : index + size]))
            if len(candidates) >= MAX_EXACT_TERMS * 2:
                break
        if len(candidates) >= MAX_EXACT_TERMS * 2:
            break
    candidates.extend(tokens)

    for candidate in candidates:
        clean = " ".join(str(candidate or "").split()).strip(" ?!.,;:")
        key = normalize_for_keyword(clean)
        if len(key) < 2 or key in seen:
            continue
        seen.add(key)
        terms.append(clean)
        if len(terms) >= MAX_EXACT_TERMS:
            break
    return terms


def _build_text_query(query: str, *, document_ids: set[UUID] | None = None) -> dict[str, Any]:
    query_norm = normalize_for_keyword(query)
    base_fields = [
        "content^1.5",
        "content.folded^1.5",
        "document_title^3",
        "document_title.folded^3",
        "source_file^2",
        "source_file.folded^2",
        "section_path^3",
        "section_path.folded^3",
        "chapter_title^2",
        "chapter_title.folded^2",
        "article_title^3",
        "article_title.folded^3",
        "table_name^4",
        "table_name.folded^4",
        "table_description^2",
        "table_description.folded^2",
        "table_headers^3",
        "table_headers.folded^3",
        "table_context^2",
        "table_context.folded^2",
        "row_text^4",
        "row_text.folded^4",
        "field_names^5",
        "field_names.folded^5",
        "entities^4",
        "entities.folded^4",
        "identifiers^8",
        "doc_codes^8",
        "dates^4",
        "chunk_type",
    ]
    norm_fields = [
        "content_norm^4",
        "document_title_norm^4",
        "source_file_norm^3",
        "section_path_norm^5",
        "table_name_norm^6",
        "table_headers_norm^5",
        "table_context_norm^3",
        "row_text_norm^6",
        "field_names_norm^6",
        "entities_norm^6",
    ]

    should: list[dict[str, Any]] = []
    if query_norm:
        should.append(
            {
                "multi_match": {
                    "query": query_norm,
                    "fields": norm_fields,
                    "operator": "or",
                }
            }
        )
    should.append(
        {
            "multi_match": {
                "query": query,
                "fields": base_fields,
                "operator": "or",
            }
        }
    )
    for term in _extract_exact_terms(query):
        term_norm = normalize_for_keyword(term)
        should.extend(
            [
                {
                    "multi_match": {
                        "query": term,
                        "fields": [
                            "identifiers^12",
                            "doc_codes^12",
                            "document_title^6",
                            "source_file^5",
                            "table_name^5",
                            "field_names^5",
                            "entities^5",
                            "content^2",
                        ],
                        "type": "phrase",
                        "slop": 1,
                    }
                },
                {
                    "multi_match": {
                        "query": term_norm,
                        "fields": [
                            "content_norm^5",
                            "document_title_norm^6",
                            "source_file_norm^5",
                            "section_path_norm^5",
                            "table_name_norm^6",
                            "row_text_norm^6",
                            "field_names_norm^7",
                            "entities_norm^7",
                        ],
                        "type": "phrase",
                        "slop": 2,
                    }
                },
            ]
        )

    filters: list[dict[str, Any]] = []
    if document_ids is not None:
        filters.append({"terms": {"document_id": [str(document_id) for document_id in document_ids]}})

    query_body: dict[str, Any] = {
        "bool": {
            "should": should,
            "minimum_should_match": 1,
        }
    }
    if filters:
        query_body["bool"]["filter"] = filters
    return query_body


def _metadata_from_source(source: dict[str, Any], *, backend: str) -> dict[str, object]:
    metadata = dict(source.get("metadata") or {})
    metadata.update(
        {
            "keyword_backend": backend,
            "semantic_chunk_id": source.get("semantic_chunk_id"),
            "document_title": source.get("document_title"),
            "source_file": source.get("source_file"),
            "chunk_type": source.get("chunk_type"),
            "section_path": source.get("section_path"),
            "table_name": source.get("table_name"),
            "table_headers": source.get("table_headers"),
            "table_context": source.get("table_context"),
            "row_text": source.get("row_text"),
            "field_names": source.get("field_names"),
            "identifiers": source.get("identifiers"),
            "doc_codes": source.get("doc_codes"),
            "dates": source.get("dates"),
            "entities": source.get("entities"),
        }
    )
    return {key: value for key, value in metadata.items() if value not in (None, "", [])}


class ElasticsearchKeywordSearchService:
    def __init__(
        self,
        *,
        store: ElasticsearchStore,
        fallback: KeywordSearchService,
        enabled: bool = True,
    ) -> None:
        self._store = store
        self._fallback = fallback
        self._enabled = enabled

    async def search(
        self,
        *,
        query: str,
        top_k: int,
        document_ids: set[UUID] | None = None,
        access_filter: AccessFilter | None = None,
    ) -> KeywordSearchResponse:
        if document_ids is not None and not document_ids:
            return KeywordSearchResponse(query=query, top_k=top_k, results=[])
        if not self._enabled or not self._store.is_configured:
            return await self._fallback.search(
                query=query,
                top_k=top_k,
                document_ids=document_ids,
                access_filter=access_filter,
            )
        try:
            payload = await self._store.search(
                query=_build_text_query(query, document_ids=document_ids),
                size=top_k,
                source_fields=SOURCE_FIELDS,
            )
            results = []
            for hit in payload.get("hits", {}).get("hits", []):
                source = hit.get("_source") or {}
                content = str(source.get("content") or "")
                results.append(
                    KeywordSearchResult(
                        chunk_id=source.get("chunk_id") or hit.get("_id"),
                        document_id=source.get("document_id"),
                        score=float(hit.get("_score") or 0.0),
                        content_preview=content[:CONTENT_PREVIEW_LIMIT],
                        metadata=_metadata_from_source(source, backend="elasticsearch"),
                    )
                )
            return KeywordSearchResponse(query=query, top_k=top_k, results=results)
        except (ElasticsearchConfigurationError, ElasticsearchStoreError, httpx.HTTPError) as exc:
            logger.warning("Elasticsearch keyword search failed; falling back to PostgreSQL: %s", exc)
            return await self._fallback.search(
                query=query,
                top_k=top_k,
                document_ids=document_ids,
                access_filter=access_filter,
            )
        except Exception as exc:
            logger.exception("Unexpected Elasticsearch keyword search error.")
            try:
                return await self._fallback.search(
                    query=query,
                    top_k=top_k,
                    document_ids=document_ids,
                    access_filter=access_filter,
                )
            except Exception as fallback_exc:
                raise KeywordSearchError("Failed to run Elasticsearch and PostgreSQL keyword search.") from fallback_exc
