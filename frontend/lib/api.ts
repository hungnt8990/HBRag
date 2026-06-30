const configuredApiBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL?.trim();
const API_BASE_URL = configuredApiBaseUrl
  ? configuredApiBaseUrl.replace(/\/$/, "")
  : "http://localhost:8000";
const TOKEN_STORAGE_KEY = "hbrag_access_token";

export type AuthUser = {
  id: string;
  username: string;
  email: string | null;
  full_name: string | null;
  organization: {
    id: string;
    ma_dviqly: string;
    ma_dviqly_cha: string | null;
    ten_dviqly: string;
    dvi_level: number;
    parent_id: string | null;
  };
  roles: string[];
  is_active: boolean;
};

export type AccessCatalogRole = {
  id: string;
  name: string;
  description: string | null;
};

export type AccessCatalogResponse = {
  organizations: AuthUser["organization"][];
  roles: AccessCatalogRole[];
  groups: string[];
};

export type LoginResponse = {
  access_token: string;
  token_type: string;
};

export type DocumentUploadResponse = {
  document_id: string;
  filename: string;
  status: string;
  storage_path: string;
};

export type DocumentBatchUploadItem = {
  filename: string;
  document_id: string | null;
  status: string;
  success: boolean;
  error: string | null;
};

export type DocumentBatchUploadResponse = {
  items: DocumentBatchUploadItem[];
  success_count: number;
  failed_count: number;
};

export type DofficeIngestRequest = {
  id_vb: string;
  force_refresh?: boolean;
  enable_enrichment?: boolean;
};

export type DofficeIngestResponse = {
  status: "success" | "skipped";
  id_vb: string;
  ky_hieu: string | null;
  trich_yeu: string | null;
  noi_ban_hanh: string | null;
  chunks_created: number;
  document_id: string;
  source_type: "doffice_elasticsearch";
  message: string | null;
};

export type DocumentAccessPolicy = {
  scope?: string | null;
  classification?: string | null;
  owner_org_id?: string | null;
  owner_org_path?: string | null;
  business_domains: string[];
  project_codes: string[];
  allowed_org_ids: string[];
  allowed_org_paths: string[];
  allowed_role_names: string[];
  allowed_group_codes: string[];
  allowed_user_ids: string[];
  denied_org_ids: string[];
  denied_org_paths: string[];
  denied_role_names: string[];
  denied_group_codes: string[];
  denied_user_ids: string[];
  inherit_permission: boolean;
  access_policy_id?: string | null;
};

export type DocumentAccessResponse = {
  document_id: string;
  access: DocumentAccessPolicy;
};

export type UploadAccessOptions = {
  organization_id?: string;
  access_scope?: string;
  classification?: string;
  allowed_org_ids?: string;
  allowed_org_paths?: string;
  allowed_role_names?: string;
  allowed_group_codes?: string;
  allowed_user_ids?: string;
  denied_org_ids?: string;
  denied_org_paths?: string;
  denied_role_names?: string;
  denied_group_codes?: string;
  denied_user_ids?: string;
  inherit_permission?: boolean;
};

export type DocumentParseResponse = {
  document_id: string;
  status: string;
  character_count: number;
  preview: string;
};

export type ChunkPreview = {
  chunk_index: number;
  content: string;
  start_char: number;
  end_char: number;
};

export type DocumentChunkResponse = {
  document_id: string;
  status: string;
  chunk_count: number;
  preview: ChunkPreview[];
};

export type DocumentVectorIndexResponse = {
  document_id: string;
  status: string;
  indexed_chunk_count: number;
};

export type DocumentDeleteResponse = {
  document_id: string;
  deleted: boolean;
  deleted_files: number;
  vector_points_deleted: boolean;
};

export type GraphIndexResponse = {
  document_id: string;
  chunks_processed: number;
  entities_extracted: number;
  relations_extracted: number;
  merged_entities: number;
  merged_relations: number;
  status: string;
};

