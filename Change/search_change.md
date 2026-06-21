# Thay đổi phần search hybrid Qdrant + Elasticsearch

## Mục tiêu

Dự án vẫn giữ Qdrant làm nguồn vector search chính như trước, đồng thời bổ sung Elasticsearch làm nguồn keyword/BM25 search chính. Khi hỏi đáp RAG, backend sẽ lấy kết quả từ:

1. Qdrant: tìm kiếm ngữ nghĩa bằng embedding.
2. Elasticsearch: tìm kiếm từ khóa/BM25 trên nội dung, heading, bảng và metadata của chunk.
3. PostgreSQL keyword search: chỉ dùng làm fallback nếu Elasticsearch chưa cấu hình, bị tắt hoặc lỗi.

Index Elasticsearch mới được đặt tên mặc định là `rag_cpcit`.

## Các file đã thêm

### `backend/app/services/elasticsearch_store.py`

File này là lớp giao tiếp thấp với Elasticsearch bằng HTTP:

- Đọc cấu hình `ELASTICSEARCH_URL` hoặc `ELASTICSEARCH_CLOUD_ID`.
- Gửi API key qua header `Authorization`.
- Tự tạo index `rag_cpcit` nếu chưa tồn tại.
- Tạo mapping và analyzer tiếng Việt đơn giản, có xử lý không dấu qua các field `_norm` và analyzer folded.
- Hỗ trợ bulk index chunk, xóa chunk theo `document_id`, refresh index và search.

### `backend/app/services/elasticsearch_indexing_service.py`

File này chuyển chunk hiện tại của HBRag sang document Elasticsearch.

Các field quan trọng được index gồm:

- Thông tin định danh: `chunk_id`, `semantic_chunk_id`, `document_id`.
- Phân quyền/phạm vi: `organization_id`, `knowledge_base_id`, `visibility`.
- Nội dung chính: `content`, `content_norm`.
- File/tài liệu: `source_file`, `document_title` và bản normalize không dấu.
- Heading/section: `section_path`, `section_id`, `parent_section_id`, `chapter_number`, `chapter_title`, `article_number`, `article_title`.
- Bảng: `table_name`, `table_description`, `table_headers`, `table_context`, `row_text`, `row_start`, `row_end`.
- Trường/thuộc tính trong bảng: `field_names`, `field_names_norm`.
- Từ khóa định danh: `identifiers`, `doc_codes`, `dates`.
- Entity đã enrich: `entities`, `entities_norm`.
- Metadata gốc của chunk: lưu vào `metadata`.

Mục đích là để Elasticsearch không chỉ tìm trên text thường, mà còn tìm tốt với tài liệu có bảng, heading, mã văn bản, tên trường dữ liệu và entity.

### `backend/app/services/elasticsearch_keyword_search.py`

File này thay vai trò keyword search chính bằng Elasticsearch.

Cách hoạt động:

- Nếu `ELASTICSEARCH_KEYWORD_ENABLED=true` và có cấu hình Elasticsearch hợp lệ, service sẽ search trên index `rag_cpcit`.
- Query được build theo dạng `bool should`, gồm:
  - `multi_match` trên nội dung thường.
  - `multi_match` trên các field normalize không dấu.
  - phrase search có boost cao cho mã, tên bảng, tên trường, heading, entity.
- Các field bảng như `table_name`, `table_headers`, `row_text`, `field_names` được boost cao hơn text thường.
- Nếu Elasticsearch lỗi hoặc chưa cấu hình, service tự động gọi lại keyword search cũ trên PostgreSQL.

## Các file đã sửa

### `backend/app/core/config.py`

Bổ sung cấu hình:

```env
ELASTICSEARCH_URL=
ELASTICSEARCH_CLOUD_ID=
ELASTICSEARCH_API_KEY=
ELASTICSEARCH_INDEX_NAME=rag_cpcit
ELASTICSEARCH_REQUEST_TIMEOUT=60
ELASTICSEARCH_VERIFY_SSL=true
ELASTICSEARCH_KEYWORD_ENABLED=true
```

