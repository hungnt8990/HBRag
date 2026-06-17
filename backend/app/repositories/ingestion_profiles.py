from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ingestion_profile import IngestionProfileConfig


class IngestionProfileRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_profile_configs(self) -> dict[str, dict[str, Any]]:
        statement = select(IngestionProfileConfig).order_by(
            IngestionProfileConfig.profile_name.asc()
        )
        result = await self._session.execute(statement)
        return {
            profile.profile_name: dict(profile.config or {})
            for profile in result.scalars().all()
        }

    async def get_profile_config(
        self,
        profile_name: str,
    ) -> IngestionProfileConfig | None:
        return await self._session.get(IngestionProfileConfig, profile_name)

    async def upsert_profile_config(
        self,
        profile_name: str,
        config: dict[str, Any],
    ) -> IngestionProfileConfig:
        profile = await self.get_profile_config(profile_name)
        if profile is None:
            profile = IngestionProfileConfig(profile_name=profile_name, config=dict(config))
            self._session.add(profile)
        else:
            profile.config = dict(config)
        await self._session.flush()
        return profile

    async def seed_missing_profile_configs(
        self,
        configs: dict[str, dict[str, Any]],
    ) -> None:
        existing = set((await self.list_profile_configs()).keys())
        for profile_name, config in configs.items():
            if profile_name in existing:
                continue
            self._session.add(
                IngestionProfileConfig(
                    profile_name=profile_name,
                    config=dict(config),
                )
            )
        await self._session.flush()

    async def commit(self) -> None:
        await self._session.commit()

    async def rollback(self) -> None:
        await self._session.rollback()