export type RagCitation = {
  citation_index: number;
  chunk_id: string;
  document_id: string;
  document_title?: string | null;
  file_name?: string | null;
  chunk_index: number;
  quote: string | null;
  article_number?: string | null;
  article_title?: string | null;
  chapter_title?: string | null;
  page_number?: number | null;
  source_flags?: Array<"vector" | "keyword" | "graph" | "neighbor">;
  metadata: Record<string, unknown>;
};

export type DocumentPerson = {
  id: string;
  username: string;
  full_name: string | null;
};

export type DocumentOrganization = {
  id: string;
  ma_dviqly: string;
  ten_dviqly: string;
  dvi_level: number;
};

export type DocumentKnowledgeBase = {
  id: string;
  name: string;
  visibility: string;
  organization: DocumentOrganization | null;
  owner: DocumentPerson | null;
};

export type DocumentListItem = {
  document_id: string;
  title: string;
  status: string;
  source_type: string;
  id_vb?: string | null;
  ky_hieu?: string | null;
  qdrant_point_count?: number | null;
  filename: string | null;
  organization: DocumentOrganization | null;
  knowledge_base: DocumentKnowledgeBase | null;
  uploaded_by: DocumentPerson | null;
  visibility: string;
  document_profile?: string | null;
  parsed_character_count: number;
  chunk_count: number;
  vector_indexed_count: number | null;
  pipeline_logs_count: number;
  graph_indexed: boolean;
  created_at: string;
  updated_at: string;
};

export type DocumentListResponse = {
  items: DocumentListItem[];
  total: number;
  limit: number;
  offset: number;
};

export type DocumentDetailFile = {
  id: string;
  filename: string;
  mime_type: string;
  storage_path: string;
  file_size: number;
  download_url: string;
  created_at: string;
};

export type DocumentPipelineLog = {
  action: string;
  status: string;
  message: string | null;
  metadata: Record<string, unknown> | null;
  created_at: string;
};

export type DocumentChunkDetail = {
  id: string;
  chunk_index: number;
  content: string;
  token_count: number | null;
  metadata: Record<string, unknown>;
  created_at: string;
};

export type GraphDocumentStatus = {
  graph_indexed: boolean;
  chunks_processed: number;
  entity_count: number;
  relation_count: number;
  last_indexed_at: string | null;
  error_message: string | null;
};

export type DocumentDetailResponse = DocumentListItem & {
  preview_text: string | null;
  files: DocumentDetailFile[];
  chunks: DocumentChunkDetail[];
  pipeline_logs: DocumentPipelineLog[];
  access_logs_summary: Record<string, number>;
  latest_retrieval_logs: Array<Record<string, unknown>>;
  graph_status: GraphDocumentStatus | null;
  graph_extraction_logs: Array<Record<string, unknown>>;
};

export type RagChatRequest = {
  query: string;
  session_id?: string;
  document_id?: string;
  organization_id?: string;
  include_descendants?: boolean;
  use_memory?: boolean;
  use_mem0?: boolean;
  memory_top_k?: number;
  use_graph?: boolean;
  graph_expansion_depth?: number;
  graph_expansion_limit?: number;
  admin_view_all?: boolean;
};

export type MemorySettings = {
  memory_enabled: boolean;
  memory_provider: string;
  mem0_enabled: boolean;
  memory_top_k: number;
  memory_auto_save: boolean;
  memory_inject_into_prompt: boolean;
};

export type MemoryItem = {
  id: string | null;
  content: string;
  memory_type: string;
  source: string;
  score: number | null;
  metadata: Record<string, unknown>;
};

export type MemoryType =
  | "preference"
  | "task"
  | "entity"
  | "instruction"
  | "fact";

export type AnswerMode = "generative" | "extractive" | "hybrid";
export type AnswerStyle = "concise" | "detailed" | "policy_explainer" | "table_qa";
export type DocumentProfile = string;

export type HeadingRuleConfig = {
  name: string;
  level: number;
  pattern: string;
  metadata_key?: string | null;
  number_metadata_key?: string | null;
  metadata_value?: "title" | "display_text" | string;
  boundary?: boolean;
  enabled?: boolean;
};

