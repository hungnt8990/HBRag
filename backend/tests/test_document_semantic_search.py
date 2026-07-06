"""Tests cho pipeline fusion semantic (document_semantic_search.py)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.services.retrieval import document_semantic_search as sem

# --------------------------- metadata filter ---------------------------


def test_extract_metadata_filters_year_month() -> None:
    filters = sem._extract_metadata_filters("quyet dinh nam 2025 thang 3")
    assert filters.years == [2025]
    assert filters.months == [3]


def test_extract_metadata_filters_accented_query() -> None:
    filters = sem._extract_metadata_filters("quyết định tháng 5 năm 2024")
    assert filters.years == [2024]
    assert filters.months == [5]


def test_extract_metadata_filters_month_requires_year() -> None:
    filters = sem._extract_metadata_filters("bang luong thang 3")
    assert filters.years == []
    assert filters.months == []
    assert not filters


def test_extract_metadata_filters_ignores_document_numbers() -> None:
    filters = sem._extract_metadata_filters("qd 258 ve khen thuong")
    assert filters.years == []


# --------------------------- RRF per-query rank ---------------------------


def test_add_vector_like_results_uses_per_query_rank(monkeypatch) -> None:
    """Rank RRF phai tinh theo TUNG query, khong phai vi tri trong list da noi."""
    monkeypatch.setattr(sem.settings, "document_search_fusion_rrf_k", 60)
    candidates: dict[str, sem._Candidate] = {}
    # Cung 1 van ban dung rank=1 o CA HAI query -> cong 2 lan w/(k+1).
    results = [
        {"rank": 1, "query_index": 0, "score": 0.9, "metadata": {"id_vb": "111"}, "document_id": "d1", "chunk_id": "c1", "content": "x"},
        {"rank": 1, "query_index": 1, "score": 0.8, "metadata": {"id_vb": "111"}, "document_id": "d1", "chunk_id": "c2", "content": "y"},
    ]
    sem._add_vector_like_results(candidates, results, weight=1.0, source_flag="vector_chunk", rrf_k=60)
    assert set(candidates) == {"111"}
    expected = 2 * (1.0 / (60 + 1))
    assert abs(candidates["111"].fused_score - expected) < 1e-9


def test_get_candidate_merges_document_id_only_candidate() -> None:
    """Guard: candidate cu key theo document_id (payload thieu id_vb) duoc GOP khi
    xuat hien key id_vb kem document_id trung."""
    candidates: dict[str, sem._Candidate] = {}
    old = sem._get_candidate(candidates, "doc-uuid-1", {"document_id": "doc-uuid-1"})
    old.fused_score = 0.5
    merged = sem._get_candidate(
        candidates, "12345", {"id_vb": "12345", "document_id": "doc-uuid-1"}
    )
    assert merged is old
    assert merged.key == "12345"
    assert set(candidates) == {"12345"}


# --------------------------- cross-encoder rerank ---------------------------


def _mk_candidates(n: int = 3) -> list[sem._Candidate]:
    out = []
    for i in range(n):
        c = sem._Candidate(key=f"vb{i}")
        c.fused_score = 1.0 - i * 0.1
        c.final_score = c.fused_score
        c.source = {"trich_yeu": f"van ban so {i}"}
        out.append(c)
    return out


def test_rerank_fallback_keeps_rrf_order_on_error(monkeypatch) -> None:
    monkeypatch.setattr(sem.settings, "document_search_rerank_enabled", True)
    monkeypatch.setattr(sem.settings, "document_search_rerank_top_k", 10)

    class _Gateway:
        async def rerank(self, *, query, candidates):
            raise RuntimeError("gateway down")

    monkeypatch.setattr(sem, "get_llm_gateway", lambda: _Gateway())
    candidates = _mk_candidates()
    asyncio.run(sem._apply_cross_encoder_rerank("cau hoi", candidates))
    assert [c.key for c in candidates] == ["vb0", "vb1", "vb2"]
    assert all(c.rerank_score is None for c in candidates)
    assert all(abs(c.final_score - c.fused_score) < 1e-9 for c in candidates)


def test_rerank_reorders_candidates(monkeypatch) -> None:
    monkeypatch.setattr(sem.settings, "document_search_rerank_enabled", True)
    monkeypatch.setattr(sem.settings, "document_search_rerank_top_k", 10)
    monkeypatch.setattr(sem.settings, "document_search_rerank_weight", 1.0)

    class _Gateway:
        async def rerank(self, *, query, candidates):
            # Dao nguoc: candidate cuoi diem cao nhat.
            return [
                SimpleNamespace(chunk_id=c.chunk_id, score=float(i))
                for i, c in enumerate(candidates)
            ]

    monkeypatch.setattr(sem, "get_llm_gateway", lambda: _Gateway())
    candidates = _mk_candidates()
    asyncio.run(sem._apply_cross_encoder_rerank("cau hoi", candidates))
    assert [c.key for c in candidates] == ["vb2", "vb1", "vb0"]
    assert candidates[0].rerank_score == 2.0


def test_rerank_disabled_keeps_order(monkeypatch) -> None:
    monkeypatch.setattr(sem.settings, "document_search_rerank_enabled", False)
    candidates = _mk_candidates()
    asyncio.run(sem._apply_cross_encoder_rerank("cau hoi", candidates))
    assert [c.key for c in candidates] == ["vb0", "vb1", "vb2"]


# --------------------------- CRAG-lite ---------------------------


def test_evidence_summary_levels() -> None:
    def _mk(status: str) -> sem._Candidate:
        c = sem._Candidate(key=status)
        c.evidence = {"status": status}
        return c

    assert sem._evidence_summary([], 10) == "insufficient"
    assert sem._evidence_summary([_mk("weak"), _mk("weak"), _mk("weak")], 10) == "insufficient"
    assert sem._evidence_summary([_mk("ambiguous"), _mk("weak")], 10) == "partial"
    assert sem._evidence_summary([_mk("strong"), _mk("weak")], 10) == "strong"


def test_top_all_weak() -> None:
    def _mk(status: str) -> sem._Candidate:
        c = sem._Candidate(key=status)
        c.evidence = {"status": status}
        return c

    assert sem._top_all_weak([_mk("weak"), _mk("weak")]) is True
    assert sem._top_all_weak([_mk("weak"), _mk("strong")]) is False
    assert sem._top_all_weak([]) is False


def test_llm_grade_ambiguous_updates_status(monkeypatch) -> None:
    monkeypatch.setattr(sem.settings, "document_search_crag_llm_grading", True)
    monkeypatch.setattr(sem.settings, "document_search_crag_llm_grading_max", 5)
    monkeypatch.setattr(sem.settings, "llm_provider", "openai_compatible")

    class _Gateway:
        async def generate(self, **kwargs):
            return '[{"key": "vb1", "verdict": "strong", "reason": "khop truc tiep"}]'

    monkeypatch.setattr(sem, "get_llm_gateway", lambda: _Gateway())
    c1 = sem._Candidate(key="vb1")
    c1.evidence = {"status": "ambiguous", "reason": "rule"}
    c1.context = [{"content": "noi dung chunk"}]
    c2 = sem._Candidate(key="vb2")
    c2.evidence = {"status": "strong"}
    asyncio.run(sem._llm_grade_ambiguous("cau hoi", [c1, c2]))
    assert c1.evidence["status"] == "strong"
    assert c1.evidence["graded_by"] == "llm"
    assert c2.evidence.get("graded_by") is None


def test_llm_grade_ambiguous_parse_error_keeps_rule_verdict(monkeypatch) -> None:
    monkeypatch.setattr(sem.settings, "document_search_crag_llm_grading", True)
    monkeypatch.setattr(sem.settings, "llm_provider", "openai_compatible")

    class _Gateway:
        async def generate(self, **kwargs):
            return "not json at all"

    monkeypatch.setattr(sem, "get_llm_gateway", lambda: _Gateway())
    c1 = sem._Candidate(key="vb1")
    c1.evidence = {"status": "ambiguous", "reason": "rule"}
    asyncio.run(sem._llm_grade_ambiguous("cau hoi", [c1]))
    assert c1.evidence["status"] == "ambiguous"


# --------------------------- clean rerank content ---------------------------


def test_clean_rerank_text_strips_metadata_labels() -> None:
    t = "Số/ký hiệu: CV\nNgày văn bản: 15/04/2025\nTrích yếu: Đề nghị hợp tác\nVăn bản: CV - Đề nghị hợp tác"
    cleaned = sem._clean_rerank_text(t)
    assert "Số/ký hiệu:" not in cleaned
    assert "Trích yếu:" not in cleaned
    assert "Văn bản:" not in cleaned
    assert "Đề nghị hợp tác" in cleaned


# --------------------------- CRAG dùng rerank_score ---------------------------


def test_crag_uses_rerank_score_over_token_overlap(monkeypatch) -> None:
    monkeypatch.setattr(sem.settings, "document_search_crag_strong_rerank", 0.5)
    monkeypatch.setattr(sem.settings, "document_search_crag_ambiguous_rerank", 0.2)
    # Doc trùng nhiều token với query nhưng reranker chấm thấp -> weak (không "strong" bừa).
    c = sem._Candidate(key="vb1")
    c.context = [{"content": "khen thuong thi dua nam 2025 quyet dinh"}]
    c.source_flags = {"bm25_document", "vector_chunk"}
    c.rerank_score = 0.01
    ev = sem._crag_lite_evidence("khen thuong thi dua nam 2025", c)
    assert ev["status"] == "weak"
    # Reranker chấm cao -> strong dù ít trùng token.
    c.rerank_score = 0.8
    ev = sem._crag_lite_evidence("khen thuong thi dua nam 2025", c)
    assert ev["status"] == "strong"


# --------------------------- boost định danh ---------------------------


def test_extract_query_identifiers() -> None:
    codes, numbers = sem._extract_query_identifiers("quy chế trả lương theo 258/QĐ-IT năm 2025")
    assert codes == {"258/QĐ-IT"}
    assert "258" in numbers
    assert "2025" not in numbers  # năm bị loại
    codes2, numbers2 = sem._extract_query_identifiers("quyết định 1660 về nghỉ")
    assert codes2 == set()
    assert numbers2 == {"1660"}


def test_candidate_identifier_match() -> None:
    c = sem._Candidate(key="x")
    c.source = {"ky_hieu": "258/QĐ-IT", "id_vb": "850373"}
    assert sem._candidate_identifier_match(c, {"258/QĐ-IT"}, set()) == "code"
    assert sem._candidate_identifier_match(c, set(), {"258"}) == "number"      # phần số ky_hieu
    assert sem._candidate_identifier_match(c, set(), {"850373"}) == "number"   # id_vb
    assert sem._candidate_identifier_match(c, set(), {"999"}) is None


def test_apply_identifier_boost_lifts_matched_doc_to_top(monkeypatch) -> None:
    monkeypatch.setattr(sem.settings, "document_search_identifier_code_boost", 1.0)
    monkeypatch.setattr(sem.settings, "document_search_identifier_number_boost", 0.5)
    matched = sem._Candidate(key="850373")
    matched.source = {"ky_hieu": "258/QĐ-IT", "id_vb": "850373"}
    matched.final_score = 0.3
    matched.evidence = {"status": "weak"}
    semantic = sem._Candidate(key="999")
    semantic.source = {"ky_hieu": "12/TB-IT", "id_vb": "999"}
    semantic.final_score = 0.9
    semantic.evidence = {"status": "strong"}
    cands = [semantic, matched]
    sem._apply_identifier_boost("quy chế trả lương 258/QĐ-IT", cands)
    assert cands[0].key == "850373"  # doc khớp mã lên top
    assert cands[0].evidence["status"] == "strong"
    assert cands[0].evidence["identifier_match"] == "code"
    assert "identifier_match" in cands[0].source_flags


def test_apply_identifier_boost_noop_without_identifier() -> None:
    c = sem._Candidate(key="1")
    c.source = {"ky_hieu": "12/TB-IT"}
    c.final_score = 0.5
    sem._apply_identifier_boost("quy chế trả lương", [c])  # không có mã trong query
    assert c.final_score == 0.5
    assert "identifier_match" not in c.source_flags


def test_query_embedding_instruction_applies_to_semantic_not_identifier() -> None:
    # Mặc định settings.embedding_query_instruction non-empty -> query semantic được bọc,
    # còn tra cứu mã/số hiệu giữ nguyên (đi đường exact/BM25).
    from app.services.rag import rag_chunk

    semantic = rag_chunk.build_query_embedding_text("quy chế trả lương")
    identifier = rag_chunk.build_query_embedding_text("3113")
    assert semantic.startswith("Instruct:") and "Query:" in semantic
    assert not identifier.startswith("Instruct:")
