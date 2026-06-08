# HBRag Architecture

## Overview

HBRag is a production-oriented Hybrid RAG system for document ingestion, retrieval, reranking, grounded answer generation, and citation tracking.

The core pipeline is:

```text
Upload
-> Parse
-> Chunk
-> Embed
-> Vector Index
-> Keyword Index
-> Hybrid Retrieval
-> Reranking
-> Grounded Answer Generation
-> Citations
```

The system combines dense vector retrieval with PostgreSQL keyword search, fuses the candidates with reciprocal-rank style scoring, reranks the combined set, sends a grounded context to the configured LLM provider, and stores citations back to the retrieved chunks.

## Backend Architecture

The backend is a FastAPI application with explicit separation between API routes, persistence models, repositories, and service-level orchestration.

### Runtime Components

| Component | Role |
| --- | --- |
| FastAPI | HTTP API, dependency injection, request validation, route composition, startup checks. |
| PostgreSQL | Relational state: documents, chunks, chat sessions, messages, citations, retrieval logs. |
| Qdrant | Vector store for dense embedding search over document chunks. |
| MinIO | S3-compatible object storage for uploaded source files. |

### Major Folders

| Folder | Responsibility |
| --- | --- |
| `app/api` | FastAPI route modules and dependency wiring. |
| `app/models` | SQLAlchemy ORM models for documents, chunks, chat, citations, and retrieval logs. |
| `app/services` | Application logic: parsing, chunking, storage, indexing, retrieval, reranking, providers, RAG answer generation. |
| `app/repositories` | Database access layer that isolates SQLAlchemy session usage from services. |
| `app/db` | Database engine/session setup and SQLAlchemy base metadata. |

### API Surface

Important route groups:

| Route Group | Purpose |
| --- | --- |
| `/health` | Basic backend health check. |
| `/api/documents/*` | Upload, parse, chunk, and vector-index source documents. |
| `/api/search/vector` | Dense vector retrieval. |
| `/api/search/keyword` | PostgreSQL keyword retrieval. |
| `/api/search/hybrid` | Combined vector and keyword retrieval. |
| `/api/search/rerank` | Hybrid retrieval followed by reranking. |
| `/api/chat/rag` | Full RAG chat flow with citations. |
| `/api/admin/runtime-config` | Safe non-secret runtime configuration diagnostics. |
| `/api/admin/recreate-vector-store` | Delete and recreate the Qdrant collection using the configured embedding dimension. |

## Retrieval Pipeline

### Vector Search

Vector search embeds the user query with the configured embedding provider and queries Qdrant collection `QDRANT_COLLECTION_NAME`. Qdrant returns chunk payloads with vector similarity scores.

The vector store is configured with:

| Setting | Value |
| --- | --- |
| Collection | `QDRANT_COLLECTION_NAME`, default `hbrag_chunks` |
| Distance | Cosine |
| Vector size | `EMBEDDING_DIMENSION` |

### Keyword Search

Keyword search runs against PostgreSQL chunk text. It is used as a lexical signal so that exact terms, names, identifiers, and short Vietnamese phrases can still be recovered even when embeddings miss them.

### Hybrid Search

Hybrid search combines vector and keyword candidates. The current design uses reciprocal-rank style fusion rather than trusting one score scale over another. This avoids comparing raw Qdrant similarity directly with database keyword scores.

The hybrid response keeps source flags and component scores so downstream code can explain whether a result came from vector search, keyword search, or both.

### Reranking

Reranking receives the hybrid candidate set and returns a relevance score per candidate. The reranked list is sorted primarily by reranker score, then by fused hybrid score, then by chunk ID for deterministic ordering.

Supported reranker implementations:

| Provider | Purpose |
| --- | --- |
| `fake` | Deterministic token-overlap reranker for local tests and development. |
| `openai_compatible` | HTTP reranker integration using configurable base URL, model, API key, and endpoint path. |
| `bge` | Optional local BGE cross-encoder wrapper, kept for compatibility when dependencies are installed. |

## Chat Pipeline

The RAG chat endpoint performs the complete answer workflow:

