"""Fusion semantic cho /api/document-search/search (thiết kế 3-DB DOffice).

Luồng (3 nhánh đầu chạy SONG SONG — LLM expansion không chặn search query gốc):
  (a) nhánh GỐC: embed dense+sparse query gốc -> search Qdrant chunks + docmeta;
  (b) nhánh MỞ RỘNG: LLM sinh 1-3 query liên quan -> embed -> search Qdrant;
  (c) ES chunk BM25 (query gốc, strip org).
Sau đó: RRF weighted fusion (rank TÍNH THEO TỪNG query) -> context builder (chunk hàng xóm
±1 + chunk CHA heading/điều-mục từ PG) -> cross-encoder rerank (Qwen3-Reranker qua
LLMGateway) -> CRAG-lite (rule + LLM chấm lại candidate mơ hồ) -> retry 1 vòng khi top yếu
-> trả hits + evidence_summary. Metadata filter nam/thang bắt tường minh từ query, áp cả
Qdrant + ES; quá chặt (kết quả < 3) thì tự bỏ filter chạy lại.

Trọng số/ngưỡng cấu hình qua settings ``document_search_fusion_*`` / ``document_search_crag_*``
/ ``document_search_rerank_*`` (app/core/config.py). Log INFO 1 dòng timing từng khâu.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import unicodedata
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from sqlalchemy import and_, or_, select

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.models.chunk import Chunk
from app.services.embeddings.embedding_sparse_factory import get_sparse_embedding_provider
from app.services.llm_gateway import get_llm_gateway
from app.services.rag.rag_chunk import build_query_embedding_text
from app.services.retrieval.retrieval_doffice_bm25 import DofficeChunkBm25Store
from app.services.vector.vector_store import (
    VectorSearchResult,
    get_doffice_chunks_vector_store,
    get_doffice_docmeta_vector_store,
)

logger = logging.getLogger(__name__)

MAX_CONTEXT_CHARS_PER_CHUNK = 1800
MAX_CONTEXT_ITEMS = 8
# Cửa sổ tìm chunk CHA (heading/điều-mục) đứng trước seed trong cùng văn bản.
PARENT_LOOKBACK_CHUNKS = 40
# chunk_type được coi là "cha" (heading/điều/mục) khi mở rộng ngữ cảnh parent-child.
PARENT_CHUNK_TYPES = {"legal_clause", "document_section", "document_header"}
# Fusion cho kết quả quá ít khi có metadata filter -> bỏ filter chạy lại.
MIN_RESULTS_BEFORE_FILTER_FALLBACK = 3


@dataclass
class SemanticFusionResult:
    hits: list[dict[str, Any]]
    expanded_queries: list[str]
    used_vector: bool
    evidence_summary: str = "partial"  # strong | partial | insufficient


@dataclass
class _Candidate:
    key: str
    source: dict[str, Any] = field(default_factory=dict)
    bm25_score: float | None = None
    semantic_score: float | None = None
    fused_score: float = 0.0
    rerank_score: float | None = None
    final_score: float = 0.0
    highlights: list[str] = field(default_factory=list)
    source_flags: set[str] = field(default_factory=set)
    chunks: list[dict[str, Any]] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)
    context: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class _MetadataFilters:
    years: list[int] = field(default_factory=list)
    months: list[int] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.years or self.months)


async def run_semantic_document_fusion(
    *,
    query: str,
    top_n: int,
    acl_subject: Any,
    bm25_hits: list[dict[str, Any]],
) -> SemanticFusionResult | None:
    """Run DOffice semantic fusion for /api/document-search/search."""

    clean = " ".join(str(query or "").split()).strip()
    if not clean:
        return None
    t_start = time.perf_counter()
    filters = _extract_metadata_filters(clean)
    depth = max(top_n * 3, int(settings.document_search_fusion_candidate_k or 0))
    # Giữ nhiều candidate hơn top_n để reranker có dư địa xếp lại.
    candidate_pool = max(top_n, int(settings.document_search_rerank_top_k or top_n))

    # 3 nhánh SONG SONG: query gốc không chờ LLM expansion (trước đây expansion chặn
    # toàn bộ pipeline -> +2-4s latency mỗi request).
    original_branch, expansion_branch, chunk_bm25 = await asyncio.gather(
        _search_original_branch(clean, top_k=depth, acl_subject=acl_subject, filters=filters),
        _search_expansion_branch(clean, top_k=depth, acl_subject=acl_subject, filters=filters),
        _search_chunk_bm25(clean, top_k=depth, acl_subject=acl_subject, filters=filters),
    )
    orig_embedded, orig_chunks, orig_docmeta = original_branch
    expanded_queries, extra_embedded, extra_chunks, extra_docmeta = expansion_branch
    embedded = orig_embedded + extra_embedded
    chunk_results = orig_chunks + extra_chunks
    docmeta_results = orig_docmeta + extra_docmeta
    t_search = time.perf_counter()
    if not chunk_results and not docmeta_results and not chunk_bm25:
        return None

    candidates = _fuse_candidates(
        bm25_hits=bm25_hits,
        vector_chunks=chunk_results,
        vector_docmeta=docmeta_results,
        bm25_chunks=chunk_bm25,
        top_n=candidate_pool,
    )
    # Metadata filter quá chặt (vd năm nêu trong query nhưng dữ liệu ghi năm khác) ->
    # bỏ filter chạy lại để không trả rỗng oan.
    if filters and len(candidates) < MIN_RESULTS_BEFORE_FILTER_FALLBACK:
        chunk_results, docmeta_results, chunk_bm25 = await _gather_sources(
            embedded, clean, top_k=depth, acl_subject=acl_subject, filters=_MetadataFilters()
        )
        candidates = _fuse_candidates(
            bm25_hits=bm25_hits,
            vector_chunks=chunk_results,
            vector_docmeta=docmeta_results,
            bm25_chunks=chunk_bm25,
            top_n=candidate_pool,
        )
    if not candidates:
        return None
    t_fuse = time.perf_counter()

    await _build_context(candidates)
    t_context = time.perf_counter()
    await _apply_cross_encoder_rerank(clean, candidates)
    t_rerank = time.perf_counter()
    for candidate in candidates:
        candidate.evidence = _crag_lite_evidence(clean, candidate)
    await _llm_grade_ambiguous(clean, candidates)

    # CRAG retry: top đều yếu -> retrieve lại SÂU HƠN (cả vector lẫn BM25 chunk), 1 vòng.
    retried_flag = False
    if _top_all_weak(candidates):
        retried_flag = True
        retry_chunks, retry_docmeta, retry_bm25 = await _gather_sources(
            embedded, clean, top_k=depth * 2, acl_subject=acl_subject, filters=filters
        )
        if retry_chunks or retry_docmeta or retry_bm25:
            retried = _fuse_candidates(
                bm25_hits=bm25_hits,
                vector_chunks=retry_chunks or chunk_results,
                vector_docmeta=retry_docmeta or docmeta_results,
                bm25_chunks=retry_bm25 or chunk_bm25,
                top_n=candidate_pool,
            )
            if retried:
                candidates = retried
                await _build_context(candidates)
                await _apply_cross_encoder_rerank(clean, candidates)
                for candidate in candidates:
                    candidate.evidence = _crag_lite_evidence(clean, candidate)
    t_end = time.perf_counter()

    used_vector = any(
        candidate.source_flags & {"vector_chunk", "vector_docmeta"} for candidate in candidates
    )
    logger.info(
        "fusion timings(ms) query=%r: search=%d fuse=%d context=%d rerank=%d crag=%d total=%d "
        "expansions=%d candidates=%d retried=%s",
        clean[:60],
        int((t_search - t_start) * 1000),
        int((t_fuse - t_search) * 1000),
        int((t_context - t_fuse) * 1000),
        int((t_rerank - t_context) * 1000),
        int((t_end - t_rerank) * 1000),
        int((t_end - t_start) * 1000),
        len(expanded_queries),
        len(candidates),
        retried_flag,
    )
    return SemanticFusionResult(
        hits=[_candidate_to_hit(candidate, expanded_queries) for candidate in candidates[:top_n]],
        expanded_queries=expanded_queries,
        used_vector=used_vector,
        evidence_summary=_evidence_summary(candidates, top_n),
    )


# ============================ Query expansion (LLM) ============================


async def expand_related_queries(query: str) -> list[str]:
    clean = " ".join(str(query or "").split()).strip()
    if not clean:
        return []
    queries = [clean]
    if settings.llm_provider == "fake":
        return queries
    max_expansions = max(1, int(settings.document_search_fusion_max_expansions or 1))
    if max_expansions <= 1:
        return queries
    try:
        gateway = get_llm_gateway()
        raw = await gateway.generate(
            system_prompt=(
                "You rewrite Vietnamese enterprise document search queries. "
                "Return only a JSON array of 2-4 short related search questions in Vietnamese. "
                "Preserve identifiers, document numbers, organization codes, and years exactly "
                "as written in the original query. NEVER invent document numbers, decree/law "
                "references, or years that are not in the original query. Use Vietnamese only."
            ),
            user_prompt=f"Original query: {clean}",
            task_name="document_search_query_expansion",
        )
        for item in _parse_query_expansion(raw):
            if item not in queries:
                queries.append(item)
            if len(queries) >= max_expansions:
                break
    except Exception:
        logger.warning("Document search query expansion failed; using original query.", exc_info=True)
    return queries


def _parse_query_expansion(raw: str) -> list[str]:
    text = str(raw or "").strip()
    if not text:
        return []
    try:
        payload = json.loads(_strip_json_fence(text))
        if isinstance(payload, list):
            return [_clean_query_item(item) for item in payload if _clean_query_item(item)]
        if isinstance(payload, dict):
            values = payload.get("queries") or payload.get("questions") or []
            if isinstance(values, list):
                return [_clean_query_item(item) for item in values if _clean_query_item(item)]
    except Exception:
        pass
    lines = re.split(r"[\n;]+", text)
    return [_clean_query_item(re.sub(r"^[-*\d.)\s]+", "", line)) for line in lines if _clean_query_item(line)]


def _strip_json_fence(text: str) -> str:
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text, flags=re.IGNORECASE).strip()
        text = re.sub(r"```$", "", text).strip()
    return text


def _clean_query_item(value: Any) -> str:
    return " ".join(str(value or "").split()).strip().strip('"')[:500]


# ===================== Metadata filter tường minh từ query =====================

_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")
_MONTH_RE = re.compile(r"\bthang\s+(\d{1,2})\b", re.IGNORECASE)


def _fold_ascii(value: str) -> str:
    normalized = unicodedata.normalize("NFD", value or "")
    normalized = "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")
    return normalized.replace("Đ", "D").replace("đ", "d")


def _extract_metadata_filters(query: str) -> _MetadataFilters:
    """Bắt năm/tháng nêu TƯỜNG MINH trong query -> filter cứng nam/thang (bảo thủ:
    chỉ khi chắc chắn; fusion tự bỏ filter nếu kết quả quá ít)."""
    folded = _fold_ascii(query or "")
    years = sorted({int(m.group()) for m in _YEAR_RE.finditer(folded)})
    months: list[int] = []
    for match in _MONTH_RE.finditer(folded):
        value = int(match.group(1))
        if 1 <= value <= 12:
            months.append(value)
    # Tháng chỉ có nghĩa khi đi kèm năm (tránh "tháng 3 lương" chung chung lọc nhầm).
    if not years:
        months = []
    return _MetadataFilters(years=years, months=sorted(set(months)))


# ================== 3 nhánh search song song + embed 1 lần ==================


async def _search_original_branch(
    query: str, *, top_k: int, acl_subject: Any, filters: _MetadataFilters
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Nhánh query GỐC: embed + search 2 collection Qdrant ngay, KHÔNG chờ LLM expansion."""
    embedded = await _embed_queries([query])
    if not embedded:
        return [], [], []
    chunks, docmeta = await asyncio.gather(
        _search_qdrant_store(
            get_doffice_chunks_vector_store(), embedded, top_k=top_k, acl_subject=acl_subject, filters=filters
        ),
        _search_qdrant_store(
            get_doffice_docmeta_vector_store(), embedded, top_k=top_k, acl_subject=acl_subject, filters=filters
        ),
    )
    return embedded, chunks, docmeta


