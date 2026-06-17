from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field, StringConstraints, model_validator

SearchQuery = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class DocumentUploadResponse(BaseModel):
    document_id: UUID
    filename: str
    status: str
    storage_path: str


class DocumentBatchUploadItem(BaseModel):
    filename: str
    document_id: UUID | None = None
    status: str
    success: bool
    error: str | None = None


class DocumentBatchUploadResponse(BaseModel):
    items: list[DocumentBatchUploadItem]
    success_count: int
    failed_count: int


class DocumentParseResponse(BaseModel):
    document_id: UUID
    status: str
    character_count: int
    preview: str


class ChunkPreview(BaseModel):
    chunk_index: int
    content: str
    start_char: int
    end_char: int


class DocumentChunkRequest(BaseModel):
    chunk_size: int | None = Field(default=None, ge=300, le=4000)
    chunk_overlap: int | None = Field(default=None, ge=0)
    chunk_mode: Literal[
        "recursive",
        "legal_article",
        "table_aware",
        "hybrid_structured",
        "docling_v6",
        "slide_page",
        "heading_aware",
    ] | None = None
    profile: Literal[
        "auto", "legal_admin", "catalog_table", "general", "spreadsheet", "slide"
    ] | None = None

    @model_validator(mode="after")
    def validate_overlap(self) -> "DocumentChunkRequest":
        if self.chunk_overlap is not None:
            limit = (self.chunk_size if self.chunk_size is not None else 4000) // 2
            if self.chunk_overlap > limit:
                raise ValueError(
                    "chunk_overlap must be between 0 and half of chunk_size."
                )
        return self


class DocumentChunkResponse(BaseModel):
    document_id: UUID
    status: str
    chunk_count: int
    preview: list[ChunkPreview]


class DocumentChunkEnrichmentRequest(BaseModel):
    force: bool = False


class ChunkEnrichmentPreview(BaseModel):
    chunk_index: int
    status: str
    summary: str | None = None
    keywords: list[str] = Field(default_factory=list)
    enriched_content_preview: str | None = None
    error: str | None = None


class DocumentChunkEnrichmentResponse(BaseModel):
    document_id: UUID
    status: str
    enriched_count: int
    failed_count: int
    skipped_count: int
    preview: list[ChunkEnrichmentPreview]


class DocumentVectorIndexResponse(BaseModel):
    document_id: UUID
    status: str
    indexed_chunk_count: int


class DocumentDeleteResponse(BaseModel):
    document_id: UUID
    deleted: bool
    deleted_files: int
    vector_points_deleted: bool

class DocumentAccessPolicy(BaseModel):
    scope: str | None = None
    classification: str | None = None
    owner_org_id: str | None = None
    owner_org_path: str | None = None
    business_domains: list[str] = Field(default_factory=list)
    project_codes: list[str] = Field(default_factory=list)
    allowed_org_ids: list[str] = Field(default_factory=list)
    allowed_org_paths: list[str] = Field(default_factory=list)
    allowed_role_names: list[str] = Field(default_factory=list)
    allowed_group_codes: list[str] = Field(default_factory=list)
    allowed_user_ids: list[str] = Field(default_factory=list)
    denied_org_ids: list[str] = Field(default_factory=list)
    denied_org_paths: list[str] = Field(default_factory=list)
    denied_role_names: list[str] = Field(default_factory=list)
    denied_group_codes: list[str] = Field(default_factory=list)
    denied_user_ids: list[str] = Field(default_factory=list)
    inherit_permission: bool = True
    access_policy_id: str | None = None

class DocumentAccessResponse(BaseModel):
    document_id: UUID
    access: DocumentAccessPolicy

class DocumentAccessUpdateRequest(DocumentAccessPolicy):
    pass

class DocumentAccessTestRequest(BaseModel):
    user_id: UUID
    action: str = "open_document"

class DocumentAccessDecisionResponse(BaseModel):
    allowed: bool
    reason: str
    matched_policy: str | None = None


class GraphIndexRequest(BaseModel):
    force_rebuild: bool = False
    extractor_provider: Literal["fake", "llm"] = "llm"
    max_entities_per_chunk: int = Field(default=30, ge=1, le=200)
    max_relations_per_chunk: int = Field(default=40, ge=0, le=200)


class GraphIndexResponse(BaseModel):
    document_id: UUID
    chunks_processed: int
    entities_extracted: int
    relations_extracted: int
    merged_entities: int
    merged_relations: int
    status: str


class DocumentPerson(BaseModel):
    id: UUID
    username: str
    full_name: str | None = None


class DocumentOrganization(BaseModel):
    id: UUID
    ma_dviqly: str
    ten_dviqly: str
    dvi_level: int

class DocumentKnowledgeBase(BaseModel):
    id: UUID
    name: str
    visibility: str
    organization: DocumentOrganization | None = None
    owner: DocumentPerson | None = None


