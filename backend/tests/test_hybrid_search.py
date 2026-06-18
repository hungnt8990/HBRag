import asyncio
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.api.routes.search import get_hybrid_search_service
from app.main import app
from app.models.retrieval import RetrievalLog
from app.repositories.retrieval_logs import RetrievalLogRepository
from app.schemas.documents import (
    HybridSearchResponse,
    HybridSearchResult,
    KeywordSearchResponse,
    KeywordSearchResult,
    VectorSearchResponse,
    VectorSearchResult,
)
from app.services.hybrid_search import HybridSearchService

DOCUMENT_ID = UUID("88888888-8888-8888-8888-888888888888")
VECTOR_ONLY_CHUNK_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
OVERLAP_CHUNK_ID = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
KEYWORD_ONLY_CHUNK_ID = UUID("cccccccc-cccc-cccc-cccc-cccccccccccc")


class FakeHybridSearchService:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def search(
        self,
        *,
        query: str,
        top_k: int,
        vector_weight: float,
        keyword_weight: float,
        document_ids=None,
    ) -> HybridSearchResponse:
        self.calls.append(
            {
                "query": query,
                "top_k": top_k,
                "vector_weight": vector_weight,
                "keyword_weight": keyword_weight,
            }
        )
        return HybridSearchResponse(
            query=query,
            top_k=top_k,
            vector_weight=vector_weight,
            keyword_weight=keyword_weight,
            results=[
                HybridSearchResult(
                    chunk_id=OVERLAP_CHUNK_ID,
                    document_id=DOCUMENT_ID,
                    fused_score=0.0325,
                    vector_score=0.91,
                    keyword_score=0.72,
                    content_preview="Hybrid result preview",
                    metadata={"start_char": 0},
                    source_flags=["vector", "keyword"],
                )
            ],
        )


class FakeVectorSearchService:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def search(self, *, query: str, top_k: int, document_ids=None) -> VectorSearchResponse:
        self.calls.append({"query": query, "top_k": top_k})
        return VectorSearchResponse(
            query=query,
            top_k=top_k,
            results=[
                VectorSearchResult(
                    chunk_id=OVERLAP_CHUNK_ID,
                    document_id=DOCUMENT_ID,
                    score=0.91,
                    content_preview="Overlap from vector",
                    metadata={"source": "vector"},
                )
            ],
        )


class FakeKeywordSearchService:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def search(self, *, query: str, top_k: int, document_ids=None) -> KeywordSearchResponse:
        self.calls.append({"query": query, "top_k": top_k})
        return KeywordSearchResponse(
            query=query,
            top_k=top_k,
            results=[
                KeywordSearchResult(
                    chunk_id=OVERLAP_CHUNK_ID,
                    document_id=DOCUMENT_ID,
                    score=0.72,
                    content_preview="Overlap from keyword",
                    metadata={"source": "keyword"},
                )
            ],
        )


class FakeRetrievalLogRepository:
    def __init__(self) -> None:
        self.saved_logs: list[dict[str, object]] = []
        self.committed = False
        self.rolled_back = False

    async def save_log(self, **kwargs: object) -> SimpleNamespace:
        self.saved_logs.append(dict(kwargs))
        return SimpleNamespace(id=UUID("dddddddd-dddd-dddd-dddd-dddddddddddd"))

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        self.rolled_back = True


class FakeSession:
    def __init__(self) -> None:
        self.added: list[object] = []
        self.flushed = False
        self.committed = False
        self.rolled_back = False

    def add(self, model: object) -> None:
        self.added.append(model)

    async def flush(self) -> None:
        self.flushed = True

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        self.rolled_back = True


def _vector_result(
    chunk_id: UUID,
    *,
    score: float,
    preview: str,
) -> VectorSearchResult:
    return VectorSearchResult(
        chunk_id=chunk_id,
        document_id=DOCUMENT_ID,
        score=score,
        content_preview=preview,
        metadata={"chunk_id": str(chunk_id)},
    )


