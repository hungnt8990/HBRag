from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.knowledge_base import KnowledgeBase, KnowledgeBaseMember
from app.models.user import User
from app.services.security.security_permissions import can_view_knowledge_base

DEFAULT_KNOWLEDGE_BASE_NAME = "Default Knowledge Base"


class KnowledgeBaseRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        name: str,
        description: str | None,
        organization_id: UUID | None,
        owner_user_id: UUID | None,
        visibility: str = "organization",
    ) -> KnowledgeBase:
        knowledge_base = KnowledgeBase(
            name=name,
            description=description,
            organization_id=organization_id,
            owner_user_id=owner_user_id,
            visibility=visibility,
            is_active=True,
        )
        self._session.add(knowledge_base)
        await self._session.flush()
        return knowledge_base

    async def get_by_id(self, knowledge_base_id: UUID) -> KnowledgeBase | None:
        statement = (
            select(KnowledgeBase)
            .options(selectinload(KnowledgeBase.members))
            .where(KnowledgeBase.id == knowledge_base_id)
        )
        result = await self._session.execute(statement)
        return result.scalar_one_or_none()

    async def get_by_ids(self, knowledge_base_ids: Sequence[UUID]) -> list[KnowledgeBase]:
        if not knowledge_base_ids:
            return []
        statement = (
            select(KnowledgeBase)
            .options(selectinload(KnowledgeBase.members))
            .where(KnowledgeBase.id.in_(list(knowledge_base_ids)))
        )
        result = await self._session.execute(statement)
        return list(result.scalars().all())

    async def list_for_user(
        self,
        *,
        user: User,
        descendant_organization_ids: set[UUID],
    ) -> list[KnowledgeBase]:
        statement = (
            select(KnowledgeBase)
            .options(selectinload(KnowledgeBase.members))
            .where(KnowledgeBase.is_active.is_(True))
            .order_by(KnowledgeBase.updated_at.desc())
        )
        result = await self._session.execute(statement)
        knowledge_bases = list(result.scalars().all())
        return [
            knowledge_base
            for knowledge_base in knowledge_bases
            if can_view_knowledge_base(
                user,
                knowledge_base,
                descendant_organization_ids=descendant_organization_ids,
            )
        ]

    async def update(
        self,
        knowledge_base: KnowledgeBase,
        *,
        name: str | None = None,
        description: str | None = None,
        visibility: str | None = None,
        is_active: bool | None = None,
    ) -> KnowledgeBase:
        if name is not None:
            knowledge_base.name = name
        if description is not None:
            knowledge_base.description = description
        if visibility is not None:
            knowledge_base.visibility = visibility
        if is_active is not None:
            knowledge_base.is_active = is_active
        await self._session.flush()
        return knowledge_base

    async def soft_delete(self, knowledge_base: KnowledgeBase) -> KnowledgeBase:
        knowledge_base.is_active = False
        await self._session.flush()
        return knowledge_base

    async def add_member(
        self,
        *,
        knowledge_base_id: UUID,
        user_id: UUID | None = None,
        role_id: UUID | None = None,
        organization_id: UUID | None = None,
        permission: str,
    ) -> KnowledgeBaseMember:
        member = KnowledgeBaseMember(
            knowledge_base_id=knowledge_base_id,
            user_id=user_id,
            role_id=role_id,
            organization_id=organization_id,
            permission=permission,
        )
        self._session.add(member)
        await self._session.flush()
        return member

    async def remove_member(self, member_id: UUID) -> bool:
        member = await self._session.get(KnowledgeBaseMember, member_id)
        if member is None:
            return False
        await self._session.delete(member)
        await self._session.flush()
        return True

    async def get_member(self, member_id: UUID) -> KnowledgeBaseMember | None:
        return await self._session.get(KnowledgeBaseMember, member_id)

    async def list_members(self, knowledge_base_id: UUID) -> list[KnowledgeBaseMember]:
        statement = (
            select(KnowledgeBaseMember)
            .where(KnowledgeBaseMember.knowledge_base_id == knowledge_base_id)
            .order_by(KnowledgeBaseMember.created_at.asc())
        )
        result = await self._session.execute(statement)
        return list(result.scalars().all())

    async def get_or_create_default(
        self,
        *,
        organization_id: UUID | None,
        owner_user_id: UUID | None,
    ) -> KnowledgeBase:
        statement = select(KnowledgeBase).where(
            KnowledgeBase.name == DEFAULT_KNOWLEDGE_BASE_NAME,
            KnowledgeBase.is_active.is_(True),
        )
        if organization_id is None:
            statement = statement.where(KnowledgeBase.organization_id.is_(None))
        else:
            statement = statement.where(KnowledgeBase.organization_id == organization_id)
        result = await self._session.execute(statement.limit(1))
        knowledge_base = result.scalar_one_or_none()
        if knowledge_base is not None:
            return knowledge_base
        return await self.create(
            name=DEFAULT_KNOWLEDGE_BASE_NAME,
            description="Default knowledge base for existing document workflows.",
            organization_id=organization_id,
            owner_user_id=owner_user_id,
            visibility="organization" if organization_id is not None else "private",
        )

    async def commit(self) -> None:
        await self._session.commit()

    async def rollback(self) -> None:
        await self._session.rollback()
