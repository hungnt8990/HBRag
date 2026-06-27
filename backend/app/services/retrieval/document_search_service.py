"""Service tìm kiếm văn bản trên ES document index (tách khỏi route/transport).

Logic tách riêng để: (1) tái sử dụng/test độc lập route; (2) route chỉ còn mỏng
(auth dependency + gọi service). Service KHÔNG phụ thuộc FastAPI — báo lỗi qua
exception domain (``DocumentSearchUnavailable`` / ``DocumentSearchError``) để route
ánh xạ sang HTTP status.
"""

from __future__ import annotations

import logging
import re
from typing import Literal

import httpx
from pydantic import BaseModel, Field

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.services.retrieval.retrieval_document_index import DocumentIndexStore
from app.services.security.security_acl_payload import AclSubject, acl_subject_to_keys

logger = logging.getLogger("document_search")


class DocumentSearchUnavailable(RuntimeError):
    """ES chưa bật -> route trả 503."""


class DocumentSearchError(RuntimeError):
    """Lỗi backend ES -> route trả 502."""


class DocumentSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000, description="Từ khoá, số ký hiệu, hoặc câu hỏi")
    id_nv: int = Field(description="Mã nhân viên — bắt buộc; id_pb/id_dv được resolve từ dm_nhan_vien")
    top_n: int = Field(default=20, ge=1, le=100, description="Số văn bản trả về")
    use_vector: bool = Field(default=True, description="Dùng BBQ kNN (False = BM25-only)")
    mode: Literal["auto", "list", "excerpt"] = Field(default="auto")


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
    highlights: list[str] = Field(default_factory=list)


class DocumentSearchResponse(BaseModel):
    query: str
    id_nv: int
    id_pb: int | None
    id_dv: int | None
    search_type: str  # exact | bm25 | hybrid
    mode_used: str  # list | excerpt
    used_vector: bool
    total: int
    results: list[DocumentSearchHit]


_SOURCE_FIELDS = [
    "document_id", "id_vb", "ky_hieu", "trich_yeu",
    "tom_tat", "noi_ban_hanh", "nguoi_ky", "ngay_vb", "nam",
]

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

_KY_HIEU_RE = re.compile(r"\d{2,5}/[A-Za-zĐ]|[A-Za-zĐ]{2,}/\d{2,}|\d{2,4}/\d{4}/[A-Z]", re.IGNORECASE)
_QUESTION_RE = re.compile(
    r"là gì|như thế nào|thế nào|ra sao|quy định|quy trình|hướng dẫn|"
    r"bao nhiêu|khi nào|ở đâu|điều kiện|ai |ai\?",
    re.IGNORECASE,
)


def detect_search_type(query: str) -> str:
    if _KY_HIEU_RE.search(query):
        return "exact"
    if len(query.split()) >= 6 or _QUESTION_RE.search(query):
        return "hybrid"
    return "bm25"


def detect_mode(query: str, requested: str) -> str:
    if requested != "auto":
        return requested
    if _QUESTION_RE.search(query) or len(query.split()) >= 8:
        return "excerpt"
    return "list"


async def _resolve_subject_from_db(id_nv: int) -> AclSubject | None:
    """Tra id_pb/id_dv THẬT từ ``dm_nhan_vien`` (dùng chung ``AclSubject.from_session``).

    None nếu id_nv không có trong danh mục.
    """
    async with AsyncSessionLocal() as session:
        return await AclSubject.from_session(session, id_nv)


async def resolve_acl_subject(id_nv: int) -> AclSubject:
    """Dựng AclSubject với id_pb/id_dv lấy TỪ dm_nhan_vien theo id_nv (id_nv là nguồn sự thật).

    KHÔNG tin id_pb/id_dv do client gửi -> chống leo thang quyền. id_nv không có trong danh
    mục (hoặc DB lỗi) -> fallback nv-only: chỉ khớp văn bản cấp đích danh nhân viên đó (~0).
    """
    try:
        subject = await _resolve_subject_from_db(id_nv)
    except Exception:
        logger.warning("Không resolve được phòng/đơn vị cho id_nv=%s -> nv-only", id_nv, exc_info=True)
        subject = None
    return subject or AclSubject(id_nv=id_nv, is_super_admin=False)


def build_acl_filters(acl_subject: AclSubject) -> list[dict]:
    if acl_subject.is_super_admin:
        return []
    clause: dict = {
        "bool": {
            "filter": [{"terms": {"acl_subjects": acl_subject_to_keys(acl_subject)}}],
            "must_not": [{"terms": {"acl_deny_nv": [acl_subject.id_nv]}}],
        }
    }
    if acl_subject.id_pb is not None:
        clause["bool"]["must_not"].append({"terms": {"acl_deny_pb": [acl_subject.id_pb]}})
    return [clause]


