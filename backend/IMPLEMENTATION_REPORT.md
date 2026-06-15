# Báo cáo triển khai Docling V6 và Qdrant Hybrid RAG cho HBRag backend

## 1. Phạm vi thực hiện

Backend đã được kiểm tra và chỉnh sửa trực tiếp, không tạo một pipeline CLI song song. Các thay đổi tập trung vào:

- phân tích tài liệu bằng Docling;
- tích hợp logic chunking của `run_docling_chunking_recommended_350_v6.py` vào service dùng chung;
- chuẩn hóa dữ liệu chunk cho RAG;
- lưu mỗi chunk hợp lệ thành một point độc lập trong Qdrant;
- hỗ trợ named dense/sparse vectors và hybrid retrieval bằng RRF;
- lưu artifact PDF/Docling JSON/JSONL/Markdown/quality ngoài Qdrant;
- loại footer hành chính khỏi vector, keyword và graph retrieval;
- bảo đảm re-index không để lại point cũ;
- bổ sung test, cấu hình và tài liệu vận hành.

## 2. Kết luận về cách chunking cũ và mới

### 2.1. Luồng cũ

Luồng cũ của backend:

```text
Upload
→ parser PDF/DOCX/TXT/Markdown
→ parsed_text dạng phẳng
→ ChunkingRouter
→ recursive / legal / heading / table-aware / adaptive segmented
→ PostgreSQL chunks
→ dense embedding
→ Qdrant dense-only
```

#### Điểm tốt

- Nhẹ, ít phụ thuộc vào model bố cục.
- Có nhiều chiến lược và vẫn hữu ích với TXT, Markdown, code và tài liệu không có cấu trúc Docling.
- Có các rule chuyên biệt cho văn bản hành chính, bảng và dữ liệu GIS.
- Phù hợp làm fallback khi Docling không có hoặc parse thất bại.

#### Điểm chưa tốt

- PDF thường bị làm phẳng trước khi chunk nên mất quan hệ heading cha-con, bảng, ô gộp, furniture và thứ tự đọc qua trang.
- `segment_router.py` có nhiều regex đặc thù GIS; thay đổi bố cục hoặc tiêu đề cột có thể làm sai section.
- Có nguy cơ cắt câu qua trang, gắn quốc hiệu thành heading, gán continuation table sang object trước và tạo chunk chỉ một hàng.
- Metadata chunk chưa thống nhất.
- Qdrant cũ dùng một dense vector và payload lồng `{content, metadata}`.
- Re-index chưa có hợp đồng rõ để dọn stale points theo tài liệu.

### 2.2. Luồng mới

Quyết định triển khai:

- **Dùng Docling V6 làm luồng chính** cho PDF, DOCX và PPTX được Docling parse thành công.
- **Giữ router/chunker cũ làm fallback** cho TXT, Markdown, code, tài liệu không được Docling hỗ trợ hoặc không có artifact Docling JSON.

Luồng mới:

```text
Upload
→ DocumentConverter
→ DoclingDocument
→ lưu Docling JSON ở MinIO
→ HybridChunker
→ V6 structural repair + quality gate
→ RagChunk chuẩn hóa
→ lưu PostgreSQL + JSONL/Markdown/quality/coverage ở MinIO
→ build_embedding_text
→ dense + sparse embedding
→ mỗi chunk indexable là một Qdrant point
→ hybrid query dense+sparse + RRF
```

Cách này tốt hơn việc xóa toàn bộ pipeline cũ vì:

- Docling phù hợp tài liệu có layout, heading và bảng.
- Router cũ vẫn có giá trị với text thuần và làm fallback an toàn.
- Việc lựa chọn dựa trên parser/artifact thực tế, không hardcode `6515.pdf` hoặc tên đơn vị/bảng cụ thể.

### 2.3. Mức độ kế thừa V6

`app/services/docling_v6_chunking.py` giữ nguyên logic thực thi của 65 function/class chung trong file V6 gốc. Chỉ loại CLI `parse_args/main` và bổ sung ba thành phần backend:

- `DoclingV6ChunkingResult`;
- `chunk_docling_document(...)`;
- `render_chunks_markdown(...)`.

So sánh AST sau khi bỏ docstring cho kết quả:

