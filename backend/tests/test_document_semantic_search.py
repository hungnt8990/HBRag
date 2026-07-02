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