class DocumentListItem(BaseModel):
    document_id: UUID
    title: str
    status: str
    filename: str | None
    organization: DocumentOrganization | None
    knowledge_base: DocumentKnowledgeBase | None = None
    uploaded_by: DocumentPerson | None
    visibility: str
    document_profile: str | None = None
    parsed_character_count: int
    created_at: datetime
    updated_at: datetime
    chunk_count: int
    vector_indexed_count: int | None = None
    pipeline_logs_count: int = 0
    graph_indexed: bool = False


class DocumentListResponse(BaseModel):
    items: list[DocumentListItem]
    total: int
    limit: int
    offset: int


class DocumentFileResponse(BaseModel):
    id: str
    filename: str
    mime_type: str
    storage_path: str
    file_size: int
    created_at: str
    download_url: str


class DocumentPipelineLogResponse(BaseModel):
    action: str
    status: str
    message: str | None
    metadata: dict[str, object] | None
    created_at: datetime

class DocumentChunkDetailResponse(BaseModel):
    id: UUID
    chunk_index: int
    content: str
    token_count: int | None = None
    metadata: dict[str, object]
    created_at: datetime


class GraphExtractionLogResponse(BaseModel):
    status: str
    entity_count: int
    relation_count: int
    merged_entity_count: int
    merged_relation_count: int
    error_message: str | None
    metadata: dict[str, object] | None
    created_at: datetime


class GraphDocumentStatusResponse(BaseModel):
    graph_indexed: bool
    chunks_processed: int
    entity_count: int
    relation_count: int
    last_indexed_at: datetime | None
    error_message: str | None


class DocumentDetailResponse(DocumentListItem):
    preview_text: str | None = None
    files: list[DocumentFileResponse]
    chunks: list[DocumentChunkDetailResponse] = []
    pipeline_logs: list[DocumentPipelineLogResponse]
    access_logs_summary: dict[str, int]
    latest_retrieval_logs: list[dict[str, object]]
    graph_status: GraphDocumentStatusResponse | None = None
    graph_extraction_logs: list[GraphExtractionLogResponse] = []


class VectorSearchRequest(BaseModel):
    query: str = Field(min_length=1)
    top_k: int = Field(default=5, ge=1, le=50)
    knowledge_base_ids: list[UUID] | None = None


class VectorSearchResult(BaseModel):
    chunk_id: UUID | str
    document_id: UUID | str
    score: float
    content_preview: str
    metadata: dict[str, object]


class VectorSearchResponse(BaseModel):
    query: str
    top_k: int
    results: list[VectorSearchResult]


class KeywordSearchRequest(BaseModel):
    query: SearchQuery
    top_k: int = Field(default=5, ge=1, le=50)
    knowledge_base_ids: list[UUID] | None = None


class KeywordSearchResult(BaseModel):
    chunk_id: UUID | str
    document_id: UUID | str
    score: float
    content_preview: str
    metadata: dict[str, object]


class KeywordSearchResponse(BaseModel):
    query: str
    top_k: int
    results: list[KeywordSearchResult]


class HybridSearchRequest(BaseModel):
    query: SearchQuery
    top_k: int = Field(default=5, ge=1, le=50)
    vector_weight: float = Field(default=1.0, ge=0.0)
    keyword_weight: float = Field(default=1.0, ge=0.0)
    knowledge_base_ids: list[UUID] | None = None


class HybridSearchResult(BaseModel):
    chunk_id: UUID | str
    document_id: UUID | str
    fused_score: float
    vector_score: float | None = None
    keyword_score: float | None = None
    content_preview: str
    metadata: dict[str, object]
    source_flags: list[Literal["vector", "keyword", "graph", "neighbor", "lexical_exact"]]


class HybridSearchResponse(BaseModel):
    query: str
    top_k: int
    vector_weight: float
    keyword_weight: float
    results: list[HybridSearchResult]


class RerankSearchRequest(BaseModel):
    query: SearchQuery
    top_k: int = Field(default=5, ge=1, le=50)
    candidate_k: int = Field(default=20, ge=1, le=200)
    knowledge_base_ids: list[UUID] | None = None

    @model_validator(mode="after")
    def validate_candidate_window(self) -> "RerankSearchRequest":
        if self.candidate_k < self.top_k:
            raise ValueError("candidate_k must be greater than or equal to top_k.")
        return self


class RerankSearchResult(BaseModel):
    chunk_id: UUID | str
    document_id: UUID | str
    rerank_score: float
    fused_score: float
    vector_score: float | None = None
    keyword_score: float | None = None
    content_preview: str
    metadata: dict[str, object]
    source_flags: list[Literal["vector", "keyword", "graph", "neighbor", "lexical_exact"]]


class RerankSearchResponse(BaseModel):
    query: str
    top_k: int
    candidate_k: int
    results: list[RerankSearchResult]
