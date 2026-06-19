from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import UUID, uuid4

from app.models.knowledge_artifact import KnowledgeArtifact
from app.schemas.documents import RerankSearchResponse
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
    def __init__(self) -> None:
        self.called = False

    async def search(self, **kwargs):
        self.called = True
        return RerankSearchResponse(
            query=kwargs["query"],
            top_k=kwargs["top_k"],
            candidate_k=kwargs["candidate_k"],
            results=[],
        )


def _artifact(
    *,
    artifact_type: str,
    canonical_text: str,
    structured_data: dict | None = None,
    normalized_identifiers: dict | None = None,
) -> KnowledgeArtifact:
    return KnowledgeArtifact(
        id=uuid4(),
        document_id=DOCUMENT_ID,
        source_chunk_ids=[str(uuid4())],
        artifact_type=artifact_type,
        context_type="identifier" if artifact_type == "identifier_lookup" else "policy",
        title=artifact_type,
        canonical_text=canonical_text,
        structured_data=structured_data or {"identifier": "3113"},
        normalized_identifiers=normalized_identifiers or {},
        citation_map={"document_id": str(DOCUMENT_ID), "chunks": [{"chunk_id": str(uuid4())}]},
        confidence_score=0.9,
        extraction_method="deterministic",
        status="ready",
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

