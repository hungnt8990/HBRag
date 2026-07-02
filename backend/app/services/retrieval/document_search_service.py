"""Service tìm kiếm văn bản trên ES document index (tách khỏi route/transport).

Logic tách riêng để: (1) tái sử dụng/test độc lập route; (2) route chỉ còn mỏng
(auth dependency + gọi service). Service KHÔNG phụ thuộc FastAPI — báo lỗi qua
exception domain (``DocumentSearchUnavailable`` / ``DocumentSearchError``) để route
ánh xạ sang HTTP status.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from typing import Any, Literal

import httpx
from pydantic import BaseModel, Field

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.services.retrieval.retrieval_document_index import DocumentIndexStore
from app.services.retrieval.retrieval_doffice_bm25 import DofficeChunkBm25Store
from app.services.security.acl_bypass_users import is_bypass_user
from app.services.security.security_acl_payload import AclSubject, acl_subject_to_keys

logger = logging.getLogger("document_search")


class DocumentSearchUnavailable(RuntimeError):
    """ES chưa bật -> route trả 503."""


class DocumentSearchError(RuntimeError):
    """Lỗi backend ES -> route trả 502."""


class DocumentSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000, description="Từ khoá, số ký hiệu, hoặc câu hỏi")
    top_n: int = Field(default=20, ge=1, le=100, description="Số văn bản trả về")
    jwtToken: str | None = Field(default=None, description="JWT để lấy id_nv (bắt buộc khi type=DO)")
    type: Literal["EO", "DO"] | None = Field(
        default=None, description="DO = tra cứu DOffice (parse token lấy id_nv); EO = làm sau"
    )
    # id_nv KHÔNG truyền trực tiếp nữa: với type=DO route tự parse từ jwtToken rồi gán vào đây.
    id_nv: int | None = Field(default=None, description="Tự lấy từ jwtToken khi type=DO")
    use_vector: bool = Field(default=True, description="Dùng BBQ kNN (False = BM25-only)")
    prefer_recent: bool = Field(
        default=True, description="Ưu tiên văn bản mới (gauss decay theo ngay_vb) — vẫn giữ độ liên quan"
    )
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
    id_nv: int | None
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

# Mã THỂ THỨC văn bản (đứng trước SỐ khi tra cứu ký hiệu, vd "qd 258" = quyết định số 258).
# Khác danh sách synonym (synonym là viết tắt NỘI DUNG); đây là LOẠI văn bản để nhận diện
# truy vấn dạng tra cứu số/ký hiệu -> không cho "quyết định" (rất phổ biến) làm nhiễu BM25.
_DOC_TYPE_ABBR = {"qd", "tb", "kh", "ct", "nq", "bc", "ttr", "hd", "qc", "cv", "gm", "tl", "tt", "cd", "nd"}
_NUM_RE = re.compile(r"\d{1,5}")


def _fold_lower(s: str) -> str:
    s = unicodedata.normalize("NFD", s.lower())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return s.replace("đ", "d")


def _is_ky_hieu_lookup(query: str) -> bool:
    """True nếu query là tra cứu ký hiệu/số văn bản: '<loại?> <số>' (vd 'qd 258', '258', 'so 168').

    Điều kiện: có ≥1 token là SỐ và MỌI token đều là số / 'so' / mã thể thức -> tránh bắt
    nhầm câu nội dung có chứa số (vd 'phụ cấp 2023' -> 'phu','cap' không phải mã -> bm25).
    """
    toks = _fold_lower(query).split()
    if not toks or not any(_NUM_RE.fullmatch(t) for t in toks):
        return False
    return all(_NUM_RE.fullmatch(t) or t == "so" or t in _DOC_TYPE_ABBR for t in toks)


def _parse_ref(query: str) -> tuple[list[str], list[str]]:
    """Tách (số, mã loại) từ truy vấn tra cứu. Vd 'qd 258' -> (['258'], ['qd'])."""
    toks = _fold_lower(query).split()
    nums = [t for t in toks if _NUM_RE.fullmatch(t)]
    types = [t for t in toks if t in _DOC_TYPE_ABBR]
    return nums, types


# Năm tường minh trong câu hỏi -> map vào field `nam` (năm văn bản).
_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")

# Mã đơn vị xuất hiện trong ky_hieu -> ưu tiên văn bản CỦA đơn vị được nhắc trong câu hỏi.
# (Có thể tách ra file config sau, giống vi_synonyms.) cpcit trong ky_hieu viết là '-IT'.
_ORG_CODES = {
    "evncpc", "evn", "evnict", "cpcit", "cpc", "cpccc", "cdmt", "evnspc", "evnnpc",
    "dnpc", "khpc", "glpc", "qnpc", "qbpc", "qtpc", "ttpc", "pypc", "knpc", "dlpc", "bdpc", "klpc",
}
_ORG_ALIAS = {"cpcit": "it"}
_ORG_ISSUER_QUERY = {
    "cpcit": "cong ty cntt dien luc mien trung",
    "evncpc": "tong cong ty dien luc mien trung",
    "cpc": "tong cong ty dien luc mien trung",
}


def _extract_years(query: str) -> list[int]:
    return sorted({int(m.group()) for m in _YEAR_RE.finditer(query)})


def _extract_orgs(query: str) -> list[str]:
    toks = set(_fold_lower(query).split())
    return [t for t in toks if t in _ORG_CODES]


def _strip_org_tokens(query: str) -> str:
    """Bỏ token tên đơn vị khỏi chuỗi nội dung (org xử lý riêng bằng boost ky_hieu).

    Tránh double-count: 'tiền lương cpcit' -> nội dung chỉ còn 'tiền lương' (org 'cpcit'
    KHÔNG còn khớp lung tung trong noi_dung), org boost nhẹ ở ky_hieu lo phần ưu tiên đơn vị.
    Nếu query CHỈ gồm token org (vd 'CPCIT') -> giữ nguyên để không thành chuỗi rỗng.
    """
    kept = [t for t in query.split() if _fold_lower(t) not in _ORG_CODES]
    return " ".join(kept) if kept else query


def detect_search_type(query: str) -> str:
    if _is_ky_hieu_lookup(query):
        return "ref"  # tra cứu số/ký hiệu rời (qd 258) -> ưu tiên ky_hieu, không nhiễu noi_dung
    if _KY_HIEU_RE.search(query):
        return "exact"  # ký hiệu đầy đủ có '/' (258/QĐ-IT)
    if _extract_orgs(query):
        # Nêu đích danh đơn vị -> cần chính xác lexical: boost org hiệu quả ở bm25, không bị
        # điểm knn (ngữ nghĩa, không phân biệt được đơn vị) lấn át như ở hybrid.
        return "bm25"
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

    id_nv nằm trong danh sách bỏ qua ACL (``config/acl_bypass_users.txt``, đọc động) ->
    ``is_super_admin=True`` -> không lọc quyền, xem được TẤT CẢ.
    """
    bypass = is_bypass_user(id_nv)
    try:
        subject = await _resolve_subject_from_db(id_nv)
    except Exception:
        logger.warning("Không resolve được phòng/đơn vị cho id_nv=%s -> nv-only", id_nv, exc_info=True)
        subject = None
    if subject is None:
        return AclSubject(id_nv=id_nv, is_super_admin=bypass)
    if bypass and not subject.is_super_admin:
        return AclSubject(
            id_nv=subject.id_nv, id_dv=subject.id_dv, id_pb=subject.id_pb, is_super_admin=True
        )
    return subject


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


