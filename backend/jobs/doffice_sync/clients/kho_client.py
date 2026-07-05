"""Client ES cho nhánh KHO AI DÙNG CHUNG (``kho_ai_dung_chung`` + ``kho_ai_dung_chung_chunk``).

Index nguồn ``kho_ai_dung_chung`` do nhóm BA quản (mapping strict, ACL nén sẵn trong
``acl_subjects``/``acl_deny``, ``id`` = UUIDv7). Job chỉ ĐỌC nguồn; nhánh chunk
``kho_ai_dung_chung_chunk`` do job tạo + ghi (id chunk = UUIDv7 mới, ``id_full`` = id doc nguồn).

Kết nối: mặc định lấy từ app settings ``elasticsearch_url``/``elasticsearch_username``/
``elasticsearch_password``/``elasticsearch_verify_ssl`` (tài khoản full — role ``doffice``
KHÔNG có quyền trên ``kho_ai_dung_chung*``, đã kiểm chứng 403).
"""

from __future__ import annotations

import json
import os
import secrets
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx

_UUID7_LAST: list[int] = [0]  # ms cuối đã cấp — chống trùng thứ tự trong cùng ms (best-effort)


def uuid7() -> str:
    """UUID v7 (RFC 9562): 48 bit unix-ms + 4 bit version + 74 bit ngẫu nhiên.

    Python 3.11 chưa có ``uuid.uuid7`` -> tự sinh. Sortable theo thời gian, hợp lệ làm
    point id Qdrant lẫn ``_id`` ES.
    """
    ms = time.time_ns() // 1_000_000
    if ms <= _UUID7_LAST[0]:
        ms = _UUID7_LAST[0] + 1
    _UUID7_LAST[0] = ms
    rand_a = secrets.randbits(12)
    rand_b = secrets.randbits(62)
    value = (ms & ((1 << 48) - 1)) << 80
    value |= 0x7 << 76          # version 7
    value |= rand_a << 64
    value |= 0b10 << 62         # variant RFC 4122
    value |= rand_b
    hexs = f"{value:032x}"
    return f"{hexs[0:8]}-{hexs[8:12]}-{hexs[12:16]}-{hexs[16:20]}-{hexs[20:32]}"


# ─── Spec payload/record 2026-07-05 (rev2) — DANH SÁCH FIELD CHÍNH XÁC do nghiệp vụ chốt ───
# Field DOC-LEVEL (lấy TỪ doc nguồn kho_ai_dung_chung) LƯU vào record ES ``kho_ai_dung_chung_chunk``.
# (id/id_full/chunk_id + field chunk-level gán riêng khi build record). ``security_level`` chưa có
# ở nguồn hôm nay -> tự xuất hiện khi API bổ sung ("đi trước 1 bước", field rỗng KHÔNG ghi).
KHO_ES_CHUNK_DOC_FIELDS: tuple[str, ...] = (
    "document_id", "title", "source_system", "doc_group", "doc_type", "doc_category",
    "issue_date", "owner_department_id", "security_level", "acl_subjects", "acl_deny",
)
# Field CHUNK-LEVEL lưu record ES chunk: chunk_order, chunk_text, chunk_type, section_path,
# content_hash, table_context (table_context cần để dựng payload Qdrant chunk ở job 2).

# Payload Qdrant docmeta (Col2 ``hbrag_doffice_docmeta``) — point id = ``id`` (UUIDv7 doc nguồn).
KHO_DOCMETA_PAYLOAD_FIELDS: tuple[str, ...] = (
    "id", "document_id", "source_system", "issuer_org_id", "issuer_org_name",
    "doc_group", "doc_type", "doc_category", "keywords", "issue_date", "expiry_date",
    "owner_department_id", "security_level", "acl_subjects", "acl_deny",
    "related_document_ids", "reference_document_ids", "priority",
)
# Payload Qdrant chunk (Col1 ``hbrag_doffice_chunks``) = phần DOC-LEVEL (từ doc nguồn, hoặc record
# ES chunk nếu doc nguồn đã mất) + phần CHUNK-LEVEL (từ record ES chunk). Point id = ``id`` chunk.
KHO_CHUNK_DOC_PAYLOAD_FIELDS: tuple[str, ...] = (
    "document_id", "source_system", "doc_group", "doc_type", "doc_category",
    "issue_date", "owner_department_id", "security_level", "acl_subjects", "acl_deny",
    "related_document_ids", "reference_document_ids", "priority",
)
KHO_CHUNK_CHUNK_PAYLOAD_FIELDS: tuple[str, ...] = (
    "id", "id_full", "chunk_id", "chunk_order", "chunk_text", "chunk_type",
    "table_context", "section_path", "content_hash",
)


