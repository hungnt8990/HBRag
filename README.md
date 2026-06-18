# HBRag

Production-ready Hybrid RAG project skeleton with FastAPI, Next.js 15, PostgreSQL, Qdrant, MinIO, and Docker Compose for local infrastructure.

Upload, parsing, chunking, vector search, keyword search, hybrid search, reranking, and RAG answer generation foundations are implemented.

## Project Structure

```text
backend/   FastAPI application
frontend/  Next.js 15 + TypeScript + shadcn-style UI
docker/    Docker-related notes and future assets
docs/      Architecture and project documentation
scripts/   Local development helper scripts
```

## Prerequisites

- Docker and Docker Compose
- Python 3.11+
- Node.js 20+

## Environment

Create a backend environment file from the example:

```powershell
Copy-Item backend/.env.example backend/.env
```

On macOS or Linux:

```bash
cp backend/.env.example backend/.env
```

## Start Local Infrastructure

```powershell
docker compose up -d
```

Services:

- PostgreSQL: `localhost:5432`
- Qdrant HTTP: `localhost:6333`
- Qdrant gRPC: `localhost:6334`
- MinIO API: `localhost:9000`
- MinIO Console: `http://localhost:9001`

The MinIO bootstrap service creates the default bucket from `MINIO_BUCKET`.
If port `9000` is already used locally, set `MINIO_API_PORT` and
`MINIO_ENDPOINT` in `backend/.env` to another port, for example
`MINIO_API_PORT=9100` and `MINIO_ENDPOINT=localhost:9100`.

## Backend

```powershell
cd backend
python -m venv .venv
#MacOS source .venv/bin/activate
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Health check:

```powershell
Invoke-RestMethod http://localhost:8000/health
```

Run backend tests:

```powershell
pytest
```

Run database migrations from the repository root:

```powershell
docker compose up -d postgres
cd backend
alembic upgrade head
```

If you are running from the repository root instead of `backend/`:

```powershell
alembic -c backend/alembic.ini upgrade head
```

Upload a document:

```powershell
Invoke-RestMethod `
  -Uri http://localhost:8000/api/documents/upload `
  -Method Post `
  -Form @{ file = Get-Item .\sample.pdf }
```

Supported upload types for now:

- PDF
- DOCX
- TXT
- MD

Parse an uploaded document:

```powershell
Invoke-RestMethod `
  -Uri http://localhost:8000/api/documents/<document-id>/parse `
  -Method Post
```

Parsing currently extracts raw text only and updates the document status to `parsed`.
Chunking, vector indexing, vector search, keyword search, hybrid search, reranking, and RAG chat answer generation are available as separate endpoints.
The frontend includes a basic workspace for the document pipeline and RAG chat.

Chunk a parsed document:

```powershell
Invoke-RestMethod `
  -Uri http://localhost:8000/api/documents/<document-id>/chunk `
  -Method Post
```

Chunking currently uses a simple character-based recursive splitter with default
`chunk_size = 1000` and `chunk_overlap = 150`.

Enrich chunks with LLM metadata before vector indexing:

```powershell
Invoke-RestMethod `
  -Uri http://localhost:8000/api/documents/<document-id>/enrich `
  -Method Post `
  -ContentType "application/json" `
  -Body '{"profile":"legal_admin","force":true}'
```

All chunk enrichment settings are runtime settings in `backend/.env`, not RAG
profile/Postgres config. `CHUNK_ENRICHMENT_ENABLED` controls whether ingestion
runs enrich after chunking; `RETRIEVAL_ENRICHMENT_ENABLED` controls whether
search/chat uses saved enrichment metadata; `ENRICHMENT_FORCE_ON_REINGEST`
controls refresh/reingest behavior; and
`ENRICHMENT_UPDATE_KEYWORD_SEARCH_VECTOR` controls whether keyword search text is
updated after enrich. Normal ingest uses `EMBEDDING_ENRICHMENT_*` when set, then
falls back to `CHUNK_ENRICHMENT_*`, then `LLM_*`. Refresh/reingest can override
with `REINGEST_ENRICHMENT_*`. Each enrichment group has its own `*_BASE_URL`, so
chunk, normal embedding enrich, and reingest enrich can call different
OpenAI-compatible endpoints.

Index chunk vectors:

```powershell
Invoke-RestMethod `
  -Uri http://localhost:8000/api/documents/<document-id>/index-vector `
  -Method Post
