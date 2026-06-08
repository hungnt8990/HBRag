# Tom Tat Loi Build/Run

## Trang Thai Hien Tai

Tai thoi diem kiem thu gan nhat, code backend, frontend build va Alembic online khong con loi.

Nhung lenh da chay thanh cong:

```powershell
docker compose config --quiet
```

```powershell
cd backend
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m alembic upgrade head --sql
.\.venv\Scripts\python.exe -m alembic upgrade head
```

Smoke test keyword search truc tiep qua service da thanh cong:

```powershell
cd backend
@'
import asyncio
from app.db.session import AsyncSessionLocal
from app.services.keyword_search import KeywordSearchService

async def main() -> None:
    async with AsyncSessionLocal() as session:
        service = KeywordSearchService(session)
        result = await service.search(query="test", top_k=1)
        print(result.model_dump())

asyncio.run(main())
'@ | .\.venv\Scripts\python.exe -
```

Ket qua:

```text
{'query': 'test', 'top_k': 1, 'results': []}
```

Ket qua backend gan nhat:

```text
42 passed, 1 warning
```

Frontend da duoc kiem thu gan nhat bang:

```powershell
cd frontend
npm run typecheck
npm run lint
npm run build
```

Kiem thu runtime truoc do da thanh cong:

- Backend: `GET http://127.0.0.1:8000/health` tra `status: ok`.
- Frontend: `GET http://127.0.0.1:3000` tra HTTP `200`.
- Luu y: backend dang chay tren port 8000 can restart de nhan CORS middleware moi neu server duoc start truoc thay doi nay.

## Gioi Han Kiem Thu Hien Tai

Da kiem tra migration bang Alembic offline SQL generation truoc do va online vao PostgreSQL local dang chay:

```powershell
.\.venv\Scripts\python.exe -m alembic upgrade head --sql
.\.venv\Scripts\python.exe -m alembic upgrade head
```

Chua chay integration upload/parse voi MinIO that trong luot nay; tests upload/parse dang dung fake storage/repository.

## Loi Da Gap Va Cach Xu Ly