```text
User Question
-> Chat Session
-> User Chat Message
-> Reranking Search
-> Context Builder
-> LLM Provider
-> Assistant Chat Message
-> Citation Generation
-> Commit
```

### Chat Session

When `session_id` is omitted, the backend creates a new chat session. When `session_id` is supplied, the backend uses the existing session and appends messages to it.

### Chat Message

The user question is stored as a `user` message. The generated answer is stored as an `assistant` message. These records provide continuity for future multi-turn features.

### Citation Generation

The answer service maps reranked result chunk IDs back to stored chunks and creates citation records for the assistant message. Citations include chunk ID, document ID, chunk index, quote text, page number when available, and metadata.

Citations are a first-class output. Future changes must preserve the citation chain from generated answer back to source chunk.

## Runtime Providers

HBRag uses provider abstractions for model-dependent behavior. Provider selection is controlled through runtime settings, not code changes.

### Embedding Provider

Interface: `EmbeddingProvider`

Responsibilities:

- Batch embed document chunk texts.
- Embed a single query.
- Expose embedding vector dimension.

Providers:

| Provider | Behavior |
| --- | --- |
| `fake` | Deterministic hash-based vectors. Default for tests and safe local scaffolding. |
| `openai_compatible` | Calls `POST {EMBEDDING_BASE_URL}/embeddings` with `model` and `input` list. |

### Reranker Provider

Interface: `Reranker`

Responsibilities:

- Score candidate chunks for a query.
- Return scores aligned to candidate chunk IDs.

Providers:

| Provider | Behavior |
| --- | --- |
| `fake` | Token overlap scoring. |
| `openai_compatible` | Calls configurable rerank endpoint with `model`, `query`, and `documents`. |
| `bge` | Optional local BGE cross encoder wrapper. |

### LLM Provider

Interface: `LLMProvider`

Responsibilities:

- Generate answer text from a system prompt and user prompt.
- Keep provider-specific transport hidden behind the interface.

Providers:

| Provider | Behavior |
| --- | --- |
| `fake` | Deterministic answer used for tests and local scaffolding. |
| `openai_compatible` | Calls `POST {LLM_BASE_URL}/chat/completions` with `model` and `messages`. |

Fake providers are useful for deterministic tests and development without external services. Production-like runs should use real `openai_compatible` providers and verify `/api/admin/runtime-config` before indexing or chatting.

## Configuration

Backend settings are defined in `backend/app/core/config.py` with `pydantic-settings`. The backend reads environment files in this order from its process working directory:

```text
../.env
.env
backend/.env
```

Use `backend/.env` when running the backend from the `backend` directory. The application does not read misspelled files such as `.evn`.

### Application and API

| Variable | Default | Description |
| --- | --- | --- |
| `APP_NAME` | `HBRag API` | FastAPI application title and health response service name. |
| `APP_VERSION` | `0.1.0` | FastAPI application version. |
| `ENVIRONMENT` | `local` | Environment label returned by health check. |
| `CORS_ALLOWED_ORIGINS` | `["http://localhost:3000","http://127.0.0.1:3000"]` | Explicit frontend origins allowed by CORS. |
| `CORS_ALLOWED_ORIGIN_REGEX` | `http://(localhost|127\.0\.0\.1):[0-9]+` | Regex for local frontend ports. |
| `BACKEND_PORT` | `8000` | Convention used by scripts and local docs; uvicorn still needs an explicit `--port` when run manually. |
| `FRONTEND_PORT` | `3000` | Convention used by frontend local development. |
| `NEXT_PUBLIC_API_BASE_URL` | `http://localhost:8000` | Frontend build/runtime API base URL used by Next.js client code. |

### PostgreSQL

| Variable | Default | Description |
| --- | --- | --- |
| `POSTGRES_HOST` | `localhost` | Docker/local PostgreSQL host convention. |
| `POSTGRES_PORT` | `5432` | Host port mapped to PostgreSQL. |
| `POSTGRES_USER` | `hbrag` | PostgreSQL user for Docker service. |
| `POSTGRES_PASSWORD` | `hbrag_password` | PostgreSQL password for Docker service. Treat as secret outside local dev. |
| `POSTGRES_DB` | `hbrag` | PostgreSQL database name. |
| `DATABASE_URL` | `postgresql+asyncpg://hbrag:hbrag_password@localhost:5432/hbrag` | Backend SQLAlchemy database URL. |
| `DATABASE_ECHO` | `false` | Enables SQLAlchemy SQL logging when true. |