```

Run vector search:

```powershell
Invoke-RestMethod `
  -Uri http://localhost:8000/api/search/vector `
  -Method Post `
  -ContentType "application/json" `
  -Body '{"query":"example question","top_k":5}'
```

Qdrant upserts are batched to avoid oversized payloads for large documents.
The default batch size is `QDRANT_UPSERT_BATCH_SIZE=128`.

Run keyword search:

```powershell
Invoke-RestMethod `
  -Uri http://localhost:8000/api/search/keyword `
  -Method Post `
  -ContentType "application/json" `
  -Body '{"query":"example question","top_k":5}'
```

Keyword search uses PostgreSQL full-text search over `chunks.search_vector` with
the `simple` text search configuration for mixed Vietnamese/English content.

Run hybrid search:

```powershell
Invoke-RestMethod `
  -Uri http://localhost:8000/api/search/hybrid `
  -Method Post `
  -ContentType "application/json" `
  -Body '{"query":"example question","top_k":5,"vector_weight":1.0,"keyword_weight":1.0}'
```

Hybrid search runs vector and keyword searches at `top_k * 3`, merges results by
`chunk_id`, fuses ranks with Reciprocal Rank Fusion, and writes a retrieval log.

Run reranking search:

```powershell
Invoke-RestMethod `
  -Uri http://localhost:8000/api/search/rerank `
  -Method Post `
  -ContentType "application/json" `
  -Body '{"query":"example question","top_k":5,"candidate_k":20}'
```

Reranking runs hybrid search for `candidate_k` candidates, scores them with the
configured reranker, returns the top `top_k`, and writes `reranked_results` to
the retrieval log.

Generate a grounded RAG chat answer:

```powershell
Invoke-RestMethod `
  -Uri http://localhost:8000/api/chat/rag `
  -Method Post `
  -ContentType "application/json" `
  -Body '{"query":"example question","top_k":5,"candidate_k":20}'
```

The RAG chat endpoint creates a chat session when `session_id` is omitted,
saves user and assistant messages, runs reranking, builds a cited context,
generates an answer through the configured LLM provider, and writes citations
linked to the retrieved chunks.

Development uses `FakeEmbeddingProvider` by default. It generates deterministic
384-dimensional vectors from text hashes and does not call an external API.
To switch to an OpenAI-compatible embedding endpoint, set
`EMBEDDING_PROVIDER=openai_compatible` and configure `EMBEDDING_BASE_URL`,
`EMBEDDING_API_KEY`, `EMBEDDING_MODEL`, and `EMBEDDING_DIMENSION`.
On startup the backend checks the Qdrant collection vector size against
`EMBEDDING_DIMENSION`. If the size differs, it logs a warning; set
`AUTO_RECREATE_COLLECTION=true` to recreate automatically, or call
`POST /api/admin/recreate-vector-store`.

Development also uses `FakeReranker` by default. It scores candidates with a
deterministic token-overlap heuristic and does not call an external model. To
use a common HTTP reranker endpoint, set `RERANKER_PROVIDER=openai_compatible`
and configure `RERANKER_BASE_URL`, `RERANKER_API_KEY`, `RERANKER_MODEL`, and
`RERANKER_ENDPOINT_PATH`.

Development uses `FakeLLM` by default. It produces deterministic answers from
the provided context and does not call an external API. To use an
OpenAI-compatible chat endpoint, set `LLM_PROVIDER=openai_compatible` and
configure `LLM_BASE_URL`, `LLM_API_KEY`, and `LLM_MODEL`.

## Frontend

```powershell
cd frontend
npm install
npm run dev
```

Open `http://localhost:3000`.

The workspace uses `NEXT_PUBLIC_API_BASE_URL` to call the FastAPI backend. The
default local value is `http://localhost:8000`.

Useful commands:

```powershell
npm run typecheck
npm run lint
```

## Notes

- The Docker Compose file is scoped to local infrastructure.
- Backend and frontend application containers can be added later when deployment targets are defined.
- Keep credentials in `backend/.env`; do not commit real secrets.

## Check Backend

cd backend
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check .

## Check Frontend

cd frontend
npm run typecheck
npm run lint
npm run build

## Reload backend

cd backend
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload

alembic upgrade head
pytest
ruff check .
uvicorn app.main:app --reload
