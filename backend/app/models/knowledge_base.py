from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.mixins import CreatedAtMixin, TimestampMixin

if TYPE_CHECKING:
    from app.models.document import Document
    from app.models.organization import Organization
    from app.models.user import Role, User

KNOWLEDGE_BASE_VISIBILITIES = ("private", "organization", "subtree", "global")
KNOWLEDGE_BASE_PERMISSIONS = ("owner", "admin", "editor", "viewer")


class KnowledgeBase(Base, TimestampMixin):
    __tablename__ = "knowledge_bases"
    __table_args__ = (
        CheckConstraint(
            "visibility IN ('private', 'organization', 'subtree', 'global')",
            name="knowledge_bases_visibility",
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    organization_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    owner_user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    visibility: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="organization",
        server_default="organization",
        index=True,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
        index=True,
    )

    organization: Mapped[Organization | None] = relationship()
    owner: Mapped[User | None] = relationship(foreign_keys=[owner_user_id])
    documents: Mapped[list[Document]] = relationship(back_populates="knowledge_base")
    members: Mapped[list[KnowledgeBaseMember]] = relationship(
        back_populates="knowledge_base",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="selectin",
    )


class KnowledgeBaseMember(Base, CreatedAtMixin):
    __tablename__ = "knowledge_base_members"
    __table_args__ = (
        CheckConstraint(
            "permission IN ('owner', 'admin', 'editor', 'viewer')",
            name="knowledge_base_members_permission",
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    knowledge_base_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    role_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("roles.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    organization_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    permission: Mapped[str] = mapped_column(String(32), nullable=False, index=True)

    knowledge_base: Mapped[KnowledgeBase] = relationship(back_populates="members")
    user: Mapped[User | None] = relationship()
    role: Mapped[Role | None] = relationship()
    organization: Mapped[Organization | None] = relationship()
