# Prompt 2: API tìm kiếm văn bản
# `app/api/routes/document_search.py`

---

## TRẠNG THÁI CODEBASE HIỆN TẠI

Giả định **Prompt 1 đã được thực hiện xong**:
- `DocumentIndexStore` đã có `upsert_document()`, `update_acl()`, mapping mới có
  `noi_dung`, `noi_ban_hanh`, `nguoi_ky`, `embedding` (BBQ)
- ES document index `hbrag_documents_v1` đã có data từ job sync
- `search_documents()` và `_build_search_body()` đã tồn tại

File cần đọc trước khi làm:
- `app/api/routes/search.py` — xem pattern router/Depends/schema
- `app/services/retrieval/retrieval_document_index.py` — `DocumentIndexStore`, `_build_search_body()`
- `app/services/security/security_acl_payload.py` — `AclSubject`, `acl_subject_to_keys()`
- `app/core/config.py` — `settings.elasticsearch_url`, `settings.two_stage_document_index_url`
- `app/services/llm_gateway/__init__.py` — `get_llm_gateway()`
- `app/main.py` — xem cách include_router

---

## MỤC TIÊU

API cho phép phần mềm bên ngoài tìm kiếm văn bản trên hệ thống RAG.

Input: `query`, `id_nv`, `id_pb`, `id_dv`

Tự động phát hiện kiểu tìm kiếm:
- **Exact** — câu có dạng số ký hiệu → ES term query, <10ms
- **BM25** — từ khoá ngắn → multi-field BM25 với highlight
- **Hybrid** — câu hỏi tự nhiên dài → BBQ kNN + BM25

ACL filter chạy trong cùng ES query — chỉ trả văn bản user có quyền xem.

Output: danh sách văn bản (mặc định) hoặc trích đoạn nội dung tuỳ param.

---

## VIỆC CẦN LÀM

### VIỆC 1 — Tạo `app/api/routes/document_search.py`

#### Schemas

```python
from typing import Literal
import re
import logging
import httpx
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from app.core.config import settings
from app.services.retrieval.retrieval_document_index import DocumentIndexStore
from app.services.security.security_acl_payload import AclSubject, acl_subject_to_keys

logger = logging.getLogger("api.document_search")
router = APIRouter(prefix="/api/document-search", tags=["document-search"])


class DocumentSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000, description="Từ khoá, số ký hiệu, hoặc câu hỏi")
    id_nv: int = Field(description="Mã nhân viên — bắt buộc để lọc quyền")
    id_pb: int | None = Field(default=None, description="Mã phòng ban")
    id_dv: int | None = Field(default=None, description="Mã đơn vị")
    top_n: int = Field(default=20, ge=1, le=100, description="Số văn bản trả về")
    use_vector: bool = Field(default=True, description="Dùng BBQ kNN (False = BM25-only)")
    mode: Literal["auto", "list", "excerpt"] = Field(
        default="auto",
        description=(
            "auto: tự detect từ câu hỏi; "
            "list: trả danh sách văn bản; "
            "excerpt: trả trích đoạn nội dung có highlight"
        ),
    )


class DocumentSearchHit(BaseModel):
    document_id: str
    id_vb: str | None = None
    ky_hieu: str | None = None
    trich_yeu: str | None = None
    tom_tat: str | None = None
    noi_ban_hanh: str | None = None
    nguoi_ky: str | None = None
    ngay_vb: str | None = None
    nam: int | None = None
    score: float
    highlights: list[str] = Field(
        default=[],
        description="Đoạn nội dung liên quan có highlight (từ noi_dung hoặc trich_yeu)"
    )


class DocumentSearchResponse(BaseModel):
    query: str
    id_nv: int
    id_pb: int | None
    id_dv: int | None
    search_type: str    # "exact" | "bm25" | "hybrid"
    mode_used: str      # "list" | "excerpt"
    used_vector: bool
    total: int
    results: list[DocumentSearchHit]
```

#### Hằng số

