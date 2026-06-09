from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

KnowledgeBaseVisibility = Literal["private", "organization", "subtree", "global"]
KnowledgeBasePermission = Literal["owner", "admin", "editor", "viewer"]


class KnowledgeBaseCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    organization_id: UUID | None = None
    visibility: KnowledgeBaseVisibility = "organization"


class KnowledgeBaseUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    visibility: KnowledgeBaseVisibility | None = None
    is_active: bool | None = None


class KnowledgeBaseMemberCreate(BaseModel):
    user_id: UUID | None = None
    role_id: UUID | None = None
    organization_id: UUID | None = None
    permission: KnowledgeBasePermission

    @model_validator(mode="after")
    def validate_single_target(self) -> "KnowledgeBaseMemberCreate":
        targets = [self.user_id, self.role_id, self.organization_id]
        if sum(item is not None for item in targets) != 1:
            raise ValueError("Exactly one member target is required.")
        return self


class KnowledgeBaseMemberResponse(BaseModel):
    id: UUID
    knowledge_base_id: UUID
    user_id: UUID | None = None
    role_id: UUID | None = None
    organization_id: UUID | None = None
    permission: str
    created_at: datetime

    model_config = {"from_attributes": True}


class KnowledgeBaseResponse(BaseModel):
    id: UUID
    name: str
    description: str | None = None
    organization_id: UUID | None = None
    owner_user_id: UUID | None = None
    visibility: str
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class KnowledgeBaseListResponse(BaseModel):
    items: list[KnowledgeBaseResponse]
    total: int