def _keyword_result(
    chunk_id: UUID,
    *,
    score: float,
    preview: str,
) -> KeywordSearchResult:
    return KeywordSearchResult(
        chunk_id=chunk_id,
        document_id=DOCUMENT_ID,
        score=score,
        content_preview=preview,
        metadata={"chunk_id": str(chunk_id)},
    )


def test_rrf_merge_with_overlapping_chunk_ids() -> None:
    results = HybridSearchService.fuse_results(
        vector_results=[
            _vector_result(VECTOR_ONLY_CHUNK_ID, score=0.95, preview="Vector only"),
            _vector_result(OVERLAP_CHUNK_ID, score=0.90, preview="Overlap vector"),
        ],
        keyword_results=[
            _keyword_result(OVERLAP_CHUNK_ID, score=0.80, preview="Overlap keyword"),
            _keyword_result(KEYWORD_ONLY_CHUNK_ID, score=0.70, preview="Keyword only"),
        ],
        top_k=3,
        vector_weight=1.0,
        keyword_weight=1.0,
        rrf_k=60,
    )

    assert [result.chunk_id for result in results] == [
        OVERLAP_CHUNK_ID,
        VECTOR_ONLY_CHUNK_ID,
        KEYWORD_ONLY_CHUNK_ID,
    ]
    assert results[0].fused_score == pytest.approx((1 / 62) + (1 / 61))
    assert results[0].vector_score == 0.90
    assert results[0].keyword_score == 0.80
    assert results[0].source_flags == ["vector", "keyword"]
    assert results[0].content_preview == "Overlap vector"


def test_rrf_keeps_vector_only_and_keyword_only_results() -> None:
    results = HybridSearchService.fuse_results(
        vector_results=[_vector_result(VECTOR_ONLY_CHUNK_ID, score=0.95, preview="Vector only")],
        keyword_results=[
            _keyword_result(KEYWORD_ONLY_CHUNK_ID, score=0.70, preview="Keyword only")
        ],
        top_k=2,
    )

    by_chunk_id = {result.chunk_id: result for result in results}

    assert set(by_chunk_id) == {VECTOR_ONLY_CHUNK_ID, KEYWORD_ONLY_CHUNK_ID}
    assert by_chunk_id[VECTOR_ONLY_CHUNK_ID].source_flags == ["vector"]
    assert by_chunk_id[VECTOR_ONLY_CHUNK_ID].keyword_score is None
    assert by_chunk_id[KEYWORD_ONLY_CHUNK_ID].source_flags == ["keyword"]
    assert by_chunk_id[KEYWORD_ONLY_CHUNK_ID].vector_score is None


def test_schema_count_query_boosts_schema_chunks_over_flow_summary() -> None:
    schema_chunk_id = UUID("dddddddd-dddd-dddd-dddd-dddddddddddd")
    flow_chunk_id = UUID("eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee")

    results = HybridSearchService.fuse_results(
        query="Khung CSDL gis hạ thế có mấy lớp thuộc tính",
        vector_results=[
            VectorSearchResult(
                chunk_id=flow_chunk_id,
                document_id=DOCUMENT_ID,
                score=0.95,
                content_preview="CÁC LỚP DỮ LIỆU\nCMIS/TTHT → Lưu trữ & Tổng hợp → GIS Hạ thế",
                metadata={"chunk_type": "docling_hybrid_repaired"},
            ),
            VectorSearchResult(
                chunk_id=schema_chunk_id,
                document_id=DOCUMENT_ID,
                score=0.70,
                content_preview="Bảng dữ liệu: F08_CotDien_HT. Tổng số trường: 18.",
                metadata={
                    "chunk_type": "table_parent",
                    "table_name": "F08_CotDien_HT",
                    "field_names": ["ID", "MaTramBienAp"],
                },
            ),
        ],
        keyword_results=[],
        top_k=2,
    )

    assert results[0].chunk_id == schema_chunk_id
    assert results[0].metadata["metadata_exact_boost"] >= 8


