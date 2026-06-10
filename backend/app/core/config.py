from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=("../.env", ".env", "backend/.env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "HBRag API"
    app_version: str = "0.1.0"
    environment: str = "local"
    cors_allowed_origins: list[str] = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ]
    cors_allowed_origin_regex: str | None = r"http://(localhost|127\.0\.0\.1):[0-9]+"

    database_url: str = "postgresql://hbrag:hbrag_password@localhost:5432/hbrag"
    database_echo: bool = False
    qdrant_url: str = "http://localhost:6333"
    qdrant_collection_name: str = "hbrag_chunks"
    qdrant_upsert_batch_size: int = 128
    auto_recreate_collection: bool = False

    default_chunk_size: int = 1000
    default_chunk_overlap: int = 150
    document_parser_provider: str = "auto"
    enable_docling: bool = False
    enable_unstructured: bool = False
    chunk_router_provider: str = "heuristic"
    enable_semantic_chunking: bool = False

    memory_provider: str = "local"
    memory_enabled: bool = True
    mem0_enabled: bool = False
    mem0_api_key: str | None = None
    mem0_mode: str = "oss"
    mem0_org_id: str | None = None
    mem0_project_id: str | None = None
    mem0_user_prefix: str = "hbrag"
    memory_top_k: int = 5
    memory_auto_save: bool = True
    memory_inject_into_prompt: bool = True
    session_summary_every_n_messages: int = 10

    embedding_provider: str = "fake"
    embedding_base_url: str | None = None
    embedding_api_key: str | None = None
    embedding_model: str | None = None
    embedding_dimension: int = 384

    reranker_provider: str = "fake"
    reranker_base_url: str | None = None
    reranker_api_key: str | None = None
    reranker_model: str | None = None
    reranker_endpoint_path: str = "/rerank"
    bge_reranker_model: str | None = None

    llm_provider: str = "fake"
    llm_base_url: str | None = None
    llm_api_key: str | None = None
    llm_model: str | None = None

    graph_enabled: bool = False
    graph_provider: str = "neo4j"
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_username: str = "neo4j"
    neo4j_password: str = "hbrag_password"
    graph_entity_extraction_enabled: bool = True
    graph_relation_extraction_enabled: bool = True
    graph_expansion_enabled: bool = True
    graph_max_entities_per_chunk: int = 30
    graph_max_relations_per_chunk: int = 40
    graph_expansion_depth: int = 1
    graph_expansion_limit: int = 20
    graph_min_entity_confidence: float = 0.4
    graph_min_relation_confidence: float = 0.4
    graph_extractor_provider: str = "llm"
    graph_entity_merge_enabled: bool = True
    graph_relation_merge_enabled: bool = True

    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin123"
    minio_bucket: str = "hbrag-documents"
    minio_secure: bool = False

    jwt_secret_key: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60

    @property
    def async_database_url(self) -> str:
        if self.database_url.startswith("postgresql://"):
            return self.database_url.replace("postgresql://", "postgresql+asyncpg://", 1)
        return self.database_url


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
