import inspect
import json
from typing import Annotated
from uuid import UUID

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
from app.repositories.knowledge_artifacts import KnowledgeArtifactRepository
from app.repositories.knowledge_bases import KnowledgeBaseRepository
from app.repositories.memory import MemoryRepository
from app.repositories.rag_runtime_config import RagRuntimeConfigRepository
from app.schemas.chat import RagChatRequest, RagChatResponse, RagChatStreamRequest
from app.services.security.security_access_control import build_access_filter, build_subject_context
from app.services.retrieval.retrieval_artifact_first_retrieval import ArtifactFirstRetrievalService
from app.services.documents.document_profiles import profile_config, resolve_profile
from app.services.embeddings.embedding_sparse_factory import get_sparse_embedding_provider
from app.services.knowledge.knowledge_artifact_indexing_service import KnowledgeArtifactIndexingService
from app.services.llm_gateway import LLMGateway, get_llm_gateway
from app.services.memory import MemoryResult, build_memory_provider
from app.services.memory.memory_service import maybe_auto_save_memory
from app.services.security.security_permissions import can_view_document, can_view_knowledge_base
from app.services.queries.query_contract_service import QueryContractService
from app.services.rag.rag_answer_service import (
    ChatSessionNotFoundError,
    RagAnswerError,
    RagAnswerService,
    RagStreamEvent,
)
from app.services.rag.rag_runtime_config import default_rag_runtime_config, load_rag_runtime_config
from app.services.rerankers.reranker_service import RerankingService
from app.services.vector.vector_store import get_artifact_vector_store

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