async def _search_expansion_branch(
    query: str, *, top_k: int, acl_subject: Any, filters: _MetadataFilters
) -> tuple[list[str], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Nhánh MỞ RỘNG: LLM sinh query liên quan -> embed -> search (chạy song song nhánh gốc)."""
    expanded_queries = await expand_related_queries(query)
    extras = [item for item in expanded_queries if item != query]
    if not extras:
        return expanded_queries, [], [], []
    embedded = await _embed_queries(extras, query_index_offset=1)
    if not embedded:
        return expanded_queries, [], [], []
    chunks, docmeta = await asyncio.gather(
        _search_qdrant_store(
            get_doffice_chunks_vector_store(), embedded, top_k=top_k, acl_subject=acl_subject, filters=filters
        ),
        _search_qdrant_store(
            get_doffice_docmeta_vector_store(), embedded, top_k=top_k, acl_subject=acl_subject, filters=filters
        ),
    )
    return expanded_queries, embedded, chunks, docmeta


async def _embed_queries(queries: list[str], *, query_index_offset: int = 0) -> list[dict[str, Any]]:
    """Embed dense + sparse cho từng query MỘT lần (song song), dùng chung cho cả 2
    collection Qdrant — trước đây mỗi collection tự embed lại (gấp đôi call, tuần tự)."""
    gateway = get_llm_gateway()
    sparse_provider = get_sparse_embedding_provider()

    async def _one(index: int, query: str) -> dict[str, Any] | None:
        try:
            text = build_query_embedding_text(query)
            dense = await gateway.embed_query(text)
            sparse = await sparse_provider.embed_query(text) if sparse_provider is not None else None
            return {"query": query, "query_index": index + query_index_offset, "dense": dense, "sparse": sparse}
        except Exception:
            logger.warning("Embed query thất bại query=%r", query[:80], exc_info=True)
            return None

    results = await asyncio.gather(*(_one(index, query) for index, query in enumerate(queries)))
    return [item for item in results if item is not None]


async def _gather_sources(
    embedded: list[dict[str, Any]],
    query: str,
    *,
    top_k: int,
    acl_subject: Any,
    filters: _MetadataFilters,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Search lại 3 nguồn với embeddings ĐÃ CÓ (dùng cho filter-fallback + CRAG retry)."""
    results = await asyncio.gather(
        _search_qdrant_store(
            get_doffice_chunks_vector_store(), embedded, top_k=top_k, acl_subject=acl_subject, filters=filters
        ),
        _search_qdrant_store(
            get_doffice_docmeta_vector_store(), embedded, top_k=top_k, acl_subject=acl_subject, filters=filters
        ),
        _search_chunk_bm25(query, top_k=top_k, acl_subject=acl_subject, filters=filters),
    )
    return results[0], results[1], results[2]


async def _search_qdrant_store(
    store: Any,
    embedded: list[dict[str, Any]],
    *,
    top_k: int,
    acl_subject: Any,
    filters: _MetadataFilters,
) -> list[dict[str, Any]]:
    async def _one(item: dict[str, Any]) -> list[dict[str, Any]]:
        query = str(item.get("query") or "")
        query_index = int(item.get("query_index") or 0)
        try:
            results = await store.search(
                query_vector=item["dense"],
                sparse_query=item.get("sparse"),
                top_k=top_k,
                acl_subject=acl_subject,
                years=filters.years or None,
                months=filters.months or None,
            )
        except Exception:
            logger.warning("Qdrant semantic search failed for query=%r", query[:80], exc_info=True)
            return []
        collected: list[dict[str, Any]] = []
        seen: set[str] = set()
        for rank, result in enumerate(results, start=1):
            payload = _vector_result_payload(result)
            key = str(payload.get("chunk_id") or payload.get("id_vb") or payload.get("document_id") or "")
            if not key or key in seen:
                continue
            seen.add(key)
            collected.append({
                "rank": rank,
                "query_index": query_index,
                "query": query,
                "score": float(getattr(result, "score", 0.0) or 0.0),
                "content": str(getattr(result, "content", "") or ""),
                "metadata": payload,
                "document_id": str(getattr(result, "document_id", "") or payload.get("document_id") or ""),
                "chunk_id": str(getattr(result, "chunk_id", "") or payload.get("chunk_id") or ""),
            })
        return collected

    batches = await asyncio.gather(*(_one(item) for item in embedded))
    return [row for batch in batches for row in batch]


def _vector_result_payload(result: VectorSearchResult) -> dict[str, Any]:
    metadata = dict(getattr(result, "metadata", None) or {})
    if getattr(result, "document_id", None):
        metadata.setdefault("document_id", str(result.document_id))
    if getattr(result, "chunk_id", None):
        metadata.setdefault("chunk_id", str(result.chunk_id))
    return metadata


async def _search_chunk_bm25(
    query: str,
    *,
    top_k: int,
    acl_subject: Any,
    filters: _MetadataFilters,
) -> list[dict[str, Any]]:
    if not settings.document_search_chunk_rerank_enabled:
        return []
    # Đồng bộ nhánh cũ: bỏ token đơn vị khỏi chuỗi nội dung (org xử lý riêng, tránh nhiễu BM25).
    from app.services.retrieval.document_search_service import _strip_org_tokens

    try:
        return await DofficeChunkBm25Store(
            url=settings.two_stage_document_index_url or settings.elasticsearch_url,
        ).search_chunks(
            _strip_org_tokens(query),
            top_n=top_k,
            acl_subject=acl_subject,
            ensure=False,
            years=filters.years or None,
            months=filters.months or None,
        )
    except Exception:
        logger.warning("DOffice chunk BM25 fallback failed query=%r", query[:80], exc_info=True)
        return []


# ================================ RRF fusion ================================


def _fuse_candidates(
    *,
    bm25_hits: list[dict[str, Any]],
    vector_chunks: list[dict[str, Any]],
    vector_docmeta: list[dict[str, Any]],
    bm25_chunks: list[dict[str, Any]],
    top_n: int,
) -> list[_Candidate]:
    rrf_k = int(settings.document_search_fusion_rrf_k or 60)
    candidates: dict[str, _Candidate] = {}

    for rank, hit in enumerate(bm25_hits, start=1):
        source = dict(hit.get("_source") or {})
        key = _doc_key(source)
        if not key:
            continue
        candidate = _get_candidate(candidates, key, source)
        candidate.source.update({k: v for k, v in source.items() if v not in (None, "", [])})
        score = float(hit.get("_score") or 0.0)
        candidate.bm25_score = max(candidate.bm25_score or 0.0, score)
        candidate.fused_score += float(settings.document_search_fusion_w_bm25_doc) / (rrf_k + rank)
        candidate.source_flags.add("bm25_document")
        hl = hit.get("highlight") or {}
        candidate.highlights.extend((hl.get("noi_dung") or hl.get("trich_yeu") or [])[:3])

    _add_vector_like_results(
        candidates, vector_docmeta,
        weight=float(settings.document_search_fusion_w_vector_docmeta),
        source_flag="vector_docmeta", rrf_k=rrf_k,
    )
    _add_vector_like_results(
        candidates, vector_chunks,
        weight=float(settings.document_search_fusion_w_vector_chunk),
        source_flag="vector_chunk", rrf_k=rrf_k,
    )

    for rank, chunk in enumerate(bm25_chunks, start=1):
        key = _doc_key(chunk)
        if not key:
            continue
        candidate = _get_candidate(candidates, key, _source_from_metadata(chunk))
        candidate.source.update({k: v for k, v in _source_from_metadata(chunk).items() if v not in (None, "", [])})
        score = float(chunk.get("_score") or 0.0)
        candidate.bm25_score = max(candidate.bm25_score or 0.0, score)
        candidate.fused_score += float(settings.document_search_fusion_w_bm25_chunk) / (rrf_k + rank)
        candidate.source_flags.add("bm25_chunk")
        candidate.chunks.append(_chunk_context_seed(chunk, source="bm25_chunk", rank=rank, score=score))
        highlights = (chunk.get("highlight") or {}).get("chunk_text") or []
        candidate.highlights.extend(highlights[:2])

    ranked = sorted(candidates.values(), key=lambda item: (-item.fused_score, item.key))
    for candidate in ranked:
        candidate.final_score = candidate.fused_score
    return ranked[: max(top_n, 1)]


def _add_vector_like_results(
    candidates: dict[str, _Candidate],
    results: list[dict[str, Any]],
    *,
    weight: float,
    source_flag: str,
    rrf_k: int,
) -> None:
    for item in results:
        metadata = dict(item.get("metadata") or {})
        key = _doc_key({**metadata, "document_id": item.get("document_id")})
        if not key:
            continue
        # RRF chuẩn: rank tính THEO TỪNG query (đã ghi ở _search_qdrant_store) — không dùng
        # vị trí trong list đã nối nhiều query (query thứ 2+ bị phạt oan).
        rank = int(item.get("rank") or 1)
        candidate = _get_candidate(candidates, key, _source_from_metadata(metadata))
        candidate.source.update({k: v for k, v in _source_from_metadata(metadata).items() if v not in (None, "", [])})
        score = float(item.get("score") or 0.0)
        candidate.semantic_score = max(candidate.semantic_score or 0.0, score)
        candidate.fused_score += weight / (rrf_k + rank)
        candidate.source_flags.add(source_flag)
        if source_flag == "vector_chunk":
            candidate.chunks.append(_chunk_context_seed(item, source=source_flag, rank=rank, score=score))


def _get_candidate(candidates: dict[str, _Candidate], key: str, source: dict[str, Any]) -> _Candidate:
    """Lấy/tạo candidate theo key, GỘP candidate document_id-only khi biết id_vb tương ứng.

    Guard cho dữ liệu cũ: nếu trước đó cùng văn bản được key bằng document_id (payload
    thiếu id_vb) và giờ xuất hiện key id_vb kèm document_id trùng -> merge để không tách đôi điểm.
    """
    existing = candidates.get(key)
    if existing is not None:
        return existing
    doc_id = str(source.get("document_id") or "").strip()
    id_vb = str(source.get("id_vb") or "").strip()
    if id_vb and doc_id and doc_id != key and doc_id in candidates and key == id_vb:
        old = candidates.pop(doc_id)
        old.key = key
        candidates[key] = old
        return old
    candidate = _Candidate(key=key, source=dict(source))
    candidates[key] = candidate
    return candidate


def _doc_key(payload: dict[str, Any]) -> str:
    return str(payload.get("id_vb") or payload.get("document_id") or "").strip()


def _source_from_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    # Ép kiểu về đúng schema DocumentSearchHit: payload Qdrant docmeta có thể lưu
    # id_vb/nam là int, ngay_vb không phải str -> tránh pydantic ValidationError (500).
    id_vb = metadata.get("id_vb")
    ngay_vb = metadata.get("ngay_vb") or metadata.get("issued_date")
    return {
        "document_id": str(metadata.get("document_id") or ""),
        "id_vb": str(id_vb) if id_vb not in (None, "") else None,
        "ky_hieu": metadata.get("ky_hieu") or metadata.get("document_code"),
        "trich_yeu": metadata.get("trich_yeu") or metadata.get("document_title") or metadata.get("subject"),
        "tom_tat": metadata.get("tom_tat"),
        "noi_ban_hanh": metadata.get("noi_ban_hanh") or metadata.get("issuing_org"),
        "nguoi_ky": metadata.get("nguoi_ky"),
        "ngay_vb": str(ngay_vb) if ngay_vb not in (None, "") else None,
        "nam": _optional_int(metadata.get("nam")),
    }


def _chunk_context_seed(payload: dict[str, Any], *, source: str, rank: int, score: float) -> dict[str, Any]:
    metadata = dict(payload.get("metadata") or payload)
    content = str(payload.get("content") or metadata.get("text") or metadata.get("content") or metadata.get("chunk_text") or "")
    return {
        "chunk_id": str(payload.get("chunk_id") or metadata.get("chunk_id") or ""),
        "document_id": str(payload.get("document_id") or metadata.get("document_id") or ""),
        "chunk_index": _optional_int(payload.get("chunk_index") or metadata.get("chunk_index")),
        "chunk_type": payload.get("chunk_type") or metadata.get("chunk_type"),
        "content": content,
        "metadata": metadata,
        "source": source,
        "rank": rank,
        "score": score,
    }


# ===================== Context builder (hàng xóm + cha) =====================


async def _build_context(candidates: list[_Candidate]) -> None:
    seed_chunk_ids = []
    for candidate in candidates:
        for chunk in candidate.chunks[:4]:
            chunk_id = _safe_uuid(chunk.get("chunk_id"))
            if chunk_id is not None:
                seed_chunk_ids.append(chunk_id)
    db_context = await _load_db_context(seed_chunk_ids)
    by_seed: dict[str, list[dict[str, Any]]] = {}
    for item in db_context:
        seed_id = str(item.get("seed_chunk_id") or item.get("chunk_id") or "")
        by_seed.setdefault(seed_id, []).append(item)

    for candidate in candidates:
        merged: list[dict[str, Any]] = []
        seen: set[str] = set()
        for chunk in candidate.chunks:
            chunk_id = str(chunk.get("chunk_id") or "")
            for db_chunk in by_seed.get(chunk_id, []):
                _append_context(merged, seen, db_chunk)
            _append_context(merged, seen, chunk)
        # Văn bản đọc liền mạch: sắp theo (document_id, chunk_index); chunk thiếu index xếp cuối.
        merged.sort(key=lambda item: (
            str(item.get("document_id") or ""),
            item.get("chunk_index") if isinstance(item.get("chunk_index"), int) else 1 << 30,
        ))
        candidate.context = merged[:MAX_CONTEXT_ITEMS]


async def _load_db_context(chunk_ids: list[UUID]) -> list[dict[str, Any]]:
    """Kéo từ PG: chunk hàng xóm ±1 + chunk CHA (heading/điều-mục gần nhất đứng trước seed).

    Gộp thành 2 query OR (trước đây mỗi seed 1 query -> N+1)."""
    if not chunk_ids:
        return []
    try:
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(Chunk).where(Chunk.id.in_(chunk_ids)))
            seeds = list(result.scalars().all())
            if not seeds:
                return []
            wanted: dict[UUID, str] = {chunk.id: str(chunk.id) for chunk in seeds}

            neighbor_conditions = [
                and_(
                    Chunk.document_id == seed.document_id,
                    Chunk.chunk_index >= max(0, int(seed.chunk_index) - 1),
                    Chunk.chunk_index <= int(seed.chunk_index) + 1,
                )
                for seed in seeds
            ]
            result = await session.execute(
                select(Chunk).where(or_(*neighbor_conditions)).order_by(Chunk.chunk_index.asc())
            )
            neighbors = list(result.scalars().all())
            for neighbor in neighbors:
                seed_id = next(
                    (
                        str(seed.id)
                        for seed in seeds
                        if seed.document_id == neighbor.document_id
                        and abs(int(seed.chunk_index) - int(neighbor.chunk_index)) <= 1
                    ),
                    None,
                )
                if seed_id is not None:
                    wanted.setdefault(neighbor.id, seed_id)

            # Chunk CHA: heading/điều-mục gần nhất ĐỨNG TRƯỚC seed (parent-child theo cấu trúc
            # văn bản). Lấy 1 cửa sổ lookback rồi chọn nearest per seed trong Python.
            parent_conditions = [
                and_(
                    Chunk.document_id == seed.document_id,
                    Chunk.chunk_index < int(seed.chunk_index),
                    Chunk.chunk_index >= max(0, int(seed.chunk_index) - PARENT_LOOKBACK_CHUNKS),
                )
                for seed in seeds
                if int(seed.chunk_index) > 0
            ]
            parents_pool: list[Chunk] = []
            if parent_conditions:
                result = await session.execute(
                    select(Chunk).where(or_(*parent_conditions)).order_by(Chunk.chunk_index.asc())
                )
                parents_pool = [
                    chunk
                    for chunk in result.scalars().all()
                    if str((chunk.chunk_metadata or {}).get("chunk_type") or "") in PARENT_CHUNK_TYPES
                ]
            for seed in seeds:
                nearest: Chunk | None = None
                for chunk in parents_pool:
                    if chunk.document_id != seed.document_id:
                        continue
                    if int(chunk.chunk_index) >= int(seed.chunk_index):
                        continue
                    if nearest is None or int(chunk.chunk_index) > int(nearest.chunk_index):
                        nearest = chunk
                if nearest is not None:
                    wanted.setdefault(nearest.id, str(seed.id))

            result = await session.execute(
                select(Chunk).where(Chunk.id.in_(list(wanted))).order_by(Chunk.chunk_index.asc())
            )
            chunks = list(result.scalars().all())
    except Exception:
        logger.warning("Document search context DB expansion failed.", exc_info=True)
        return []

    return [_chunk_model_context(chunk, seed_chunk_id=wanted.get(chunk.id)) for chunk in chunks]


