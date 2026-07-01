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
from typing import Literal

import httpx
from pydantic import BaseModel, Field

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.services.retrieval.retrieval_document_index import DocumentIndexStore
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


def _wrap_recency(query_block: dict) -> dict:
    """Bọc query bằng function_score gauss decay theo ngay_vb -> điểm = liên_quan × độ_mới."""
    return {
        "function_score": {
            "query": query_block,
            "functions": [
                {
                    "gauss": {
                        _RECENCY_FIELD: {
                            "origin": "now",
                            "offset": _RECENCY_OFFSET,
                            "scale": _RECENCY_SCALE,
                            "decay": _RECENCY_DECAY,
                        }
                    }
                }
            ],
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
    # nội dung: fuzzy (gõ sai 1-2 ký tự) + minimum_should_match (chịu được gõ thiếu/nhiễu vài từ).
    # phrase_prefix: bắt kiểu gõ dở từ cuối ("kế hoạch cung cấp đi…").
    should = [
        {"match": {"ky_hieu": {"query": content_query, "boost": 6.0}}},
        {
            "multi_match": {
                "query": content_query,
                "type": "best_fields",
                "fields": ["trich_yeu^3", "tom_tat^2", "keywords^1.5", "noi_dung^1", "noi_ban_hanh^0.5"],
                "fuzziness": "AUTO",
                "prefix_length": 1,
                "max_expansions": 50,
                "operator": "or",
                "minimum_should_match": "2<70%",
            }
        },
        {
            "multi_match": {
                "query": content_query,
                "type": "phrase_prefix",
                "fields": ["trich_yeu^3", "tom_tat^2", "noi_dung^1"],
                "boost": 0.6,
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
                    "fields": ["trich_yeu^3", "tom_tat^2", "noi_dung^1"],
                    "boost": 4.0,
                }
            }
        )

    # Năm tường minh -> FILTER CỨNG theo `nam` (áp cả lên knn) vì vector ngữ nghĩa KHÔNG phân
    # biệt được năm; chỉ boost trong phần BM25 sẽ bị điểm knn lấn át ở chế độ hybrid.
    years = _extract_years(query)
    extra_filters = [{"terms": {"nam": years}}] if years else []
    # Org được nhắc -> boost NHẸ văn bản CỦA đơn vị đó (ky_hieu chứa mã org) như tie-breaker:
    # ưu tiên trong nhóm ĐÃ liên quan chủ đề, KHÔNG kéo văn bản lạc chủ đề lên top (trước boost=8.0
    # lấn cả điểm BM25 nội dung -> 'tiền lương cpcit' ra toàn văn bản CPCIT bất kỳ, không về lương).
    for o in _extract_orgs(query):
        should.append({"match": {"ky_hieu": {"query": _ORG_ALIAS.get(o, o), "boost": 2.0}}})

    # Có năm tường minh -> KHÔNG ép "mới nhất" (người dùng đã chỉ định năm; filter nam lo việc đó).
    apply_recency = prefer_recent and not years
    combined_filter = acl_filters + extra_filters
    bool_query = {"bool": {"should": should, "filter": combined_filter}}
    if search_type == "bm25" or query_vector is None:
        return {
            "size": top_n,
            "_source": _SOURCE_FIELDS,
            "highlight": _HIGHLIGHT,
            "query": _wrap_recency(bool_query) if apply_recency else bool_query,
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
        # recency áp lên phần BM25 (knn giữ điểm ngữ nghĩa) -> hybrid vẫn nghiêng về văn bản mới.
        "query": _wrap_recency(bool_query) if apply_recency else bool_query,
    }


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
