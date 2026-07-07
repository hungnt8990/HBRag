"""ES BM25 cấp văn bản cho thiết kế DOffice 3-DB.

Mỗi văn bản = 1 record. CHỈ lưu thông tin văn bản (mọi trường thô + full noi_dung
đã làm sạch) + ACL để lọc quyền. KHÔNG lưu vector, KHÔNG lưu chunk. Tìm kiếm thuần
BM25 (thế mạnh sẵn có của Elasticsearch cho tiếng Việt khi kết hợp asciifolding +
synonym viết tắt). Vector/semantic do Qdrant đảm nhiệm (2 collection riêng).
"""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING, Any

import httpx

from app.core.config import settings
from app.services.retrieval.retrieval_profile import get_retrieval_profile
from app.services.retrieval.retrieval_shared import es_client_kwargs

if TYPE_CHECKING:
    from app.services.security.security_acl_payload import AclSubject

logger = logging.getLogger(__name__)

# Dùng lại synonyms_set viết tắt tiếng Việt (đẩy qua scripts/sync_es_synonyms.py).
SYNONYMS_SET_NAME = "vi_abbreviations"

# Cấu hình domain (field + boost theo SCHEMA BA, lexicon, limits) đọc từ retrieval profile
# (retrieval_profile.py — 2026-07-06 remap từ tên cũ ky_hieu/trich_yeu/nam/thang).
_PROFILE = get_retrieval_profile()

# Trường text được BM25 doc-level (kèm boost): document_no boost cao vì là định danh
# tra cứu chính; ocr_content = toàn văn.
_TEXT_SEARCH_FIELDS = _PROFILE.doc_text_fields

# Index do job kho AI / nhóm BA quản (mapping riêng) — backend TUYỆT ĐỐI không PUT
# mapping/ghi đè: chỉ đọc (search). Xem run_kho_chunk.py / kho_client.py.
_PROTECTED_INDEX_PREFIX = "kho_ai_dung_chung"


def _is_protected_index(index_name: str) -> bool:
    return index_name.startswith(_PROTECTED_INDEX_PREFIX)

_NOI_DUNG_BODY_START_RE = re.compile(
    r"(?:\n|^)(?:TỔNG CÔNG TY|TẬP ĐOÀN|CÔNG TY|ĐẢNG BỘ|ỦY BAN|BAN CHẤP HÀNH|Số\s*:)",
    re.IGNORECASE,
)
_NOI_DUNG_METADATA_LINE_RE = re.compile(
    r"^(?:THÔNG TIN VĂN BẢN DOFFICE|ID_VB|Số/ký hiệu văn bản|Ngày văn bản|Trích yếu|"
    r"Nơi ban hành|Người ký|Tên file|Đường dẫn|Năm|Tháng)\s*:?",
    re.IGNORECASE,
)
_NOI_DUNG_FOOTER_RE = re.compile(
    r"(?:\n\s*Nơi nhận\s*:.*|\n\s*Lưu\s*:.*)$",
    re.IGNORECASE | re.DOTALL,
)


def extract_doffice_body_text(noi_dung: str | None) -> str:
    """Strip DOffice metadata preamble so BM25 can score the real formal-document body."""
    text = str(noi_dung or "").strip()
    if not text:
        return ""
    match = _NOI_DUNG_BODY_START_RE.search(text)
    if match:
        text = text[match.start():].strip()
    else:
        kept = [
            line for line in text.splitlines()
            if not _NOI_DUNG_METADATA_LINE_RE.match(line.strip())
        ]
        text = "\n".join(kept).strip()
    text = _NOI_DUNG_FOOTER_RE.sub("", text).strip()
    return text or str(noi_dung or "").strip()