def _chunk_model_context(chunk: Chunk, *, seed_chunk_id: str | None) -> dict[str, Any]:
    metadata = dict(chunk.chunk_metadata or {})
    return {
        "chunk_id": str(chunk.id),
        "seed_chunk_id": seed_chunk_id,
        "document_id": str(chunk.document_id),
        "chunk_index": chunk.chunk_index,
        "chunk_type": metadata.get("chunk_type"),
        "content": str(chunk.content or "")[:MAX_CONTEXT_CHARS_PER_CHUNK],
        "metadata": metadata,
        "source": "postgres_context",
        "score": None,
    }


def _append_context(target: list[dict[str, Any]], seen: set[str], item: dict[str, Any]) -> None:
    key = str(item.get("chunk_id") or item.get("content") or "")[:200]
    if not key or key in seen:
        return
    seen.add(key)
    clean = dict(item)
    clean["content"] = str(clean.get("content") or "")[:MAX_CONTEXT_CHARS_PER_CHUNK]
    target.append(clean)


# ======================= Cross-encoder rerank (LLMGateway) =======================


async def _apply_cross_encoder_rerank(query: str, candidates: list[_Candidate]) -> None:
    """Rerank top candidates bằng cross-encoder (Qwen3-Reranker qua gateway); điểm cuối =
    w*rerank_norm + (1-w)*rrf_norm. Reranker lỗi -> giữ nguyên thứ tự RRF (fallback an toàn)."""
    for candidate in candidates:
        candidate.final_score = candidate.fused_score
    if not settings.document_search_rerank_enabled or len(candidates) < 2:
        return
    top = candidates[: max(2, int(settings.document_search_rerank_top_k or len(candidates)))]
    try:
        from app.services.rerankers.reranker_base import RerankCandidate

        entries = [
            RerankCandidate(chunk_id=candidate.key, content=_rerank_content(candidate))
            for candidate in top
        ]
        scores = await get_llm_gateway().rerank(query=query, candidates=entries)
    except Exception:
        logger.warning("Cross-encoder rerank lỗi -> giữ thứ tự RRF query=%r", query[:60], exc_info=True)
        return
    by_key = {str(score.chunk_id): float(score.score) for score in scores or []}
    if not by_key:
        return

    fused_values = [candidate.fused_score for candidate in top]
    rerank_values = list(by_key.values())
    fused_lo, fused_span = min(fused_values), (max(fused_values) - min(fused_values)) or 1.0
    rerank_lo, rerank_span = min(rerank_values), (max(rerank_values) - min(rerank_values)) or 1.0
    weight = min(1.0, max(0.0, float(settings.document_search_rerank_weight)))

    for candidate in top:
        fused_norm = (candidate.fused_score - fused_lo) / fused_span
        rerank_raw = by_key.get(candidate.key)
        if rerank_raw is None:
            candidate.final_score = (1.0 - weight) * fused_norm
            continue
        candidate.rerank_score = rerank_raw
        rerank_norm = (rerank_raw - rerank_lo) / rerank_span
        candidate.final_score = weight * rerank_norm + (1.0 - weight) * fused_norm
    # Candidate ngoài top rerank: giữ dưới nhóm được rerank (điểm RRF gốc luôn < 1 sau chuẩn hoá).
    candidates.sort(key=lambda item: (-item.final_score, item.key))