```text
COMMON_EXECUTABLE_DEFS=65
DIFFERENT_EXECUTABLE_DEFS=[]
BACKEND_ONLY=['DoclingV6ChunkingResult', 'chunk_docling_document', 'render_chunks_markdown']
SOURCE_ONLY=['main', 'parse_args']
```

## 3. Luồng lưu trữ mới

### 3.1. MinIO/object storage

```text
<document>.pdf
<document>.docling.json
<document>.chunks.jsonl
<document>.chunks.md
<document>.quality.json
<document>.coverage.json
```

- PDF: nguồn gốc và citation.
- Docling JSON: cấu trúc tài liệu để chunk lại mà không parse PDF từ đầu.
- JSONL: nguồn backup/debug/re-index.
- Markdown: preview cho con người.
- Quality/Coverage: quality gate và kiểm tra bảo toàn nội dung.

### 3.2. PostgreSQL

- `documents`: trạng thái parser/chunker/indexing, version, artifact paths, quality và số lượng chunk.
- `chunks`: clean text, token count và metadata JSONB.
- Không cần migration mới vì backend đã có metadata JSONB phù hợp.

### 3.3. Qdrant

Mỗi chunk indexable là một point:

```json
{
  "id": "stable-uuid5",
  "vector": {
    "dense": [0.01, -0.02],
    "sparse": {
      "indices": [123, 456],
      "values": [0.8, 0.6]
    }
  },
  "payload": {
    "chunk_id": "database-chunk-uuid",
    "semantic_chunk_id": "chunk_002",
    "document_id": "document-uuid",
    "document_version": "v1",
    "tenant_id": "organization-uuid",
    "text": "Nội dung sạch của chunk",
    "chunk_type": "assignment_section",
    "content_format": "text",
    "section_path": [
      "1. CPCIT",
      "1.1. GIS 110kV, GIS trung thế"
    ],
    "unit": "CPCIT",
    "scope": ["GIS 110kV", "GIS trung thế"],
    "pages": [1],
    "page_start": 1,
    "page_end": 1,
    "table_name": null,
    "quality_status": "pass",
    "indexable": true,
    "embedding_enabled": true
  }
}
```

`chunk_id` được giữ là UUID của row PostgreSQL để pipeline reranking/hydration cũ tiếp tục hoạt động. `semantic_chunk_id` là ID logic do chunker sinh.

## 4. Chuẩn hóa RagChunk

Model `RagChunk` được thêm tại `app/services/rag_chunk.py`, bao gồm:

- identity: document/chunk/version/tenant;
- raw text, clean text;
- section path và parent section;
- unit, scope;
- pages;
- table metadata;
- parser/chunker version;
- quality và validation issues;
- flags indexable/embedding;
- content hash;
- database chunk ID và access metadata.

Mapping chính:

```text
headings → section_path
min(pages) → page_start
max(pages) → page_end
Markdown table → content_format=markdown_table
```

Bảng được giữ nguyên Markdown trong `text`; metadata bảng được suy ra và lưu riêng.

## 5. Embedding text

Hàm duy nhất:

```python
build_embedding_text(chunk: RagChunk) -> str
```

Chuỗi embedding chỉ thêm metadata có ích chưa xuất hiện trong text:

```text
Tài liệu: <title>
Cơ quan: <issuer>
Đơn vị: <unit>
Phạm vi: <scope>
Mục: <missing headings>
Bảng: <table name>
Mô tả bảng: <description>
Phạm vi hàng: <row start-end>

<clean text>
```

Không nối document preamble vào tất cả chunk. `raw_text` không được dùng để embedding.

## 6. Điều kiện index

Một chunk chỉ được đưa vào Qdrant khi:

```python
chunk.indexable is True
and chunk.embedding_enabled is True
and chunk.quality_status not in {"failed", "rejected"}
and chunk.chunk_type not in NON_INDEXABLE_CHUNK_TYPES
and chunk.text.strip()
```

Các loại bị loại:

```text
administrative_footer
header_footer
footer
empty
parse_error
```

Footer vẫn được giữ trong PostgreSQL/JSONL để bảo toàn tài liệu, nhưng không đi vào vector, keyword hay graph retrieval nghiệp vụ.

