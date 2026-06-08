# HBRag AI Developer Guide

This document is written for future AI coding agents working on HBRag: Codex, GPT, Claude, Cursor, Windsurf, Cline, Roo, and similar systems. Read this before making architectural changes.

## Project Goal

HBRag is a production-oriented Hybrid RAG platform.

The system ingests source documents, parses and chunks them, indexes them into dense vector and keyword retrieval stores, performs hybrid retrieval, reranks candidates, generates grounded answers, and stores citations that connect answers back to source chunks.

The project is not a single prompt wrapper. It is a retrieval platform with persistence, provider abstractions, model runtime configuration, diagnostics, and operational failure handling.

## Coding Rules

1. Never remove provider abstraction.
2. Never hardcode model names.
3. Never hardcode API keys.
4. Read runtime configuration from settings.
5. Maintain backward compatibility.
6. Preserve retrieval logs.
7. Preserve citations.

Additional rules:

- Keep fake providers available for deterministic tests.
- Do not silently fall back to fake providers when a real provider is misconfigured.
- Do not expose API keys through diagnostics, logs, tests, exceptions, or frontend payloads.
- Do not bypass repositories when modifying persisted application data.
- Do not change search behavior while working on unrelated API, UI, or provider tasks.
- Keep route dependencies explicit and testable with FastAPI dependency overrides.
- When changing retrieval ranking, update tests and documentation together.

## System Boundaries

HBRag is split into:

| Layer | Responsibility |
| --- | --- |
| Frontend | Next.js workspace UI for upload, parse, chunk, index, and RAG chat. |
| Backend | FastAPI API and application orchestration. |
| PostgreSQL | Relational metadata, chunks, chat, citations, and retrieval logs. |
| Qdrant | Dense vector search over chunk embeddings. |
| MinIO | Uploaded source file storage. |
| External model providers | Embeddings, reranking, and LLM generation through runtime-configured provider interfaces. |

## Retrieval Design

Current retrieval order:

```text
Vector
+
Keyword
-> Hybrid (RRF)
-> Reranker
-> Context Builder
-> LLM
```

Do not bypass reranking.

The retriever intentionally keeps dense and lexical signals separate until hybrid fusion. Vector search is good at semantic similarity; keyword search is good at exact terms, names, identifiers, codes, and short phrases. Hybrid retrieval combines both candidate sets before reranking.

### Vector Retrieval

Vector retrieval embeds the query using the configured embedding provider and searches Qdrant. The result carries chunk IDs, document IDs, vector scores, content previews, and metadata.

### Keyword Retrieval

Keyword retrieval uses PostgreSQL chunk text. It is not a fallback; it is a required complementary retrieval signal.

### Hybrid Retrieval

Hybrid retrieval uses reciprocal-rank style fusion. Avoid comparing raw vector scores and keyword scores directly unless the scoring model is explicitly changed and tested.

### Reranking

Reranking receives the hybrid candidates and returns relevance scores per chunk. Reranking is the final relevance ordering step before context construction.

Do not send raw vector-only results directly to the LLM in the normal RAG path.

### Context Builder

The context builder prepares the selected chunks for answer generation with stable citation markers. Citation marker behavior must remain compatible with downstream citation generation.

### LLM

The LLM receives a system prompt and grounded user prompt. It should answer from the provided context and preserve source traceability through citations.

## Embedding

Current production-like model:

```text
BAAI/bge-m3
```

Current dimension:

```text
1024
```

The model and dimension must come from environment variables:

```text
EMBEDDING_PROVIDER=openai_compatible
EMBEDDING_BASE_URL=...
EMBEDDING_API_KEY=...
EMBEDDING_MODEL=BAAI/bge-m3
EMBEDDING_DIMENSION=1024
```

Never hardcode the current model into provider code. Tests may use fake model names, but runtime code must read from settings.

If the embedding model changes, verify:

1. The new model output dimension.
2. `EMBEDDING_DIMENSION`.
3. Qdrant collection vector size.
4. Whether existing vectors must be deleted and rebuilt.

## Reranker

Current production-like model:

```text
BAAI/bge-reranker-v2-m3
```

Runtime configuration:

```text
RERANKER_PROVIDER=openai_compatible
RERANKER_BASE_URL=...
RERANKER_API_KEY=...
RERANKER_MODEL=BAAI/bge-reranker-v2-m3
RERANKER_ENDPOINT_PATH=/rerank
```

Reranker APIs are less standardized than OpenAI chat and embedding APIs. Keep endpoint path configurable. If adding support for a provider-specific response format, add parsing in the provider implementation and tests for that response shape.

Do not replace reranking with prompt-only selection in the LLM.

## LLM

The LLM is configured through environment variables:

```text
LLM_PROVIDER=openai_compatible
LLM_BASE_URL=...
LLM_API_KEY=...
LLM_MODEL=...
```

Never hardcode provider-specific logic into the RAG answer service. Provider-specific transport belongs behind the LLM provider interface.

The fake LLM returns deterministic text for tests. If the UI shows text like "Generated from provided context", check `GET /api/admin/runtime-config`; the backend is likely still using `LLM_PROVIDER=fake` or the frontend is pointed at a stale backend server.

## Runtime Providers

