from __future__ import annotations

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
from app.models.user import User
from app.repositories.chat import ChatRepository, CitationCreate
from app.repositories.document_logs import DocumentLogRepository
from app.schemas.chat import RagChatResponse, RagCitationResponse, RagSessionContext
from app.schemas.documents import RerankSearchResult
from app.services.hybrid_search import is_identifier_lookup_query
from app.services.llms import LLMProvider
from app.services.memory.base import MemoryResult
from app.services.query_scope_router import classify_query_scope, scoped_direct_answer
from app.services.reranking_service import RerankingService
from app.services.structured.answer_renderer import render_structured_answer
from app.services.structured.context_completion import collect_structured_evidence
from app.services.table_aware_chunking import extract_entities_from_text
from app.services.table_relationships import (
    analyze_person_area_membership_query,
    is_trusted_relationship_metadata,
    normalize_metadata_value,
)

MEMORY_RULES = (
    "User Memory and Session Summary are background context only: never cite them, and if "
    "they conflict with the retrieved document context, the document context wins. "
    "Citations must only refer to the numbered retrieved document chunks."
)

SYSTEM_PROMPT = (
    "You are a grounded RAG assistant. Answer only from the provided document context. "
    "If the answer is not in the context, say you do not have enough information. "
    f"{MEMORY_RULES}"
)

GENERATIVE_PROMPT = (
    "You are a grounded RAG assistant. Answer naturally and summarize the retrieved "
    "document context. If the answer is not in the context, say you do not have enough "
    f"information. {MEMORY_RULES}"
)

EXTRACTIVE_PROMPT = (
    "You are a document extraction engine. "
    "Return only information explicitly present in the retrieved context. "
    "Do not infer. Do not summarize. Do not rewrite legal wording. "
    "Prefer direct quotations from the retrieved chunks. "
    "If the answer is not in the context, say you do not have enough information. "
    f"{MEMORY_RULES}"
)

HYBRID_PROMPT = (
    "Provide a concise answer. Then provide supporting text from the retrieved context. "
    "If the answer is not in the context, say you do not have enough information. "
    f"{MEMORY_RULES}"
)

ANSWER_MODE_PROMPTS = {
    "generative": GENERATIVE_PROMPT,
    "extractive": EXTRACTIVE_PROMPT,
    "hybrid": HYBRID_PROMPT,
}
DEFAULT_ANSWER_MODE = "hybrid"

CONCISE_STYLE = "Answer style: Concise. Reply in 1-2 sentences without filler."
DETAILED_STYLE = (
    "Answer style: Detailed. Provide a thorough explanation. Preserve exact numbers, "
    "dates, money amounts, and legal wording from the context."
)
POLICY_EXPLAINER_STYLE = (
    "Answer style: Policy explainer. "
    "1) Answer the direct question first using exact numbers, dates, monetary amounts, "
    "and wording from the retrieved context. "
    "2) Then add only details that directly explain the same answer from the retrieved "
    "chunks; do not list loosely related cases, documents, or systems. "
    "If a directly relevant chunk contains a summary followed by list items, include "
    "those list items as concise bullets. "
    "3) Include notes and conditions only if they are present and directly relevant. "
    "4) If table rows exist in the context and the user asks for a list, convert them "
    "into clear bullet points. "
    "5) Cite source chunks using their numeric markers. "
    "6) Use Vietnamese administrative style. "
    "For short identifier/code queries such as '3113', answer what the identifier refers "
    "to and the directly attached date/topic only; do not expand into other retrieved "
    "chunks unless they literally contain the same identifier. "
    "Do not say 'hôm nay' unless the retrieved context explicitly says today. "
    "Do not repeat the same source line or quote more than once. "
    "Do not list duplicate citations. "
    "If only one relevant item is found, give one concise answer and one source. "
    "Answer in Vietnamese only; do not use foreign words when Vietnamese wording is available. "
    "Do not invent information."
)

IDENTIFIER_LOOKUP_STYLE = (
    "Identifier lookup mode: the user is asking about an exact code, number, "
    "document number, or identifier. Answer only what that identifier refers to in "
    "the retrieved context. Do not expand to related documents, related systems, "
    "similar records, or background information unless the same exact identifier is "
    "present in those chunks. Copy document codes, identifiers, dates, organization "
    "names, and proper nouns exactly as they appear in the context; never normalize, "
    "correct, abbreviate, translate, or rewrite them. If the exact identifier appears "
    "as part of a longer code, preserve the full longer code exactly. If there is any "
    "uncertainty, quote the exact identifier string from the context instead of "
    "paraphrasing. Do not infer approval, issuer actions, legal effect, signer, or "
    "recipient unless explicitly stated in the retrieved context. Answer in Vietnamese "
    "only, in 1-3 concise sentences, and cite the supporting chunk. "
)

TABLE_QA_STYLE = (
    "Answer style: Table QA. "
    "You are a document QA assistant. Answer only from the provided context. "
    "When the question asks for a list, all rows, who, total, amount, or complete "
    "table coverage, list every TABLE_ROW present in ENTITY_MATCHED_ROWS or retrieved "
    "context as separate records. "
    "When ENTITY_MATCHED_ROWS exists, treat those rows as structured candidate records, "
    "but also read Retrieved Document Context for narrative sections, definitions, goals, "
    "conditions, and explanations. Do not ignore narrative context just because table rows exist. "
    "TABLE_SUPPORT is title/header/caption support, not a result list. "
    "When ENTITY_MATCHED_ROWS contains N rows, the main answer must have N bullet "
    "points unless rows are exact duplicates. "
    "If the context contains TABLE_ROW records, treat each TABLE_ROW as one record. "
    "When the question asks about an entity, find every TABLE_ROW containing that entity "
    "and answer from fields in the same row. "
    "Do not assume fixed column names. Use the original field labels from context. "
    "Preserve proper names, addresses, dates, and money amounts exactly as written. "
    "If a total row is present, state the total. "
    "Use TABLE_TITLE and TABLE_HEADER context when available to explain the row. "
    "If multiple rows contain the same entity, list all non-duplicate matching rows. "
    "For each row, prefer descriptive fields over ordinal-only fields. "
    "For yes/no questions, start the first sentence with Có or Không. "
    "If a row says Nhân sự đề xuất, say the person is được đề xuất tham gia; do not infer "
    "they are owner, lead, implementer, or solely responsible. "
    "For person-area membership, only use table_row or entity_profile context with high "
    "confidence and relationship_type technology_area_staff. "
    "If context has table_parse_warning or low confidence, say there is not enough direct "
    "evidence and do not use it to confirm membership. "
    "Apply role_note only to the exact person whose row metadata states that note. "
    "If context conflicts, prefer chunk_type table_row or entity_profile from staff tables. "
    "If a row has only generic labels such as cell_1 or cell_2, use those labels as-is. "
    "Do not say 'similar rows' instead of listing the matching rows. "
    "Do not use a person's list number as the row's main ordinal field. "
    "Do not infer missing fields. "
    "Do not use legal/policy language if the document is not a legal/policy document. "
    "Do not say detailed rows are unavailable when TABLE_ROW records are present. "
    "If there is not enough information, say so clearly. "
    "Answer in Vietnamese. Do not use foreign words when Vietnamese wording is available. "
    "Do not invent information."
)

