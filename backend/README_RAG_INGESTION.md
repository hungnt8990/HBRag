# HBRag ingestion: Docling V6 + Qdrant hybrid retrieval

## 1. Kết luận lựa chọn chunking

Backend giữ **router/chunker cũ làm fallback** cho TXT, Markdown, mã nguồn và các tài liệu không có `DoclingDocument`. Với PDF, DOCX và PPTX được Docling hỗ trợ, luồng mặc định chuyển sang:

```text
Upload
→ Docling DocumentConverter
→ lưu DoclingDocument JSON ở MinIO
→ Docling HybridChunker
→ V6 structural repair/table balancing
→ RagChunk chuẩn hóa
→ lưu Chunk trong PostgreSQL + artifact JSONL/Markdown/quality ở MinIO
→ dense + sparse embedding
→ mỗi RagChunk hợp lệ là một point Qdrant
```

Không thay toàn bộ hệ thống bằng một script CLI. Logic V6 được đưa vào service dùng chung và được gọi từ API cũng như ingestion queue.

## 2. Cách cũ: ưu và nhược điểm

### Ưu điểm

- Không phụ thuộc mô hình layout nặng; chạy được với text parser có sẵn.
- Router có nhiều chiến lược: recursive, legal article, heading-aware, table-aware và adaptive segmented.
- Các rule GIS hiện tại xử lý được một số mẫu biểu đã biết.
- Phù hợp làm fallback cho văn bản thuần và dữ liệu không parse được bằng Docling.

### Nhược điểm

- PDF thường bị làm phẳng trước khi chunk, khiến mất quan hệ heading cha-con, bảng, ô gộp và thứ tự đọc qua trang.
- `segment_router.py` có nhiều regex đặc thù GIS; độ chính xác giảm khi bố cục hoặc tên cột thay đổi.
- Một số bảng bị biến thành từng hàng độc lập, tạo chunk quá nhỏ và làm mất ngữ cảnh tên bảng/header.
- Khó xử lý câu bị cắt qua trang, furniture bị nhận nhầm thành heading và bảng tiếp tục qua nhiều trang.
- Metadata chưa thống nhất; vector payload trước đây lồng `{content, metadata}` và chỉ có dense vector.
- Re-index trước đây dựa vào DB chunk UUID và không chủ động xóa point cũ theo tài liệu.

## 3. Cách mới: ưu và giới hạn

### Ưu điểm

- Chunk dựa trên cấu trúc `DoclingDocument`, sau đó mới áp dụng giới hạn token.
- Giữ Markdown của bảng, lặp header bảng, cân bằng số hàng giữa các chunk.
- V6 sửa các lỗi thường gặp: câu qua trang, heading cha-con, furniture, merged-cell, bảng bị gán nhầm section, mã kỹ thuật bị tách khoảng trắng và chunk một hàng có thể tránh được.
- Phân biệt `raw_text`, `text` và `embedding_text`.
- Footer hành chính vẫn được bảo toàn trong artifact/DB nhưng không đi vào vector, keyword hoặc graph retrieval nghiệp vụ.
- Mỗi chunk hợp lệ là một point Qdrant với named dense/sparse vectors và payload phẳng.
- Stable UUID5 + xóa theo `document_id` trước re-index bảo đảm không còn point cũ.

### Giới hạn

- Docling cần thêm thời gian/model khi parse lần đầu.
- PDF scan phải bật OCR; PDF có text layer nên dùng `DOCLING_OCR_MODE=off` để tránh OCR làm sai mã kỹ thuật.
- Structural repair V6 vẫn là hậu xử lý heuristic tổng quát; `quality.json` và `validation_issues` phải được theo dõi.
- Collection Qdrant cũ không có named vectors phải được chuyển sang collection version mới; backend không tự xóa production collection khi `AUTO_RECREATE_COLLECTION=false`.

## 4. Dữ liệu được lưu ở đâu

### MinIO / object storage

```text
<document>.pdf
<document>.docling.json
<document>.chunks.jsonl
<document>.chunks.md
<document>.quality.json
<document>.coverage.json
```

- PDF: nguồn/citation.
- Docling JSON: cấu trúc parser để chunk lại mà không parse PDF lần nữa.
- JSONL: nguồn chuẩn để debug/re-index.
- Markdown: preview cho con người.
- Quality/Coverage: quality gate.

### PostgreSQL

