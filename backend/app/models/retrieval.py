from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from sqlalchemy import ForeignKey, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.mixins import CreatedAtMixin

if TYPE_CHECKING:
    from app.models.chat import ChatSession


class RetrievalLog(Base, CreatedAtMixin):
    __tablename__ = "retrieval_logs"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    session_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("chat_sessions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    query: Mapped[str] = mapped_column(Text, nullable=False)
    vector_results: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    keyword_results: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    hybrid_results: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    reranked_results: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    session: Mapped[ChatSession | None] = relationship(back_populates="retrieval_logs")
