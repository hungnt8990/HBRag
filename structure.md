# Cau Truc Du An

Du an `HBRag` la skeleton cho Hybrid RAG, gom backend FastAPI, frontend Next.js 15, ha tang local bang Docker Compose, database foundation bang SQLAlchemy/Alembic, upload, parsing, chunking, embedding, vector indexing, keyword search, hybrid search, reranking, RAG answer generation foundation va frontend workspace co ban.

```text
HBRag/
|-- .env.example
|-- .gitignore
|-- .ignore
|-- README.md
|-- docker-compose.yml
|-- structure.md
|-- error.md
|-- backend/
|   |-- pyproject.toml
|   |-- alembic.ini
|   |-- alembic/
|   |   |-- env.py
|   |   |-- script.py.mako
|   |   `-- versions/
|   |       |-- 0001_initial_schema.py
|   |       |-- 0002_add_parsed_document_fields.py
|   |       `-- 0003_add_chunk_search_vector.py
|   |-- app/
|   |   |-- __init__.py
|   |   |-- main.py
|   |   |-- api/
|   |   |   |-- __init__.py
|   |   |   `-- routes/
|   |   |       |-- __init__.py
|   |   |       |-- chat.py
|   |   |       |-- health.py
|   |   |       |-- documents.py
|   |   |       `-- search.py
|   |   |-- core/
|   |   |   |-- __init__.py
|   |   |   `-- config.py
|   |   |-- db/
|   |   |   |-- __init__.py
|   |   |   |-- base.py
|   |   |   `-- session.py
|   |   |-- models/
|   |   |   |-- __init__.py
|   |   |   |-- mixins.py
|   |   |   |-- document.py
|   |   |   |-- chunk.py
|   |   |   |-- chat.py
|   |   |   |-- citation.py
|   |   |   `-- retrieval.py
|   |   |-- repositories/
|   |   |   |-- __init__.py
|   |   |   |-- documents.py
|   |   |   |-- chat.py
|   |   |   `-- retrieval_logs.py
|   |   |-- schemas/
|   |   |   |-- __init__.py
|   |   |   |-- documents.py
|   |   |   `-- chat.py
|   |   `-- services/
|   |       |-- __init__.py
|   |       |-- storage.py
|   |       |-- document_service.py
|   |       |-- document_parser_service.py
|   |       |-- chunking_service.py
|   |       |-- vector_indexing_service.py
|   |       |-- vector_store.py
|   |       |-- keyword_search.py
|   |       |-- hybrid_search.py
|   |       |-- reranking_service.py
|   |       |-- rag_answer_service.py
|   |       |-- llms/
|   |       |   |-- __init__.py
|   |       |   |-- base.py
|   |       |   |-- factory.py
|   |       |   |-- fake_llm.py
|   |       |   `-- openai_llm.py
|   |       |-- embeddings/
|   |       |   |-- __init__.py
|   |       |   |-- base.py
|   |       |   |-- factory.py
|   |       |   |-- fake_provider.py
|   |       |   `-- openai_provider.py
|   |       |-- rerankers/
|   |       |   |-- __init__.py
|   |       |   |-- base.py
|   |       |   |-- factory.py
|   |       |   |-- fake_reranker.py
|   |       |   `-- bge_reranker.py
|   |       `-- parsers/
|   |           |-- __init__.py
|   |           |-- base.py
|   |           |-- text_parser.py
|   |           |-- pdf_parser.py
|   |           `-- docx_parser.py
|   `-- tests/
|       |-- test_health.py
|       |-- test_models.py
|       |-- test_document_upload.py
|       |-- test_document_parse.py
|       |-- test_document_chunking.py
|       |-- test_vector_indexing.py
|       |-- test_keyword_search.py
|       |-- test_hybrid_search.py
|       |-- test_reranking.py
|       |-- test_rag_chat.py
|       `-- test_vector_store.py
|-- frontend/
|   |-- package.json
|   |-- package-lock.json
|   |-- next.config.ts
|   |-- tsconfig.json
|   |-- next-env.d.ts
|   |-- postcss.config.mjs
|   |-- tailwind.config.ts
|   |-- eslint.config.mjs
|   |-- components.json
|   |-- app/
|   |   |-- globals.css
|   |   |-- layout.tsx
|   |   `-- page.tsx
|   |-- components/
|   |   `-- ui/
|   |       |-- button.tsx
|   |       |-- card.tsx
|   |       |-- input.tsx
|   |       `-- textarea.tsx
|   `-- lib/
|       |-- api.ts
|       `-- utils.ts
|-- docker/
|   `-- README.md
|-- docs/
|   |-- AI_DEVELOPER_GUIDE.md
|   `-- ARCHITECTURE.md
`-- scripts/
    |-- README.md
    |-- start-infra.ps1
    `-- stop-infra.ps1
```

