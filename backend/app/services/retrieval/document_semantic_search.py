"""Fusion semantic cho /api/document-search/search (thiết kế 3-DB DOffice).

Luồng (3 nhánh đầu chạy SONG SONG — LLM expansion không chặn search query gốc):
  (a) nhánh GỐC: embed dense query gốc -> search Qdrant chunks + docmeta (dense-only;
      lexical do ES BM25 nhánh (c) đảm nhiệm — sparse Qdrant tắt qua sparse_embedding_enabled);
  (b) nhánh MỞ RỘNG: LLM sinh 1-3 query liên quan -> embed -> search Qdrant;
  (c) ES chunk BM25 (query gốc, strip org).
Sau đó: RRF weighted fusion (rank TÍNH THEO TỪNG query) -> enrich metadata văn bản từ index
nguồn `kho_ai_dung_chung` (document_no/title/summary/signer — ES chunk + Qdrant payload không
có) -> context builder (chunk hàng xóm ±1 + chunk CHA heading/điều-mục từ ES chunk index —
pipeline kho AI KHÔNG ghi chunk vào PG) -> cross-encoder rerank (bge-reranker qua LLMGateway)
-> CRAG-lite (rule + LLM chấm lại candidate mơ hồ) -> retry 1 vòng khi top yếu -> trả hits +
evidence_summary. Metadata filter năm/tháng bắt tường minh từ query, áp cả Qdrant (DatetimeRange
trên issue_date) + ES (range issue_date); quá chặt (kết quả < 3) thì tự bỏ filter chạy lại.

2026-07-06: remap toàn bộ sang SCHEMA BA (document_id/document_no/title/summary/issue_date/
id_full/chunk_order) — bỏ tên cũ id_vb/ky_hieu/trich_yeu/nam/thang.

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
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from app.core.config import settings
from app.services.embeddings.embedding_sparse_factory import get_sparse_embedding_provider
from app.services.llm_gateway import get_llm_gateway
from app.services.rag.rag_chunk import build_query_embedding_text
from app.services.retrieval.retrieval_doffice_bm25 import (
    DofficeBm25DocumentStore,
    DofficeChunkBm25Store,
)
from app.services.retrieval.retrieval_profile import get_retrieval_profile
from app.services.retrieval.retrieval_shared import TtlCache
from app.services.vector.vector_store import (
    VectorSearchResult,
    get_doffice_chunks_vector_store,
    get_doffice_docmeta_vector_store,
)

logger = logging.getLogger(__name__)

# Cache theo query (TTL): người dùng lặp lại/chỉnh nhẹ truy vấn rất phổ biến -> khỏi gọi lại
# LLM expansion (~1-2s) và embed (~100-300ms). TTL ngắn để đổi cấu hình/model không dính lâu.
_EXPANSION_CACHE = TtlCache(maxsize=256, ttl_seconds=600.0)
_EMBED_CACHE = TtlCache(maxsize=512, ttl_seconds=600.0)

# Phạm vi văn bản (list document_id) cho MỘT lần fusion — set ở đầu ``run_semantic_document_fusion``,
# đọc ở các nhánh search leaf (Qdrant + ES BM25). Dùng ContextVar để KHÔNG phải thay đổi chữ ký hàng loạt
# hàm trung gian, và để phạm vi LUÔN được áp (không bị nhánh filter-fallback/CRAG-retry vô tình bỏ như
# metadata filter năm/tháng). ContextVar an toàn đa request: mỗi request/asyncio task có bản sao riêng.
_DOC_SCOPE: ContextVar[frozenset[str] | None] = ContextVar("doffice_doc_scope", default=None)

# Giới hạn context + chunk_type "cha" đọc từ retrieval profile (cấu hình domain).
_PROFILE = get_retrieval_profile()
MAX_CONTEXT_CHARS_PER_CHUNK = _PROFILE.max_context_chars_per_chunk
MAX_CONTEXT_ITEMS = _PROFILE.max_context_items
# Cửa sổ tìm chunk CHA (heading/điều-mục) đứng trước seed trong cùng văn bản.
PARENT_LOOKBACK_CHUNKS = _PROFILE.parent_lookback_chunks
# chunk_type được coi là "cha" (heading/điều/mục) khi mở rộng ngữ cảnh parent-child.
PARENT_CHUNK_TYPES = set(_PROFILE.parent_chunk_types)
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
    bm25_hits: list[dict[str, Any]] | None = None,
    bm25_hits_task: asyncio.Future[tuple[dict[str, Any], list[dict[str, Any]]]] | None = None,
    document_ids: set[str] | None = None,
) -> SemanticFusionResult | None:
    """Run DOffice semantic fusion for /api/document-search/search.

    ``bm25_hits_task``: task ES BM25 doc-level đang chạy SONG SONG (trả ``(data, hits)``) —
    fusion chỉ cần hits ở bước trộn RRF nên await MUỘN (sau 3 nhánh search) thay vì bắt
    service chờ tuần tự. Truyền ``bm25_hits`` (list) khi đã có sẵn (test/legacy).

    ``document_ids``: GIỚI HẠN tra cứu trong danh sách văn bản (``document_id``) này — dùng cho
    chat trên 1 nhóm văn bản. ``None``/rỗng = không giới hạn (chỉ lọc ACL, tức toàn bộ văn bản
    người dùng được phép). Áp CỨNG ở cả Qdrant lẫn ES BM25, KHÔNG bị bỏ ở nhánh fallback.
    """

    clean = " ".join(str(query or "").split()).strip()
    if not clean:
        return None
    scope_token = _DOC_SCOPE.set(frozenset(document_ids) if document_ids else None)
    try:
        return await _run_fusion_inner(clean, top_n=top_n, acl_subject=acl_subject,
                                       bm25_hits=bm25_hits, bm25_hits_task=bm25_hits_task)
    finally:
        _DOC_SCOPE.reset(scope_token)


async def _run_fusion_inner(
    clean: str,
    *,
    top_n: int,
    acl_subject: Any,
    bm25_hits: list[dict[str, Any]] | None,
    bm25_hits_task: asyncio.Future[tuple[dict[str, Any], list[dict[str, Any]]]] | None,
) -> SemanticFusionResult | None:
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
    # Await MUỘN task BM25 doc-level (đã chạy song song với 3 nhánh trên). Lỗi ES sẽ
    # propagate lên service (giữ hành vi cũ: ES chết -> request lỗi, không nuốt).
    if bm25_hits_task is not None:
        _, bm25_hits = await bm25_hits_task
    bm25_hits = bm25_hits or []

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

    # Enrich metadata văn bản (document_no/title/summary/signer) từ index nguồn — ES chunk
    # và Qdrant payload KHÔNG mang các field này; rerank/citation/identifier-boost cần chúng.
    await _enrich_doc_sources(candidates, acl_subject=acl_subject)
    # Candidate chỉ trúng qua docmeta/BM25 doc-level (không có chunk nào) -> kéo top chunk
    # thật từ ES để passage/rerank có NỘI DUNG thay vì chỉ title/summary.
    await _expand_chunkless_candidates(clean, candidates, acl_subject=acl_subject)
    await _build_context(candidates, acl_subject=acl_subject)
    t_context = time.perf_counter()
    await _apply_cross_encoder_rerank(clean, candidates)
    t_rerank = time.perf_counter()
    for candidate in candidates:
        candidate.evidence = _crag_lite_evidence(clean, candidate)
    # Boost định danh: query hỗn hợp nội dung+mã -> đưa exact-doc lên top (giữ semantic phía
    # dưới). Chạy TRƯỚC LLM grading (boost chỉ đổi điểm + evidence, không cần verdict LLM).
    _apply_identifier_boost(clean, candidates)
    # LLM chỉ chấm lại khi top-3 CHƯA có căn cứ mạnh: khi đã có strong thì verdict LLM không
    # đổi evidence_summary lẫn quyết định retry (chỉ tinh nhãn candidate phía dưới) mà tốn
    # ~1-3s mỗi request — đo thực tế khâu này từng chiếm ~80% latency warm.
    if not any(
        c.evidence.get("status") == "strong" for c in candidates[: min(3, len(candidates))]
    ):
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
                await _enrich_doc_sources(candidates, acl_subject=acl_subject)
                await _expand_chunkless_candidates(clean, candidates, acl_subject=acl_subject)
                await _build_context(candidates, acl_subject=acl_subject)
                await _apply_cross_encoder_rerank(clean, candidates)
                for candidate in candidates:
                    candidate.evidence = _crag_lite_evidence(clean, candidate)
                _apply_identifier_boost(clean, candidates)
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
    cached = _EXPANSION_CACHE.get(clean)
    if cached is not None:
        return list(cached)
    try:
        gateway = get_llm_gateway()
        # Deadline: LLM chậm quá -> bỏ expansion (nhánh chậm nhất khâu search), dùng query gốc.
        timeout_s = float(settings.document_search_fusion_expansion_timeout_s or 0) or None
        raw = await asyncio.wait_for(
            gateway.generate(
                system_prompt=(
                    "You rewrite Vietnamese enterprise document search queries. "
                    "Return only a JSON array of 2-4 short related search questions in Vietnamese. "
                    "Preserve identifiers, document numbers, organization codes, and years exactly "
                    "as written in the original query. NEVER invent document numbers, decree/law "
                    "references, or years that are not in the original query. Use Vietnamese only."
                ),
                user_prompt=f"Original query: {clean}",
                task_name="document_search_query_expansion",
            ),
            timeout=timeout_s,
        )
        for item in _parse_query_expansion(raw):
            if item not in queries:
                queries.append(item)
            if len(queries) >= max_expansions:
                break
        # Chỉ cache khi LLM trả lời thành công (lỗi/timeout có thể là nhất thời).
        _EXPANSION_CACHE.put(clean, list(queries))
    except TimeoutError:
        logger.warning(
            "Query expansion quá %.1fs -> bỏ, dùng query gốc query=%r", timeout_s, clean[:60]
        )
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
    """Embed dense cho từng query MỘT lần (song song), dùng chung cho cả 2 collection
    Qdrant — trước đây mỗi collection tự embed lại (gấp đôi call, tuần tự). Sparse chỉ
    embed khi provider bật (mặc định TẮT -> None, search Qdrant dense-only)."""
    gateway = get_llm_gateway()
    sparse_provider = get_sparse_embedding_provider()

    async def _one(index: int, query: str) -> dict[str, Any] | None:
        try:
            text = build_query_embedding_text(query)
            cached = _EMBED_CACHE.get(text)
            if cached is not None:
                dense, sparse = cached
            else:
                dense = await gateway.embed_query(text)
                sparse = await sparse_provider.embed_query(text) if sparse_provider is not None else None
                _EMBED_CACHE.put(text, (dense, sparse))
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
        scope = _DOC_SCOPE.get()
        try:
            results = await store.search(
                query_vector=item["dense"],
                sparse_query=item.get("sparse"),
                top_k=top_k,
                acl_subject=acl_subject,
                document_ids=set(scope) if scope else None,
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
            key = str(payload.get("chunk_id") or payload.get("id") or payload.get("document_id") or "")
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

    scope = _DOC_SCOPE.get()
    try:
        return await DofficeChunkBm25Store(
            url=settings.two_stage_document_index_url or settings.elasticsearch_url,
        ).search_chunks(
            _strip_org_tokens(query),
            top_n=top_k,
            acl_subject=acl_subject,
            ensure=False,
            document_ids=set(scope) if scope else None,
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
        candidate.highlights.extend((hl.get("ocr_content") or hl.get("title") or [])[:3])

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
    """Lấy/tạo candidate theo key (``document_id`` — có mặt ở MỌI payload kho AI)."""
    existing = candidates.get(key)
    if existing is not None:
        return existing
    candidate = _Candidate(key=key, source=dict(source))
    candidates[key] = candidate
    return candidate


def _doc_key(payload: dict[str, Any]) -> str:
    return str(payload.get("document_id") or payload.get("id_full") or payload.get("id") or "").strip()


def _source_from_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    """Source nội bộ theo KEY BA chuẩn (document_no/title/summary/issue_date...). Payload
    Qdrant/record ES chunk chỉ điền được một phần — phần còn lại do _enrich_doc_sources bù.
    Ép str để tránh pydantic ValidationError (payload có thể lưu số)."""
    issue_date = metadata.get("issue_date")
    issue_date_str = str(issue_date)[:10] if issue_date not in (None, "") else None
    issue_year = _optional_int(metadata.get("issue_year"))
    if issue_year is None and issue_date_str and issue_date_str[:4].isdigit():
        issue_year = int(issue_date_str[:4])
    return {
        "document_id": str(metadata.get("document_id") or ""),
        "id_full": str(metadata.get("id_full") or metadata.get("id") or "") or None,
        "document_no": metadata.get("document_no"),
        "title": metadata.get("title"),
        "summary": metadata.get("summary"),
        "issuer_org_name": metadata.get("issuer_org_name"),
        "signer": metadata.get("signer"),
        "issue_date": issue_date_str,
        "issue_year": issue_year,
    }


def _chunk_context_seed(payload: dict[str, Any], *, source: str, rank: int, score: float) -> dict[str, Any]:
    metadata = dict(payload.get("metadata") or payload)
    content = str(payload.get("content") or metadata.get("text") or metadata.get("content") or metadata.get("chunk_text") or "")
    return {
        "chunk_id": str(payload.get("chunk_id") or metadata.get("chunk_id") or ""),
        "document_id": str(payload.get("document_id") or metadata.get("document_id") or ""),
        # id_full + chunk_order (schema BA): khoá mở rộng ngữ cảnh trên ES chunk index.
        "id_full": str(payload.get("id_full") or metadata.get("id_full") or ""),
        "chunk_index": _optional_int(
            payload.get("chunk_order") or metadata.get("chunk_order")
            or payload.get("chunk_index") or metadata.get("chunk_index")
        ),
        "chunk_type": payload.get("chunk_type") or metadata.get("chunk_type"),
        "section_path": payload.get("section_path") or metadata.get("section_path"),
        "content": content,
        "metadata": metadata,
        "source": source,
        "rank": rank,
        "score": score,
    }


# ================= Enrich metadata văn bản từ index nguồn =================


async def _enrich_doc_sources(candidates: list[_Candidate], *, acl_subject: Any) -> None:
    """Bù metadata văn bản (document_no/title/summary/signer/issuer_org_name) từ index nguồn
    `kho_ai_dung_chung` — ES chunk chỉ mang title, Qdrant payload không mang các field này.
    1 call _search theo terms document_id (kèm ACL), KHÔNG đè giá trị đã có."""
    doc_ids = {
        str(candidate.source.get("document_id") or candidate.key).strip()
        for candidate in candidates
        if not (candidate.source.get("document_no") and candidate.source.get("title"))
    }
    doc_ids.discard("")
    if not doc_ids:
        return
    store = DofficeBm25DocumentStore(
        url=settings.two_stage_document_index_url or settings.elasticsearch_url,
    )
    try:
        sources = await store.fetch_doc_sources(sorted(doc_ids), acl_subject=acl_subject)
    except Exception:
        logger.warning("Enrich doc-source từ index nguồn lỗi — giữ metadata sẵn có.", exc_info=True)
        return
    for candidate in candidates:
        doc_id = str(candidate.source.get("document_id") or candidate.key).strip()
        src = sources.get(doc_id)
        if not src:
            continue
        issue_date = str(src.get("issue_date") or "")
        enriched = {
            "document_no": src.get("document_no"),
            "title": src.get("title"),
            "summary": src.get("summary"),
            "signer": src.get("signer"),
            "issuer_org_name": src.get("issuer_org_name"),
            "issue_date": issue_date[:10] or None,
            "issue_year": src.get("issue_year"),
        }
        for key, value in enriched.items():
            if value not in (None, "") and candidate.source.get(key) in (None, ""):
                candidate.source[key] = value


# Số candidate không-chunk được kéo chunk từ ES + số chunk mỗi doc (docmeta->chunk expansion).
_CHUNKLESS_EXPAND_MAX_DOCS = 5
_CHUNKLESS_EXPAND_TOP_CHUNKS = 3


async def _expand_chunkless_candidates(
    query: str, candidates: list[_Candidate], *, acl_subject: Any
) -> None:
    """Candidate KHÔNG có chunk (chỉ trúng docmeta semantic / BM25 doc-level) -> BM25 top chunk
    của chính văn bản đó từ ES chunk index (filter document_id + ACL) làm seed passage."""
    targets = [c for c in candidates[: _CHUNKLESS_EXPAND_MAX_DOCS * 2] if not c.chunks][
        :_CHUNKLESS_EXPAND_MAX_DOCS
    ]
    if not targets:
        return
    store = _context_store()

    async def _one(candidate: _Candidate) -> None:
        doc_id = str(candidate.source.get("document_id") or candidate.key).strip()
        if not doc_id:
            return
        try:
            chunks = await store.search_chunks(
                query,
                top_n=_CHUNKLESS_EXPAND_TOP_CHUNKS,
                acl_subject=acl_subject,
                ensure=False,
                document_ids={doc_id},
            )
        except Exception:
            logger.debug("docmeta->chunk expansion lỗi doc=%s", doc_id, exc_info=True)
            return
        for rank, chunk in enumerate(chunks, start=1):
            candidate.chunks.append(
                _chunk_context_seed(
                    chunk, source="docmeta_expansion", rank=rank,
                    score=float(chunk.get("_score") or 0.0),
                )
            )

    await asyncio.gather(*(_one(candidate) for candidate in targets))


# ===================== Context builder (hàng xóm + cha, từ ES chunk) =====================


def _context_store() -> DofficeChunkBm25Store:
    return DofficeChunkBm25Store(
        url=settings.two_stage_document_index_url or settings.elasticsearch_url,
    )


async def _build_context(candidates: list[_Candidate], *, acl_subject: Any) -> None:
    """Mở rộng ngữ cảnh từ ES chunk index (pipeline kho AI KHÔNG ghi chunk vào PG):
    hàng xóm ``chunk_order`` ±1 + chunk CHA (heading/điều-mục gần nhất đứng trước seed).
    1 request ``_msearch`` cho mọi seed, luôn kèm ACL."""
    seeds: list[tuple[str, int]] = []
    seen_seeds: set[tuple[str, int]] = set()
    for candidate in candidates:
        for chunk in candidate.chunks[:4]:
            id_full = str(chunk.get("id_full") or "")
            order = chunk.get("chunk_index")
            if not id_full or not isinstance(order, int):
                continue
            seed = (id_full, order)
            if seed not in seen_seeds:
                seen_seeds.add(seed)
                seeds.append(seed)

    by_seed: dict[tuple[str, int], list[dict[str, Any]]] = {}
    if seeds:
        try:
            responses = await _context_store().fetch_context_chunks(
                seeds,
                acl_subject=acl_subject,
                parent_chunk_types=frozenset(PARENT_CHUNK_TYPES),
                parent_lookback=PARENT_LOOKBACK_CHUNKS,
            )
        except Exception:
            logger.warning("Document search context ES expansion failed.", exc_info=True)
            responses = []
        for seed, hits in zip(seeds, responses):
            by_seed[seed] = _select_context_hits(seed, hits)

    for candidate in candidates:
        merged: list[dict[str, Any]] = []
        seen: set[str] = set()
        for chunk in candidate.chunks:
            id_full = str(chunk.get("id_full") or "")
            order = chunk.get("chunk_index")
            if id_full and isinstance(order, int):
                for es_chunk in by_seed.get((id_full, order), []):
                    _append_context(merged, seen, es_chunk)
            _append_context(merged, seen, chunk)
        # Văn bản đọc liền mạch: sắp theo (document_id, chunk_index); chunk thiếu index xếp cuối.
        merged.sort(key=lambda item: (
            str(item.get("document_id") or ""),
            item.get("chunk_index") if isinstance(item.get("chunk_index"), int) else 1 << 30,
        ))
        candidate.context = _select_context_by_budget(merged)


def _select_context_by_budget(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Chọn context theo NGÂN SÁCH ký tự (max_context_total_chars) thay vì chỉ đếm item —
    chunk nhỏ không chiếm slot ngang chunk lớn. Trần item = 2×MAX_CONTEXT_ITEMS."""
    budget = _PROFILE.max_context_total_chars
    selected: list[dict[str, Any]] = []
    total = 0
    for item in items:
        length = len(str(item.get("content") or ""))
        if selected and total + length > budget:
            break
        selected.append(item)
        total += length
        if len(selected) >= MAX_CONTEXT_ITEMS * 2:
            break
    return selected