## 7. Dense và sparse retrieval

### Dense

- Dùng embedding provider hiện tại.
- Batch embedding.
- Validate số vector và dimension.

### Sparse

- Thêm abstraction `SparseEmbeddingProvider`.
- Provider mặc định là deterministic hashing sparse encoder.
- Bảo toàn whole token và token components cho mã kỹ thuật như:

```text
F08_CotDien_HT
MaTramBienAp
720/NQ-HĐTV
PMISToGIS
KHoPC
```

Provider hashing không phải SPLADE/BM25 học máy; có thể thay thế sau qua cùng interface.

### Qdrant query

Khi sparse bật:

```text
dense Prefetch
+
sparse Prefetch
→ FusionQuery(RRF)
→ top_k
```

Khi sparse tắt hoặc query không có sparse terms, backend tự rơi về dense-only.

## 8. Point ID và re-index

Stable point ID dùng UUID5 từ:

```text
tenant_id
document_id
document_version
semantic_chunk_id
chunker_version
content_hash
```

Trước mỗi re-index:

1. xóa toàn bộ point theo `document_id` và tenant nếu có;
2. batch upsert tập point mới;
3. nếu một batch thất bại sau khi các batch trước đã thành công, cleanup toàn bộ point của tài liệu;
4. không đánh dấu tài liệu `indexed` nếu upsert chưa hoàn tất.

Kết quả:

- chạy lại không tạo point trùng;
- chunk cũ không còn tồn tại sau re-index;
- không để retrieval đọc index một phần.

## 9. Qdrant collection và payload indexes

Collection mới mặc định:

```text
hbrag_chunks_v2
```

Cấu hình:

```python
vectors_config={
    "dense": VectorParams(size=<embedding_dimension>, distance=COSINE)
}
sparse_vectors_config={
    "sparse": SparseVectorParams()
}
```

Payload indexes:

### KEYWORD

```text
tenant_id
organization_id
knowledge_base_id
document_id
document_version
chunk_id
semantic_chunk_id
chunk_type
content_format
unit
scope
source_file
quality_status
table_name
visibility
```

### INTEGER

```text
page_start
page_end
chunk_index
```

Backend không tự xóa collection production khi schema cũ không tương thích. Khuyến nghị dùng collection versioned và re-index có kiểm soát.

## 10. File mới

```text
.env.example
README_RAG_INGESTION.md
IMPLEMENTATION_REPORT.md
app/services/docling_v6_chunking.py
app/services/parsers/docling_parser.py
app/services/rag_chunk.py
app/services/embeddings/sparse.py
app/services/embeddings/sparse_factory.py
tests/test_rag_chunk.py
```

## 11. File đã sửa

```text
app/api/routes/documents.py
app/api/routes/search.py
app/core/config.py
app/schemas/documents.py
app/services/chunking_service.py
app/services/document_parser_service.py
app/services/embeddings/__init__.py
app/services/graph/graph_indexing_service.py
app/services/ingestion_queue.py
app/services/keyword_search.py
app/services/parsers/__init__.py
app/services/parsers/base.py
app/services/parsers/optional_adapters.py
app/services/vector_indexing_service.py
app/services/vector_store.py
pyproject.toml
tests/test_document_parse.py
tests/test_vector_indexing.py
tests/test_vector_store.py
```

## 12. API/service integration

Business logic được đưa vào service dùng chung:

- `DoclingParser`;
- `DocumentParserService`;
- `ChunkingService`;
- `RagChunk` normalizer;
- dense embedding provider hiện có;
- sparse embedding provider;
- `VectorIndexingService`;
- `QdrantVectorStore`;
- ingestion queue;
- retrieval routes.

Không để toàn bộ logic trong FastAPI route. Script CLI V6 chỉ còn là tài liệu tham chiếu, không phải đường chạy chính.

## 13. Cấu hình mới

Xem `.env.example`, gồm:

