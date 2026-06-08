from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, replace
from typing import Any
from uuid import UUID

from app.models.chat import ChatSession
from app.models.chunk import Chunk
from app.models.user import User
from app.repositories.chat import ChatRepository, CitationCreate
from app.repositories.document_logs import DocumentLogRepository
from app.schemas.chat import RagChatResponse, RagCitationResponse
from app.schemas.documents import RerankSearchResult
from app.services.llms import LLMProvider
from app.services.memory.base import MemoryResult
from app.services.reranking_service import RerankingService

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
    "and legal wording from the retrieved context. "
    "2) Then list related cases that appear in the same article or table. "
    "3) Include notes and conditions if present. "
    "4) If table rows exist in the context, convert them into clear bullet points. "
    "5) Cite source chunks using their numeric markers. "
    "6) Use Vietnamese administrative style. "
    "Do not repeat the same source line or quote more than once. "
    "Do not list duplicate citations. "
    "If only one relevant rule is found, give one concise answer and one source. "
    "Do not invent information."
)

TABLE_QA_STYLE = (
    "Answer style: Table QA. "
    "You are a document QA assistant. Answer only from the provided context. "
    "If the context contains tables, synthesize information by row and column. "
    "Do not assume fixed column names. Use the original column names from context. "
    "Do not use legal/policy language if the document is not a legal/policy document. "
    "If multiple rows contain the same entity, list all related rows. "
    "For each row, show the most descriptive columns (exclude ordinal-only columns). "
    "Keep original column names so the user understands the data source. "
    "If there is not enough information, say so clearly. "
    "Do not invent information."
)

ANSWER_STYLE_INSTRUCTIONS = {
    "concise": CONCISE_STYLE,
    "detailed": DETAILED_STYLE,
    "policy_explainer": POLICY_EXPLAINER_STYLE,
    "table_qa": TABLE_QA_STYLE,
}
DEFAULT_ANSWER_STYLE = "policy_explainer"


def system_prompt_for_mode(answer_mode: str | None) -> str:
    if not answer_mode:
        return ANSWER_MODE_PROMPTS[DEFAULT_ANSWER_MODE]
    return ANSWER_MODE_PROMPTS.get(answer_mode.lower().strip(), HYBRID_PROMPT)


def build_system_prompt(
    *,
    answer_mode: str | None,
    answer_style: str | None,
) -> str:
    base = system_prompt_for_mode(answer_mode)
    style_key = (answer_style or DEFAULT_ANSWER_STYLE).lower().strip()
    style = ANSWER_STYLE_INSTRUCTIONS.get(style_key, POLICY_EXPLAINER_STYLE)
    return f"{base}\n\n{style}"