Provider factories live under:

```text
backend/app/services/embeddings/factory.py
backend/app/services/rerankers/factory.py
backend/app/services/llms/factory.py
```

Provider implementations live under:

```text
backend/app/services/embeddings/
backend/app/services/rerankers/
backend/app/services/llms/
```

Current provider names:

| Capability | Fake Provider | Real Provider |
| --- | --- | --- |
| Embeddings | `fake` | `openai_compatible` |
| Reranker | `fake` | `openai_compatible` |
| LLM | `fake` | `openai_compatible` |

Factory behavior should be strict. Unknown providers should raise errors. Missing required real-provider configuration should raise errors. Do not silently substitute fake providers.

## Qdrant

Collection:

```text
hbrag_chunks
```

Distance:

```text
Cosine
```

Dimension:

```text
read from EMBEDDING_DIMENSION
```

The configured `EMBEDDING_DIMENSION` must match the collection vector size. A dimension mismatch is expected when switching from the fake embedding provider to a real embedding model with a different vector size.

Collection management behavior:

- Startup checks whether the collection exists.
- Missing collection is created with `EMBEDDING_DIMENSION`.
- Existing collection vector size is checked.
- Mismatch logs a warning.
- `AUTO_RECREATE_COLLECTION=true` recreates the collection on startup.
- `POST /api/admin/recreate-vector-store` manually deletes and recreates the collection.

After collection recreation, all documents must be indexed again.

## Persistence Rules

### Retrieval Logs

Preserve retrieval logs. They are needed for debugging, evaluation, and future observability.

When changing retrieval code, ensure logs still capture:

- Query.
- Vector results.
- Keyword results.
- Hybrid results.
- Reranked results.
- Session ID when present.

### Citations

Preserve citations. The answer must remain traceable to source chunks.

When changing answer generation, ensure the service still:

- Retrieves the final chunks by ID.
- Stores assistant message citations.
- Returns citation metadata to the frontend.
- Keeps citation markers stable enough for users to map answer claims to sources.

## Configuration Rules

Read runtime configuration from `app.core.config.settings`.

Do not read environment variables directly in services unless there is a strong reason and the behavior is documented. Centralized settings keep tests, diagnostics, and deployment predictable.

Never commit real secrets. This includes:

- `EMBEDDING_API_KEY`
- `RERANKER_API_KEY`
- `LLM_API_KEY`
- `POSTGRES_PASSWORD`
- `MINIO_SECRET_KEY`
- `MINIO_ROOT_PASSWORD`

The diagnostics endpoint must stay non-secret:

```http
GET /api/admin/runtime-config
```

It may return provider names, base URLs, model names, dimensions, collection name, and safe boolean flags. It must not return API keys.

## Local Development Notes

Use `backend/.env` for backend runtime configuration. The backend does not read `.evn`.

Use `frontend/.env.local` for the frontend API base URL:

```text
NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8000
```

The frontend dev script is expected to use port 3000 only. If port 3000 is already in use, stop the existing frontend process instead of allowing Next.js to switch to 3001.

## Future Roadmap

1. Real OpenAI-compatible providers.
2. Multi-document collections.
3. Knowledge base management.
4. Streaming responses.
5. User authentication.
6. Observability.
7. Evaluation framework.
8. Agentic retrieval.

Roadmap items should not break the existing ingestion, retrieval, reranking, answer, and citation flows.

## Development Checklist

Before every PR:

- `pytest` passes.
- `ruff` passes.
- Type checks pass.
- No secrets committed.
- Architecture documentation updated.
- Runtime diagnostics still hides API keys.
- Provider abstractions remain intact.
- Retrieval logs still write successfully.
- Citations still link answers to source chunks.
- Qdrant dimension changes include a migration or recreation plan.

## Recommended Commands

Backend:

```powershell
cd backend
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m pytest
```

Frontend:

```powershell
cd frontend
npm run lint
npm run typecheck
```

Operational checks:

```powershell
Invoke-RestMethod -Uri http://127.0.0.1:8000/api/admin/runtime-config -Method Get
Invoke-RestMethod -Uri http://127.0.0.1:8000/api/admin/recreate-vector-store -Method Post
```

Use the recreate endpoint only when intentionally deleting and rebuilding vectors.

## GraphRAG Notes

GraphRAG is optional and must stay optional.

Non-negotiable behavior:

- Keep `GRAPH_ENABLED=false` as the safe default.
- Keep the Qdrant + PostgreSQL hybrid path intact even if Neo4j is unavailable.
- Never let GraphRAG bypass permission filtering or reranking.
- Keep citations chunk-based even when graph candidates helped retrieval.
- Never expose `NEO4J_PASSWORD` in `GET /api/admin/runtime-config` or any other API response.

Current graph flow:

```text
Chunk
-> entity / relation extraction
-> merge aliases and duplicate relations
-> Neo4j upsert
-> optional graph expansion during chat
-> merge with hybrid candidates
-> reranker
```

When editing GraphRAG code:

1. Update docs and tests together.
2. Preserve fake graph extraction for deterministic tests.
3. Preserve PostgreSQL audit tables for status/log visibility.
4. Prefer graceful degradation over breaking normal RAG chat.
