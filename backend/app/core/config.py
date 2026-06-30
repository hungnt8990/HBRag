from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BACKEND_ENV_FILE,
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
    # Cho phép localhost + mọi IP nội bộ (10.x.x.x, 172.16-31.x, 192.168.x.x) trên mọi
    # cổng -> FE chạy ở bất kỳ máy nội bộ nào (vd http://10.72.113.21:3000) đều gọi được,
    # khỏi phải liệt kê từng IP trong cors_allowed_origins.
    cors_allowed_origin_regex: str | None = (
        r"https?://(localhost|127\.0\.0\.1|10\.\d+\.\d+\.\d+|"
        r"172\.(1[6-9]|2\d|3[01])\.\d+\.\d+|192\.168\.\d+\.\d+)(:\d+)?"
    )

    database_url: str = "postgresql://hbrag:hbrag_password@localhost:5432/hbrag"
    database_echo: bool = False
    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: str | None = None
    qdrant_collection_name: str = "hbrag_chunks_v2"
    qdrant_artifact_collection_name: str = "hbrag_artifacts_v1"
    # Thiết kế DOffice 3-DB (job đồng bộ mới): 2 collection Qdrant + 1 index ES BM25.
    # Col 1: vector từng chunk nội dung. Col 2: 1 point/văn bản cho metadata (mọi
    # trường TRỪ noi_dung) — tìm theo ngữ nghĩa (dense) lẫn ký hiệu (sparse).
    qdrant_chunks_collection_name: str = "hbrag_doffice_chunks_v1"
    qdrant_docmeta_collection_name: str = "hbrag_doffice_docmeta_v1"
    # ES BM25 cấp văn bản (không vector, không chunk).
    doffice_documents_index_name: str = "hbrag_doffice_documents_v1"
    # ES BM25 cấp CHUNK (nhánh 2): mỗi chunk = 1 record + ACL nén, để BM25 đúng đoạn/căn cứ.
    doffice_chunks_index_name: str = "hbrag_doffice_chunks_es_v1"
    # Dev: vẫn ghi chunk vào PostgreSQL để soi; product có thể tắt.
    store_chunks_in_pg: bool = True
    # DOffice job Qdrant: làm sạch -> chunk -> LƯU chunk vào PG -> embedding. True = giữ
    # chunk trong PostgreSQL (đặt False nếu chỉ muốn PG giữ raw, chunk xoá sau khi embed).
    doffice_store_chunks_in_pg: bool = True
    # Số chunk/1 request embed khi job Qdrant chạy. 1 = embed TỪNG chunk (request nhỏ, dễ
    # qua gateway yếu, thấy tiến độ từng chunk). Tăng lên để gộp lô (nhanh hơn nếu gateway khỏe).
    doffice_embed_request_batch_size: int = 1
    qdrant_upsert_batch_size: int = 64
    qdrant_upsert_retry_count: int = 2
    qdrant_hybrid_candidate_multiplier: int = 4
    auto_recreate_collection: bool = False
    # Validate/tạo collection RAG GENERIC (non-doffice) khi startup. Dự án chỉ dùng DOffice
    # -> TẮT để không tự tạo lại hbrag_chunks_qwen3_8b_v1 rỗng. Bật True nếu cần RAG generic.
    validate_generic_vector_store_on_startup: bool = False

    # Qdrant performance — mặc định TẮT/an toàn; chỉ có hiệu lực khi tạo/recreate
    # collection mới (hnsw/quantization/on_disk) hoặc khi query (search_hnsw_ef).
    qdrant_quantization_enabled: bool = False          # INT8 quantization (bật khi recreate)
    qdrant_vector_on_disk: bool = False                # vector gốc trên đĩa (RAM < ~300GB)
    qdrant_hnsw_m: int = 16                             # số neighbor HNSW
    qdrant_hnsw_ef_construct: int = 100                # chất lượng dựng index
    qdrant_hnsw_on_disk: bool = False                  # HNSW index trên đĩa
    qdrant_search_hnsw_ef: int = 128                   # ef lúc query (accuracy vs speed)
    qdrant_quantization_rescore: bool = True           # rescore sau khi search quantized
    qdrant_quantization_oversampling: float = 2.0      # oversample trước rescore
    qdrant_shard_number: int = 1                       # số shard (4-8 cho ~20M chunks)
    qdrant_replication_factor: int = 1                 # replica (2 cho HA)
    qdrant_memmap_threshold: int = 20000               # segment size -> dùng mmap

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

    doffice_es_url: str = "https://10.72.121.232:9200/doffice_vanban/_search"
    doffice_es_timeout_seconds: int = 30
    doffice_es_username: str | None = None
    doffice_es_password: str | None = None
    doffice_es_verify_ssl: bool = False  # ES nội bộ self-signed -> mặc định bỏ verify

    # ACL giả định: API DOffice hiện CHƯA trả don_vi_list/phong_ban_list/ca_nhan_list.
    # Khi bật, nếu bản ghi thiếu 3 list này thì dùng giá trị mẫu dưới để bộ quyền vẫn chạy
    # (id phải có trong danh mục: đơn vị 269, phòng 43310 là ví dụ thật). Tắt khi API trả ACL.
    doffice_synthetic_acl_enabled: bool = True
    doffice_synthetic_don_vi_list: list[int] = Field(default_factory=lambda: [269])
    doffice_synthetic_phong_ban_list: list[int] = Field(default_factory=lambda: [43310])
    doffice_synthetic_ca_nhan_list: list[int] = Field(default_factory=list)

    # Chunker DOffice v2: bỏ table-explosion (mỗi bảng 1 vài chunk thay vì 4 "view"),
    # làm sạch text trước khi chunk. Tắt (False) để quay lại builder cũ (legacy).
    doffice_chunker_v2_enabled: bool = True

    elasticsearch_enabled: bool = False
    elasticsearch_url: str = "http://localhost:9200"
    elasticsearch_index_name: str = "hbrag_chunks_bm25_v1"
    elasticsearch_timeout_seconds: int = 30
    elasticsearch_index_batch_size: int = 128
    elasticsearch_fallback_to_postgres: bool = True
    # ES index tuning — chỉ áp dụng khi tạo index MỚI; index cũ dùng
    # scripts/maintenance/es_update_settings.py để cập nhật.
    elasticsearch_number_of_shards: int = 8
    elasticsearch_number_of_replicas: int = 1
    elasticsearch_refresh_interval: str = "30s"

    # Redis cache cho kết quả search (mặc định TẮT; bật khi redis_url có giá trị).
    redis_url: str | None = None
    search_cache_ttl_seconds: int = 300
    search_cache_enabled: bool = False

    # Two-stage retrieval (mặc định TẮT) — Stage1 tìm document, Stage2 search chunk.
    two_stage_retrieval_enabled: bool = False
    two_stage_document_index_url: str | None = None    # None = dùng cùng elasticsearch_url
    two_stage_stage1_top_n: int = 50
    two_stage_stage1_min_results: int = 3              # fallback full search khi stage1 < ngưỡng
    two_stage_chunk_threshold: int = 5_000_000         # bật two-stage khi corpus đủ lớn
    # Retrieval thiết kế DOffice mới: Stage-1 = ES BM25 doc ∪ Qdrant docmeta; Stage-2 =
    # Qdrant chunks. Bật sau khi job đồng bộ 3-DB đã đổ dữ liệu (TẮT mặc định).
    doffice_retrieval_enabled: bool = False
    # Document index BBQ embedding (Stage 1 hybrid kNN + BM25)
    two_stage_document_embedding_enabled: bool = False  # bật khi muốn dùng BBQ vector
    two_stage_document_embedding_text: str = "trich_yeu_tom_tat"  # nguồn text để embed
    # API tìm kiếm văn bản: API key tĩnh để chặn truy cập (rỗng = mở, cho dev).
    document_search_api_key: str | None = None
    # Tìm kiếm văn bản CHỈ qua ES BM25 + ACL (bỏ kNN/embed) — bật khi model embedding
    # chết/chậm để API không treo chờ embed. False = cho phép hybrid (kNN+BM25) như cũ.
    document_search_bm25_only: bool = True
    # API cập nhật ACL cho DOffice gọi: API key tĩnh (rỗng = mở, cho dev).
    doffice_acl_api_key: str | None = None

    # Đăng nhập bằng Active Directory (LDAP). Khi bật, /api/auth/login-ad xác thực tài
    # khoản AD rồi tra dm_nhan_vien lấy id_nv để áp ACL (tự tạo User nếu chưa có).
    ad_enabled: bool = False
    ad_domain: str = "cpc-ad.evncpc.vn"  # = DomainAd .NET; bind dạng domain\\username
    ad_port: int = 389
    ad_use_ssl: bool = False
    ad_timeout_seconds: int = 8
    ad_email_suffix: str = "@cpc.vn"  # suy email từ username khi tra dm_nhan_vien

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
    chunk_enrichment_mode: str = "selective"
    chunk_enrichment_min_chars: int = 300
    chunk_enrichment_max_llm_chunks_per_document: int | None = None
    chunk_enrichment_concurrency: int = 2
    chunk_enrichment_prompt_version: str = "v2"
    use_enriched_content_for_embedding: bool = True
    retrieval_enrichment_enabled: bool = False
    enrichment_force_on_reingest: bool = False
    enrichment_update_keyword_search_vector: bool = True
    chunk_enrichment_provider: str | None = None
    chunk_enrichment_base_url: str | None = None
    chunk_enrichment_api_key: str | None = None
    chunk_enrichment_model: str | None = None
    chunk_enrichment_max_chars: int = 6000
    chunk_enrichment_version: str = "v1"
    embedding_enrichment_provider: str | None = None
    embedding_enrichment_base_url: str | None = None
    embedding_enrichment_api_key: str | None = None
    embedding_enrichment_model: str | None = None
    embedding_enrichment_max_chars: int = 6000
    embedding_enrichment_version: str = "v1"
    reingest_enrichment_provider: str | None = None
    reingest_enrichment_base_url: str | None = None
    reingest_enrichment_api_key: str | None = None
    reingest_enrichment_model: str | None = None
    reingest_enrichment_max_chars: int = 6000
    reingest_enrichment_version: str = "v1"

    enable_offline_enrichment: bool = True
    enable_query_enrichment: bool = True
    enable_context_expansion: bool = True
    enable_completeness_check: bool = False
    enable_second_retrieval: bool = False
    max_second_retrieval_rounds: int = 1
    overview_top_k: int = 12
    raw_top_k: int = 0
    summary_top_k: int = 6
    table_top_k: int = 10
    max_context_chars: int = 20_000

    enable_chunk_enrichment_at_ingest: bool = False
    enable_chunk_enrichment_at_retrieval: bool = False
    enable_knowledge_artifact_compilation: bool = True
    enable_llm_artifact_extraction: bool = False
    enable_artifact_first_retrieval: bool = True
    enable_chunk_fallback: bool = True
    enable_neighbor_expansion: bool = True
    enable_graph_expansion: bool = True
    artifact_confidence_threshold: float = 0.45
    retrieval_token_budget: int = 6000
    max_artifacts: int = 6
    max_chunks: int = 8

    graph_enabled: bool = False
    graph_provider: str = "neo4j"
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_username: str = "neo4j"
    neo4j_password: str = "hbrag_neo4j_password"
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
    graph_auto_index_on_ingest: bool = False

    storage_backend: str = "minio"
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
