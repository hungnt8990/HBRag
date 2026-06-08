from uuid import UUID

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class OrganizationResponse(BaseModel):
    id: UUID
    ma_dviqly: str
    ma_dviqly_cha: str | None
    ten_dviqly: str
    dvi_level: int
    parent_id: UUID | None


class UserResponse(BaseModel):
    id: UUID
    username: str
    email: str | None
    full_name: str | None
    organization: OrganizationResponse
    roles: list[str]
    is_active: bool