def _select_context_hits(seed: tuple[str, int], hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Từ pool hit của 1 seed: giữ hàng xóm ±1 + chunk CHA GẦN NHẤT đứng trước seed."""
    _, order = seed
    selected: list[dict[str, Any]] = []
    nearest_parent: dict[str, Any] | None = None
    for hit in hits:
        hit_order = _optional_int(hit.get("chunk_order"))
        if hit_order is None:
            continue
        if abs(hit_order - order) <= 1:
            selected.append(hit)
            continue
        chunk_type = str(hit.get("chunk_type") or "")
        if chunk_type in PARENT_CHUNK_TYPES and hit_order < order:
            if nearest_parent is None or hit_order > _optional_int(nearest_parent.get("chunk_order")):
                nearest_parent = hit
    if nearest_parent is not None:
        selected.insert(0, nearest_parent)
    return [_es_chunk_context(hit) for hit in selected]


def _es_chunk_context(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "chunk_id": str(record.get("id") or record.get("chunk_id") or ""),
        "document_id": str(record.get("document_id") or ""),
        "id_full": str(record.get("id_full") or ""),
        "chunk_index": _optional_int(record.get("chunk_order")),
        "chunk_type": record.get("chunk_type"),
        "section_path": record.get("section_path"),
        "content_hash": record.get("content_hash"),
        "content": str(record.get("chunk_text") or "")[:MAX_CONTEXT_CHARS_PER_CHUNK],
        "metadata": {"title": record.get("title"), "section_path": record.get("section_path")},
        "source": "es_context",
        "score": None,
    }


def _append_context(target: list[dict[str, Any]], seen: set[str], item: dict[str, Any]) -> None:
    metadata = item.get("metadata") or {}
    # Dedup ưu tiên content_hash (chunk trùng nội dung do re-chunk/overlap không chiếm 2 slot),
    # fallback chunk_id rồi prefix content.
    key = str(
        item.get("content_hash") or metadata.get("content_hash")
        or item.get("chunk_id") or item.get("content") or ""
    )[:200]
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
    fused_lo, fused_span = min(fused_values), (max(fused_values) - min(fused_values)) or 1.0
    weight = min(1.0, max(0.0, float(settings.document_search_rerank_weight)))

    for candidate in top:
        # RRF min-max để làm tiebreak; điểm rerank dùng THÔ (reranker đã hiệu chỉnh 0-1) ->
        # set toàn weak cho điểm cuối thấp thật (không bị min-max thổi doc kém nhất thành 1.0).
        fused_norm = (candidate.fused_score - fused_lo) / fused_span
        rerank_raw = by_key.get(candidate.key)
        if rerank_raw is None:
            candidate.final_score = (1.0 - weight) * fused_norm * 0.5
            continue
        candidate.rerank_score = rerank_raw
        candidate.final_score = weight * rerank_raw + (1.0 - weight) * fused_norm
    # Candidate ngoài top rerank: giữ dưới nhóm được rerank (điểm RRF gốc luôn < 1 sau chuẩn hoá).
    candidates.sort(key=lambda item: (-item.final_score, item.key))


# Nhãn metadata bị chèn đầu dòng chunk (build_embedding_text/_element_content). Bỏ NHÃN,
# giữ GIÁ TRỊ -> reranker thấy nội dung thật, không bị format đánh lừa (Qwen3-Reranker từng
# chấm doc lạc đề 0.79 vì nhãn; BGE miễn nhiễm nhưng vẫn nên gửi sạch).
_META_LABEL_RE = re.compile(
    r"^(?:S[ố́o]?/?k[ýy] hi[ệe]u|S[ố́o] hi[ệe]u/m[ãa]|Ng[àa]y( v[ăa]n b[ảa]n| ban h[àa]nh)?|"
    r"Tr[íi]ch y[ếe]u|V[ăa]n b[ảa]n|T[àa]i li[ệe]u|C[ơo] quan|M[ụu]c|[ĐDĐ]i[ềe]u|Kho[ảa]n|"
    r"[ĐD]i[ểe]m|Ph[ụu] l[ụu]c|B[ảa]ng|C[ộo]t b[ảa]ng)\s*:\s*",
    re.IGNORECASE,
)


def _clean_rerank_text(text: str) -> str:
    lines_out: list[str] = []
    for line in str(text or "").splitlines():
        stripped = _META_LABEL_RE.sub("", line).strip()
        if stripped:
            lines_out.append(stripped)
    return " ".join(lines_out)


def _rerank_content(candidate: _Candidate) -> str:
    parts: list[str] = []
    title = str(candidate.source.get("title") or "").strip()
    if title:
        parts.append(title)
    summary = str(candidate.source.get("summary") or "").strip()
    if summary:
        parts.append(summary[:400])
    best_chunk = max(
        candidate.chunks,
        key=lambda chunk: float(chunk.get("score") or 0.0),
        default=None,
    )
    if best_chunk is not None:
        parts.append(_clean_rerank_text(best_chunk.get("content") or "")[:900])
    elif candidate.highlights:
        parts.append(re.sub(r"</?mark>", "", " ... ".join(candidate.highlights[:3])))
    # Bỏ đoạn trùng lặp (title lặp lại trong body) rồi cắt.
    seen: set[str] = set()
    uniq: list[str] = []
    for part in parts:
        p = part.strip()
        if p and p.casefold() not in seen:
            seen.add(p.casefold())
            uniq.append(p)
    return "\n".join(uniq)[:1500] or candidate.key


# ===================== Boost định danh (mã/số văn bản) =====================

# Token ký hiệu đầy đủ trong query (vd "258/QĐ-IT", "6515/EVNCPC-VTCNTT+KD").
_CODE_TOKEN_RE = re.compile(r"[0-9A-Za-zĐđ]{1,6}/[0-9A-Za-zĐđ+\-]+")
_BARE_NUM_RE = re.compile(r"\b\d{2,6}\b")


def _norm_code(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).upper()


def _extract_query_identifiers(query: str) -> tuple[set[str], set[str]]:
    """Trích mã ký hiệu đầy đủ + số văn bản rời từ query (bỏ năm — metadata filter lo)."""
    codes = {_norm_code(m.group()) for m in _CODE_TOKEN_RE.finditer(query or "")}
    codes.discard("")
    numbers: set[str] = set()
    for m in _BARE_NUM_RE.finditer(query or ""):
        n = m.group()
        if len(n) == 4 and 1990 <= int(n) <= 2099:  # năm -> bỏ (tránh boost nhầm theo năm)
            continue
        numbers.add(n)
    return codes, numbers


def _candidate_identifier_match(candidate: _Candidate, codes: set[str], numbers: set[str]) -> str | None:
    document_no = _norm_code(candidate.source.get("document_no"))
    document_id = str(candidate.source.get("document_id") or "").strip()
    for code in codes:
        if code and document_no and code in document_no:  # mã đầy đủ khớp -> tin cậy cao
            return "code"
    no_number = document_no.split("/")[0] if "/" in document_no else document_no
    for num in numbers:
        # số văn bản khớp document_id hoặc phần số của document_no
        if num and (num == document_id or num == no_number):
            return "number"
    return None


def _apply_identifier_boost(query: str, candidates: list[_Candidate]) -> None:
    """Query hỗn hợp (nội dung + mã/số VB): cộng điểm lớn cho candidate khớp mã người dùng nêu,
    đặt evidence=strong (nêu đích danh = căn cứ mạnh dù rerank ngữ nghĩa có thể thấp), rồi xếp lại.
    -> exact-doc lên top MÀ vẫn giữ các kết quả semantic phía dưới (đúng ý 'kết hợp cả hai')."""
    codes, numbers = _extract_query_identifiers(query)
    if not codes and not numbers:
        return
    code_boost = float(settings.document_search_identifier_code_boost)
    num_boost = float(settings.document_search_identifier_number_boost)
    changed = False
    for candidate in candidates:
        kind = _candidate_identifier_match(candidate, codes, numbers)
        if kind is None:
            continue
        changed = True
        candidate.final_score += code_boost if kind == "code" else num_boost
        candidate.source_flags.add("identifier_match")
        candidate.evidence = {
            **candidate.evidence,
            "status": "strong",
            "reason": f"Khop ma/so van ban nguoi dung neu ({kind}).",
            "identifier_match": kind,
        }
    if changed:
        candidates.sort(key=lambda item: (-item.final_score, item.key))


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
    rerank = candidate.rerank_score

    if support_count == 0:
        status = "weak"
        reason = "Khong co chunk/context lam can cu truc tiep."
    elif rerank is not None:
        # Cross-encoder (Qwen3-Reranker) là tín hiệu liên quan chính xác nhất -> ưu tiên. Doc
        # trùng nhiều token nhưng reranker chấm thấp = KHÔNG trả lời được truy vấn -> weak.
        strong_rr = float(settings.document_search_crag_strong_rerank)
        ambiguous_rr = float(settings.document_search_crag_ambiguous_rerank)
        if rerank >= strong_rr:
            status = "strong"
            reason = "Reranker cross-encoder danh gia doc lien quan cao voi truy van."
        elif rerank >= ambiguous_rr:
            status = "ambiguous"
            reason = "Reranker danh gia lien quan vua phai; can cu chua that manh."
        else:
            status = "weak"
            reason = "Reranker danh gia doc it lien quan truy van."
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
        "rerank_score": round(rerank, 4) if rerank is not None else None,
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
                    "document_no": candidate.source.get("document_no"),
                    "title": candidate.source.get("title"),
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


def _optional_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