```python
# Fields trả về trong _source (KHÔNG lấy noi_dung — dùng highlight thay thế)
_SOURCE_FIELDS = [
    "document_id", "id_vb", "ky_hieu", "trich_yeu",
    "tom_tat", "noi_ban_hanh", "nguoi_ky", "ngay_vb", "nam",
]

# Highlight config — ES tự trích đoạn có từ khoá
_HIGHLIGHT = {
    "fields": {
        "noi_dung": {
            "fragment_size": 200,
            "number_of_fragments": 3,
            "pre_tags": ["<mark>"],
            "post_tags": ["</mark>"],
        },
        "trich_yeu": {
            "fragment_size": 150,
            "number_of_fragments": 1,
            "pre_tags": ["<mark>"],
            "post_tags": ["</mark>"],
        },
    },
    "require_field_match": False,
}

# Regex phát hiện số ký hiệu văn bản
_KY_HIEU_RE = re.compile(
    r"\d{2,5}/[A-ZĐÁÀẢÃẠĂẮẶẴẤẬẦẨẪÉÈẺẼẸÊẾỆỀỂỄÍÌỈĨỊÓÒỎÕỌÔỐỘỒỔỖƠỚỢỜỞỠÚÙỦŨỤƯỨỰỪỬỮÝỲỶỸỴ]"
    r"|[A-ZĐÁÀẢÃẠĂẮẶẴẤẬẦẨẪÉÈẺẼẸÊẾỆỀỂỄÍÌỈĨỊÓÒỎÕỌÔỐỘỒỔỖƠỚỢỜỞỠÚÙỦŨỤƯỨỰỪỬỮÝỲỶỸỴ]{2,}/\d{2,}"
    r"|\d{2,4}/\d{4}/[A-Z]",
    re.IGNORECASE,
)

# Regex phát hiện câu hỏi tự nhiên
_QUESTION_RE = re.compile(
    r"là gì|như thế nào|thế nào|ra sao|quy định|quy trình|hướng dẫn|"
    r"bao nhiêu|khi nào|ở đâu|điều kiện|ai |ai\?",
    re.IGNORECASE,
)
```

#### Hàm phát hiện kiểu tìm kiếm

```python
def _detect_search_type(query: str) -> str:
    """Trả về 'exact' | 'bm25' | 'hybrid'."""
    if _KY_HIEU_RE.search(query):
        return "exact"
    word_count = len(query.split())
    if word_count >= 6 or _QUESTION_RE.search(query):
        return "hybrid"
    return "bm25"


def _detect_mode(query: str, requested: str) -> str:
    """Trả về 'list' | 'excerpt'."""
    if requested != "auto":
        return requested
    if _QUESTION_RE.search(query) or len(query.split()) >= 8:
        return "excerpt"
    return "list"
```

#### Hàm build ACL filter

```python
def _build_acl_filters(acl_subject: AclSubject) -> list[dict]:
    if acl_subject.is_super_admin:
        return []
    subject_keys = acl_subject_to_keys(acl_subject)
    clause: dict = {
        "bool": {
            "filter": [{"terms": {"acl_subjects": subject_keys}}],
            "must_not": [{"terms": {"acl_deny_nv": [acl_subject.id_nv]}}],
        }
    }
    if acl_subject.id_pb is not None:
        clause["bool"]["must_not"].append(
            {"terms": {"acl_deny_pb": [acl_subject.id_pb]}}
        )
    return [clause]
```

#### Hàm build ES query body

```python
def _build_query_body(
    query: str,
    top_n: int,
    search_type: str,
    acl_filters: list[dict],
    query_vector: list[float] | None,
) -> dict:
    """Trả về ES query body hoàn chỉnh."""

    if search_type == "exact":
        return {
            "size": top_n,
            "_source": _SOURCE_FIELDS,
            "highlight": _HIGHLIGHT,
            "query": {
                "bool": {
                    "should": [
                        {"term": {"ky_hieu":  {"value": query, "boost": 10.0}}},
                        {"match": {"ky_hieu": {"query": query,  "boost": 6.0}}},
                        {"term": {"id_vb":    {"value": query, "boost": 10.0}}},
                    ],
                    "filter": acl_filters,
                    "minimum_should_match": 1,
                }
            },
        }

    # BM25 should — không minimum_should_match để không bỏ sót
    should = [
        {"match": {"ky_hieu":      {"query": query, "boost": 6.0}}},
        {"match": {"trich_yeu":    {"query": query, "boost": 3.0}}},
        {"match": {"tom_tat":      {"query": query, "boost": 2.0}}},
        {"match": {"keywords":     {"query": query, "boost": 1.5}}},
        {"match": {"noi_dung":     {"query": query, "boost": 1.0}}},
        {"match": {"noi_ban_hanh": {"query": query, "boost": 0.5}}},
    ]

    if search_type == "bm25" or query_vector is None:
        return {
            "size": top_n,
            "_source": _SOURCE_FIELDS,
            "highlight": _HIGHLIGHT,
            "query": {"bool": {"should": should, "filter": acl_filters}},
        }

    # Hybrid: BBQ kNN + BM25 trong 1 query ES
    return {
        "size": top_n,
        "_source": _SOURCE_FIELDS,
        "highlight": _HIGHLIGHT,
        "knn": {
            "field": "embedding",
            "query_vector": query_vector,
            "k": top_n,
            "num_candidates": top_n * 4,
            "filter": acl_filters,
        },
        "query": {"bool": {"should": should, "filter": acl_filters}},
    }
```

