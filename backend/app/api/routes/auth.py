from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import get_current_user
from app.core.security import create_access_token, verify_password
from app.db.session import get_db_session
from app.models.document import Document
from app.models.user import User
from app.repositories.auth import AuthRepository
from app.repositories.documents import DocumentRepository
from app.schemas.auth import (
    AccessCatalogResponse,
    LoginRequest,
    OrganizationResponse,
    RoleResponse,
    TokenResponse,
    UserResponse,
)
from app.services.security.security_permissions import can_assign_upload_organization, can_view_document

router = APIRouter(prefix="/api/auth", tags=["auth"])


def get_auth_repository(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> AuthRepository:
    return AuthRepository(session)

def get_document_repository(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> DocumentRepository:
    return DocumentRepository(session)


@router.post("/login", response_model=TokenResponse)
async def login(
    request: LoginRequest,
    repository: Annotated[AuthRepository, Depends(get_auth_repository)],
) -> TokenResponse:
    user = await repository.get_user_by_username(request.username)
    if user is None or not user.is_active or not verify_password(
        request.password,
        user.hashed_password,
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password.",
        )
    return TokenResponse(access_token=create_access_token(subject=str(user.id)))


@router.get("/me", response_model=UserResponse)
async def me(current_user: Annotated[User, Depends(get_current_user)]) -> UserResponse:
    return to_user_response(current_user)

@router.get("/access-catalog", response_model=AccessCatalogResponse)
async def access_catalog(
    current_user: Annotated[User, Depends(get_current_user)],
    auth_repository: Annotated[AuthRepository, Depends(get_auth_repository)],
    document_repository: Annotated[DocumentRepository, Depends(get_document_repository)],
) -> AccessCatalogResponse:
    descendant_organization_ids = await auth_repository.get_descendant_organization_ids(
        current_user.organization_id,
    )
    organizations = await auth_repository.list_organizations()
    assignable_organizations = [
        organization
        for organization in organizations
        if can_assign_upload_organization(
            current_user,
            organization.id,
            descendant_organization_ids=descendant_organization_ids,
        )
    ]
    roles = await auth_repository.list_roles()
    documents = await document_repository.list_documents_for_permission_check()
    visible_documents = [
        document
        for document in documents
        if can_view_document(
            current_user,
            document,
            descendant_organization_ids=descendant_organization_ids,
        )
    ]

    return AccessCatalogResponse(
        organizations=[to_organization_response(organization) for organization in assignable_organizations],
        roles=[
            RoleResponse(id=role.id, name=role.name, description=role.description)
            for role in roles
        ],
        groups=_extract_access_group_codes(visible_documents),
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout() -> None:
    return None


def to_user_response(user: User) -> UserResponse:
    return UserResponse(
        id=user.id,
        username=user.username,
        email=user.email,
        full_name=user.full_name,
        organization=to_organization_response(user.organization),
        roles=[role.name for role in user.roles],
        is_active=user.is_active,
    )

def to_organization_response(organization) -> OrganizationResponse:
    return OrganizationResponse(
        id=organization.id,
        ma_dviqly=organization.ma_dviqly,
        ma_dviqly_cha=organization.ma_dviqly_cha,
        ten_dviqly=organization.ten_dviqly,
        dvi_level=organization.dvi_level,
        parent_id=organization.parent_id,
    )

def _extract_access_group_codes(documents: list[Document]) -> list[str]:
    groups: set[str] = set()
    for document in documents:
        metadata = getattr(document, "document_metadata", None)
        if not isinstance(metadata, dict):
            continue
        access = metadata.get("access")
        if not isinstance(access, dict):
            continue
        for field_name in ("allowed_group_codes", "denied_group_codes"):
            groups.update(_string_values(access.get(field_name)))
    return sorted(groups)

def _string_values(value: object) -> set[str]:
    if value is None:
        return set()
    if isinstance(value, str):
        parts = value.replace(";", ",").replace("|", ",").split(",")
        return {part.strip() for part in parts if part.strip()}
    if isinstance(value, list | tuple | set):
        return {str(item).strip() for item in value if str(item).strip()}
    return set()
