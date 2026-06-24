from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import get_current_user
from app.db.session import get_db_session
from app.models.knowledge_base import KnowledgeBase
from app.models.user import User
from app.repositories.auth import AuthRepository
from app.repositories.knowledge_bases import KnowledgeBaseRepository
from app.schemas.knowledge_bases import (
    KnowledgeBaseCreate,
    KnowledgeBaseListResponse,
    KnowledgeBaseMemberCreate,
    KnowledgeBaseMemberResponse,
    KnowledgeBaseResponse,
    KnowledgeBaseUpdate,
)
from app.services.security.security_permissions import (
    can_assign_upload_organization,
    can_manage_knowledge_base,
    can_view_knowledge_base,
)

router = APIRouter(prefix="/api/knowledge-bases", tags=["knowledge-bases"])


def get_knowledge_base_repository(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> KnowledgeBaseRepository:
    return KnowledgeBaseRepository(session)


def get_auth_repository(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> AuthRepository:
    return AuthRepository(session)


@router.post("", response_model=KnowledgeBaseResponse, status_code=status.HTTP_201_CREATED)
async def create_knowledge_base(
    request: KnowledgeBaseCreate,
    repository: Annotated[
        KnowledgeBaseRepository,
        Depends(get_knowledge_base_repository),
    ],
    auth_repository: Annotated[AuthRepository, Depends(get_auth_repository)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> KnowledgeBaseResponse:
    target_organization_id = request.organization_id or current_user.organization_id
    descendant_ids = await auth_repository.get_descendant_organization_ids(
        current_user.organization_id
    )
    if not can_assign_upload_organization(
        current_user,
        target_organization_id,
        descendant_organization_ids=descendant_ids,
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot create a knowledge base for the selected organization.",
        )

    knowledge_base = await repository.create(
        name=request.name,
        description=request.description,
        organization_id=target_organization_id,
        owner_user_id=current_user.id,
        visibility=request.visibility,
    )
    await repository.commit()
    return KnowledgeBaseResponse.model_validate(knowledge_base)


@router.get("", response_model=KnowledgeBaseListResponse)
async def list_knowledge_bases(
    repository: Annotated[
        KnowledgeBaseRepository,
        Depends(get_knowledge_base_repository),
    ],
    auth_repository: Annotated[AuthRepository, Depends(get_auth_repository)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> KnowledgeBaseListResponse:
    descendant_ids = await auth_repository.get_descendant_organization_ids(
        current_user.organization_id
    )
    items = await repository.list_for_user(
        user=current_user,
        descendant_organization_ids=descendant_ids,
    )
    return KnowledgeBaseListResponse(
        items=[KnowledgeBaseResponse.model_validate(item) for item in items],
        total=len(items),
    )


@router.get("/{knowledge_base_id}", response_model=KnowledgeBaseResponse)
async def get_knowledge_base(
    knowledge_base_id: UUID,
    repository: Annotated[
        KnowledgeBaseRepository,
        Depends(get_knowledge_base_repository),
    ],
    auth_repository: Annotated[AuthRepository, Depends(get_auth_repository)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> KnowledgeBaseResponse:
    knowledge_base, _ = await _require_viewable_knowledge_base(
        knowledge_base_id=knowledge_base_id,
        repository=repository,
        auth_repository=auth_repository,
        current_user=current_user,
    )
    return KnowledgeBaseResponse.model_validate(knowledge_base)


@router.patch("/{knowledge_base_id}", response_model=KnowledgeBaseResponse)
async def update_knowledge_base(
    knowledge_base_id: UUID,
    request: KnowledgeBaseUpdate,
    repository: Annotated[
        KnowledgeBaseRepository,
        Depends(get_knowledge_base_repository),
    ],
    auth_repository: Annotated[AuthRepository, Depends(get_auth_repository)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> KnowledgeBaseResponse:
    knowledge_base, _ = await _require_manageable_knowledge_base(
        knowledge_base_id=knowledge_base_id,
        repository=repository,
        auth_repository=auth_repository,
        current_user=current_user,
    )
    updated = await repository.update(
        knowledge_base,
        name=request.name,
        description=request.description,
        visibility=request.visibility,
        is_active=request.is_active,
    )
    await repository.commit()
    return KnowledgeBaseResponse.model_validate(updated)


@router.delete("/{knowledge_base_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_knowledge_base(
    knowledge_base_id: UUID,
    repository: Annotated[
        KnowledgeBaseRepository,
        Depends(get_knowledge_base_repository),
    ],
    auth_repository: Annotated[AuthRepository, Depends(get_auth_repository)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> None:
    knowledge_base, _ = await _require_manageable_knowledge_base(
        knowledge_base_id=knowledge_base_id,
        repository=repository,
        auth_repository=auth_repository,
        current_user=current_user,
    )
    await repository.soft_delete(knowledge_base)
    await repository.commit()


@router.post(
    "/{knowledge_base_id}/members",
    response_model=KnowledgeBaseMemberResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_knowledge_base_member(
    knowledge_base_id: UUID,
    request: KnowledgeBaseMemberCreate,
    repository: Annotated[
        KnowledgeBaseRepository,
        Depends(get_knowledge_base_repository),
    ],
    auth_repository: Annotated[AuthRepository, Depends(get_auth_repository)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> KnowledgeBaseMemberResponse:
    await _require_manageable_knowledge_base(
        knowledge_base_id=knowledge_base_id,
        repository=repository,
        auth_repository=auth_repository,
        current_user=current_user,
    )
    member = await repository.add_member(
        knowledge_base_id=knowledge_base_id,
        user_id=request.user_id,
        role_id=request.role_id,
        organization_id=request.organization_id,
        permission=request.permission,
    )
    await repository.commit()
    return KnowledgeBaseMemberResponse.model_validate(member)


@router.delete(
    "/{knowledge_base_id}/members/{member_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def remove_knowledge_base_member(
    knowledge_base_id: UUID,
    member_id: UUID,
    repository: Annotated[
        KnowledgeBaseRepository,
        Depends(get_knowledge_base_repository),
    ],
    auth_repository: Annotated[AuthRepository, Depends(get_auth_repository)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> None:
    await _require_manageable_knowledge_base(
        knowledge_base_id=knowledge_base_id,
        repository=repository,
        auth_repository=auth_repository,
        current_user=current_user,
    )
    member = await repository.get_member(member_id)
    if member is None or member.knowledge_base_id != knowledge_base_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Member not found.")
    await repository.remove_member(member_id)
    await repository.commit()


async def _require_viewable_knowledge_base(
    *,
    knowledge_base_id: UUID,
    repository: KnowledgeBaseRepository,
    auth_repository: AuthRepository,
    current_user: User,
) -> tuple[KnowledgeBase, set[UUID]]:
    knowledge_base = await repository.get_by_id(knowledge_base_id)
    if knowledge_base is None or not knowledge_base.is_active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Knowledge base not found.",
        )
    descendant_ids = await auth_repository.get_descendant_organization_ids(
        current_user.organization_id
    )
    if not can_view_knowledge_base(
        current_user,
        knowledge_base,
        descendant_organization_ids=descendant_ids,
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Knowledge base access is not allowed.",
        )
    return knowledge_base, descendant_ids


async def _require_manageable_knowledge_base(
    *,
    knowledge_base_id: UUID,
    repository: KnowledgeBaseRepository,
    auth_repository: AuthRepository,
    current_user: User,
) -> tuple[KnowledgeBase, set[UUID]]:
    knowledge_base, descendant_ids = await _require_viewable_knowledge_base(
        knowledge_base_id=knowledge_base_id,
        repository=repository,
        auth_repository=auth_repository,
        current_user=current_user,
    )
    if not can_manage_knowledge_base(
        current_user,
        knowledge_base,
        descendant_organization_ids=descendant_ids,
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Knowledge base management is not allowed.",
        )
    return knowledge_base, descendant_ids