| Khu vuc | Hien tuong | Nguyen nhan | Cach xu ly | Trang thai |
|---|---|---|---|---|
| Backend package install | `pip install -e ".[dev]"` bao `Multiple top-level packages discovered in a flat-layout: ['app', 'alembic']` | Sau khi them `backend/alembic/`, setuptools auto-discovery thay nhieu top-level package | Them `[tool.setuptools.packages.find]` trong `backend/pyproject.toml`, chi include `app*` va exclude `alembic*`, `tests*` | Da xu ly |
| Upload route import | FastAPI multipart route can dependency `python-multipart` | `POST /api/documents/upload` dung `UploadFile` va `File(...)` | Them `python-multipart>=0.0.20` vao backend dependencies | Da xu ly |
| Storage client import | Upload foundation can MinIO client SDK | Runtime storage dung S3-compatible MinIO client | Them `minio>=7.2.12` vao backend dependencies | Da xu ly |
| Parser dependencies | PDF/DOCX parser can thu vien doc file that | PDF dung `pypdf`, DOCX dung `python-docx` | Them `pypdf>=5.1.0` va `python-docx>=1.1.2` vao backend dependencies | Da xu ly |
| Chunking migration | Can luu raw text cho chunking | Schema cu chua co `documents.parsed_text` va `documents.parsed_at` | Them migration `0002_add_parsed_document_fields.py`, them status `chunked` vao check constraint | Da xu ly |
| Vector store dependency | Can Python client de giao tiep Qdrant async | Backend chua co `qdrant-client` dependency | Them `qdrant-client>=1.14.0`; smoke test Qdrant collection `hbrag_chunks` thanh cong | Da xu ly |
| OpenAI embedding provider | Can SDK neu sau nay doi tu fake sang OpenAI embeddings | Default dev/test dung fake, nhung provider OpenAI can SDK rieng | Them `openai>=1.100.0`; provider chi goi API khi `EMBEDDING_PROVIDER=openai` | Da xu ly |
| Keyword search migration | Can keyword search tren chunks bang PostgreSQL full-text search | Schema cu chua co `chunks.search_vector` va GIN index | Them migration `0003_add_chunk_search_vector.py`, backfill `to_tsvector('simple', content)`, tao GIN index va populate khi tao chunks | Da xu ly |
| Hybrid search foundation | Can hop nhat vector va keyword results, dong thoi ghi retrieval log | Truoc do chi co vector search va keyword search rieng le | Them `hybrid_search.py`, endpoint `POST /api/search/hybrid`, weighted RRF va `RetrievalLogRepository` | Da xu ly |
| Reranking foundation | Can rerank hybrid candidates va ghi `reranked_results` | Truoc do hybrid search moi dung RRF, chua co reranker abstraction | Them `services/rerankers/`, `FakeReranker`, optional `BGEReranker`, `reranking_service.py` va endpoint `POST /api/search/rerank` | Da xu ly |
| RAG answer generation | Can tao answer co citations va luu chat messages/citations | Truoc do moi dung search/rerank, chua co LLM provider hay chat endpoint | Them `services/llms/`, `FakeLLM`, optional `OpenAILLM`, `rag_answer_service.py`, `ChatRepository` va endpoint `POST /api/chat/rag` | Da xu ly |
| Frontend workspace | Can UI co ban cho document pipeline va RAG chat | Frontend truoc do chi la landing page stack | Them `frontend/lib/api.ts`, workspace tai `frontend/app/page.tsx`, va UI primitives Card/Input/Textarea | Da xu ly |
| Backend CORS local | Browser frontend can goi FastAPI tu `localhost:3000` | Browser se chan cross-origin requests neu API khong bat CORS | Them CORS middleware voi localhost origin/regex trong `backend/app/main.py` va config | Da xu ly |
| Upload frontend 404 | Browser upload request bao `404` | Backend dang chay process cu, OpenAPI runtime chi expose `/health` nen `/api/documents/upload` khong ton tai | Dung cac process uvicorn cu va start lai backend tu dung `backend/app/main.py`; OpenAPI hien co `/api/documents/upload` | Da xu ly |
| Upload storage 500 | Sau khi het 404, upload smoke test tra `Failed to upload document` | Port `9000` dang bi container `rag-minio` khac chiem va credentials khong khop HBRag | Them `MINIO_API_PORT`, tao `.env` local dung `MINIO_ENDPOINT=localhost:9100`, recreate `hbrag-minio`, bucket init thanh cong | Da xu ly |
| Index Vector 500 | Click `Index Vector` tra `Failed to vector index document` | Document co `6,266` chunks, backend gui tat ca points vao Qdrant trong mot request; Qdrant tu choi payload `52,677,616 bytes` vuot limit `33,554,432 bytes` | Them `QDRANT_UPSERT_BATCH_SIZE=128`, Qdrant vector store upsert theo batch, cho phep re-index document da `indexed` | Da xu ly |
| Backend install | `pip install -e ".[dev]"` bi timeout trong lan chay dau | Moi truong local dung Python 3.11, trong khi khai bao ban dau yeu cau Python 3.12 | Doi `requires-python` tu `>=3.12` sang `>=3.11` va `ruff target-version` tu `py312` sang `py311` | Da xu ly |
| Alembic migration | Check constraint name bi render thanh `ck_documents_ck_documents_status` va `ck_chat_messages_ck_chat_messages_role` trong offline SQL | Alembic naming convention ap dung lai len ten constraint da format san | Dung `op.f("ck_documents_status")` va `op.f("ck_chat_messages_role")` trong migration | Da xu ly |
| Backend lint | Ruff bao import order, unused import va line length trong migration | File moi them chua dung rule Ruff cua project | Chay Ruff auto-fix va xuong dong cac column dai | Da xu ly |
| Docker/PostgreSQL | Lan truoc khong chay duoc `docker compose ps` de verify migration online | Docker Desktop/daemon chua chay o thoi diem do | Docker hien da chay; da chay `alembic upgrade head` online vao PostgreSQL local | Da verify online |
| Frontend audit | `npm audit` bao 2 loi muc moderate lien quan `next` va `postcss` | Next keo dependency PostCSS nam trong range co advisory | Pin `postcss` ve `8.5.10` va them `overrides.postcss = 8.5.10` | Da xu ly, audit sach |
| Frontend lint | `eslint .` quet ca `.next/types` va bao nhieu loi generated code | ESLint CLI quet generated output cua Next.js | Them ignore trong `frontend/eslint.config.mjs`: `.next/**`, `node_modules/**`, `next-env.d.ts` | Da xu ly |
| Frontend lint script | `next lint` co canh bao deprecated trong Next.js 15 | Next.js se bo `next lint` o Next.js 16 | Doi script `lint` sang `eslint .` | Da xu ly |
| Next generated file | Sau khi build/dev, `frontend/next-env.d.ts` co the duoc Next.js tu sinh them reference toi `.next/types/routes.d.ts` | Day la hanh vi generated file cua Next.js khi chay build/dev | Khong coi la loi runtime; file da duoc ESLint ignore | Khong anh huong build |
| Backend test | `pytest` co warning `StarletteDeprecationWarning` tu `fastapi.testclient` | Dependency Starlette/FastAPI canh bao huong dung `httpx2` trong tuong lai | Chua can sua vi test van pass; theo doi khi nang version FastAPI/Starlette | Con warning, khong phai loi |

## Luu Y Van Hanh

- Docker Compose hien chi chay ha tang local: PostgreSQL, Qdrant, MinIO.
- Backend va frontend chay bang dev server rieng, chua container hoa app.
- Database foundation da co SQLAlchemy async, Alembic va initial migration.
- Document upload foundation da co endpoint, service, repository, schema va MinIO storage client.
- Document parsing foundation da co endpoint, service, parser interface va parsers cho TXT, MD, PDF, DOCX.
- Document chunking foundation da co endpoint, recursive text chunker, re-chunk delete old chunks va ghi `chunks`.
- Embedding va Qdrant vector indexing foundation da co fake embedding provider, Qdrant service, index endpoint va vector search endpoint.
- Keyword search foundation da co PostgreSQL full-text search tren `chunks.search_vector`, endpoint `POST /api/search/keyword` va test schema/query safety.
- Hybrid search foundation da co endpoint `POST /api/search/hybrid`, weighted RRF va ghi `retrieval_logs`.
- Reranking foundation da co endpoint `POST /api/search/rerank`, fake token-overlap reranker default va ghi `reranked_results`.
- RAG answer generation foundation da co endpoint `POST /api/chat/rag`, FakeLLM default, luu `chat_messages` va tao `citations`.
- Frontend workspace da co document pipeline panel, RAG chat panel, loading states, error messages va citations.
- Upload smoke test bang TXT qua `POST /api/documents/upload` da thanh cong HTTP `201`.
- Index vector smoke test voi document `7fb5095c-9093-4e92-89a1-beb4aa2c6333` da thanh cong HTTP `200`, `indexed_chunk_count = 6266`; Qdrant `points_count = 6266`.
- Neu chay `npm run build` hoac `npm run dev`, Next.js co the sinh lai `.next/` va chinh `next-env.d.ts`; day la hanh vi binh thuong.