def test_schema_count_query_prefers_structural_overview_over_field_rows() -> None:
    attribute_chunk_id = UUID("11111111-1111-1111-1111-111111111111")
    relationship_chunk_id = UUID("22222222-2222-2222-2222-222222222222")
    summary_chunk_id = UUID("33333333-3333-3333-3333-333333333333")
    field_chunk_id = UUID("44444444-4444-4444-4444-444444444444")

    results = HybridSearchService.fuse_results(
        query="Khung CSDL gis hạ thế có mấy lớp thuộc tính",
        vector_results=[
            VectorSearchResult(
                chunk_id=field_chunk_id,
                document_id=DOCUMENT_ID,
                score=0.99,
                content_preview="F08_CotDien_HT có trường Trị số tiếp địa",
                metadata={"chunk_type": "schema_field_row", "field_name": "TriSoTiepDia"},
            ),
            VectorSearchResult(
                chunk_id=attribute_chunk_id,
                document_id=DOCUMENT_ID,
                score=0.70,
                content_preview="03 bảng dữ liệu thuộc tính",
                metadata={"chunk_type": "attribute_table_schema", "table_name": "HinhAnhCotDien"},
            ),
            VectorSearchResult(
                chunk_id=relationship_chunk_id,
                document_id=DOCUMENT_ID,
                score=0.69,
                content_preview="03 mối quan hệ 1-M",
                metadata={
                    "chunk_type": "gis_relationship_schema",
                    "relationship_name": "PXXXXX_CotDien_HT_HinhAnhCotDien",
                    "target_table": "HinhAnhCotDien",
                },
            ),
            VectorSearchResult(
                chunk_id=summary_chunk_id,
                document_id=DOCUMENT_ID,
                score=0.68,
                content_preview="Khung CSDL tổng thể có 11 lớp dữ liệu",
                metadata={"chunk_type": "schema_object_summary"},
            ),
        ],
        keyword_results=[],
        top_k=4,
    )

    ranked_ids = [result.chunk_id for result in results]
    assert ranked_ids.index(field_chunk_id) > ranked_ids.index(attribute_chunk_id)
    assert ranked_ids.index(field_chunk_id) > ranked_ids.index(relationship_chunk_id)
    assert ranked_ids.index(field_chunk_id) > ranked_ids.index(summary_chunk_id)

def test_schema_field_boost_uses_profile_query_intent_rules() -> None:
    field_chunk_id = UUID("44444444-4444-4444-4444-444444444444")
    vector_result = VectorSearchResult(
        chunk_id=field_chunk_id,
        document_id=DOCUMENT_ID,
        score=0.99,
        content_preview="F08_CotDien_HT có trường Trị số tiếp địa",
        metadata={"chunk_type": "schema_field_row"},
    )

    default_results = HybridSearchService.fuse_results(
        query="schema-cell detail",
        vector_results=[vector_result],
        keyword_results=[],
        top_k=1,
    )
    custom_results = HybridSearchService.fuse_results(
        query="schema-cell detail",
        vector_results=[vector_result],
        keyword_results=[],
        top_k=1,
        query_intent_rules={
            "field_detail_schema": {
                "direct_terms": ["schema-cell"],
                "required_any_terms": [],
                "specific_item_patterns": [],
                "phrases": [],
            }
        },
    )

    assert "metadata_exact_boost" not in default_results[0].metadata
    assert custom_results[0].metadata["metadata_exact_boost"] == 4.0

def test_enrichment_metadata_boost_is_gated() -> None:
    enriched_chunk_id = UUID("dddddddd-dddd-dddd-dddd-dddddddddddd")
    plain_chunk_id = UUID("eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee")
    vector_results = [
        VectorSearchResult(
            chunk_id=plain_chunk_id,
            document_id=DOCUMENT_ID,
            score=0.99,
            content_preview="Nội dung chung",
            metadata={"chunk_id": str(plain_chunk_id)},
        ),
        VectorSearchResult(
            chunk_id=enriched_chunk_id,
            document_id=DOCUMENT_ID,
            score=0.5,
            content_preview="Nội dung gốc không có mã",
            metadata={
                "chunk_id": str(enriched_chunk_id),
                "enrichment": {
                    "keywords": ["PMISToGIS"],
                    "document_code": "123/QĐ-CPCIT",
                },
            },
        ),
    ]

    disabled = HybridSearchService.fuse_results(
        query="PMISToGIS 123/QĐ-CPCIT",
        vector_results=vector_results,
        keyword_results=[],
        top_k=2,
        retrieval_enrichment_enabled=False,
    )
    enabled = HybridSearchService.fuse_results(
        query="PMISToGIS 123/QĐ-CPCIT",
        vector_results=vector_results,
        keyword_results=[],
        top_k=2,
        retrieval_enrichment_enabled=True,
    )

    disabled_enriched = next(item for item in disabled if item.chunk_id == enriched_chunk_id)
    enabled_enriched = next(item for item in enabled if item.chunk_id == enriched_chunk_id)
    assert "enrichment_boost" not in disabled_enriched.metadata
    assert enabled_enriched.metadata["enrichment_boost"] > 0


