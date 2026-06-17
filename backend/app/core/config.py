from functools import lru_cache
from typing import Any

from pydantic import Field
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
    qdrant_api_key: str | None = None
    qdrant_collection_name: str = "hbrag_chunks_v2"
    qdrant_upsert_batch_size: int = 64
    qdrant_upsert_retry_count: int = 2
    qdrant_hybrid_candidate_multiplier: int = 4
    auto_recreate_collection: bool = False

    dense_vector_name: str = "dense"
    sparse_vector_name: str = "sparse"
    sparse_embedding_enabled: bool = True
    sparse_embedding_provider: str = "hashing"
    sparse_embedding_hash_dimensions: int = 1_048_576
    store_raw_text_in_qdrant: bool = False
    store_embedding_text_in_qdrant: bool = False

    default_chunk_size: int = 1000
    default_chunk_overlap: int = 150
    document_parser_provider: str = "auto"
    enable_docling: bool = True
    enable_docling_v6_chunking: bool = True
    docling_chunk_max_tokens: int = 350
    docling_context_budget: int = 80
    docling_context_mode: str = "metadata"
    docling_ocr_mode: str = "off"
    docling_strict_quality: bool = True
    enable_unstructured: bool = False

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

    chunk_enrichment_enabled: bool = False
    chunk_enrichment_model: str | None = None
    chunk_enrichment_max_chars: int = 6000
    chunk_enrichment_version: str = "v1"

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

    access_classification_rank: dict[str, int] = Field(
        default_factory=lambda: {
            "public_internal": 0,
            "internal": 1,
            "restricted": 2,
            "personal_data": 3,
            "confidential": 4,
            "secret": 5,
        }
    )
    access_sensitive_classifications: list[str] = Field(
        default_factory=lambda: ["personal_data", "confidential", "secret"]
    )
    access_read_all_documents: bool = True
    access_default_classification: str = "internal"
    access_default_scope: str = "corp_wide"
    access_explicit_acl_scope: str = "explicit_acl"
    access_scope_aliases: dict[str, str] = Field(
        default_factory=lambda: {
            "global": "corp_wide",
            "organization": "unit_only",
            "private": "explicit_acl",
        }
    )
    access_visibility_defaults: dict[str, dict[str, Any]] = Field(
        default_factory=lambda: {
            "global": {
                "scope": "corp_wide",
                "classification": "internal",
                "inherit_permission": True,
            },
            "subtree": {
                "scope": "subtree",
                "classification": "internal",
                "inherit_permission": True,
            },
            "private": {
                "scope": "explicit_acl",
                "classification": "restricted",
                "inherit_permission": False,
            },
            "organization": {
                "scope": "corp_wide",
                "classification": "internal",
                "inherit_permission": True,
            },
        }
    )
    access_manage_roles: list[str] = Field(
        default_factory=lambda: ["CORP_ADMIN", "COMPANY_ADMIN"]
    )
    access_leadership_roles: list[str] = Field(
        default_factory=lambda: ["SUPER_ADMIN", "CORP_ADMIN", "COMPANY_ADMIN"]
    )
    access_leadership_positions: list[str] = Field(
        default_factory=lambda: [
            "corp_leader",
            "board_head",
            "board_deputy",
            "company_director",
            "company_deputy_director",
            "department_head",
            "department_deputy",
        ]
    )
    access_corp_wide_scopes: list[str] = Field(
        default_factory=lambda: ["public_internal", "corp_wide"]
    )
    access_org_tree_scopes: list[str] = Field(default_factory=lambda: ["unit_only", "subtree"])
    permission_super_admin_role: str = "SUPER_ADMIN"
    permission_corp_admin_role: str = "CORP_ADMIN"
    permission_company_admin_role: str = "COMPANY_ADMIN"
    permission_unit_user_role: str = "UNIT_USER"
    permission_viewer_role: str = "VIEWER"
    permission_admin_roles: list[str] = Field(
        default_factory=lambda: ["SUPER_ADMIN", "CORP_ADMIN", "COMPANY_ADMIN"]
    )
    permission_upload_roles: list[str] = Field(
        default_factory=lambda: ["SUPER_ADMIN", "CORP_ADMIN", "COMPANY_ADMIN", "UNIT_USER"]
    )
    permission_cross_org_upload_roles: list[str] = Field(
        default_factory=lambda: ["CORP_ADMIN", "COMPANY_ADMIN"]
    )
    knowledge_base_view_permissions: list[str] = Field(
        default_factory=lambda: ["owner", "admin", "editor", "viewer"]
    )
    knowledge_base_manage_permissions: list[str] = Field(
        default_factory=lambda: ["owner", "admin"]
    )
    knowledge_base_upload_permissions: list[str] = Field(
        default_factory=lambda: ["owner", "admin", "editor"]
    )

    @property
    def async_database_url(self) -> str:
        if self.database_url.startswith("postgresql://"):
            return self.database_url.replace("postgresql://", "postgresql+asyncpg://", 1)
        return self.database_url


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
