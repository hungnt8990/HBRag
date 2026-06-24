from pathlib import Path

from app.core.config import Settings
from app.services import vector_store
from app.services.embeddings import embedding_factory
from app.services.embeddings.embedding_fake_provider import FakeEmbeddingProvider
from app.services.embeddings.embedding_openai_provider import OpenAICompatibleEmbeddingProvider
from app.services.llms import llm_factory
from app.services.llms.llm_fake_llm import FakeLLM
from app.services.llms.llm_openai_llm import OpenAICompatibleLLM
from app.services.rerankers import reranker_factory
from app.services.rerankers.reranker_fake_reranker import FakeReranker
from app.services.rerankers.reranker_openai_compatible_reranker import OpenAICompatibleReranker

PROVIDER_ENV_VARS = [
    "AUTO_RECREATE_COLLECTION",
    "EMBEDDING_PROVIDER",
    "EMBEDDING_BASE_URL",
    "EMBEDDING_API_KEY",
    "EMBEDDING_MODEL",
    "EMBEDDING_DIMENSION",
    "RERANKER_PROVIDER",
    "RERANKER_BASE_URL",
    "RERANKER_API_KEY",
    "RERANKER_MODEL",
    "RERANKER_ENDPOINT_PATH",
    "LLM_PROVIDER",
    "LLM_BASE_URL",
    "LLM_API_KEY",
    "LLM_MODEL",
]


def test_default_providers_are_fake(monkeypatch) -> None:
    for env_var in PROVIDER_ENV_VARS:
        monkeypatch.delenv(env_var, raising=False)

    settings = Settings(_env_file=None)

    assert settings.embedding_provider == "fake"
    assert settings.reranker_provider == "fake"
    assert settings.llm_provider == "fake"
    assert settings.embedding_dimension == 384
    assert settings.auto_recreate_collection is False
    assert settings.qdrant_artifact_collection_name == "hbrag_artifacts_v1"
    assert settings.graph_enabled is False
    assert settings.graph_provider == "neo4j"
    assert settings.graph_expansion_depth == 1
    assert settings.graph_expansion_limit == 20


def test_config_loads_openai_compatible_settings(tmp_path: Path, monkeypatch) -> None:
    for env_var in PROVIDER_ENV_VARS:
        monkeypatch.delenv(env_var, raising=False)

    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "EMBEDDING_PROVIDER=openai_compatible",
                "EMBEDDING_BASE_URL=http://embedding.test/v1",
                "EMBEDDING_API_KEY=embedding-key",
                "EMBEDDING_MODEL=embedding-model",
                "EMBEDDING_DIMENSION=768",
                "AUTO_RECREATE_COLLECTION=true",
                "RERANKER_PROVIDER=openai_compatible",
                "RERANKER_BASE_URL=http://reranker.test/v1",
                "RERANKER_API_KEY=reranker-key",
                "RERANKER_MODEL=reranker-model",
                "RERANKER_ENDPOINT_PATH=/rerank",
                "LLM_PROVIDER=openai_compatible",
                "LLM_BASE_URL=http://llm.test/v1",
                "LLM_API_KEY=llm-key",
                "LLM_MODEL=llm-model",
                "GRAPH_ENABLED=true",
                "GRAPH_PROVIDER=neo4j",
                "NEO4J_URI=bolt://neo4j.test:7687",
                "NEO4J_USERNAME=graph-user",
                "NEO4J_PASSWORD=graph-secret",
            ]
        ),
        encoding="utf-8",
    )

    settings = Settings(_env_file=env_file)

    assert settings.embedding_provider == "openai_compatible"
    assert settings.embedding_base_url == "http://embedding.test/v1"
    assert settings.embedding_api_key == "embedding-key"
    assert settings.embedding_model == "embedding-model"
    assert settings.embedding_dimension == 768
    assert settings.auto_recreate_collection is True
    assert settings.reranker_provider == "openai_compatible"
    assert settings.reranker_base_url == "http://reranker.test/v1"
    assert settings.reranker_api_key == "reranker-key"
    assert settings.reranker_model == "reranker-model"
    assert settings.reranker_endpoint_path == "/rerank"
    assert settings.llm_provider == "openai_compatible"
    assert settings.llm_base_url == "http://llm.test/v1"
    assert settings.llm_api_key == "llm-key"
    assert settings.llm_model == "llm-model"
    assert settings.graph_enabled is True
    assert settings.graph_provider == "neo4j"
    assert settings.neo4j_uri == "bolt://neo4j.test:7687"
    assert settings.neo4j_username == "graph-user"
    assert settings.neo4j_password == "graph-secret"


def test_vector_store_uses_embedding_dimension_from_config(monkeypatch) -> None:
    configured_settings = Settings(_env_file=None, embedding_dimension=1024)
    monkeypatch.setattr(vector_store, "settings", configured_settings)
    vector_store.get_vector_store.cache_clear()

    try:
        store = vector_store.get_vector_store()
    finally:
        vector_store.get_vector_store.cache_clear()

    assert store.vector_size == 1024


def test_factories_return_fake_providers(monkeypatch) -> None:
    configured_settings = Settings(
        _env_file=None,
        embedding_provider="fake",
        embedding_dimension=256,
        reranker_provider="fake",
        llm_provider="fake",
    )
    _set_factory_settings(monkeypatch, configured_settings)

    try:
        embedding_provider = embedding_factory.get_embedding_provider()
        reranker = reranker_factory.get_reranker()
        llm_provider = llm_factory.get_llm_provider()
    finally:
        _clear_factory_caches()

    assert isinstance(embedding_provider, FakeEmbeddingProvider)
    assert embedding_provider.dimension == 256
    assert isinstance(reranker, FakeReranker)
    assert isinstance(llm_provider, FakeLLM)


def test_factories_return_openai_compatible_providers(monkeypatch) -> None:
    configured_settings = Settings(
        _env_file=None,
        embedding_provider="openai_compatible",
        embedding_base_url="http://embedding.test/v1",
        embedding_api_key="embedding-key",
        embedding_model="embedding-model",
        embedding_dimension=768,
        reranker_provider="openai_compatible",
        reranker_base_url="http://reranker.test/v1",
        reranker_api_key="reranker-key",
        reranker_model="reranker-model",
        reranker_endpoint_path="/rerank",
        llm_provider="openai_compatible",
        llm_base_url="http://llm.test/v1",
        llm_api_key="llm-key",
        llm_model="llm-model",
    )
    _set_factory_settings(monkeypatch, configured_settings)

    try:
        embedding_provider = embedding_factory.get_embedding_provider()
        reranker = reranker_factory.get_reranker()
        llm_provider = llm_factory.get_llm_provider()
    finally:
        _clear_factory_caches()

    assert isinstance(embedding_provider, OpenAICompatibleEmbeddingProvider)
    assert embedding_provider.dimension == 768
    assert isinstance(reranker, OpenAICompatibleReranker)
    assert isinstance(llm_provider, OpenAICompatibleLLM)


def _set_factory_settings(monkeypatch, configured_settings: Settings) -> None:
    monkeypatch.setattr(embedding_factory, "settings", configured_settings)
    monkeypatch.setattr(reranker_factory, "settings", configured_settings)
    monkeypatch.setattr(llm_factory, "settings", configured_settings)
    _clear_factory_caches()


def _clear_factory_caches() -> None:
    embedding_factory.get_embedding_provider.cache_clear()
    reranker_factory.get_reranker.cache_clear()
    llm_factory.get_llm_provider.cache_clear()