- `documents`: trạng thái, parser/chunker version, artifact paths, chunk counts, collection.
- `chunks`: clean text, token count và metadata JSONB.
- Không cần migration mới vì các trường mở rộng được lưu trong JSONB hiện có.

### Qdrant

Mỗi chunk indexable là một point:

```json
{
  "id": "stable-uuid5",
  "vector": {
    "dense": [0.1, 0.2],
    "sparse": {"indices": [1, 4], "values": [0.6, 0.8]}
  },
  "payload": {
    "chunk_id": "database-chunk-uuid",
    "semantic_chunk_id": "chunk_002",
    "document_id": "document-uuid",
    "text": "clean chunk text",
    "section_path": ["1. CPCIT", "1.1. GIS 110kV, GIS trung thế"],
    "unit": "CPCIT",
    "scope": ["GIS 110kV", "GIS trung thế"],
    "pages": [1],
    "page_start": 1,
    "page_end": 1,
    "table_name": null,
    "indexable": true,
    "embedding_enabled": true
  }
}
```

`chunk_id` trong payload vẫn là UUID của bảng `chunks`, để reranker/RAG answer service có thể hydrate lại dữ liệu từ PostgreSQL. `semantic_chunk_id` là ID logic do chunker sinh.

## 5. Embedding text

Dense và sparse embeddings dùng cùng một chuỗi được dựng từ metadata có ích:

```text
Tài liệu: <document title>
Đơn vị: <unit>
Phạm vi: <scope>
Mục: <chỉ các heading chưa có trong text>
Bảng: <table name>
Mô tả bảng: <description>
Phạm vi hàng: <start-end>

<clean text>
```

Không nối toàn bộ document preamble vào mọi chunk. Metadata đã có trong phần đầu `text` sẽ không được thêm lần nữa.

## 6. Chunk nào được index

Điều kiện:

```python
indexable is True
and embedding_enabled is True
and quality_status not in {"failed", "rejected"}
and chunk_type not in NON_INDEXABLE_CHUNK_TYPES
and text.strip()
```

Các loại bị loại khỏi vector/keyword/graph retrieval:

```text
administrative_footer
header_footer
footer
empty
parse_error
```

## 7. Hybrid retrieval

- Dense: truy vấn theo ngữ nghĩa.
- Sparse hashing: bảo toàn exact token/mã như `F08_CotDien_HT`, `MaTramBienAp`, `PMISToGIS`.
- Qdrant fusion: RRF giữa named dense và named sparse vector.
- Hệ thống hiện vẫn có lớp Postgres keyword search + RRF ở `HybridSearchService`; vector leg nay đã mạnh hơn nhờ dense+sparse Qdrant.
- Kết quả trả về `text`, source file, pages, section path, unit, scope và table name để tạo citation.

## 8. Re-index và idempotency

Stable point ID được tạo từ:

```text
tenant_id : document_id : document_version : semantic_chunk_id : chunker_version : content_hash
```

Trước mỗi re-index, backend xóa toàn bộ point có cùng `document_id` và `tenant_id`, rồi batch upsert tập point mới. Vì vậy:

- chạy lại không tạo bản sao;
- chunk đã bị xóa khỏi phiên bản mới không còn point mồ côi;
- nội dung thay đổi tạo point ID mới nhưng point cũ đã được dọn.

## 9. Cấu hình

Xem `backend/.env.example`. Khuyến nghị:

```env
ENABLE_DOCLING=true
ENABLE_DOCLING_V6_CHUNKING=true
DOCLING_CHUNK_MAX_TOKENS=350
DOCLING_CONTEXT_MODE=metadata
DOCLING_OCR_MODE=off
QDRANT_COLLECTION_NAME=hbrag_chunks_v2
SPARSE_EMBEDDING_ENABLED=true
```

Với PDF scan:

```env
DOCLING_OCR_MODE=rapidocr-onnx
```

Cài thêm ONNX Runtime nếu môi trường Docling chưa có.

## 10. Collection migration

Collection cũ dùng unnamed dense vector không tương thích với collection mới có named `dense` và `sparse` vectors. Không bật tự động xóa production. Tạo collection mới bằng tên versioned, chạy re-index toàn bộ tài liệu, kiểm tra retrieval rồi mới đổi alias/config.

## 11. Kiểm tra

```bash
python -m compileall app tests
python -m ruff check .
python -m pytest
```

Nếu dùng Qdrant integration thật, chạy Qdrant/Postgres/MinIO trước khi chạy nhóm integration test.
