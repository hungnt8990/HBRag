from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.mixins import TimestampMixin

if TYPE_CHECKING:
    from app.models.document import Document
    from app.models.user import User


class Organization(Base, TimestampMixin):
    __tablename__ = "organizations"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    ma_dviqly: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    ma_dviqly_cha: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    ten_dviqly: Mapped[str] = mapped_column(String(255), nullable=False)
    dvi_level: Mapped[int] = mapped_column(Integer, nullable=False)
    parent_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    parent: Mapped[Organization | None] = relationship(
        remote_side=[id],
        back_populates="children",
    )
    children: Mapped[list[Organization]] = relationship(back_populates="parent")
    users: Mapped[list[User]] = relationship(back_populates="organization")
    documents: Mapped[list[Document]] = relationship(back_populates="organization")
