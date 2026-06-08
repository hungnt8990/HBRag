from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from sqlalchemy import Boolean, CheckConstraint, Float, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.mixins import TimestampMixin

if TYPE_CHECKING:
    from app.models.chat import ChatSession
    from app.models.organization import Organization
    from app.models.user import User

MEMORY_TYPES = ("preference", "task", "entity", "instruction", "fact")
MEMORY_SOURCES = ("manual", "chat_extracted", "system", "mem0")


class UserMemory(Base, TimestampMixin):
    __tablename__ = "user_memories"
    __table_args__ = (
        CheckConstraint(
            "memory_type IN ('preference', 'task', 'entity', 'instruction', 'fact')",
            name="user_memories_memory_type",
        ),
        CheckConstraint(
            "source IN ('manual', 'chat_extracted', 'system', 'mem0')",
            name="user_memories_source",
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    organization_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    memory_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        server_default="local",
        default="manual",
    )
    confidence: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        server_default="1.0",
        default=1.0,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default="true",
        default=True,
    )
    memory_metadata: Mapped[dict[str, Any] | None] = mapped_column(
        "metadata",
        JSONB,
        nullable=True,
    )

    user: Mapped[User] = relationship()
    organization: Mapped[Organization | None] = relationship()


class SessionSummary(Base, TimestampMixin):
    __tablename__ = "session_summaries"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    session_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("chat_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    last_message_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("chat_messages.id", ondelete="SET NULL"),
        nullable=True,
    )
    summary_metadata: Mapped[dict[str, Any] | None] = mapped_column(
        "metadata",
        JSONB,
        nullable=True,
    )

    session: Mapped[ChatSession] = relationship()
    user: Mapped[User] = relationship()
