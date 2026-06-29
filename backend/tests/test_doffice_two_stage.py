"""Test logic Stage-1 resolver (RRF hợp nhất ES BM25 + Qdrant docmeta)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.schemas.documents import KeywordSearchResponse
from app.services.retrieval.retrieval_doffice_two_stage import (
    DofficeStage1Resolver,
    NoOpKeywordSearchService,
)


class _FakeBm25:
    def __init__(self, doc_ids):
        self._doc_ids = doc_ids

    async def search_documents(self, query, *, top_n=50, acl_subject=None):
        return [{"document_id": d} for d in self._doc_ids]


class _FakeDocmeta:
    def __init__(self, doc_ids):
        self._doc_ids = doc_ids

    async def search(self, *, query, top_k, acl_subject=None):
        results = [SimpleNamespace(document_id=d) for d in self._doc_ids]
        return SimpleNamespace(results=results)


def test_stage1_rrf_union_ranks_overlap_first() -> None:
    # B xuất hiện ở CẢ 2 nguồn -> điểm RRF cao nhất -> đứng đầu.
    resolver = DofficeStage1Resolver(
        bm25_store=_FakeBm25(["A", "B"]),
        docmeta_search_service=_FakeDocmeta(["B", "C"]),
    )
    ids = asyncio.run(resolver.search_documents("gis", top_n=10))
    assert ids[0] == "B"
    assert set(ids) == {"A", "B", "C"}


def test_stage1_survives_one_source_failing() -> None:
    class _BoomBm25:
        async def search_documents(self, *a, **k):
            raise RuntimeError("es down")

    resolver = DofficeStage1Resolver(
        bm25_store=_BoomBm25(),
        docmeta_search_service=_FakeDocmeta(["X", "Y"]),
    )
    ids = asyncio.run(resolver.search_documents("q", top_n=10))
    assert ids == ["X", "Y"]


def test_noop_keyword_returns_empty() -> None:
    resp = asyncio.run(NoOpKeywordSearchService().search(query="q", top_k=5))
    assert isinstance(resp, KeywordSearchResponse)
    assert resp.results == []
    assert resp.top_k == 5