```text
QDRANT_URL
QDRANT_API_KEY
QDRANT_COLLECTION_NAME
QDRANT_UPSERT_BATCH_SIZE
QDRANT_UPSERT_RETRY_COUNT
QDRANT_HYBRID_CANDIDATE_MULTIPLIER
DENSE_VECTOR_NAME
SPARSE_VECTOR_NAME
SPARSE_EMBEDDING_ENABLED
SPARSE_EMBEDDING_PROVIDER
SPARSE_EMBEDDING_HASH_DIMENSIONS
ENABLE_DOCLING
ENABLE_DOCLING_V6_CHUNKING
DOCLING_CHUNK_MAX_TOKENS
DOCLING_CONTEXT_BUDGET
DOCLING_CONTEXT_MODE
DOCLING_OCR_MODE
DOCLING_STRICT_QUALITY
STORE_RAW_TEXT_IN_QDRANT
STORE_EMBEDDING_TEXT_IN_QDRANT
```

PDF có text layer nên dùng:

```env
DOCLING_OCR_MODE=off
```

PDF scan có thể dùng:

```env
DOCLING_OCR_MODE=rapidocr-onnx
```

## 14. Kiểm tra đã thực hiện trong môi trường hiện tại

### Thành công

```text
python -m compileall -q app tests
→ PASS
```

Chạy riêng test không phụ thuộc SQLAlchemy/Qdrant/Docling:

```text
7 passed in 0.08s
```

Sparse smoke test:

```text
SPARSE_SMOKE_OK
```

Kiểm tra logic V6 được port:

```text
65 executable definitions chung, 0 khác biệt
```

### Chưa thể chạy đầy đủ trong môi trường hiện tại

`python -m pytest` dừng khi load `tests/conftest.py` vì runtime hiện tại chưa có:

```text
ModuleNotFoundError: No module named 'sqlalchemy'
```

`python -m ruff check .` chưa chạy được vì runtime hiện tại chưa có package `ruff`.

Môi trường cũng chưa có các dependency integration chính như `qdrant-client`, `docling`, `minio`. Kết nối mạng không khả dụng nên không thể cài bổ sung trong phiên làm việc này.

Điều này có nghĩa:

- syntax/compile và các test thuần đã qua;
- chưa xác nhận end-to-end với Postgres, MinIO, Docling và Qdrant thật trong môi trường này;
- cần chạy bộ test đầy đủ trong `.venv` của dự án trước khi triển khai production.

## 15. Lệnh nghiệm thu cần chạy tại máy dự án

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'

python -m ruff check .
python -m pytest
```

Sau đó bật Postgres, MinIO và Qdrant, rồi kiểm thử end-to-end:

1. upload `6515.pdf`;
2. parse bằng Docling với OCR off;
3. chunk mặc định hoặc `chunk_mode=docling_v6`;
4. kiểm tra `.chunks.md`, `.chunks.jsonl`, `.quality.json`;
5. index Qdrant;
6. chạy lại index hai lần và xác nhận số point không tăng;
7. kiểm tra footer không xuất hiện trong retrieval;
8. hỏi các truy vấn:
   - `CPCIT được giao nhiệm vụ gì với GIS 110kV, trung thế và hạ thế?`
   - `F05_CongToKhachHang_HT có trường MaKhachHang không?`
   - `F08_CotDien_HT lấy dữ liệu MaTramBienAp từ nguồn nào?`
9. xác nhận citation trả về đúng file, trang và section.

## 16. Giới hạn và khuyến nghị tiếp theo

- Sparse hashing là baseline lexical channel; có thể nâng cấp sang learned sparse hoặc BM25 provider sau khi có benchmark.
- V6 structural repair vẫn có heuristic; luôn lưu và theo dõi `quality.json`.
- Nên benchmark retrieval cũ/mới trên tập câu hỏi chuẩn, không chỉ một tài liệu.
- Collection cũ cần migration/re-index sang collection named vectors mới.
- Không bật `AUTO_RECREATE_COLLECTION=true` ở production nếu chưa có backup và kế hoạch migration.

## 17. Trạng thái nghiệm thu hiện tại

Đã hoàn tất phần sửa mã nguồn và tích hợp kiến trúc theo yêu cầu. Bản này đủ để đưa vào môi trường dự án nhằm chạy full dependency tests và end-to-end validation. Chưa tuyên bố production-ready cho đến khi `ruff`, toàn bộ `pytest` và integration test với Postgres/MinIO/Docling/Qdrant thật đều đạt.
