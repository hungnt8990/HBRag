# Prompt tối ưu hóa HBRag Backend — 2 triệu văn bản

> Dán prompt này vào phiên Claude mới (có đính kèm toàn bộ source code),
> Claude sẽ thực hiện từng ưu tiên theo thứ tự.

---

## CONTEXT (đọc trước khi làm)

Đây là backend RAG (FastAPI + Qdrant + Elasticsearch) cho EVNCPC với ~2 triệu văn bản
DOffice, ước tính ~20 triệu chunks sau khi ingest. Hệ thống đã có:

- Qdrant collection `hbrag_chunks_qwen3_8b_v1`, dense 4096-dim (Qwen3-Embedding-8B) + sparse
- ES index `hbrag_chunks_bm25_v1`
- ACL mới `acl_*` payload (acl_allow_dv/pb/nv, acl_deny_pb/nv) — đã wire cả Qdrant + ES
- Hybrid search: Qdrant RRF nội bộ (dense+sparse Prefetch) → ES BM25 → Python RRF → rerank
- `_payload_filter()` trong `vector_store.py` đang dựng Filter với 3 FieldCondition OR riêng
- `build_es_acl_filter()` trong `security_acl_payload.py` đang dựng 3 `should` clause riêng
- `_ensure_payload_indexes()` trong `vector_store.py` **CHƯA** index các trường `acl_*`
- `qdrant_create_payload_indexes.py` cũng **CHƯA** có `acl_*`
- `_index_definition()` trong ES **CHƯA** khai báo mapping cho `acl_*`
- Config chưa có: quantization, on_disk, hnsw tuning, two-stage, cache

Dữ liệu đầu vào từ DOffice có cấu trúc thực tế:
```json
{
  "don_vi_list": [269],
  "phong_ban_list": [42926, 43300, 43303, ...],   // ~17 phòng ban
  "ca_nhan_list": [117948, 117945, 117906, ...]    // ~160 cá nhân
}
```
Bộ nén ACL đã hoạt động đúng: 160 người → `acl_allow_pb=[X] + deny_nv=[Y]`.

---

## NHIỆM VỤ

Thực hiện **3 ưu tiên** theo thứ tự sau. Với mỗi ưu tiên, hãy **chỉnh sửa trực tiếp
các file liên quan** và giải thích ngắn gọn lý do thay đổi.

---

## ƯU TIÊN 1 — Payload index ACL + Flatten acl_subjects + ES mapping (làm ngay)

### 1A. Flatten ACL thành `acl_subjects` — gộp allow vào 1 list duy nhất

**File cần sửa: `app/services/security/security_acl_payload.py`**

Thêm hàm `to_chunk_payload_flat()` — tạo trường `acl_subjects` là list string flatten
`["dv_{id}", "pb_{id}", "nv_{id}"]` từ allow lists. Giữ `acl_deny_pb` và `acl_deny_nv`
riêng (deny không flatten vì cần check riêng biệt). Cũng thêm hàm `build_qdrant_acl_filter_flat()`
và `build_es_acl_filter_flat()` dùng `acl_subjects` thay vì 3 clause riêng.

Ví dụ output:
```python
# Thay vì:
{"acl_allow_dv": [251, 312], "acl_allow_pb": [43310], "acl_allow_nv": [9981]}

# Thêm trường:
{"acl_subjects": ["dv_251", "dv_312", "pb_43310", "nv_9981"],
 "acl_deny_pb": [], "acl_deny_nv": [], "acl_ver": "..."}
```

Filter Qdrant mới (1 FieldCondition thay vì 3 OR):
```python
FieldCondition(key="acl_subjects", match=MatchAny(any=["dv_312", "pb_43310", "nv_9981"]))
```

Filter ES mới (1 terms clause thay vì 3 should):
```json
{"terms": {"acl_subjects": ["dv_312", "pb_43310", "nv_9981"]}}
```

**Yêu cầu:**
- Giữ nguyên `to_chunk_payload()` cũ (backward compat) — thêm mới `to_chunk_payload_flat()`
  gọi `to_chunk_payload()` rồi thêm `acl_subjects`
- `build_qdrant_acl_conditions()` cũ vẫn giữ; thêm `build_qdrant_acl_conditions_flat()`
- `build_es_acl_filter()` cũ vẫn giữ; thêm `build_es_acl_filter_flat()`
- Thêm helper `acl_subject_to_keys(subject: AclSubject) -> list[str]` — dùng chung cho cả Qdrant và ES

### 1B. Tạo payload index cho acl_* trong Qdrant

**File cần sửa: `app/services/vector/vector_store.py`**

