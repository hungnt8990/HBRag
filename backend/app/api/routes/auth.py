from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import get_current_user
from app.core.security import create_access_token, verify_password
from app.db.session import get_db_session
from app.models.user import User
from app.repositories.auth import AuthRepository
from app.schemas.auth import LoginRequest, OrganizationResponse, TokenResponse, UserResponse

router = APIRouter(prefix="/api/auth", tags=["auth"])


def get_auth_repository(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> AuthRepository:
    return AuthRepository(session)


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


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout() -> None:
    return None


def to_user_response(user: User) -> UserResponse:
    return UserResponse(
        id=user.id,
        username=user.username,
        email=user.email,
        full_name=user.full_name,
        organization=OrganizationResponse(
            id=user.organization.id,
            ma_dviqly=user.organization.ma_dviqly,
            ma_dviqly_cha=user.organization.ma_dviqly_cha,
            ten_dviqly=user.organization.ten_dviqly,
            dvi_level=user.organization.dvi_level,
            parent_id=user.organization.parent_id,
        ),
        roles=[role.name for role in user.roles],
        is_active=user.is_active,
    )