## Root

- `.env.example`: mau bien moi truong cho backend, frontend, PostgreSQL, Qdrant, MinIO, CORS va provider configs. `DATABASE_URL` dang dung async driver `postgresql+asyncpg`.
- `.gitignore`: loai tru file moi truong, dependency, cache, build output, log va virtual environment.
- `.ignore`: giup `rg` bo qua thu muc sinh ra nhu `node_modules`, `.next`, `.venv`, `.pytest_cache`, `.ruff_cache` khi workspace chua co Git repo.
- `README.md`: huong dan setup, chay ha tang local, backend, frontend, test, migration, upload, parse, chunk, vector search, keyword search, hybrid search, reranking search va RAG chat.
- `.env`: local env hien tai dung MinIO host port `9100` de tranh conflict voi container khac dang chiem port `9000`.
- `docker-compose.yml`: dinh nghia PostgreSQL, Qdrant, MinIO va service khoi tao bucket MinIO.
- `structure.md`: tai lieu mo ta cay thu muc hien tai.
- `error.md`: tai lieu tom tat loi build/chay va trang thai xu ly.

## Backend

- `backend/pyproject.toml`: khai bao package Python, dependencies FastAPI, SQLAlchemy async, asyncpg, Alembic, MinIO, pypdf, python-docx, Uvicorn, Pydantic Settings, pytest va Ruff.
- `backend/alembic.ini`: cau hinh Alembic.
- `backend/alembic/env.py`: Alembic async environment, doc URL tu `app.core.config.settings`.
- `backend/alembic/versions/0001_initial_schema.py`: migration dau tien tao cac bang database.
- `backend/alembic/versions/0002_add_parsed_document_fields.py`: them `parsed_text`, `parsed_at` va status `chunked`.
- `backend/alembic/versions/0003_add_chunk_search_vector.py`: them `chunks.search_vector`, backfill tu `content` va tao GIN index.
- `backend/app/main.py`: entrypoint FastAPI, tao app va dang ky router.
- `backend/app/core/config.py`: cau hinh typed bang `pydantic-settings`, gom database, CORS, Qdrant, embedding, reranker, LLM va MinIO settings.
- `backend/app/db/base.py`: SQLAlchemy declarative base va naming convention cho constraints/indexes.
- `backend/app/db/session.py`: async engine, async session factory va dependency `get_db_session()`.
- `backend/app/models/`: ORM models cho `documents`, `document_files`, `chunks`, `chat_sessions`, `chat_messages`, `citations`, `retrieval_logs`.
- `backend/app/repositories/documents.py`: repository tao, doc va cap nhat documents/files/chunks; populate `chunks.search_vector` bang `to_tsvector('simple', content)` khi tao chunks.
- `backend/app/repositories/chat.py`: repository tao chat session, chat messages, citations va doc chunks theo ids.
- `backend/app/repositories/retrieval_logs.py`: repository ghi `retrieval_logs` cho search pipeline.
- `backend/app/services/storage.py`: MinIO/S3-compatible storage client abstraction, upload/download/delete object.
- `backend/app/services/document_service.py`: upload orchestration, file type validation, storage write va DB write.
- `backend/app/services/document_parser_service.py`: parse orchestration, load DB metadata, download original file, select parser, update status.
- `backend/app/services/chunking_service.py`: chunk orchestration va simple recursive text chunker.
- `backend/app/services/vector_indexing_service.py`: embed chunks, upsert Qdrant vectors, vector search orchestration.
- `backend/app/services/vector_store.py`: Qdrant async client wrapper, ensure collection, batched upsert va search.
- `backend/app/services/keyword_search.py`: PostgreSQL full-text keyword search service dung `plainto_tsquery('simple', query)` va `ts_rank_cd`.
- `backend/app/services/hybrid_search.py`: hybrid search service chay vector + keyword depth `top_k * 3`, fuse bang Reciprocal Rank Fusion va ghi retrieval log.
- `backend/app/services/reranking_service.py`: reranking service chay hybrid candidates, goi reranker, sort theo rerank score va ghi `reranked_results`.
- `backend/app/services/rag_answer_service.py`: RAG answer service tao session/message, chay reranking, build context, goi LLM provider va ghi citations.
- `backend/app/services/embeddings/`: embedding provider abstraction, fake provider default va optional OpenAI provider.
- `backend/app/services/rerankers/`: reranker abstraction, fake token-overlap reranker default, optional BGE reranker wrapper va factory.
- `backend/app/services/llms/`: LLM provider abstraction, FakeLLM default va optional OpenAI LLM provider.
- `backend/app/services/parsers/`: modular parsers cho TXT, MD, PDF va DOCX.
- `backend/app/schemas/documents.py`: request/response schemas cho document upload, parse, chunk, vector search, keyword search, hybrid search va reranking search.
- `backend/app/schemas/chat.py`: request/response schemas cho endpoint RAG chat va citations.
- `backend/app/api/routes/health.py`: endpoint `GET /health`.
- `backend/app/api/routes/documents.py`: endpoints `POST /api/documents/upload`, `POST /api/documents/{document_id}/parse`, `POST /api/documents/{document_id}/chunk` va `POST /api/documents/{document_id}/index-vector`.
- `backend/app/api/routes/search.py`: endpoints `POST /api/search/vector`, `POST /api/search/keyword`, `POST /api/search/hybrid` va `POST /api/search/rerank`.
- `backend/app/api/routes/chat.py`: endpoint `POST /api/chat/rag`.
- `backend/tests/test_health.py`: test health check.
- `backend/tests/test_models.py`: test import model, configure mapper, metadata table names va `chunks.search_vector`.
- `backend/tests/test_document_upload.py`: test upload endpoint bang fake repository/storage.
- `backend/tests/test_document_parse.py`: test parse endpoint va parser selection bang fake repository/storage.
- `backend/tests/test_document_chunking.py`: test overlap, reject unparsed, chunk create va re-chunk delete old chunks.
- `backend/tests/test_vector_indexing.py`: test fake embedding, vector index endpoint, vector search schema va reject chunks rong.
- `backend/tests/test_vector_store.py`: test Qdrant vector store upsert points theo batch.
- `backend/tests/test_keyword_search.py`: test keyword endpoint schema, reject query rong va compile query an toan.
- `backend/tests/test_hybrid_search.py`: test RRF overlap, vector-only/keyword-only, endpoint schema, reject query rong va save retrieval log.
- `backend/tests/test_reranking.py`: test fake reranker token overlap, endpoint rerank schema, reject query rong va save `reranked_results`.
- `backend/tests/test_rag_chat.py`: test FakeLLM deterministic, tao session/message/citations va endpoint RAG schema.