Trong `PAYLOAD_INTEGER_FIELDS` và `PAYLOAD_KEYWORD_FIELDS` / `_ensure_payload_indexes()`:

Thêm vào `PAYLOAD_INTEGER_FIELDS`:
```python
"acl_allow_dv", "acl_allow_pb", "acl_allow_nv", "acl_deny_pb", "acl_deny_nv"
```

Thêm vào `PAYLOAD_KEYWORD_FIELDS` (hoặc tạo `PAYLOAD_KEYWORD_ARRAY_FIELDS`):
```python
"acl_subjects"   # trường flatten mới — type KEYWORD
```

**File cần sửa: `scripts/maintenance/qdrant_create_payload_indexes.py`**

Thêm vào danh sách tạo index:
```python
# ACL new system
("acl_subjects",    PayloadSchemaType.KEYWORD),   # flatten allow list
("acl_deny_pb",     PayloadSchemaType.INTEGER),
("acl_deny_nv",     PayloadSchemaType.INTEGER),
("acl_allow_dv",    PayloadSchemaType.INTEGER),
("acl_allow_pb",    PayloadSchemaType.INTEGER),
("acl_allow_nv",    PayloadSchemaType.INTEGER),
```

### 1C. ES mapping + filter context cho acl_*

**File cần sửa: `app/services/retrieval/retrieval_elasticsearch_keyword_search.py`**

Trong `_index_definition()`, thêm vào `mappings.properties`:
```json
"acl_subjects":  {"type": "keyword", "index": true, "doc_values": true},
"acl_deny_pb":   {"type": "integer", "index": true},
"acl_deny_nv":   {"type": "integer", "index": true},
"acl_allow_dv":  {"type": "integer", "index": true},
"acl_allow_pb":  {"type": "integer", "index": true},
"acl_allow_nv":  {"type": "integer", "index": true}
```

Trong `_build_query()`, sửa cách thêm ACL clause vào `filters` list:
- Hiện tại: `filters.append(acl_clause)` — acl_clause là `{"bool": {"should": [...], "minimum_should_match": 1, "must_not": [...]}}`
- Sửa thành dùng `build_es_acl_filter_flat()` nếu có `acl_subjects` trên document,
  fallback về `build_es_acl_filter()` — clause này nằm trong `filter` context (đã cache được)

Thêm ACL fields vào `_chunk_document()` — copy từ payload:
```python
for acl_field in ("acl_subjects", "acl_allow_dv", "acl_allow_pb",
                  "acl_allow_nv", "acl_deny_pb", "acl_deny_nv", "acl_ver"):
    val = payload.get(acl_field)
    if val not in (None, [], ""):
        doc[acl_field] = val
```

### 1D. Sửa `_payload_filter()` dùng flatten filter

**File cần sửa: `app/services/vector/vector_store.py`**

Trong `_payload_filter()`, phần xử lý `acl_subject`:
```python
if acl_subject is not None:
    from app.services.security.security_acl_payload import (
        build_qdrant_acl_conditions_flat,
        build_qdrant_acl_conditions,
    )
    # Ưu tiên flat filter (1 lookup), fallback về 3-condition cũ
    acl_conditions = build_qdrant_acl_conditions_flat(acl_subject)
    if acl_conditions is not None:
        acl_should, acl_must_not = acl_conditions
        must.append(Filter(should=acl_should))
        must_not.extend(acl_must_not)
```

---

## ƯU TIÊN 2 — Qdrant quantization + on_disk + HNSW tuning

### 2A. Thêm config settings

**File cần sửa: `app/core/config.py`**

Thêm các settings sau (với default an toàn, không break existing):
```python
# Qdrant performance
qdrant_quantization_enabled: bool = False          # bật khi collection mới hoặc recreate
qdrant_vector_on_disk: bool = False                # bật khi RAM < 300GB
qdrant_hnsw_m: int = 16                           # HNSW neighbor count
qdrant_hnsw_ef_construct: int = 100               # build quality
qdrant_hnsw_on_disk: bool = False                 # HNSW index on disk
qdrant_search_hnsw_ef: int = 128                  # query-time ef (accuracy vs speed)
qdrant_quantization_rescore: bool = True          # rescore sau quantized search
qdrant_quantization_oversampling: float = 2.0     # oversample trước rescore
qdrant_shard_number: int = 1                      # số shard (tăng lên 4-8 cho 20M chunks)
qdrant_replication_factor: int = 1               # replica (tăng lên 2 cho HA)
qdrant_memmap_threshold: int = 20000             # segment size → dùng mmap
```