QUOTE_LIMIT = 500
SESSION_TITLE_LIMIT = 255


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
        memory_context: list[MemoryResult] | None = None,
        session_summary: str | None = None,
        answer_mode: str | None = None,
        answer_style: str | None = None,
        max_context_chars: int = 6000,
        use_graph: bool = False,
        graph_expansion_depth: int = 1,
        graph_expansion_limit: int = 20,
    ) -> RagChatResponse:
        try:
            chat_session = await self._get_or_create_session(query=query, session_id=session_id)
            user_message = await self._chat_repository.create_message(
                session_id=chat_session.id,
                role="user",
                content=query,
            )

            if document_ids is None:
                rerank_response = await self._reranking_service.search(
                    query=query,
                    top_k=top_k,
                    candidate_k=candidate_k,
                    session_id=chat_session.id,
                    use_graph=use_graph,
                    graph_expansion_depth=graph_expansion_depth,
                    graph_expansion_limit=graph_expansion_limit,
                )
            else:
                rerank_response = await self._reranking_service.search(
                    query=query,
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
                context_chunks=context_chunks,
                max_context_chars=max_context_chars,
            )
            context_chunks = self._deduplicate_context_chunks(context_chunks)
            user_prompt = self._build_user_prompt(
                query=query,
                context_chunks=context_chunks,
                memory_context=memory_context,
                session_summary=session_summary,
            )
            answer = await self._llm_provider.generate(
                system_prompt=build_system_prompt(
                    answer_mode=answer_mode,
                    answer_style=answer_style,
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
        memory_context: list[MemoryResult] | None = None,
        session_summary: str | None = None,
        answer_mode: str | None = None,
        answer_style: str | None = None,
        max_context_chars: int = 6000,
        use_graph: bool = False,
        graph_expansion_depth: int = 1,
        graph_expansion_limit: int = 20,
    ) -> AsyncIterator[RagStreamEvent]:
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
            if document_ids is None:
                rerank_response = await self._reranking_service.search(
                    query=query,
                    top_k=top_k,
                    candidate_k=candidate_k,
                    session_id=chat_session.id,
                    use_graph=use_graph,
                    graph_expansion_depth=graph_expansion_depth,
                    graph_expansion_limit=graph_expansion_limit,
                )
            else:
                rerank_response = await self._reranking_service.search(
                    query=query,
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
                context_chunks=context_chunks,
                max_context_chars=max_context_chars,
            )
            context_chunks = self._deduplicate_context_chunks(context_chunks)
            user_prompt = self._build_user_prompt(
                query=query,
                context_chunks=context_chunks,
                memory_context=memory_context,
                session_summary=session_summary,
            )

            yield RagStreamEvent(
                event="metadata",
                data={
                    "session_id": str(chat_session.id),
                    "user_message_id": str(user_message.id),
                },
            )

            answer_parts: list[str] = []
            async for delta in self._llm_provider.stream_generate(
                system_prompt=build_system_prompt(
                    answer_mode=answer_mode,
                    answer_style=answer_style,
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
            yield RagStreamEvent(
                event="error",
                data={"message": "Failed to generate RAG answer."},
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
        context_chunks: list[ContextChunk],
        max_context_chars: int,
    ) -> list[ContextChunk]:
        if not context_chunks:
            return context_chunks

        get_neighbors = getattr(self._chat_repository, "get_neighbor_chunks", None)
        if get_neighbors is None:
            return context_chunks

        existing_ids: set[UUID] = {context_chunk.chunk.id for context_chunk in context_chunks}
        seen_articles: set[tuple[UUID, str]] = set()
        total_chars = sum(len(item.chunk.content) for item in context_chunks)

        expanded = list(context_chunks)
        next_index = max((item.citation_index for item in context_chunks), default=0) + 1

        for context_chunk in context_chunks:
            metadata = context_chunk.chunk.chunk_metadata or {}
            article_number = metadata.get("article_number")
            if not article_number:
                continue

            key = (context_chunk.chunk.document_id, str(article_number))
            if key in seen_articles:
                continue
            seen_articles.add(key)

            try:
                neighbors = await get_neighbors(
                    document_id=context_chunk.chunk.document_id,
                    article_number=str(article_number),
                    exclude_ids=tuple(existing_ids),
                )
            except Exception:
                continue

            for neighbor in neighbors:
                if neighbor.id in existing_ids:
                    continue
                neighbor_len = len(neighbor.content or "")
                if total_chars + neighbor_len > max_context_chars:
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
    def _build_user_prompt(
        *,
        query: str,
        context_chunks: list[ContextChunk],
        memory_context: list[MemoryResult] | None = None,
        session_summary: str | None = None,
    ) -> str:
        sections: list[str] = []

        if memory_context:
            memory_lines = "\n".join(
                f"- ({memory.memory_type}) {memory.content}" for memory in memory_context
            )
            sections.append(f"User Memory:\n{memory_lines}")

        if session_summary:
            sections.append(f"Session Summary:\n{session_summary}")

        context = "\n".join(
            f"[{context_chunk.citation_index}] {context_chunk.chunk.content}"
            for context_chunk in context_chunks
        )
        sections.append(f"Retrieved Document Context:\n{context}")
        sections.append(f"Question:\n{query}")
        return "\n\n".join(sections)

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
        source_flags = list(context_chunk.source_flags or [context_chunk.source_type])

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
            metadata={
                **metadata,
                "source_type": context_chunk.source_type,
                "source_flags": source_flags,
            },
        )

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