## Database Tables

- `documents`: metadata tai lieu, source type, status va timestamps.
- `documents.parsed_text`: raw text sau parse, nullable.
- `documents.parsed_at`: thoi diem parse, nullable.
- `document_files`: file goc cua document, duong dan MinIO/object storage va kich thuoc file.
- `chunks`: noi dung chunk, thu tu chunk, token count, JSONB metadata va `search_vector` cho PostgreSQL full-text search.
- `chat_sessions`: phien chat.
- `chat_messages`: tin nhan chat voi role `user`, `assistant`, `system`.
- `citations`: lien ket message voi chunk/document va thong tin quote/page.
- `retrieval_logs`: log ket qua vector, keyword, hybrid va rerank dang JSONB; hybrid search ghi `vector_results`, `keyword_results`, `hybrid_results`, reranking/RAG search ghi them `reranked_results`.

## Document Upload Foundation

- Endpoint: `POST /api/documents/upload`.
- Input: multipart form field `file`.
- Supported file types: PDF, DOCX, TXT, MD.
- Runtime flow: validate extension, create `documents` row with `status = uploaded`, upload original file to MinIO, create `document_files` row, return document id, filename, status va storage path.

## Document Parsing Foundation

- Endpoint: `POST /api/documents/{document_id}/parse`.
- Runtime flow: load document va document file metadata, download original file tu MinIO, select parser theo MIME type hoac extension, extract raw text, update document status tu `uploaded` sang `parsed`.
- Response: `document_id`, `status`, `character_count`, `preview`.
- Parsers:
  - TXT/MD: decode text.
  - PDF: extract text bang `pypdf`.
  - DOCX: extract paragraphs bang `python-docx`.
