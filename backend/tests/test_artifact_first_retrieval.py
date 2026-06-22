from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import UUID, uuid4

from app.models.knowledge_artifact import KnowledgeArtifact
from app.schemas.documents import RerankSearchResponse, RerankSearchResult
from app.services.artifact_first_retrieval import ArtifactFirstRetrievalService
from app.services.query_contract_service import QueryContractService
from app.services.rag_runtime_config import RagRuntimeConfigValues

DOCUMENT_ID = UUID("11111111-1111-1111-1111-111111111111")


def test_artifact_first_retrieval_uses_exact_artifact_before_chunk_fallback() -> None:
    async def run_test() -> None:
        artifact = _artifact(
            artifact_type="identifier_lookup",
            canonical_text="Identifier 3113 appears in a document profile.",
            normalized_identifiers={"identifiers": ["3113"]},
        )
        reranking_service = FakeRerankingService()
        service = ArtifactFirstRetrievalService(
            artifact_repository=FakeArtifactRepository(exact_artifacts=[artifact]),  # type: ignore[arg-type]
            artifact_indexing_service=FakeArtifactIndexingService(),  # type: ignore[arg-type]
            reranking_service=reranking_service,  # type: ignore[arg-type]
            query_contract_service=QueryContractService(),
            rag_config=_config(),
        )

        result = await service.retrieve(query="3113 la gi?", top_k=3, candidate_k=10)

        assert result.selected_artifacts == [artifact]
        assert result.chunk_response is None
        assert result.used_chunk_fallback is False
        assert reranking_service.called is False

    asyncio.run(run_test())

def test_artifact_first_retrieval_uses_typed_idea_block_exact_match() -> None:
    async def run_test() -> None:
        artifact = _artifact(
            artifact_type="identifier_lookup",
            idea_block_type="document_identity",
            canonical_text="Loai: Dinh danh van ban\nidentifier: 3113",
            normalized_identifiers={"identifiers": ["3113"]},
        )
        reranking_service = FakeRerankingService()
        service = ArtifactFirstRetrievalService(
            artifact_repository=FakeArtifactRepository(exact_artifacts=[artifact]),  # type: ignore[arg-type]
            artifact_indexing_service=FakeArtifactIndexingService(),  # type: ignore[arg-type]
            reranking_service=reranking_service,  # type: ignore[arg-type]
            query_contract_service=QueryContractService(),
            rag_config=_config(),
        )

        result = await service.retrieve(query="3113 la van ban gi?", top_k=3, candidate_k=10)

        assert result.selected_artifacts == [artifact]
        assert result.used_chunk_fallback is False
        assert reranking_service.called is False

    asyncio.run(run_test())


def test_artifact_first_retrieval_uses_vector_artifact_when_exact_search_misses() -> None:
    async def run_test() -> None:
        artifact = _artifact(
            artifact_type="policy_rule_artifact",
            canonical_text="Policy rule: matched row has one day.",
            structured_data={"case_name": "sample", "days": "1"},
        )
        repository = FakeArtifactRepository(vector_artifacts=[artifact])
        service = ArtifactFirstRetrievalService(
            artifact_repository=repository,  # type: ignore[arg-type]
            artifact_indexing_service=FakeArtifactIndexingService([artifact]),  # type: ignore[arg-type]
            reranking_service=FakeRerankingService(),  # type: ignore[arg-type]
            query_contract_service=QueryContractService(),
            rag_config=_config(),
        )

        result = await service.retrieve(query="duoc nghi may ngay co huong luong?", top_k=3, candidate_k=10)

        assert result.selected_artifacts == [artifact]
        assert result.used_chunk_fallback is False

    asyncio.run(run_test())