def _vi_analysis() -> dict[str, Any]:
    return {
        "filter": {
            "vi_synonyms": {
                "type": "synonym_graph",
                "synonyms_set": SYNONYMS_SET_NAME,
                "updateable": True,
            },
            "vi_doffice_boilerplate_stop": {
                "type": "stop",
                "stopwords": [
                    "thong", "tin", "van", "ban", "doffice", "id", "vb",
                    "so", "ky", "hieu", "ngay", "trich", "yeu", "noi",
                    "hanh", "nguoi", "ky", "ten", "file", "duong", "dan",
                    "nam", "thang",
                ],
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
            "vi_bm25_body": {
                "type": "custom",
                "tokenizer": "standard",
                "filter": ["lowercase", "asciifolding", "vi_doffice_boilerplate_stop"],
            },
            "vi_bm25_body_search": {
                "type": "custom",
                "tokenizer": "standard",
                "filter": ["lowercase", "asciifolding", "vi_synonyms", "vi_doffice_boilerplate_stop"],
            },
        },
    }


class DofficeBm25DocumentStore:
    """Index ES BM25 cấp văn bản: 1 record/văn bản, không vector, không chunk."""

    def __init__(self, *, url: str | None = None, index_name: str | None = None, timeout_seconds: float = 30.0) -> None:
        self.url = (url or settings.elasticsearch_url).rstrip("/")
        self.index_name = index_name or _PROFILE.es_doc_index or settings.doffice_documents_index_name
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
            "noi_dung_body": {
                "type": "text",
                "analyzer": "vi_bm25_body",
                "search_analyzer": "vi_bm25_body_search",
                "index_options": "offsets",
            },
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
        async with httpx.AsyncClient(**es_client_kwargs(self.timeout_seconds)) as client:
            resp = await client.head(f"{self.url}/{self.index_name}")
            if resp.status_code == 200:
                self._index_ready = True
                return
            # Index BA/job quản: KHÔNG PUT mapping (mapping của họ khác định nghĩa cũ ở đây).
            # Thiếu index -> search trả rỗng (404), KHÔNG tự tạo sai mapping.
            if _is_protected_index(self.index_name):
                logger.warning(
                    "Index %s (BA/job quản) chưa tồn tại — bỏ qua tạo, search sẽ trả rỗng.",
                    self.index_name,
                )
                return
            resp = await client.put(f"{self.url}/{self.index_name}", json=self._index_definition())
            if resp.status_code < 400 or "resource_already_exists" in resp.text:
                self._index_ready = True
                return
            raise RuntimeError(
                f"Tạo index {self.index_name} lỗi: HTTP {resp.status_code} {resp.text[:300]}"
            )

    async def delete_index(self) -> None:
        if _is_protected_index(self.index_name):
            raise RuntimeError(f"Index {self.index_name} do BA/job quản — không cho xoá từ store này.")
        self._index_ready = False
        async with httpx.AsyncClient(**es_client_kwargs(self.timeout_seconds)) as client:
            resp = await client.delete(f"{self.url}/{self.index_name}")
        if resp.status_code not in (200, 404):
            raise RuntimeError(
                f"Xóa index {self.index_name} lỗi: HTTP {resp.status_code} {resp.text[:300]}"
            )

    async def fetch_doc_sources(
        self,
        document_ids: list[str],
        *,
        acl_subject: AclSubject | None = None,
    ) -> dict[str, dict[str, Any]]:
        """Lấy metadata văn bản (schema BA) theo ``document_id`` từ index nguồn — CHỈ ĐỌC.

        Dùng để enrich candidate fusion (ES chunk/Qdrant payload KHÔNG có document_no/
        signer/summary). Trả map ``document_id -> _source`` (đã lọc ACL)."""
        ids = [str(d) for d in document_ids if d]
        if not ids:
            return {}
        filters: list[dict[str, Any]] = [{"terms": {"document_id": ids}}]
        if acl_subject is not None:
            from app.services.security.security_acl_payload import build_es_acl_filter_flat

            clause = build_es_acl_filter_flat(acl_subject)
            if clause is not None:
                filters.append(clause)
        body = {
            "size": len(ids),
            "_source": list(_PROFILE.doc_source_fields),
            "query": {"bool": {"filter": filters}},
        }
        from app.services.retrieval.retrieval_shared import get_es_http_client

        resp = await get_es_http_client().post(f"{self.url}/{self.index_name}/_search", json=body)
        if resp.status_code == 404:
            return {}
        if resp.status_code >= 400:
            raise RuntimeError(f"fetch_doc_sources ES lỗi: HTTP {resp.status_code} {resp.text[:300]}")
        hits = resp.json().get("hits", {}).get("hits", [])
        out: dict[str, dict[str, Any]] = {}
        for hit in hits:
            source = hit.get("_source") or {}
            doc_id = str(source.get("document_id") or "")
            if doc_id and doc_id not in out:
                out[doc_id] = source
        return out

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
        if _is_protected_index(self.index_name):
            raise RuntimeError(
                f"Index {self.index_name} do BA/job quản (mapping strict) — không ghi từ store này."
            )
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
            record["noi_dung_body"] = extract_doffice_body_text(noi_dung_clean)
        record["acl_subjects"] = acl_subjects
        record["acl_deny"] = acl_deny
        if acl_ver:
            record["acl_ver"] = acl_ver
        async with httpx.AsyncClient(**es_client_kwargs(self.timeout_seconds)) as client:
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
        async with httpx.AsyncClient(**es_client_kwargs(self.timeout_seconds)) as client:
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
        async with httpx.AsyncClient(**es_client_kwargs(self.timeout_seconds)) as client:
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
        async with httpx.AsyncClient(**es_client_kwargs(self.timeout_seconds)) as client:
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
        acl_subject: AclSubject | None = None,
        years: list[int] | None = None,
        months: list[int] | None = None,
    ) -> list[dict[str, Any]]:
        """BM25 thuần trên các trường văn bản (schema BA), lọc ACL cứng. Trả [{document_id,_score,...}]."""
        await self.ensure_index()
        filters: list[dict[str, Any]] = []
        if acl_subject is not None:
            from app.services.security.security_acl_payload import build_es_acl_filter_flat

            clause = build_es_acl_filter_flat(acl_subject)
            if clause is not None:
                filters.append(clause)
        if years:
            filters.append({"terms": {_PROFILE.year_field: [int(y) for y in years]}})
        if months:
            filters.append({"terms": {_PROFILE.month_field: [int(m) for m in months]}})
        should = [
            {"match": {field: {"query": query, "boost": boost}}}
            for field, boost in _TEXT_SEARCH_FIELDS
        ]
        body = {
            "size": top_n,
            "_source": list(_PROFILE.doc_source_fields),
            "query": {"bool": {"should": should, "minimum_should_match": 1, "filter": filters}},
        }
        async with httpx.AsyncClient(**es_client_kwargs(self.timeout_seconds)) as client:
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


