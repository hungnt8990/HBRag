from __future__ import annotations

import base64
import json
import logging
from typing import Any

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)


class ElasticsearchConfigurationError(RuntimeError):
    pass


class ElasticsearchStoreError(RuntimeError):
    pass


def _strip_trailing_slash(value: str) -> str:
    return value.rstrip("/")


def _url_from_cloud_id(cloud_id: str) -> str:
    try:
        encoded = cloud_id.split(":", 1)[1] if ":" in cloud_id else cloud_id
        decoded = base64.b64decode(encoded).decode("utf-8")
        host, elasticsearch_id, *_ = decoded.split("$")
    except Exception as exc:
        raise ElasticsearchConfigurationError("Invalid ELASTICSEARCH_CLOUD_ID.") from exc
    if not host or not elasticsearch_id:
        raise ElasticsearchConfigurationError("Invalid ELASTICSEARCH_CLOUD_ID.")
    return f"https://{elasticsearch_id}.{host}"


def _authorization_header(api_key: str | None) -> dict[str, str]:
    if not api_key:
        return {}
    normalized = api_key.strip()
    if normalized.lower().startswith(("apikey ", "bearer ", "basic ")):
        return {"Authorization": normalized}
    return {"Authorization": f"ApiKey {normalized}"}


class ElasticsearchStore:
    def __init__(
        self,
        *,
        base_url: str | None,
        cloud_id: str | None,
        api_key: str | None,
        index_name: str,
        timeout: float,
        verify_ssl: bool,
    ) -> None:
        self.index_name = index_name
        self._api_key = api_key
        self._timeout = timeout
        self._verify_ssl = verify_ssl
        self._configuration_error: str | None = None
        try:
            self._base_url = self._resolve_base_url(base_url=base_url, cloud_id=cloud_id)
        except ElasticsearchConfigurationError as exc:
            logger.warning("Elasticsearch configuration is invalid: %s", exc)
            self._configuration_error = str(exc)
            self._base_url = None

    @property
    def is_configured(self) -> bool:
        return bool(self._base_url and self._api_key and self.index_name)

    async def ensure_index(self) -> None:
        self._require_configured()
        assert self._base_url is not None
        mapping = self._index_definition()
        async with self._client() as client:
            response = await client.head(f"/{self.index_name}")
            if response.status_code == 404:
                create_response = await client.put(f"/{self.index_name}", json=mapping)
                self._raise_for_response(create_response, "create Elasticsearch index")
                return
            if response.status_code >= 400:
                self._raise_for_response(response, "check Elasticsearch index")
            put_response = await client.put(
                f"/{self.index_name}/_mapping",
                json=mapping["mappings"],
            )
            self._raise_for_response(put_response, "update Elasticsearch mapping")

    async def bulk_index(self, documents: list[dict[str, Any]]) -> None:
        self._require_configured()
        if not documents:
            return
        lines: list[str] = []
        for document in documents:
            chunk_id = str(document.get("chunk_id") or "")
            if not chunk_id:
                raise ElasticsearchStoreError("Every Elasticsearch document needs chunk_id.")
            lines.append(json.dumps({"index": {"_index": self.index_name, "_id": chunk_id}}, ensure_ascii=False))
            lines.append(json.dumps(document, ensure_ascii=False, default=str))
        body = "\n".join(lines) + "\n"
        async with self._client() as client:
            response = await client.post(
                "/_bulk",
                content=body.encode("utf-8"),
                headers={"Content-Type": "application/x-ndjson"},
            )
            self._raise_for_response(response, "bulk index Elasticsearch documents")
            payload = response.json()
            if payload.get("errors"):
                failed_items = [
                    item
                    for item in payload.get("items", [])
                    if (item.get("index") or {}).get("error")
                ][:3]
                raise ElasticsearchStoreError(
                    f"Elasticsearch bulk indexing failed for {len(failed_items)} sampled item(s): {failed_items}"
                )

    async def delete_document(self, document_id: str) -> None:
        self._require_configured()
        async with self._client() as client:
            response = await client.post(
                f"/{self.index_name}/_delete_by_query",
                json={
                    "query": {
                        "term": {
                            "document_id": str(document_id),
                        }
                    }
                },
            )
            if response.status_code == 404:
                return
            self._raise_for_response(response, "delete Elasticsearch document chunks")

    async def refresh(self) -> None:
        self._require_configured()
        async with self._client() as client:
            response = await client.post(f"/{self.index_name}/_refresh")
            self._raise_for_response(response, "refresh Elasticsearch index")

    async def search(
        self,
        *,
        query: dict[str, Any],
        size: int,
        source_fields: list[str] | None = None,
    ) -> dict[str, Any]:
        self._require_configured()
        body: dict[str, Any] = {"query": query, "size": size}
        if source_fields is not None:
            body["_source"] = source_fields
        async with self._client() as client:
            response = await client.post(f"/{self.index_name}/_search", json=body)
            self._raise_for_response(response, "search Elasticsearch index")
            return response.json()

    def _client(self) -> httpx.AsyncClient:
        assert self._base_url is not None
        return httpx.AsyncClient(
            base_url=self._base_url,
            headers=_authorization_header(self._api_key),
            timeout=self._timeout,
            verify=self._verify_ssl,
        )

    @staticmethod
    def _resolve_base_url(*, base_url: str | None, cloud_id: str | None) -> str | None:
        if base_url and base_url.strip():
            return _strip_trailing_slash(base_url.strip())
        if cloud_id and cloud_id.strip():
            return _strip_trailing_slash(_url_from_cloud_id(cloud_id.strip()))
        return None

    def _require_configured(self) -> None:
        if not self.is_configured:
            detail = f" Detail: {self._configuration_error}" if self._configuration_error else ""
            raise ElasticsearchConfigurationError(
                "Elasticsearch is not configured. Set ELASTICSEARCH_URL or "
                f"ELASTICSEARCH_CLOUD_ID, plus ELASTICSEARCH_API_KEY.{detail}"
            )

    @staticmethod
    def _raise_for_response(response: httpx.Response, action: str) -> None:
        if response.status_code < 400:
            return
        try:
            detail = response.json()
        except ValueError:
            detail = response.text
        raise ElasticsearchStoreError(
            f"Failed to {action}: HTTP {response.status_code}: {detail}"
        )

    @staticmethod
    def _index_definition() -> dict[str, Any]:
        text_with_folded = {
            "type": "text",
            "fields": {
                "folded": {"type": "text", "analyzer": "vi_folded"},
                "keyword": {"type": "keyword", "ignore_above": 512},
            },
        }
        return {
            "settings": {
                "analysis": {
                    "char_filter": {
                        "vietnamese_char_filter": {
                            "type": "mapping",
                            "mappings": ["\u0110=>D", "\u0111=>d"],
                        }
                    },
                    "analyzer": {
                        "vi_folded": {
                            "tokenizer": "standard",
                            "char_filter": ["vietnamese_char_filter"],
                            "filter": ["lowercase", "asciifolding"],
                        }
                    },
                }
            },
            "mappings": {
                "dynamic": True,
                "properties": {
                    "chunk_id": {"type": "keyword"},
                    "semantic_chunk_id": {"type": "keyword"},
                    "document_id": {"type": "keyword"},
                    "organization_id": {"type": "keyword"},
                    "knowledge_base_id": {"type": "keyword"},
                    "uploaded_by_user_id": {"type": "keyword"},
                    "visibility": {"type": "keyword"},
                    "chunk_index": {"type": "integer"},
                    "chunk_type": {"type": "keyword"},
                    "content_format": {"type": "keyword"},
                    "parser": {"type": "keyword"},
                    "chunker": {"type": "keyword"},
                    "source_file": text_with_folded,
                    "source_file_norm": {"type": "text"},
                    "document_title": text_with_folded,
                    "document_title_norm": {"type": "text"},
                    "content": {
                        "type": "text",
                        "fields": {"folded": {"type": "text", "analyzer": "vi_folded"}},
                    },
                    "content_norm": {"type": "text"},
                    "section_path": text_with_folded,
                    "section_path_norm": {"type": "text"},
                    "section_id": {"type": "keyword"},
                    "parent_section_id": {"type": "keyword"},
                    "chapter_number": {"type": "keyword"},
                    "chapter_title": text_with_folded,
                    "article_number": {"type": "keyword"},
                    "article_title": text_with_folded,
                    "table_name": text_with_folded,
                    "table_name_norm": {"type": "text"},
                    "table_description": text_with_folded,
                    "table_headers": text_with_folded,
                    "table_headers_norm": {"type": "text"},
                    "table_context": text_with_folded,
                    "table_context_norm": {"type": "text"},
                    "row_text": {
                        "type": "text",
                        "fields": {"folded": {"type": "text", "analyzer": "vi_folded"}},
                    },
                    "row_text_norm": {"type": "text"},
                    "row_start": {"type": "integer"},
                    "row_end": {"type": "integer"},
                    "field_names": text_with_folded,
                    "field_names_norm": {"type": "text"},
                    "identifiers": {"type": "keyword"},
                    "doc_codes": {"type": "keyword"},
                    "dates": {"type": "keyword"},
                    "entities": text_with_folded,
                    "entities_norm": {"type": "text"},
                    "metadata": {"type": "object", "enabled": False},
                }
            },
        }


_elasticsearch_store: ElasticsearchStore | None = None


def get_elasticsearch_store() -> ElasticsearchStore:
    global _elasticsearch_store
    if _elasticsearch_store is None:
        _elasticsearch_store = ElasticsearchStore(
            base_url=settings.elasticsearch_url,
            cloud_id=settings.elasticsearch_cloud_id,
            api_key=settings.elasticsearch_api_key,
            index_name=settings.elasticsearch_index_name,
            timeout=settings.elasticsearch_request_timeout,
            verify_ssl=settings.elasticsearch_verify_ssl,
        )
    return _elasticsearch_store