def get_knowledge_base_repository(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> KnowledgeBaseRepository:
    return KnowledgeBaseRepository(session)


def get_document_log_repository(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> DocumentLogRepository:
    return DocumentLogRepository(session)


def get_knowledge_artifact_repository(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> KnowledgeArtifactRepository:
    return KnowledgeArtifactRepository(session)


def get_rag_runtime_config_repository(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> RagRuntimeConfigRepository:
    return RagRuntimeConfigRepository(session)


async def get_rag_answer_service(
    chat_repository: Annotated[ChatRepository, Depends(get_chat_repository)],
    reranking_service: Annotated[RerankingService, Depends(get_reranking_service)],
    llm_provider: Annotated[LLMGateway, Depends(get_llm_gateway)],
    document_log_repository: Annotated[
        DocumentLogRepository,
        Depends(get_document_log_repository),
    ],
    artifact_repository: Annotated[
        KnowledgeArtifactRepository,
        Depends(get_knowledge_artifact_repository),
    ],
    rag_config_repository: Annotated[
        RagRuntimeConfigRepository,
        Depends(get_rag_runtime_config_repository),
    ],
) -> RagAnswerService:
    try:
        rag_config = await load_rag_runtime_config(rag_config_repository)
        await rag_config_repository.commit()
    except Exception:
        await rag_config_repository.rollback()
        rag_config = default_rag_runtime_config()
    artifact_indexing_service = KnowledgeArtifactIndexingService(
        repository=artifact_repository,
        llm_gateway=get_llm_gateway(),
        vector_store=get_artifact_vector_store(),
        sparse_embedding_provider=get_sparse_embedding_provider(),
    )
    artifact_first_retrieval_service = ArtifactFirstRetrievalService(
        artifact_repository=artifact_repository,
        artifact_indexing_service=artifact_indexing_service,
        reranking_service=reranking_service,
        query_contract_service=QueryContractService(),
        rag_config=rag_config,
    )
    return RagAnswerService(
        chat_repository=chat_repository,
        reranking_service=reranking_service,
        llm_provider=llm_provider,
        document_log_repository=document_log_repository,
        artifact_first_retrieval_service=artifact_first_retrieval_service,
    )


def _non_empty_uuid_set(values) -> set[UUID] | None:
    if values is None:
        return None
    result = {UUID(str(value)) for value in values if str(value).strip()}
    return result or None


def _apply_client_document_scope(
    visible_document_ids: set[UUID],
    allowed_document_ids,
) -> set[UUID]:
    """Intersect permissions with an explicit client scope only.

    The chatbot may narrow the scope with allowed_document_ids, but cannot widen
    permissions. Empty/missing lists are treated as no extra filter to avoid
    accidentally hiding all documents for unrelated follow-up questions.
    """

    requested_ids = _non_empty_uuid_set(allowed_document_ids)
    if requested_ids is None:
        return visible_document_ids
    return visible_document_ids & requested_ids


@router.post("/rag", response_model=RagChatResponse)
async def rag_chat(
    request: RagChatRequest,
    repository: Annotated[DocumentRepository, Depends(get_document_repository)],
    auth_repository: Annotated[AuthRepository, Depends(get_auth_repository)],
    knowledge_base_repository: Annotated[
        KnowledgeBaseRepository,
        Depends(get_knowledge_base_repository),
    ],
    memory_repository: Annotated[MemoryRepository, Depends(get_memory_repository)],
    current_user: Annotated[User, Depends(get_current_user)],
    service: Annotated[RagAnswerService, Depends(get_rag_answer_service)],
) -> RagChatResponse:
    try:
        visible_document_ids = await _visible_document_ids(
            repository=repository,
            auth_repository=auth_repository,
            knowledge_base_repository=knowledge_base_repository,
            current_user=current_user,
            document_id=request.document_id,
            organization_id=request.organization_id,
            knowledge_base_ids=request.knowledge_base_ids,
            include_descendants=request.include_descendants,
        )
        visible_document_ids = _apply_client_document_scope(
            visible_document_ids,
            request.session_context.allowed_document_ids if request.session_context else None,
        )
        subject_context = await _subject_context(
            auth_repository=auth_repository,
            current_user=current_user,
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
        settings_document_id = (
            request.document_id
            or (
                request.session_context.current_document_id
                if request.session_context is not None
                else None
            )
            or (next(iter(visible_document_ids)) if len(visible_document_ids) == 1 else None)
        )
        resolved = await _resolve_profile_settings(
            repository=repository,
            profile=request.profile,
            document_id=settings_document_id,
            top_k=request.top_k,
            candidate_k=request.candidate_k,
            answer_mode=request.answer_mode,
            answer_style=request.answer_style,
            max_context_chars=request.max_context_chars,
            retrieval_enrichment_enabled=request.retrieval_enrichment_enabled,
        )
        response = await _call_answer_service(
            service,
            query=request.query,
            session_id=request.session_id,
            top_k=resolved["top_k"],
            candidate_k=resolved["candidate_k"],
            current_user=current_user,
            document_ids=visible_document_ids,
            session_context=request.session_context,
            memory_context=memory_context,
            session_summary=session_summary,
            answer_mode=resolved["answer_mode"],
            answer_style=resolved["answer_style"],
            max_context_chars=resolved["max_context_chars"],
            use_graph=request.use_graph,
            graph_expansion_depth=request.graph_expansion_depth,
            graph_expansion_limit=request.graph_expansion_limit,
            access_filter=build_access_filter(subject_context),
            subject_context=subject_context,
            retrieval_enrichment_enabled=resolved["retrieval_enrichment_enabled"],
            query_intent_rules=resolved["query_intent_rules"],
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
    retrieval_enrichment_enabled: bool | None = None,
) -> dict:
    """Resolve runtime RAG settings automatically from the document profile.

    UI clients may omit profile/top_k/candidate_k/answer_mode/answer_style/
    max_context_chars. In that normal path, the backend chooses the saved
    document profile detected during ingestion, then falls back to config-driven
    auto-detection from parsed text/file hints, and finally to the general
    profile. Explicit client values still work as API-level overrides.
    """

    requested_profile = (profile or "auto").strip().lower()
    document = None
    parsed_text = None
    filename = None
    content_type = None
    saved_profile = None

    if document_id is not None:
        get_document = getattr(repository, "get_document", None)
        if get_document is not None:
            try:
                document = await get_document(document_id)
            except Exception:
                document = None
        if document is not None:
            parsed_text = getattr(document, "parsed_text", None)
            saved_profile = getattr(document, "document_profile", None)
            filename = getattr(document, "title", None)
            files = list(getattr(document, "files", None) or [])
            if files:
                filename = getattr(files[0], "filename", None) or filename
                content_type = getattr(files[0], "mime_type", None)

    if requested_profile == "auto":
        normalized_saved_profile = (saved_profile or "").strip().lower()
        if normalized_saved_profile and normalized_saved_profile != "auto":
            concrete_profile = resolve_profile(
                normalized_saved_profile,
                text=parsed_text,
                filename=filename,
                content_type=content_type,
            )
        else:
            concrete_profile = resolve_profile(
                "auto",
                text=parsed_text,
                filename=filename,
                content_type=content_type,
            )
    else:
        concrete_profile = resolve_profile(
            requested_profile,
            text=parsed_text,
            filename=filename,
            content_type=content_type,
        )

    config = profile_config(concrete_profile)
    resolved_top_k = top_k if top_k is not None else int(config["top_k"])
    resolved_candidate_k = (
        candidate_k if candidate_k is not None else int(config["candidate_k"])
    )
    if resolved_candidate_k < resolved_top_k:
        resolved_candidate_k = resolved_top_k

    return {
        "profile": concrete_profile,
        "top_k": resolved_top_k,
        "candidate_k": resolved_candidate_k,
        "answer_mode": answer_mode if answer_mode is not None else config["answer_mode"],
        "answer_style": (
            answer_style if answer_style is not None else config["answer_style"]
        ),
        "max_context_chars": (
            max_context_chars
            if max_context_chars is not None
            else int(config["max_context_chars"])
        ),
        "retrieval_enrichment_enabled": (
            bool(retrieval_enrichment_enabled)
            if retrieval_enrichment_enabled is not None
            else bool(settings.retrieval_enrichment_enabled)
        ),
        "query_intent_rules": config.get("query_intent_rules") or {},
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
    knowledge_base_repository: Annotated[
        KnowledgeBaseRepository,
        Depends(get_knowledge_base_repository),
    ],
    memory_repository: Annotated[MemoryRepository, Depends(get_memory_repository)],
    current_user: Annotated[User, Depends(get_current_user)],
    service: Annotated[RagAnswerService, Depends(get_rag_answer_service)],
) -> StreamingResponse:
    visible_document_ids = await _visible_document_ids(
        repository=repository,
        auth_repository=auth_repository,
        knowledge_base_repository=knowledge_base_repository,
        current_user=current_user,
        document_id=request.scope.document_id,
        organization_id=request.scope.organization_id,
        knowledge_base_ids=request.scope.knowledge_base_ids,
        include_descendants=request.scope.include_descendants,
    )
    visible_document_ids = _apply_client_document_scope(
        visible_document_ids,
        request.session_context.allowed_document_ids if request.session_context else None,
    )
    subject_context = await _subject_context(
        auth_repository=auth_repository,
        current_user=current_user,
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
    settings_document_id = (
        request.scope.document_id
        or (
            request.session_context.current_document_id
            if request.session_context is not None
            else None
        )
        or (next(iter(visible_document_ids)) if len(visible_document_ids) == 1 else None)
    )
    resolved = await _resolve_profile_settings(
        repository=repository,
        profile=request.profile,
        document_id=settings_document_id,
        top_k=request.top_k,
        candidate_k=request.candidate_k,
        answer_mode=request.answer_mode,
        answer_style=request.answer_style,
        max_context_chars=request.max_context_chars,
        retrieval_enrichment_enabled=request.retrieval_enrichment_enabled,
    )

    async def event_stream():
        try:
            async for event in _call_answer_stream_service(
                service,
                query=request.query,
                session_id=request.session_id,
                top_k=resolved["top_k"],
                candidate_k=resolved["candidate_k"],
                current_user=current_user,
                document_ids=visible_document_ids,
                session_context=request.session_context,
                memory_context=memory_context,
                session_summary=session_summary,
                answer_mode=resolved["answer_mode"],
                answer_style=resolved["answer_style"],
                max_context_chars=resolved["max_context_chars"],
                use_graph=request.use_graph,
                graph_expansion_depth=request.graph_expansion_depth,
                graph_expansion_limit=request.graph_expansion_limit,
                access_filter=build_access_filter(subject_context),
                subject_context=subject_context,
                retrieval_enrichment_enabled=resolved["retrieval_enrichment_enabled"],
                query_intent_rules=resolved["query_intent_rules"],
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
    knowledge_base_repository: KnowledgeBaseRepository,
    current_user: User,
    document_id,
    organization_id,
    knowledge_base_ids,
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
    requested_knowledge_base_ids = set(knowledge_base_ids or [])
    if knowledge_base_ids is not None and not requested_knowledge_base_ids:
        return set()
    if requested_knowledge_base_ids:
        knowledge_bases = await knowledge_base_repository.get_by_ids(
            list(requested_knowledge_base_ids)
        )
        if len(knowledge_bases) != len(requested_knowledge_base_ids):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Knowledge base not found.",
            )
        for knowledge_base in knowledge_bases:
            if not can_view_knowledge_base(
                current_user,
                knowledge_base,
                descendant_organization_ids=descendant_ids,
            ):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Knowledge base access is not allowed.",
                )
        documents = await repository.list_documents_for_permission_check(
            knowledge_base_ids=requested_knowledge_base_ids
        )
    else:
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


async def _subject_context(
    *,
    auth_repository: AuthRepository,
    current_user: User,
):
    descendant_ids = await auth_repository.get_descendant_organization_ids(
        current_user.organization_id
    )
    return build_subject_context(
        current_user,
        descendant_organization_ids=descendant_ids,
    )


async def _call_answer_service(service, **kwargs):
    parameters = inspect.signature(service.answer).parameters
    accepts_var_kwargs = any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters.values()
    )
    supported = (
        kwargs
        if accepts_var_kwargs
        else {key: value for key, value in kwargs.items() if key in parameters}
    )
    return await service.answer(**supported)


def _call_answer_stream_service(service, **kwargs):
    parameters = inspect.signature(service.answer_stream).parameters
    accepts_var_kwargs = any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters.values()
    )
    supported = (
        kwargs
        if accepts_var_kwargs
        else {key: value for key, value in kwargs.items() if key in parameters}
    )
    return service.answer_stream(**supported)