# Trường text BM25 cấp CHUNK (boost) — SCHEMA BA index `kho_ai_dung_chung_chunk`:
# chunk_text là chính, kèm ngữ cảnh heading (section_path) + tiêu đề văn bản (title)
# + tên bảng (table_context). Chunk index KHÔNG có document_no — identifier lo ở doc-level.
_CHUNK_TEXT_SEARCH_FIELDS = _PROFILE.chunk_text_fields


def _issue_date_range_filter(years: list[int], months: list[int] | None) -> dict[str, Any]:
    """Dựng filter range trên ``issue_date`` (kiểu date) từ năm (+tháng tuỳ chọn)."""
    import calendar

    clauses: list[dict[str, Any]] = []
    months_norm = sorted({int(m) for m in months if 1 <= int(m) <= 12}) if months else None
    for year in sorted({int(y) for y in years}):
        if months_norm:
            for month in months_norm:
                last_day = calendar.monthrange(year, month)[1]
                clauses.append({
                    "range": {"issue_date": {
                        "gte": f"{year}-{month:02d}-01",
                        "lte": f"{year}-{month:02d}-{last_day:02d}",
                    }}
                })
        else:
            clauses.append({"range": {"issue_date": {"gte": f"{year}-01-01", "lte": f"{year}-12-31"}}})
    if len(clauses) == 1:
        return clauses[0]
    return {"bool": {"should": clauses, "minimum_should_match": 1}}