# Recency decay (gauss) — ưu tiên văn bản mới nhưng vẫn giữ độ liên quan (boost_mode=multiply).
# Trong vòng OFFSET ngày: không giảm điểm; sau đó giảm dần, đến SCALE thì còn ~DECAY.
_RECENCY_FIELD = "ngay_vb.date"
_RECENCY_OFFSET = "30d"
_RECENCY_SCALE = "365d"
_RECENCY_DECAY = 0.5

# Ưu tiên đơn vị được nhắc trong query: NHÂN điểm (weight) thay vì CỘNG (should boost). Vì nhân
# theo score_mode=multiply nên chỉ khuếch đại VB ĐÃ có điểm nội dung -> VB đúng đơn vị nhưng lạc
# chủ đề (điểm ~0) nhân lên vẫn ~0, KHÔNG lọt top (trước đây should boost cộng 8.0 kéo cả VB lạc
# chủ đề của đơn vị lên đầu). 3.0 đủ đưa VB đơn vị lên trên VB đơn vị khác cùng chủ đề — tinh chỉnh.
_ORG_BOOST_WEIGHT = 3.0
_ORG_ISSUER_BOOST_WEIGHT = 1.6
_RRF_K = 60
_DOC_RRF_WEIGHT = 1.0
_CHUNK_RRF_WEIGHT = 0.03