#### Endpoint chính

```python
@router.post(
    "/search",
    response_model=DocumentSearchResponse,
    summary="Tìm kiếm văn bản (exact / BM25 / hybrid kNN+BM25 + ACL)",
)
async def document_search(
    request: DocumentSearchRequest,
) -> DocumentSearchResponse:
    """Tìm kiếm văn bản trong hệ thống RAG.

    Tự động phát hiện kiểu tìm:
    - Số ký hiệu (6515/EVNCPC) → exact term, <10ms
    - Từ khoá ngắn → BM25 multi-field với highlight
    - Câu hỏi tự nhiên → hybrid BBQ kNN + BM25

    ACL filter: chỉ trả văn bản id_nv/id_pb/id_dv có quyền xem.
    """
    if not settings.elasticsearch_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Elasticsearch chưa được bật.",
        )

    acl_subject = AclSubject(
        id_nv=request.id_nv,
        id_pb=request.id_pb,
        id_dv=request.id_dv,
        is_super_admin=False,
    )

    search_type = _detect_search_type(request.query)
    mode_used = _detect_mode(request.query, request.mode)
    acl_filters = _build_acl_filters(acl_subject)

    # Embed query nếu hybrid
    query_vector: list[float] | None = None
    used_vector = False
    if search_type == "hybrid" and request.use_vector:
        try:
            from app.services.llm_gateway import get_llm_gateway
            query_vector = await get_llm_gateway().embed_query(request.query)
            used_vector = True
        except Exception:
            logger.warning(
                "Embed query thất bại, fallback BM25 query=%r",
                request.query[:60],
                exc_info=True,
            )
            search_type = "bm25"

    body = _build_query_body(
        request.query, request.top_n,
        search_type, acl_filters, query_vector,
    )

    store = DocumentIndexStore(
        url=settings.two_stage_document_index_url or settings.elasticsearch_url
    )
    await store.ensure_index()

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{store.url}/{store.index_name}/_search",
                json=body,
            )
        if resp.status_code >= 400:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"ES lỗi HTTP {resp.status_code}: {resp.text[:200]}",
            )
        data = resp.json()
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("ES search lỗi query=%r", request.query[:60])
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Lỗi kết nối ES: {exc}",
        ) from exc

    hits = data.get("hits", {}).get("hits", [])
    results = []
    for h in hits:
        src = h.get("_source") or {}
        hl = h.get("highlight") or {}
        # Ưu tiên highlight từ noi_dung, fallback trich_yeu
        highlights = hl.get("noi_dung", []) or hl.get("trich_yeu", [])
        results.append(DocumentSearchHit(
            document_id=src.get("document_id", ""),
            id_vb=src.get("id_vb"),
            ky_hieu=src.get("ky_hieu"),
            trich_yeu=src.get("trich_yeu"),
            tom_tat=src.get("tom_tat"),
            noi_ban_hanh=src.get("noi_ban_hanh"),
            nguoi_ky=src.get("nguoi_ky"),
            ngay_vb=src.get("ngay_vb"),
            nam=src.get("nam"),
            score=float(h.get("_score") or 0.0),
            highlights=highlights,
        ))

    logger.info(
        "document_search id_nv=%s type=%s mode=%s vector=%s results=%d query=%r",
        request.id_nv, search_type, mode_used, used_vector,
        len(results), request.query[:60],
    )

    return DocumentSearchResponse(
        query=request.query,
        id_nv=request.id_nv,
        id_pb=request.id_pb,
        id_dv=request.id_dv,
        search_type=search_type,
        mode_used=mode_used,
        used_vector=used_vector,
        total=len(results),
        results=results,
    )
```

### VIỆC 2 — Wire router vào `app/main.py`

Thêm vào `app/main.py` cạnh các router hiện có:
```python
from app.api.routes.document_search import router as document_search_router
app.include_router(document_search_router)
```