def _rerank_content(candidate: _Candidate) -> str:
    parts: list[str] = []
    trich_yeu = str(candidate.source.get("trich_yeu") or "").strip()
    if trich_yeu:
        parts.append(trich_yeu)
    tom_tat = str(candidate.source.get("tom_tat") or "").strip()
    if tom_tat:
        parts.append(tom_tat[:400])
    best_chunk = max(
        candidate.chunks,
        key=lambda chunk: float(chunk.get("score") or 0.0),
        default=None,
    )
    if best_chunk is not None:
        parts.append(str(best_chunk.get("content") or "")[:900])
    elif candidate.highlights:
        parts.append(re.sub(r"</?mark>", "", " ... ".join(candidate.highlights[:3])))
    return "\n".join(part for part in parts if part)[:1500] or candidate.key


# ============================== CRAG-lite ==============================


def _crag_lite_evidence(query: str, candidate: _Candidate) -> dict[str, Any]:
    text = "\n".join(str(item.get("content") or "") for item in candidate.context)
    text += "\n" + " ".join(str(value or "") for value in candidate.source.values())
    query_tokens = _tokens(query)
    evidence_tokens = _tokens(text)
    overlap = query_tokens & evidence_tokens
    coverage = len(overlap) / max(len(query_tokens), 1)
    source_count = len(candidate.source_flags)
    support_count = sum(1 for item in candidate.context if str(item.get("content") or "").strip())
    strong_coverage = float(settings.document_search_crag_strong_coverage)
    ambiguous_coverage = float(settings.document_search_crag_ambiguous_coverage)

    if support_count == 0:
        status = "weak"
        reason = "Khong co chunk/context lam can cu truc tiep."
    elif coverage >= strong_coverage and (source_count >= 2 or support_count >= 2):
        status = "strong"
        reason = "Nhieu nguon ho tro va noi dung context phu hop truy van."
    elif coverage >= ambiguous_coverage:
        status = "ambiguous"
        reason = "Co can cu lien quan nhung muc phu hop chua du manh."
    else:
        status = "weak"
        reason = "Context thu hoi chua bao phu du y chinh cua truy van."
    return {
        "status": status,
        "reason": reason,
        "support_count": support_count,
        "coverage": round(coverage, 4),
        "matched_terms": sorted(overlap)[:12],
        "source_flags": sorted(candidate.source_flags),
    }


