from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import get_current_user
from app.core.config import settings
from app.db.session import get_db_session
from app.models.user import User
from app.repositories.memory import MemoryRepository
from app.schemas.memory import (
    MemoryCreateRequest,
    MemoryDeleteResponse,
    MemoryItemResponse,
    MemorySettingsResponse,
)
from app.services.memory import MemoryConfigError, MemoryResult, build_memory_provider

router = APIRouter(prefix="/api/memory", tags=["memory"])


def get_memory_repository(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> MemoryRepository:
    return MemoryRepository(session)


@router.get(
    "/settings",
    response_model=MemorySettingsResponse,
    summary="Lấy cấu hình bộ nhớ",
)
async def get_memory_settings(
    _current_user: Annotated[User, Depends(get_current_user)],
) -> MemorySettingsResponse:
    return MemorySettingsResponse(
        memory_enabled=settings.memory_enabled,
        memory_provider=settings.memory_provider,
        mem0_enabled=settings.mem0_enabled,
        memory_top_k=settings.memory_top_k,
        memory_auto_save=settings.memory_auto_save,
        memory_inject_into_prompt=settings.memory_inject_into_prompt,
    )


@router.patch(
    "/settings",
    status_code=status.HTTP_501_NOT_IMPLEMENTED,
    summary="Cập nhật cấu hình bộ nhớ",
)
async def patch_memory_settings(
    _current_user: Annotated[User, Depends(get_current_user)],
) -> None:
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail=(
            "Global memory settings are not persisted server-side. "
            "Store per-session preferences on the frontend."
        ),
    )


@router.get(
    "",
    response_model=list[MemoryItemResponse],
    summary="Danh sách mục bộ nhớ",
)
async def list_memory(
    repository: Annotated[MemoryRepository, Depends(get_memory_repository)],
    current_user: Annotated[User, Depends(get_current_user)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[MemoryItemResponse]:
    provider = build_memory_provider(repository)
    results = await provider.list_memory(user=current_user, limit=limit, offset=offset)
    return [_to_item(result) for result in results]


@router.post(
    "",
    response_model=MemoryItemResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Ghi nhớ hội thoại",
)
async def create_memory(
    request: MemoryCreateRequest,
    repository: Annotated[MemoryRepository, Depends(get_memory_repository)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> MemoryItemResponse:
    provider = build_memory_provider(repository)
    try:
        result = await provider.add_memory(
            user=current_user,
            content=request.content,
            memory_type=request.memory_type,
            metadata={"source": request.source},
        )
    except MemoryConfigError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    return _to_item(result)


@router.delete(
    "/{memory_id}",
    response_model=MemoryDeleteResponse,
    summary="Xóa một mục bộ nhớ",
)
async def delete_memory(
    memory_id: str,
    repository: Annotated[MemoryRepository, Depends(get_memory_repository)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> MemoryDeleteResponse:
    provider = build_memory_provider(repository)
    try:
        deleted = await provider.delete_memory(user=current_user, memory_id=memory_id)
    except MemoryConfigError as exc:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail=(
                "The configured memory provider does not support local deletion. "
                f"{exc}"
            ),
        ) from exc
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Memory not found.",
        )
    return MemoryDeleteResponse(memory_id=memory_id, deleted=True)


def _to_item(result: MemoryResult) -> MemoryItemResponse:
    return MemoryItemResponse(
        id=result.id,
        content=result.content,
        memory_type=result.memory_type,
        source=result.source,
        score=result.score,
        metadata=dict(result.metadata or {}),
    )