def _recency_function() -> dict:
    """Hàm gauss decay theo ngay_vb (điểm ~1 trong OFFSET ngày, giảm dần còn ~DECAY tại SCALE)."""
    return {
        "gauss": {
            _RECENCY_FIELD: {
                "origin": "now",
                "offset": _RECENCY_OFFSET,
                "scale": _RECENCY_SCALE,
                "decay": _RECENCY_DECAY,
            }
        }
    }


def _org_boost_functions(query: str) -> list[dict]:
    """Hàm NHÂN điểm cho VB CỦA đơn vị được nhắc (mã đơn vị nằm trong ``ky_hieu``, vd '-IT').

    ``filter`` + ``weight`` trong function_score (score_mode=multiply) -> ưu tiên đơn vị TRONG
    nhóm đã liên quan chủ đề, không kéo VB lạc chủ đề lên top.
    """
    functions: list[dict] = []
    for org in _extract_orgs(query):
        functions.append(
            {"filter": {"match": {"ky_hieu": _ORG_ALIAS.get(org, org)}}, "weight": _ORG_BOOST_WEIGHT}
        )
        issuer_query = _ORG_ISSUER_QUERY.get(org)
        if issuer_query:
            functions.append(
                {"filter": {"match": {"noi_ban_hanh": issuer_query}}, "weight": _ORG_ISSUER_BOOST_WEIGHT}
            )
    return functions


def _wrap_score_functions(query_block: dict, functions: list[dict]) -> dict:
    """Bọc query bằng function_score (nhân điểm) khi có hàm; không có hàm -> trả nguyên query."""
    if not functions:
        return query_block
    return {
        "function_score": {
            "query": query_block,
            "functions": functions,
            "boost_mode": "multiply",
            "score_mode": "multiply",
        }
    }