ANSWER_STYLE_INSTRUCTIONS = {
    "concise": CONCISE_STYLE,
    "detailed": DETAILED_STYLE,
    "policy_explainer": POLICY_EXPLAINER_STYLE,
    "table_qa": TABLE_QA_STYLE,
}
DEFAULT_ANSWER_STYLE = "policy_explainer"
PUBLIC_SOURCE_FLAGS = {"vector", "keyword", "graph", "neighbor"}
SOURCE_FLAG_ALIASES = {
    "lexical_exact": "keyword",
    "exact": "keyword",
    "entity_exact": "keyword",
    "keyword_exact": "keyword",
    "primary": "vector",
    "semantic": "vector",
}
TABLE_ENUMERATION_QUERY_PATTERNS = (
    "danh sách",
    "liệt kê",
    "bao gồm",
    "gồm những ai",
    "có những ai",
    "những hộ nào",
    "các hộ",
    "các cá nhân",
    "tổng cộng",
    "số tiền",
    "bao nhiêu",
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
        llm_provider: LLMProvider,
        document_log_repository: DocumentLogRepository | None = None,
    ) -> None:
        self._chat_repository = chat_repository
        self._reranking_service = reranking_service
        self._llm_provider = llm_provider
        self._document_log_repository = document_log_repository

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

            retrieval_query = self._query_with_short_term_context(
                query=query,
                session_context=session_context,
            )

            if document_ids is None:
                rerank_response = await self._reranking_service.search(
                    query=retrieval_query,
                    top_k=top_k,
                    candidate_k=candidate_k,
                    session_id=chat_session.id,
                    use_graph=use_graph,
                    graph_expansion_depth=graph_expansion_depth,
                    graph_expansion_limit=graph_expansion_limit,
                )
            else:
                rerank_response = await self._reranking_service.search(
                    query=retrieval_query,
                    top_k=top_k,
                    candidate_k=candidate_k,
                    session_id=chat_session.id,
                    document_ids=document_ids,
                    use_graph=use_graph,
                    graph_expansion_depth=graph_expansion_depth,
                    graph_expansion_limit=graph_expansion_limit,
                )
            context_chunks = await self._load_context_chunks(
                rerank_results=rerank_response.results,
            )
            context_chunks = await self._expand_with_neighbors(
                query=query,
                context_chunks=context_chunks,
                max_context_chars=max_context_chars,
            )
            context_chunks = self._deduplicate_context_chunks(context_chunks)
            context_chunks = self._filter_identifier_context(
                query=query,
                context_chunks=context_chunks,
            )
            context_chunks = await self._augment_person_area_context(
                query=query,
                context_chunks=context_chunks,
                scoped_document_ids=document_ids,
            )
            context_chunks = await self._augment_legal_leave_context(
                query=query,
                context_chunks=context_chunks,
                scoped_document_ids=document_ids,
            )
            deterministic_answer = self._deterministic_legal_leave_answer(
                query=query,
                context_chunks=context_chunks,
            )
            if deterministic_answer is None:
                deterministic_answer = self._deterministic_person_area_answer(
                    query=query,
                    context_chunks=context_chunks,
                )
            if deterministic_answer is None:
                deterministic_answer = self._deterministic_narrative_section_answer(
                    query=query,
                    context_chunks=context_chunks,
                )
            if deterministic_answer is not None:
                answer = deterministic_answer
            elif self._person_area_query_person(query) is not None:
                answer = self._insufficient_person_area_answer(query)
            else:
                user_prompt = self._build_user_prompt(
                    query=query,
                    context_chunks=context_chunks,
                    memory_context=memory_context,
                    session_summary=session_summary,
                    session_context=session_context,
                )
                answer = await self._llm_provider.generate(
                    system_prompt=build_system_prompt(
                        answer_mode=answer_mode,
                        answer_style=answer_style,
                        query=query,
                    ),
                    user_prompt=user_prompt,
                )
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
                cited_document_ids = {
                    context_chunk.chunk.document_id for context_chunk in context_chunks
                }
                for document_id in cited_document_ids:
                    await self._document_log_repository.create_access_log(
                        document_id=document_id,
                        user_id=current_user.id,
                        organization_id=current_user.organization_id,
                        action="chat",
                        metadata={"session_id": str(chat_session.id), "query": query},
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
            citations=[
                self._build_citation_response(context_chunk=context_chunk, quote=citation.quote)
                for context_chunk, citation in zip(context_chunks, citation_records, strict=True)
            ],
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

            retrieval_query = self._query_with_short_term_context(
                query=query,
                session_context=session_context,
            )

            if document_ids is None:
                rerank_response = await self._reranking_service.search(
                    query=retrieval_query,
                    top_k=top_k,
                    candidate_k=candidate_k,
                    session_id=chat_session.id,
                    use_graph=use_graph,
                    graph_expansion_depth=graph_expansion_depth,
                    graph_expansion_limit=graph_expansion_limit,
                )
            else:
                rerank_response = await self._reranking_service.search(
                    query=retrieval_query,
                    top_k=top_k,
                    candidate_k=candidate_k,
                    session_id=chat_session.id,
                    document_ids=document_ids,
                    use_graph=use_graph,
                    graph_expansion_depth=graph_expansion_depth,
                    graph_expansion_limit=graph_expansion_limit,
                )
            context_chunks = await self._load_context_chunks(
                rerank_results=rerank_response.results,
            )
            context_chunks = await self._expand_with_neighbors(
                query=query,
                context_chunks=context_chunks,
                max_context_chars=max_context_chars,
            )
            context_chunks = self._deduplicate_context_chunks(context_chunks)
            context_chunks = self._filter_identifier_context(
                query=query,
                context_chunks=context_chunks,
            )
            context_chunks = await self._augment_person_area_context(
                query=query,
                context_chunks=context_chunks,
                scoped_document_ids=document_ids,
            )
            context_chunks = await self._augment_legal_leave_context(
                query=query,
                context_chunks=context_chunks,
                scoped_document_ids=document_ids,
            )
            deterministic_answer = self._deterministic_legal_leave_answer(
                query=query,
                context_chunks=context_chunks,
            )
            if deterministic_answer is None:
                deterministic_answer = self._deterministic_person_area_answer(
                    query=query,
                    context_chunks=context_chunks,
                )
            if deterministic_answer is None:
                deterministic_answer = self._deterministic_narrative_section_answer(
                    query=query,
                    context_chunks=context_chunks,
                )

            yield RagStreamEvent(
                event="metadata",
                data={
                    "session_id": str(chat_session.id),
                    "user_message_id": str(user_message.id),
                },
            )

            if deterministic_answer is not None:
                answer = deterministic_answer
                yield RagStreamEvent(event="token", data={"delta": answer})
            elif self._person_area_query_person(query) is not None:
                answer = self._insufficient_person_area_answer(query)
                yield RagStreamEvent(event="token", data={"delta": answer})
            else:
                user_prompt = self._build_user_prompt(
                    query=query,
                    context_chunks=context_chunks,
                    memory_context=memory_context,
                    session_summary=session_summary,
                    session_context=session_context,
                )
                answer_parts: list[str] = []
                async for delta in self._llm_provider.stream_generate(
                    system_prompt=build_system_prompt(
                        answer_mode=answer_mode,
                        answer_style=answer_style,
                        query=query,
                    ),
                    user_prompt=user_prompt,
                ):
                    if not delta:
                        continue
                    answer_parts.append(delta)
                    yield RagStreamEvent(event="token", data={"delta": delta})

                answer = "".join(answer_parts)
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
                cited_document_ids = {
                    context_chunk.chunk.document_id for context_chunk in context_chunks
                }
                for document_id in cited_document_ids:
                    await self._document_log_repository.create_access_log(
                        document_id=document_id,
                        user_id=current_user.id,
                        organization_id=current_user.organization_id,
                        action="chat",
                        metadata={"session_id": str(chat_session.id), "query": query},
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
                for context_chunk, citation in zip(
                    context_chunks, citation_records, strict=True
                )
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

    async def _expand_with_neighbors(
        self,
        *,
        query: str,
        context_chunks: list[ContextChunk],
        max_context_chars: int,
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
        if (
            get_article_neighbors is None
            and get_table_chunks is None
            and get_entity_coverage_chunks is None
        ):
            return context_chunks

        existing_ids: set[UUID] = {context_chunk.chunk.id for context_chunk in context_chunks}
        seen_articles: set[tuple[UUID, str]] = set()
        seen_documents: set[UUID] = set()
        seen_tables: set[tuple[UUID, str]] = set()
        total_chars = sum(len(item.chunk.content) for item in context_chunks)
        query_terms = self._query_terms(query)
        wants_full_table = self._is_table_enumeration_query(query)
        context_char_limit = (
            max(max_context_chars, TABLE_ENUMERATION_CONTEXT_CHAR_LIMIT)
            if wants_full_table
            else max_context_chars
        )

        expanded = list(context_chunks)
        next_index = max((item.citation_index for item in context_chunks), default=0) + 1

        for context_chunk in context_chunks:
            metadata = context_chunk.chunk.chunk_metadata or {}
            document_id = context_chunk.chunk.document_id
            if (
                query_terms
                and get_entity_coverage_chunks is not None
                and document_id not in seen_documents
            ):
                seen_documents.add(document_id)
                try:
                    coverage_chunks = await get_entity_coverage_chunks(
                        document_id=document_id,
                        search_terms=query_terms,
                        exclude_ids=tuple(existing_ids),
                    )
                except Exception:
                    coverage_chunks = []

                coverage_added = 0
                for coverage_chunk in self._prioritize_entity_coverage_chunks(
                    chunks=coverage_chunks,
                    query_terms=query_terms,
                ):
                    if coverage_chunk.id in existing_ids:
                        continue
                    coverage_len = len(coverage_chunk.content or "")
                    over_context_limit = total_chars + coverage_len > context_char_limit
                    allow_budget_override = (
                        coverage_added < 2
                        and RagAnswerService._is_high_signal_context_chunk(
                            chunk=coverage_chunk,
                            query_terms=query_terms,
                        )
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
                    neighbors = [
                        neighbor
                        for neighbor in neighbors
                        if neighbor.document_id == document_id
                    ]

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
        "bao nhiêu", "mục tiêu", or "tham gia". Structured retrieval is cheap
        and safe to try for any non-empty query; row/section scoring plus
        answerability thresholds decide whether retrieved structured evidence is
        actually relevant.
        """

        normalized = normalize_metadata_value(
            RagAnswerService._strip_vietnamese_accents(query)
        )
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

        normalized = normalize_metadata_value(
            RagAnswerService._strip_vietnamese_accents(query)
        )
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
            "row" in chunk_type
            or relationship_type in {"structured_fact_row", "legal_leave_benefit"}
            or legacy_relationship_type == "legal_leave_benefit"
            or bool(metadata.get("case_name") or metadata.get("row_text"))
        )

    @staticmethod
    def _prioritize_legal_leave_chunks(
        *,
        chunks: list[Chunk],
        query: str,
    ) -> list[Chunk]:
        def priority(chunk: Chunk) -> tuple[int, float, int]:
            metadata = chunk.chunk_metadata or {}
            content_key = normalize_metadata_value(
                RagAnswerService._strip_vietnamese_accents(chunk.content or "")
            )
            if RagAnswerService._is_structured_fact_row(metadata):
                row = {
                    "case_name": str(metadata.get("case_name") or ""),
                    "row_text": str(metadata.get("row_text") or chunk.content or ""),
                    "total_leave_benefit": str(
                        metadata.get("total_leave_benefit")
                        or metadata.get("total_benefit")
                        or ""
                    ),
                    "labor_code_benefit": str(
                        metadata.get("labor_code_benefit")
                        or metadata.get("base_benefit")
                        or ""
                    ),
                    "collective_agreement_benefit": str(
                        metadata.get("collective_agreement_benefit")
                        or metadata.get("additional_benefit")
                        or ""
                    ),
                }
                score = RagAnswerService._legal_table_fact_row_score(query, row)
                return (0, -score, chunk.chunk_index)
            if any(term in content_key for term in RagAnswerService._query_content_phrases(query)):
                return (3, 0.0, chunk.chunk_index)
            return (8, 0.0, chunk.chunk_index)

        return sorted(chunks, key=priority)

    @staticmethod
    def _legal_table_fact_tokens(value: Any) -> set[str]:
        """Tokenize fact text without language/domain-specific stopword lists."""

        text = normalize_metadata_value(
            RagAnswerService._strip_vietnamese_accents(str(value or ""))
        )
        return {token for token in re.findall(r"[a-z0-9]+", text) if len(token) > 1}

    @staticmethod
    def _legal_table_fact_row_score(query: str, row: dict[str, Any]) -> float:
        """Score how well a query matches one legal_table_row without case hardcodes."""

        query_norm = normalize_metadata_value(RagAnswerService._strip_vietnamese_accents(query))
        case_name = str(row.get("case_name") or "")
        row_text = " ".join(
            str(row.get(key) or "")
            for key in (
                "case_name",
                "row_text",
                "total_leave_benefit",
                "labor_code_benefit",
                "collective_agreement_benefit",
            )
        )
        case_norm = normalize_metadata_value(RagAnswerService._strip_vietnamese_accents(case_name))
        row_norm = normalize_metadata_value(RagAnswerService._strip_vietnamese_accents(row_text))
        query_tokens = RagAnswerService._legal_table_fact_tokens(query_norm)
        case_tokens = RagAnswerService._legal_table_fact_tokens(case_norm)
        row_tokens = RagAnswerService._legal_table_fact_tokens(row_norm)
        if not query_tokens:
            return 0.0

        case_overlap = len(query_tokens & case_tokens) / max(len(query_tokens), 1)
        row_overlap = len(query_tokens & row_tokens) / max(len(query_tokens), 1)
        score = case_overlap * 0.65 + row_overlap * 0.20

        variants = RagAnswerService._legal_table_case_variants(case_name)
        variant_scores: list[float] = []
        for variant in variants:
            variant_norm = normalize_metadata_value(
                RagAnswerService._strip_vietnamese_accents(variant)
            )
            variant_tokens = RagAnswerService._legal_table_fact_tokens(variant_norm)
            if not variant_tokens:
                continue
            variant_overlap = len(query_tokens & variant_tokens) / max(len(variant_tokens), 1)
            query_coverage = len(query_tokens & variant_tokens) / max(len(query_tokens), 1)
            variant_score = variant_overlap * 0.65 + query_coverage * 0.35
            if variant_norm and variant_norm in query_norm:
                variant_score += 0.35
            if query_norm and query_norm in variant_norm:
                variant_score += 0.2
            generic_event_tokens = {"ket", "hon", "sinh", "con", "chet"}
            specific_tokens = variant_tokens - generic_event_tokens
            if specific_tokens and specific_tokens.issubset(query_tokens):
                variant_score += min(0.35, 0.12 * len(specific_tokens))
            variant_scores.append(variant_score)
        if variant_scores:
            score = max(score, max(variant_scores))

        # Generic phrase containment bonuses. These do not encode any benefit or
        # case answer; they only reward a table row whose case text appears in the
        # user question, or whose question tokens are mostly contained in the case.
        if case_norm and case_norm in query_norm:
            score += 0.25
        if query_norm and query_norm in case_norm:
            score += 0.15
        if case_tokens and case_tokens.issubset(query_tokens):
            score += 0.15

        # Penalize broad rows when the user supplied additional content tokens.
        # This is generic token coverage, not a case-specific rule.
        extra_query_tokens = query_tokens - case_tokens
        if extra_query_tokens and len(case_tokens) <= 2:
            score -= min(0.35, 0.10 * len(extra_query_tokens))

        return max(score, 0.0)

    @staticmethod
    def _split_legal_table_case_names(value: Any) -> list[str]:
        """Split compound case names in a generic way for cleaner answers."""

        name = re.sub(r"\s+", " ", str(value or "")).strip().rstrip(" .;:")
        if not name:
            return ["Trường hợp liên quan"]
        # Some legal table cells contain multiple cases separated by semicolons.
        # Preserve one answer bullet per case without hardcoding the actual cases.
        if ";" in name:
            parts = [part.strip().rstrip(" .;:") for part in name.split(";")]
            return [part for part in parts if part]
        return [name]

    @staticmethod
    def _legal_table_query_asks_for_related_cases(query: str) -> bool:
        """Return True only when the user explicitly asks for a broader list.

        This keeps a specific row question (for example one relative/event in a
        benefits table) from expanding into every other row in the same table.
        The check is intentionally generic: it detects list/related-intent words,
        not any concrete legal case or answer.
        """

        normalized = normalize_metadata_value(
            RagAnswerService._strip_vietnamese_accents(query)
        )
        broad_patterns = (
            "cac truong hop",
            "truong hop nao",
            "nhung truong hop",
            "cac dong",
            "cac muc",
            "cac quyen loi",
            "quyen loi lien quan",
            "truong hop lien quan",
            "ngoai truong hop",
            "ngoai ra",
            "lien quan",
            "liet ke",
            "danh sach",
            "tat ca",
            "day du",
            "bao gom",
            "gom nhung",
            "nhung ai",
        )
        return any(pattern in normalized for pattern in broad_patterns)

    @staticmethod
    def _legal_table_case_variants(case_name: str) -> list[str]:
        """Create readable case variants from a table cell without case hardcodes.

        Legal table cells often write compact alternatives like
        "A, B <event>". For direct answers we can render the matched alternative
        and mention the remaining alternatives. This function uses only syntax
        from the row text; it does not encode any concrete legal case.
        """

        cleaned = re.sub(r"\s+", " ", str(case_name or "")).strip().rstrip(" .;:")
        if not cleaned:
            return []

        semicolon_parts = [part.strip().rstrip(" .;:") for part in cleaned.split(";") if part.strip()]
        variants: list[str] = []
        for segment in semicolon_parts:
            comma_parts = [part.strip().rstrip(" .;:") for part in segment.split(",") if part.strip()]
            if len(comma_parts) <= 1:
                variants.append(segment)
                continue

            last = comma_parts[-1]
            last_tokens = last.split()
            suffix = " ".join(last_tokens[-2:]) if len(last_tokens) >= 3 else ""
            for index, part in enumerate(comma_parts):
                if index == len(comma_parts) - 1 or not suffix:
                    variants.append(part)
                else:
                    part_norm = normalize_metadata_value(
                        RagAnswerService._strip_vietnamese_accents(part)
                    )
                    suffix_norm = normalize_metadata_value(
                        RagAnswerService._strip_vietnamese_accents(suffix)
                    )
                    if suffix_norm and suffix_norm not in part_norm:
                        variants.append(f"{part} {suffix}")
                    else:
                        variants.append(part)

        # Stable de-duplication while preserving original order.
        result: list[str] = []
        seen: set[str] = set()
        for variant in variants:
            key = normalize_metadata_value(RagAnswerService._strip_vietnamese_accents(variant))
            if key and key not in seen:
                seen.add(key)
                result.append(variant)
        return result or [cleaned]

    @staticmethod
    def _select_legal_table_case_variant(query: str, case_name: str) -> tuple[str, list[str]]:
        """Pick the row phrase most directly matched by the query.

        The selected phrase and alternatives are derived from the row text and
        token overlap only. No row labels or legal outcomes are hardcoded.
        """

        variants = RagAnswerService._legal_table_case_variants(case_name)
        if not variants:
            return "Trường hợp liên quan", []

        query_tokens = RagAnswerService._legal_table_fact_tokens(query)
        scored: list[tuple[float, int, str]] = []
        for index, variant in enumerate(variants):
            variant_tokens = RagAnswerService._legal_table_fact_tokens(variant)
            if not variant_tokens:
                score = 0.0
            else:
                score = len(query_tokens & variant_tokens) / max(len(variant_tokens), 1)
            scored.append((score, -index, variant))

        scored.sort(reverse=True)
        selected = scored[0][2]
        selected_key = normalize_metadata_value(RagAnswerService._strip_vietnamese_accents(selected))
        alternatives = [
            variant
            for variant in variants
            if normalize_metadata_value(RagAnswerService._strip_vietnamese_accents(variant)) != selected_key
        ]
        return selected, alternatives

    @staticmethod
    def _legal_table_source_label(row: dict[str, Any]) -> str:
        source_value = next(
            (
                str(row.get(key) or "").strip()
                for key in ("source_label", "document_title", "source_file")
                if str(row.get(key) or "").strip()
            ),
            "tài liệu được truy xuất",
        )
        source_name = RagAnswerService._clean_source_label(source_value)
        source_context = " ".join(
            str(row.get(key) or "")
            for key in ("source_date", "document_date", "doc_date", "source_file")
        )
        date_match = re.search(r"(\d{1,2})[.\-/](\d{1,2})[.\-/](\d{4})", source_context)
        if not date_match:
            return source_name

        day, month, year = date_match.groups()
        date_label = f"{int(day):02d}/{int(month):02d}/{year}"
        source_name = re.sub(r"^\d+\s*[.\-]\s*", "", source_name).strip()
        source_name = re.sub(r"KY\s+KET", "ký ngày", source_name, flags=re.IGNORECASE)
        source_name = re.sub(r"\d{1,2}[.\-/]\d{1,2}[.\-/]\d{4}", "", source_name)
        source_name = re.sub(r"\s+", " ", source_name).strip(" .;:-")
        if "ký ngày" in source_name.casefold():
            return f"{source_name} {date_label}"
        return f"{source_name} ký ngày {date_label}"

    @staticmethod
    def _clean_source_label(value: str) -> str:
        label = str(value or "").strip().split("/")[-1]
        label = re.sub(r"\.(docx?|pdf|xlsx?|pptx?)$", "", label, flags=re.IGNORECASE)
        label = re.sub(r"[_-]+", " ", label)
        return re.sub(r"\s+", " ", label).strip() or "tài liệu được truy xuất"

    @staticmethod
    def _legal_table_topic_label(row: dict[str, Any]) -> str:
        value = next(
            (
                str(row.get(key) or "").strip()
                for key in ("table_name", "article_title", "section_title")
                if str(row.get(key) or "").strip()
            ),
            "",
        )
        value = re.sub(r"^Điều\s+\d+\s*[.:\-]?\s*", "", value, flags=re.IGNORECASE)
        return re.sub(r"\s+", " ", value).strip(" .;:")

    @staticmethod
    def _legal_table_fact_days(row: dict[str, Any]) -> int | None:
        value = row.get("total_leave_days") or row.get("total_days")
        try:
            if value is None or value == "":
                benefit = RagAnswerService._legal_table_fact_benefit(row)
                return RagAnswerService._extract_first_day_count(benefit)
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _legal_table_fact_benefit(row: dict[str, Any]) -> str:
        return str(
            row.get("total_leave_benefit")
            or row.get("total_benefit")
            or row.get("labor_code_benefit")
            or row.get("base_benefit")
            or row.get("collective_agreement_benefit")
            or row.get("additional_benefit")
            or ""
        ).strip()

    @staticmethod
    def _legal_table_fact_note(row: dict[str, Any]) -> str:
        """Build a generic explanation from base/additional fact columns."""

        base = str(row.get("labor_code_benefit") or row.get("base_benefit") or "").strip()
        additional = str(
            row.get("collective_agreement_benefit")
            or row.get("additional_benefit")
            or ""
        ).strip()
        base_days = RagAnswerService._extract_first_day_count(base)
        additional_days = RagAnswerService._extract_first_day_count(additional)
        if base_days is not None and additional_days is not None:
            return (
                f" ({base_days:02d} ngày theo quy định nền + "
                f"{additional_days:02d} ngày theo quy định bổ sung)"
            )
        return ""

    @staticmethod
    def _extract_first_day_count(value: str) -> int | None:
        text = normalize_metadata_value(RagAnswerService._strip_vietnamese_accents(value))
        match = re.search(r"nghi\s+(\d+)\s+ngay", text)
        if not match:
            return None
        try:
            return int(match.group(1))
        except ValueError:
            return None

    @staticmethod
    def _is_yes_no_question(query: str) -> bool:
        """Best-effort yes/no detector without language-specific keyword rules.

        This deterministic answer path can safely render a factual answer even
        when the question is not yes/no. We only use a weak, punctuation-based
        signal here and avoid embedding Vietnamese cue phrases in code.
        """

        return False

    @staticmethod
    def _lowercase_initial(value: str) -> str:
        value = str(value or "").strip()
        if not value:
            return value
        return value[:1].lower() + value[1:]

    @staticmethod
    def _legal_case_match_key(value: str) -> str:
        return normalize_metadata_value(RagAnswerService._strip_vietnamese_accents(value))

    @staticmethod
    def _legal_table_topic_subject(topic: str) -> str:
        topic_text = RagAnswerService._lowercase_initial(topic).strip()
        if not topic_text:
            return "quyền lợi liên quan"
        topic_key = normalize_metadata_value(RagAnswerService._strip_vietnamese_accents(topic_text))
        if topic_key.startswith("nghi"):
            return f"người lao động được {topic_text}"
        return topic_text

    @staticmethod
    def _legal_table_notice_suffix(row: dict[str, Any]) -> str:
        """Preserve row-level conditions such as notice/approval requirements."""

        for key in (
            "total_leave_benefit",
            "total_benefit",
            "collective_agreement_benefit",
            "additional_benefit",
            "labor_code_benefit",
            "base_benefit",
            "row_text",
        ):
            value = str(row.get(key) or "").strip()
            value_key = normalize_metadata_value(
                RagAnswerService._strip_vietnamese_accents(value)
            )
            if "phai thong bao" not in value_key:
                continue

            match = re.search(
                r"(?i)(phải\s+thông\s+báo\b.*|phai\s+thong\s+bao\b.*)",
                value,
            )
            notice = match.group(1) if match else value
            notice = re.sub(r"\bNSDLĐ\b", "người sử dụng lao động", notice)
            notice = re.sub(
                r"\bNSDLD\b",
                "người sử dụng lao động",
                notice,
                flags=re.IGNORECASE,
            )
            notice = re.sub(r"\s+", " ", notice).strip(" .;,")
            if not notice:
                continue
            return f" và {RagAnswerService._lowercase_initial(notice)}"
        return ""

    @staticmethod
    def _legal_table_day_breakdown(row: dict[str, Any]) -> str:
        base = str(row.get("labor_code_benefit") or row.get("base_benefit") or "").strip()
        additional = str(
            row.get("collective_agreement_benefit")
            or row.get("additional_benefit")
            or ""
        ).strip()
        base_days = RagAnswerService._extract_first_day_count(base)
        additional_days = RagAnswerService._extract_first_day_count(additional)
        if base_days is None or additional_days is None:
            return ""
        return (
            f" (bao gồm {base_days:02d} ngày theo quy định nền và "
            f"{additional_days:02d} ngày theo quy định bổ sung trong tài liệu)"
        )

    @staticmethod
    def _legal_table_related_case_bullet(case_name: str, row: dict[str, Any]) -> str | None:
        label = re.sub(r"\s+", " ", str(case_name or "")).strip().rstrip(" .;:")
        if not label:
            return None

        days = RagAnswerService._legal_table_fact_days(row)
        benefit = RagAnswerService._legal_table_fact_benefit(row)
        if days is None and not benefit:
            return None

        if days is not None:
            detail = f"Được nghỉ **{days:02d} ngày**"
            detail += RagAnswerService._legal_table_day_breakdown(row)
            detail += RagAnswerService._legal_table_notice_suffix(row)
            detail = detail.rstrip(" .") + "."
        else:
            detail = f"Được hưởng: {benefit.rstrip(' .')}."

        return f"- **{label}:** {detail}"

    @staticmethod
    def _build_related_legal_table_case_section(
        *,
        query: str,
        best: dict[str, Any],
        rows: list[dict[str, Any]],
    ) -> str | None:
        selected_case, _ = RagAnswerService._select_legal_table_case_variant(
            query,
            str(best.get("case_name") or ""),
        )
        selected_key = RagAnswerService._legal_case_match_key(selected_case)
        best_case_key = RagAnswerService._legal_case_match_key(
            str(best.get("case_name") or "")
        )
        if not selected_key or selected_key != best_case_key:
            return None

        bullets: list[str] = []
        seen: set[str] = set()
        for row in sorted(rows, key=lambda item: str(item.get("case_code") or "")):
            if row is best:
                continue
            row_case_name = str(row.get("case_name") or "")
            row_case_key = RagAnswerService._legal_case_match_key(row_case_name)
            if selected_key not in row_case_key:
                continue

            for case_part in RagAnswerService._split_legal_table_case_names(row_case_name):
                case_key = RagAnswerService._legal_case_match_key(case_part)
                if not case_key or selected_key not in case_key or case_key in seen:
                    continue
                bullet = RagAnswerService._legal_table_related_case_bullet(case_part, row)
                if not bullet:
                    continue
                seen.add(case_key)
                bullets.append(bullet)

        if not bullets:
            return None

        selected_text = RagAnswerService._lowercase_initial(selected_case)
        topic = RagAnswerService._legal_table_topic_label(best)
        topic_text = RagAnswerService._lowercase_initial(topic) if topic else "quyền lợi này"
        return "\n".join(
            [
                f"Ngoài trường hợp **{selected_text}**, tài liệu còn quy định "
                f"cụ thể về {topic_text} cho các trường hợp liên quan như sau:",
                "",
                *bullets,
            ]
        )

    @staticmethod
    def _build_legal_table_fact_answer(row: dict[str, Any], *, query: str) -> str | None:
        """Build a direct answer from one matched row's metadata only."""

        case_name = str(row.get("case_name") or "").strip()
        selected_case, alternatives = RagAnswerService._select_legal_table_case_variant(
            query, case_name
        )
        selected_case_text = RagAnswerService._lowercase_initial(selected_case)
        days = RagAnswerService._legal_table_fact_days(row)
        benefit = RagAnswerService._legal_table_fact_benefit(row)
        labor = str(row.get("labor_code_benefit") or "").strip()
        collective = str(row.get("collective_agreement_benefit") or "").strip()
        labor_days = RagAnswerService._extract_first_day_count(labor)
        collective_days = RagAnswerService._extract_first_day_count(collective)
        source_label = RagAnswerService._legal_table_source_label(row)
        yes_no_question = RagAnswerService._is_yes_no_question(query)
        topic = RagAnswerService._legal_table_topic_label(row)
        topic_subject = RagAnswerService._legal_table_topic_subject(topic)

        if days is not None:
            if yes_no_question:
                lines = [
                    (
                        f"Có, theo {source_label}, trường hợp **{selected_case_text}** "
                        "có trong dữ liệu được truy xuất."
                    ),
                    f"Cụ thể, {topic_subject} là **{days:02d} ngày**.",
                ]
            else:
                lines = [
                    (
                        f"Theo {source_label}, khi **{selected_case_text}**, "
                        f"{topic_subject} là **{days:02d} ngày**."
                    )
                ]
        elif benefit:
            if yes_no_question:
                lines = [
                    (
                        f"Có, theo {source_label}, trường hợp **{selected_case_text}** "
                        f"được hưởng quyền lợi này: {benefit}."
                    )
                ]
            else:
                lines = [f"Theo {source_label}, khi **{selected_case_text}**: {benefit}."]
        else:
            return None

        if labor_days is not None and collective_days is not None:
            lines.extend(
                [
                    "",
                    "Số ngày nghỉ này được tính cụ thể như sau:",
                    f"- {labor_days:02d} ngày theo quy định của Bộ luật Lao động.",
                    (
                        f"- {collective_days:02d} ngày được hưởng thêm theo "
                        "quy định bổ sung trong tài liệu."
                    ),
                ]
            )

        if alternatives:
            alternatives_text = "; ".join(
                RagAnswerService._lowercase_initial(alternative)
                for alternative in alternatives
            )
            lines.extend(
                [
                    "",
                    (
                        "Quy định này cũng áp dụng tương tự đối với trường hợp "
                        f"{alternatives_text}."
                    ),
                ]
            )

        return "\n".join(lines).strip() or None

    @staticmethod
    def _deterministic_legal_leave_answer(
        *,
        query: str,
        context_chunks: list[ContextChunk],
    ) -> str | None:
        return RagAnswerService._deterministic_structured_fact_answer(
            query=query,
            context_chunks=context_chunks,
        )

    async def _augment_person_area_context(
        self,
        *,
        query: str,
        context_chunks: list[ContextChunk],
        scoped_document_ids: set[UUID] | None = None,
    ) -> list[ContextChunk]:
        """Pull exact person rows before deterministic person-area answers."""

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
    def _person_area_matches(area: str, target: str) -> bool:
        area_key = normalize_metadata_value(area)
        target_key = normalize_metadata_value(target)
        if not area_key or not target_key:
            return False
        if area_key == target_key or area_key in target_key or target_key in area_key:
            return True

        target_acronyms = set(re.findall(r"\b[A-Z0-9][A-Z0-9._/-]{1,}\b", target))
        area_acronyms = set(re.findall(r"\b[A-Z0-9][A-Z0-9._/-]{1,}\b", area))
        if target_acronyms and not (target_acronyms & area_acronyms):
            return False

        area_tokens = re.findall(r"[a-z0-9]+", area_key)
        target_tokens = re.findall(r"[a-z0-9]+", target_key)
        if not area_tokens or not target_tokens:
            return False

        def ngrams(tokens: list[str], size: int) -> set[tuple[str, ...]]:
            return {
                tuple(tokens[index : index + size])
                for index in range(0, len(tokens) - size + 1)
            }

        area_token_set = set(area_tokens)
        target_token_set = set(target_tokens)
        token_overlap = area_token_set & target_token_set
        token_coverage = len(token_overlap) / max(len(target_token_set), 1)

        area_bigrams = ngrams(area_tokens, 2)
        target_bigrams = ngrams(target_tokens, 2)
        bigram_overlap = area_bigrams & target_bigrams
        bigram_coverage = len(bigram_overlap) / max(len(target_bigrams), 1)

        return token_coverage >= 0.65 or bigram_coverage >= 0.45

    @staticmethod
    def _is_generic_person_area_candidate(value: str | None) -> bool:
        """Return True for low-signal area candidates without fixed phrases."""

        raw = str(value or "").strip()
        normalized = normalize_metadata_value(raw)
        if not normalized:
            return True
        tokens = re.findall(r"[a-z0-9]+", normalized)
        if not tokens:
            return True
        has_anchor = bool(re.search(r"[A-Z]{2,}|\d", raw)) or any(
            len(token) >= 6 for token in tokens
        )
        return len(tokens) <= 6 and not has_anchor

    @staticmethod
    def _parse_person_area_profile_text(
        content: str,
        *,
        person_name: str,
        citation: int,
    ) -> list[dict[str, str]]:
        if not content or not person_name:
            return []
        person_match = re.search(r"(?im)^\s*Nhân sự\s*:\s*(?P<name>[^.\n]+)", content)
        if person_match is None:
            return []
        profile_name = " ".join(person_match.group("name").split()).strip(" .;:")
        if not RagAnswerService._person_name_matches(profile_name, person_name):
            return []

        rows: list[dict[str, str]] = []
        for line in content.splitlines():
            match = re.match(
                r"\s*-\s*(?P<area>.+?)\s*;\s*phòng chủ trì\s*:\s*"
                r"(?P<department>[^.;\n]+)(?:\s*;\s*ghi chú\s*:\s*(?P<note>[^.\n]+))?\.??\s*$",
                line,
                flags=re.IGNORECASE,
            )
            if match is None:
                continue
            row = {
                "area": " ".join(match.group("area").split()).strip(" .;:"),
                "department": " ".join(match.group("department").split()).strip(" .;:"),
                "stt": "",
                "citation": str(citation),
                "role_note": "",
            }
            note = match.group("note")
            if note:
                row["role_note"] = " ".join(note.split()).strip(" .;:")
            if row["area"]:
                rows.append(row)
        return rows

    @staticmethod
    def _strip_vietnamese_accents(value: str) -> str:
        normalized = unicodedata.normalize("NFD", value or "")
        stripped = "".join(char for char in normalized if unicodedata.category(char) != "Mn")
        return stripped.replace("Đ", "D").replace("đ", "d")

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
                metadata_name = str(
                    metadata.get("person_name") or metadata.get("entity_name") or ""
                )
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
    def _deterministic_person_area_answer(
        *,
        query: str,
        context_chunks: list[ContextChunk],
    ) -> str | None:
        """Answer person -> technology-area lookups without asking the LLM to infer.

        This prevents small/free LLMs from adding nearby table rows or dropping valid rows
        when the evidence already exists as trusted table_row/entity_profile metadata.
        """

        membership_query = analyze_person_area_membership_query(query)
        person_name = RagAnswerService._person_area_query_person(query)
        if person_name is None and membership_query is not None:
            person_name = membership_query.person_candidate
        if person_name is None:
            return None

        person_key = normalize_metadata_value(person_name)
        rows: list[dict[str, str]] = []
        seen: dict[tuple[str, str], int] = {}

        def add_row(
            *,
            area: object,
            department: object = "",
            stt: object = "",
            citation: int,
            role_note: object = "",
        ) -> None:
            area_text = str(area or "").strip()
            if not area_text:
                return
            department_text = str(department or "").strip()
            stt_text = str(stt or "").strip()
            key = (
                normalize_metadata_value(area_text),
                normalize_metadata_value(department_text),
            )
            if key in seen:
                existing = rows[seen[key]]
                if not existing.get("stt") and stt_text:
                    existing["stt"] = stt_text
                    existing["citation"] = str(citation)
                if not existing.get("role_note") and str(role_note or "").strip():
                    existing["role_note"] = str(role_note or "").strip()
                return
            seen[key] = len(rows)
            rows.append(
                {
                    "area": area_text,
                    "department": department_text,
                    "stt": stt_text,
                    "citation": str(citation),
                    "role_note": str(role_note or "").strip(),
                }
            )

        for context_chunk in context_chunks:
            metadata = context_chunk.chunk.chunk_metadata or {}
            chunk_type = str(metadata.get("chunk_type") or "")

            if chunk_type == "entity_profile":
                metadata_person = str(
                    metadata.get("person_name") or metadata.get("entity_name") or ""
                ).strip()
                handled_profile = False
                if metadata_person and RagAnswerService._person_name_matches(
                    metadata_person,
                    person_name,
                ):
                    for area_payload in metadata.get("areas", []) or []:
                        if not isinstance(area_payload, dict):
                            continue
                        add_row(
                            area=area_payload.get("area"),
                            department=area_payload.get("lead_department"),
                            stt=area_payload.get("stt"),
                            role_note=area_payload.get("role_note"),
                            citation=context_chunk.citation_index,
                        )
                    handled_profile = True
                else:
                    for parsed in RagAnswerService._parse_person_area_profile_text(
                        context_chunk.chunk.content or "",
                        person_name=person_name,
                        citation=context_chunk.citation_index,
                    ):
                        add_row(**parsed)
                        handled_profile = True
                if handled_profile:
                    continue
                continue

            if is_trusted_relationship_metadata(metadata):
                staff_names = [
                    str(item.get("name") or "")
                    for item in metadata.get("staff", []) or []
                    if isinstance(item, dict)
                ] or [str(name) for name in metadata.get("staff_names", []) or []]
                if not any(
                    RagAnswerService._person_name_matches(name, person_name)
                    for name in staff_names
                ):
                    continue
                role_note = ""
                for staff in metadata.get("staff", []) or []:
                    if not isinstance(staff, dict):
                        continue
                    if RagAnswerService._person_name_matches(
                        str(staff.get("name") or ""),
                        person_name,
                    ):
                        role_note = str(staff.get("role_note") or "").strip()
                        break
                add_row(
                    area=metadata.get("area"),
                    department=metadata.get("lead_department"),
                    stt=metadata.get("stt"),
                    role_note=role_note,
                    citation=context_chunk.citation_index,
                )
                continue

            # Fallback for older table chunks that have TABLE_ROW text but no trusted metadata.
            for line in RagAnswerService._table_context_lines(context_chunk.chunk.content or ""):
                if "table_row" not in line.casefold():
                    continue
                if person_key not in normalize_metadata_value(line):
                    continue
                parsed = RagAnswerService._parse_table_row_line_for_area(line)
                if parsed is None:
                    continue
                add_row(citation=context_chunk.citation_index, **parsed)

            # Fallback for canonical relationship table blocks generated as plain
            # text by parsers, e.g. repeated blocks of:
            # STT / Mảng công nghệ / Phòng chủ trì / Nhân sự đề xuất.
            # These blocks are direct row evidence, but older indexes may not have
            # chunk_type=table_row or entity_profile metadata yet.
            chunk_content = context_chunk.chunk.content or ""

            for parsed in RagAnswerService._parse_canonical_person_area_blocks(
                chunk_content,
                person_key=person_key,
            ):
                add_row(citation=context_chunk.citation_index, **parsed)

            for parsed in RagAnswerService._parse_pipe_person_area_rows(
                chunk_content,
                person_key=person_key,
            ):
                add_row(citation=context_chunk.citation_index, **parsed)

        if not rows:
            return None

        rows.sort(key=lambda item: RagAnswerService._natural_stt_key(item.get("stt") or ""))
        if (
            membership_query is not None
            and not RagAnswerService._is_generic_person_area_candidate(
                membership_query.area_candidate
            )
        ):
            matched_rows = [
                row
                for row in rows
                if RagAnswerService._person_area_matches(
                    row.get("area") or "",
                    membership_query.area_candidate or "",
                )
            ]
            if matched_rows:
                matched = matched_rows[0]
                dept = (
                    f" — Phòng chủ trì: {matched['department']}"
                    if matched.get("department")
                    else ""
                )
                lines = [
                    (
                        f"Đúng, {person_name} được đề xuất tham gia mảng công nghệ "
                        f"{matched['area']}{dept}. [{matched['citation']}]"
                    )
                ]
                other_rows = [row for row in rows if row not in matched_rows]
                if other_rows:
                    lines.append("Ngoài ra, nhân sự này còn được đề xuất ở các mảng:")
                    for row in other_rows:
                        other_dept = (
                            f" — Phòng chủ trì: {row['department']}"
                            if row.get("department")
                            else ""
                        )
                        lines.append(f"- {row['area']}{other_dept}. [{row['citation']}]")
                return "\n".join(lines)

            known_areas = "; ".join(row["area"] for row in rows if row.get("area"))
            return (
                f"Chưa thấy dòng/bản ghi trực tiếp xác nhận {person_name} tham gia "
                f"mảng {membership_query.area_candidate}. "
                f"Các mảng tìm thấy cho nhân sự này là: {known_areas}."
            ).strip()

        count_text = f"{len(rows):02d}" if len(rows) < 10 else str(len(rows))
        lines = [f"{person_name} được đề xuất tham gia {count_text} mảng công nghệ:"]
        for index, row in enumerate(rows, start=1):
            dept = f" — Phòng chủ trì: {row['department']}" if row.get("department") else ""
            note = f"; ghi chú: {row['role_note']}" if row.get("role_note") else ""
            stt = f"STT {row['stt']}: " if row.get("stt") else ""
            lines.append(f"{index}. {stt}{row['area']}{dept}{note}. [{row['citation']}]")
        lines.append("Thông tin trên được tổng hợp từ các bản ghi nhân sự trong tài liệu.")
        return "\n".join(lines)


    @staticmethod
    def _insufficient_person_area_answer(query: str) -> str:
        person_name = RagAnswerService._person_area_query_person(query) or "nhân sự này"
        return (
            f"Chưa đủ căn cứ trực tiếp trong các dòng/bản ghi đã truy xuất để xác định "
            f"{person_name} tham gia những mảng công nghệ nào. "
            "Tôi không suy luận từ các dòng lân cận; hãy re-index tài liệu hoặc kiểm tra "
            "rằng các TABLE_ROW/entity_profile của bảng nhân sự đã được tạo đầy đủ."
        )


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
    def _parse_canonical_person_area_blocks(
        content: str,
        *,
        person_key: str,
    ) -> list[dict[str, str]]:
        """Extract person-area rows from explicit canonical table-block text.

        This is intentionally narrow: it only reads blocks that contain the
        original labels emitted by the table relationship parser. It does not use
        neighboring prose or loose row adjacency, so it remains grounded while
        supporting older indexes that missed per-row metadata.
        """

        if not content or not person_key:
            return []
        if person_key not in normalize_metadata_value(content):
            return []
        if not re.search(r"Mảng công nghệ\s*:", content, flags=re.IGNORECASE):
            return []
        if not re.search(r"Nhân sự đề xuất\s*:", content, flags=re.IGNORECASE):
            return []

        blocks = re.split(r"(?=^\s*STT\s*:)", content, flags=re.IGNORECASE | re.MULTILINE)
        rows: list[dict[str, str]] = []
        for block in blocks:
            if person_key not in normalize_metadata_value(block):
                continue
            stt = RagAnswerService._extract_labeled_value(block, "STT")
            area = RagAnswerService._extract_labeled_value(block, "Mảng công nghệ")
            department = RagAnswerService._extract_labeled_value(block, "Phòng chủ trì")
            staff = RagAnswerService._extract_labeled_value(block, "Nhân sự đề xuất")
            if not area or not staff:
                continue
            if person_key not in normalize_metadata_value(staff):
                continue
            rows.append({"area": area, "department": department or "", "stt": stt or ""})
        return rows

    @staticmethod
    def _parse_pipe_person_area_rows(
        content: str,
        *,
        person_key: str,
    ) -> list[dict[str, str]]:
        """Extract person-area rows from pipe-delimited table text.

        Some parsers keep the source table as plain text using ``|`` separators,
        for example ``| STT | Mảng công nghệ | Phòng chủ trì | Nhân sự đề xuất |``.
        These rows are direct table evidence even when no per-row metadata exists.
        """

        if not content or not person_key:
            return []
        normalized_content = normalize_metadata_value(content)
        if person_key not in normalized_content:
            return []
        if "|" not in content:
            return []

        rows: list[dict[str, str]] = []
        header_map: dict[str, int] = {}
        for raw_line in content.splitlines():
            line = raw_line.strip()
            if not line.startswith("|") or line.count("|") < 4:
                continue

            cells = [cell.strip() for cell in line.strip("|").split("|")]
            if not cells:
                continue
            normalized_cells = [normalize_metadata_value(cell) for cell in cells]

            # Separator rows such as |---|---|---|---| are not data.
            if all(re.fullmatch(r":?-{3,}:?", cell.replace(" ", "")) for cell in cells):
                continue

            if (
                "stt" in normalized_cells
                and "mang cong nghe" in normalized_cells
                and "nhan su de xuat" in normalized_cells
            ):
                header_map = {name: index for index, name in enumerate(normalized_cells)}
                continue

            if not header_map:
                continue
            if person_key not in normalize_metadata_value(" ".join(cells)):
                continue

            area_index = header_map.get("mang cong nghe")
            staff_index = header_map.get("nhan su de xuat")
            if area_index is None or staff_index is None:
                continue
            if area_index >= len(cells) or staff_index >= len(cells):
                continue

            staff = cells[staff_index]
            if person_key not in normalize_metadata_value(staff):
                continue

            department = ""
            department_index = header_map.get("phong chu tri")
            if department_index is not None and department_index < len(cells):
                department = cells[department_index]

            stt = ""
            stt_index = header_map.get("stt")
            if stt_index is not None and stt_index < len(cells):
                stt = cells[stt_index]

            rows.append(
                {
                    "area": cells[area_index],
                    "department": department,
                    "stt": stt,
                }
            )
        return rows

    @staticmethod
    def _extract_labeled_value(content: str, label: str) -> str:
        labels = ("STT", "Mảng công nghệ", "Phòng chủ trì", "Nhân sự đề xuất")
        next_labels = [candidate for candidate in labels if candidate != label]
        next_pattern = "|".join(re.escape(candidate) for candidate in next_labels)
        pattern = re.compile(
            rf"{re.escape(label)}\s*:\s*(?P<value>.*?)(?=^\s*(?:{next_pattern})\s*:|\Z)",
            flags=re.IGNORECASE | re.DOTALL | re.MULTILINE,
        )
        match = pattern.search(content)
        if match is None:
            return ""
        return " ".join(match.group("value").split()).strip(" -;:|/")

    @staticmethod
    def _parse_table_row_line_for_area(line: str) -> dict[str, str] | None:
        fields: dict[str, str] = {}
        row_match = re.search(r"\brow=(?P<row>\d+)\b", line, flags=re.IGNORECASE)
        for part in line.split("|"):
            label, separator, value = part.partition(":")
            if not separator:
                continue
            fields[normalize_metadata_value(label)] = value.strip()

        area = (
            fields.get("mang cong nghe")
            or fields.get("nhom nhiem vu")
            or fields.get("ten mang")
            or fields.get("cell_2")
        )
        department = (
            fields.get("phong chu tri")
            or fields.get("don vi")
            or fields.get("phong")
            or fields.get("cell_3")
        )
        stt = fields.get("stt") or fields.get("cell_1") or (row_match.group("row") if row_match else "")
        if not area:
            return None
        return {"area": area, "department": department or "", "stt": stt or ""}

    @staticmethod
    def _natural_stt_key(value: str) -> tuple[int, str]:
        match = re.search(r"\d+", value or "")
        if match:
            return (int(match.group(0)), value)
        return (10_000, value or "")

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

        normalized_tokens = [
            token
            for token in (
                normalize_metadata_value(RagAnswerService._strip_vietnamese_accents(word))
                for word in words
            )
            if len(token) >= 2
        ]

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
        content = normalize_metadata_value(
            RagAnswerService._strip_vietnamese_accents(chunk.content or "")
        )
        if not content:
            return False

        matched = 0
        for term in query_terms:
            term_key = normalize_metadata_value(
                RagAnswerService._strip_vietnamese_accents(term)
            )
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
        metadata_key = normalize_metadata_value(
            RagAnswerService._strip_vietnamese_accents(metadata_text)
        )
        return any(
            normalize_metadata_value(RagAnswerService._strip_vietnamese_accents(term))
            and normalize_metadata_value(RagAnswerService._strip_vietnamese_accents(term)) in metadata_key
            for term in query_terms
        )

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
    ) -> list[Chunk]:
        matches: list[Chunk] = []
        supporting: list[Chunk] = []
        for chunk in chunks:
            metadata = chunk.chunk_metadata or {}
            chunk_type = str(metadata.get("chunk_type") or "")
            content = chunk.content.casefold()
            if any(term.casefold() in content for term in query_terms):
                matches.append(chunk)
            elif chunk_type in {"entity_summary", "table_block"}:
                supporting.append(chunk)

        ordered: list[Chunk] = []
        seen_ids: set[UUID] = set()
        for chunk in [*matches, *supporting]:
            if chunk.id in seen_ids:
                continue
            seen_ids.add(chunk.id)
            ordered.append(chunk)
        return ordered or chunks

    @staticmethod
    def _filter_identifier_context(
        *,
        query: str,
        context_chunks: list[ContextChunk],
    ) -> list[ContextChunk]:
        """For code-only lookups, keep only chunks that literally contain the code.

        This prevents a query like ``3113`` from carrying topical neighbor chunks into
        the LLM prompt, which previously caused unrelated summaries and hallucinated
        "related cases" sections.
        """

        if not is_identifier_lookup_query(query):
            return context_chunks

        normalized_query = " ".join((query or "").split()).strip(" ?!.,;:").casefold()
        if not normalized_query:
            return context_chunks

        exact_chunks = [
            context_chunk
            for context_chunk in context_chunks
            if normalized_query in (context_chunk.chunk.content or "").casefold()
        ]
        if not exact_chunks:
            return context_chunks
        return [
            replace(context_chunk, citation_index=index)
            for index, context_chunk in enumerate(exact_chunks, start=1)
        ]

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

        return [
            replace(context_chunk, citation_index=index)
            for index, context_chunk in enumerate(deduplicated, start=1)
        ]

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
            ("Chủ đề trước", session_context.last_topic),
            ("Phạm vi hiện tại", session_context.current_scope),
            ("Phạm vi người dùng", session_context.user_scope),
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

        return (
            f"{query}\n\n"
            "Ngữ cảnh hội thoại ngắn hạn do chatbot cung cấp để hỗ trợ truy xuất, "
            "không phải nguồn trích dẫn:\n"
            + "\n".join(f"- {hint}" for hint in hints[:8])
        )

    @staticmethod
    def _short_term_context_section(
        *,
        session_context: RagSessionContext | None = None,
    ) -> str | None:
        """Render chatbot-supplied short-term context for prompting.

        This context can help interpret follow-up questions, but retrieved
        document chunks remain the only citable source of truth.
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

        return (
            "Short-term chatbot context (for query understanding only; "
            "do not cite it; retrieved document context wins if there is any conflict):\n"
            + "\n".join(lines[:10])
        )

    @staticmethod
    def _identifier_values_from_context(context_chunks: list[ContextChunk]) -> list[str]:
        """Collect exact identifier strings from retrieved chunks for prompt constraints.

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
            for match in re.findall(r"\b\d{2,6}/[A-ZÀ-Ỹ0-9Đ\-]+\b", content):
                add(match)
            for match in re.findall(r"\b\d{1,2}/\d{1,2}/\d{4}\b", content):
                add(match)

        return values[:20]

    @staticmethod
    def _build_user_prompt(
        *,
        query: str,
        context_chunks: list[ContextChunk],
        memory_context: list[MemoryResult] | None = None,
        session_summary: str | None = None,
        session_context: RagSessionContext | None = None,
    ) -> str:
        sections: list[str] = []

        session_context_section = RagAnswerService._short_term_context_section(
            session_context=session_context
        )
        if session_context_section:
            sections.append(session_context_section)

        if memory_context:
            memory_lines = "\n".join(
                f"- ({memory.memory_type}) {memory.content}" for memory in memory_context
            )
            sections.append(f"User Memory:\n{memory_lines}")

        if session_summary:
            sections.append(f"Session Summary:\n{session_summary}")

        query_terms = RagAnswerService._query_terms(query)
        identifier_lookup = is_identifier_lookup_query(query)
        if identifier_lookup:
            exact_chunks = [
                context_chunk
                for context_chunk in context_chunks
                if str((context_chunk.chunk.chunk_metadata or {}).get("identifier_exact_boost") or "0") != "0"
                or query.strip().casefold() in (context_chunk.chunk.content or "").casefold()
            ]
            if exact_chunks:
                context_chunks = exact_chunks
        if identifier_lookup:
            exact_values = RagAnswerService._identifier_values_from_context(context_chunks)
            if exact_values:
                exact_value_lines = "\n".join(f"  - {value}" for value in exact_values) or "  - Không có mã định danh chính xác trong metadata; hãy dùng nguyên văn mã xuất hiện trong ngữ cảnh."

                sections.append(
                    "Identifier lookup constraints:\n"
                    f"- User query is an identifier/code lookup: {query.strip()}\n"
                    "- Use only the retrieved chunks below that contain the exact identifier.\n"
                    "- Copy these exact identifier/document-code strings exactly; do not rewrite, abbreviate, translate, normalize, or guess them:\n"
                    f"{exact_value_lines}\n"
                    "- If the user only enters a short number/code, answer in this structure when possible:\n"
                    f"  1) Identify the code/document: 'Số {query.strip()} (cụ thể là văn bản số {{exact_doc_code}}) là văn bản do {{issuing_org}} ban hành ngày {{exact_date}} về {{subject}}.'\n"
                    "  2) State the main subject of that referenced document.\n"
                    "  3) Explain that the current EVNICT document uses that referenced document as the basis for its notification.\n"
                    "  4) Summarize the implementation details from the current document, such as app download links, feature updates, CMS updates, and update mechanism.\n"
                    "- For short identifier/code queries, do not answer too briefly if the retrieved context contains implementation details.\n"
                    "- It is acceptable to use bullets for implementation details when the context contains multiple items.\n"
                    f"- Prefer the phrase 'Số {query.strip()} (cụ thể là văn bản số ...)' or '{query.strip()} là một phần trong số hiệu văn bản ...' for short numeric/code queries.\n"
                    f"- Do not start the answer with 'Văn bản {query.strip()}' when the query is only a short number/code.\n"
                    "- Clearly distinguish the referenced EVN document from the current EVNICT notification document.\n"
                    "- If the context says 'Căn cứ văn bản số ...', say that the current document uses it as a basis; do not imply the referenced document was issued by the current document's issuing unit.\n"
                    "- Do not use vague or unsupported phrases such as 'căn cứ tham chiếu', 'số thứ tự', 'có nội dung chính thức đề', 'phê duyệt', or similar wording unless the context explicitly says so.\n"
                    "- Do not add related documents, related systems, or inferred approval/issuer details unless explicitly stated in the retrieved chunks.\n"
                    "- Keep the answer in Vietnamese and grounded in the retrieved context.\n"
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

        document_context_chunks = context_chunks
        if matched_rows:
            document_context_chunks = [
                context_chunk
                for context_chunk in context_chunks
                if not RagAnswerService._is_structured_result_context_chunk(
                    context_chunk,
                    query_terms=query_terms,
                    include_all_table_rows=include_all_table_rows,
                )
            ]

        context = "\n".join(
            f"[{context_chunk.citation_index}] {context_chunk.chunk.content}"
            for context_chunk in document_context_chunks
        )
        if context:
            sections.append(
                "Retrieved Document Context:\n"
                f"{context}\n\n"
                "Context use rule: ENTITY_MATCHED_ROWS are structured candidate rows. "
                "Retrieved Document Context is the broader evidence set and may contain "
                "the directly relevant narrative section, objective, definition, "
                "condition, or explanation. Choose the evidence that directly answers "
                "the question; do not ignore narrative context just because table rows "
                "are present. If the directly relevant narrative chunk contains a "
                "summary followed by list items in the same section, preserve those "
                "items as focused bullets instead of collapsing the answer to one "
                "short sentence."
            )

        sections.append(f"Question:\n{query}")
        return "\n\n".join(sections)

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
                if is_result_row and (
                    include_all_table_rows
                    or RagAnswerService._contains_any_query_term(line, query_terms)
                ):
                    if formatted not in seen_rows:
                        seen_rows.add(formatted)
                        matched_rows.append(formatted)
                    continue

                is_support_line = (
                    "table_title" in normalized_line
                    or "table_header" in normalized_line
                    or "table_caption" in normalized_line
                    or chunk_type in {"table_title", "table_header", "table_caption"}
                )
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
            is_support_line = (
                "table_title" in normalized_line
                or "table_header" in normalized_line
                or "table_caption" in normalized_line
                or chunk_type in support_chunk_types
            )

            if is_result_row and (
                include_all_table_rows
                or RagAnswerService._contains_any_query_term(line, query_terms)
            ):
                has_result_line = True
                continue
            if is_support_line:
                continue
            if line.strip():
                has_narrative_line = True

        return has_result_line and not has_narrative_line

    @staticmethod
    def _table_context_lines(content: str) -> list[str]:
        marker_pattern = re.compile(
            r"\s+(?=(?:TABLE_TITLE|TABLE_CAPTION|TABLE_HEADER|TABLE_ROW)\b)"
        )
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
    def _structured_evidence_can_answer_directly(evidence: Any) -> bool:
        """Return True only when a structured row contains an answer value.

        Matching an identifier-like field such as a table row title, staff name,
        area, department, or page is not enough to produce a deterministic answer.
        Those rows are still useful as retrieval context, but the final answer
        should be generated by the normal RAG prompt so nearby narrative/section
        chunks can answer descriptive questions such as goals, scope, or purpose.
        """

        fields = getattr(getattr(evidence, "row", None), "fields", {}) or {}
        answer_field_keys = {
            "answer",
            "description",
            "definition",
            "goal",
            "objective",
            "purpose",
            "summary",
            "value",
            "measure_value",
            "measure_text",
            "total_benefit",
            "total_days",
            "total_leave_benefit",
            "total_leave_days",
            "base_benefit",
            "additional_benefit",
            "labor_code_benefit",
            "collective_agreement_benefit",
            "condition",
            "conditions",
        }
        for key in answer_field_keys:
            value = fields.get(key)
            if value not in (None, "", [], {}):
                return True
        return False


    @staticmethod
    def _is_narrative_chunk(chunk: Chunk) -> bool:
        metadata = getattr(chunk, "chunk_metadata", None) or {}
        chunk_type = str(metadata.get("chunk_type") or "")
        structured_types = {
            "entity_profile",
            "legal_table_row",
            "structured_fact_row",
            "table_block",
            "table_complete",
            "table_header",
            "table_row",
            "table_rows",
            "table_title",
        }
        return chunk_type not in structured_types

    @staticmethod
    def _section_text_lines(content: str) -> list[str]:
        return [line.strip() for line in (content or "").splitlines() if line.strip()]

    @staticmethod
    def _is_section_list_item(line: str) -> bool:
        return bool(re.match(r"^\s*(?:[-*•‣◦]\s+|\d{1,3}[\.)]\s+)", line))

    @staticmethod
    def _strip_section_list_marker(line: str) -> str:
        return re.sub(r"^\s*(?:[-*•‣◦]\s+|\d{1,3}[\.)]\s+)", "", line).strip()

    @staticmethod
    def _clean_section_heading(line: str) -> str:
        heading = re.sub(r"^\s*#+\s*", "", line or "").strip()
        heading = re.sub(r"^\s*\d+(?:\.\d+)*\s*[.)-]?\s*", "", heading).strip()
        return heading.strip(" -*•‣◦")

    @staticmethod
    def _clean_section_summary(line: str) -> str:
        summary = " ".join((line or "").split()).strip(" .;:")
        if ":" in summary:
            label, value = summary.split(":", 1)
            if 0 < len(label.split()) <= 8 and value.strip():
                summary = value.strip(" .;:")
        return summary

    @staticmethod
    def _ensure_sentence(text: str) -> str:
        text = " ".join((text or "").split()).strip()
        if not text:
            return text
        if text[-1] in ".!?…":
            return text
        return f"{text}."

    @staticmethod
    def _narrative_section_score(query: str, *, heading: str, content: str) -> float:
        query_tokens = set(re.findall(r"[a-z0-9]+", normalize_metadata_value(query)))
        if not query_tokens:
            return 0.0
        heading_tokens = set(re.findall(r"[a-z0-9]+", normalize_metadata_value(heading)))
        content_tokens = set(re.findall(r"[a-z0-9]+", normalize_metadata_value(content)))
        heading_overlap = query_tokens & heading_tokens
        content_overlap = query_tokens & content_tokens
        score = 0.0
        if heading_tokens:
            score += 2.0 * (len(heading_overlap) / max(len(heading_tokens), 1))
        score += len(content_overlap) / max(len(query_tokens), 1)
        normalized_query = normalize_metadata_value(query)
        normalized_heading = normalize_metadata_value(heading)
        if normalized_heading and normalized_heading in normalized_query:
            score += 1.0
        elif normalized_query and normalized_query in normalized_heading:
            score += 0.75
        return score

    @staticmethod
    def _deterministic_narrative_section_answer(
        *,
        query: str,
        context_chunks: list[ContextChunk],
    ) -> str | None:
        """Render a complete narrative section when the evidence is self-contained.

        This is intentionally schema-free and language-agnostic at detection time:
        it does not look for labels such as a concrete policy, topic, or Vietnamese
        keyword. It only uses section structure: a heading, one or more summary
        lines, and several list items in the same retrieved chunk.
        """

        candidates: list[tuple[float, ContextChunk, str, str, list[str]]] = []
        for context_chunk in context_chunks:
            chunk = context_chunk.chunk
            if not RagAnswerService._is_narrative_chunk(chunk):
                continue
            lines = RagAnswerService._section_text_lines(chunk.content or "")
            if len(lines) < 4:
                continue

            heading = RagAnswerService._clean_section_heading(lines[0])
            if not heading:
                continue

            bullet_lines = [
                RagAnswerService._strip_section_list_marker(line)
                for line in lines[1:]
                if RagAnswerService._is_section_list_item(line)
            ]
            bullet_lines = [line for line in bullet_lines if line]
            if len(bullet_lines) < 2:
                continue

            summary_parts: list[str] = []
            for line in lines[1:]:
                if RagAnswerService._is_section_list_item(line):
                    break
                cleaned = RagAnswerService._clean_section_summary(line)
                if cleaned:
                    summary_parts.append(cleaned)
                if len(summary_parts) >= 2:
                    break
            if not summary_parts:
                continue

            score = RagAnswerService._narrative_section_score(
                query,
                heading=heading,
                content=chunk.content or "",
            )
            if score < 0.6:
                continue
            candidates.append(
                (
                    score,
                    context_chunk,
                    heading,
                    " ".join(summary_parts),
                    bullet_lines[:8],
                )
            )

        if not candidates:
            return None

        candidates.sort(key=lambda item: item[0], reverse=True)
        _, context_chunk, heading, summary, bullet_lines = candidates[0]
        citation = context_chunk.citation_index
        lines = [
            (
                f"**{heading}** có nội dung chính: "
                f"{RagAnswerService._ensure_sentence(summary)} [{citation}]"
            ),
            "",
            "Các nội dung trọng tâm:",
        ]
        lines.extend(
            f"- {RagAnswerService._ensure_sentence(item)} [{citation}]"
            for item in bullet_lines
        )
        return "\n".join(lines).strip()

    @staticmethod
    def _deterministic_structured_fact_answer(
        *,
        query: str,
        context_chunks: list[ContextChunk],
    ) -> str | None:
        evidences = collect_structured_evidence(
            query=query,
            context_chunks=context_chunks,
            min_score=0.25,
        )
        direct_evidences = [
            evidence
            for evidence in evidences
            if RagAnswerService._structured_evidence_can_answer_directly(evidence)
        ]
        if not direct_evidences:
            return None
        return render_structured_answer(
            query=query,
            evidences=direct_evidences,
        )