def test_artifact_first_retrieval_filters_vector_artifacts_by_exact_identifier() -> None:
    async def run_test() -> None:
        wrong = _artifact(
            artifact_type="document_profile",
            canonical_text="Thong bao 648/TB-IT ve ung dung quan ly tien do.",
            normalized_identifiers={"identifiers": ["648/TB-IT"]},
        )
        right = _artifact(
            artifact_type="document_profile",
            canonical_text="Van ban 907/EVNICT-TTPM co duong dan App Store.",
            normalized_identifiers={"identifiers": ["907/EVNICT-TTPM"]},
        )
        repository = FakeArtifactRepository(vector_artifacts=[wrong, right])
        service = ArtifactFirstRetrievalService(
            artifact_repository=repository,  # type: ignore[arg-type]
            artifact_indexing_service=FakeArtifactIndexingService([wrong, right]),  # type: ignore[arg-type]
            reranking_service=FakeRerankingService(),  # type: ignore[arg-type]
            query_contract_service=QueryContractService(),
            rag_config=_config(),
        )

        result = await service.retrieve(
            query="Trong van ban 907/EVNICT-TTPM, duong dan App Store la gi?",
            top_k=3,
            candidate_k=10,
        )

        assert result.selected_artifacts == [right]

    asyncio.run(run_test())


def test_artifact_first_retrieval_falls_back_to_chunks_when_no_artifact() -> None:
    async def run_test() -> None:
        reranking_service = FakeRerankingService()
        service = ArtifactFirstRetrievalService(
            artifact_repository=FakeArtifactRepository(),  # type: ignore[arg-type]
            artifact_indexing_service=FakeArtifactIndexingService(),  # type: ignore[arg-type]
            reranking_service=reranking_service,  # type: ignore[arg-type]
            query_contract_service=QueryContractService(),
            rag_config=_config(),
        )

        result = await service.retrieve(query="khong co artifact", top_k=3, candidate_k=10)

        assert result.selected_artifacts == []
        assert result.chunk_response is not None
        assert result.used_chunk_fallback is True
        assert reranking_service.called is True

    asyncio.run(run_test())

def test_artifact_first_retrieval_filters_chunk_fallback_by_exact_identifier() -> None:
    async def run_test() -> None:
        wrong = _rerank_result(
            chunk_id=uuid4(),
            document_id=uuid4(),
            content_preview="Thong bao 648/TB-IT ve ung dung quan ly tien do.",
            metadata={"doc_code": "648/TB-IT"},
        )
        right = _rerank_result(
            chunk_id=uuid4(),
            document_id=uuid4(),
            content_preview="Van ban 907/EVNICT-TTPM co duong dan App Store.",
            metadata={"doc_code": "907/EVNICT-TTPM"},
        )
        reranking_service = FakeRerankingService(results=[wrong, right])
        service = ArtifactFirstRetrievalService(
            artifact_repository=FakeArtifactRepository(),  # type: ignore[arg-type]
            artifact_indexing_service=FakeArtifactIndexingService(),  # type: ignore[arg-type]
            reranking_service=reranking_service,  # type: ignore[arg-type]
            query_contract_service=QueryContractService(),
            rag_config=_config(),
        )

        result = await service.retrieve(
            query="Trong van ban 907/EVNICT-TTPM, duong dan App Store la gi?",
            top_k=3,
            candidate_k=10,
        )

        assert result.chunk_response is not None
        assert [item.chunk_id for item in result.chunk_response.results] == [right.chunk_id]

    asyncio.run(run_test())

def test_artifact_first_retrieval_returns_empty_fallback_when_exact_identifier_absent() -> None:
    async def run_test() -> None:
        wrong = _rerank_result(
            chunk_id=uuid4(),
            document_id=uuid4(),
            content_preview="Thong bao 648/TB-IT ve ung dung quan ly tien do.",
            metadata={"doc_code": "648/TB-IT"},
        )
        reranking_service = FakeRerankingService(results=[wrong])
        service = ArtifactFirstRetrievalService(
            artifact_repository=FakeArtifactRepository(),  # type: ignore[arg-type]
            artifact_indexing_service=FakeArtifactIndexingService(),  # type: ignore[arg-type]
            reranking_service=reranking_service,  # type: ignore[arg-type]
            query_contract_service=QueryContractService(),
            rag_config=_config(),
        )

        result = await service.retrieve(
            query="Trong van ban 907/EVNICT-TTPM, duong dan App Store la gi?",
            top_k=3,
            candidate_k=10,
        )

        assert result.chunk_response is not None
        assert result.chunk_response.results == []

    asyncio.run(run_test())