### 2B. Áp dụng config vào `_create_collection()`

**File cần sửa: `app/services/vector/vector_store.py`**

Sửa `_create_collection()` để nhận config từ settings:
```python
async def _create_collection(self) -> None:
    from qdrant_client.models import (
        HnswConfigDiff, OptimizersConfigDiff,
        ScalarQuantization, ScalarQuantizationConfig, ScalarType,
    )

    vectors_config = {
        self.dense_vector_name: VectorParams(
            size=self.vector_size,
            distance=self.distance,
            on_disk=settings.qdrant_vector_on_disk,
        )
    }

    quantization_config = None
    if settings.qdrant_quantization_enabled:
        quantization_config = ScalarQuantization(
            scalar=ScalarQuantizationConfig(
                type=ScalarType.INT8,
                quantile=0.99,
                always_ram=True,   # quantized index vẫn ở RAM
            )
        )

    kwargs: dict[str, Any] = {
        "collection_name": self.collection_name,
        "vectors_config": vectors_config,
        "hnsw_config": HnswConfigDiff(
            m=settings.qdrant_hnsw_m,
            ef_construct=settings.qdrant_hnsw_ef_construct,
            on_disk=settings.qdrant_hnsw_on_disk,
        ),
        "optimizers_config": OptimizersConfigDiff(
            memmap_threshold=settings.qdrant_memmap_threshold,
        ),
        "shard_number": settings.qdrant_shard_number,
        "replication_factor": settings.qdrant_replication_factor,
    }
    if quantization_config is not None:
        kwargs["quantization_config"] = quantization_config

    if self.sparse_enabled:
        kwargs["sparse_vectors_config"] = {
            self.sparse_vector_name: SparseVectorParams()
        }
    await self._client.create_collection(**kwargs)
```

### 2C. Áp dụng search params khi query

**File cần sửa: `app/services/vector/vector_store.py`**

Trong `search()`, thêm `search_params` vào các `Prefetch` và query:
```python
from qdrant_client.models import SearchParams, QuantizationSearchParams

search_params = SearchParams(
    hnsw_ef=settings.qdrant_search_hnsw_ef,
    exact=False,
    quantization=(
        QuantizationSearchParams(
            ignore=False,
            rescore=settings.qdrant_quantization_rescore,
            oversampling=settings.qdrant_quantization_oversampling,
        )
        if settings.qdrant_quantization_enabled
        else None
    ),
)
# Thêm params=search_params vào Prefetch (dense và sparse) và query_points
```

### 2D. ES index settings tối ưu

**File cần sửa: `app/services/retrieval/retrieval_elasticsearch_keyword_search.py`**

Trong `_index_definition()`, thêm vào `settings`:
```json
"number_of_shards": 8,
"number_of_replicas": 1,
"refresh_interval": "30s",
"index.max_result_window": 200,
"index.queries.cache.enabled": true,
"index.requests.cache.enable": true
```

**Lưu ý quan trọng:** `refresh_interval: "30s"` chỉ áp dụng khi tạo index mới.
Thêm script `scripts/maintenance/es_update_settings.py` để update setting trên index hiện tại:
```python
# PUT /{index}/_settings
# {"index": {"refresh_interval": "30s", "number_of_replicas": 1}}
```

---

## ƯU TIÊN 3 — Two-stage retrieval + Redis cache

### 3A. Document-level index cho Stage 1

Tạo file mới: **`app/services/retrieval/retrieval_document_index.py`**

