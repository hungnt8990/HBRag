from __future__ import annotations

import inspect
import json
import logging
import re
import unicodedata
from collections.abc import AsyncIterator
from dataclasses import dataclass, replace
from typing import Any
from uuid import UUID

from app.core.config import settings
from app.models.chat import ChatSession
from app.models.chunk import Chunk
from app.models.knowledge_artifact import KnowledgeArtifact
from app.models.user import User
from app.repositories.chat import ChatRepository, CitationCreate
from app.repositories.document_logs import DocumentLogRepository
from app.schemas.chat import RagChatResponse, RagCitationResponse, RagSessionContext
from app.schemas.documents import RerankSearchResult
from app.services.security.security_access_control import (
    AccessAction,
    AccessFilter,
    SubjectContext,
    build_access_filter,
    build_resource_context,
    build_subject_context,
    can_access_resource,
)
from app.services.retrieval.retrieval_artifact_first_retrieval import ArtifactFirstRetrievalResult, ArtifactFirstRetrievalService
from app.services.retrieval.retrieval_hybrid_search import is_identifier_lookup_query
from app.services.llm_gateway import LLMGateway
from app.services.memory.memory_base import MemoryResult
from app.services.queries.query_contract_service import QueryContract, QueryContractService
from app.services.queries.query_intent_rules import is_field_detail_schema_query
from app.services.queries.query_rewrite_service import QueryRewriteResult, QueryRewriteService
from app.services.queries.query_scope_router import classify_query_scope, scoped_direct_answer
from app.services.queries.query_strategy import QueryStrategy, classify_query_strategy
from app.services.chunkers.chunker_table_aware_chunking import extract_entities_from_text
from app.services.rerankers.reranker_service import RerankingService
from app.services.chunkers.chunker_table_relationships import (
    is_trusted_relationship_metadata,
    normalize_metadata_value,
)

MEMORY_RULES = (
    "User Memory and Session Summary are background notes only: never cite them, and if "
    "they conflict with the provided document text, the document text wins. "
    "Citations must only refer to the numbered document passages."
)

LANGUAGE_OUTPUT_RULES = (
    "Answer in the same language as the user's question unless the user asks for a "
    "different language. Preserve proper names, document titles, identifiers, and quoted "
    "source text exactly as written in the document text. Do not create a separate "
    "Sources, References, or Documents section; the application renders the source list separately. Never output "
    "hidden reasoning, chain-of-thought, scratchpad text, internal labels, search mechanics, or <think> tags."
)

SYSTEM_PROMPT = f"You are a grounded document assistant. Answer only from the provided document text. If the document text does not contain the answer, say so naturally. {MEMORY_RULES} {LANGUAGE_OUTPUT_RULES}"

GENERATIVE_PROMPT = (
    f"You are a grounded document assistant. Answer naturally from the provided document text. If the document text does not contain the answer, say so naturally. {MEMORY_RULES} {LANGUAGE_OUTPUT_RULES}"
)

EXTRACTIVE_PROMPT = (
    "You are a document extraction engine. "
    "Return only information explicitly present in the document text. "
    "Do not infer. Do not summarize. Do not rewrite legal wording. "
    "Prefer direct quotations from the numbered document passages. "
    "If the answer is not in the document text, say so naturally. "
    f"{MEMORY_RULES} {LANGUAGE_OUTPUT_RULES}"
)

HYBRID_PROMPT = f"Provide a concise answer grounded in the provided document text, with supporting details. If the answer is not in the document text, say so naturally. {MEMORY_RULES} {LANGUAGE_OUTPUT_RULES}"

ANSWER_MODE_PROMPTS = {
    "generative": GENERATIVE_PROMPT,
    "extractive": EXTRACTIVE_PROMPT,
    "hybrid": HYBRID_PROMPT,
}
DEFAULT_ANSWER_MODE = "hybrid"

CONCISE_STYLE = "Answer style: Concise. Reply in 1-2 sentences without filler."
DETAILED_STYLE = "Answer style: Detailed. Provide a thorough explanation. Preserve exact numbers, dates, money amounts, and legal wording from the document text."
POLICY_EXPLAINER_STYLE = (
    "Answer style: Policy explainer. "
    "1) Answer the direct question first using exact numbers, dates, monetary amounts, "
    "and wording from the document text. "
    "2) Then add only details that directly explain the same answer from the document "
    "passages; do not list loosely related cases, documents, or systems. "
    "If a directly relevant passage contains a summary followed by list items, include "
    "those list items as concise bullets. "
    "3) Include notes and conditions only if they are present and directly relevant. "
    "4) If table rows exist in the document text and the user asks for a list, convert them "
    "into clear bullet points. "
    "5) Use the numbered passage markers only for grounding; do not expose them as clutter. "
    "For short identifier/code queries, answer what the identifier refers "
    "to and the directly attached date/topic only; do not expand into other document "
    "passages unless they literally contain the same identifier. "
    "Do not say 'hÃ´m nay' unless the document text explicitly says today. "
    "Do not repeat the same source line or quote more than once. "
    "Do not list duplicate citations. "
    "If only one relevant item is found, give one concise answer and one source. "
    "Do not invent information or mention search mechanics."
)

IDENTIFIER_LOOKUP_STYLE = (
    "Identifier lookup mode: the user is asking about an exact code, number, "
    "document number, or identifier. Answer only what that identifier refers to in "
    "the document text. Do not expand to related documents, related systems, "
    "similar records, or background information unless the same exact identifier is "
    "present in those passages. Copy document codes, identifiers, dates, organization "
    "names, and proper nouns exactly as they appear in the document text; never normalize, "
    "correct, abbreviate, translate, or rewrite them. If the exact identifier appears "
    "as part of a longer code, preserve the full longer code exactly. If there is any "
    "uncertainty, quote the exact identifier string from the document text instead of "
    "paraphrasing. Do not infer approval, issuer actions, legal effect, signer, or "
    "recipient unless explicitly stated in the document text. For document-number "
    "lookups, answer as a compact document profile: code, title/subject, issuer/date, "
    "referenced document basis, and the directly attached implementation or table-summary "
    "details when those fields are present in the document text. Use bullets when that "
    "makes the document text easier to read. "
)

TABLE_QA_STYLE = (
    "Answer style: Table QA. "
    "You are a document QA assistant. Answer only from the provided document text. "
    "When the question asks for a list, all rows, who, total, amount, or complete "
    "table coverage, list every TABLE_ROW present in ENTITY_MATCHED_ROWS or Document Text "
    "as separate records. "
    "When ENTITY_MATCHED_ROWS exists, treat those rows as structured candidate records, "
    "but also read Document Text for narrative sections, definitions, goals, "
    "conditions, and explanations. Do not ignore narrative text just because table rows exist. "
    "TABLE_SUPPORT is title/header/caption support, not a result list. "
    "When ENTITY_MATCHED_ROWS contains N rows, the main answer must have N bullet "
    "points unless rows are exact duplicates. "
    "If the document text contains TABLE_ROW records, treat each TABLE_ROW as one record. "
    "When the question asks about an entity, find every TABLE_ROW containing that entity "
    "and answer from fields in the same row. "
    "Do not assume fixed column names. Use the original field labels from the document text. "
    "Preserve proper names, addresses, dates, and money amounts exactly as written. "
    "If a total row is present, state the total. "
    "Use TABLE_TITLE and TABLE_HEADER text when available to explain the row. "
    "If multiple rows contain the same entity, list all non-duplicate matching rows. "
    "For each row, prefer descriptive fields over ordinal-only fields. "
    "For yes/no questions, start with a clear affirmative or negative in the user's language. "
    "When a table row states a proposed/assigned/listed role, preserve that wording and "
    "do not infer ownership, leadership, implementation, or sole responsibility. "
    "For entity-to-topic membership, only use table_row or entity_profile context with "
    "high confidence metadata. "
    "If the document text has table_parse_warning or low confidence, say there is not enough direct "
    "evidence and do not use it to confirm membership. "
    "Apply role_note only to the exact person whose row metadata states that note. "
    "If document text conflicts, prefer table_row or entity_profile entries from staff tables. "
    "If a row has only generic labels such as cell_1 or cell_2, use those labels as-is. "
    "Do not say 'similar rows' instead of listing the matching rows. "
    "Do not use a person's list number as the row's main ordinal field. "
    "Do not infer missing fields. "
    "Do not use legal/policy language if the document is not a legal/policy document. "
    "Do not say detailed rows are unavailable when TABLE_ROW records are present. "
    "If there is not enough information, say so clearly in the user's language. "
    "Do not invent information."
)

ANSWER_STYLE_INSTRUCTIONS = {
    "concise": CONCISE_STYLE,
    "detailed": DETAILED_STYLE,
    "policy_explainer": POLICY_EXPLAINER_STYLE,
    "table_qa": TABLE_QA_STYLE,
}
DEFAULT_ANSWER_STYLE = "policy_explainer"
PUBLIC_SOURCE_FLAGS = {"vector", "keyword", "graph", "neighbor", "artifact"}
SOURCE_FLAG_ALIASES = {
    "lexical_exact": "keyword",
    "exact": "keyword",
    "entity_exact": "keyword",
    "keyword_exact": "keyword",
    "primary": "vector",
    "semantic": "vector",
    "knowledge_artifact": "artifact",
}
TABLE_ENUMERATION_QUERY_PATTERNS = (
    "danh sÃ¡ch",
    "liá»‡t kÃª",
    "bao gá»“m",
    "gá»“m nhá»¯ng ai",
    "cÃ³ nhá»¯ng ai",
    "nhá»¯ng há»™ nÃ o",
    "cÃ¡c há»™",
    "cÃ¡c cÃ¡ nhÃ¢n",
    "tá»•ng cá»™ng",
    "sá»‘ tiá»n",
    "bao nhiÃªu",
    "list",
    "all rows",
    "who",
    "total",
    "amount",
)
TABLE_ENUMERATION_CONTEXT_CHAR_LIMIT = 20_000


def system_prompt_for_mode(answer_mode: str | None) -> str:
    if not answer_mode:
        return ANSWER_MODE_PROMPTS[DEFAULT_ANSWER_MODE]
    return ANSWER_MODE_PROMPTS.get(answer_mode.lower().strip(), HYBRID_PROMPT)


def build_system_prompt(
    *,
    answer_mode: str | None,
    answer_style: str | None,
    query: str | None = None,
) -> str:
    base = system_prompt_for_mode(answer_mode)
    style_key = (answer_style or DEFAULT_ANSWER_STYLE).lower().strip()
    style = ANSWER_STYLE_INSTRUCTIONS.get(style_key, POLICY_EXPLAINER_STYLE)
    prompt_parts = [base, style]
    if query and is_identifier_lookup_query(query):
        prompt_parts.append(IDENTIFIER_LOOKUP_STYLE)
    return "\n\n".join(prompt_parts)


QUOTE_LIMIT = 500
SESSION_TITLE_LIMIT = 255
logger = logging.getLogger(__name__)


class RagAnswerError(RuntimeError):
    pass


class ChatSessionNotFoundError(LookupError):
    pass


@dataclass(frozen=True)
class ContextChunk:
    citation_index: int
    chunk: Chunk
    source_type: str = "primary"
    source_flags: list[str] | None = None
    artifact_ids: list[str] | None = None


@dataclass(frozen=True)
class RagStreamEvent:
    event: str
    data: Any