RESET_WIPE = 9  # --reset 9: reset stage tương ứng của job (KHÔNG đụng nguồn kho_ai_dung_chung)
KHO_CHUNK_JOB_NAME = "kho_ai_chunk"  # tiền tố job_name checkpoint (mọi phạm vi issuer_org)


async def reset_es_chunk_stage(client: "KhoAiEsClient") -> None:
    """Reset STAGE CHUNK (dùng ở ``run_kho_chunk --reset 9``).

    Xoá + tạo lại RỖNG index ES ``kho_ai_dung_chung_chunk``. KHÔNG đụng Qdrant (2 collection
    do ``run_kho_qdrant`` quản), KHÔNG đụng PostgreSQL, KHÔNG đụng nguồn ``kho_ai_dung_chung``
    (guard tên trong ``delete_chunk_index``). Trạng thái đã/chưa chunk suy TỪ ES nên xoá index
    chunk = reset sạch.
    """
    await client.delete_chunk_index()
    await client.ensure_chunk_index()


class KhoAiEsClient:
    """Đọc ``kho_ai_dung_chung`` (scroll/fetch) + tạo/ghi/đánh dấu ``kho_ai_dung_chung_chunk``."""

    def __init__(
        self,
        *,
        url: str | None = None,
        user: str | None = None,
        password: str | None = None,
        verify_ssl: bool | None = None,
        index: str | None = None,
        chunk_index: str | None = None,
        timeout_seconds: float = 60.0,
    ) -> None:
        from app.core.config import settings

        self._url = (url or os.getenv("KHO_ES_URL") or settings.elasticsearch_url).rstrip("/")
        user = user if user is not None else (os.getenv("KHO_ES_USER") or settings.elasticsearch_username)
        password = password if password is not None else (
            os.getenv("KHO_ES_PASSWORD") or settings.elasticsearch_password
        )
        self._auth = (user, password or "") if user else None
        self._verify = settings.elasticsearch_verify_ssl if verify_ssl is None else verify_ssl
        self.index = index or settings.doffice_documents_index_name
        self.chunk_index = chunk_index or settings.doffice_chunks_index_name
        self._timeout = timeout_seconds
        self._chunk_index_ready = False

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(verify=self._verify, auth=self._auth, timeout=self._timeout)

    @staticmethod
    def _raise(resp: httpx.Response, what: str) -> None:
        if resp.status_code >= 400:
            raise RuntimeError(f"{what} lỗi: HTTP {resp.status_code} {resp.text[:300]}")

    # ------------------------------------------------------------------ nguồn
    def _doc_query(
        self,
        *,
        issuer_org_filter: list[str] | None,
        updated_after: str | None,
    ) -> dict[str, Any]:
        filters: list[dict[str, Any]] = []
        if updated_after:
            filters.append({"range": {"updated_at": {"gte": updated_after}}})
        if issuer_org_filter:
            filters.append({"terms": {"issuer_org_id": [str(v) for v in issuer_org_filter]}})
        return {"bool": {"filter": filters}} if filters else {"match_all": {}}

    async def count_documents(
        self,
        *,
        issuer_org_filter: list[str] | None = None,
        updated_after: str | None = None,
    ) -> int:
        body = {"query": self._doc_query(issuer_org_filter=issuer_org_filter, updated_after=updated_after)}
        async with self._client() as client:
            resp = await client.post(f"{self._url}/{self.index}/_count", json=body)
            self._raise(resp, f"count {self.index}")
            return int(resp.json().get("count") or 0)

    async def scroll_batches(
        self,
        *,
        batch_size: int = 200,
        issuer_org_filter: list[str] | None = None,
        updated_after: str | None = None,
        search_after: list | None = None,
    ) -> AsyncIterator[tuple[list[dict[str, Any]], list | None]]:
        """Yield ``(docs, sort_values)`` — ``sort_values=None`` ở batch cuối.

        Sort ổn định ``updated_at asc, id asc`` để checkpoint search_after resume được.
        """
        async with self._client() as client:
            while True:
                body: dict[str, Any] = {
                    "size": batch_size,
                    "query": self._doc_query(
                        issuer_org_filter=issuer_org_filter, updated_after=updated_after
                    ),
                    "sort": [{"updated_at": "asc"}, {"id": "asc"}],
                }
                if search_after:
                    body["search_after"] = search_after
                resp = await client.post(f"{self._url}/{self.index}/_search", json=body)
                self._raise(resp, f"scroll {self.index}")
                hits = resp.json().get("hits", {}).get("hits", [])
                if not hits:
                    return
                docs = [h.get("_source") or {} for h in hits]
                last_sort = hits[-1].get("sort")
                if len(hits) < batch_size:
                    yield docs, None
                    return
                yield docs, last_sort
                search_after = last_sort

    async def fetch_by_id(self, ids: list[str]) -> list[dict[str, Any]]:
        """Lấy doc nguồn theo field ``id`` (UUIDv7). Lưu ý ``_id`` ES là dạng khác."""
        if not ids:
            return []
        body = {"size": len(ids), "query": {"terms": {"id": [str(v) for v in ids]}}}
        async with self._client() as client:
            resp = await client.post(f"{self._url}/{self.index}/_search", json=body)
            self._raise(resp, f"fetch {self.index}")
        return [h.get("_source") or {} for h in resp.json().get("hits", {}).get("hits", [])]

    async def existing_chunk_id_full(self, id_full_list: list[str]) -> set[str]:
        """Trong ``id_full_list``, trả tập ĐÃ có chunk trong ``kho_ai_dung_chung_chunk``.

        Dùng để xác định văn bản nào CHƯA chunk (không cần checkpoint PostgreSQL): doc nào
        không nằm trong tập trả về = chưa có chunk -> cần chunk.
        """
        if not id_full_list:
            return set()
        await self.ensure_chunk_index()
        body = {
            "size": 0,
            "query": {"terms": {"id_full": [str(v) for v in id_full_list]}},
            "aggs": {"ids": {"terms": {"field": "id_full", "size": len(id_full_list)}}},
        }
        async with self._client() as client:
            resp = await client.post(f"{self._url}/{self.chunk_index}/_search", json=body)
        if resp.status_code == 404:
            return set()
        self._raise(resp, "existing_chunk_id_full")
        buckets = resp.json().get("aggregations", {}).get("ids", {}).get("buckets", [])
        return {str(b["key"]) for b in buckets}

    async def count_chunked_documents(self) -> int:
        """Số văn bản DISTINCT đã có chunk (cardinality trên id_full) — hiển thị tổng đã chunk."""
        await self.ensure_chunk_index()
        body = {"size": 0, "aggs": {"n": {"cardinality": {"field": "id_full"}}}}
        async with self._client() as client:
            resp = await client.post(f"{self._url}/{self.chunk_index}/_search", json=body)
        if resp.status_code == 404:
            return 0
        self._raise(resp, "count_chunked_documents")
        return int(resp.json().get("aggregations", {}).get("n", {}).get("value") or 0)

    async def all_source_ids(
        self,
        *,
        issuer_org_filter: list[str] | None = None,
        on_progress: Any = None,
    ) -> list[str]:
        """TOÀN BỘ field ``id`` (UUIDv7) văn bản nguồn theo phạm vi — CHỈ lấy id (nhẹ).

        Dùng cho BƯỚC KIỂM TRA: đối chiếu với tập đã-chunk để ra danh sách CHƯA chunk.
        """
        ids: list[str] = []
        search_after: list | None = None
        async with self._client() as client:
            while True:
                body: dict[str, Any] = {
                    "size": 1000,
                    "_source": ["id"],
                    "query": self._doc_query(issuer_org_filter=issuer_org_filter, updated_after=None),
                    "sort": [{"id": "asc"}],
                }
                if search_after:
                    body["search_after"] = search_after
                resp = await client.post(f"{self._url}/{self.index}/_search", json=body)
                self._raise(resp, f"scan ids {self.index}")
                hits = resp.json().get("hits", {}).get("hits", [])
                if not hits:
                    break
                for h in hits:
                    sid = (h.get("_source") or {}).get("id")
                    if sid:
                        ids.append(str(sid))
                if on_progress is not None:
                    on_progress(len(ids))
                if len(hits) < 1000:
                    break
                search_after = hits[-1].get("sort")
        return ids

    async def all_chunked_id_full(self, *, on_progress: Any = None) -> set[str]:
        """TOÀN BỘ ``id_full`` DISTINCT đã có chunk (composite agg, phân trang chính xác)."""
        await self.ensure_chunk_index()
        out: set[str] = set()
        after: dict | None = None
        async with self._client() as client:
            while True:
                comp: dict[str, Any] = {
                    "size": 1000,
                    "sources": [{"id_full": {"terms": {"field": "id_full"}}}],
                }
                if after:
                    comp["after"] = after
                body = {"size": 0, "aggs": {"d": {"composite": comp}}}
                resp = await client.post(f"{self._url}/{self.chunk_index}/_search", json=body)
                if resp.status_code == 404:
                    return out
                self._raise(resp, "all_chunked_id_full")
                agg = resp.json().get("aggregations", {}).get("d", {})
                buckets = agg.get("buckets", [])
                for b in buckets:
                    out.add(str(b["key"]["id_full"]))
                if on_progress is not None:
                    on_progress(len(out))
                after = agg.get("after_key")
                if not after or len(buckets) < 1000:
                    break
        return out

    # ------------------------------------------------------------ nhánh chunk
    @staticmethod
    def _chunk_index_definition(*, with_synonyms: bool = True) -> dict[str, Any]:
        """Mapping ``kho_ai_dung_chung_chunk`` — tự chứa analyzer (không phụ thuộc index khác).

        Analyzer đặt tên ``vi_bm25``/``vi_bm25_search`` GIỐNG các index doffice cũ để
        ``DofficeChunkBm25Store.search_chunks`` dùng được nguyên trạng. ``with_synonyms=False``
        là fallback khi cluster chưa có synonyms_set ``vi_abbreviations``.
        """
        from app.core.config import settings

        search_filters = ["lowercase", "asciifolding"] + (["vi_synonyms"] if with_synonyms else [])
        analysis: dict[str, Any] = {
            "analyzer": {
                "vi_bm25": {
                    "type": "custom", "tokenizer": "standard",
                    "filter": ["lowercase", "asciifolding"],
                },
                "vi_bm25_search": {
                    "type": "custom", "tokenizer": "standard", "filter": search_filters,
                },
            },
        }
        if with_synonyms:
            analysis["filter"] = {
                "vi_synonyms": {
                    "type": "synonym_graph",
                    "synonyms_set": "vi_abbreviations",
                    "updateable": True,
                }
            }
        text = {"type": "text", "analyzer": "vi_bm25", "search_analyzer": "vi_bm25_search"}
        kw_text = {**text, "fields": {"raw": {"type": "keyword", "ignore_above": 512}}}
        # CHỈ các field theo spec chốt (2026-07-05 rev2) + 2 field CƠ CHẾ bắt buộc:
        #  - qdrant_indexed: cờ đánh dấu chunk đã embed (yêu cầu "đánh dấu chunk nào đã quét").
        #  - table_context: chunk-level, BẮT BUỘC để job 2 dựng payload Qdrant chunk (spec Qdrant
        #    chunk có table_context nhưng job 2 chỉ đọc từ record ES này -> phải lưu ở đây).
        properties: dict[str, Any] = {
            "id": {"type": "keyword"},
            "id_full": {"type": "keyword"},
            "document_id": {"type": "keyword"},
            "title": kw_text,
            "source_system": {"type": "keyword"},
            "doc_group": {
                "properties": {
                    "cv_den": {"type": "keyword"},
                    "cv_di": {"type": "keyword"},
                    "cv_noi_bo": {"type": "keyword"},
                }
            },
            "doc_type": {"type": "keyword"},
            "doc_category": {"type": "keyword"},
            "issue_date": {"type": "date"},
            "owner_department_id": {"type": "keyword"},
            "security_level": {"type": "keyword"},
            "acl_subjects": {"type": "keyword", "doc_values": True},
            "acl_deny": {"type": "keyword", "doc_values": True},
            "chunk_id": {"type": "keyword"},
            "chunk_order": {"type": "integer"},
            "chunk_text": {**text, "index_options": "offsets"},
            "chunk_type": {"type": "keyword"},
            "section_path": kw_text,
            "content_hash": {"type": "keyword"},
            # --- cơ chế bắt buộc (ngoài spec hiển thị) ---
            "table_context": kw_text,
            "qdrant_indexed": {"type": "boolean"},
        }
        return {
            "settings": {
                "number_of_shards": settings.elasticsearch_number_of_shards,
                "number_of_replicas": settings.elasticsearch_number_of_replicas,
                "refresh_interval": "30s",
                "analysis": analysis,
            },
            "mappings": {"properties": properties},
        }

    async def ensure_chunk_index(self) -> None:
        """Tạo ``kho_ai_dung_chung_chunk`` nếu chưa có (fallback bỏ synonyms nếu cluster thiếu set)."""
        if self._chunk_index_ready:
            return
        async with self._client() as client:
            resp = await client.head(f"{self._url}/{self.chunk_index}")
            if resp.status_code == 200:
                self._chunk_index_ready = True
                return
            resp = await client.put(
                f"{self._url}/{self.chunk_index}", json=self._chunk_index_definition()
            )
            if resp.status_code >= 400 and "synonym" in resp.text.lower():
                resp = await client.put(
                    f"{self._url}/{self.chunk_index}",
                    json=self._chunk_index_definition(with_synonyms=False),
                )
            if resp.status_code < 400 or "resource_already_exists" in resp.text:
                self._chunk_index_ready = True
                return
            raise RuntimeError(
                f"Tạo index {self.chunk_index} lỗi: HTTP {resp.status_code} {resp.text[:300]}"
            )

    async def delete_chunk_index(self) -> None:
        """Xoá TOÀN BỘ index nhánh chunk (``kho_ai_dung_chung_chunk``) — dùng cho reset.

        ⚠️ CHỈ xoá index chunk do job quản; TUYỆT ĐỐI không đụng index nguồn
        ``kho_ai_dung_chung`` (guard tên bên dưới đề phòng cấu hình sai).
        """
        if self.chunk_index == self.index:
            raise RuntimeError(
                f"Từ chối xoá: chunk_index trùng index nguồn ({self.index!r})."
            )
        self._chunk_index_ready = False
        async with self._client() as client:
            resp = await client.delete(f"{self._url}/{self.chunk_index}")
        if resp.status_code not in (200, 404):
            self._raise(resp, f"delete index {self.chunk_index}")

    async def delete_chunks_by_id_full(self, id_full: str) -> None:
        """Xoá mọi chunk của 1 doc (idempotent trước khi ghi lại khi re-chunk)."""
        await self.ensure_chunk_index()
        body = {"query": {"term": {"id_full": str(id_full)}}}
        async with self._client() as client:
            resp = await client.post(
                f"{self._url}/{self.chunk_index}/_delete_by_query?conflicts=proceed",
                json=body,
            )
        if resp.status_code not in (200, 404):
            self._raise(resp, f"delete_chunks_by_id_full {id_full}")

    async def bulk_upsert_chunks(self, records: list[dict[str, Any]]) -> None:
        """Ghi/đè nhiều chunk (``_id`` = record["id"]) qua ES ``_bulk``."""
        if not records:
            return
        await self.ensure_chunk_index()
        lines: list[str] = []
        for rec in records:
            rec_id = rec.get("id")
            if not rec_id:
                continue
            lines.append(json.dumps({"index": {"_index": self.chunk_index, "_id": rec_id}}))
            lines.append(json.dumps(rec, ensure_ascii=False))
        if not lines:
            return
        async with self._client() as client:
            resp = await client.post(
                f"{self._url}/_bulk",
                content=("\n".join(lines) + "\n").encode("utf-8"),
                headers={"Content-Type": "application/x-ndjson"},
            )
        self._raise(resp, "bulk_upsert_chunks")
        result = resp.json()
        if result.get("errors"):
            first = next(
                (it["index"].get("error") for it in result.get("items", [])
                 if it.get("index", {}).get("error")),
                None,
            )
            raise RuntimeError(f"bulk_upsert_chunks có item lỗi: {str(first)[:300]}")

    # ----------------------------------------------------- job Qdrant (nhánh 2)
    async def refresh_chunk_index(self) -> None:
        """Ép refresh index chunk (refresh_interval 30s) để quét thấy chunk vừa ghi."""
        async with self._client() as client:
            resp = await client.post(f"{self._url}/{self.chunk_index}/_refresh")
        if resp.status_code not in (200, 404):
            self._raise(resp, f"refresh {self.chunk_index}")

    def _pending_chunk_query(self) -> dict[str, Any]:
        # Chưa embed = KHÔNG có cờ qdrant_indexed=true. Phạm vi đơn vị đã lọc ở job 1 (chỉ
        # chunk đúng issuer_org mới tồn tại) nên nhánh Qdrant không cần lọc issuer_org.
        return {"bool": {"must_not": [{"term": {"qdrant_indexed": True}}]}}

    async def count_pending_chunks(self) -> int:
        await self.ensure_chunk_index()
        body = {"query": self._pending_chunk_query()}
        async with self._client() as client:
            resp = await client.post(f"{self._url}/{self.chunk_index}/_count", json=body)
            self._raise(resp, f"count pending {self.chunk_index}")
            return int(resp.json().get("count") or 0)

    async def pending_id_full_page(
        self,
        *,
        page_size: int = 50,
        after_key: dict | None = None,
    ) -> tuple[list[str], dict | None]:
        """Trang danh sách ``id_full`` còn chunk CHƯA embed (composite agg, có after_key)."""
        await self.ensure_chunk_index()
        composite: dict[str, Any] = {
            "size": page_size,
            "sources": [{"id_full": {"terms": {"field": "id_full"}}}],
        }
        if after_key:
            composite["after"] = after_key
        body = {
            "size": 0,
            "query": self._pending_chunk_query(),
            "aggs": {"docs": {"composite": composite}},
        }
        async with self._client() as client:
            resp = await client.post(f"{self._url}/{self.chunk_index}/_search", json=body)
            self._raise(resp, f"pending_id_full {self.chunk_index}")
        agg = resp.json().get("aggregations", {}).get("docs", {})
        buckets = agg.get("buckets", [])
        ids = [b["key"]["id_full"] for b in buckets]
        return ids, agg.get("after_key") if len(buckets) == page_size else None

    async def fetch_chunks_by_id_full(self, id_full: str, *, page_size: int = 500) -> list[dict[str, Any]]:
        """Mọi chunk của 1 doc, sort ``chunk_order asc`` (search_after nếu > page_size)."""
        await self.ensure_chunk_index()
        out: list[dict[str, Any]] = []
        search_after: list | None = None
        async with self._client() as client:
            while True:
                body: dict[str, Any] = {
                    "size": page_size,
                    "query": {"term": {"id_full": str(id_full)}},
                    "sort": [{"chunk_order": "asc"}, {"id": "asc"}],
                }
                if search_after:
                    body["search_after"] = search_after
                resp = await client.post(f"{self._url}/{self.chunk_index}/_search", json=body)
                self._raise(resp, f"fetch_chunks {id_full}")
                hits = resp.json().get("hits", {}).get("hits", [])
                out.extend(h.get("_source") or {} for h in hits)
                if len(hits) < page_size:
                    return out
                search_after = hits[-1].get("sort")

    async def unmark_all_chunks(self) -> int:
        """Bỏ đánh dấu MỌI chunk (qdrant_indexed=false) -> để embed LẠI từ đầu.

        Dùng khi ``run_kho_qdrant --reset 9`` recreate 2 collection Qdrant: chunk trên ES
        vẫn còn (do run_kho_chunk quản), chỉ cần xoá cờ để job quét embed lại. Trả số chunk đã đổi.
        """
        await self.ensure_chunk_index()
        body = {
            "script": {"source": "ctx._source.qdrant_indexed = false", "lang": "painless"},
            "query": {"term": {"qdrant_indexed": True}},
        }
        async with self._client() as client:
            resp = await client.post(
                f"{self._url}/{self.chunk_index}/_update_by_query?conflicts=proceed&refresh=true",
                json=body,
            )
        if resp.status_code == 404:
            return 0
        self._raise(resp, "unmark_all_chunks")
        return int(resp.json().get("updated") or 0)

    async def mark_chunks_indexed(self, chunk_ids: list[str]) -> None:
        """Đánh dấu chunk đã embed vào Qdrant (bulk partial update, cờ qdrant_indexed=true)."""
        if not chunk_ids:
            return
        lines: list[str] = []
        for cid in chunk_ids:
            lines.append(json.dumps({"update": {"_index": self.chunk_index, "_id": cid}}))
            lines.append(json.dumps({"doc": {"qdrant_indexed": True}}))
        async with self._client() as client:
            resp = await client.post(
                f"{self._url}/_bulk",
                content=("\n".join(lines) + "\n").encode("utf-8"),
                headers={"Content-Type": "application/x-ndjson"},
            )
        self._raise(resp, "mark_chunks_indexed")
        if resp.json().get("errors"):
            raise RuntimeError("mark_chunks_indexed có item lỗi (xem log ES).")