```python
"""Stage-1 document-level search — tìm top-N document trước khi search chunk.

Index riêng trên ES: mỗi document 1 record (title + summary + keywords + acl_*).
Search nhanh hơn nhiều so với search trực tiếp 20M chunks.
"""

class DocumentIndexStore:
    """ES index: hbrag_documents_v1 — 1 doc/record (không phải chunk)."""
    
    INDEX_NAME = "hbrag_documents_v1"
    
    @staticmethod
    def _index_definition() -> dict:
        return {
            "settings": {
                "number_of_shards": 4,
                "number_of_replicas": 1,
                "refresh_interval": "60s",
                "index.queries.cache.enabled": True,   # cache ACL filter — rất quan trọng
            },
            "mappings": {
                "properties": {
                    "document_id":  {"type": "keyword"},
                    "id_vb":        {"type": "keyword"},
                    "ky_hieu":      {"type": "keyword"},
                    "trich_yeu":    {"type": "text", "analyzer": "vi_bm25"},
                    "tom_tat":      {"type": "text", "analyzer": "vi_bm25"},
                    "keywords":     {"type": "text", "analyzer": "vi_bm25"},
                    "nam":          {"type": "integer"},
                    "ngay_vb":      {"type": "date"},
                    # ACL fields — đầy đủ để filter chính xác
                    "acl_subjects": {"type": "keyword", "doc_values": True},  # flatten allow list
                    "acl_deny_pb":  {"type": "integer", "index": True},
                    "acl_deny_nv":  {"type": "integer", "index": True},
                }
            }
        }

    def _build_search_query(
        self,
        query: str,
        *,
        top_n: int,
        acl_subject: "AclSubject | None",
    ) -> dict:
        """Query body cho Stage-1 document search.

        Dùng filter context cho ACL (được cache node-level).
        Dùng should context cho BM25 scoring trên trich_yeu + tom_tat + keywords.
        """
        filters: list[dict] = []

        # ACL filter — nằm trong filter context → ES cache tự động
        if acl_subject is not None and not acl_subject.is_super_admin:
            from app.services.security.security_acl_payload import acl_subject_to_keys
            subject_keys = acl_subject_to_keys(acl_subject)  # ["dv_312", "pb_43310", "nv_9981"]

            acl_filter: dict = {
                "bool": {
                    "filter": [{"terms": {"acl_subjects": subject_keys}}],
                    "must_not": [],
                }
            }
            if acl_subject.id_pb is not None:
                acl_filter["bool"]["must_not"].append(
                    {"terms": {"acl_deny_pb": [acl_subject.id_pb]}}
                )
            acl_filter["bool"]["must_not"].append(
                {"terms": {"acl_deny_nv": [acl_subject.id_nv]}}
            )
            filters.append(acl_filter)

        return {
            "size": top_n,
            "_source": ["document_id", "id_vb", "ky_hieu", "trich_yeu"],
            "query": {
                "bool": {
                    "filter": filters,           # ACL — cached, không tính score
                    "should": [
                        {"match": {"trich_yeu": {"query": query, "boost": 3.0}}},
                        {"match": {"tom_tat":   {"query": query, "boost": 2.0}}},
                        {"match": {"keywords":  {"query": query, "boost": 1.5}}},
                        {"match": {"ky_hieu":   {"query": query, "boost": 5.0}}},
                    ],
                    "minimum_should_match": 1,
                }
            },
        }

    async def search_documents(
        self,
        query: str,
        *,
        top_n: int = 50,
        acl_subject: "AclSubject | None" = None,
    ) -> list[str]:
        """Trả về list document_id phù hợp (đã filter ACL).

        Chỉ trả document_id — Stage 2 dùng để filter chunk search.
        """
        payload = self._build_search_query(query, top_n=top_n, acl_subject=acl_subject)
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(
                f"{self.url}/{self.INDEX_NAME}/_search",
                json=payload,
            )
            if response.status_code >= 400:
                raise RuntimeError(f"Document index search failed: HTTP {response.status_code}")
            data = response.json()
        hits = data.get("hits", {}).get("hits", [])
        return [hit["_source"]["document_id"] for hit in hits if hit.get("_source", {}).get("document_id")]

class TwoStageHybridSearchService:
    """Wrapper: Stage1 (document search) → Stage2 (chunk search trong document_ids).
    
    Kích hoạt khi số chunk > threshold (mặc định 5_000_000).
    """
    
    def __init__(
        self,
        *,
        hybrid_search: HybridSearchService,
        document_index: DocumentIndexStore,
        stage1_top_n: int = 50,
        enabled: bool = True,
    ):
        ...
    
    async def search(self, *, query, top_k, acl_subject, **kwargs) -> HybridSearchResponse:
        if not self.enabled:
            return await self.hybrid_search.search(
                query=query, top_k=top_k, acl_subject=acl_subject, **kwargs
            )
        
        # Stage 1: tìm document_id phù hợp
        doc_ids = await self.document_index.search_documents(
            query, top_n=self.stage1_top_n, acl_subject=acl_subject
        )
        if not doc_ids:
            return HybridSearchResponse(query=query, top_k=top_k, results=[])
        
        # Stage 2: search chunk CHỈ trong doc_ids đó
        return await self.hybrid_search.search(
            query=query,
            top_k=top_k,
            document_ids={UUID(d) for d in doc_ids},
            acl_subject=acl_subject,
            **kwargs,
        )
```

Thêm script ingest document vào document index:
**`scripts/maintenance/build_document_index.py`**

