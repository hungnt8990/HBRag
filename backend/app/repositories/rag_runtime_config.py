from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.rag_config import RagRuntimeConfig


class RagRuntimeConfigRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_configs(self) -> dict[str, dict[str, Any]]:
        statement = select(RagRuntimeConfig).order_by(RagRuntimeConfig.config_name.asc())
        result = await self._session.execute(statement)
        return {
            config.config_name: dict(config.config or {})
            for config in result.scalars().all()
        }

    async def get_config(self, config_name: str) -> RagRuntimeConfig | None:
        return await self._session.get(RagRuntimeConfig, config_name)

    async def upsert_config(
        self,
        config_name: str,
        config: dict[str, Any],
    ) -> RagRuntimeConfig:
        model = await self.get_config(config_name)
        if model is None:
            model = RagRuntimeConfig(config_name=config_name, config=dict(config))
            self._session.add(model)
        else:
            model.config = dict(config)
        await self._session.flush()
        return model

    async def seed_missing_configs(self, configs: dict[str, dict[str, Any]]) -> None:
        existing = set((await self.list_configs()).keys())
        for config_name, config in configs.items():
            if config_name in existing:
                continue
            self._session.add(
                RagRuntimeConfig(
                    config_name=config_name,
                    config=dict(config),
                )
            )
        await self._session.flush()

    async def commit(self) -> None:
        await self._session.commit()

    async def rollback(self) -> None:
        await self._session.rollback()