export type QueryIntentRuleConfig = {
  direct_terms?: string[];
  required_any_terms?: string[];
  specific_item_patterns?: string[];
  phrases?: string[];
  [key: string]: unknown;
};

export type QueryIntentRulesConfig = Record<string, QueryIntentRuleConfig>;

export type ProfileConfig = {
  chunk_mode: ChunkMode;
  chunk_size: number;
  chunk_overlap: number;
  top_k: number;
  candidate_k: number;
  answer_mode: AnswerMode;
  answer_style: AnswerStyle;
  max_context_chars: number;
  heading_rules?: HeadingRuleConfig[];
  query_intent_rules?: QueryIntentRulesConfig;
  [key: string]: unknown;
};

export type HeadingRuleTestMatch = {
  start: number;
  end: number;
  level: number;
  name: string;
  label: string;
  number: string;
  title: string;
  display_text: string;
  boundary: boolean;
};

export type HeadingRuleTestResponse = {
  matches: HeadingRuleTestMatch[];
};

export type ProfilesResponse = {
  default_profile: string;
  profiles: string[];
  configs: Record<string, ProfileConfig>;
};

export async function getProfiles(): Promise<ProfilesResponse> {
  return requestJson<ProfilesResponse>("/api/admin/profiles", {
    method: "GET",
  });
}