Logic xây dựng document record:
```python
# Nguồn dữ liệu: ES chunk index hbrag_chunks_bm25_v1
# Với mỗi document_id, lấy 1 chunk bất kỳ (chunk_index=0) để đọc acl_*
# → không cần query Postgres hay Qdrant

# Query ES lấy chunk đầu của mỗi document
{
  "collapse": {"field": "document_id"},   # 1 chunk đại diện/document
  "sort": [{"chunk_index": "asc"}],       # lấy chunk đầu tiên
  "_source": [
    "document_id", "id_vb", "ky_hieu", "trich_yeu",
    "acl_subjects", "acl_deny_pb", "acl_deny_nv",
    "nam", "ngay_vb"
  ],
  "size": 1000   # scroll/search_after để lấy hết
}

# tom_tat và keywords đọc từ payload chunk_index=0
# (đã được ghi vào ES lúc ingest qua _chunk_document())
# Nếu không có trong chunk, đọc từ bảng document_metadata PostgreSQL

# Bulk index vào hbrag_documents_v1 — idempotent (_id = document_id)
```

**Lưu ý:** Script phải dùng `search_after` pagination (không dùng `from/size` quá 10000) để scroll qua toàn bộ 2M documents.

### 3B. Redis cache layer

Tạo file mới: **`app/services/cache/search_cache.py`**

```python
"""Redis cache cho search results.

Key: SHA256(query + id_pb + id_dv + top_k)[:16]
TTL: 300s (5 phút) — đủ cho burst query cùng phòng ban
"""
import hashlib, json
from typing import Any

class SearchResultCache:
    def __init__(self, redis_url: str, ttl_seconds: int = 300):
        ...
    
    def _cache_key(self, query: str, acl_subject: "AclSubject", top_k: int) -> str:
        raw = f"{query}|{acl_subject.id_pb}|{acl_subject.id_dv}|{top_k}"
        return "search:" + hashlib.sha256(raw.encode()).hexdigest()[:16]
    
    async def get(self, query: str, acl_subject, top_k: int) -> Any | None:
        ...
    
    async def set(self, query: str, acl_subject, top_k: int, result: Any) -> None:
        ...
    
    async def invalidate_by_document(self, document_id: str) -> None:
        """Xóa cache khi document được re-index (tùy chọn)."""
        ...
```

Thêm settings:
```python
# Cache
redis_url: str | None = None          # None = tắt cache
search_cache_ttl_seconds: int = 300
search_cache_enabled: bool = False    # bật khi redis_url có giá trị
```

Tích hợp cache vào route search (`app/api/routes/search.py`):
- Wrap `hybrid_search_service.search()` bằng cache get/set
- Cache key dùng `(query, id_pb, id_dv, top_k)` — user cùng phòng chia sẻ cache

### 3C. Thêm settings two-stage vào config

**File cần sửa: `app/core/config.py`**

```python
# Two-stage retrieval
two_stage_retrieval_enabled: bool = False
two_stage_document_index_url: str | None = None  # None = dùng cùng ES URL
two_stage_stage1_top_n: int = 50
two_stage_chunk_threshold: int = 5_000_000  # bật two-stage khi corpus đủ lớn
```

---

## YÊU CẦU CHUNG

1. **Không phá backward compat** — giữ tất cả API cũ, chỉ thêm mới.
2. **Default = tắt** cho mọi feature mới (quantization, two-stage, cache) — bật qua `.env`.
3. **Thêm logging** tại các điểm quan trọng (cache hit/miss, stage1 result count).
4. **Type hint đầy đủ** — dùng `from __future__ import annotations`.
5. Với mỗi file sửa, **liệt kê rõ các dòng/function** đã thay đổi.
6. Sau khi xong Ưu tiên 1, chạy thử `pytest tests/test_acl_resolver_payload.py -q`
   để đảm bảo không break test cũ.

---

## THỨ TỰ THỰC HIỆN

```
Bước 1: security_acl_payload.py — thêm flatten helpers
Bước 2: vector_store.py — thêm acl_* vào payload indexes + dùng flat filter
Bước 3: qdrant_create_payload_indexes.py — thêm acl_* indexes
Bước 4: retrieval_elasticsearch_keyword_search.py — mapping + flat filter + chunk_document
Bước 5: core/config.py — thêm tất cả settings mới (Qdrant + Cache + Two-stage)
Bước 6: vector_store.py — _create_collection() + search() với quantization + HNSW
Bước 7: es_update_settings.py script mới
Bước 8: retrieval_document_index.py — TwoStageHybridSearchService
Bước 9: build_document_index.py script mới
Bước 10: cache/search_cache.py + tích hợp vào search route
```

Sau mỗi bước, xác nhận file đã sửa và tóm tắt thay đổi.
