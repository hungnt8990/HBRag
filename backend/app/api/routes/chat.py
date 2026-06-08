import json
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import get_current_user
from app.api.routes.search import get_reranking_service
from app.core.config import settings
from app.db.session import get_db_session
from app.models.user import User
from app.repositories.auth import AuthRepository
from app.repositories.chat import ChatRepository
from app.repositories.document_logs import DocumentLogRepository
from app.repositories.documents import DocumentRepository
from app.repositories.memory import MemoryRepository
from app.schemas.chat import RagChatRequest, RagChatResponse, RagChatStreamRequest
from app.services.document_profiles import FALLBACK_CONFIG, profile_config, resolve_profile
from app.services.llms import LLMProvider
from app.services.llms.factory import get_llm_provider
from app.services.memory import MemoryResult, build_memory_provider
from app.services.memory.memory_service import maybe_auto_save_memory
from app.services.permissions import can_view_document
from app.services.rag_answer_service import (
    ChatSessionNotFoundError,
    RagAnswerError,
    RagAnswerService,
    RagStreamEvent,
)
from app.services.reranking_service import RerankingService

router = APIRouter(prefix="/api/chat", tags=["chat"])


def get_chat_repository(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ChatRepository:
    return ChatRepository(session)


def get_memory_repository(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> MemoryRepository:
    return MemoryRepository(session)


def get_document_repository(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> DocumentRepository:
    return DocumentRepository(session)


def get_auth_repository(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> AuthRepository:
    return AuthRepository(session)


def get_document_log_repository(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> DocumentLogRepository:
    return DocumentLogRepository(session)


def get_rag_answer_service(
    chat_repository: Annotated[ChatRepository, Depends(get_chat_repository)],
    reranking_service: Annotated[RerankingService, Depends(get_reranking_service)],
    llm_provider: Annotated[LLMProvider, Depends(get_llm_provider)],
    document_log_repository: Annotated[
        DocumentLogRepository,
        Depends(get_document_log_repository),
    ],
) -> RagAnswerService:
    return RagAnswerService(
        chat_repository=chat_repository,
        reranking_service=reranking_service,
        llm_provider=llm_provider,
        document_log_repository=document_log_repository,
    )


@router.post("/rag", response_model=RagChatResponse)
async def rag_chat(
    request: RagChatRequest,
    repository: Annotated[DocumentRepository, Depends(get_document_repository)],
    auth_repository: Annotated[AuthRepository, Depends(get_auth_repository)],
    memory_repository: Annotated[MemoryRepository, Depends(get_memory_repository)],
    current_user: Annotated[User, Depends(get_current_user)],
    service: Annotated[RagAnswerService, Depends(get_rag_answer_service)],
) -> RagChatResponse:
    try:
        visible_document_ids = await _visible_document_ids(
            repository=repository,
            auth_repository=auth_repository,
            current_user=current_user,
            document_id=request.document_id,
            organization_id=request.organization_id,
            include_descendants=request.include_descendants,
        )
        memory_context, session_summary = await _gather_memory(
            memory_repository=memory_repository,
            current_user=current_user,
            query=request.query,
            session_id=request.session_id,
            use_memory=request.use_memory,
            use_mem0=request.use_mem0,
            memory_top_k=request.memory_top_k,
        )
        resolved = await _resolve_profile_settings(
            repository=repository,
            profile=request.profile,
            document_id=request.document_id,
            top_k=request.top_k,
            candidate_k=request.candidate_k,
            answer_mode=request.answer_mode,
            answer_style=request.answer_style,
            max_context_chars=request.max_context_chars,
        )
        try:
            response = await service.answer(
                query=request.query,
                session_id=request.session_id,
                top_k=resolved["top_k"],
                candidate_k=resolved["candidate_k"],
                current_user=current_user,
                document_ids=visible_document_ids,
                memory_context=memory_context,
                session_summary=session_summary,
                answer_mode=resolved["answer_mode"],
                answer_style=resolved["answer_style"],
                max_context_chars=resolved["max_context_chars"],
                use_graph=request.use_graph,
                graph_expansion_depth=request.graph_expansion_depth,
                graph_expansion_limit=request.graph_expansion_limit,
            )
        except TypeError as exc:
            if "unexpected keyword argument" not in str(exc):
                raise
            response = await service.answer(
                query=request.query,
                session_id=request.session_id,
                top_k=resolved["top_k"],
                candidate_k=resolved["candidate_k"],
            )
        await _auto_save(
            memory_repository=memory_repository,
            current_user=current_user,
            message=request.query,
            session_id=response.session_id,
            use_memory=request.use_memory,
            use_mem0=request.use_mem0,
        )
        return response
    except ChatSessionNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except RagAnswerError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc


async def _resolve_profile_settings(
    *,
    repository: DocumentRepository,
    profile: str | None,
    document_id,
    top_k: int | None,
    candidate_k: int | None,
    answer_mode: str | None,
    answer_style: str | None,
    max_context_chars: int | None,
) -> dict:
    selected = profile
    parsed_text = None
    if selected is None and document_id is not None:
        get_document = getattr(repository, "get_document", None)
        if get_document is not None:
            try:
                document = await get_document(document_id)
            except Exception:
                document = None
            if document is not None:
                selected = getattr(document, "document_profile", None)
                parsed_text = getattr(document, "parsed_text", None)

    if selected is None:
        config = dict(FALLBACK_CONFIG)
    else:
        config = profile_config(resolve_profile(selected, text=parsed_text))

    resolved_top_k = top_k if top_k is not None else config["top_k"]
    resolved_candidate_k = candidate_k if candidate_k is not None else config["candidate_k"]
    if resolved_candidate_k < resolved_top_k:
        resolved_candidate_k = resolved_top_k

    return {
        "top_k": resolved_top_k,
        "candidate_k": resolved_candidate_k,
        "answer_mode": answer_mode if answer_mode is not None else config["answer_mode"],
        "answer_style": (
            answer_style if answer_style is not None else config["answer_style"]
        ),
        "max_context_chars": (
            max_context_chars
            if max_context_chars is not None
            else config["max_context_chars"]
        ),
    }


async def _gather_memory(
    *,
    memory_repository: MemoryRepository,
    current_user: User,
    query: str,
    session_id,
    use_memory: bool,
    use_mem0: bool,
    memory_top_k: int,
) -> tuple[list[MemoryResult], str | None]:
    if not (settings.memory_enabled and settings.memory_inject_into_prompt and use_memory):
        return [], None

    memory_context: list[MemoryResult] = []
    try:
        provider = build_memory_provider(memory_repository, use_mem0=use_mem0)
        memory_context = await provider.search_memory(
            user=current_user,
            query=query,
            limit=memory_top_k,
        )
    except Exception:
        memory_context = []
        try:
            await memory_repository.rollback()
        except Exception:
            pass

    session_summary = None
    if session_id is not None:
        try:
            summary_record = await memory_repository.get_session_summary(session_id=session_id)
            session_summary = summary_record.summary if summary_record else None
        except Exception:
            try:
                await memory_repository.rollback()
            except Exception:
                pass
    return memory_context, session_summary


async def _auto_save(
    *,
    memory_repository: MemoryRepository,
    current_user: User,
    message: str,
    session_id,
    use_memory: bool,
    use_mem0: bool,
) -> None:
    if not (settings.memory_enabled and settings.memory_auto_save and use_memory):
        return
    try:
        provider = build_memory_provider(memory_repository, use_mem0=use_mem0)
        await maybe_auto_save_memory(
            provider=provider,
            user=current_user,
            message=message,
            session_id=str(session_id) if session_id is not None else None,
        )
    except Exception:
        try:
            await memory_repository.rollback()
        except Exception:
            pass


def _format_sse_event(event: RagStreamEvent) -> str:
    return f"event: {event.event}\ndata: {json.dumps(event.data, ensure_ascii=False)}\n\n"


@router.post("/rag/stream")
async def rag_chat_stream(
    request: RagChatStreamRequest,
    repository: Annotated[DocumentRepository, Depends(get_document_repository)],
    auth_repository: Annotated[AuthRepository, Depends(get_auth_repository)],
    memory_repository: Annotated[MemoryRepository, Depends(get_memory_repository)],
    current_user: Annotated[User, Depends(get_current_user)],
    service: Annotated[RagAnswerService, Depends(get_rag_answer_service)],
) -> StreamingResponse:
    visible_document_ids = await _visible_document_ids(
        repository=repository,
        auth_repository=auth_repository,
        current_user=current_user,
        document_id=request.scope.document_id,
        organization_id=request.scope.organization_id,
        include_descendants=request.scope.include_descendants,
    )
    memory_context, session_summary = await _gather_memory(
        memory_repository=memory_repository,
        current_user=current_user,
        query=request.query,
        session_id=request.session_id,
        use_memory=request.use_memory,
        use_mem0=request.use_mem0,
        memory_top_k=request.memory_top_k,
    )
    resolved = await _resolve_profile_settings(
        repository=repository,
        profile=request.profile,
        document_id=request.scope.document_id,
        top_k=request.top_k,
        candidate_k=request.candidate_k,
        answer_mode=request.answer_mode,
        answer_style=request.answer_style,
        max_context_chars=request.max_context_chars,
    )

    async def event_stream():
        try:
            async for event in service.answer_stream(
                query=request.query,
                session_id=request.session_id,
                top_k=resolved["top_k"],
                candidate_k=resolved["candidate_k"],
                current_user=current_user,
                document_ids=visible_document_ids,
                memory_context=memory_context,
                session_summary=session_summary,
                answer_mode=resolved["answer_mode"],
                answer_style=resolved["answer_style"],
                max_context_chars=resolved["max_context_chars"],
                use_graph=request.use_graph,
                graph_expansion_depth=request.graph_expansion_depth,
                graph_expansion_limit=request.graph_expansion_limit,
            ):
                yield _format_sse_event(event)
        except ChatSessionNotFoundError:
            yield _format_sse_event(
                RagStreamEvent(event="error", data={"message": "Chat session not found."})
            )
            return
        except RagAnswerError:
            # answer_stream already emitted an error event before raising.
            return

        await _auto_save(
            memory_repository=memory_repository,
            current_user=current_user,
            message=request.query,
            session_id=request.session_id,
            use_memory=request.use_memory,
            use_mem0=request.use_mem0,
        )

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _visible_document_ids(
    *,
    repository: DocumentRepository,
    auth_repository: AuthRepository,
    current_user: User,
    document_id,
    organization_id,
    include_descendants: bool,
) -> set:
    descendant_ids = await auth_repository.get_descendant_organization_ids(
        current_user.organization_id
    )
    requested_org_ids = None
    if organization_id is not None:
        requested_org_ids = (
            await auth_repository.get_descendant_organization_ids(organization_id)
            if include_descendants
            else {organization_id}
        )
    documents = await repository.list_documents_for_permission_check()
    visible_ids = {
        document.id
        for document in documents
        if can_view_document(
            current_user,
            document,
            descendant_organization_ids=descendant_ids,
        )
    }
    if document_id is not None:
        visible_ids = {document_id} if document_id in visible_ids else set()
    if requested_org_ids is not None:
        visible_ids = {
            candidate_id
            for candidate_id in visible_ids
            for document in documents
            if document.id == candidate_id and document.organization_id in requested_org_ids
        }
    return visible_ids