class FakeArtifactRepository:
    def __init__(
        self,
        exact_artifacts: list[KnowledgeArtifact] | None = None,
        vector_artifacts: list[KnowledgeArtifact] | None = None,
    ) -> None:
        self.exact_artifacts = exact_artifacts or []
        self.vector_artifacts = vector_artifacts or []

    async def search_exact(self, **kwargs):
        return self.exact_artifacts

    async def get_by_ids(self, artifact_ids):
        ids = {UUID(str(artifact_id)) for artifact_id in artifact_ids}
        return [artifact for artifact in self.vector_artifacts if artifact.id in ids]


class FakeArtifactIndexingService:
    def __init__(self, artifacts: list[KnowledgeArtifact] | None = None) -> None:
        self.artifacts = artifacts or []

    async def search(self, **kwargs):
        return [
            SimpleNamespace(
                artifact_id=artifact.id,
                score=0.9,
            )
            for artifact in self.artifacts
        ]


class FakeRerankingService:
    def __init__(self, results: list[RerankSearchResult] | None = None) -> None:
        self.called = False
        self.results = results or []

    async def search(self, **kwargs):
        self.called = True
        return RerankSearchResponse(
            query=kwargs["query"],
            top_k=kwargs["top_k"],
            candidate_k=kwargs["candidate_k"],
            results=list(self.results),
        )


def _artifact(
    *,
    artifact_type: str,
    canonical_text: str,
    idea_block_type: str | None = None,
    structured_data: dict | None = None,
    normalized_identifiers: dict | None = None,
) -> KnowledgeArtifact:
    source_chunk_id = str(uuid4())
    return KnowledgeArtifact(
        id=uuid4(),
        document_id=DOCUMENT_ID,
        source_chunk_ids=[source_chunk_id],
        evidence_chunk_ids=[source_chunk_id],
        artifact_type=artifact_type,
        idea_block_type=idea_block_type,
        context_type="identifier" if artifact_type == "identifier_lookup" else "policy",
        title=artifact_type,
        canonical_text=canonical_text,
        idea_metadata=structured_data or {"identifier": "3113"},
        structured_data=structured_data or {"identifier": "3113"},
        normalized_identifiers=normalized_identifiers or {},
        citation_map={"document_id": str(DOCUMENT_ID), "chunks": [{"chunk_id": str(uuid4())}]},
        scope_key=f"{DOCUMENT_ID}|{idea_block_type or artifact_type}",
        content_hash="content-hash",
        dedup_hash="dedup-hash",
        embedding_status="indexed",
        confidence_score=0.9,
        extraction_method="deterministic",
        status="ready",
    )


def _rerank_result(
    *,
    chunk_id: UUID,
    document_id: UUID,
    content_preview: str,
    metadata: dict,
) -> RerankSearchResult:
    return RerankSearchResult(
        chunk_id=chunk_id,
        document_id=document_id,
        rerank_score=0.9,
        fused_score=0.9,
        vector_score=None,
        keyword_score=0.9,
        content_preview=content_preview,
        metadata=metadata,
        source_flags=["keyword"],
    )

def _config() -> RagRuntimeConfigValues:
    return RagRuntimeConfigValues(
        enable_chunk_enrichment_at_ingest=False,
        enable_chunk_enrichment_at_retrieval=False,
        enable_knowledge_artifact_compilation=True,
        enable_llm_artifact_extraction=False,
        enable_artifact_first_retrieval=True,
        enable_chunk_fallback=True,
        enable_neighbor_expansion=False,
        enable_graph_expansion=False,
        artifact_confidence_threshold=0.45,
        retrieval_token_budget=6000,
        max_artifacts=4,
        max_chunks=3,
    )
