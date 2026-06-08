from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.organization import Organization
from app.models.user import Role, User


class AuthRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_user_by_username(self, username: str) -> User | None:
        statement = (
            select(User)
            .options(selectinload(User.roles), selectinload(User.organization))
            .where(User.username == username)
        )
        result = await self._session.execute(statement)
        return result.scalar_one_or_none()

    async def get_user_by_id(self, user_id: UUID) -> User | None:
        statement = (
            select(User)
            .options(selectinload(User.roles), selectinload(User.organization))
            .where(User.id == user_id)
        )
        result = await self._session.execute(statement)
        return result.scalar_one_or_none()

    async def get_descendant_organization_ids(self, organization_id: UUID) -> set[UUID]:
        organizations = list((await self._session.execute(select(Organization))).scalars().all())
        children_by_parent: dict[UUID, list[Organization]] = {}
        for organization in organizations:
            if organization.parent_id is not None:
                children_by_parent.setdefault(organization.parent_id, []).append(organization)

        visible_ids: set[UUID] = set()
        stack = [organization_id]
        while stack:
            current = stack.pop()
            if current in visible_ids:
                continue
            visible_ids.add(current)
            stack.extend(child.id for child in children_by_parent.get(current, []))
        return visible_ids

    async def list_roles(self) -> list[Role]:
        return list((await self._session.execute(select(Role))).scalars().all())
