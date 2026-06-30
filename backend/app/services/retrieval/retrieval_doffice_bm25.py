"""ES BM25 cấp văn bản cho thiết kế DOffice 3-DB.

Mỗi văn bản = 1 record. CHỈ lưu thông tin văn bản (mọi trường thô + full noi_dung
đã làm sạch) + ACL để lọc quyền. KHÔNG lưu vector, KHÔNG lưu chunk. Tìm kiếm thuần
BM25 (thế mạnh sẵn có của Elasticsearch cho tiếng Việt khi kết hợp asciifolding +
synonym viết tắt). Vector/semantic do Qdrant đảm nhiệm (2 collection riêng).
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

import httpx

from app.core.config import settings

if TYPE_CHECKING:
    from app.services.security.security_acl_payload import AclSubject

logger = logging.getLogger(__name__)

# Dùng lại synonyms_set viết tắt tiếng Việt (đẩy qua scripts/sync_es_synonyms.py).
SYNONYMS_SET_NAME = "vi_abbreviations"

# Trường text được BM25 (kèm boost khi search). noi_dung full nằm ở đây để tìm trên
# toàn văn; ky_hieu boost cao vì là định danh tra cứu chính.
_TEXT_SEARCH_FIELDS = (
    ("ky_hieu", 6.0),
    ("trich_yeu", 3.0),
    ("tom_tat", 2.0),
    ("noi_ban_hanh", 1.5),
    ("nguoi_ky", 1.0),
    ("ten_file", 1.0),
    ("noi_dung", 1.0),
)


def _vi_analysis() -> dict[str, Any]:
    return {
        "filter": {
            "vi_synonyms": {
                "type": "synonym_graph",
                "synonyms_set": SYNONYMS_SET_NAME,
                "updateable": True,
            },
        },
        "analyzer": {
            "vi_bm25": {
                "type": "custom",
                "tokenizer": "standard",
                "filter": ["lowercase", "asciifolding"],
            },
            "vi_bm25_search": {
                "type": "custom",
                "tokenizer": "standard",
                "filter": ["lowercase", "asciifolding", "vi_synonyms"],
            },
        },
    }


class DofficeBm25DocumentStore:
    """Index ES BM25 cấp văn bản: 1 record/văn bản, không vector, không chunk."""

    def __init__(self, *, url: str | None = None, index_name: str | None = None, timeout_seconds: float = 30.0) -> None:
        self.url = (url or settings.elasticsearch_url).rstrip("/")
        self.index_name = index_name or settings.doffice_documents_index_name
        self.timeout_seconds = timeout_seconds
        self._index_ready = False  # cache: ensure_index chỉ thật sự chạy 1 lần/process

    @staticmethod
    def _index_definition() -> dict[str, Any]:
        text = {"type": "text", "analyzer": "vi_bm25", "search_analyzer": "vi_bm25_search"}
        properties: dict[str, Any] = {
            "document_id": {"type": "keyword"},
            "id_vb": {"type": "keyword"},
            "id_dv_ban_hanh": {"type": "keyword"},
            # ky_hieu vừa keyword (khớp chính xác) vừa text (BM25 mờ).
            "ky_hieu": {"type": "text", "analyzer": "vi_bm25", "fields": {"raw": {"type": "keyword"}}},
            "trich_yeu": text,
            "tom_tat": text,
            "noi_ban_hanh": {"type": "text", "analyzer": "vi_bm25", "fields": {"raw": {"type": "keyword"}}},
            "nguoi_ky": {"type": "text", "analyzer": "vi_bm25", "fields": {"raw": {"type": "keyword"}}},
            "ten_file": {"type": "text", "analyzer": "vi_bm25"},
            "duong_dan": {"type": "keyword"},
            "noi_dung": {**text, "index_options": "offsets"},
            "type_ocr": {"type": "keyword"},
            "nam": {"type": "integer"},
            "thang": {"type": "integer"},
            "ngay_vb": {
                "type": "keyword",
                "fields": {"date": {"type": "date", "format": "yyyy-MM-dd", "ignore_malformed": True}},
            },
            "ngay_tao": {"type": "keyword"},
            "ngay_capnhat": {"type": "keyword"},
            # ACL phẳng — giống Qdrant/chunk cũ để lọc cùng cách.
            "acl_subjects": {"type": "keyword", "doc_values": True},
            "acl_deny": {"type": "keyword", "doc_values": True},
            "acl_ver": {"type": "keyword"},
        }
        return {
            "settings": {
                "number_of_shards": settings.elasticsearch_number_of_shards,
                "number_of_replicas": settings.elasticsearch_number_of_replicas,
                "refresh_interval": "60s",
                "index.queries.cache.enabled": True,
                "analysis": _vi_analysis(),
            },
            "mappings": {"properties": properties},
        }

    async def ensure_index(self) -> None:
        # Cache: bỏ HEAD/PUT lặp lại mỗi lần upsert (upsert_document gọi hàm này mỗi doc ->
        # nếu không cache sẽ là 1 round-trip ES thừa/văn bản, rất chậm khi ES tải nặng).
        if self._index_ready:
            return
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            resp = await client.head(f"{self.url}/{self.index_name}")
            if resp.status_code == 200:
                self._index_ready = True
                return
            resp = await client.put(f"{self.url}/{self.index_name}", json=self._index_definition())
            if resp.status_code < 400 or "resource_already_exists" in resp.text:
                self._index_ready = True
                return
            raise RuntimeError(
                f"Tạo index {self.index_name} lỗi: HTTP {resp.status_code} {resp.text[:300]}"
            )

    async def delete_index(self) -> None:
        self._index_ready = False
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            resp = await client.delete(f"{self.url}/{self.index_name}")
        if resp.status_code not in (200, 404):
            raise RuntimeError(
                f"Xóa index {self.index_name} lỗi: HTTP {resp.status_code} {resp.text[:300]}"
            )

    async def upsert_document(
        self,
        *,
        document_id: str,
        id_vb: str,
        fields: dict[str, Any],
        noi_dung_clean: str | None,
        acl_subjects: list[str],
        acl_deny: list[str],
        acl_ver: str | None = None,
    ) -> None:
        """Ghi/đè 1 record văn bản. ``fields`` = các trường thô (trừ noi_dung);
        ``noi_dung_clean`` = full nội dung đã làm sạch (KHÔNG cắt).

        ``acl_subjects`` = allow_list ["dv_/pb_/nv_"]; ``acl_deny`` = deny_list ["pb_/nv_"]."""
        await self.ensure_index()
        record: dict[str, Any] = {"document_id": document_id, "id_vb": str(id_vb)}
        for key in (
            "ky_hieu", "trich_yeu", "tom_tat", "noi_ban_hanh", "nguoi_ky", "ten_file",
            "duong_dan", "id_dv_ban_hanh", "type_ocr", "nam", "thang", "ngay_vb",
            "ngay_tao", "ngay_capnhat",
        ):
            value = fields.get(key)
            if value not in (None, ""):
                record[key] = value
        if noi_dung_clean:
            record["noi_dung"] = noi_dung_clean
        record["acl_subjects"] = acl_subjects
        record["acl_deny"] = acl_deny
        if acl_ver:
            record["acl_ver"] = acl_ver
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            resp = await client.put(
                f"{self.url}/{self.index_name}/_doc/{id_vb}",
                content=json.dumps(record, ensure_ascii=False).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
        if resp.status_code >= 400:
            raise RuntimeError(
                f"upsert_document ES lỗi id_vb={id_vb}: HTTP {resp.status_code} {resp.text[:300]}"
            )

    async def update_acl(
        self,
        id_vb: str,
        *,
        acl_subjects: list[str],
        acl_deny: list[str],
        acl_ver: str | None = None,
    ) -> None:
        """Partial update CHỈ trường ACL theo id_vb (không đụng nội dung)."""
        doc = {
            "acl_subjects": acl_subjects,
            "acl_deny": acl_deny,
        }
        if acl_ver:
            doc["acl_ver"] = acl_ver
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            resp = await client.post(
                f"{self.url}/{self.index_name}/_update/{id_vb}",
                content=json.dumps({"doc": doc}, ensure_ascii=False).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
        if resp.status_code == 404:
            logger.warning("update_acl: id_vb=%s chưa có trong %s -> bỏ qua", id_vb, self.index_name)
            return
        if resp.status_code >= 400:
            raise RuntimeError(
                f"update_acl ES lỗi id_vb={id_vb}: HTTP {resp.status_code} {resp.text[:300]}"
            )

    async def delete_by_id_vb(self, id_vb: str) -> None:
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            resp = await client.delete(f"{self.url}/{self.index_name}/_doc/{id_vb}")
        if resp.status_code not in (200, 404):
            raise RuntimeError(
                f"delete_by_id_vb ES lỗi id_vb={id_vb}: HTTP {resp.status_code} {resp.text[:300]}"
            )

    async def existing_id_vb(self, id_vb_list: list[str]) -> set[str]:
        if not id_vb_list:
            return set()
        body = {
            "size": len(id_vb_list),
            "_source": ["id_vb"],
            "query": {"terms": {"id_vb": [str(v) for v in id_vb_list]}},
        }
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            resp = await client.post(f"{self.url}/{self.index_name}/_search", json=body)
        if resp.status_code == 404:
            return set()
        if resp.status_code >= 400:
            raise RuntimeError(f"existing_id_vb ES lỗi: HTTP {resp.status_code} {resp.text[:300]}")
        hits = resp.json().get("hits", {}).get("hits", [])
        return {str(h["_source"]["id_vb"]) for h in hits if h.get("_source", {}).get("id_vb")}

    async def search_documents(
        self,
        query: str,
        *,
        top_n: int = 50,
        acl_subject: "AclSubject | None" = None,
    ) -> list[dict[str, Any]]:
        """BM25 thuần trên các trường văn bản, lọc ACL cứng. Trả [{document_id,id_vb,_score,...}]."""
        await self.ensure_index()
        filters: list[dict[str, Any]] = []
        if acl_subject is not None:
            from app.services.security.security_acl_payload import build_es_acl_filter_flat

            clause = build_es_acl_filter_flat(acl_subject)
            if clause is not None:
                filters.append(clause)
        should = [
            {"match": {field: {"query": query, "boost": boost}}}
            for field, boost in _TEXT_SEARCH_FIELDS
        ]
        body = {
            "size": top_n,
            "_source": ["document_id", "id_vb", "ky_hieu", "trich_yeu", "tom_tat", "ngay_vb", "nam"],
            "query": {"bool": {"should": should, "minimum_should_match": 1, "filter": filters}},
        }
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            resp = await client.post(f"{self.url}/{self.index_name}/_search", json=body)
        if resp.status_code == 404:
            return []
        if resp.status_code >= 400:
            raise RuntimeError(f"search_documents ES lỗi: HTTP {resp.status_code} {resp.text[:300]}")
        hits = resp.json().get("hits", {}).get("hits", [])
        results: list[dict[str, Any]] = []
        for hit in hits:
            source = hit.get("_source") or {}
            if source.get("document_id"):
                results.append({**source, "_score": hit.get("_score")})
        return results


# Trường text BM25 cấp CHUNK (boost): chunk_text là chính, kèm ngữ cảnh heading + ký hiệu.
_CHUNK_TEXT_SEARCH_FIELDS = (
    ("chunk_text", 1.0),
    ("section_path", 1.5),
    ("ky_hieu", 4.0),
    ("trich_yeu", 2.0),
)


class DofficeChunkBm25Store:
    """Index ES BM25 cấp CHUNK (nhánh 2): mỗi chunk = 1 record + ACL nén.

    Dùng để BM25 đúng ĐOẠN/căn cứ (bổ sung cho nhánh full doc-level). Không vector.
    """

    def __init__(self, *, url: str | None = None, index_name: str | None = None, timeout_seconds: float = 30.0) -> None:
        self.url = (url or settings.elasticsearch_url).rstrip("/")
        self.index_name = index_name or settings.doffice_chunks_index_name
        self.timeout_seconds = timeout_seconds
        self._index_ready = False

    @staticmethod
    def _index_definition() -> dict[str, Any]:
        text = {"type": "text", "analyzer": "vi_bm25", "search_analyzer": "vi_bm25_search"}
        kw_text = {"type": "text", "analyzer": "vi_bm25", "fields": {"raw": {"type": "keyword"}}}
        properties: dict[str, Any] = {
            "document_id": {"type": "keyword"},
            "id_vb": {"type": "keyword"},
            "chunk_id": {"type": "keyword"},
            "chunk_index": {"type": "integer"},
            "chunk_type": {"type": "keyword"},
            "chunk_text": {**text, "index_options": "offsets"},
            "section_path": kw_text,
            "table_name": kw_text,
            # Doc-level kế thừa (filter + dẫn nguồn).
            "ky_hieu": kw_text,
            "trich_yeu": text,
            "noi_ban_hanh": kw_text,
            "id_dv_ban_hanh": {"type": "keyword"},
            "nam": {"type": "integer"},
            "thang": {"type": "integer"},
            "ngay_vb": {
                "type": "keyword",
                "fields": {"date": {"type": "date", "format": "yyyy-MM-dd", "ignore_malformed": True}},
            },
            # ACL phẳng — lọc cùng cách Qdrant/doc-level.
            "acl_subjects": {"type": "keyword", "doc_values": True},
            "acl_deny": {"type": "keyword", "doc_values": True},
            "acl_ver": {"type": "keyword"},
        }
        return {
            "settings": {
                "number_of_shards": settings.elasticsearch_number_of_shards,
                "number_of_replicas": settings.elasticsearch_number_of_replicas,
                "refresh_interval": "60s",
                "index.queries.cache.enabled": True,
                "analysis": _vi_analysis(),
            },
            "mappings": {"properties": properties},
        }

    async def ensure_index(self) -> None:
        if self._index_ready:
            return
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            resp = await client.head(f"{self.url}/{self.index_name}")
            if resp.status_code == 200:
                self._index_ready = True
                return
            resp = await client.put(f"{self.url}/{self.index_name}", json=self._index_definition())
            if resp.status_code < 400 or "resource_already_exists" in resp.text:
                self._index_ready = True
                return
            raise RuntimeError(
                f"Tạo index {self.index_name} lỗi: HTTP {resp.status_code} {resp.text[:300]}"
            )

    async def delete_index(self) -> None:
        self._index_ready = False
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            resp = await client.delete(f"{self.url}/{self.index_name}")
        if resp.status_code not in (200, 404):
            raise RuntimeError(
                f"Xóa index {self.index_name} lỗi: HTTP {resp.status_code} {resp.text[:300]}"
            )

    async def delete_by_id_vb(self, id_vb: str) -> None:
        """Xoá MỌI chunk của 1 văn bản (idempotent trước khi ghi lại)."""
        await self.ensure_index()
        body = {"query": {"term": {"id_vb": str(id_vb)}}}
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            resp = await client.post(
                f"{self.url}/{self.index_name}/_delete_by_query?conflicts=proceed",
                content=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
        if resp.status_code not in (200, 404):
            raise RuntimeError(
                f"delete_by_id_vb (chunk) ES lỗi id_vb={id_vb}: HTTP {resp.status_code} {resp.text[:300]}"
            )

    async def bulk_upsert_chunks(self, records: list[dict[str, Any]]) -> None:
        """Ghi/đè nhiều chunk (mỗi record phải có ``chunk_id`` làm _id) qua ES _bulk."""
        if not records:
            return
        await self.ensure_index()
        lines: list[str] = []
        for rec in records:
            chunk_id = rec.get("chunk_id")
            if not chunk_id:
                continue
            lines.append(json.dumps({"index": {"_index": self.index_name, "_id": chunk_id}}))
            lines.append(json.dumps(rec, ensure_ascii=False))
        if not lines:
            return
        body = "\n".join(lines) + "\n"
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            resp = await client.post(
                f"{self.url}/_bulk",
                content=body.encode("utf-8"),
                headers={"Content-Type": "application/x-ndjson"},
            )
        if resp.status_code >= 400:
            raise RuntimeError(
                f"bulk_upsert_chunks ES lỗi: HTTP {resp.status_code} {resp.text[:300]}"
            )
        result = resp.json()
        if result.get("errors"):
            first = next(
                (it["index"].get("error") for it in result.get("items", []) if it.get("index", {}).get("error")),
                None,
            )
            raise RuntimeError(f"bulk_upsert_chunks ES có lỗi item: {str(first)[:300]}")

    async def search_chunks(
        self,
        query: str,
        *,
        top_n: int = 50,
        acl_subject: "AclSubject | None" = None,
    ) -> list[dict[str, Any]]:
        """BM25 cấp chunk + lọc ACL cứng. Trả [{document_id,id_vb,chunk_id,chunk_text,_score,...}]."""
        await self.ensure_index()
        filters: list[dict[str, Any]] = []
        if acl_subject is not None:
            from app.services.security.security_acl_payload import build_es_acl_filter_flat

            clause = build_es_acl_filter_flat(acl_subject)
            if clause is not None:
                filters.append(clause)
        should = [
            {"match": {field: {"query": query, "boost": boost}}}
            for field, boost in _CHUNK_TEXT_SEARCH_FIELDS
        ]
        body = {
            "size": top_n,
            "_source": ["document_id", "id_vb", "chunk_id", "chunk_index", "chunk_type", "chunk_text", "ky_hieu", "trich_yeu", "ngay_vb"],
            "query": {"bool": {"should": should, "minimum_should_match": 1, "filter": filters}},
        }
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            resp = await client.post(f"{self.url}/{self.index_name}/_search", json=body)
        if resp.status_code == 404:
            return []
        if resp.status_code >= 400:
            raise RuntimeError(f"search_chunks ES lỗi: HTTP {resp.status_code} {resp.text[:300]}")
        hits = resp.json().get("hits", {}).get("hits", [])
        results: list[dict[str, Any]] = []
        for hit in hits:
            source = hit.get("_source") or {}
            if source.get("document_id"):
                results.append({**source, "_score": hit.get("_score")})
        return results