### Qdrant

| Variable | Default | Description |
| --- | --- | --- |
| `QDRANT_HOST` | `localhost` | Docker/local Qdrant host convention. |
| `QDRANT_HTTP_PORT` | `6333` | Host HTTP port mapped to Qdrant. |
| `QDRANT_GRPC_PORT` | `6334` | Host gRPC port mapped to Qdrant. |
| `QDRANT_URL` | `http://localhost:6333` | Backend Qdrant HTTP URL. |
| `QDRANT_COLLECTION_NAME` | `hbrag_chunks` | Collection used for chunk vectors. |
| `QDRANT_UPSERT_BATCH_SIZE` | `128` | Number of points sent per Qdrant upsert batch. |
| `AUTO_RECREATE_COLLECTION` | `false` | If true, startup recreates the Qdrant collection when vector size differs from `EMBEDDING_DIMENSION`. This deletes existing vectors. |

### Embedding

| Variable | Default | Description |
| --- | --- | --- |
| `EMBEDDING_PROVIDER` | `fake` | `fake` or `openai_compatible`. |
| `EMBEDDING_BASE_URL` | empty | Base URL for an OpenAI-compatible embedding API. |
| `EMBEDDING_API_KEY` | empty | API key for embedding provider. Never expose through diagnostics or commit to source control. |
| `EMBEDDING_MODEL` | empty | Embedding model name configured at runtime. |
| `EMBEDDING_DIMENSION` | `384` | Vector dimension used by embedding provider and Qdrant collection. Must match the model output. |

### Reranker

| Variable | Default | Description |
| --- | --- | --- |
| `RERANKER_PROVIDER` | `fake` | `fake`, `openai_compatible`, or optional `bge`. |
| `RERANKER_BASE_URL` | empty | Base URL for an HTTP reranker service. |
| `RERANKER_API_KEY` | empty | API key for reranker provider. Never expose through diagnostics or commit to source control. |
| `RERANKER_MODEL` | empty | Reranker model name configured at runtime. |
| `RERANKER_ENDPOINT_PATH` | `/rerank` | Path appended to `RERANKER_BASE_URL`, or a full URL if the reranker endpoint is not under the base URL. |
| `BGE_RERANKER_MODEL` | empty | Optional local BGE cross-encoder model name used only when `RERANKER_PROVIDER=bge`. |

### LLM

| Variable | Default | Description |
| --- | --- | --- |
| `LLM_PROVIDER` | `fake` | `fake` or `openai_compatible`. |
| `LLM_BASE_URL` | empty | Base URL for an OpenAI-compatible chat completions API. |
| `LLM_API_KEY` | empty | API key for LLM provider. Never expose through diagnostics or commit to source control. |
| `LLM_MODEL` | empty | Chat model name configured at runtime. |

### MinIO

| Variable | Default | Description |
| --- | --- | --- |
| `MINIO_ENDPOINT` | `localhost:9000` | Backend MinIO endpoint. |
| `MINIO_API_PORT` | `9000` | Host API port mapped to MinIO. |
| `MINIO_CONSOLE_PORT` | `9001` | Host console port mapped to MinIO. |
| `MINIO_ROOT_USER` | `minioadmin` | MinIO root user for Docker service. |
| `MINIO_ROOT_PASSWORD` | `minioadmin123` | MinIO root password for Docker service. Treat as secret outside local dev. |
| `MINIO_ACCESS_KEY` | `minioadmin` | Backend access key for MinIO. |
| `MINIO_SECRET_KEY` | `minioadmin123` | Backend secret key for MinIO. Never commit real credentials. |
| `MINIO_BUCKET` | `hbrag-documents` | Bucket for uploaded source files. |
| `MINIO_SECURE` | `false` | Use HTTPS for MinIO when true. |

## Vector Store

Qdrant stores one vector point per chunk. Each point payload includes chunk ID, document ID, chunk index, content preview/source content, and metadata.

