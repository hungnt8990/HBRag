"""Two-stage retrieval — Stage-1 tìm top-N document trước khi search chunk.

Với ~20M chunks, search trực tiếp toàn bộ chunk vừa chậm vừa tốn. Ý tưởng:
- Stage 1: search trên index document-level (`hbrag_documents_v1`, 1 record/văn bản:
  trích yếu + tóm tắt + keywords + ACL) -> trả về ~top_n document_id (đã lọc ACL).
- Stage 2: search chunk như bình thường nhưng GIỚI HẠN trong các document_id đó.

Mặc định TẮT (`two_stage_retrieval_enabled=False`); bật khi corpus đủ lớn.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any
from uuid import UUID

import httpx

from app.core.config import settings

if TYPE_CHECKING:
    from app.services.security.security_acl_payload import AclSubject

logger = logging.getLogger(__name__)


class DocumentIndexStore:
    """ES index document-level: mỗi văn bản 1 record (không phải chunk)."""

    INDEX_NAME = "hbrag_documents_v1"

    def __init__(self, *, url: str | None = None, timeout_seconds: float = 30.0) -> None:
        self.url = (url or settings.two_stage_document_index_url or settings.elasticsearch_url).rstrip("/")
        self.index_name = self.INDEX_NAME
        self.timeout_seconds = timeout_seconds

    @staticmethod
    def _index_definition(*, include_embedding: bool = True) -> dict[str, Any]:
        properties: dict[str, Any] = {
            "document_id": {"type": "keyword"},
            "id_vb": {"type": "keyword"},
            "ky_hieu": {"type": "text", "analyzer": "vi_bm25"},
            "trich_yeu": {"type": "text", "analyzer": "vi_bm25"},
            "tom_tat": {"type": "text", "analyzer": "vi_bm25"},
            "keywords": {"type": "text", "analyzer": "vi_bm25"},
            "noi_dung": {"type": "text", "analyzer": "vi_bm25", "index_options": "offsets"},
            "noi_ban_hanh": {"type": "keyword"},
            "nguoi_ky": {"type": "keyword"},
            "ten_file": {"type": "keyword"},
            "nam": {"type": "integer"},
            "ngay_vb": {"type": "keyword"},
            # ACL — giống chunk index để lọc cùng cách.
            "acl_subjects": {"type": "keyword", "doc_values": True},
            "acl_deny_pb": {"type": "integer"},
            "acl_deny_nv": {"type": "integer"},
        }
        if include_embedding:
            # BBQ binary-quantized HNSW (ES >= 8.12). Embedding đã chuẩn hoá (L2=1)
            # nên dùng ``dot_product``.
            properties["embedding"] = {
                "type": "dense_vector",
                "dims": settings.embedding_dimension,
                "index": True,
                "similarity": "dot_product",
                "index_options": {"type": "bbq_hnsw", "m": 16, "ef_construction": 100},
            }
        return {
            "settings": {
                "number_of_shards": 4,
                "number_of_replicas": settings.elasticsearch_number_of_replicas,
                "refresh_interval": "60s",
                "index.queries.cache.enabled": True,
                "analysis": {
                    "analyzer": {
                        "vi_bm25": {
                            "type": "custom",
                            "tokenizer": "standard",
                            "filter": ["lowercase", "asciifolding"],
                        }
                    }
                },
            },
            "mappings": {"properties": properties},
        }

    async def ensure_index(self) -> None:
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            resp = await client.head(f"{self.url}/{self.index_name}")
            if resp.status_code == 200:
                return
            resp = await client.put(
                f"{self.url}/{self.index_name}",
                json=self._index_definition(include_embedding=True),
            )
            if resp.status_code < 400 or "resource_already_exists" in resp.text:
                return
            # ES có thể không hỗ trợ bbq_hnsw (cần >= 8.12) -> tạo lại BM25-only.
            logger.warning(
                "Tạo document index kèm BBQ thất bại (HTTP %s) -> thử lại BM25-only. %s",
                resp.status_code,
                resp.text[:200],
            )
            resp = await client.put(
                f"{self.url}/{self.index_name}",
                json=self._index_definition(include_embedding=False),
            )
            if resp.status_code >= 400 and "resource_already_exists" not in resp.text:
                raise RuntimeError(f"Tạo document index lỗi: HTTP {resp.status_code} {resp.text[:300]}")

    async def bulk_index(self, documents: list[dict[str, Any]]) -> int:
        """Bulk index list document record (mỗi dict cần 'document_id'). Trả số bản ghi."""
        if not documents:
            return 0
        await self.ensure_index()
        lines: list[str] = []
        import json

        for doc in documents:
            lines.append(json.dumps({"index": {"_index": self.index_name, "_id": doc["document_id"]}}))
            lines.append(json.dumps(doc, ensure_ascii=False))
        body = "\n".join(lines) + "\n"
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            resp = await client.post(
                f"{self.url}/_bulk",
                content=body.encode("utf-8"),
                headers={"Content-Type": "application/x-ndjson"},
                params={"refresh": "false"},
            )
            if resp.status_code >= 400:
                raise RuntimeError(f"Bulk index document lỗi: HTTP {resp.status_code} {resp.text[:300]}")
        return len(documents)

    async def update_acl(
        self,
        document_id: str,
        *,
        acl_subjects: list[str],
        acl_deny_pb: list[int],
        acl_deny_nv: list[int],
    ) -> None:
        """Partial update CHỈ 3 trường ACL của 1 document record.

        Dùng ES ``_update`` (partial) nên KHÔNG đụng đến ``embedding`` hay các trường
        BM25 (ky_hieu/trich_yeu/tom_tat/keywords). Document chưa có trong index ->
        bỏ qua (404): sẽ được tạo đầy đủ khi ingest/build. Không upsert để tránh tạo
        record thiếu nội dung tìm kiếm.
        """
        import json

        body = json.dumps(
            {
                "doc": {
                    "acl_subjects": acl_subjects,
                    "acl_deny_pb": acl_deny_pb,
                    "acl_deny_nv": acl_deny_nv,
                }
            },
            ensure_ascii=False,
        )
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            resp = await client.post(
                f"{self.url}/{self.index_name}/_update/{document_id}",
                content=body.encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
        if resp.status_code == 404:
            logger.warning(
                "update_acl: document=%s chưa có trong document index -> bỏ qua",
                document_id,
            )
            return
        if resp.status_code >= 400:
            raise RuntimeError(
                f"update_acl lỗi document={document_id}: HTTP {resp.status_code} {resp.text[:300]}"
            )
        logger.debug("update_acl OK document=%s", document_id)

    async def upsert_document(
        self,
        *,
        document_id: str,
        id_vb: str | None = None,
        ky_hieu: str | None = None,
        trich_yeu: str | None = None,
        tom_tat: str | None = None,
        keywords: str | None = None,
        noi_dung: str | None = None,
        noi_ban_hanh: str | None = None,
        nguoi_ky: str | None = None,
        ten_file: str | None = None,
        nam: int | None = None,
        ngay_vb: str | None = None,
        acl_subjects: list[str],
        acl_deny_pb: list[int],
        acl_deny_nv: list[int],
        embedding: list[float] | None = None,
    ) -> None:
        """Tạo/cập nhật đầy đủ 1 record document index (gọi sau khi ingest 1 văn bản).

        ``noi_dung`` được truncate 50K ký tự (guard server-side); ``embedding=None``
        -> không ghi field embedding (BM25-only).
        """
        await self.ensure_index()
        import json

        record: dict[str, Any] = {"document_id": document_id}
        for key, value in (
            ("id_vb", id_vb),
            ("ky_hieu", ky_hieu),
            ("trich_yeu", trich_yeu),
            ("tom_tat", tom_tat),
            ("keywords", keywords),
            ("noi_dung", (noi_dung or "")[:50_000] or None),
            ("noi_ban_hanh", noi_ban_hanh),
            ("nguoi_ky", nguoi_ky),
            ("ten_file", ten_file),
            ("nam", nam),
            ("ngay_vb", ngay_vb),
        ):
            if value is not None:
                record[key] = value
        record["acl_subjects"] = acl_subjects
        record["acl_deny_pb"] = acl_deny_pb
        record["acl_deny_nv"] = acl_deny_nv
        if embedding is not None:
            record["embedding"] = embedding

        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            resp = await client.put(
                f"{self.url}/{self.index_name}/_doc/{document_id}",
                content=json.dumps(record, ensure_ascii=False).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            if resp.status_code >= 400:
                raise RuntimeError(
                    f"upsert_document lỗi document={document_id}: HTTP {resp.status_code} {resp.text[:300]}"
                )

    async def existing_id_vb(self, id_vb_list: list[str]) -> set[str]:
        """Tập ``id_vb`` đã có record trong document index (terms query trên id_vb)."""
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
                raise RuntimeError(
                    f"existing_id_vb lỗi: HTTP {resp.status_code} {resp.text[:300]}"
                )
            hits = resp.json().get("hits", {}).get("hits", [])
        return {str(h["_source"]["id_vb"]) for h in hits if h.get("_source", {}).get("id_vb")}

    async def update_document_embedding(self, document_id: str, embedding: list[float]) -> None:
        """Partial-update CHỈ field ``embedding`` (bổ sung BBQ cho doc đã có, CASE 3).

        Không đụng ACL/BM25 fields. Doc chưa có -> 404, bỏ qua.
        """
        import json

        body = json.dumps({"doc": {"embedding": embedding}})
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            resp = await client.post(
                f"{self.url}/{self.index_name}/_update/{document_id}",
                content=body.encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
        if resp.status_code == 404:
            logger.warning(
                "update_document_embedding: document=%s chưa có trong index -> bỏ qua",
                document_id,
            )
            return
        if resp.status_code >= 400:
            raise RuntimeError(
                f"update_document_embedding lỗi document={document_id}: HTTP {resp.status_code} {resp.text[:300]}"
            )

    def _build_search_body(
        self,
        query: str,
        *,
        top_n: int,
        acl_subject: "AclSubject | None",
        query_vector: list[float] | None,
        source_fields: list[str],
    ) -> dict[str, Any]:
        """Dựng ES query body dùng chung cho mọi search trên document index.

        ACL đặt ở ``filter`` context (lọc cứng, ES cache được); BM25 ở ``should`` chỉ
        để *scoring* (KHÔNG ``minimum_should_match`` — câu hỏi ngữ nghĩa vẫn lọt, miễn
        trong ACL). Có ``query_vector`` -> hybrid kNN(BBQ) + BM25; không thì BM25-only.
        ACL filter tái dùng ``build_es_acl_filter_flat`` (None nếu super admin).
        """
        filters: list[dict[str, Any]] = []
        if acl_subject is not None:
            from app.services.security.security_acl_payload import build_es_acl_filter_flat

            clause = build_es_acl_filter_flat(acl_subject)
            if clause is not None:
                filters.append(clause)
        should = [
            {"match": {"ky_hieu": {"query": query, "boost": 6.0}}},
            {"match": {"trich_yeu": {"query": query, "boost": 3.0}}},
            {"match": {"tom_tat": {"query": query, "boost": 2.0}}},
            {"match": {"keywords": {"query": query, "boost": 1.5}}},
        ]
        body: dict[str, Any] = {
            "size": top_n,
            "_source": source_fields,
            "query": {"bool": {"should": should, "filter": filters}},
        }
        if query_vector is not None:
            body["knn"] = {
                "field": "embedding",
                "query_vector": query_vector,
                "k": top_n,
                "num_candidates": top_n * 4,
                "filter": filters,
            }
        return body

    async def search_documents(
        self,
        query: str,
        *,
        top_n: int = 50,
        acl_subject: "AclSubject | None" = None,
        query_vector: list[float] | None = None,
    ) -> list[str]:
        """Trả về list document_id phù hợp nhất (đã lọc ACL)."""
        await self.ensure_index()
        body = self._build_search_body(
            query,
            top_n=top_n,
            acl_subject=acl_subject,
            query_vector=query_vector,
            source_fields=["document_id"],
        )
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            resp = await client.post(f"{self.url}/{self.index_name}/_search", json=body)
            if resp.status_code == 404:
                return []
            if resp.status_code >= 400:
                raise RuntimeError(f"Search document lỗi: HTTP {resp.status_code} {resp.text[:300]}")
            hits = resp.json().get("hits", {}).get("hits", [])
        return [h["_source"]["document_id"] for h in hits if h.get("_source", {}).get("document_id")]

    async def search_documents_with_detail(
        self,
        query: str,
        *,
        top_n: int = 20,
        acl_subject: "AclSubject | None" = None,
        query_vector: list[float] | None = None,
    ) -> list[dict[str, Any]]:
        """Như :meth:`search_documents` nhưng trả full metadata + ``_score`` mỗi hit.

        Dùng cho API trả kết quả trực tiếp cho người dùng (document-level).
        """
        await self.ensure_index()
        body = self._build_search_body(
            query,
            top_n=top_n,
            acl_subject=acl_subject,
            query_vector=query_vector,
            source_fields=[
                "document_id",
                "id_vb",
                "ky_hieu",
                "trich_yeu",
                "tom_tat",
                "ngay_vb",
                "nam",
            ],
        )
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            resp = await client.post(f"{self.url}/{self.index_name}/_search", json=body)
            if resp.status_code == 404:
                return []
            if resp.status_code >= 400:
                raise RuntimeError(f"Search document lỗi: HTTP {resp.status_code} {resp.text[:300]}")
            hits = resp.json().get("hits", {}).get("hits", [])
        results: list[dict[str, Any]] = []
        for hit in hits:
            source = hit.get("_source") or {}
            if not source.get("document_id"):
                continue
            results.append({**source, "_score": hit.get("_score")})
        return results


class TwoStageHybridSearchService:
    """Bọc hybrid search: Stage1 (document) -> Stage2 (chunk trong document_ids).

    Expose CẢ ``run_search`` (trả ``HybridSearchRun``) lẫn ``search`` (trả
    ``HybridSearchResponse``) để là drop-in cho ``HybridSearchService`` — consumer
    chính (``RerankingService``) gọi ``run_search``.

    Fallback: khi Stage 1 trả < ``stage1_min_results`` document, bỏ qua việc giới
    hạn scope và chạy full hybrid search (vẫn áp ACL ở Stage 2). Tránh trả rỗng cho
    câu hỏi rất lạ / corpus chưa đủ phủ document index.
    """

    def __init__(
        self,
        *,
        hybrid_search: Any,
        document_index: DocumentIndexStore,
        llm_gateway: Any = None,
        stage1_top_n: int = 50,
        stage1_min_results: int = 3,
        enabled: bool = True,
    ) -> None:
        self._hybrid_search = hybrid_search
        self._document_index = document_index
        self._llm_gateway = llm_gateway
        self._stage1_top_n = stage1_top_n
        self._stage1_min_results = stage1_min_results
        self._enabled = enabled

    async def _resolve_stage_ids(
        self,
        *,
        query: str,
        acl_subject: "AclSubject | None",
        document_ids: "set[UUID] | None",
    ) -> "set[UUID] | None":
        """Tính scope Stage 1. Trả ``None`` nghĩa là KHÔNG giới hạn (full search:
        do tắt two-stage hoặc fallback). Ngược lại trả tập document_id để giới hạn.
        """
        if not self._enabled:
            return None
        # Embed query để dùng kNN(BBQ) ở Stage 1 nếu bật cấu hình + có gateway.
        query_vector: list[float] | None = None
        if self._llm_gateway is not None and settings.two_stage_document_embedding_enabled:
            try:
                query_vector = await self._llm_gateway.embed_query(query)
            except Exception:
                logger.warning("Embed query Stage 1 thất bại -> BM25-only", exc_info=True)
        doc_ids = await self._document_index.search_documents(
            query,
            top_n=self._stage1_top_n,
            acl_subject=acl_subject,
            query_vector=query_vector,
        )
        fallback = len(doc_ids) < self._stage1_min_results
        logger.info(
            "two-stage stage1: %d doc (vector=%s, fallback=%s, min=%d)",
            len(doc_ids),
            query_vector is not None,
            fallback,
            self._stage1_min_results,
        )
        if fallback:
            return None
        stage_ids = {UUID(d) for d in doc_ids}
        if document_ids:
            # Caller đã giới hạn -> giao với kết quả Stage 1.
            stage_ids &= {UUID(str(x)) for x in document_ids}
        return stage_ids

    async def run_search(
        self,
        *,
        query: str,
        top_k: int,
        acl_subject: "AclSubject | None" = None,
        document_ids: "set[UUID] | None" = None,
        **kwargs: Any,
    ) -> Any:
        stage_ids = await self._resolve_stage_ids(
            query=query, acl_subject=acl_subject, document_ids=document_ids
        )
        effective_ids = document_ids if stage_ids is None else stage_ids
        return await self._hybrid_search.run_search(
            query=query,
            top_k=top_k,
            document_ids=effective_ids,
            acl_subject=acl_subject,
            **kwargs,
        )

    async def search(
        self,
        *,
        query: str,
        top_k: int,
        acl_subject: "AclSubject | None" = None,
        document_ids: "set[UUID] | None" = None,
        **kwargs: Any,
    ) -> Any:
        stage_ids = await self._resolve_stage_ids(
            query=query, acl_subject=acl_subject, document_ids=document_ids
        )
        effective_ids = document_ids if stage_ids is None else stage_ids
        return await self._hybrid_search.search(
            query=query,
            top_k=top_k,
            document_ids=effective_ids,
            acl_subject=acl_subject,
            **kwargs,
        )
