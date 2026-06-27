from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from app.models.knowledge_artifact import KnowledgeArtifact
from app.repositories.knowledge_artifacts import KnowledgeArtifactRepository
from app.schemas.documents import RerankSearchResponse
from app.services.knowledge.knowledge_artifact_indexing_service import KnowledgeArtifactIndexingService
from app.services.queries.query_contract_service import QueryContract, QueryContractService
from app.services.rag.rag_runtime_config import RagRuntimeConfigValues
from app.services.rerankers.reranker_service import RerankingService
from app.services.security.security_access_control import AccessFilter, SubjectContext

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ArtifactFirstRetrievalResult:
    query_contract: QueryContract
    selected_artifacts: list[KnowledgeArtifact] = field(default_factory=list)
    chunk_response: RerankSearchResponse | None = None
    used_chunk_fallback: bool = False
    used_neighbor_expansion: bool = False
    confidence_score: float = 0.0
    retrieval_latency_ms: int = 0


class ArtifactFirstRetrievalService:
    def __init__(
        self,
        *,
        artifact_repository: KnowledgeArtifactRepository,
        artifact_indexing_service: KnowledgeArtifactIndexingService,
        reranking_service: RerankingService,
        query_contract_service: QueryContractService,
        rag_config: RagRuntimeConfigValues,
    ) -> None:
        self._artifact_repository = artifact_repository
        self._artifact_indexing_service = artifact_indexing_service
        self._reranking_service = reranking_service
        self._query_contract_service = query_contract_service
        self._rag_config = rag_config

    async def retrieve(
        self,
        *,
        query: str,
        top_k: int,
        candidate_k: int,
        session_id: UUID | None = None,
        document_ids: set[UUID] | None = None,
        use_graph: bool = False,
        graph_expansion_depth: int = 1,
        graph_expansion_limit: int = 20,
        access_filter: AccessFilter | None = None,
        subject_context: SubjectContext | None = None,
        retrieval_enrichment_enabled: bool = False,
        query_intent_rules: dict[str, Any] | None = None,
    ) -> ArtifactFirstRetrievalResult:
        started = time.perf_counter()
        contract = self._query_contract_service.build_contract(
            query,
            confidence_threshold=self._rag_config.artifact_confidence_threshold,
            retrieval_budget=self._rag_config.retrieval_token_budget,
            allow_chunk_fallback=self._rag_config.enable_chunk_fallback,
            allow_neighbor_expansion=self._rag_config.enable_neighbor_expansion,
            allow_graph_expansion=self._rag_config.enable_graph_expansion,
        )

        if not self._rag_config.enable_artifact_first_retrieval:
            chunk_response = await self._fallback_chunks(
                query=query,
                top_k=top_k,
                candidate_k=candidate_k,
                session_id=session_id,
                document_ids=document_ids,
                use_graph=use_graph,
                graph_expansion_depth=graph_expansion_depth,
                graph_expansion_limit=graph_expansion_limit,
                access_filter=access_filter,
                subject_context=subject_context,
                retrieval_enrichment_enabled=retrieval_enrichment_enabled,
                query_intent_rules=query_intent_rules,
            )
            return ArtifactFirstRetrievalResult(
                query_contract=contract,
                chunk_response=chunk_response,
                used_chunk_fallback=True,
                retrieval_latency_ms=self._duration_ms(started),
            )

        selected_artifacts = await self._retrieve_artifacts(
            contract=contract,
            document_ids=document_ids,
        )
        confidence_score = max((artifact.confidence_score for artifact in selected_artifacts), default=0.0)
        artifact_sufficient = self._artifacts_are_sufficient(contract=contract, artifacts=selected_artifacts)

        chunk_response = None
        used_chunk_fallback = False
        if (not artifact_sufficient or not selected_artifacts) and contract.allow_chunk_fallback:
            chunk_response = await self._fallback_chunks(
                query=query,
                top_k=min(max(top_k, self._rag_config.max_chunks), candidate_k),
                candidate_k=candidate_k,
                session_id=session_id,
                document_ids=document_ids,
                use_graph=use_graph and contract.allow_graph_expansion,
                graph_expansion_depth=graph_expansion_depth,
                graph_expansion_limit=graph_expansion_limit,
                access_filter=access_filter,
                subject_context=subject_context,
                retrieval_enrichment_enabled=retrieval_enrichment_enabled,
                query_intent_rules=query_intent_rules,
            )
            used_chunk_fallback = True

        latency_ms = self._duration_ms(started)
        logger.info(
            "artifact_first_retrieval intent=%s contexts=%s artifacts=%s chunks=%s fallback=%s neighbor=%s confidence=%.3f latency_ms=%s token_budget=%s",
            contract.detected_intent,
            contract.target_contexts,
            len(selected_artifacts),
            len(chunk_response.results) if chunk_response is not None else 0,
            used_chunk_fallback,
            contract.allow_neighbor_expansion,
            confidence_score,
            latency_ms,
            contract.retrieval_budget,
        )
        return ArtifactFirstRetrievalResult(
            query_contract=contract,
            selected_artifacts=selected_artifacts,
            chunk_response=chunk_response,
            used_chunk_fallback=used_chunk_fallback,
            used_neighbor_expansion=contract.allow_neighbor_expansion,
            confidence_score=confidence_score,
            retrieval_latency_ms=latency_ms,
        )

    async def _retrieve_artifacts(
        self,
        *,
        contract: QueryContract,
        document_ids: set[UUID] | None,
    ) -> list[KnowledgeArtifact]:
        preferred_types = set(contract.preferred_artifact_types)
        exact_terms = self._artifact_exact_terms(contract)
        exact_artifacts = await self._artifact_repository.search_exact(
            terms=exact_terms,
            document_ids=document_ids,
            artifact_types=preferred_types,
            min_confidence=contract.confidence_threshold,
            limit=self._rag_config.max_artifacts,
        )

        artifacts = list(exact_artifacts)
        if len(artifacts) < self._rag_config.max_artifacts:
            try:
                vector_hits = await self._artifact_indexing_service.search(
                    query=contract.raw_query,
                    top_k=self._rag_config.max_artifacts * 2,
                    document_ids=document_ids,
                )
            except Exception:
                logger.exception("Artifact vector search failed; continuing with Postgres artifacts and chunk fallback.")
                vector_hits = []
            vector_artifact_ids = [hit.artifact_id for hit in vector_hits]
            vector_artifacts = await self._artifact_repository.get_by_ids(vector_artifact_ids)
            score_by_id = {hit.artifact_id: hit.score for hit in vector_hits}
            vector_artifacts = [
                artifact
                for artifact in vector_artifacts
                if artifact.status == "ready"
                and artifact.artifact_type in preferred_types
                and float(artifact.confidence_score or 0.0) >= contract.confidence_threshold
            ]
            vector_artifacts.sort(key=lambda artifact: (-score_by_id.get(artifact.id, 0.0), -float(artifact.confidence_score or 0.0)))
            artifacts.extend(vector_artifacts)

        return self._dedupe_artifacts(artifacts)[: self._rag_config.max_artifacts]

    async def _fallback_chunks(self, **kwargs: Any) -> RerankSearchResponse:
        return await self._reranking_service.search(**kwargs)

    @staticmethod
    def _artifacts_are_sufficient(
        *,
        contract: QueryContract,
        artifacts: list[KnowledgeArtifact],
    ) -> bool:
        if not artifacts:
            return False
        confidence = max(float(artifact.confidence_score or 0.0) for artifact in artifacts)
        if confidence < contract.confidence_threshold:
            return False
        preferred = set(contract.preferred_artifact_types)
        if not any(artifact.artifact_type in preferred for artifact in artifacts):
            return False
        if contract.detected_intent in {"procedure_lookup", "policy_rule_lookup", "person_assignment", "table_lookup"}:
            return any(bool(artifact.structured_data) for artifact in artifacts)
        return True

    @staticmethod
    def _artifact_exact_terms(contract: QueryContract) -> list[str]:
        terms = [*contract.exact_terms]
        terms.extend(contract.filters.get("quoted_terms", []))
        terms.extend(ArtifactFirstRetrievalService._query_phrases(contract.raw_query))
        return ArtifactFirstRetrievalService._dedupe_text(terms, limit=16)

    @staticmethod
    def _query_phrases(query: str) -> list[str]:
        clean = " ".join((query or "").split()).strip(" ?!.,;:")
        tokens = [token for token in re.findall(r"\w+", clean, flags=re.UNICODE) if len(token) > 1]
        phrases: list[str] = []
        if clean:
            phrases.append(clean)
        for size in range(min(4, len(tokens)), 1, -1):
            for index in range(0, len(tokens) - size + 1):
                phrases.append(" ".join(tokens[index : index + size]))
                if len(phrases) >= 12:
                    return phrases
        phrases.extend(tokens)
        return phrases

    @staticmethod
    def _dedupe_text(values: list[str], *, limit: int) -> list[str]:
        ordered: list[str] = []
        seen: set[str] = set()
        for value in values:
            clean = " ".join(str(value or "").split()).strip(" ?!.,;:")
            if len(clean) < 2:
                continue
            key = clean.casefold()
            if key in seen:
                continue
            seen.add(key)
            ordered.append(clean)
            if len(ordered) >= limit:
                break
        return ordered

    @staticmethod
    def _dedupe_artifacts(artifacts: list[KnowledgeArtifact]) -> list[KnowledgeArtifact]:
        ordered: list[KnowledgeArtifact] = []
        seen: set[UUID] = set()
        for artifact in artifacts:
            if artifact.id in seen:
                continue
            seen.add(artifact.id)
            ordered.append(artifact)
        return ordered

    @staticmethod
    def _duration_ms(started: float) -> int:
        return round((time.perf_counter() - started) * 1000)