Bạn có thể dùng một trong hai cách:

- Dùng `ELASTICSEARCH_URL` nếu có endpoint trực tiếp.
- Dùng `ELASTICSEARCH_CLOUD_ID` nếu dùng Elastic Cloud.

`ELASTICSEARCH_API_KEY` là nơi bạn điền API key thật.

### `backend/app/services/ingestion_queue.py`

Bước `index` của pipeline ingestion được mở rộng:

- Trước đây: chỉ index vào Qdrant.
- Hiện tại: index vào Qdrant trước, sau đó index cùng bộ chunk đó vào Elasticsearch.

Nếu Elasticsearch chưa cấu hình hoặc `ELASTICSEARCH_CLOUD_ID` sai định dạng, lỗi liên quan đến elasticsearch, bước Elasticsearch sẽ được đánh dấu `skipped`, còn Qdrant vẫn chạy bình thường. Nếu Elasticsearch đã cấu hình hợp lệ nhưng lỗi API, lỗi kết nối hoặc lỗi mapping, kết quả Qdrant vẫn được giữ; output của bước index sẽ ghi `elasticsearch.status = failed` kèm nội dung lỗi để kiểm tra.

### `backend/app/api/routes/search.py`

Dependency keyword search đã đổi từ PostgreSQL keyword search trực tiếp sang:

```text
ElasticsearchKeywordSearchService -> fallback PostgreSQL KeywordSearchService
```

Vì vậy các API hiện có như `/api/search/keyword`, `/api/search/hybrid`, `/api/search/rerank` vẫn giữ endpoint cũ, nhưng keyword branch sẽ ưu tiên Elasticsearch.

### `.env` và `.env.example`

Đã thêm các biến cấu hình Elasticsearch. API key đang để trống để bạn tự điền.

## Luồng hoạt động mới

### Khi upload và enqueue ingestion

1. Upload file như cũ.
2. Parse tài liệu như cũ.
3. Chunk tài liệu như cũ.
4. Enrich chunk như cũ.
5. Index:
   - Qdrant nhận embedding/vector payload như trước.
   - Elasticsearch nhận document giàu field để phục vụ BM25/keyword search.

### Khi hỏi đáp RAG

1. Backend gọi hybrid search.
2. Vector branch tìm bằng Qdrant.
3. Keyword branch tìm bằng Elasticsearch.
4. Nếu Elasticsearch chưa dùng được, keyword branch fallback sang PostgreSQL.
5. Backend merge kết quả vector + keyword bằng logic hybrid hiện tại.
6. Các chunk tốt nhất được đưa vào LLM để sinh câu trả lời.

## Lưu ý cấu hình

Sau khi thêm API key, cần restart backend để settings được nạp lại.

Nếu dùng Elastic Cloud, có thể cấu hình:

```env
ELASTICSEARCH_CLOUD_ID=...
ELASTICSEARCH_API_KEY=...
ELASTICSEARCH_INDEX_NAME=rag_cpcit
ELASTICSEARCH_KEYWORD_ENABLED=true
```

Nếu dùng URL trực tiếp:

```env
ELASTICSEARCH_URL=https://your-elasticsearch-endpoint
ELASTICSEARCH_API_KEY=...
ELASTICSEARCH_INDEX_NAME=rag_cpcit
ELASTICSEARCH_KEYWORD_ENABLED=true
```

## Ảnh hưởng tới chức năng cũ

Upload, chunking, enrichment, Qdrant indexing và RAG hiện tại vẫn giữ nguyên. Thay đổi chính là thêm Elasticsearch vào phía keyword search. PostgreSQL keyword search không bị xóa, chỉ chuyển thành fallback. Nếu Elasticsearch lỗi khi index, hệ thống không rollback phần Qdrant đã index.