export async function updateProfileConfig(
  profileName: string,
  config: ProfileConfig,
): Promise<ProfilesResponse> {
  return requestJson<ProfilesResponse>(`/api/admin/profiles/${profileName}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ config }),
  });
}

export async function testHeadingRules(options: {
  profile: string;
  sample_text: string;
  config?: ProfileConfig;
}): Promise<HeadingRuleTestResponse> {
  return requestJson<HeadingRuleTestResponse>(
    "/api/admin/profiles/test-heading-rules",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(options),
    },
  );
}

export type RagChatResponse = {
  session_id: string;
  user_message_id: string;
  assistant_message_id: string;
  answer: string;
  citations: RagCitation[];
};

export type RuntimeConfigResponse = {
  embedding_provider: string;
  embedding_base_url: string | null;
  embedding_model: string | null;
  embedding_dimension: number;
  reranker_provider: string;
  reranker_base_url: string | null;
  reranker_model: string | null;
  llm_provider: string;
  llm_base_url: string | null;
  llm_model: string | null;
  chunk_enrichment_enabled: boolean;
  retrieval_enrichment_enabled: boolean;
  enrichment_force_on_reingest: boolean;
  enrichment_update_keyword_search_vector: boolean;
  chunk_enrichment_provider: string | null;
  chunk_enrichment_base_url: string | null;
  chunk_enrichment_model: string | null;
  chunk_enrichment_max_chars: number;
  chunk_enrichment_version: string;
  embedding_enrichment_provider: string | null;
  embedding_enrichment_base_url: string | null;
  embedding_enrichment_model: string | null;
  embedding_enrichment_max_chars: number;
  embedding_enrichment_version: string;
  reingest_enrichment_provider: string | null;
  reingest_enrichment_base_url: string | null;
  reingest_enrichment_model: string | null;
  reingest_enrichment_max_chars: number;
  reingest_enrichment_version: string;
  chunk_enrichment_enablement_source: string;
  vector_collection_name: string;
  auto_recreate_collection: boolean;
  default_chunk_size: number;
  default_chunk_overlap: number;
  graph_enabled: boolean;
  graph_provider: string;
  graph_expansion_enabled: boolean;
  graph_expansion_depth: number;
  graph_expansion_limit: number;
  streaming_supported: boolean;
};

export type GraphHealthResponse = {
  enabled: boolean;
  provider: string;
  healthy: boolean;
  message: string;
};

export type IngestionStep = {
  name: string;
  state: "idle" | "running" | "succeeded" | "failed" | string;
  started_at: string | null;
  completed_at: string | null;
  duration_ms: number | null;
  output: Record<string, unknown>;
  error: string | null;
};

export type IngestionLog = {
  timestamp: string;
  step: string;
  level: "info" | "success" | "error" | string;
  message: string;
  duration_ms: number | null;
};

export type IngestionJob = {
  job_id: string;
  filename: string;
  content_type: string | null;
  status: "queued" | "running" | "succeeded" | "failed" | string;
  created_at: string;
  updated_at: string;
  document_id: string | null;
  error: string | null;
  ingestion_profile?: string;
  resolved_ingestion_profile?: string | null;
  steps: IngestionStep[];
  logs: IngestionLog[];
};

export class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

export function getStoredAccessToken(): string | null {
  if (typeof window === "undefined") {
    return null;
  }
  return window.localStorage.getItem(TOKEN_STORAGE_KEY);
}

export function storeAccessToken(token: string): void {
  window.localStorage.setItem(TOKEN_STORAGE_KEY, token);
}

export function clearAccessToken(): void {
  window.localStorage.removeItem(TOKEN_STORAGE_KEY);
}

export async function login(username: string, password: string): Promise<LoginResponse> {
  const response = await requestJson<LoginResponse>(
    "/api/auth/login",
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ username, password }),
    },
    { skipAuth: true },
  );
  storeAccessToken(response.access_token);
  return response;
}

export async function getCurrentUser(): Promise<AuthUser> {
  return requestJson<AuthUser>("/api/auth/me", {
    method: "GET",
  });
}

export async function getAccessCatalog(): Promise<AccessCatalogResponse> {
  return requestJson<AccessCatalogResponse>("/api/auth/access-catalog", {
    method: "GET",
  });
}

function appendUploadAccessOptions(
  formData: FormData,
  access?: UploadAccessOptions,
) {
  if (!access) {
    return;
  }
  for (const [key, value] of Object.entries(access)) {
    if (value === undefined || value === null || value === "") {
      continue;
    }
    formData.append(key, String(value));
  }
}

export async function uploadDocument(
  file: File,
  access?: UploadAccessOptions,
): Promise<DocumentUploadResponse> {
  const formData = new FormData();
  formData.append("file", file);
  appendUploadAccessOptions(formData, access);

  return requestJson<DocumentUploadResponse>("/api/documents/upload", {
    method: "POST",
    body: formData,
  });
}

export async function uploadDocumentBatch(
  files: File[],
  access?: UploadAccessOptions,
): Promise<DocumentBatchUploadResponse> {
  const formData = new FormData();
  for (const file of files) {
    formData.append("files", file);
  }
  appendUploadAccessOptions(formData, access);
  return requestJson<DocumentBatchUploadResponse>("/api/documents/upload-batch", {
    method: "POST",
    body: formData,
  });
}

export async function ingestDofficeDocument(
  payload: DofficeIngestRequest,
): Promise<DofficeIngestResponse> {
  return requestJson<DofficeIngestResponse>("/api/documents/doffice/ingest", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export async function enqueueDofficeIngestionJob(
  payload: DofficeIngestRequest,
): Promise<IngestionJob> {
  return requestJson<IngestionJob>("/api/documents/doffice/ingest-jobs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export async function listDocuments(options?: {
  status?: string;
  organization_id?: string;
  include_descendants?: boolean;
  search?: string;
  qdrantIndexed?: boolean;
  limit?: number;
  offset?: number;
}): Promise<DocumentListResponse> {
  const params = new URLSearchParams();
  if (options?.status) {
    params.set("status", options.status);
  }
  if (options?.organization_id) {
    params.set("organization_id", options.organization_id);
  }
  if (options?.include_descendants) {
    params.set("include_descendants", "true");
  }
  if (options?.search) {
    params.set("search", options.search);
  }
  if (options?.qdrantIndexed !== undefined) {
    params.set("qdrant_indexed", options.qdrantIndexed ? "true" : "false");
  }
  params.set("limit", String(options?.limit ?? 50));
  params.set("offset", String(options?.offset ?? 0));
  const query = params.toString();
  return requestJson<DocumentListResponse>(`/api/documents${query ? `?${query}` : ""}`, {
    method: "GET",
  });
}

export async function parseDocument(
  documentId: string,
): Promise<DocumentParseResponse> {
  return requestJson<DocumentParseResponse>(`/api/documents/${documentId}/parse`, {
    method: "POST",
  });
}

export type ChunkMode =
  | "recursive"
  | "legal_article"
  | "table_aware"
  | "hybrid_structured"
  | "docling_router"
  | "docling_v6"
  | "slide_page"
  | "heading_aware";

export async function chunkDocument(
  documentId: string,
  options?: {
    chunk_size?: number;
    chunk_overlap?: number;
    chunk_mode?: ChunkMode;
    profile?: DocumentProfile;
  },
): Promise<DocumentChunkResponse> {
  const hasBody =
    options?.chunk_size !== undefined ||
    options?.chunk_overlap !== undefined ||
    options?.chunk_mode !== undefined ||
    options?.profile !== undefined;
  return requestJson<DocumentChunkResponse>(`/api/documents/${documentId}/chunk`, {
    method: "POST",
    ...(hasBody
      ? {
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            chunk_size: options?.chunk_size,
            chunk_overlap: options?.chunk_overlap,
            chunk_mode: options?.chunk_mode,
            profile: options?.profile,
          }),
        }
      : {}),
  });
}

export async function indexDocumentVector(
  documentId: string,
  options?: { profile?: string; use_enriched_content_for_embedding?: boolean },
): Promise<DocumentVectorIndexResponse> {
  return requestJson<DocumentVectorIndexResponse>(
    `/api/documents/${documentId}/index-vector`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(options ?? {}),
    },
  );
}

export async function indexDocumentGraph(
  documentId: string,
  options?: {
    force_rebuild?: boolean;
    extractor_provider?: "fake" | "llm";
    max_entities_per_chunk?: number;
    max_relations_per_chunk?: number;
  },
): Promise<GraphIndexResponse> {
  return requestJson<GraphIndexResponse>(`/api/documents/${documentId}/index-graph`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      force_rebuild: options?.force_rebuild ?? false,
      extractor_provider: options?.extractor_provider ?? "llm",
      max_entities_per_chunk: options?.max_entities_per_chunk,
      max_relations_per_chunk: options?.max_relations_per_chunk,
    }),
  });
}

export async function getDocumentDetail(
  documentId: string,
): Promise<DocumentDetailResponse> {
  return requestJson<DocumentDetailResponse>(`/api/documents/${documentId}`, {
    method: "GET",
  });
}

export type DocumentQdrantPayloadsResponse = {
  document_id: string;
  collection: string;
  count: number;
  points: Array<Record<string, unknown>>;
};

export async function getDocumentChunkQdrantPayloads(
  documentId: string,
): Promise<DocumentQdrantPayloadsResponse> {
  return requestJson<DocumentQdrantPayloadsResponse>(
    `/api/documents/${documentId}/chunks/qdrant`,
    { method: "GET" },
  );
}

export async function getDocumentAccess(
  documentId: string,
): Promise<DocumentAccessResponse> {
  return requestJson<DocumentAccessResponse>(`/api/documents/${documentId}/access`, {
    method: "GET",
  });
}

export async function updateDocumentAccess(
  documentId: string,
  access: DocumentAccessPolicy,
): Promise<DocumentAccessResponse> {
  return requestJson<DocumentAccessResponse>(`/api/documents/${documentId}/access`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(access),
  });
}

export async function downloadDocumentFile(file: DocumentDetailFile): Promise<void> {
  const headers = new Headers();
  const token = getStoredAccessToken();
  if (token) {
    headers.set("Authorization", `Bearer ${token}`);
  }

  const response = await fetch(`${API_BASE_URL}${file.download_url}`, { headers });
  if (!response.ok) {
    throw new ApiError(response.status, await readErrorMessage(response));
  }

  const blob = await response.blob();
  const url = window.URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = file.filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  window.URL.revokeObjectURL(url);
}

export async function deleteDocument(
  documentId: string,
): Promise<DocumentDeleteResponse> {
  return requestJson<DocumentDeleteResponse>(`/api/documents/${documentId}`, {
    method: "DELETE",
  });
}

export async function askRagChat(
  payload: RagChatRequest,
): Promise<RagChatResponse> {
  return requestJson<RagChatResponse>("/api/chat/rag", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });
}

export async function getMemorySettings(): Promise<MemorySettings> {
  return requestJson<MemorySettings>("/api/memory/settings", {
    method: "GET",
  });
}

export async function listMemories(
  limit = 50,
  offset = 0,
): Promise<MemoryItem[]> {
  return requestJson<MemoryItem[]>(
    `/api/memory?limit=${limit}&offset=${offset}`,
    {
      method: "GET",
    },
  );
}

export async function createMemory(
  content: string,
  memoryType: MemoryType,
): Promise<MemoryItem> {
  return requestJson<MemoryItem>("/api/memory", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      content,
      memory_type: memoryType,
      source: "manual",
    }),
  });
}

export async function deleteMemory(
  memoryId: string,
): Promise<{ memory_id: string; deleted: boolean }> {
  return requestJson<{ memory_id: string; deleted: boolean }>(
    `/api/memory/${memoryId}`,
    {
      method: "DELETE",
    },
  );
}

export async function getRuntimeConfig(): Promise<RuntimeConfigResponse> {
  return requestJson<RuntimeConfigResponse>("/api/admin/runtime-config", {
    method: "GET",
  });
}

export async function getGraphHealth(): Promise<GraphHealthResponse> {
  return requestJson<GraphHealthResponse>("/api/admin/graph-health", {
    method: "GET",
  });
}

export async function enqueueIngestionJob(
  file: File,
  access?: UploadAccessOptions,
): Promise<IngestionJob> {
  const formData = new FormData();
  formData.append("file", file);
  appendUploadAccessOptions(formData, access);

  return requestJson<IngestionJob>("/api/admin/ingestion-jobs", {
    method: "POST",
    body: formData,
  });
}

export async function reingestDocument(
  documentId: string,
  options?: { profile?: string; ingestion_profile?: string },
): Promise<IngestionJob> {
  const profile = options?.profile ?? options?.ingestion_profile ?? "auto";
  return requestJson<IngestionJob>(`/api/admin/documents/${documentId}/reingest`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ingestion_profile: profile, profile }),
  });
}

export async function getIngestionJob(jobId: string): Promise<IngestionJob> {
  return requestJson<IngestionJob>(`/api/admin/ingestion-jobs/${jobId}`, {
    method: "GET",
  });
}

export async function listIngestionJobs(): Promise<IngestionJob[]> {
  return requestJson<IngestionJob[]>("/api/admin/ingestion-jobs", {
    method: "GET",
  });
}

export async function deleteIngestionJob(
  jobId: string,
): Promise<{ job_id: string; deleted: boolean }> {
  return requestJson<{ job_id: string; deleted: boolean }>(
    `/api/admin/ingestion-jobs/${jobId}`,
    {
      method: "DELETE",
    },
  );
}

export function getErrorMessage(error: unknown): string {
  if (error instanceof Error) {
    return error.message;
  }
  return "Unexpected error.";
}

async function requestJson<T>(
  path: string,
  init: RequestInit,
  options: { skipAuth?: boolean } = {},
): Promise<T> {
  const headers = new Headers(init.headers);
  const token = getStoredAccessToken();
  if (!options.skipAuth && token && !headers.has("Authorization")) {
    headers.set("Authorization", `Bearer ${token}`);
  }
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers,
  });
  if (!response.ok) {
    throw new ApiError(response.status, await readErrorMessage(response));
  }

  return (await response.json()) as T;
}

async function readErrorMessage(response: Response): Promise<string> {
  try {
    const payload = (await response.json()) as { detail?: unknown };
    if (typeof payload.detail === "string") {
      return payload.detail;
    }
    if (Array.isArray(payload.detail)) {
      return payload.detail
        .map((item) => {
          if (
            typeof item === "object" &&
            item !== null &&
            "msg" in item &&
            typeof item.msg === "string"
          ) {
            return item.msg;
          }
          return JSON.stringify(item);
        })
        .join("; ");
    }
  } catch {
    // Fall through to HTTP status text.
  }

  return response.statusText || `Request failed with status ${response.status}.`;
}