def build_query_body(
    query: str,
    top_n: int,
    search_type: str,
    acl_filters: list[dict],
    query_vector: list[float] | None,
    *,
    prefer_recent: bool = False,
    fuzzy_fallback: bool = False,
) -> dict:
    if search_type == "ref":
        # Tra cứu số/ký hiệu rời ("qd 258"). Ký hiệu lưu dạng "258/QĐ-IT" -> token [258, qd, it].
        # match_phrase "<số> <loại>" khớp ĐÚNG thứ tự số->loại -> đẩy 258/QĐ lên trên 258/BC.
        nums, types = _parse_ref(query)
        should: list[dict] = []
        for n in nums:
            for t in types:
                should.append({"match_phrase": {"ky_hieu": {"query": f"{n} {t}", "boost": 12.0}}})
            should.append({"match": {"ky_hieu": {"query": n, "boost": 4.0}}})
            should.append({"term": {"id_vb": {"value": n, "boost": 3.0}}})
        for t in types:  # loại văn bản (IDF thấp, boost nhẹ để phân biệt khi cùng số)
            should.append({"match": {"ky_hieu": {"query": t, "boost": 1.0}}})
        return {
            "size": top_n,
            "_source": _SOURCE_FIELDS,
            "highlight": _HIGHLIGHT,
            "query": {"bool": {"should": should, "filter": acl_filters, "minimum_should_match": 1}},
        }

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

    # Tách token đơn vị khỏi CHUỖI NỘI DUNG: org được ưu tiên riêng bằng boost ky_hieu (dưới),
    # không để 'cpcit' vừa khớp lung tung trong noi_dung vừa boost ky_hieu (double-count) ->
    # trước đây kéo mọi văn bản của đơn vị lên top bất kể chủ đề. Nội dung giờ chỉ còn chủ đề thật.
    content_query = _strip_org_tokens(query)

    # ký hiệu: match thường (KHÔNG fuzzy — là mã, fuzzy dễ khớp sai số văn bản).
    # nội dung primary: ưu tiên phrase + AND/high-MSM trên field body đã bỏ boilerplate (nếu index v2 có);
    # fuzzy chỉ được bật ở lượt fallback khi primary quá ít kết quả.
    content_fields = [
        "trich_yeu^4",
        "tom_tat^2.5",
        "keywords^1.5",
        "noi_dung_body^1.4",
        "noi_dung^0.6",
        "noi_ban_hanh^0.5",
    ]
    should = [
        {"match": {"ky_hieu": {"query": content_query, "boost": 6.0}}},
        {
            "multi_match": {
                "query": content_query,
                "type": "best_fields",
                "fields": content_fields,
                "operator": "and",
                "boost": 2.5,
            }
        },
        {
            "multi_match": {
                "query": content_query,
                "type": "best_fields",
                "fields": content_fields,
                "operator": "or",
                "minimum_should_match": "2<75%",
                "boost": 0.7,
            }
        },
        {
            "multi_match": {
                "query": content_query,
                "type": "phrase_prefix",
                "fields": ["trich_yeu^3", "tom_tat^2", "noi_dung_body^1", "noi_dung^0.4"],
                "boost": 0.35,
            }
        },
    ]

    # Thưởng cụm liền đúng chủ đề (vd "tiền lương") -> kéo văn bản đúng nghĩa lên trên nhiễu do
    # asciifolding fold thanh điệu ("lương" khớp nhầm "năng lượng"/"chất lượng"). Chỉ khi ≥2 từ.
    if len(content_query.split()) >= 2:
        should.append(
            {
                "multi_match": {
                    "query": content_query,
                    "type": "phrase",
                    "fields": ["trich_yeu^6", "tom_tat^3", "noi_dung_body^2", "noi_dung^0.8"],
                    "boost": 5.0,
                }
            }
        )
    if fuzzy_fallback:
        should.append(
            {
                "multi_match": {
                    "query": content_query,
                    "type": "best_fields",
                    "fields": content_fields,
                    "fuzziness": "AUTO",
                    "prefix_length": 1,
                    "max_expansions": 30,
                    "operator": "or",
                    "minimum_should_match": "2<70%",
                    "boost": 0.35,
                }
            }
        )

    # Năm tường minh -> FILTER CỨNG theo `nam` (áp cả lên knn) vì vector ngữ nghĩa KHÔNG phân
    # biệt được năm; chỉ boost trong phần BM25 sẽ bị điểm knn lấn át ở chế độ hybrid.
    years = _extract_years(query)
    extra_filters = [{"terms": {"nam": years}}] if years else []

    # Có năm tường minh -> KHÔNG ép "mới nhất" (người dùng đã chỉ định năm; filter nam lo việc đó).
    apply_recency = prefer_recent and not years
    # Điểm cuối = liên_quan × (recency nếu bật) × (org weight nếu nhắc đơn vị). Cả hai đều NHÂN
    # (function_score, score_mode=multiply) -> ưu tiên đơn vị/độ mới TRONG nhóm đã liên quan chủ đề,
    # không kéo VB lạc chủ đề lên top. Org được nhắc đã tách khỏi content_query (khớp org qua weight
    # trên ky_hieu), nên nội dung chỉ còn chủ đề thật.
    score_functions: list[dict] = []
    if apply_recency:
        score_functions.append(_recency_function())
    score_functions.extend(_org_boost_functions(query))

    combined_filter = acl_filters + extra_filters
    bool_query = {"bool": {"should": should, "filter": combined_filter, "minimum_should_match": 1}}
    scored_query = _wrap_score_functions(bool_query, score_functions)
    if search_type == "bm25" or query_vector is None:
        return {
            "size": top_n,
            "_source": _SOURCE_FIELDS,
            "highlight": _HIGHLIGHT,
            "query": scored_query,
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
            "filter": combined_filter,
        },
        # recency + org áp lên phần BM25 (knn giữ điểm ngữ nghĩa) -> hybrid vẫn nghiêng VB mới/đúng đơn vị.
        "query": scored_query,
    }


async def _search_es(store: DocumentIndexStore, body: dict[str, Any]) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(f"{store.url}/{store.index_name}/_search", json=body)
    if resp.status_code >= 400:
        raise DocumentSearchError(f"ES lỗi HTTP {resp.status_code}: {resp.text[:200]}")
    return resp.json()


def _hit_key(hit: dict[str, Any]) -> str:
    src = hit.get("_source") or {}
    return str(src.get("id_vb") or src.get("document_id") or "")


