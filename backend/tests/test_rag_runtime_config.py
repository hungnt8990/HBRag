from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.services.rag.rag_runtime_config import load_rag_runtime_config, save_rag_runtime_config


def test_rag_runtime_config_db_values_override_settings_defaults() -> None:
    async def run_test() -> None:
        repository = FakeRagRuntimeConfigRepository(
            {
                "default": {
                    "enable_artifact_first_retrieval": False,
                    "enable_chunk_fallback": False,
                    "artifact_confidence_threshold": 0.82,
                    "max_artifacts": 2,
                }
            }
        )

        config = await load_rag_runtime_config(repository)  # type: ignore[arg-type]

        assert config.enable_artifact_first_retrieval is False
        assert config.enable_chunk_fallback is False
        assert config.artifact_confidence_threshold == 0.82
        assert config.max_artifacts == 2
        assert config.enable_knowledge_artifact_compilation is True

    asyncio.run(run_test())


def test_rag_runtime_config_save_normalizes_values() -> None:
    async def run_test() -> None:
        repository = FakeRagRuntimeConfigRepository({})

        config = await save_rag_runtime_config(  # type: ignore[arg-type]
            repository,
            {
                "enable_artifact_first_retrieval": "false",
                "artifact_confidence_threshold": "2.5",
                "max_chunks": "0",
            },
        )

        assert config.enable_artifact_first_retrieval is False
        assert config.artifact_confidence_threshold == 1.0
        assert config.max_chunks > 0
        assert repository.store["default"]["enable_artifact_first_retrieval"] is False

    asyncio.run(run_test())


class FakeRagRuntimeConfigRepository:
    def __init__(self, store: dict[str, dict]) -> None:
        self.store = store

    async def seed_missing_configs(self, configs):
        for name, config in configs.items():
            self.store.setdefault(name, dict(config))

    async def get_config(self, config_name: str):
        if config_name not in self.store:
            return None
        return SimpleNamespace(config_name=config_name, config=self.store[config_name])

    async def upsert_config(self, config_name: str, config: dict):
        self.store[config_name] = dict(config)
        return SimpleNamespace(config_name=config_name, config=self.store[config_name])