### VIỆC 3 — Unit tests

Tạo `tests/test_document_search.py`:

```python
# Test 1: detect_search_type
# "6515/EVNCPC" → "exact"
# "quy trình GIS lưới điện" → "bm25"
# "quy định về phụ cấp điện lực là gì năm 2023" → "hybrid"

# Test 2: detect_mode
# mode="list" → "list"
# mode="auto" + câu ngắn → "list"
# mode="auto" + "điều kiện nghỉ phép là gì" → "excerpt"

# Test 3: _build_acl_filters
# AclSubject(id_nv=117058, id_pb=43038) → có term filter + must_not deny

# Test 4: endpoint /api/document-search/search
# Mock DocumentIndexStore.ensure_index() và httpx.AsyncClient
# Case exact: trả 1 hit với score cao
# Case bm25: trả nhiều hits với highlights
# Case hybrid: mock embed_query, verify knn field trong body
# Case ES lỗi 400: raise HTTP 502
# Case embed thất bại: fallback bm25
```

---

## YÊU CẦU CHUNG

1. **Chỉ tạo/sửa** `app/api/routes/document_search.py`, cập nhật `app/main.py`.
   Không sửa file nào khác.

2. **Không cần auth** — caller tự truyền `id_nv`, `id_pb`, `id_dv`. API này
   dùng cho internal service, không expose ra public.

3. **Highlight thay cho noi_dung** — không trả `noi_dung` trong response.
   ES tự trích 3 đoạn ~200 ký tự có từ khoá.

4. **BBQ graceful** — nếu embed thất bại: fallback BM25, `used_vector=False`.
   Không trả lỗi cho user.

5. **Chạy pytest sau khi xong:**
   ```bash
   pytest tests/test_document_search.py -v
   ```

---

## VALIDATION

```bash
# Test exact — tìm theo số ký hiệu
curl -X POST http://localhost:8000/api/document-search/search \
  -H "Content-Type: application/json" \
  -d '{
    "query": "6515/EVNCPC-VTCNTT+KD+KT",
    "id_nv": 117058, "id_pb": 43038, "id_dv": 1833
  }'
# Kỳ vọng: search_type="exact", results có ky_hieu khớp

# Test BM25 — từ khoá
curl -X POST http://localhost:8000/api/document-search/search \
  -d '{
    "query": "GIS lưới điện chuẩn hóa",
    "id_nv": 117058, "id_pb": 43038, "id_dv": 1833
  }'
# Kỳ vọng: search_type="bm25", highlights có đoạn từ noi_dung

# Test hybrid — câu hỏi tự nhiên
curl -X POST http://localhost:8000/api/document-search/search \
  -d '{
    "query": "quy trình đồng bộ dữ liệu địa lý lưới điện là gì",
    "id_nv": 117058, "id_pb": 43038, "id_dv": 1833
  }'
# Kỳ vọng: search_type="hybrid", used_vector=true, mode_used="excerpt"

# Test ACL — user không có quyền
curl -X POST http://localhost:8000/api/document-search/search \
  -d '{
    "query": "GIS", "id_nv": 99999, "id_pb": 99999, "id_dv": 99999
  }'
# Kỳ vọng: results=[] (không có văn bản nào match quyền)
```

---

## VÍ DỤ RESPONSE

```json
{
  "query": "GIS lưới điện chuẩn hóa",
  "id_nv": 117058,
  "id_pb": 43038,
  "id_dv": 1833,
  "search_type": "bm25",
  "mode_used": "list",
  "used_vector": false,
  "total": 3,
  "results": [
    {
      "document_id": "550e8400-e29b-41d4-a716-446655440000",
      "id_vb": "1068586",
      "ky_hieu": "6515/EVNCPC-VTCNTT+KD+KT",
      "trich_yeu": "Kế hoạch xây dựng hệ thống GIS chuẩn hóa cơ sở dữ liệu lưới điện",
      "tom_tat": "...",
      "noi_ban_hanh": "Tổng công ty Điện lực miền Trung",
      "nguoi_ky": "Lê Hoàng Anh Dũng",
      "ngay_vb": "2025-08-18",
      "nam": 2025,
      "score": 12.47,
      "highlights": [
        "xây dựng hệ thống <mark>GIS</mark> chuẩn hóa cơ sở dữ liệu <mark>lưới điện</mark>",
        "triển khai <mark>GIS</mark> 110kV và trung thế theo phương án sáp nhập"
      ]
    }
  ]
}
```