def test_person_area_membership_boosts_valid_entity_or_table_row() -> None:
    valid_chunk_id = UUID("dddddddd-dddd-dddd-dddd-dddddddddddd")
    warning_chunk_id = UUID("eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee")
    query = (
        "Nguyễn Trọng Hùng tham gia Xây dựng nền tảng RAG trên dữ liệu nội bộ "
        "đúng không?"
    )

    results = HybridSearchService.fuse_results(
        query=query,
        vector_results=[
            VectorSearchResult(
                chunk_id=warning_chunk_id,
                document_id=DOCUMENT_ID,
                score=0.99,
                content_preview="Nguyễn Trọng Hùng Platform AI",
                metadata={
                    "chunk_type": "table_block",
                    "table_parse_warning": True,
                    "confidence": 0.2,
                },
            ),
            VectorSearchResult(
                chunk_id=valid_chunk_id,
                document_id=DOCUMENT_ID,
                score=0.5,
                content_preview=(
                    "Nhân sự: Nguyễn Trọng Hùng. Xây dựng nền tảng RAG trên "
                    "dữ liệu nội bộ."
                ),
                metadata={
                    "chunk_type": "entity_profile",
                    "relationship_type": "technology_area_staff",
                    "confidence": 0.95,
                    "person_name": "Nguyễn Trọng Hùng",
                    "areas": [
                        {
                            "area": "Xây dựng nền tảng RAG trên dữ liệu nội bộ",
                            "lead_department": "PTUD",
                            "stt": "3",
                        }
                    ],
                },
            ),
        ],
        keyword_results=[],
        top_k=2,
    )

    assert results[0].chunk_id == valid_chunk_id
    assert results[0].metadata["chunk_type"] == "entity_profile"
    assert results[0].metadata["membership_boost"] >= 10
    assert "membership_boost" not in results[1].metadata


def test_identifier_lookup_boosts_doc_code_metadata_over_topical_vector_match() -> None:
    caption_chunk_id = UUID("99999999-9999-9999-9999-999999999999")
    dispatch_chunk_id = UUID("77777777-7777-7777-7777-777777777777")

    results = HybridSearchService.fuse_results(
        query="3113",
        vector_results=[
            VectorSearchResult(
                chunk_id=caption_chunk_id,
                document_id=DOCUMENT_ID,
                score=0.99,
                content_preview="Màn hình ứng dụng EVN CSKH, tài liệu 907.",
                metadata={
                    "chunk_type": "app_ui_caption",
                    "identifiers": ["907"],
                },
            ),
            VectorSearchResult(
                chunk_id=dispatch_chunk_id,
                document_id=DOCUMENT_ID,
                score=0.4,
                content_preview="Công văn cập nhật phiên bản chính thức EVN CSKH.",
                metadata={
                    "chunk_type": "official_dispatch_main",
                    "doc_codes": ["3113/EVN-KDMBĐ"],
                    "identifiers": ["3113/EVN-KDMBĐ", "3113", "EVN-KDMBĐ"],
                },
            ),
        ],
        keyword_results=[],
        top_k=2,
    )

    assert results[0].chunk_id == dispatch_chunk_id
    assert results[0].metadata["identifier_exact_boost"] == 50.0
    assert "lexical_exact" in results[0].source_flags
    assert results[1].chunk_id == caption_chunk_id
    assert "identifier_exact_boost" not in results[1].metadata