async def _llm_grade_ambiguous(query: str, candidates: list[_Candidate]) -> None:
    """CRAG hybrid: LLM chấm lại các candidate 'ambiguous' ở top (1 call batch).

    Lỗi/parse fail -> giữ verdict rule-based (không chặn kết quả)."""
    if not settings.document_search_crag_llm_grading or settings.llm_provider == "fake":
        return
    limit = max(1, int(settings.document_search_crag_llm_grading_max or 1))
    targets = [
        candidate
        for candidate in candidates[: max(limit, MAX_CONTEXT_ITEMS)]
        if candidate.evidence.get("status") == "ambiguous"
    ][:limit]
    if not targets:
        return
    lines: list[str] = []
    for candidate in targets:
        excerpt = " ".join(
            str(item.get("content") or "")[:400] for item in candidate.context[:2]
        ).strip()
        lines.append(
            json.dumps(
                {
                    "key": candidate.key,
                    "ky_hieu": candidate.source.get("ky_hieu"),
                    "trich_yeu": candidate.source.get("trich_yeu"),
                    "evidence": excerpt[:900],
                },
                ensure_ascii=False,
            )
        )
    try:
        raw = await get_llm_gateway().generate(
            system_prompt=(
                "You grade Vietnamese document-search evidence. For each candidate decide if "
                "its evidence answers the query: strong (directly answers), ambiguous (related "
                "but incomplete), weak (irrelevant). Return ONLY a JSON array of "
                '{"key": string, "verdict": "strong"|"ambiguous"|"weak", "reason": string}.'
            ),
            user_prompt="Query: " + query + "\nCandidates:\n" + "\n".join(lines),
            task_name="document_search_crag_grading",
        )
        payload = json.loads(_strip_json_fence(str(raw or "").strip()))
    except Exception:
        logger.warning("CRAG LLM grading lỗi -> giữ verdict rule-based.", exc_info=True)
        return
    if not isinstance(payload, list):
        return
    verdicts = {
        str(item.get("key")): item
        for item in payload
        if isinstance(item, dict) and str(item.get("verdict") or "") in {"strong", "ambiguous", "weak"}
    }
    for candidate in targets:
        verdict = verdicts.get(candidate.key)
        if verdict is None:
            continue
        candidate.evidence = {
            **candidate.evidence,
            "status": str(verdict["verdict"]),
            "reason": str(verdict.get("reason") or candidate.evidence.get("reason") or ""),
            "graded_by": "llm",
        }