class DofficeChunkBm25Store:
    """Index ES BM25 cấp CHUNK (nhánh 2): mỗi chunk = 1 record + ACL nén.

    Dùng để BM25 đúng ĐOẠN/căn cứ (bổ sung cho nhánh full doc-level). Không vector.
    """

    def __init__(self, *, url: str | None = None, index_name: str | None = None, timeout_seconds: float = 30.0) -> None:
        self.url = (url or settings.elasticsearch_url).rstrip("/")
        self.index_name = index_name or _PROFILE.es_chunk_index or settings.doffice_chunks_index_name
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
        async with httpx.AsyncClient(**es_client_kwargs(self.timeout_seconds)) as client:
            resp = await client.head(f"{self.url}/{self.index_name}")
            if resp.status_code == 200:
                self._index_ready = True
                return
            # Index chunk kho AI do job run_kho_chunk tạo (mapping ở kho_client.py) —
            # backend KHÔNG PUT mapping cũ đè lên. Thiếu index -> search trả rỗng.
            if _is_protected_index(self.index_name):
                logger.warning(
                    "Index %s (job quản) chưa tồn tại — bỏ qua tạo, search sẽ trả rỗng.",
                    self.index_name,
                )
                return
            resp = await client.put(f"{self.url}/{self.index_name}", json=self._index_definition())
            if resp.status_code < 400 or "resource_already_exists" in resp.text:
                self._index_ready = True
                return
            raise RuntimeError(
                f"Tạo index {self.index_name} lỗi: HTTP {resp.status_code} {resp.text[:300]}"
            )

    async def delete_index(self) -> None:
        if _is_protected_index(self.index_name):
            raise RuntimeError(f"Index {self.index_name} do job quản — không cho xoá từ store này.")
        self._index_ready = False
        async with httpx.AsyncClient(**es_client_kwargs(self.timeout_seconds)) as client:
            resp = await client.delete(f"{self.url}/{self.index_name}")
        if resp.status_code not in (200, 404):
            raise RuntimeError(
                f"Xóa index {self.index_name} lỗi: HTTP {resp.status_code} {resp.text[:300]}"
            )

    async def delete_by_id_vb(self, id_vb: str) -> None:
        """Xoá MỌI chunk của 1 văn bản (idempotent trước khi ghi lại)."""
        await self.ensure_index()
        body = {"query": {"term": {"id_vb": str(id_vb)}}}
        async with httpx.AsyncClient(**es_client_kwargs(self.timeout_seconds)) as client:
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
        if _is_protected_index(self.index_name):
            raise RuntimeError(
                f"Index {self.index_name} do job kho AI ghi (schema BA) — không ghi từ store này."
            )
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
        async with httpx.AsyncClient(**es_client_kwargs(self.timeout_seconds)) as client:
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
        acl_subject: AclSubject | None = None,
        ensure: bool = True,
        document_ids: set[str] | None = None,
        years: list[int] | None = None,
        months: list[int] | None = None,
    ) -> list[dict[str, Any]]:
        """BM25 cấp chunk + lọc ACL cứng. Trả [{document_id,id_vb,chunk_id,chunk_text,_score,...}].

        ``document_ids``: giới hạn trong danh sách văn bản (``document_id``) — dùng cho chat trên
        nhóm văn bản; ``None`` = không giới hạn (chỉ ACL).
        """
        if ensure:
            await self.ensure_index()
        filters: list[dict[str, Any]] = []
        if acl_subject is not None:
            from app.services.security.security_acl_payload import build_es_acl_filter_flat

            clause = build_es_acl_filter_flat(acl_subject)
            if clause is not None:
                filters.append(clause)
        if document_ids:
            filters.append({"terms": {"document_id": [str(d) for d in document_ids]}})
        if years:
            filters.append(_issue_date_range_filter(years, months))
        elif months:
            # Chunk index chỉ có issue_date (date) — lọc "tháng X mọi năm" không biểu diễn
            # được bằng range; bỏ qua (nhánh doc-level lọc được qua issue_month).
            logger.debug("search_chunks: bỏ qua filter tháng %s vì không kèm năm", months)
        fields = [f"{field}^{boost}" for field, boost in _CHUNK_TEXT_SEARCH_FIELDS]
        should: list[dict[str, Any]] = [
            {
                "multi_match": {
                    "query": query,
                    "type": "best_fields",
                    "fields": fields,
                    "operator": "and",
                    "boost": 2.5,
                }
            },
            {
                "multi_match": {
                    "query": query,
                    "type": "best_fields",
                    "fields": fields,
                    "operator": "or",
                    "minimum_should_match": "2<75%",
                    "boost": 0.5,
                }
            },
        ]
        if len(query.split()) >= 2:
            should.insert(
                0,
                {
                    "multi_match": {
                        "query": query,
                        "type": "phrase",
                        "fields": list(_PROFILE.chunk_phrase_fields),
                        "boost": 5.0,
                    }
                },
            )
        query_block: dict[str, Any] = {"bool": {"should": should, "minimum_should_match": 1, "filter": filters}}
        body = {
            "size": top_n,
            "_source": list(_PROFILE.chunk_source_fields),
            "highlight": {
                "fields": {
                    "chunk_text": {
                        "fragment_size": 220,
                        "number_of_fragments": 2,
                        "pre_tags": ["<mark>"],
                        "post_tags": ["</mark>"],
                    }
                },
                "require_field_match": False,
            },
            "query": {
                "function_score": {
                    "query": query_block,
                    "functions": [
                        {"filter": {"term": {"chunk_type": chunk_type}}, "weight": weight}
                        for chunk_type, weight in _PROFILE.chunk_type_weights
                    ],
                    "boost_mode": "multiply",
                    "score_mode": "multiply",
                }
            },
        }
        # Hot path search: dùng client keep-alive CHUNG theo loop (không bắt tay TCP mỗi call).
        from app.services.retrieval.retrieval_shared import get_es_http_client

        resp = await get_es_http_client().post(f"{self.url}/{self.index_name}/_search", json=body)
        if resp.status_code == 404:
            return []
        if resp.status_code >= 400:
            raise RuntimeError(f"search_chunks ES lỗi: HTTP {resp.status_code} {resp.text[:300]}")
        hits = resp.json().get("hits", {}).get("hits", [])
        results: list[dict[str, Any]] = []
        for hit in hits:
            source = hit.get("_source") or {}
            if source.get("document_id"):
                results.append({**source, "_score": hit.get("_score"), "highlight": hit.get("highlight") or {}})
        return results

    async def fetch_context_chunks(
        self,
        seeds: list[tuple[str, int]],
        *,
        acl_subject: AclSubject | None = None,
        parent_chunk_types: frozenset[str] | set[str] = frozenset(),
        parent_lookback: int = 40,
    ) -> list[dict[str, Any]]:
        """Lấy chunk ngữ cảnh từ ES cho các seed ``(id_full, chunk_order)``.

        Mỗi seed lấy: hàng xóm ``chunk_order`` trong [n-1, n+1] + các chunk CHA
        (``chunk_type`` ∈ parent_chunk_types, ``chunk_order`` ∈ [n-lookback, n)).
        1 request ``_msearch`` cho mọi seed; luôn kèm ACL (không nới quyền dù cùng
        văn bản). Trả list kết quả THEO THỨ TỰ seed, mỗi phần tử = list hit
        (đã sort theo chunk_order tăng dần)."""
        if not seeds:
            return []
        acl_clause = None
        if acl_subject is not None:
            from app.services.security.security_acl_payload import build_es_acl_filter_flat

            acl_clause = build_es_acl_filter_flat(acl_subject)
        lines: list[str] = []
        for id_full, order in seeds:
            n = int(order)
            blocks: list[dict[str, Any]] = [
                {"range": {"chunk_order": {"gte": n - 1, "lte": n + 1}}},
            ]
            if parent_chunk_types and n > 0:
                blocks.append({
                    "bool": {"filter": [
                        {"terms": {"chunk_type": sorted(parent_chunk_types)}},
                        {"range": {"chunk_order": {"gte": max(0, n - parent_lookback), "lt": n}}},
                    ]}
                })
            filters: list[dict[str, Any]] = [{"term": {"id_full": str(id_full)}}]
            if acl_clause is not None:
                filters.append(acl_clause)
            body = {
                "size": parent_lookback + 4,
                "_source": [
                    "document_id", "id", "id_full", "chunk_id", "chunk_order",
                    "chunk_type", "chunk_text", "section_path", "title", "content_hash",
                ],
                "sort": [{"chunk_order": "asc"}],
                "query": {"bool": {
                    "filter": filters,
                    "should": blocks,
                    "minimum_should_match": 1,
                }},
            }
            lines.append(json.dumps({"index": self.index_name}))
            lines.append(json.dumps(body, ensure_ascii=False))
        from app.services.retrieval.retrieval_shared import get_es_http_client

        resp = await get_es_http_client().post(
            f"{self.url}/_msearch",
            content=("\n".join(lines) + "\n").encode("utf-8"),
            headers={"Content-Type": "application/x-ndjson"},
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"fetch_context_chunks ES lỗi: HTTP {resp.status_code} {resp.text[:300]}")
        responses = resp.json().get("responses", [])
        out: list[list[dict[str, Any]]] = []
        for item in responses:
            hits = (item.get("hits") or {}).get("hits", []) if isinstance(item, dict) else []
            out.append([hit.get("_source") or {} for hit in hits])
        # msearch trả đúng số response = số seed; nếu lệch (lỗi cục bộ) thì đệm rỗng.
        while len(out) < len(seeds):
            out.append([])
        return out