def _apply_chunk_rerank(
    doc_hits: list[dict[str, Any]],
    chunk_hits: list[dict[str, Any]],
    top_n: int,
) -> list[dict[str, Any]]:
    if not chunk_hits:
        return doc_hits

    entries: dict[str, dict[str, Any]] = {}
    for rank, hit in enumerate(doc_hits, start=1):
        key = _hit_key(hit)
        if not key:
            continue
        entries[key] = {
            "hit": hit,
            "rrf": _DOC_RRF_WEIGHT / (_RRF_K + rank),
            "chunk_highlights": [],
        }

    seen_chunk_docs: set[str] = set()
    for rank, chunk in enumerate(chunk_hits, start=1):
        key = str(chunk.get("id_vb") or chunk.get("document_id") or "")
        if not key or key in seen_chunk_docs:
            continue
        entry = entries.get(key)
        if entry is None:
            continue
        seen_chunk_docs.add(key)
        entry["rrf"] += _CHUNK_RRF_WEIGHT / (_RRF_K + rank)
        highlights = (chunk.get("highlight") or {}).get("chunk_text") or []
        if highlights and not entry["chunk_highlights"]:
            entry["chunk_highlights"] = highlights[:2]

    ranked = sorted(entries.values(), key=lambda e: e["rrf"], reverse=True)[:top_n]
    reranked: list[dict[str, Any]] = []
    for entry in ranked:
        hit = dict(entry["hit"])
        if entry["chunk_highlights"]:
            hl = dict(hit.get("highlight") or {})
            existing = hl.get("noi_dung") or hl.get("trich_yeu") or []
            hl["noi_dung"] = [*entry["chunk_highlights"], *existing][:3]
            hit["highlight"] = hl
        hit["_score"] = round(float(entry["rrf"]) * 10_000, 6)
        reranked.append(hit)
    return reranked


async def _search_chunk_evidence(query: str, top_n: int, acl_subject: AclSubject) -> list[dict[str, Any]]:
    if not settings.document_search_chunk_rerank_enabled:
        return []
    multiplier = max(1, int(settings.document_search_chunk_rerank_multiplier or 1))
    max_hits = max(top_n, int(settings.document_search_chunk_rerank_max_hits or top_n))
    chunk_top_n = min(max_hits, max(top_n * multiplier, top_n))
    try:
        return await DofficeChunkBm25Store(
            url=settings.two_stage_document_index_url or settings.elasticsearch_url,
        ).search_chunks(
            _strip_org_tokens(query),
            top_n=chunk_top_n,
            acl_subject=acl_subject,
            ensure=False,
        )
    except Exception:
        logger.warning("Chunk BM25 rerank lỗi, giữ nguyên doc-level results query=%r", query[:60], exc_info=True)
        return []


async def execute_document_search(request: DocumentSearchRequest) -> DocumentSearchResponse:
    """Thực thi tìm kiếm: detect kiểu -> (embed nếu hybrid) -> ES query -> map kết quả."""
    if not settings.elasticsearch_enabled:
        raise DocumentSearchUnavailable("Elasticsearch chưa được bật.")

    # id_nv là nguồn sự thật: id_pb/id_dv resolve từ dm_nhan_vien (không tin client).
    acl_subject = await resolve_acl_subject(request.id_nv)
    search_type = detect_search_type(request.query)
    # BM25-only: hạ "hybrid" -> "bm25" để KHÔNG gọi embed (tránh treo khi gateway embedding
    # chết). API chỉ truy vấn ES BM25 + ACL filter.
    if settings.document_search_bm25_only and search_type == "hybrid":
        search_type = "bm25"
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

    body = build_query_body(
        request.query, request.top_n, search_type, acl_filters, query_vector,
        prefer_recent=request.prefer_recent,
    )
    store = DocumentIndexStore(url=settings.two_stage_document_index_url or settings.elasticsearch_url)
    # Tìm trên index DOffice (BM25 doc-level + ACL) — nơi job đồng bộ đổ dữ liệu, KHÔNG phải
    # index two-stage cũ (hbrag_documents_v1, rỗng). KHÔNG ensure_index ở đây (job tạo/quản lý).
    store.index_name = settings.doffice_documents_index_name

    try:
        data = await _search_es(store, body)
        hits = data.get("hits", {}).get("hits", [])
        if (
            search_type not in {"exact", "ref"}
            and len(hits) < settings.document_search_fuzzy_fallback_min_results
        ):
            fallback_body = build_query_body(
                request.query, request.top_n, search_type, acl_filters, query_vector,
                prefer_recent=request.prefer_recent,
                fuzzy_fallback=True,
            )
            fallback_data = await _search_es(store, fallback_body)
            fallback_hits = fallback_data.get("hits", {}).get("hits", [])
            if len(fallback_hits) > len(hits):
                data = fallback_data
                hits = fallback_hits
        if search_type not in {"exact", "ref"}:
            chunk_hits = await _search_chunk_evidence(request.query, request.top_n, acl_subject)
            if chunk_hits:
                data = {
                    **data,
                    "hits": {
                        **(data.get("hits") or {}),
                        "hits": _apply_chunk_rerank(hits, chunk_hits, request.top_n),
                    },
                }
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