- Answer generation chay qua endpoint RAG chat rieng.

## Document Chunking Foundation

- Endpoint: `POST /api/documents/{document_id}/chunk`.
- Runtime flow: load document, require status `parsed` hoac `chunked`, require `parsed_text`, delete old chunks, create new chunks, update status thanh `chunked`.
- Default chunk config: `chunk_size = 1000`, `chunk_overlap = 150`.
- Split preference: paragraph `\n\n`, sentence `. `, space ` `, fallback by character.
- Chunk metadata JSONB:
  - `chunk_size`
  - `chunk_overlap`
  - `start_char`
  - `end_char`
- Response: `document_id`, `status`, `chunk_count`, preview cua 2 chunks dau tien.
- Answer generation chay qua endpoint RAG chat rieng.

## Embedding Va Vector Indexing Foundation

- Endpoint index: `POST /api/documents/{document_id}/index-vector`.
- Endpoint search: `POST /api/search/vector`.
- Default embedding provider: `FakeEmbeddingProvider`.
- Fake embedding:
  - deterministic
  - dimension `384`
  - generated from text hash
  - khong goi external API
- Qdrant collection: `hbrag_chunks`.
- Qdrant vector size: `384`.
- Qdrant distance: cosine.
- Index flow: require document status `chunked` hoac `indexed`, load chunks, embed chunk content, upsert Qdrant payload theo batch, update status thanh `indexed`.
- Qdrant upsert batch size mac dinh: `128`, cau hinh qua `QDRANT_UPSERT_BATCH_SIZE`.
- Vector payload:
  - `chunk_id`
  - `document_id`
  - `chunk_index`
  - `content`
  - `metadata`
- Vector search flow: embed query, query Qdrant, return `chunk_id`, `document_id`, `score`, content preview va metadata.
- Answer generation chay qua endpoint RAG chat rieng.

## Keyword Search Foundation

- Endpoint search: `POST /api/search/keyword`.
- Storage/index: `chunks.search_vector` kieu `tsvector`, co GIN index `ix_chunks_search_vector`.
- Search config: PostgreSQL `simple`, dung cho foundation ban dau voi noi dung Vietnamese/English mixed.
- Populate flow: khi tao chunks, repository cap nhat `search_vector = to_tsvector('simple', content)`.
- Query flow: dung `plainto_tsquery('simple', query)`, match bang operator `@@`, rank bang `ts_rank_cd`, return `chunk_id`, `document_id`, `score`, content preview va metadata.
- Answer generation chay qua endpoint RAG chat rieng.

## Hybrid Search Foundation

- Endpoint search: `POST /api/search/hybrid`.
- Request body:
  - `query`
  - `top_k`
  - `vector_weight`
  - `keyword_weight`
- Flow: chay vector search voi depth `top_k * 3`, chay keyword search voi depth `top_k * 3`, merge theo `chunk_id`, tinh fused score bang weighted Reciprocal Rank Fusion.
- RRF formula: `score = weight * (1 / (k + rank))`, voi default `k = 60`.
- Response moi result gom `chunk_id`, `document_id`, `fused_score`, `vector_score`, `keyword_score`, content preview, metadata va `source_flags`.
- Retrieval log: ghi `query`, `vector_results`, `keyword_results`, `hybrid_results` vao `retrieval_logs`.
- Answer generation chay qua endpoint RAG chat rieng.

## Reranking Foundation

- Endpoint search: `POST /api/search/rerank`.
- Request body:
  - `query`
  - `top_k`
  - `candidate_k`
- Default reranker provider: `FakeReranker`.
- Fake reranker:
  - deterministic
  - score dua tren token overlap giua query va content preview cua candidate
  - khong goi external model