def build_query_body(
    query: str,
    top_n: int,
    search_type: str,
    acl_filters: list[dict],
    query_vector: list[float] | None,
) -> dict:
    if search_type == "exact":
        return {
            "size": top_n,
            "_source": _SOURCE_FIELDS,
            "highlight": _HIGHLIGHT,
            "query": {
                "bool": {
                    "should": [
                        {"term": {"ky_hieu": {"value": query, "boost": 10.0}}},
                        {"match": {"ky_hieu": {"query": query, "boost": 6.0}}},
                        {"term": {"id_vb": {"value": query, "boost": 10.0}}},
                    ],
                    "filter": acl_filters,
                    "minimum_should_match": 1,
                }
            },
        }

    should = [
        {"match": {"ky_hieu": {"query": query, "boost": 6.0}}},
        {"match": {"trich_yeu": {"query": query, "boost": 3.0}}},
        {"match": {"tom_tat": {"query": query, "boost": 2.0}}},
        {"match": {"keywords": {"query": query, "boost": 1.5}}},
        {"match": {"noi_dung": {"query": query, "boost": 1.0}}},
        {"match": {"noi_ban_hanh": {"query": query, "boost": 0.5}}},
    ]

    if search_type == "bm25" or query_vector is None:
        return {
            "size": top_n,
            "_source": _SOURCE_FIELDS,
            "highlight": _HIGHLIGHT,
            "query": {"bool": {"should": should, "filter": acl_filters}},
        }

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


async def execute_document_search(request: DocumentSearchRequest) -> DocumentSearchResponse:
    """Thực thi tìm kiếm: detect kiểu -> (embed nếu hybrid) -> ES query -> map kết quả."""
    if not settings.elasticsearch_enabled:
        raise DocumentSearchUnavailable("Elasticsearch chưa được bật.")

    # id_nv là nguồn sự thật: id_pb/id_dv resolve từ dm_nhan_vien (không tin client).
    acl_subject = await resolve_acl_subject(request.id_nv)
    search_type = detect_search_type(request.query)
    mode_used = detect_mode(request.query, request.mode)
    acl_filters = build_acl_filters(acl_subject)

    query_vector: list[float] | None = None
    used_vector = False
    if search_type == "hybrid" and request.use_vector:
        try:
            from app.services.llm_gateway import get_llm_gateway

            query_vector = await get_llm_gateway().embed_query(request.query)
            used_vector = True
        except Exception:
            logger.warning(
                "Embed query thất bại, fallback BM25 query=%r", request.query[:60], exc_info=True
            )
            search_type = "bm25"

    body = build_query_body(request.query, request.top_n, search_type, acl_filters, query_vector)
    store = DocumentIndexStore(url=settings.two_stage_document_index_url or settings.elasticsearch_url)
    await store.ensure_index()

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(f"{store.url}/{store.index_name}/_search", json=body)
        if resp.status_code >= 400:
            raise DocumentSearchError(f"ES lỗi HTTP {resp.status_code}: {resp.text[:200]}")
        data = resp.json()
    except DocumentSearchError:
        raise
    except Exception as exc:
        logger.exception("ES search lỗi query=%r", request.query[:60])
        raise DocumentSearchError(f"Lỗi kết nối ES: {exc}") from exc

    results: list[DocumentSearchHit] = []
    for hit in data.get("hits", {}).get("hits", []):
        src = hit.get("_source") or {}
        hl = hit.get("highlight") or {}
        highlights = hl.get("noi_dung", []) or hl.get("trich_yeu", [])
        results.append(
            DocumentSearchHit(
                document_id=src.get("document_id", ""),
                id_vb=src.get("id_vb"),
                ky_hieu=src.get("ky_hieu"),
                trich_yeu=src.get("trich_yeu"),
                tom_tat=src.get("tom_tat"),
                noi_ban_hanh=src.get("noi_ban_hanh"),
                nguoi_ky=src.get("nguoi_ky"),
                ngay_vb=src.get("ngay_vb"),
                nam=src.get("nam"),
                score=float(hit.get("_score") or 0.0),
                highlights=highlights,
            )
        )

    logger.info(
        "document_search id_nv=%s pb=%s dv=%s type=%s mode=%s vector=%s results=%d query=%r",
        request.id_nv, acl_subject.id_pb, acl_subject.id_dv,
        search_type, mode_used, used_vector, len(results), request.query[:60],
    )
    return DocumentSearchResponse(
        query=request.query,
        id_nv=request.id_nv,
        id_pb=acl_subject.id_pb,
        id_dv=acl_subject.id_dv,
        search_type=search_type,
        mode_used=mode_used,
        used_vector=used_vector,
        total=len(results),
        results=results,
    )