Current vector store conventions:

| Property | Value |
| --- | --- |
| Collection name | `QDRANT_COLLECTION_NAME`, default `hbrag_chunks` |
| Distance metric | Cosine |
| Vector dimension | `EMBEDDING_DIMENSION` |

The Qdrant vector dimension must exactly match the embedding model output dimension. For example, if the embedding model returns 1024-dimensional vectors, the Qdrant collection must also be created with size 1024. A 384-dimensional collection cannot accept 1024-dimensional vectors.

On startup, the backend checks the existing Qdrant collection configuration:

- If the collection does not exist, it creates it with the configured dimension.
- If the collection exists and the vector size matches, startup continues normally.
- If the collection exists and the vector size differs, the backend logs a warning.
- If `AUTO_RECREATE_COLLECTION=true`, the backend deletes and recreates the collection with `EMBEDDING_DIMENSION`.

Manual reset endpoint:

```http
POST /api/admin/recreate-vector-store
```

After recreating a collection, all documents must be indexed again because previous vectors are deleted.

## Production Notes

### Common Failure Modes

| Failure | Symptom | Fix |
| --- | --- | --- |
| Qdrant dimension mismatch | Indexing fails, vector upsert errors, or retrieval cannot use newly embedded vectors. | Recreate the vector store with `POST /api/admin/recreate-vector-store`, then re-index documents. |
| Wrong embedding model name | Embedding API returns provider error or empty/invalid data. | Verify `EMBEDDING_MODEL` against the provider. |
| Wrong reranker model name | Rerank endpoint returns provider error or scores cannot be parsed. | Verify `RERANKER_MODEL` and `RERANKER_ENDPOINT_PATH`. |
| Wrong LLM model name | Chat completion endpoint returns provider error. | Verify `LLM_MODEL` against the configured provider. |
| Missing API key | Provider returns 401/403 or request fails. | Set the corresponding `*_API_KEY` in environment. |
| Fake provider accidentally enabled | Answers look like deterministic test text, such as "Generated from provided context". | Check `GET /api/admin/runtime-config`; set provider variables to `openai_compatible`; restart backend. |
| Misspelled env file | Runtime config remains default even though variables appear configured. | Use `backend/.env`, not `.evn`. |
| Frontend pointing at old backend | UI shows old API URL or calls a stale server. | Update `frontend/.env.local` and restart Next.js. |

### Operational Checks

Before indexing production-like data:

1. Call `GET /api/admin/runtime-config`.
2. Confirm providers are not accidentally `fake`.
3. Confirm `EMBEDDING_DIMENSION` matches the embedding model.
4. Confirm Qdrant collection vector size matches `EMBEDDING_DIMENSION`.
5. Recreate and re-index if the dimension has changed.

## GraphRAG Extension

HBRag now supports an optional Neo4j-based GraphRAG layer inspired by LightRAG.

Design rules:

- Hybrid RAG remains the default retrieval backbone.
- GraphRAG only adds recall-expansion candidates before reranking.
- PostgreSQL stores graph auditability only: `graph_document_status` and `graph_extraction_logs`.
- Neo4j stores the actual graph: `Document`, `Chunk`, and `Entity` nodes plus `HAS_CHUNK`, `MENTIONS`, `RELATED_TO`, and `SUPPORTED_BY` edges.

Document lifecycle with graph enabled:

```text
Upload
-> Parse
-> Chunk
-> Embed + Index
-> Graph Index
-> Hybrid Retrieval
-> Optional Graph Expansion
-> Reranking
-> Answer
```

Operational notes:

- Rebuild one document graph with `POST /api/documents/{document_id}/index-graph`.
- Use `force_rebuild=true` when the chunk set or graph extraction strategy changed.
- Check health with `GET /api/admin/graph-health`.
- Do not expose `NEO4J_PASSWORD` through diagnostics or frontend payloads.

Recommended use cases:

- Legal and administrative documents.
- Policies, regulations, benefits, conditions, and article/table-driven content.

Avoid GraphRAG for:

- Short FAQs.
- Very small documents.
- Noisy OCR where entity extraction is unreliable.