def test_hybrid_endpoint_rejects_empty_query() -> None:
    service = FakeHybridSearchService()
    app.dependency_overrides[get_hybrid_search_service] = lambda: service

    try:
        client = TestClient(app)
        response = client.post(
            "/api/search/hybrid",
            json={"query": "   ", "top_k": 5},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422
    assert service.calls == []


def test_hybrid_endpoint_returns_expected_schema() -> None:
    service = FakeHybridSearchService()
    app.dependency_overrides[get_hybrid_search_service] = lambda: service

    try:
        client = TestClient(app)
        response = client.post(
            "/api/search/hybrid",
            json={
                "query": "hybrid search",
                "top_k": 2,
                "vector_weight": 1.2,
                "keyword_weight": 0.8,
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload == {
        "query": "hybrid search",
        "top_k": 2,
        "vector_weight": 1.2,
        "keyword_weight": 0.8,
        "results": [
            {
                "chunk_id": str(OVERLAP_CHUNK_ID),
                "document_id": str(DOCUMENT_ID),
                "fused_score": 0.0325,
                "vector_score": 0.91,
                "keyword_score": 0.72,
                "content_preview": "Hybrid result preview",
                "metadata": {"start_char": 0},
                "source_flags": ["vector", "keyword"],
            }
        ],
    }
    assert service.calls == [
        {
            "query": "hybrid search",
            "top_k": 2,
            "vector_weight": 1.2,
            "keyword_weight": 0.8,
        }
    ]


def test_hybrid_service_runs_deeper_searches_and_saves_retrieval_log() -> None:
    async def run_test() -> None:
        vector_service = FakeVectorSearchService()
        keyword_service = FakeKeywordSearchService()
        log_repository = FakeRetrievalLogRepository()
        service = HybridSearchService(
            vector_search_service=vector_service,
            keyword_search_service=keyword_service,
            retrieval_log_repository=log_repository,
        )

        response = await service.search(
            query="hybrid search",
            top_k=2,
            vector_weight=1.0,
            keyword_weight=1.0,
        )

        assert response.results[0].chunk_id == OVERLAP_CHUNK_ID
        assert response.results[0].source_flags == ["vector", "keyword"]
        assert vector_service.calls == [{"query": "hybrid search", "top_k": 6}]
        assert keyword_service.calls == [{"query": "hybrid search", "top_k": 6}]
        assert log_repository.committed is True
        assert log_repository.rolled_back is False
        assert len(log_repository.saved_logs) == 1
        saved_log = log_repository.saved_logs[0]
        assert saved_log["query"] == "hybrid search"
        assert saved_log["vector_results"]["top_k"] == 6
        assert saved_log["keyword_results"]["top_k"] == 6
        assert saved_log["hybrid_results"]["top_k"] == 2

    asyncio.run(run_test())


def test_retrieval_log_repository_can_save_log() -> None:
    async def run_test() -> None:
        session = FakeSession()
        repository = RetrievalLogRepository(session)  # type: ignore[arg-type]

        log = await repository.save_log(
            query="hybrid query",
            vector_results={"results": [{"chunk_id": str(VECTOR_ONLY_CHUNK_ID)}]},
            keyword_results={"results": [{"chunk_id": str(KEYWORD_ONLY_CHUNK_ID)}]},
            hybrid_results={"results": [{"chunk_id": str(OVERLAP_CHUNK_ID)}]},
        )
        await repository.commit()

        assert isinstance(log, RetrievalLog)
        assert session.added == [log]
        assert session.flushed is True
        assert session.committed is True
        assert log.query == "hybrid query"
        assert log.vector_results == {"results": [{"chunk_id": str(VECTOR_ONLY_CHUNK_ID)}]}
        assert log.keyword_results == {"results": [{"chunk_id": str(KEYWORD_ONLY_CHUNK_ID)}]}
        assert log.hybrid_results == {"results": [{"chunk_id": str(OVERLAP_CHUNK_ID)}]}
        assert log.reranked_results is None

    asyncio.run(run_test())
