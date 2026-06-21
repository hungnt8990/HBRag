# Guide chạy local dự án HBRag

Tài liệu này hướng dẫn chạy dự án HBRag trên máy local, ưu tiên Windows/PowerShell vì repo đang nằm trên Windows. Dự án gồm:

- Backend: FastAPI/Python trong `backend/`
- Frontend: Next.js 15/React trong `frontend/`
- Hạ tầng local: PostgreSQL, Qdrant, MinIO, Neo4j bằng `docker-compose.yml`

## 1. Cần cài trước

Cài các công cụ sau:

- Docker Desktop có Docker Compose
- Python 3.11 trở lên
- Node.js 20 trở lên
- Git

Kiểm tra nhanh:

```powershell
docker --version
docker compose version
python --version
node --version
npm --version
```

## 2. Chuẩn bị biến môi trường

Mở terminal tại thư mục root của project:

```powershell
cd D:\Internship\RAG_CPCIT\HBRag
```

Nếu chưa có file `.env`, tạo từ mẫu:

```powershell
Copy-Item .env.example .env
```

Với chạy local mặc định, chưa cần API key ngoài. Project đang dùng các provider fake mặc định:

- `EMBEDDING_PROVIDER=fake`
- `RERANKER_PROVIDER=fake`
- `LLM_PROVIDER=fake`
- `GRAPH_ENABLED=false`

Nhờ vậy bạn có thể chạy upload, parse, chunk, search và chat thử mà không cần OpenAI hay endpoint model riêng. Nếu muốn dùng model thật thì xem phần "Cấu hình model thật" ở cuối file.

## 3. Start hạ tầng local

Từ root project:

```powershell
docker compose up -d
```

Hoặc dùng script có sẵn:

```powershell
.\scripts\start-infra.ps1
```

Các service local:

- PostgreSQL: `localhost:5432`
- Qdrant HTTP: `http://localhost:6333`
- Qdrant gRPC: `localhost:6334`
- MinIO API: `localhost:9000`
- MinIO Console: `http://localhost:9001`
- Neo4j Browser: `http://localhost:7474`
- Neo4j Bolt: `bolt://localhost:7687`

Kiểm tra container:

```powershell
docker compose ps
```

Nếu port `9000` bị trùng, sửa `.env`:

```env
MINIO_API_PORT=9100
MINIO_ENDPOINT=localhost:9100
```

Sau đó chạy lại:

```powershell
docker compose up -d
```

## 4. Setup backend

Tạo virtual environment và cài dependency:

```powershell
cd D:\Internship\RAG_CPCIT\HBRag\backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

Nếu PowerShell chặn activate script, chạy:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

Sau đó mở lại terminal hoặc chạy lại lệnh activate.

## 5. Chạy database migration

Đảm bảo PostgreSQL đã chạy bằng Docker Compose, sau đó:

```powershell
cd D:\Internship\RAG_CPCIT\HBRag\backend
.\.venv\Scripts\Activate.ps1
python -m alembic upgrade head
```

Migration sẽ tạo các bảng chính như documents, chunks, users, roles, organizations, knowledge bases, memory, graph audit...

Các role mặc định được seed bởi migration:

- `SUPER_ADMIN`
- `CORP_ADMIN`
- `COMPANY_ADMIN`
- `UNIT_USER`
- `VIEWER`

## 6. Seed organization và tạo user đăng nhập

Frontend có trang login, nên cần tạo ít nhất một user.

Seed danh sách đơn vị từ CSV:

```powershell
cd D:\Internship\RAG_CPCIT\HBRag\backend
.\.venv\Scripts\Activate.ps1
python .\scripts\seed_organizations.py .\data\organizations.csv
```

Tạo user admin local:

```powershell
python .\scripts\create_user.py `
  --username huybui `
  --password huybui123 `
  --ma-dviqly CPC `
  --role SUPER_ADMIN `
  --email abc@example.local `
  --full-name "Local Admin"
```

Thông tin đăng nhập mẫu:

- Username: `admin`
- Password: `admin123`

Bạn có thể đổi password trong lệnh trên trước khi chạy.

## 7. Chạy backend

Trong terminal backend:

```powershell
cd D:\Internship\RAG_CPCIT\HBRag\backend
.\.venv\Scripts\Activate.ps1
python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Mở:

- API health: `http://localhost:8000/health`
- Swagger docs: `http://localhost:8000/docs`

Test bằng PowerShell:

```powershell
Invoke-RestMethod http://localhost:8000/health
```

## 8. Setup và chạy frontend

Mở terminal thứ hai:

```powershell
cd D:\Internship\RAG_CPCIT\HBRag\frontend
npm install
npm run dev
```

Mở trình duyệt:

```text
http://localhost:3000
```

Frontend mặc định gọi backend qua:

```env
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
```

Giá trị này nằm trong `.env` ở root project. Nếu đổi port backend, nhớ đổi biến này rồi restart frontend.

## 9. Luồng test nhanh sau khi chạy

1. Vào `http://localhost:3000`
2. Đăng nhập bằng user đã tạo, ví dụ `admin` / `admin123`
3. Upload file PDF/DOCX/TXT/MD
4. Chạy pipeline parse/chunk/index vector trên giao diện nếu có nút tương ứng
5. Hỏi thử ở phần chat/RAG

Bạn cũng có thể test API trực tiếp ở `http://localhost:8000/docs`.

## 10. Một số API hữu ích

Health:

```powershell
Invoke-RestMethod http://localhost:8000/health
```

Login:

```powershell
$login = Invoke-RestMethod `
  -Uri http://localhost:8000/api/auth/login `
  -Method Post `
  -ContentType "application/json" `
  -Body '{"username":"admin","password":"admin123"}'

$token = $login.access_token
```

Upload document:

```powershell
Invoke-RestMethod `
  -Uri http://localhost:8000/api/documents/upload `
  -Method Post `
  -Headers @{ Authorization = "Bearer $token" } `
  -Form @{ file = Get-Item "D:\path\to\sample.pdf" }
```

Parse document:

```powershell
$documentId = "paste-document-id-here"

Invoke-RestMethod `
  -Uri "http://localhost:8000/api/documents/$documentId/parse" `
  -Method Post `
  -Headers @{ Authorization = "Bearer $token" }
```

Chunk document:

```powershell
Invoke-RestMethod `
  -Uri "http://localhost:8000/api/documents/$documentId/chunk" `
  -Method Post `
  -Headers @{ Authorization = "Bearer $token" }
```

Index vector:

```powershell
Invoke-RestMethod `
  -Uri "http://localhost:8000/api/documents/$documentId/index-vector" `
  -Method Post `
  -Headers @{ Authorization = "Bearer $token" }
```

Chat RAG:

```powershell
Invoke-RestMethod `
  -Uri http://localhost:8000/api/chat/rag `
  -Method Post `
  -Headers @{ Authorization = "Bearer $token" } `
  -ContentType "application/json" `
  -Body '{"query":"Tóm tắt nội dung tài liệu","top_k":5,"candidate_k":20}'
```

## 11. Lệnh kiểm tra chất lượng code

Backend:

```powershell
cd D:\Internship\RAG_CPCIT\HBRag\backend
.\.venv\Scripts\Activate.ps1
python -m pytest
python -m ruff check .
```

Frontend:

```powershell
cd D:\Internship\RAG_CPCIT\HBRag\frontend
npm run typecheck
npm run lint
npm run build
```

## 12. Dừng project

Dừng backend/frontend bằng `Ctrl + C` trong từng terminal.

Dừng hạ tầng Docker nhưng giữ dữ liệu:

```powershell
cd D:\Internship\RAG_CPCIT\HBRag
docker compose down
```

Hoặc:

```powershell
.\scripts\stop-infra.ps1
```

Xóa cả dữ liệu volume local nếu muốn reset sạch database, Qdrant, MinIO, Neo4j:

```powershell
docker compose down -v
```

Sau khi reset volume, cần chạy lại migration, seed organization và tạo user.

## 13. Cấu hình model thật

Mặc định project dùng fake provider để chạy local không cần API key. Nếu muốn dùng endpoint OpenAI-compatible, sửa `.env`.

Embedding:

```env
EMBEDDING_PROVIDER=openai_compatible
EMBEDDING_BASE_URL=https://your-embedding-endpoint
EMBEDDING_API_KEY=your-key
EMBEDDING_MODEL=your-embedding-model
EMBEDDING_DIMENSION=1536
```

Reranker:

```env
RERANKER_PROVIDER=openai_compatible
RERANKER_BASE_URL=https://your-reranker-endpoint
RERANKER_API_KEY=your-key
RERANKER_MODEL=your-reranker-model
RERANKER_ENDPOINT_PATH=/rerank
```

LLM:

```env
LLM_PROVIDER=openai_compatible
LLM_BASE_URL=https://your-chat-endpoint
LLM_API_KEY=your-key
LLM_MODEL=your-chat-model
```

Nếu đổi `EMBEDDING_DIMENSION` sau khi Qdrant collection đã được tạo, backend có thể cảnh báo lệch vector size. Có hai cách xử lý:

```env
AUTO_RECREATE_COLLECTION=true
```

Hoặc gọi API:

```text
POST /api/admin/recreate-vector-store
```

Lưu ý: recreate collection có thể làm mất vector index hiện có, cần index lại tài liệu.

## 14. Bật Graph RAG tùy chọn

Neo4j đã có trong Docker Compose, nhưng graph mặc định đang tắt:

```env
GRAPH_ENABLED=false
```

Nếu muốn bật:

```env
GRAPH_ENABLED=true
NEO4J_URI=bolt://localhost:7687
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=hbrag_password
```

Sau đó restart backend. Kiểm tra graph health:

```text
GET http://localhost:8000/api/admin/graph-health
```

Nếu dùng extractor LLM thật cho graph, cần cấu hình LLM provider phù hợp. Nếu chỉ muốn test local nhẹ, giữ `GRAPH_ENABLED=false`.

## 15. Lỗi thường gặp

Port bị chiếm:

- Frontend cố định port `3000`; script `npm run dev` sẽ báo lỗi nếu port này đang bận.
- Backend dùng port `8000`; đổi bằng tham số `--port` và cập nhật `NEXT_PUBLIC_API_BASE_URL`.
- MinIO dùng `9000` và `9001`; có thể đổi `MINIO_API_PORT`, `MINIO_CONSOLE_PORT`, `MINIO_ENDPOINT`.

Không login được:

- Đảm bảo đã chạy `alembic upgrade head`.
- Đảm bảo đã seed organization.
- Đảm bảo đã tạo user bằng `backend/scripts/create_user.py`.
- Đảm bảo backend đang chạy ở `http://localhost:8000`.

Upload lỗi MinIO:

- Kiểm tra container `hbrag-minio` đang chạy.
- Kiểm tra `.env` có `MINIO_ENDPOINT=localhost:9000` đúng với port compose.
- Nếu đổi port MinIO, restart backend.

Search hoặc index lỗi Qdrant:

- Kiểm tra container `hbrag-qdrant` đang chạy.
- Nếu đổi embedding dimension, recreate collection rồi index lại.

Backend startup báo lỗi Postgres:

- Chạy `docker compose ps`.
- Đợi Postgres healthy rồi chạy lại backend.
- Kiểm tra `DATABASE_URL` trong `.env`.

Dependency Python cài chậm hoặc lỗi:

- Project dùng `docling`, `pdfplumber`, `pypdf`, `python-docx`, `qdrant-client`, `sqlalchemy`, `fastapi`.
- Nên dùng Python 3.11 và virtualenv mới.
- Chạy `python -m pip install --upgrade pip` trước khi cài.

## 16. Thứ tự chạy hằng ngày

Sau khi đã setup một lần, mỗi lần chạy lại local thường chỉ cần:

Terminal 1:

```powershell
cd D:\Internship\RAG_CPCIT\HBRag
docker compose up -d
```

Terminal 2:

```powershell
cd D:\Internship\RAG_CPCIT\HBRag\backend
.\.venv\Scripts\Activate.ps1
python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Terminal 3:

```powershell
cd D:\Internship\RAG_CPCIT\HBRag\frontend
npm run dev
```

Mở:

```text
http://localhost:3000
```