class RagAnswerService:
    def __init__(
        self,
        *,
        chat_repository: ChatRepository,
        reranking_service: RerankingService,
        llm_provider: LLMGateway,
        document_log_repository: DocumentLogRepository | None = None,
        query_rewrite_service: QueryRewriteService | None = None,
        artifact_first_retrieval_service: ArtifactFirstRetrievalService | None = None,
    ) -> None:
        self._chat_repository = chat_repository
        self._reranking_service = reranking_service
        self._llm_provider = llm_provider
        self._document_log_repository = document_log_repository
        self._query_rewrite_service = query_rewrite_service or QueryRewriteService(llm_provider)
        self._artifact_first_retrieval_service = artifact_first_retrieval_service

    async def answer(
        self,
        *,
        query: str,
        session_id: UUID | None,
        top_k: int,
        candidate_k: int,
        current_user: User | None = None,
        document_ids: set[UUID] | None = None,
        session_context: RagSessionContext | None = None,
        memory_context: list[MemoryResult] | None = None,
        session_summary: str | None = None,
        answer_mode: str | None = None,
        answer_style: str | None = None,
        max_context_chars: int = 6000,
        use_graph: bool = False,
        graph_expansion_depth: int = 1,
        graph_expansion_limit: int = 20,
        access_filter: AccessFilter | None = None,
        subject_context: SubjectContext | None = None,
        retrieval_enrichment_enabled: bool = False,
        query_intent_rules: dict[str, Any] | None = None,
    ) -> RagChatResponse:
        if current_user is not None and document_ids is None:
            raise RagAnswerError("Scoped document ids are required for authenticated RAG.")
        try:
            chat_session = await self._get_or_create_session(query=query, session_id=session_id)
            user_message = await self._chat_repository.create_message(
                session_id=chat_session.id,
                role="user",
                content=query,
            )

            scope_result = classify_query_scope(query)
            direct_answer = scoped_direct_answer(scope_result)
            if direct_answer is not None:
                assistant_message = await self._chat_repository.create_message(
                    session_id=chat_session.id,
                    role="assistant",
                    content=direct_answer,
                )
                await self._chat_repository.commit()
                return RagChatResponse(
                    session_id=chat_session.id,
                    user_message_id=user_message.id,
                    assistant_message_id=assistant_message.id,
                    answer=direct_answer,
                    citations=[],
                )

            rewrite_result = await self._rewrite_for_retrieval(
                query=query,
                session_context=session_context,
                memory_context=memory_context,
                session_summary=session_summary,
            )
            retrieval_query = rewrite_result.retrieval_query
            evidence_query = self._evidence_query_for_rewrite(
                query=query,
                rewrite_result=rewrite_result,
            )
            query_strategy = classify_query_strategy(evidence_query)
            retrieval_query = self._strategy_enriched_query(
                retrieval_query,
                query_strategy=query_strategy,
            )
            effective_top_k, effective_candidate_k = self._adaptive_retrieval_window(
                top_k=top_k,
                candidate_k=candidate_k,
                query_strategy=query_strategy,
            )
            effective_max_context_chars = self._adaptive_context_char_limit(
                max_context_chars=max_context_chars,
                query_strategy=query_strategy,
            )
            if current_user is not None and subject_context is None:
                subject_context = build_subject_context(current_user)
            if subject_context is not None and access_filter is None:
                access_filter = build_access_filter(subject_context)

            artifact_result = await self._retrieve_artifact_first_or_rerank(
                query=retrieval_query,
                top_k=effective_top_k,
                candidate_k=effective_candidate_k,
                session_id=chat_session.id,
                document_ids=document_ids,
                use_graph=use_graph,
                graph_expansion_depth=graph_expansion_depth,
                graph_expansion_limit=graph_expansion_limit,
                access_filter=access_filter,
                subject_context=subject_context,
                retrieval_enrichment_enabled=retrieval_enrichment_enabled,
                query_intent_rules=query_intent_rules,
            )
            rerank_response = artifact_result.chunk_response
            selected_artifacts = artifact_result.selected_artifacts
            query_contract = artifact_result.query_contract
            context_chunks = await self._load_context_chunks(
                rerank_results=rerank_response.results if rerank_response is not None else [],
            )
            artifact_context_chunks = await self._load_artifact_source_context_chunks(
                selected_artifacts=selected_artifacts,
                existing_context_chunks=context_chunks,
            )
            context_chunks = [*artifact_context_chunks, *context_chunks]
            context_chunks = self._filter_accessible_context_chunks(
                context_chunks,
                subject_context=subject_context,
            )
            if getattr(settings, "enable_context_expansion", True) and query_contract.allow_neighbor_expansion:
                context_chunks = await self._expand_with_neighbors(
                    query=evidence_query,
                    context_chunks=context_chunks,
                    max_context_chars=effective_max_context_chars,
                    query_strategy=query_strategy,
                )
            context_chunks = self._filter_accessible_context_chunks(
                context_chunks,
                subject_context=subject_context,
            )
            context_chunks = self._deduplicate_context_chunks(context_chunks)
            context_chunks = self._filter_identifier_context(
                query=evidence_query,
                context_chunks=context_chunks,
            )
            context_chunks = await self._augment_person_area_context(
                query=evidence_query,
                context_chunks=context_chunks,
                scoped_document_ids=document_ids,
            )
            context_chunks = self._filter_accessible_context_chunks(
                context_chunks,
                subject_context=subject_context,
            )
            context_chunks = await self._augment_legal_leave_context(
                query=evidence_query,
                context_chunks=context_chunks,
                scoped_document_ids=document_ids,
            )
            context_chunks = self._filter_accessible_context_chunks(
                context_chunks,
                subject_context=subject_context,
            )
            requires_direct_evidence = self._query_requires_direct_entity_evidence(
                query=evidence_query,
                context_chunks=context_chunks,
            )
            if requires_direct_evidence and self._artifacts_contain_named_entity(
                query=evidence_query,
                selected_artifacts=selected_artifacts,
            ):
                requires_direct_evidence = False
            relevance_query = evidence_query if len(self._topical_query_terms(query)) < 2 else query
            relevance_failed = False
            if not self._context_is_topically_relevant(
                query=relevance_query,
                context_chunks=context_chunks,
            ) and not self._artifacts_are_topically_relevant(
                query=relevance_query,
                selected_artifacts=selected_artifacts,
            ):
                relevance_failed = True
                context_chunks = []
                selected_artifacts = []
            if relevance_failed and requires_direct_evidence:
                answer = self._insufficient_direct_evidence_answer(query)
            elif not context_chunks and not selected_artifacts:
                answer = self._missing_accessible_context_answer(query)
            elif requires_direct_evidence:
                answer = self._insufficient_direct_evidence_answer(query)
            else:
                user_prompt = self._build_user_prompt(
                    query=query,
                    standalone_query=evidence_query,
                    context_chunks=context_chunks,
                    query_strategy=query_strategy,
                    memory_context=memory_context,
                    session_summary=session_summary,
                    session_context=session_context,
                    query_intent_rules=query_intent_rules,
                    selected_artifacts=selected_artifacts,
                    query_contract=query_contract,
                )
                answer = await self._llm_provider.generate(
                    system_prompt=build_system_prompt(
                        answer_mode=answer_mode,
                        answer_style=answer_style,
                        query=evidence_query,
                    ),
                    user_prompt=user_prompt,
                )
                answer = self._clean_llm_answer(answer)
            assistant_message = await self._chat_repository.create_message(
                session_id=chat_session.id,
                role="assistant",
                content=answer,
            )
            citation_records = await self._chat_repository.create_citations(
                message_id=assistant_message.id,
                citations=[
                    CitationCreate(
                        chunk_id=context_chunk.chunk.id,
                        document_id=context_chunk.chunk.document_id,
                        quote=self._quote(context_chunk.chunk.content),
                        page_number=self._page_number(context_chunk.chunk.chunk_metadata),
                    )
                    for context_chunk in context_chunks
                ],
            )
            if current_user is not None and self._document_log_repository is not None:
                cited_document_ids = {context_chunk.chunk.document_id for context_chunk in context_chunks}
                for document_id in cited_document_ids:
                    await self._document_log_repository.create_access_log(
                        document_id=document_id,
                        user_id=current_user.id,
                        organization_id=current_user.organization_id,
                        action="chat",
                        metadata={
                            "session_id": str(chat_session.id),
                            "query": query,
                            "retrieval_query": retrieval_query,
                            "evidence_query": evidence_query,
                            "query_strategy": list(query_strategy.strategies),
                            "query_contract": query_contract.detected_intent,
                            "selected_artifact_count": len(selected_artifacts),
                            "used_chunk_fallback": artifact_result.used_chunk_fallback,
                            "rewrite_used": rewrite_result.rewritten,
                            "rewrite_reason": rewrite_result.reason,
                        },
                    )
            await self._chat_repository.commit()
        except ChatSessionNotFoundError:
            await self._chat_repository.rollback()
            raise
        except Exception as exc:
            await self._chat_repository.rollback()
            raise RagAnswerError("Failed to generate RAG answer.") from exc

        return RagChatResponse(
            session_id=chat_session.id,
            user_message_id=user_message.id,
            assistant_message_id=assistant_message.id,
            answer=answer,
            citations=[self._build_citation_response(context_chunk=context_chunk, quote=citation.quote) for context_chunk, citation in zip(context_chunks, citation_records, strict=True)],
        )

    async def answer_stream(
        self,
        *,
        query: str,
        session_id: UUID | None,
        top_k: int,
        candidate_k: int,
        current_user: User | None = None,
        document_ids: set[UUID] | None = None,
        session_context: RagSessionContext | None = None,
        memory_context: list[MemoryResult] | None = None,
        session_summary: str | None = None,
        answer_mode: str | None = None,
        answer_style: str | None = None,
        max_context_chars: int = 6000,
        use_graph: bool = False,
        graph_expansion_depth: int = 1,
        graph_expansion_limit: int = 20,
        access_filter: AccessFilter | None = None,
        subject_context: SubjectContext | None = None,
        retrieval_enrichment_enabled: bool = False,
        query_intent_rules: dict[str, Any] | None = None,
    ) -> AsyncIterator[RagStreamEvent]:
        if current_user is not None and document_ids is None:
            yield RagStreamEvent(
                event="error",
                data={"message": "Scoped document ids are required for authenticated RAG."},
            )
            raise RagAnswerError("Scoped document ids are required for authenticated RAG.")
        try:
            chat_session = await self._get_or_create_session(query=query, session_id=session_id)
            user_message = await self._chat_repository.create_message(
                session_id=chat_session.id,
                role="user",
                content=query,
            )
        except ChatSessionNotFoundError:
            await self._chat_repository.rollback()
            raise

        try:
            scope_result = classify_query_scope(query)
            direct_answer = scoped_direct_answer(scope_result)
            if direct_answer is not None:
                yield RagStreamEvent(
                    event="metadata",
                    data={
                        "session_id": str(chat_session.id),
                        "user_message_id": str(user_message.id),
                        "scope": scope_result.scope,
                        "scope_reason": scope_result.reason,
                    },
                )
                yield RagStreamEvent(event="token", data={"delta": direct_answer})
                assistant_message = await self._chat_repository.create_message(
                    session_id=chat_session.id,
                    role="assistant",
                    content=direct_answer,
                )
                await self._chat_repository.commit()
                yield RagStreamEvent(event="citations", data=[])
                yield RagStreamEvent(
                    event="done",
                    data={"assistant_message_id": str(assistant_message.id)},
                )
                return

            rewrite_result = await self._rewrite_for_retrieval(
                query=query,
                session_context=session_context,
                memory_context=memory_context,
                session_summary=session_summary,
            )
            retrieval_query = rewrite_result.retrieval_query
            evidence_query = self._evidence_query_for_rewrite(
                query=query,
                rewrite_result=rewrite_result,
            )
            query_strategy = classify_query_strategy(evidence_query)
            retrieval_query = self._strategy_enriched_query(
                retrieval_query,
                query_strategy=query_strategy,
            )
            effective_top_k, effective_candidate_k = self._adaptive_retrieval_window(
                top_k=top_k,
                candidate_k=candidate_k,
                query_strategy=query_strategy,
            )
            effective_max_context_chars = self._adaptive_context_char_limit(
                max_context_chars=max_context_chars,
                query_strategy=query_strategy,
            )
            if current_user is not None and subject_context is None:
                subject_context = build_subject_context(current_user)
            if subject_context is not None and access_filter is None:
                access_filter = build_access_filter(subject_context)

            artifact_result = await self._retrieve_artifact_first_or_rerank(
                query=retrieval_query,
                top_k=effective_top_k,
                candidate_k=effective_candidate_k,
                session_id=chat_session.id,
                document_ids=document_ids,
                use_graph=use_graph,
                graph_expansion_depth=graph_expansion_depth,
                graph_expansion_limit=graph_expansion_limit,
                access_filter=access_filter,
                subject_context=subject_context,
                retrieval_enrichment_enabled=retrieval_enrichment_enabled,
                query_intent_rules=query_intent_rules,
            )
            rerank_response = artifact_result.chunk_response
            selected_artifacts = artifact_result.selected_artifacts
            query_contract = artifact_result.query_contract
            context_chunks = await self._load_context_chunks(
                rerank_results=rerank_response.results if rerank_response is not None else [],
            )
            artifact_context_chunks = await self._load_artifact_source_context_chunks(
                selected_artifacts=selected_artifacts,
                existing_context_chunks=context_chunks,
            )
            context_chunks = [*artifact_context_chunks, *context_chunks]
            context_chunks = self._filter_accessible_context_chunks(
                context_chunks,
                subject_context=subject_context,
            )
            if getattr(settings, "enable_context_expansion", True) and query_contract.allow_neighbor_expansion:
                context_chunks = await self._expand_with_neighbors(
                    query=evidence_query,
                    context_chunks=context_chunks,
                    max_context_chars=effective_max_context_chars,
                    query_strategy=query_strategy,
                )
            context_chunks = self._filter_accessible_context_chunks(
                context_chunks,
                subject_context=subject_context,
            )
            context_chunks = self._deduplicate_context_chunks(context_chunks)
            context_chunks = self._filter_identifier_context(
                query=evidence_query,
                context_chunks=context_chunks,
            )
            context_chunks = await self._augment_person_area_context(
                query=evidence_query,
                context_chunks=context_chunks,
                scoped_document_ids=document_ids,
            )
            context_chunks = self._filter_accessible_context_chunks(
                context_chunks,
                subject_context=subject_context,
            )
            context_chunks = await self._augment_legal_leave_context(
                query=evidence_query,
                context_chunks=context_chunks,
                scoped_document_ids=document_ids,
            )
            context_chunks = self._filter_accessible_context_chunks(
                context_chunks,
                subject_context=subject_context,
            )
            requires_direct_evidence = self._query_requires_direct_entity_evidence(
                query=evidence_query,
                context_chunks=context_chunks,
            )
            if requires_direct_evidence and self._artifacts_contain_named_entity(
                query=evidence_query,
                selected_artifacts=selected_artifacts,
            ):
                requires_direct_evidence = False
            relevance_query = evidence_query if len(self._topical_query_terms(query)) < 2 else query
            relevance_failed = False
            if not self._context_is_topically_relevant(
                query=relevance_query,
                context_chunks=context_chunks,
            ) and not self._artifacts_are_topically_relevant(
                query=relevance_query,
                selected_artifacts=selected_artifacts,
            ):
                relevance_failed = True
                context_chunks = []
                selected_artifacts = []
            yield RagStreamEvent(
                event="metadata",
                data={
                    "session_id": str(chat_session.id),
                    "user_message_id": str(user_message.id),
                    "retrieval_query": retrieval_query,
                    "evidence_query": evidence_query,
                    "query_strategy": list(query_strategy.strategies),
                    "query_contract": query_contract.detected_intent,
                    "selected_artifact_count": len(selected_artifacts),
                    "used_chunk_fallback": artifact_result.used_chunk_fallback,
                    "rewrite_used": rewrite_result.rewritten,
                    "rewrite_reason": rewrite_result.reason,
                },
            )

            if relevance_failed and requires_direct_evidence:
                answer = self._insufficient_direct_evidence_answer(query)
                yield RagStreamEvent(event="token", data={"delta": answer})
            elif not context_chunks and not selected_artifacts:
                answer = self._missing_accessible_context_answer(query)
                yield RagStreamEvent(event="token", data={"delta": answer})
            elif requires_direct_evidence:
                answer = self._insufficient_direct_evidence_answer(query)
                yield RagStreamEvent(event="token", data={"delta": answer})
            else:
                user_prompt = self._build_user_prompt(
                    query=query,
                    standalone_query=evidence_query,
                    context_chunks=context_chunks,
                    query_strategy=query_strategy,
                    memory_context=memory_context,
                    session_summary=session_summary,
                    session_context=session_context,
                    query_intent_rules=query_intent_rules,
                    selected_artifacts=selected_artifacts,
                    query_contract=query_contract,
                )
                answer_parts: list[str] = []
                async for delta in self._llm_provider.stream_generate(
                    system_prompt=build_system_prompt(
                        answer_mode=answer_mode,
                        answer_style=answer_style,
                        query=evidence_query,
                    ),
                    user_prompt=user_prompt,
                ):
                    if not delta:
                        continue
                    answer_parts.append(delta)

                answer = self._clean_llm_answer("".join(answer_parts))
                if answer:
                    yield RagStreamEvent(event="token", data={"delta": answer})
            assistant_message = await self._chat_repository.create_message(
                session_id=chat_session.id,
                role="assistant",
                content=answer,
            )
            citation_records = await self._chat_repository.create_citations(
                message_id=assistant_message.id,
                citations=[
                    CitationCreate(
                        chunk_id=context_chunk.chunk.id,
                        document_id=context_chunk.chunk.document_id,
                        quote=self._quote(context_chunk.chunk.content),
                        page_number=self._page_number(context_chunk.chunk.chunk_metadata),
                    )
                    for context_chunk in context_chunks
                ],
            )
            if current_user is not None and self._document_log_repository is not None:
                cited_document_ids = {context_chunk.chunk.document_id for context_chunk in context_chunks}
                for document_id in cited_document_ids:
                    await self._document_log_repository.create_access_log(
                        document_id=document_id,
                        user_id=current_user.id,
                        organization_id=current_user.organization_id,
                        action="chat",
                        metadata={
                            "session_id": str(chat_session.id),
                            "query": query,
                            "retrieval_query": retrieval_query,
                            "evidence_query": evidence_query,
                            "query_strategy": list(query_strategy.strategies),
                            "query_contract": query_contract.detected_intent,
                            "selected_artifact_count": len(selected_artifacts),
                            "used_chunk_fallback": artifact_result.used_chunk_fallback,
                            "rewrite_used": rewrite_result.rewritten,
                            "rewrite_reason": rewrite_result.reason,
                        },
                    )
            await self._chat_repository.commit()
        except Exception as exc:
            await self._chat_repository.rollback()
            logger.exception("Failed to generate RAG answer stream")
            error_data = {"message": "Failed to generate RAG answer."}
            if settings.environment.casefold() not in {"prod", "production"}:
                error_data["detail"] = f"{type(exc).__name__}: {exc}"
            yield RagStreamEvent(
                event="error",
                data=error_data,
            )
            raise RagAnswerError("Failed to generate RAG answer.") from exc

        yield RagStreamEvent(
            event="citations",
            data=[
                self._build_citation_response(
                    context_chunk=context_chunk,
                    quote=citation.quote,
                ).model_dump(mode="json")
                for context_chunk, citation in zip(context_chunks, citation_records, strict=True)
            ],
        )
        yield RagStreamEvent(
            event="done",
            data={"assistant_message_id": str(assistant_message.id)},
        )

    async def _get_or_create_session(
        self,
        *,
        query: str,
        session_id: UUID | None,
    ) -> ChatSession:
        if session_id is None:
            return await self._chat_repository.create_session(title=self._session_title(query))

        chat_session = await self._chat_repository.get_session(session_id)
        if chat_session is None:
            raise ChatSessionNotFoundError("Chat session not found.")
        return chat_session

    async def _run_reranking_search(self, **kwargs):
        parameters = inspect.signature(self._reranking_service.search).parameters
        accepts_var_kwargs = any(parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in parameters.values())
        supported = kwargs if accepts_var_kwargs else {key: value for key, value in kwargs.items() if key in parameters}
        return await self._reranking_service.search(**supported)

    async def _retrieve_artifact_first_or_rerank(self, **kwargs) -> ArtifactFirstRetrievalResult:
        artifact_service = self._artifact_first_retrieval_service
        if artifact_service is not None:
            try:
                return await artifact_service.retrieve(**kwargs)
            except Exception:
                logger.exception("Artifact-first retrieval failed; falling back to chunk reranking.")

        rerank_response = await self._run_reranking_search(**kwargs)
        query_contract = QueryContractService().build_contract(
            str(kwargs.get("query") or ""),
            allow_graph_expansion=bool(kwargs.get("use_graph")),
        )
        return ArtifactFirstRetrievalResult(
            query_contract=query_contract,
            selected_artifacts=[],
            chunk_response=rerank_response,
            used_chunk_fallback=True,
        )

    async def _load_artifact_source_context_chunks(
        self,
        *,
        selected_artifacts: list[KnowledgeArtifact],
        existing_context_chunks: list[ContextChunk],
    ) -> list[ContextChunk]:
        if not selected_artifacts:
            return []
        artifact_ids_by_chunk_id: dict[UUID, list[str]] = {}
        chunk_ids: list[UUID] = []
        for artifact in selected_artifacts:
            for raw_chunk_id in artifact.source_chunk_ids or []:
                try:
                    chunk_id = UUID(str(raw_chunk_id))
                except (TypeError, ValueError):
                    continue
                if chunk_id not in artifact_ids_by_chunk_id:
                    chunk_ids.append(chunk_id)
                    artifact_ids_by_chunk_id[chunk_id] = []
                artifact_ids_by_chunk_id[chunk_id].append(str(artifact.id))
        if not chunk_ids:
            return []
        chunks = await self._chat_repository.get_chunks_by_ids(chunk_ids)
        chunk_by_id = {chunk.id: chunk for chunk in chunks}
        next_index = max((item.citation_index for item in existing_context_chunks), default=0) + 1
        context_chunks: list[ContextChunk] = []
        for chunk_id in chunk_ids:
            chunk = chunk_by_id.get(chunk_id)
            if chunk is None:
                continue
            context_chunks.append(
                ContextChunk(
                    citation_index=next_index,
                    chunk=chunk,
                    source_type="artifact",
                    source_flags=["artifact"],
                    artifact_ids=artifact_ids_by_chunk_id.get(chunk_id, []),
                )
            )
            next_index += 1
        return context_chunks

    async def _rewrite_for_retrieval(
        self,
        *,
        query: str,
        session_context: RagSessionContext | None,
        memory_context: list[MemoryResult] | None,
        session_summary: str | None,
    ) -> QueryRewriteResult:
        try:
            result = await self._query_rewrite_service.rewrite(
                query=query,
                session_context=session_context,
                memory_context=memory_context,
                session_summary=session_summary,
            )
        except Exception:
            fallback_query = self._query_with_short_term_context(
                query=query,
                session_context=session_context,
            )
            return QueryRewriteResult(
                original_query=query,
                retrieval_query=fallback_query,
                rewritten=fallback_query != query,
                reason="rewrite_service_error_context_hints",
            )

        retrieval_query = " ".join((result.retrieval_query or query).split())
        if not retrieval_query:
            retrieval_query = query
        if retrieval_query == result.retrieval_query:
            return result
        return replace(result, retrieval_query=retrieval_query)

    @staticmethod
    def _evidence_query_for_rewrite(
        *,
        query: str,
        rewrite_result: QueryRewriteResult,
    ) -> str:
        if rewrite_result.reason.endswith("context_hints"):
            return query
        return rewrite_result.retrieval_query or query

    @staticmethod
    def _strategy_enriched_query(
        query: str,
        *,
        query_strategy: QueryStrategy,
    ) -> str:
        if not getattr(settings, "enable_query_enrichment", True):
            return query
        if not (
            query_strategy.requires_overview_context
            or "table_detail" in query_strategy.strategies
            or "comparison" in query_strategy.strategies
        ):
            return query

        base_query = " ".join((query or "").split()).strip()
        existing = normalize_metadata_value(RagAnswerService._strip_vietnamese_accents(base_query))
        terms = RagAnswerService._dedupe_text_values(
            [
                *query_strategy.search_terms,
                *RagAnswerService._query_content_phrases(base_query),
            ],
            limit=16,
        )
        expansion_terms: list[str] = []
        for term in terms:
            key = normalize_metadata_value(RagAnswerService._strip_vietnamese_accents(term))
            if not key or key in existing:
                continue
            expansion_terms.append(term)
            if len(expansion_terms) >= 10:
                break

        if not expansion_terms:
            return query

        expansion = "\n".join(f"- {term}" for term in expansion_terms)
        return (
            f"{base_query}\n\n"
            "Retrieval expansion terms derived from query strategy; "
            "use only for search, not as answer facts:\n"
            f"{expansion}"
        )

    @staticmethod
    def _adaptive_retrieval_window(
        *,
        top_k: int,
        candidate_k: int,
        query_strategy: QueryStrategy,
    ) -> tuple[int, int]:
        effective_top_k = max(1, int(top_k or 1))
        strategy_names = set(query_strategy.strategies)
        configured_windows: list[int] = []
        if query_strategy.requires_overview_context:
            configured_windows.extend(
                [
                    getattr(settings, "overview_top_k", 0),
                    getattr(settings, "summary_top_k", 0),
                ]
            )
        if "table_detail" in strategy_names:
            configured_windows.append(getattr(settings, "table_top_k", 0))
        if strategy_names == {"semantic_search"}:
            configured_windows.append(getattr(settings, "raw_top_k", 0))

        for value in configured_windows:
            try:
                configured = int(value or 0)
            except (TypeError, ValueError):
                configured = 0
            if configured > 0:
                effective_top_k = max(effective_top_k, configured)

        effective_candidate_k = max(int(candidate_k or effective_top_k), effective_top_k)
        return effective_top_k, effective_candidate_k

    @staticmethod
    def _adaptive_context_char_limit(
        *,
        max_context_chars: int,
        query_strategy: QueryStrategy,
    ) -> int:
        effective_limit = max(1, int(max_context_chars or 1))
        if not (
            query_strategy.requires_overview_context
            or "table_detail" in query_strategy.strategies
        ):
            return effective_limit
        try:
            configured_limit = int(getattr(settings, "max_context_chars", 0) or 0)
        except (TypeError, ValueError):
            configured_limit = 0
        return max(effective_limit, configured_limit) if configured_limit > 0 else effective_limit

    async def _load_context_chunks(
        self,
        *,
        rerank_results: list[RerankSearchResult],
    ) -> list[ContextChunk]:
        chunk_ids = [UUID(str(result.chunk_id)) for result in rerank_results]
        chunks = await self._chat_repository.get_chunks_by_ids(chunk_ids)
        chunk_by_id = {chunk.id: chunk for chunk in chunks}
        result_by_id = {str(result.chunk_id): result for result in rerank_results}

        return [
            ContextChunk(
                citation_index=index,
                chunk=chunk,
                source_type=(result_by_id[str(chunk_id)].source_flags or ["primary"])[0],
                source_flags=list(result_by_id[str(chunk_id)].source_flags or ["primary"]),
            )
            for index, chunk_id in enumerate(chunk_ids, start=1)
            if (chunk := chunk_by_id.get(chunk_id)) is not None
        ]

    @staticmethod
    def _filter_accessible_context_chunks(
        context_chunks: list[ContextChunk],
        *,
        subject_context: SubjectContext | None,
    ) -> list[ContextChunk]:
        if subject_context is None:
            return context_chunks
        filtered: list[ContextChunk] = []
        for context_chunk in context_chunks:
            metadata = context_chunk.chunk.chunk_metadata or {}
            if not isinstance(metadata, dict) or not isinstance(metadata.get("access"), dict):
                filtered.append(context_chunk)
                continue
            decision = can_access_resource(
                subject_context,
                build_resource_context(None, context_chunk.chunk),
                AccessAction.READ_ANSWER,
            )
            if decision.allowed:
                filtered.append(context_chunk)
        return filtered

    async def _expand_with_neighbors(
        self,
        *,
        query: str,
        context_chunks: list[ContextChunk],
        max_context_chars: int,
        query_strategy: QueryStrategy | None = None,
    ) -> list[ContextChunk]:
        if not context_chunks:
            return context_chunks

        get_article_neighbors = getattr(self._chat_repository, "get_neighbor_chunks", None)
        get_entity_coverage_chunks = getattr(
            self._chat_repository,
            "get_entity_coverage_chunks",
            None,
        )
        get_table_chunks = getattr(self._chat_repository, "get_table_chunks", None)
        if get_article_neighbors is None and get_table_chunks is None and get_entity_coverage_chunks is None:
            return context_chunks

        existing_ids: set[UUID] = {context_chunk.chunk.id for context_chunk in context_chunks}
        seen_articles: set[tuple[UUID, str]] = set()
        seen_documents: set[UUID] = set()
        seen_tables: set[tuple[UUID, str]] = set()
        total_chars = sum(len(item.chunk.content) for item in context_chunks)
        query_strategy = query_strategy or classify_query_strategy(query)
        query_terms = self._query_terms(query)
        coverage_search_terms = self._dedupe_text_values(
            [*query_terms, *query_strategy.search_terms],
            limit=64,
        )
        if self._is_schema_count_query(query):
            query_terms = self._schema_count_search_terms(query_terms)
            coverage_search_terms = query_terms
        wants_full_table = self._is_table_enumeration_query(query)
        wants_schema_context = self._is_schema_count_query(query)
        wants_overview_context = query_strategy.requires_overview_context
        context_char_limit = max(max_context_chars, TABLE_ENUMERATION_CONTEXT_CHAR_LIMIT) if wants_full_table or wants_schema_context or wants_overview_context else max_context_chars

        expanded = list(context_chunks)
        next_index = max((item.citation_index for item in context_chunks), default=0) + 1

        for context_chunk in context_chunks:
            metadata = context_chunk.chunk.chunk_metadata or {}
            document_id = context_chunk.chunk.document_id
            if coverage_search_terms and get_entity_coverage_chunks is not None and document_id not in seen_documents:
                seen_documents.add(document_id)
                try:
                    coverage_chunks = await get_entity_coverage_chunks(
                        document_id=document_id,
                        search_terms=coverage_search_terms,
                        exclude_ids=tuple(existing_ids),
                    )
                except Exception:
                    coverage_chunks = []

                coverage_added = 0
                for coverage_chunk in self._prioritize_entity_coverage_chunks(
                    chunks=coverage_chunks,
                    query_terms=query_terms,
                    prefer_structural_schema=wants_schema_context,
                ):
                    if coverage_chunk.id in existing_ids:
                        continue
                    coverage_len = len(coverage_chunk.content or "")
                    over_context_limit = total_chars + coverage_len > context_char_limit
                    budget_override_limit = 6 if wants_schema_context else 2
                    allow_budget_override = coverage_added < budget_override_limit and RagAnswerService._is_high_signal_context_chunk(
                        chunk=coverage_chunk,
                        query_terms=query_terms,
                    )
                    if over_context_limit and not allow_budget_override:
                        continue
                    expanded.append(
                        ContextChunk(
                            citation_index=next_index,
                            chunk=coverage_chunk,
                            source_type="neighbor",
                            source_flags=[
                                *(context_chunk.source_flags or [context_chunk.source_type]),
                                "neighbor",
                            ],
                        )
                    )
                    existing_ids.add(coverage_chunk.id)
                    total_chars += coverage_len
                    coverage_added += 1
                    next_index += 1

            table_refs = self._table_references(metadata)
            if table_refs and get_table_chunks is not None:
                for table_id in table_refs:
                    table_key = (document_id, table_id)
                    if table_key in seen_tables:
                        continue
                    seen_tables.add(table_key)
                    try:
                        neighbors = await get_table_chunks(
                            document_id=document_id,
                            table_id=table_id,
                            exclude_ids=tuple(existing_ids),
                        )
                    except Exception:
                        neighbors = []
                    neighbors = [neighbor for neighbor in neighbors if neighbor.document_id == document_id]

                    relevant_neighbors = self._prioritize_table_neighbors(
                        neighbors=neighbors,
                        query_terms=query_terms,
                        include_full_table=wants_full_table,
                    )
                    for neighbor in relevant_neighbors:
                        if neighbor.id in existing_ids:
                            continue
                        neighbor_len = len(neighbor.content or "")
                        if total_chars + neighbor_len > context_char_limit:
                            return expanded
                        expanded.append(
                            ContextChunk(
                                citation_index=next_index,
                                chunk=neighbor,
                                source_type="neighbor",
                                source_flags=[
                                    *(context_chunk.source_flags or [context_chunk.source_type]),
                                    "neighbor",
                                ],
                            )
                        )
                        existing_ids.add(neighbor.id)
                        total_chars += neighbor_len
                        next_index += 1

            article_number = metadata.get("article_number")
            if not article_number or get_article_neighbors is None:
                continue

            key = (document_id, str(article_number))
            if key in seen_articles:
                continue
            seen_articles.add(key)

            try:
                neighbors = await get_article_neighbors(
                    document_id=document_id,
                    article_number=str(article_number),
                    exclude_ids=tuple(existing_ids),
                )
            except Exception:
                continue

            for neighbor in neighbors:
                if neighbor.id in existing_ids:
                    continue
                neighbor_len = len(neighbor.content or "")
                if total_chars + neighbor_len > context_char_limit:
                    return expanded
                expanded.append(
                    ContextChunk(
                        citation_index=next_index,
                        chunk=neighbor,
                        source_type="neighbor",
                        source_flags=[
                            *(context_chunk.source_flags or [context_chunk.source_type]),
                            "neighbor",
                        ],
                    )
                )
                existing_ids.add(neighbor.id)
                total_chars += neighbor_len
                next_index += 1

        return expanded

    async def _augment_legal_leave_context(
        self,
        *,
        query: str,
        context_chunks: list[ContextChunk],
        scoped_document_ids: set[UUID] | None = None,
    ) -> list[ContextChunk]:
        """Recover exact structured row/section facts for table-like questions.

        The method name is kept for backward compatibility with older tests and
        call sites. The logic is no longer tied to one legal table: it derives
        search terms from the user's question and prioritizes chunks whose
        metadata/content look like direct row facts.
        """

        if not self._is_legal_leave_query(query):
            return context_chunks

        get_entity_coverage_chunks = getattr(
            self._chat_repository,
            "get_entity_coverage_chunks",
            None,
        )
        if get_entity_coverage_chunks is None:
            return context_chunks

        existing_ids: set[UUID] = {item.chunk.id for item in context_chunks}
        candidate_document_ids: list[UUID] = []
        if scoped_document_ids:
            candidate_document_ids.extend(sorted(scoped_document_ids, key=str))
        candidate_document_ids.extend(item.chunk.document_id for item in context_chunks)

        ordered_document_ids: list[UUID] = []
        seen_documents: set[UUID] = set()
        for document_id in candidate_document_ids:
            if document_id in seen_documents:
                continue
            seen_documents.add(document_id)
            ordered_document_ids.append(document_id)

        if not ordered_document_ids:
            return context_chunks

        search_terms = self._legal_leave_search_terms(query)
        if not search_terms:
            return context_chunks

        augmented = list(context_chunks)
        next_index = max((item.citation_index for item in augmented), default=0) + 1
        for document_id in ordered_document_ids:
            try:
                coverage_chunks = await get_entity_coverage_chunks(
                    document_id=document_id,
                    search_terms=search_terms,
                    exclude_ids=tuple(existing_ids),
                    max_matches=100,
                )
            except TypeError:
                coverage_chunks = await get_entity_coverage_chunks(
                    document_id=document_id,
                    search_terms=search_terms,
                    exclude_ids=tuple(existing_ids),
                )
            except Exception:
                logger.exception("Failed to augment structured fact context")
                continue

            for chunk in self._prioritize_legal_leave_chunks(
                chunks=coverage_chunks,
                query=query,
            ):
                if chunk.id in existing_ids:
                    continue
                augmented.append(
                    ContextChunk(
                        citation_index=next_index,
                        chunk=chunk,
                        source_type="structured_fact_exact",
                        source_flags=["structured_fact_exact"],
                    )
                )
                existing_ids.add(chunk.id)
                next_index += 1

        return self._deduplicate_context_chunks(augmented)

    @staticmethod
    def _is_legal_leave_query(query: str) -> bool:
        """Backward-compatible wrapper for the generic structured-fact pass."""

        return RagAnswerService._should_run_structured_fact_pass(query)

    @staticmethod
    def _should_run_structured_fact_pass(query: str) -> bool:
        """Return True when it is safe to attempt structured evidence recovery.

        This deliberately avoids language-specific intent keywords such as
        "bao nhiÃªu", "má»¥c tiÃªu", or "tham gia". Structured retrieval is cheap
        and safe to try for any non-empty query; row/section scoring plus
        answerability thresholds decide whether retrieved structured evidence is
        actually relevant.
        """

        normalized = normalize_metadata_value(RagAnswerService._strip_vietnamese_accents(query))
        return bool(normalized)

    @staticmethod
    def _legal_leave_search_terms(query: str) -> list[str]:
        """Build exact coverage terms only from the user's query text."""

        terms: list[str] = [query]
        terms.extend(RagAnswerService._query_content_phrases(query))

        ordered: list[str] = []
        seen: set[str] = set()
        for term in terms:
            clean = " ".join(str(term or "").split()).strip()
            if not clean:
                continue
            key = normalize_metadata_value(RagAnswerService._strip_vietnamese_accents(clean))
            if key in seen:
                continue
            seen.add(key)
            ordered.append(clean)
        return ordered

    @staticmethod
    def _query_content_phrases(query: str) -> list[str]:
        """Extract query-derived n-grams without language/domain stopword lists.

        The returned terms are built only from the user's text. Relevance is
        decided later by retrieval scoring, so this helper must not encode
        Vietnamese intent words, table cases, legal terms, or organization names.
        """

        normalized = normalize_metadata_value(RagAnswerService._strip_vietnamese_accents(query))
        tokens = [token for token in re.findall(r"[a-z0-9]+", normalized) if len(token) > 1]
        if not tokens:
            return []

        phrases: list[str] = []
        for size in range(min(6, len(tokens)), 1, -1):
            for index in range(0, len(tokens) - size + 1):
                phrase = " ".join(tokens[index : index + size])
                if 3 <= len(phrase) <= 100:
                    phrases.append(phrase)
                if len(phrases) >= 12:
                    return RagAnswerService._dedupe_text_values(phrases, limit=12)

        phrases.extend(tokens)
        return RagAnswerService._dedupe_text_values(phrases, limit=16)

    @staticmethod
    def _is_structured_fact_row(metadata: dict[str, Any]) -> bool:
        chunk_type = str(metadata.get("chunk_type") or "")
        relationship_type = str(metadata.get("relationship_type") or "")
        legacy_relationship_type = str(metadata.get("legacy_relationship_type") or "")
        return (
            "row" in chunk_type or relationship_type in {"structured_fact_row", "legal_leave_benefit"} or legacy_relationship_type == "legal_leave_benefit" or bool(metadata.get("case_name") or metadata.get("row_text"))
        )

    @staticmethod
    def _prioritize_legal_leave_chunks(
        *,
        chunks: list[Chunk],
        query: str,
    ) -> list[Chunk]:
        def priority(chunk: Chunk) -> tuple[int, float, int]:
            metadata = chunk.chunk_metadata or {}
            content_key = normalize_metadata_value(RagAnswerService._strip_vietnamese_accents(chunk.content or ""))
            if RagAnswerService._is_structured_fact_row(metadata):
                score = RagAnswerService._structured_fact_metadata_score(
                    query=query,
                    metadata=metadata,
                    content=chunk.content or "",
                )
                return (0, -score, chunk.chunk_index)
            if any(term in content_key for term in RagAnswerService._query_content_phrases(query)):
                return (3, 0.0, chunk.chunk_index)
            return (8, 0.0, chunk.chunk_index)

        return sorted(chunks, key=priority)

    @staticmethod
    def _metadata_score_tokens(value: Any) -> set[str]:
        text = normalize_metadata_value(RagAnswerService._strip_vietnamese_accents(str(value or "")))
        return {token for token in re.findall(r"[a-z0-9]+", text) if len(token) > 1}

    @staticmethod
    def _structured_fact_metadata_score(
        *,
        query: str,
        metadata: dict[str, Any],
        content: str,
    ) -> float:
        query_tokens = RagAnswerService._metadata_score_tokens(query)
        if not query_tokens:
            return 0.0

        field_values = [value for key, value in metadata.items() if key not in {"chunk_id", "chunk_type", "source_file", "source_uri"}]
        field_values.append(content)
        metadata_tokens = set().union(*(RagAnswerService._metadata_score_tokens(value) for value in field_values))
        if not metadata_tokens:
            return 0.0

        overlap = query_tokens & metadata_tokens
        if not overlap:
            return 0.0

        precision = len(overlap) / max(len(metadata_tokens), 1)
        recall = len(overlap) / max(len(query_tokens), 1)
        return (recall * 0.75) + (precision * 0.25) + (0.05 * len(overlap))

    async def _augment_person_area_context(
        self,
        *,
        query: str,
        context_chunks: list[ContextChunk],
        scoped_document_ids: set[UUID] | None = None,
    ) -> list[ContextChunk]:
        """Pull exact entity rows so the LLM receives complete grounded evidence."""

        person_name = self._person_area_query_person(query)
        if person_name is None:
            return context_chunks

        get_entity_coverage_chunks = getattr(
            self._chat_repository,
            "get_entity_coverage_chunks",
            None,
        )
        if get_entity_coverage_chunks is None:
            return context_chunks

        document_ids: list[UUID] = []
        seen_document_ids: set[UUID] = set()
        existing_ids: set[UUID] = {context_chunk.chunk.id for context_chunk in context_chunks}

        # Search exact person rows in both initially retrieved documents and the
        # authenticated permission scope. The initial retrieval may miss a table
        # row for person-name questions, so this second pass recovers trusted
        # TABLE_ROW/entity_profile evidence without relying on LLM inference.
        candidate_document_ids: list[UUID] = []
        if scoped_document_ids:
            candidate_document_ids.extend(sorted(scoped_document_ids, key=str))
        candidate_document_ids.extend(context_chunk.chunk.document_id for context_chunk in context_chunks)

        for document_id in candidate_document_ids:
            if document_id in seen_document_ids:
                continue
            seen_document_ids.add(document_id)
            document_ids.append(document_id)

        search_terms = self._person_name_search_terms(person_name)
        if not search_terms:
            return context_chunks

        augmented = list(context_chunks)
        next_index = max((item.citation_index for item in augmented), default=0) + 1
        for document_id in document_ids:
            try:
                coverage_chunks = await get_entity_coverage_chunks(
                    document_id=document_id,
                    search_terms=search_terms,
                    exclude_ids=tuple(existing_ids),
                    max_matches=100,
                )
            except TypeError:
                coverage_chunks = await get_entity_coverage_chunks(
                    document_id=document_id,
                    search_terms=search_terms,
                    exclude_ids=tuple(existing_ids),
                )
            except Exception:
                logger.exception("Failed to augment person-area context for %s", person_name)
                continue

            for chunk in self._prioritize_person_area_chunks(
                chunks=coverage_chunks,
                person_name=person_name,
            ):
                if chunk.id in existing_ids:
                    continue
                augmented.append(
                    ContextChunk(
                        citation_index=next_index,
                        chunk=chunk,
                        source_type="entity_exact",
                        source_flags=["entity_exact"],
                    )
                )
                existing_ids.add(chunk.id)
                next_index += 1

        return self._deduplicate_context_chunks(augmented)

    @staticmethod
    def _person_name_search_terms(person_name: str) -> list[str]:
        terms: list[str] = []
        compact = " ".join(person_name.split()).strip()
        if compact:
            terms.append(compact)
        ascii_name = RagAnswerService._strip_vietnamese_accents(compact)
        if ascii_name and ascii_name.casefold() != compact.casefold():
            terms.append(ascii_name)
        return terms

    @staticmethod
    def _person_name_matches(candidate: str, person_name: str) -> bool:
        candidate_key = normalize_metadata_value(candidate)
        person_key = normalize_metadata_value(person_name)
        if not candidate_key or not person_key:
            return False
        if candidate_key == person_key:
            return True
        person_tokens = person_key.split()
        if len(person_tokens) >= 2 and person_key in candidate_key:
            return True
        candidate_tokens = set(candidate_key.split())
        return bool(person_tokens) and set(person_tokens).issubset(candidate_tokens)

    @staticmethod
    def _strip_vietnamese_accents(value: str) -> str:
        normalized = unicodedata.normalize("NFD", value or "")
        stripped = "".join(char for char in normalized if unicodedata.category(char) != "Mn")
        return stripped.replace("Ä", "D").replace("Ä‘", "d")

    @staticmethod
    def _prioritize_person_area_chunks(
        *,
        chunks: list[Chunk],
        person_name: str,
    ) -> list[Chunk]:
        def priority(chunk: Chunk) -> tuple[int, int]:
            metadata = chunk.chunk_metadata or {}
            chunk_type = str(metadata.get("chunk_type") or "")
            if chunk_type == "entity_profile":
                metadata_name = str(metadata.get("person_name") or metadata.get("entity_name") or "")
                if RagAnswerService._person_name_matches(metadata_name, person_name):
                    return (0, chunk.chunk_index)
                if RagAnswerService._person_name_matches(chunk.content or "", person_name):
                    return (3, chunk.chunk_index)
                return (6, chunk.chunk_index)
            if is_trusted_relationship_metadata(metadata):
                return (1, chunk.chunk_index)
            content_key = normalize_metadata_value(chunk.content or "")
            if "table_row" in content_key and RagAnswerService._person_name_matches(
                content_key,
                person_name,
            ):
                return (2, chunk.chunk_index)
            if "entity_summary" in content_key and RagAnswerService._person_name_matches(
                content_key,
                person_name,
            ):
                return (3, chunk.chunk_index)
            return (5, chunk.chunk_index)

        return sorted(chunks, key=priority)

    @staticmethod
    def _person_area_query_person(query: str) -> str | None:
        """Extract a likely person/entity name without intent-keyword gates."""

        entities = extract_entities_from_text(query)
        for entity in entities:
            cleaned = entity.strip(" ?!.,;:")
            if len(cleaned.split()) >= 2:
                return cleaned
        return None

    @staticmethod
    def _query_requires_direct_entity_evidence(
        *,
        query: str,
        context_chunks: list[ContextChunk],
    ) -> bool:
        entities = RagAnswerService._query_named_entities(query)
        if not entities:
            return False
        return not RagAnswerService._context_contains_named_entity(
            entities=entities,
            context_chunks=context_chunks,
        )

    @staticmethod
    def _query_named_entities(query: str) -> list[str]:
        entities: list[str] = []
        for entity in extract_entities_from_text(query):
            cleaned = " ".join(entity.strip(" ?!.,;:").split())
            if len(cleaned.split()) >= 2:
                entities.append(cleaned)
        return RagAnswerService._dedupe_text_values(entities, limit=8)

    @staticmethod
    def _context_contains_named_entity(
        *,
        entities: list[str],
        context_chunks: list[ContextChunk],
    ) -> bool:
        entity_keys = [normalize_metadata_value(entity) for entity in entities]
        entity_keys = [key for key in entity_keys if key]
        if not entity_keys:
            return False

        for context_chunk in context_chunks:
            chunk = context_chunk.chunk
            metadata = chunk.chunk_metadata or {}
            metadata_text = " ".join(RagAnswerService._stringify_metadata_value(value) for value in metadata.values())
            haystack = normalize_metadata_value(f"{chunk.content or ''} {metadata_text}")
            if any(key in haystack for key in entity_keys):
                return True
        return False

    @staticmethod
    def _artifacts_contain_named_entity(
        *,
        query: str,
        selected_artifacts: list[KnowledgeArtifact],
    ) -> bool:
        entities = RagAnswerService._query_named_entities(query)
        if not entities or not selected_artifacts:
            return False
        entity_keys = [normalize_metadata_value(entity) for entity in entities]
        haystack = RagAnswerService._artifact_haystack(selected_artifacts)
        return any(key and key in haystack for key in entity_keys)

    @staticmethod
    def _artifacts_are_topically_relevant(
        *,
        query: str,
        selected_artifacts: list[KnowledgeArtifact],
    ) -> bool:
        if not selected_artifacts:
            return False
        haystack = RagAnswerService._artifact_haystack(selected_artifacts)
        if not haystack:
            return False
        phrases = RagAnswerService._topical_query_phrases(query)
        if any(phrase in haystack for phrase in phrases):
            return True
        terms = RagAnswerService._topical_query_terms(query)
        if not terms:
            return True
        haystack_tokens = set(re.findall(r"[a-z0-9]+", haystack))
        matched = {term for term in terms if term in haystack_tokens}
        required = 1 if len(terms) <= 2 else 2
        return len(matched) >= required

    @staticmethod
    def _artifact_haystack(selected_artifacts: list[KnowledgeArtifact]) -> str:
        text = " ".join(
            " ".join(
                [
                    artifact.title or "",
                    artifact.canonical_text or "",
                    RagAnswerService._stringify_metadata_value(artifact.structured_data or {}),
                    RagAnswerService._stringify_metadata_value(artifact.normalized_identifiers or {}),
                ]
            )
            for artifact in selected_artifacts
        )
        return normalize_metadata_value(RagAnswerService._strip_vietnamese_accents(text))

    @staticmethod
    def _context_is_topically_relevant(
        *,
        query: str,
        context_chunks: list[ContextChunk],
    ) -> bool:
        if not context_chunks:
            return False

        haystack = normalize_metadata_value(
            RagAnswerService._strip_vietnamese_accents(
                " ".join(
                    f"{context_chunk.chunk.content or ''} "
                    f"{RagAnswerService._stringify_metadata_value(context_chunk.chunk.chunk_metadata or {})}"
                    for context_chunk in context_chunks
                )
            )
        )
        if not haystack:
            return False

        phrases = RagAnswerService._topical_query_phrases(query)
        if any(phrase in haystack for phrase in phrases):
            return True

        terms = RagAnswerService._topical_query_terms(query)
        if not terms:
            return True

        haystack_tokens = set(re.findall(r"[a-z0-9]+", haystack))
        matched = {term for term in terms if term in haystack_tokens}
        if len(phrases) >= 3 and len(terms) >= 5:
            return len(matched) >= max(6, int(len(terms) * 0.6))
        required = 1 if len(terms) <= 2 else 2
        return len(matched) >= required

    @staticmethod
    def _topical_query_terms(query: str) -> list[str]:
        normalized = normalize_metadata_value(RagAnswerService._strip_vietnamese_accents(query or ""))
        stopwords = {
            "about",
            "bao",
            "can",
            "cai",
            "cho",
            "co",
            "con",
            "cua",
            "duoc",
            "evn",
            "evncpc",
            "hoi",
            "how",
            "khi",
            "la",
            "may",
            "nao",
            "ngay",
            "nay",
            "nhieu",
            "sao",
            "so",
            "tap",
            "the",
            "theo",
            "thi",
            "ve",
            "viec",
            "what",
        }
        terms: list[str] = []
        for token in re.findall(r"[a-z0-9]+", normalized):
            if len(token) <= 1 or token.isdigit() or token in stopwords:
                continue
            terms.append(token)
        return RagAnswerService._dedupe_text_values(terms, limit=16)

    @staticmethod
    def _topical_query_phrases(query: str) -> list[str]:
        normalized = normalize_metadata_value(RagAnswerService._strip_vietnamese_accents(query or ""))
        stopwords = {
            "bao",
            "cho",
            "co",
            "cua",
            "duoc",
            "evn",
            "evncpc",
            "hoi",
            "khi",
            "la",
            "may",
            "nao",
            "ngay",
            "nhieu",
            "so",
            "theo",
            "ve",
        }
        tokens = [
            token
            for token in re.findall(r"[a-z0-9]+", normalized)
            if len(token) > 1 and not token.isdigit() and token not in stopwords
        ]
        phrases: list[str] = []
        for size in range(min(5, len(tokens)), 1, -1):
            for index in range(0, len(tokens) - size + 1):
                phrase = " ".join(tokens[index : index + size])
                if len(phrase) >= 6:
                    phrases.append(phrase)
        return RagAnswerService._dedupe_text_values(phrases, limit=24)

    @staticmethod
    def _stringify_metadata_value(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, dict):
            return " ".join(RagAnswerService._stringify_metadata_value(item) for item in value.values())
        if isinstance(value, (list, tuple, set)):
            return " ".join(RagAnswerService._stringify_metadata_value(item) for item in value)
        return str(value)

    @staticmethod
    def _looks_vietnamese_query(query: str) -> bool:
        if re.search(r"[ÄƒÃ¢Ä‘ÃªÃ´Æ¡Æ°Ä‚Ã‚ÄÃŠÃ”Æ Æ¯]", query or ""):
            return True
        normalized = normalize_metadata_value(RagAnswerService._strip_vietnamese_accents(query or ""))
        return bool(re.search(r"\b(la|co|khong|nhung|cua|trong|theo)\b", normalized))

    @staticmethod
    def _looks_identifier_only_query(query: str) -> bool:
        clean = " ".join(str(query or "").split()).strip()
        return bool(clean and re.fullmatch(r"[A-Za-z0-9Ã„ÂÃ„â€˜/._+\-]+", clean))

    @staticmethod
    def _missing_accessible_context_answer(query: str) -> str:
        return "Kh\u00f4ng t\u00ecm th\u1ea5y th\u00f4ng tin ph\u00f9 h\u1ee3p trong c\u00e1c t\u00e0i li\u1ec7u b\u1ea1n c\u00f3 quy\u1ec1n truy c\u1eadp."

    @staticmethod
    def _insufficient_direct_evidence_answer(query: str) -> str:
        if (
            RagAnswerService._looks_vietnamese_query(query)
            or RagAnswerService._looks_identifier_only_query(query)
            or any(ord(char) > 127 for char in query or "")
        ):
            return "Kh\u00f4ng t\u00ecm th\u1ea5y th\u00f4ng tin ph\u00f9 h\u1ee3p trong c\u00e1c t\u00e0i li\u1ec7u b\u1ea1n c\u00f3 quy\u1ec1n truy c\u1eadp."
        return "I could not find that information in the available documents."

    @staticmethod
    def _query_terms(query: str) -> list[str]:
        terms: list[str] = []
        normalized_query = " ".join(query.split()).strip(" ?!.,;:")
        if normalized_query:
            terms.append(normalized_query)

        terms.extend(extract_entities_from_text(query))
        terms.extend(RagAnswerService._query_keyphrases(query))

        ordered: list[str] = []
        seen: set[str] = set()
        for term in terms:
            clean = " ".join(str(term or "").split()).strip(" ?!.,;:")
            if not clean:
                continue
            key = normalize_metadata_value(RagAnswerService._strip_vietnamese_accents(clean))
            if key in seen:
                continue
            seen.add(key)
            ordered.append(clean)
        return ordered

    @staticmethod
    def _query_keyphrases(query: str) -> list[str]:
        """Extract lexical retrieval hints without stopword/intent lists."""

        words = [word.strip(" ,.;:()[]{}<>!?\"'`") for word in query.split()]
        words = [word for word in words if word]
        if not words:
            return []

        phrases: list[str] = []

        for word in words:
            if re.fullmatch(r"[A-Z0-9][A-Z0-9._/-]{1,}", word):
                phrases.append(word)

        normalized_tokens = [token for token in (normalize_metadata_value(RagAnswerService._strip_vietnamese_accents(word)) for word in words) if len(token) >= 2]

        max_n = min(7, len(normalized_tokens))
        for size in range(max_n, 1, -1):
            for start in range(0, len(normalized_tokens) - size + 1):
                phrase = " ".join(normalized_tokens[start : start + size])
                if 4 <= len(phrase) <= 100:
                    phrases.append(phrase)
                if len(phrases) >= 24:
                    return RagAnswerService._dedupe_text_values(phrases, limit=24)

        phrases.extend(normalized_tokens)
        return RagAnswerService._dedupe_text_values(phrases, limit=24)

    @staticmethod
    def _dedupe_text_values(values: list[str], *, limit: int) -> list[str]:
        ordered: list[str] = []
        seen: set[str] = set()
        for value in values:
            clean = " ".join(str(value or "").split()).strip(" ?!.,;:")
            if not clean:
                continue
            key = normalize_metadata_value(RagAnswerService._strip_vietnamese_accents(clean))
            if key in seen:
                continue
            seen.add(key)
            ordered.append(clean)
            if len(ordered) >= limit:
                break
        return ordered

    @staticmethod
    def _is_high_signal_context_chunk(
        *,
        chunk: Chunk,
        query_terms: list[str],
    ) -> bool:
        if not query_terms:
            return False
        content = normalize_metadata_value(RagAnswerService._strip_vietnamese_accents(chunk.content or ""))
        if not content:
            return False

        matched = 0
        for term in query_terms:
            term_key = normalize_metadata_value(RagAnswerService._strip_vietnamese_accents(term))
            if not term_key or len(term_key) < 2:
                continue
            if term_key in content:
                matched += 1
            if matched >= 2:
                return True

        metadata = chunk.chunk_metadata or {}
        metadata_text = " ".join(
            str(value)
            for key in (
                "unit",
                "section_id",
                "section_title",
                "section_path",
                "article_title",
                "title",
                "document_context",
            )
            if (value := metadata.get(key)) is not None
        )
        metadata_key = normalize_metadata_value(RagAnswerService._strip_vietnamese_accents(metadata_text))
        return any(normalize_metadata_value(RagAnswerService._strip_vietnamese_accents(term)) and normalize_metadata_value(RagAnswerService._strip_vietnamese_accents(term)) in metadata_key for term in query_terms)

    @staticmethod
    def _table_references(metadata: dict[str, Any]) -> list[str]:
        refs: list[str] = []
        table_id = metadata.get("table_id")
        if table_id is not None:
            refs.append(str(table_id))

        table_ids = metadata.get("table_ids")
        if isinstance(table_ids, list):
            refs.extend(str(item) for item in table_ids if item is not None)

        ordered: list[str] = []
        seen: set[str] = set()
        for ref in refs:
            key = ref.strip()
            if not key or key in seen:
                continue
            seen.add(key)
            ordered.append(key)
        return ordered

    @staticmethod
    def _prioritize_table_neighbors(
        *,
        neighbors: list[Chunk],
        query_terms: list[str],
        include_full_table: bool = False,
    ) -> list[Chunk]:
        if include_full_table:
            return RagAnswerService._ordered_table_context_chunks(neighbors)

        if not query_terms:
            return neighbors

        high_priority: list[Chunk] = []
        supporting: list[Chunk] = []
        for neighbor in neighbors:
            metadata = neighbor.chunk_metadata or {}
            chunk_type = str(metadata.get("chunk_type") or "")
            content = neighbor.content.casefold()
            if chunk_type in {"table_title", "table_header"}:
                supporting.append(neighbor)
                continue
            if any(term.casefold() in content for term in query_terms):
                high_priority.append(neighbor)

        ordered: list[Chunk] = []
        seen_ids: set[UUID] = set()
        for chunk in [*supporting, *high_priority]:
            if chunk.id in seen_ids:
                continue
            seen_ids.add(chunk.id)
            ordered.append(chunk)
        return ordered or neighbors

    @staticmethod
    def _ordered_table_context_chunks(chunks: list[Chunk]) -> list[Chunk]:
        title_chunks: list[Chunk] = []
        header_chunks: list[Chunk] = []
        row_chunks: list[Chunk] = []
        block_chunks: list[Chunk] = []
        other_chunks: list[Chunk] = []

        for chunk in chunks:
            metadata = chunk.chunk_metadata or {}
            chunk_type = str(metadata.get("chunk_type") or "")
            if chunk_type == "table_title":
                title_chunks.append(chunk)
            elif chunk_type == "table_header":
                header_chunks.append(chunk)
            elif chunk_type == "table_row":
                row_chunks.append(chunk)
            elif chunk_type == "table_block":
                block_chunks.append(chunk)
            elif chunk_type == "entity_summary":
                continue
            else:
                other_chunks.append(chunk)

        data_chunks = row_chunks if row_chunks else block_chunks
        return [*title_chunks, *header_chunks, *data_chunks, *other_chunks]

    @staticmethod
    def _prioritize_entity_coverage_chunks(
        *,
        chunks: list[Chunk],
        query_terms: list[str],
        prefer_structural_schema: bool = False,
    ) -> list[Chunk]:
        matches: list[Chunk] = []
        supporting: list[Chunk] = []
        for chunk in chunks:
            metadata = chunk.chunk_metadata or {}
            chunk_type = str(metadata.get("chunk_type") or "")
            content = normalize_metadata_value(
                RagAnswerService._strip_vietnamese_accents(chunk.content or "")
            )
            if any(
                normalize_metadata_value(RagAnswerService._strip_vietnamese_accents(term))
                and normalize_metadata_value(RagAnswerService._strip_vietnamese_accents(term)) in content
                for term in query_terms
            ):
                matches.append(chunk)
            elif chunk_type in {
                "document_summary",
                "heading_outline",
                "section_summary",
                "table_summary",
                "entity_catalog",
                "entity_summary",
                "table_block",
            }:
                supporting.append(chunk)

        ordered: list[Chunk] = []
        seen_ids: set[UUID] = set()
        if prefer_structural_schema:
            matches.sort(key=RagAnswerService._schema_coverage_priority)
            supporting.sort(key=RagAnswerService._schema_coverage_priority)
        for chunk in [*matches, *supporting]:
            if chunk.id in seen_ids:
                continue
            seen_ids.add(chunk.id)
            ordered.append(chunk)
        return ordered or chunks

    @staticmethod
    def _schema_coverage_priority(chunk: Chunk) -> tuple[int, int]:
        metadata = chunk.chunk_metadata or {}
        chunk_type = str(metadata.get("chunk_type") or "")
        configured_priority = RagAnswerService._optional_int(
            metadata.get("schema_coverage_priority")
        )
        if configured_priority is not None:
            priority = configured_priority
        elif RagAnswerService._is_schema_overview_metadata(metadata):
            priority = 0
        elif chunk_type == "relationship_definition" or metadata.get("relationship_name"):
            priority = 1
        elif chunk_type in {"attribute_table_schema", "gis_relationship_schema"}:
            priority = 2
        elif chunk_type in {"schema_object_summary", "document_summary", "heading_outline", "section_summary"}:
            priority = 3
        elif chunk_type in {"table_parent", "table_complete", "table_rows"}:
            priority = 7
        elif chunk_type in {"schema_field_row", "table_row", "legal_table_row", "structured_fact_row"}:
            priority = 9
        else:
            priority = 8
        return (priority, int(getattr(chunk, "chunk_index", 0) or 0))

    @staticmethod
    def _is_schema_overview_metadata(metadata: dict[str, Any]) -> bool:
        roles = RagAnswerService._metadata_roles(metadata)
        return bool(
            metadata.get("schema_overview")
            or "structural_schema_overview" in roles
        )

    @staticmethod
    def _metadata_roles(metadata: dict[str, Any]) -> set[str]:
        raw_roles = metadata.get("retrieval_roles")
        if isinstance(raw_roles, str):
            values = [raw_roles]
        elif isinstance(raw_roles, (list, tuple, set)):
            values = [str(value) for value in raw_roles]
        else:
            values = []
        return {value.strip() for value in values if value.strip()}

    @staticmethod
    def _optional_int(value: Any) -> int | None:
        if value in (None, ""):
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _filter_identifier_context(
        *,
        query: str,
        context_chunks: list[ContextChunk],
    ) -> list[ContextChunk]:
        """For code-only lookups, keep only chunks that literally contain the code.

        This prevents short identifier lookups from carrying unrelated topical
        neighbor chunks into the LLM prompt.
        """

        if not is_identifier_lookup_query(query):
            return context_chunks

        normalized_query = " ".join((query or "").split()).strip(" ?!.,;:").casefold()
        if not normalized_query:
            return context_chunks

        exact_chunks = [context_chunk for context_chunk in context_chunks if normalized_query in (context_chunk.chunk.content or "").casefold()]
        if not exact_chunks:
            return context_chunks
        return [replace(context_chunk, citation_index=index) for index, context_chunk in enumerate(exact_chunks, start=1)]

    def _deduplicate_context_chunks(
        self,
        context_chunks: list[ContextChunk],
    ) -> list[ContextChunk]:
        """Remove duplicate chunks and reassign sequential citation indexes.

        Duplicates are detected by chunk id, then by normalized content, then by
        (document_id, article_number, normalized content). Because primary
        (higher-ranked) chunks precede neighbors, keeping the first occurrence
        preserves the highest-priority chunk.
        """
        seen_ids: set[UUID] = set()
        seen_content: set[str] = set()
        seen_article_lines: set[tuple[str, str, str]] = set()
        deduplicated: list[ContextChunk] = []

        for context_chunk in context_chunks:
            chunk = context_chunk.chunk
            if chunk.id in seen_ids:
                continue

            normalized = self._normalize_text(chunk.content)
            if normalized and normalized in seen_content:
                continue

            metadata = chunk.chunk_metadata or {}
            article_number = metadata.get("article_number")
            article_key: tuple[str, str, str] | None = None
            if article_number is not None and normalized:
                article_key = (str(chunk.document_id), str(article_number), normalized)
                if article_key in seen_article_lines:
                    continue

            seen_ids.add(chunk.id)
            if normalized:
                seen_content.add(normalized)
            if article_key is not None:
                seen_article_lines.add(article_key)
            deduplicated.append(context_chunk)

        return [replace(context_chunk, citation_index=index) for index, context_chunk in enumerate(deduplicated, start=1)]

    @staticmethod
    def _normalize_text(text: str | None) -> str:
        return " ".join((text or "").split()).lower()

    @staticmethod
    def _query_with_short_term_context(
        *,
        query: str,
        session_context: RagSessionContext | None = None,
    ) -> str:
        """Build a retrieval-only query with optional chatbot short-term hints.

        HBRag does not persist user memory here. The supplied context is only a
        retrieval hint for follow-up questions, and the original user query
        remains the primary signal.
        """

        if session_context is None:
            return query

        hints: list[str] = []
        for label, value in (
            ("Chá»§ Ä‘á» trÆ°á»›c", session_context.last_topic),
            ("Pháº¡m vi hiá»‡n táº¡i", session_context.current_scope),
            ("Pháº¡m vi ngÆ°á»i dÃ¹ng", session_context.user_scope),
        ):
            text = " ".join(str(value or "").split())
            if text:
                hints.append(f"{label}: {text[:500]}")

        recent_messages = list(session_context.recent_messages or [])[-4:]
        for message in recent_messages:
            content = " ".join(str(getattr(message, "content", "") or "").split())
            if not content:
                continue
            role = str(getattr(message, "role", "message") or "message")
            hints.append(f"{role}: {content[:500]}")

        if not hints:
            return query

        return f"{query}\n\nNgá»¯ cáº£nh há»™i thoáº¡i ngáº¯n háº¡n do chatbot cung cáº¥p Ä‘á»ƒ há»— trá»£ truy xuáº¥t, khÃ´ng pháº£i nguá»“n trÃ­ch dáº«n:\n" + "\n".join(f"- {hint}" for hint in hints[:8])

    @staticmethod
    def _short_term_context_section(
        *,
        session_context: RagSessionContext | None = None,
    ) -> str | None:
        """Render chatbot-supplied short-term context for prompting.

        This context can help interpret follow-up questions, but provided
        document passages remain the only citable source of truth.
        """

        if session_context is None:
            return None

        lines: list[str] = []
        if session_context.last_topic:
            lines.append(f"- Last topic: {session_context.last_topic}")
        if session_context.current_scope:
            lines.append(f"- Current scope: {session_context.current_scope}")
        if session_context.user_scope:
            lines.append(f"- User scope: {session_context.user_scope}")

        for message in list(session_context.recent_messages or [])[-4:]:
            content = " ".join(str(getattr(message, "content", "") or "").split())
            if not content:
                continue
            role = str(getattr(message, "role", "message") or "message")
            lines.append(f"- {role}: {content[:700]}")

        if not lines:
            return None

        return "Conversation memory (internal; use only to understand references, do not cite it; document text wins if there is any conflict):\n" + "\n".join(lines[:10])

    @staticmethod
    def _identifier_values_from_context(context_chunks: list[ContextChunk]) -> list[str]:
        """Collect exact identifier strings from document passages for prompt constraints.

        Keep this helper defensive because older chunks may not have the new metadata
        fields yet. Values are used only to tell the LLM what strings must be copied
        exactly, not to create new facts.
        """

        values: list[str] = []
        seen: set[str] = set()

        def add(value: object) -> None:
            text = str(value or "").strip()
            if not text:
                return
            if len(text) > 80:
                return
            key = text.casefold()
            if key in seen:
                return
            seen.add(key)
            values.append(text)

        for context_chunk in context_chunks:
            chunk = context_chunk.chunk
            metadata = chunk.chunk_metadata or {}
            for field_name in (
                "doc_codes",
                "identifiers",
                "dates",
                "document_codes",
                "document_numbers",
            ):
                raw_value = metadata.get(field_name)
                if isinstance(raw_value, (list, tuple, set)):
                    for item in raw_value:
                        add(item)
                else:
                    add(raw_value)

            content = chunk.content or ""
            for match in re.findall(r"\b\d{2,6}/[A-ZÃ€-á»¸0-9Ä\-]+\b", content):
                add(match)
            for match in re.findall(r"\b\d{1,2}/\d{1,2}/\d{4}\b", content):
                add(match)

        return values[:20]

    @staticmethod
    def _query_strategy_section(query_strategy: QueryStrategy) -> str | None:
        if not query_strategy.strategies:
            return None

        strategy_names = ", ".join(query_strategy.strategies)
        lines = [
            "Reading notes (internal; not a cited source):",
            f"- Strategies: {strategy_names}.",
        ]
        if query_strategy.requires_overview_context:
            lines.extend(
                [
                    "- This question may require document, section, heading, table, or summary-level evidence, not only the first matching passage.",
                    "- Group the document text by heading, section, table, object type, or row type when those boundaries are visible.",
                    "- Prefer coverage across relevant sections over repeating many passages from one section.",
                ]
            )
        if "table_detail" in query_strategy.strategies:
            lines.append(
                "- If table evidence is present, use table title/header/summary with the row or field evidence."
            )
        if query_strategy.may_need_second_retrieval:
            lines.append(
                "- Before finalizing, check whether the document text omits a visible related group or count; if it does, state the missing information instead of guessing."
            )
        return "\n".join(lines)

    @staticmethod
    def _prefer_structural_overview_context_chunks(
        context_chunks: list[ContextChunk],
    ) -> list[ContextChunk]:
        has_structural_context = any(
            RagAnswerService._is_structural_overview_context_chunk(context_chunk)
            for context_chunk in context_chunks
        )
        if not has_structural_context:
            return context_chunks

        filtered = [
            context_chunk
            for context_chunk in context_chunks
            if not RagAnswerService._is_field_level_schema_context_chunk(context_chunk)
        ]
        result = filtered or context_chunks
        return sorted(
            result,
            key=lambda context_chunk: RagAnswerService._schema_coverage_priority(
                context_chunk.chunk
            ),
        )

    @staticmethod
    def _is_structural_overview_context_chunk(context_chunk: ContextChunk) -> bool:
        metadata = getattr(context_chunk.chunk, "chunk_metadata", None) or {}
        chunk_type = str(metadata.get("chunk_type") or "")
        return RagAnswerService._is_schema_overview_metadata(metadata) or chunk_type in {
            "attribute_table_schema",
            "gis_relationship_schema",
            "relationship_definition",
            "schema_object_summary",
            "document_summary",
            "heading_outline",
            "section_summary",
            "table_summary",
            "docling_section",
            "docling_hybrid_repaired",
        }

    @staticmethod
    def _is_field_level_schema_context_chunk(context_chunk: ContextChunk) -> bool:
        metadata = getattr(context_chunk.chunk, "chunk_metadata", None) or {}
        chunk_type = str(metadata.get("chunk_type") or "")
        if RagAnswerService._is_schema_overview_metadata(metadata):
            return False
        if metadata.get("field_level_schema"):
            return True
        if "field_level_schema" in RagAnswerService._metadata_roles(metadata):
            return True
        if chunk_type == "schema_field_row":
            return True
        if metadata.get("field_name"):
            return True
        return bool(
            metadata.get("field_names")
            and chunk_type in {"table_parent", "table_complete", "table_rows"}
            and not metadata.get("relationship_name")
            and not metadata.get("target_table")
        )

    @staticmethod
    def _is_field_detail_schema_query(
        query: str,
        query_intent_rules: dict[str, Any] | None = None,
    ) -> bool:
        return is_field_detail_schema_query(query, query_intent_rules)

    @staticmethod
    def _query_contract_section(query_contract: QueryContract) -> str:
        return (
            "Answer planning notes (internal; never mention this section or its labels):\n"
            f"- User intent hint: {query_contract.detected_intent}\n"
            f"- Relevant text scopes: {', '.join(query_contract.target_contexts)}\n"
            f"- Preferred compiled note types: {', '.join(query_contract.preferred_artifact_types)}\n"
            f"- Expected answer shape: {query_contract.output_shape}\n"
            f"- Source support style: {query_contract.citation_requirement}\n"
            f"- May use direct document passages when compiled notes are incomplete: {query_contract.allow_chunk_fallback}"
        )

    @staticmethod
    def _knowledge_artifact_context_section(
        *,
        selected_artifacts: list[KnowledgeArtifact],
        context_chunks: list[ContextChunk],
    ) -> str | None:
        if not selected_artifacts:
            return None
        citation_indexes_by_chunk_id = {
            str(context_chunk.chunk.id): context_chunk.citation_index
            for context_chunk in context_chunks
        }
        lines: list[str] = [
            "Compiled Document Notes (internal; use for facts only, never mention this label):",
            "Use these compiled notes before direct document passages when they contain the requested field.",
        ]
        for index, artifact in enumerate(selected_artifacts, start=1):
            source_indexes = [
                citation_indexes_by_chunk_id[str(chunk_id)]
                for chunk_id in artifact.source_chunk_ids or []
                if str(chunk_id) in citation_indexes_by_chunk_id
            ]
            source_marker = ", ".join(f"[{item}]" for item in source_indexes) or "no source passage loaded"
            structured = json.dumps(
                artifact.structured_data or {},
                ensure_ascii=False,
                sort_keys=True,
            )
            identifiers = json.dumps(
                artifact.normalized_identifiers or {},
                ensure_ascii=False,
                sort_keys=True,
            )
            lines.append(
                f"[KA{index}] type={artifact.artifact_type}; context={artifact.context_type}; "
                f"confidence={float(artifact.confidence_score or 0.0):.2f}; source_passages={source_marker}\n"
                f"title: {artifact.title or ''}\n"
                f"canonical_text: {artifact.canonical_text}\n"
                f"structured_data: {structured}\n"
                f"normalized_identifiers: {identifiers}"
            )
        return "\n".join(lines)

    @staticmethod
    def _build_user_prompt(
        *,
        query: str,
        standalone_query: str | None = None,
        context_chunks: list[ContextChunk],
        query_strategy: QueryStrategy | None = None,
        memory_context: list[MemoryResult] | None = None,
        session_summary: str | None = None,
        session_context: RagSessionContext | None = None,
        query_intent_rules: dict[str, Any] | None = None,
        selected_artifacts: list[KnowledgeArtifact] | None = None,
        query_contract: QueryContract | None = None,
    ) -> str:
        sections: list[str] = []
        retrieval_query = " ".join((standalone_query or query or "").split())
        has_standalone_query = bool(retrieval_query and normalize_metadata_value(retrieval_query) != normalize_metadata_value(query or ""))
        evidence_query = retrieval_query or query

        language_instruction = RagAnswerService._answer_language_instruction(query)
        if language_instruction:
            sections.append(language_instruction)

        if has_standalone_query:
            sections.append(f"Conversation reference wording (internal; answer the user's original question, and do not cite this as evidence):\n{retrieval_query}")

        query_strategy = query_strategy or classify_query_strategy(evidence_query)
        strategy_section = RagAnswerService._query_strategy_section(query_strategy)
        if strategy_section:
            sections.append(strategy_section)

        if query_contract is not None:
            sections.append(RagAnswerService._query_contract_section(query_contract))

        artifact_context_section = RagAnswerService._knowledge_artifact_context_section(
            selected_artifacts=selected_artifacts or [],
            context_chunks=context_chunks,
        )
        if artifact_context_section:
            sections.append(artifact_context_section)

        session_context_section = RagAnswerService._short_term_context_section(session_context=session_context)
        if session_context_section:
            sections.append(session_context_section)

        if memory_context:
            memory_lines = "\n".join(f"- ({memory.memory_type}) {memory.content}" for memory in memory_context)
            sections.append(f"User Memory:\n{memory_lines}")

        if session_summary:
            sections.append(f"Session Summary:\n{session_summary}")

        query_terms = RagAnswerService._query_terms(evidence_query)
        identifier_lookup = is_identifier_lookup_query(evidence_query)
        if identifier_lookup:
            exact_chunks = [
                context_chunk
                for context_chunk in context_chunks
                if str((context_chunk.chunk.chunk_metadata or {}).get("identifier_exact_boost") or "0") != "0" or evidence_query.strip().casefold() in (context_chunk.chunk.content or "").casefold()
            ]
            if exact_chunks:
                context_chunks = exact_chunks
        if identifier_lookup:
            exact_values = RagAnswerService._identifier_values_from_context(context_chunks)
            if exact_values:
                exact_value_lines = "\n".join(f"  - {value}" for value in exact_values)

                sections.append(
                    "Exact identifier evidence policy:\n"
                    f"- The user is asking about this exact identifier/code: {evidence_query.strip()}\n"
                    "- Use the document passages that contain the exact identifier.\n"
                    "- Preserve these exact identifier/document-code strings without rewriting them:\n"
                    f"{exact_value_lines}\n"
                    "- Extract any directly attached fields, dates, parties, titles, links, "
                    "lists, notes, status text, responsibilities, or mechanisms from the same "
                    "evidence. Do not stop at the first sentence if additional attached details "
                    "answer the question.\n"
                    "- Do not add related records or inferred legal/business effect unless the "
                    "document text explicitly states it.\n"
                )

        include_all_table_rows = RagAnswerService._is_table_enumeration_query(query)
        matched_rows, table_support = RagAnswerService._table_context_sections(
            context_chunks=context_chunks,
            query_terms=query_terms,
            include_all_table_rows=include_all_table_rows,
        )

        if matched_rows:
            sections.append("ENTITY_MATCHED_ROWS:\n" + "\n".join(matched_rows))
            if table_support:
                sections.append("TABLE_SUPPORT:\n" + "\n".join(table_support))

        relationship_evidence = RagAnswerService._structured_relationship_evidence_section(
            context_chunks=context_chunks,
        )
        if relationship_evidence:
            sections.append(relationship_evidence)

        if RagAnswerService._is_schema_count_query(evidence_query):
            schema_overview_evidence = RagAnswerService._schema_overview_evidence_section(
                context_chunks=context_chunks,
            )
            if schema_overview_evidence:
                sections.append(schema_overview_evidence)

        count_evidence = RagAnswerService._count_evidence_section(
            query=query,
            context_chunks=context_chunks,
        )
        if count_evidence:
            sections.append(count_evidence)

        prompt_context_chunks = context_chunks
        if query_strategy.requires_overview_context and not RagAnswerService._is_field_detail_schema_query(
            evidence_query,
            query_intent_rules,
        ):
            prompt_context_chunks = RagAnswerService._prefer_structural_overview_context_chunks(
                context_chunks
            )

        document_context_chunks = prompt_context_chunks
        if matched_rows:
            document_context_chunks = [
                context_chunk
                for context_chunk in prompt_context_chunks
                if not RagAnswerService._is_structured_result_context_chunk(
                    context_chunk,
                    query_terms=query_terms,
                    include_all_table_rows=include_all_table_rows,
                )
            ]

        context = "\n".join(f"[{context_chunk.citation_index}] {context_chunk.chunk.content}" for context_chunk in document_context_chunks)
        if context:
            sections.append(
                "Document Text:\n"
                f"{context}\n\n"
                "Document use rule: ENTITY_MATCHED_ROWS are structured candidate rows. "
                "Document Text is the broader evidence set and may contain "
                "the directly relevant narrative section, objective, definition, "
                "condition, or explanation. Choose the evidence that directly answers "
                "the question; do not ignore narrative text just because table rows "
                "are present. If the directly relevant narrative passage contains a "
                "summary followed by list items in the same section, preserve those "
                "items as focused bullets instead of collapsing the answer to one "
                "short sentence."
            )

        sections.append(
            "Dynamic answer requirements:\n"
            "- Follow the Language constraint above; otherwise answer in the same language as the user's question unless the question asks otherwise.\n"
            "- Infer the question type from the wording and the document text; do not rely on fixed document names, people, organizations, or domain-specific templates.\n"
            "- When Compiled Document Notes are present, treat structured_data and canonical_text as primary document facts. Use direct document passages only to verify or fill fields missing from the notes.\n"
            "- If a required field is absent from both compiled notes and direct document passages, say naturally that the current documents do not show that information instead of guessing.\n"
            "- If Conversation reference wording is present, use it only to resolve references in the original question; do not treat it as a cited source.\n"
            "- Never mention how information was searched, ranked, grouped, or prepared; do not mention any prompt section labels in the final answer.\n"
            "- Write for the user: focus only on what the document says and keep missing-information messages natural.\n"
            "- Start with the direct answer. For count questions, state the count first. For yes/no questions, state the decision first. For list questions, list the matching records.\n"
            "- When COUNT_EVIDENCE exists, use it to choose the count whose nearby noun "
            "phrase, label, heading, or row field best matches the entity being counted "
            "in the question. If several counts could match, state the ambiguity and "
            "list the competing counts instead of choosing a broader total.\n"
            "- For count questions about attributes, tables, columns, fields, layers, or "
            "objects, do not substitute a broader category total when COUNT_EVIDENCE "
            "contains a candidate that explicitly matches the narrower counted entity.\n"
            "- When the evidence distinguishes different groups, tables, layers, sections, "
            "object types, phases, or relationship types, keep those categories separate. "
            "Do not merge partial, priority, phase, or subtype counts into one answer "
            "unless the same evidence explicitly labels that sum as the requested count.\n"
            "- When SCHEMA_OVERVIEW_EVIDENCE exists, cover each directly evidenced "
            "structural group it lists, such as attribute/data tables, GIS layers or "
            "objects, and relationships. Do not invent names for remaining groups that "
            "the evidence does not list.\n"
            "- For overview, structure, count, or list questions, answer at the "
            "category, section, table, layer, object, or relationship level. Do not "
            "switch to field-level schemas unless the user explicitly asks for "
            "fields, columns, or attributes of one specific item.\n"
            "- If the answer mentions a counted group and the evidence lists explicit "
            "items for that group, include those item names or relationship endpoints "
            "instead of stopping at the count.\n"
            "- Prefer the smallest evidence span that directly answers the question, then add only details that explain that answer.\n"
            "- Do not expand into field-level schemas, unrelated rows, or long background details unless the question asks for those details.\n"
            "- For table-like evidence, use the row fields and original labels shown in ENTITY_MATCHED_ROWS or Document Text; do not assume fixed column names.\n"
            "- For narrative evidence, preserve the relevant section heading and bullet structure when it helps answer the question.\n"
            "- If the document text is insufficient or conflicting, say that clearly instead of guessing.\n"
            "- Do not create a Sources, References, Documents, or source-list section at the end.\n"
            "- Keep the answer grounded in the numbered document passages. The application renders document sources separately; do not interrupt sentences with citation-only clutter."
        )
        sections.append(f"Question:\n{query}")
        return "\n\n".join(sections)

    @staticmethod
    def _answer_language_instruction(query: str) -> str:
        if RagAnswerService._looks_vietnamese_query(query) or RagAnswerService._looks_identifier_only_query(query):
            return (
                "Language constraint:\n"
                "- Answer only in Vietnamese. Numeric or code-only lookups in this application should still be answered in Vietnamese.\n"
                "- Preserve exact source names, identifiers, product names, URLs, and "
                "technical terms as written in the evidence.\n"
                "- Do not mix unrelated languages into the answer."
            )
        return (
            "Language constraint:\n"
            "- Answer in the same language as the user's question.\n"
            "- Preserve exact source names, identifiers, product names, URLs, and "
            "technical terms as written in the evidence.\n"
            "- Do not mix unrelated languages into the answer."
        )

    @staticmethod
    def _clean_llm_answer(answer: str) -> str:
        cleaned = str(answer or "")
        cleaned = re.sub(r"(?is)<think>.*?</think>", "", cleaned)
        cleaned = re.sub(r"(?is)<think>.*$", "", cleaned)
        cleaned = re.sub(r"(?is)^.*?</think>", "", cleaned)
        cleaned = re.sub(r"(?im)^\s*(?:supporting text|sources?|references?)\s*:\s*$", "", cleaned)
        internal_terms = (
            r"target_contexts|ENTITY_MATCHED_ROWS|TABLE_SUPPORT|retrieved\s+(?:context|chunks|evidence)|"
            r"retrieval|BM25|vector\s+search|context\s+window|chunk(?:s)?|"
            r"ngá»¯\s*cáº£nh|ngu\s*canh|Ä‘oáº¡n\s+trÃ­ch|doan\s+trich"
        )
        cleaned_lines: list[str] = []
        for line in cleaned.splitlines():
            is_note_line = re.match(r"^\s*(?:lÆ°u Ã½|luu y|ghi chÃº|ghi chu|note)\s*[:ï¼š-]", line, flags=re.IGNORECASE)
            has_internal_term = re.search(internal_terms, line, flags=re.IGNORECASE)
            has_prompt_label = re.search(r"\b(?:target_contexts|ENTITY_MATCHED_ROWS|TABLE_SUPPORT)\b", line)
            if (is_note_line and has_internal_term) or has_prompt_label:
                continue
            cleaned_lines.append(line)
        cleaned = "\n".join(cleaned_lines)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
        return cleaned.strip()

    @staticmethod
    def _count_evidence_section(
        *,
        query: str,
        context_chunks: list[ContextChunk],
    ) -> str | None:
        if not RagAnswerService._is_count_query(query):
            return None
        query_tokens = RagAnswerService._count_query_tokens(query)
        query_phrases = RagAnswerService._count_query_phrases(query)
        if not query_tokens:
            return None

        candidates: list[tuple[float, int, str, str]] = []
        seen: set[str] = set()
        for context_chunk in context_chunks:
            for line in RagAnswerService._count_candidate_lines(context_chunk.chunk.content or ""):
                normalized = normalize_metadata_value(RagAnswerService._strip_vietnamese_accents(line))
                numbers = re.findall(r"\b\d{1,4}\b", normalized)
                if not numbers:
                    continue
                tokens = set(re.findall(r"[a-z0-9]+", normalized))
                overlap = query_tokens & tokens
                if not overlap:
                    continue
                phrase_hits = sum(1 for phrase in query_phrases if phrase in normalized)
                score = len(overlap) + (2.5 * phrase_hits)
                key = normalize_metadata_value(line)
                if key in seen:
                    continue
                seen.add(key)
                candidates.append((score, context_chunk.citation_index, numbers[0], line))

        if not candidates:
            return None

        candidates.sort(key=lambda item: item[0], reverse=True)
        lines = [
            "COUNT_EVIDENCE:",
            "Use these candidate counts only as extracted evidence; still answer from the cited context.",
        ]
        for score, citation, number, text in candidates[:8]:
            lines.append(f"- score={score:.2f}; citation=[{citation}]; count={number}; text={text}")
        return "\n".join(lines)

    @staticmethod
    def _structured_relationship_evidence_section(
        *,
        context_chunks: list[ContextChunk],
    ) -> str | None:
        rows: list[str] = []
        seen: set[str] = set()
        for context_chunk in context_chunks:
            metadata = getattr(context_chunk.chunk, "chunk_metadata", None) or {}
            relationship_name = RagAnswerService._metadata_text(
                metadata.get("relationship_name")
                or getattr(context_chunk.chunk, "relationship_name", None)
            )
            source = RagAnswerService._metadata_text(
                metadata.get("source_layer")
                or metadata.get("source_table")
                or metadata.get("source_entity")
            )
            target = RagAnswerService._metadata_text(
                metadata.get("target_table")
                or metadata.get("target_layer")
                or metadata.get("target_entity")
            )
            cardinality = RagAnswerService._metadata_text(
                metadata.get("cardinality") or metadata.get("relationship_cardinality")
            )
            source_key = RagAnswerService._metadata_text(metadata.get("source_key"))
            target_key = RagAnswerService._metadata_text(metadata.get("target_key"))
            chunk_type = str(metadata.get("chunk_type") or "")

            if not any([relationship_name, source, target, cardinality]):
                continue
            if "relationship" not in chunk_type and not relationship_name:
                continue

            parts = [f"citation=[{context_chunk.citation_index}]"]
            if relationship_name:
                parts.append(f"name={relationship_name}")
            if source or target:
                endpoints = f"{source or '?'} -> {target or '?'}"
                parts.append(f"endpoints={endpoints}")
            if source_key or target_key:
                parts.append(f"keys={source_key or '?'} -> {target_key or '?'}")
            if cardinality:
                parts.append(f"cardinality={cardinality}")

            line = "- " + "; ".join(parts)
            key = normalize_metadata_value(line)
            if key in seen:
                continue
            seen.add(key)
            rows.append(line)
            if len(rows) >= 12:
                break

        if not rows:
            return None
        return (
            "STRUCTURED_RELATIONSHIP_EVIDENCE:\n"
            "Use these relationship records only when the cited context supports them.\n"
            + "\n".join(rows)
        )

    @staticmethod
    def _schema_overview_evidence_section(
        *,
        context_chunks: list[ContextChunk],
    ) -> str | None:
        rows: list[str] = []
        seen: set[str] = set()
        for context_chunk in sorted(
            context_chunks,
            key=lambda item: RagAnswerService._schema_coverage_priority(item.chunk),
        ):
            content = context_chunk.chunk.content or ""
            metadata = getattr(context_chunk.chunk, "chunk_metadata", None) or {}
            chunk_type = str(metadata.get("chunk_type") or "")
            if not (
                RagAnswerService._is_schema_overview_metadata(metadata)
                or chunk_type
                in {
                    "attribute_table_schema",
                    "gis_relationship_schema",
                    "relationship_definition",
                    "schema_object_summary",
                }
                or metadata.get("relationship_name")
            ):
                continue

            candidate_lines = RagAnswerService._count_candidate_lines(content)
            if not candidate_lines:
                candidate_lines = [line for line in content.splitlines() if line.strip()]
            for line in candidate_lines[:3]:
                rendered = f"- [{context_chunk.citation_index}] {line}"
                key = normalize_metadata_value(rendered)
                if key in seen:
                    continue
                seen.add(key)
                rows.append(rendered)
                if len(rows) >= 10:
                    break
            if len(rows) >= 10:
                break

        if not rows:
            return None
        return (
            "SCHEMA_OVERVIEW_EVIDENCE:\n"
            "Separate table, layer/object, and relationship counts. Include each structural "
            "group that is directly evidenced; do not invent unnamed remaining layers or fields.\n"
            + "\n".join(rows)
        )

    @staticmethod
    def _metadata_text(value: object) -> str | None:
        if value is None:
            return None
        if isinstance(value, (list, tuple, set)):
            text = ", ".join(str(item).strip() for item in value if str(item).strip())
        else:
            text = str(value).strip()
        return text or None

    @staticmethod
    def _is_count_query(query: str) -> bool:
        normalized = normalize_metadata_value(RagAnswerService._strip_vietnamese_accents(query))
        count_terms = (
            "bao nhieu",
            "co may",
            "count",
            "how many",
            "number of",
            "so luong",
            "tong so",
        )
        return any(term in normalized for term in count_terms)

    @staticmethod
    def _count_query_tokens(query: str) -> set[str]:
        normalized = normalize_metadata_value(RagAnswerService._strip_vietnamese_accents(query))
        stopwords = {
            "a",
            "an",
            "and",
            "are",
            "bao",
            "co",
            "count",
            "cua",
            "duoc",
            "how",
            "la",
            "may",
            "many",
            "number",
            "of",
            "so",
            "the",
            "there",
            "trong",
            "what",
        }
        return {token for token in re.findall(r"[a-z0-9]+", normalized) if len(token) > 1 and token not in stopwords and not token.isdigit()}

    @staticmethod
    def _count_query_phrases(query: str) -> list[str]:
        normalized = normalize_metadata_value(RagAnswerService._strip_vietnamese_accents(query))
        tokens = [token for token in re.findall(r"[a-z0-9]+", normalized) if len(token) > 1]
        phrases: list[str] = []
        for size in (3, 2):
            for index in range(0, max(len(tokens) - size + 1, 0)):
                phrase = " ".join(tokens[index : index + size])
                if phrase not in phrases:
                    phrases.append(phrase)
        return phrases[:16]

    @staticmethod
    def _count_candidate_lines(content: str) -> list[str]:
        lines: list[str] = []
        for raw_line in (content or "").replace("\\_", "_").splitlines():
            for part in re.split(r"(?<=[.;:])\s+(?=\S)", raw_line):
                line = " ".join(part.split()).strip(" -*â€¢â€£â—¦")
                if line and re.search(r"\b\d{1,4}\b", line):
                    lines.append(line)
        return lines

    @staticmethod
    def _table_context_sections(
        *,
        context_chunks: list[ContextChunk],
        query_terms: list[str],
        include_all_table_rows: bool = False,
    ) -> tuple[list[str], list[str]]:
        matched_rows: list[str] = []
        support: list[str] = []
        seen_rows: set[str] = set()
        seen_support: set[str] = set()

        for context_chunk in context_chunks:
            content = context_chunk.chunk.content
            citation = context_chunk.citation_index
            metadata = getattr(context_chunk.chunk, "chunk_metadata", None) or {}
            chunk_type = str(metadata.get("chunk_type") or "")

            for line in RagAnswerService._table_context_lines(content):
                normalized_line = line.casefold()
                formatted = f"[{citation}] {line}"
                is_result_row = "table_row" in normalized_line or chunk_type in {
                    "table_row",
                    "entity_profile",
                }
                if is_result_row and (include_all_table_rows or RagAnswerService._contains_any_query_term(line, query_terms)):
                    if formatted not in seen_rows:
                        seen_rows.add(formatted)
                        matched_rows.append(formatted)
                    continue

                is_support_line = "table_title" in normalized_line or "table_header" in normalized_line or "table_caption" in normalized_line or chunk_type in {"table_title", "table_header", "table_caption"}
                if is_support_line and formatted not in seen_support:
                    seen_support.add(formatted)
                    support.append(formatted)

        return matched_rows, support

    @staticmethod
    def _is_structured_result_context_chunk(
        context_chunk: ContextChunk,
        *,
        query_terms: list[str],
        include_all_table_rows: bool = False,
    ) -> bool:
        """Return True when a chunk is already represented in ENTITY_MATCHED_ROWS.

        ENTITY_MATCHED_ROWS is meant to expose structured table/entity rows once.
        Retrieved Document Context should still keep narrative chunks from the same
        section, but should not repeat pure result-row chunks because that makes
        the prompt look like the same fact appears twice.
        """

        content = context_chunk.chunk.content or ""
        metadata = getattr(context_chunk.chunk, "chunk_metadata", None) or {}
        chunk_type = str(metadata.get("chunk_type") or "")

        result_chunk_types = {"table_row", "entity_profile"}
        support_chunk_types = {"table_title", "table_header", "table_caption"}
        if chunk_type in support_chunk_types:
            return False

        has_result_line = False
        has_narrative_line = False
        for line in RagAnswerService._table_context_lines(content):
            normalized_line = line.casefold()
            is_result_row = "table_row" in normalized_line or chunk_type in result_chunk_types
            is_support_line = "table_title" in normalized_line or "table_header" in normalized_line or "table_caption" in normalized_line or chunk_type in support_chunk_types

            if is_result_row and (include_all_table_rows or RagAnswerService._contains_any_query_term(line, query_terms)):
                has_result_line = True
                continue
            if is_support_line:
                continue
            if line.strip():
                has_narrative_line = True

        return has_result_line and not has_narrative_line

    @staticmethod
    def _table_context_lines(content: str) -> list[str]:
        marker_pattern = re.compile(r"\s+(?=(?:TABLE_TITLE|TABLE_CAPTION|TABLE_HEADER|TABLE_ROW)\b)")
        lines: list[str] = []
        for raw_line in content.splitlines():
            for line in marker_pattern.split(raw_line.strip()):
                if line.strip():
                    lines.append(line.strip())
        return lines or [content.strip()]

    @staticmethod
    def _contains_any_query_term(text: str, query_terms: list[str]) -> bool:
        if not query_terms:
            return False
        normalized = text.casefold()
        return any(term.casefold() in normalized for term in query_terms)

    @staticmethod
    def _is_table_enumeration_query(query: str) -> bool:
        normalized = " ".join(query.casefold().split())
        return any(pattern in normalized for pattern in TABLE_ENUMERATION_QUERY_PATTERNS)

    @staticmethod
    def _session_title(query: str) -> str:
        title = " ".join(query.split())
        return title[:SESSION_TITLE_LIMIT] or "New chat"

    @staticmethod
    def _quote(content: str) -> str:
        return content[:QUOTE_LIMIT]

    def _build_citation_response(
        self,
        *,
        context_chunk: ContextChunk,
        quote: str | None,
    ) -> RagCitationResponse:
        metadata = self._metadata(context_chunk.chunk.chunk_metadata)
        document = getattr(context_chunk.chunk, "document", None)
        files = getattr(document, "files", None) or []
        raw_source_flags = list(context_chunk.source_flags or [context_chunk.source_type])
        source_flags = self._public_source_flags(raw_source_flags)
        if not source_flags:
            source_flags = self._public_source_flags([context_chunk.source_type]) or ["vector"]

        response_metadata = {
            **metadata,
            "source_type": context_chunk.source_type,
            "source_flags": source_flags,
        }
        if context_chunk.artifact_ids:
            response_metadata["artifact_ids"] = context_chunk.artifact_ids
        if raw_source_flags != source_flags:
            response_metadata["raw_source_flags"] = raw_source_flags
        if "lexical_exact" in raw_source_flags:
            response_metadata["match_type"] = "lexical_exact"

        return RagCitationResponse(
            citation_index=context_chunk.citation_index,
            chunk_id=context_chunk.chunk.id,
            document_id=context_chunk.chunk.document_id,
            document_title=getattr(document, "title", None),
            file_name=getattr(files[0], "filename", None) if files else None,
            chunk_index=context_chunk.chunk.chunk_index,
            quote=quote,
            article_number=self._string_or_none(metadata.get("article_number")),
            article_title=self._string_or_none(metadata.get("article_title")),
            chapter_title=self._string_or_none(metadata.get("chapter_title")),
            page_number=self._page_number(metadata),
            source_flags=source_flags,
            metadata=response_metadata,
        )

    @staticmethod
    def _public_source_flags(source_flags: list[str]) -> list[str]:
        public_flags: list[str] = []
        seen: set[str] = set()
        for flag in source_flags:
            normalized = SOURCE_FLAG_ALIASES.get(flag, flag)
            if normalized not in PUBLIC_SOURCE_FLAGS or normalized in seen:
                continue
            seen.add(normalized)
            public_flags.append(normalized)
        return public_flags

    @staticmethod
    def _metadata(metadata: dict[str, Any] | None) -> dict[str, object]:
        return dict(metadata or {})

    @staticmethod
    def _page_number(metadata: dict[str, Any] | None) -> int | None:
        if not metadata:
            return None

        value = metadata.get("page_number")
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
        return None

    @staticmethod
    def _string_or_none(value: object) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @staticmethod
    def _is_schema_count_query(query: str) -> bool:
        normalized = normalize_metadata_value(RagAnswerService._strip_vietnamese_accents(query))
        count_terms = (
            "how many",
            "number of",
            "count",
            "may",
            "bao nhieu",
            "so luong",
        )
        structure_terms = (
            "schema",
            "database",
            "table",
            "column",
            "field",
            "attribute",
            "layer",
            "object",
            "csdl",
            "co so du lieu",
            "bang",
            "cot",
            "truong",
            "thuoc tinh",
            "lop",
            "doi tuong",
        )
        return any(term in normalized for term in count_terms) and any(term in normalized for term in structure_terms)

    @staticmethod
    def _schema_count_search_terms(existing_terms: list[str]) -> list[str]:
        terms = [
            *existing_terms,
            "table",
            "attribute",
            "schema",
            "layer",
            "báº£ng dá»¯ liá»‡u",
            "báº£ng dá»¯ liá»‡u thuá»™c tÃ­nh",
            "thuá»™c tÃ­nh",
            "lá»›p dá»¯ liá»‡u",
        ]
        for term in existing_terms:
            terms.extend(RagAnswerService._query_surface_phrases(term))
            terms.extend(RagAnswerService._query_content_phrases(term))
        return RagAnswerService._dedupe_text_values(terms, limit=64)

    @staticmethod
    def _query_surface_phrases(query: str) -> list[str]:
        words = [word.strip(" ,.;:()[]{}<>!?\"'`") for word in query.split()]
        words = [word for word in words if len(word) > 1]
        phrases: list[str] = []
        for size in range(min(4, len(words)), 0, -1):
            for index in range(0, len(words) - size + 1):
                phrase = " ".join(words[index : index + size])
                if 2 <= len(phrase) <= 80:
                    phrases.append(phrase)
        return RagAnswerService._dedupe_text_values(phrases, limit=16)