- Optional BGE reranker wrapper: `BGEReranker`, lazy-load model khi `RERANKER_PROVIDER=bge`; can cai them optional `sentence-transformers` neu dung duong nay.
- Flow: chay hybrid search voi `candidate_k`, rerank candidates, sort theo `rerank_score`, tra ve top `top_k`.
- Response moi result gom `chunk_id`, `document_id`, `rerank_score`, `fused_score`, `vector_score`, `keyword_score`, content preview, metadata va `source_flags`.
- Retrieval log: ghi `query`, `vector_results`, `keyword_results`, `hybrid_results` va `reranked_results` vao `retrieval_logs`.
- Answer generation chay qua endpoint RAG chat rieng.

## RAG Answer Generation Foundation

- Endpoint: `POST /api/chat/rag`.
- Request body:
  - `query`
  - `session_id` optional
  - `top_k`
  - `candidate_k`
- Default LLM provider: `FakeLLM`.
- Fake LLM:
  - deterministic
  - khong goi external API
  - answer luon neu duoc generate tu provided context
  - co citation markers nhu `[1]`, `[2]` khi context co markers
- Optional OpenAI LLM provider: `OpenAILLM`, chi dung khi `LLM_PROVIDER=openai` va co `OPENAI_API_KEY`.
- Flow: neu thieu `session_id` thi tao `chat_sessions`, luu user message, chay reranking, load full chunks theo `chunk_id`, build context, generate answer, luu assistant message, tao citations.
- Prompt:
  - System: grounded RAG assistant, chi tra loi tu context, noi khong du thong tin neu context khong co answer.
  - User: gom `Question:` va `Context:` voi chunk markers `[1]`, `[2]`.
- Citation flow: quote lay 300 ky tu dau cua chunk content, citations link assistant message voi `chunk_id` va `document_id`.
- Response gom `session_id`, `user_message_id`, `assistant_message_id`, `answer` va danh sach citations.
- Frontend workspace co panel document pipeline va panel RAG chat co citations.

## Frontend

- `frontend/package.json`: khai bao Next.js 15, React 19, TypeScript, Tailwind, shadcn-style dependencies va scripts.
- `frontend/package-lock.json`: lockfile npm de cai dependency on dinh.
- `frontend/app/layout.tsx`: root layout cua Next.js App Router.
- `frontend/app/page.tsx`: RAG workspace client-side, gom panel document pipeline va panel chat voi citations.
- `frontend/app/globals.css`: Tailwind base styles va CSS variables theo phong cach shadcn UI.
- `frontend/components/ui/button.tsx`: Button component kieu shadcn, ho tro variant va `asChild`.
- `frontend/components/ui/card.tsx`: Card primitives cho top-level workspace panels.
- `frontend/components/ui/input.tsx`: Input primitive cho file upload.
- `frontend/components/ui/textarea.tsx`: Textarea primitive cho question input.
- `frontend/lib/api.ts`: typed frontend API client cho upload, parse, chunk, vector index va RAG chat.
- `frontend/lib/utils.ts`: helper `cn()` de merge class Tailwind.
- `frontend/components.json`: cau hinh shadcn UI.
- `frontend/tailwind.config.ts`, `postcss.config.mjs`: cau hinh Tailwind va PostCSS.
- `frontend/eslint.config.mjs`: cau hinh ESLint flat config, bo qua generated files.

## Infrastructure Va Docs

- `docker/README.md`: ghi chu ve Docker assets.
- `docs/ARCHITECTURE.md`: mo ta kien truc Hybrid RAG, provider runtime, cau hinh va vector store.
- `docs/AI_DEVELOPER_GUIDE.md`: huong dan cho AI coding agents lam viec an toan voi HBRag.
- `scripts/start-infra.ps1`: chay `docker compose up -d`.
- `scripts/stop-infra.ps1`: chay `docker compose down`.

## Thu Muc Sinh Ra Khi Chay

Nhung thu muc/file sau co the xuat hien sau khi cai dependency, build hoac chay dev server, nhung da duoc ignore:

- `backend/.venv/`
- `backend/.pytest_cache/`
- `backend/.ruff_cache/`
- `backend/**/*.egg-info/`
- `backend/**/__pycache__/`
- `frontend/node_modules/`
- `frontend/.next/`
- `frontend/tsconfig.tsbuildinfo`
- `*.log`