def _top_all_weak(candidates: list[_Candidate]) -> bool:
    top = candidates[: min(3, len(candidates))]
    return bool(top) and all(item.evidence.get("status") == "weak" for item in top)


def _evidence_summary(candidates: list[_Candidate], top_n: int) -> str:
    """Cờ tổng cấp response: strong | partial | insufficient ("thiếu căn cứ")."""
    top = candidates[: min(3, top_n, len(candidates))]
    if not top:
        return "insufficient"
    statuses = [str(item.evidence.get("status") or "weak") for item in top]
    if all(status == "weak" for status in statuses):
        return "insufficient"
    if any(status == "strong" for status in statuses):
        return "strong"
    return "partial"


def _tokens(value: str) -> set[str]:
    normalized = _fold_ascii(value).casefold()
    stopwords = {"la", "gi", "va", "cua", "co", "cho", "trong", "the", "nao", "ve", "cac", "nhung"}
    return {token for token in re.findall(r"[a-z0-9]{2,}", normalized) if token not in stopwords}


def _candidate_to_hit(candidate: _Candidate, expanded_queries: list[str]) -> dict[str, Any]:
    source = dict(candidate.source)
    source.setdefault("document_id", candidate.key)
    return {
        "_source": source,
        "_score": round((candidate.final_score or candidate.fused_score) * 10000, 6),
        "highlight": {"noi_dung": candidate.highlights[:3]},
        "_semantic": {
            "bm25_score": candidate.bm25_score,
            "semantic_score": candidate.semantic_score,
            "fused_score": candidate.fused_score,
            "rerank_score": candidate.rerank_score,
            "evidence": candidate.evidence,
            "context": candidate.context,
            "expanded_queries": expanded_queries,
            "source_flags": sorted(candidate.source_flags),
        },
    }


def _safe_uuid(value: Any) -> UUID | None:
    try:
        return UUID(str(value))
    except (TypeError, ValueError):
        return None


def _optional_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
