# HBRag - Tóm Tắt Pipeline Chatbot Và RAG Pipeline

Tài liệu này giúp bạn nắm nhanh dự án HBRag theo đúng source code hiện tại. `README.md` và `structure.md` mô tả nền tảng ban đầu, nhưng code hiện tại đã mở rộng khá nhiều: có xác thực/phân quyền, knowledge base, ingestion profile, Docling V6 chunking, xử lý bảng, dense + sparse vector search, hybrid retrieval, rerank, memory, streaming chat và GraphRAG tùy chọn.

## 1. Bức Tranh Tổng Thể

HBRag là hệ thống Hybrid RAG gồm:

- Backend FastAPI trong `backend/app`.
- Frontend Next.js trong `frontend`.
- PostgreSQL lưu metadata tài liệu, chunks, user, organization, knowledge base, chat session/message, citations, logs và memory.
- MinIO lưu file gốc và artifact sinh ra trong lúc parse/chunk.
- Qdrant lưu vector dense/sparse của chunk.
- Neo4j tùy chọn cho GraphRAG.

Pipeline tổng quát:

```text
Upload
-> Parse
-> Chunk
-> Optional Enrich
-> Embed dense/sparse
-> Index Qdrant + PostgreSQL keyword index
-> Search vector/keyword/hybrid
-> Rerank
-> Expand context
-> Build prompt
-> LLM answer
-> Save message + citation
```

## 2. File Chính Cần Biết

- `backend/app/main.py`: tạo FastAPI app, đăng ký router, startup load ingestion profiles, validate Qdrant, validate Neo4j nếu bật graph.
- `backend/app/core/config.py`: toàn bộ biến cấu hình quan trọng: Qdrant, embedding, sparse embedding, parser, Docling, chunk size, reranker, LLM, memory, graph, access control.
- `backend/app/api/routes/documents.py`: API upload, list, parse, chunk, enrich, index vector, index graph, detail, delete.
- `backend/app/api/routes/search.py`: API vector search, keyword search, hybrid search, rerank search.
- `backend/app/api/routes/chat.py`: API chat RAG thường và streaming.
- `backend/app/services/document_service.py`: xử lý upload file.
- `backend/app/services/document_parser_service.py`: orchestration parse file.
- `backend/app/services/parsers/*`: parser TXT/MD/PDF/DOCX/Docling.
- `backend/app/services/chunking_service.py`: orchestration chunking và chọn mode.
- `backend/app/services/docling_generic_chunking.py`: Docling V6 chunking, sửa cấu trúc, chia token, quality report.
- `backend/app/services/chunkers/docling_router.py`: route Docling chunks sang legal/table/generic strategies.
- `backend/app/services/table_aware_chunking.py`: xử lý bảng, table row, entity summary/profile.
- `backend/app/services/rag_chunk.py`: schema metadata chunk, build text để embedding, payload Qdrant, filter chunk indexable.
- `backend/app/services/vector_indexing_service.py`: embed chunk, upsert Qdrant, vector search.
- `backend/app/services/vector_store.py`: wrapper Qdrant, collection named vector dense/sparse, filter, payload index.
- `backend/app/services/keyword_search.py`: PostgreSQL full-text + exact match.
- `backend/app/services/hybrid_search.py`: fuse vector + keyword bằng RRF và boost metadata.
- `backend/app/services/reranking_service.py`: lấy hybrid candidates, optional graph expansion, rerank.
- `backend/app/services/rag_answer_service.py`: pipeline chat RAG đầy đủ: rewrite, retrieval, context expansion, prompt, LLM, citation.
- `backend/app/services/query_rewrite_service.py`: rewrite follow-up question bằng context hội thoại/memory.
- `backend/app/services/query_scope_router.py`: chặn smalltalk/out-of-scope hoặc route identifier lookup.
- `backend/app/services/ingestion_profiles.py`: profile mặc định và thông số chunk/retrieval/answer theo loại tài liệu.
- `backend/app/services/document_profiles.py`: auto detect profile.
- `backend/app/services/ingestion_queue.py`: pipeline tự động upload -> parse -> chunk -> enrich -> index.
- `backend/app/repositories/documents.py`: DB document/chunk, populate `chunks.search_vector`.
- `backend/app/repositories/chat.py`: DB chat/citation/context neighbor lookup.
- `frontend/lib/api.ts`: client API typed.
- `frontend/lib/streaming.ts`: client SSE streaming chat.
- `frontend/components/chat-answer-panel.tsx`: hiển thị answer + inline citations.
- `frontend/components/document-library-panel.tsx`: hiển thị document library, logs, detail.

## 3. RAG Ingestion Pipeline

### 3.1. Upload

Entry point:

- API: `POST /api/documents/upload`, `POST /api/documents/upload-batch`.
- Route: `backend/app/api/routes/documents.py`.
- Service: `backend/app/services/document_service.py`.
- DB repository: `backend/app/repositories/documents.py`.
- Storage: `backend/app/services/storage.py`.

Cách làm hiện tại:

- Chỉ chấp nhận `.pdf`, `.docx`, `.txt`, `.md`.
- Lấy filename an toàn bằng `Path(...).name`.
- Kiểm tra file rỗng.
- Check duplicate theo `filename + file_size`.
- Tạo row `documents` với status `uploaded`.
- Upload file gốc lên MinIO theo path:

```text
documents/{document_id}/original/{uuid}{extension}
```

- Tạo row `document_files`.
- Ghi pipeline log action `upload`.
- Upload có auth/permission: user phải có quyền upload, document có `organization_id`, `knowledge_base_id`, `visibility` và access metadata.

Trạng thái sau bước này: `uploaded`.

### 3.2. Parse

Entry point:

- API: `POST /api/documents/{document_id}/parse`.
- Service: `DocumentParserService` trong `backend/app/services/document_parser_service.py`.
- Parser: `backend/app/services/parsers/*`.

Cách làm hiện tại:

- Chỉ parse document status `uploaded`, trừ khi `force_reparse=True` trong ingestion queue/reingestion.
- Lấy file gốc từ MinIO.
- Chọn parser theo setting `DOCUMENT_PARSER_PROVIDER`.
- Config default trong `config.py`:
  - `document_parser_provider = "auto"`
  - `enable_docling = True`
  - `enable_unstructured = False`
  - fallback parser builtin luôn có.

Thứ tự parser:

1. Nếu provider là `auto` hoặc `docling` và Docling available thì dùng `DoclingParser`.
2. Nếu bật unstructured thì thử `UnstructuredParser`.
3. Fallback: `TextParser`, `MarkdownParser`, `PdfParser`, `DocxParser`.

Parser builtin:

- `TextParser`: decode UTF-8 BOM, fallback latin-1, tách heading đơn giản.
- `MarkdownParser`: giữ markdown text, parse heading/list/code block thành `ParsedElement`.
- `PdfParser`: ưu tiên `pdfplumber`, fallback `pypdf`; có detect/extract table; serialize bảng thành `TABLE_TITLE`, `TABLE_HEADER`, `TABLE_ROW`; có xử lý bảng nhân sự phụ trách mảng công nghệ.
- `DocxParser`: đọc paragraph và table theo đúng thứ tự xuất hiện; table được serialize; heading lấy từ style `Title`/`Heading N`.

Parser Docling:

- File: `backend/app/services/parsers/docling_parser.py`.
- Hỗ trợ `.pdf`, `.pptx`, `.docx` ở cấp parser, nhưng upload service hiện chỉ cho PDF/DOCX/TXT/MD.
- Bật table structure: `pipeline_options.do_table_structure = True`.
- OCR mặc định tắt: `docling_ocr_mode = "off"`, `force_backend_text=True`.
- Nếu `docling_ocr_mode = "rapidocr-onnx"` thì bật RapidOCR.
- Output text là markdown từ `doc.export_to_markdown()`.
- Lưu thêm `docling_document` lossless JSON vào metadata tạm thời.
- Riêng PDF, lấy `page_texts` bằng pdfplumber và pypdf, chọn page text có score tốt hơn.
- Parse elements có page number, bbox, heading_path, table_id.

Sau parse:

- `parsed_text` được sanitize bỏ `\x00`.
- `documents.status = "parsed"`.
- `documents.parsed_at` được cập nhật.
- `document_metadata` được merge:
  - `parser`
  - `parsed_metadata`
  - `parsed_elements`
- Nếu Docling có `docling_document`, service lưu artifact lên MinIO:

```text
documents/{document_id}/artifacts/{stem}.docling.json
```

Trạng thái sau bước này: `parsed`.

### 3.3. Profile Detection

File:

- `backend/app/services/ingestion_profiles.py`
- `backend/app/services/document_profiles.py`

Dự án dùng ingestion profile để quyết định:

- chunk mode
- chunk size/overlap
- top_k/candidate_k khi chat
- answer mode/style
- max context chars
- heading rules
- detect rules

Profile mặc định đang có:

- `general`
  - chunk_mode `recursive`
  - chunk_size `1000`
  - chunk_overlap `150`
  - top_k `5`
  - candidate_k `20`
  - answer_style `detailed`
- `legal_admin`
  - chunk_mode `legal_article`
  - chunk_size `2500`
  - chunk_overlap `300`
  - top_k `8`
  - candidate_k `40`
  - heading rules cho chương/điều
- `catalog_table`
  - chunk_mode `table_aware`
  - chunk_size `1800`
  - chunk_overlap `120`
  - top_k `12`
  - candidate_k `60`
  - answer_style `table_qa`
- `staff_technology_matrix`
  - chunk_mode `table_aware`
  - chunk_size `1600`
  - chunk_overlap `120`
  - top_k `12`
  - candidate_k `80`
  - answer_style `table_qa`
- `spreadsheet`
  - chunk_mode `table_aware`
  - chunk_size `1800`
  - chunk_overlap `200`
  - answer_mode `extractive`
- `slide`
  - chunk_mode `slide_page`
  - chunk_size `1200`
  - chunk_overlap `0`

Auto detect profile:

- Ưu tiên file type: spreadsheet/slide.
- Dùng heading rules và detect rules trong profile config.
- Nếu text có dạng serialized table thì fallback `spreadsheet`.
- Nếu không match thì `general`.

Startup backend sẽ seed/load profile configs từ PostgreSQL.

### 3.4. Chunking

Entry point:

- API: `POST /api/documents/{document_id}/chunk`.
- Service: `ChunkingService` trong `backend/app/services/chunking_service.py`.

Request:

- `chunk_size`: 300-4000.
- `chunk_overlap`: >= 0 và không vượt quá nửa chunk_size.
- `chunk_mode`: `recursive`, `legal_article`, `table_aware`, `hybrid_structured`, `docling_v6`, `slide_page`, `heading_aware`.
- `profile`: `auto`, `legal_admin`, `catalog_table`, `general`, `spreadsheet`, `slide`.

Điều kiện:

- Document phải status `parsed` hoặc `chunked`.
- `parsed_text` không được rỗng.
- Chunk lại sẽ xóa chunk cũ trước khi tạo chunk mới.
- Sau khi chunk thành công, status thành `chunked`.

#### 3.4.1. Recursive chunking

File: `RecursiveTextChunker` trong `chunking_service.py`.

Thông số default:

- `default_chunk_size = 1000`
- `default_chunk_overlap = 150`
- split boundary tối thiểu `MIN_SPLIT_RATIO = 0.85`.

Separator ưu tiên:

```text
"\n\n\n", "\n\n", "\n- ", "\n+ ", ". ", "\n", " ", ""
```

Cách xử lý:

- Trước khi chunk text, service gọi `split_tables_and_text`.
- Nếu detect pattern bảng GIS schema `F\d+_...`, đoạn bảng được giữ nguyên thành chunk `gis_table`, không bị cắt theo overlap.
- Text thường được cắt theo `chunk_size`, tìm separator gần cuối window, nếu không có thì cắt theo target_end.
- Chunk sau bắt đầu từ `end_char - chunk_overlap`.
- Metadata có `start_char`, `end_char`, `chunk_size`, `chunk_overlap`, `overlap_applied`, `chunk_mode`, `document_profile`, `chunk_type`.

#### 3.4.2. Legal article chunking

File: `LegalArticleChunker` trong `chunking_service.py`.

Cách làm:

- Dùng heading rules trong profile `legal_admin`.
- Boundary heading thường là `Điều N`.
- Parent heading thường là `Chương`.
- Nếu section nhỏ hơn chunk_size thì giữ trọn điều/section.
- Nếu section quá dài thì dùng recursive inner chunker với separator:

```text
"\n\n", "\n", ". "
```

- Metadata thêm:
  - `chapter_title`
  - `article_number`
  - `article_title`
  - `section_title`
  - `heading_label`
  - `heading_number`
  - `heading_level`
  - `subchunk_index` nếu bị tách tiếp.

Mục tiêu: không làm đứt một điều/khoản quan trọng, giữ được chương/điều để citation và neighbor expansion sau này.

#### 3.4.3. Table-aware / hybrid structured chunking

File:

- `chunking_service.py`
- `table_aware_chunking.py`
- `table_relationships.py`

Khi nào dùng:

- Nếu request/profile chọn `table_aware` hoặc `hybrid_structured`.
- Nếu document có `parsed_elements` loại `table`/`table_row` và không ép chunk_mode, service tự chuyển sang `hybrid_structured`.

Cách làm:

- Tách prose chunks riêng.
- Tách table chunks riêng.
- Tạo thêm entity profile chunks từ table row.
- Reindex lại chunk_index.

Bảng được xử lý như sau:

- Detect serialized table `TABLE_TITLE`, `TABLE_HEADER`, `TABLE_ROW`.
- Detect pipe table có dấu `|`.
- Detect aligned table dựa trên khoảng trắng/cột.
- Tạo chunk phụ trợ:
  - `table_title`
  - `table_header`
  - `table_block`
- Tạo mỗi row thành một chunk:
  - `chunk_type = table_row`
  - `chunk_overlap = 0`
  - metadata có `table_id`, `headers`, `row_index`, `row_start`, `row_end`, `page_number`.
- Với bảng nhân sự/mảng công nghệ, có logic riêng:
  - parse `STT`
  - `area`
  - `lead_department`
  - `staff_names`
  - `relationship_type = technology_area_staff`
  - tạo `entity_profile` cho nhân sự.

Đây là phần quan trọng để chatbot trả lời câu hỏi dạng:

- ai phụ trách mảng nào
- một người tham gia những mảng nào
- liệt kê tất cả dòng
- đếm số cột/bảng/lớp dữ liệu

#### 3.4.4. Slide page chunking

Mode: `slide_page`.

- Nếu parsed_elements có element type `slide`, mỗi slide thành một chunk.
- Metadata có `chunk_type = slide_page`, `page_number`, `page_range`.
- Nếu không có slide elements thì fallback recursive.

Lưu ý: Docling V6 có heuristic riêng cho presentation-like PDF, nên với PDF slide parse bằng Docling, thường sẽ đi qua Docling router hơn là branch slide element đơn giản.

#### 3.4.5. Heading-aware chunking

Mode: `heading_aware`.

- Dùng `parsed_elements` title/heading/paragraph/list/code.
- Gom prose theo heading_path/section.
- Nếu không tạo được chunk từ parsed_elements thì fallback recursive.

#### 3.4.6. Docling router / Docling V6 chunking

Đây là flow mạnh nhất hiện tại.

Điều kiện dùng:

- `enable_docling_v6_chunking = True`.
- ChunkingService có storage.
- Request chunk_mode là `None`, `docling_router`, hoặc `docling_v6`.
- `document_metadata.parser == "docling"`.
- `parsed_metadata.artifact_paths.docling_json` tồn tại.

Flow:

1. Load `.docling.json` artifact từ MinIO.
2. `DoclingDocument.load_from_json`.
3. Gọi `chunk_docling_document` trong `docling_generic_chunking.py`.
4. Gọi `route_docling_chunks` trong `chunkers/docling_router.py`.
5. Enforce token limit lần nữa ở service-level.
6. Build quality report.
7. Nếu `docling_strict_quality = True` và có critical issue thì reject.
8. Convert record thành `RagChunk`.
9. Lưu chunks vào PostgreSQL.
10. Lưu artifacts:
    - `.chunks.jsonl`
    - `.chunks.md`
    - `.quality.json`
    - `.coverage.json`

Thông số Docling trong `config.py`:

- `docling_chunk_max_tokens = 350`
- `docling_context_budget = 80`
- `docling_context_mode = "metadata"`
- `docling_ocr_mode = "off"`
- `docling_strict_quality = True`

Docling V6 làm gì:

- Dùng `HybridChunker` của Docling.
- Tokenizer là `RegexVietnameseTokenizer`, đếm gần đúng bằng regex, không tải model ngoài.
- Giữ table dạng Markdown, repeat table header.
- Detect document profile:
  - `technical_schema`
  - `administrative_with_tables`
  - `administrative`
  - `presentation`
  - `mixed_with_tables`
  - `general`
- Nếu presentation-like: tạo record theo từng page/slide, ưu tiên native page text nếu chất lượng tốt hơn.
- Sửa các lỗi hay gặp:
  - câu bị cắt qua trang
  - heading cha-con
  - table quá nhỏ/quá dài
  - object/relationship schema bị gộp
  - identifier kỹ thuật
  - administrative footer không index
- Build coverage report và quality report.
- Enforce mỗi record không quá max token.

Docling router làm gì:

- Nếu document là legal article thì build legal records riêng.
- Nếu không, giữ generic Docling records.
- Bổ sung catalog table records nếu detect được.
- Bổ sung staff-area relationship records nếu detect được.
- Đặt profile:
  - `legal_article_document`
  - `catalog_table`
  - `mixed_administrative_technical`
  - `mixed_administrative_technical_with_relationships`

Metadata chunk Docling rất đầy đủ:

- `chunk_id`, `chunk_type`, `content_format`
- `section_path`
- `pages`, `page_start`, `page_end`
- `table_name`, `row_start`, `row_end`, `table_columns`
- `identifiers`, `doc_codes`, `dates`
- `quality_status`, `validation_issues`
- `parser = docling`
- `chunker = docling_router_v1`
- `chunk_strategy`, `docling_router_strategy`
- `document_context`

### 3.5. PostgreSQL Keyword Index

File: `DocumentRepository.create_chunks`.

Sau khi insert chunks, repository chạy:

```sql
search_vector = to_tsvector('simple', content)
```

Bảng `chunks` có cột `search_vector` và GIN index từ migration. Cấu hình `simple` dùng để tránh stemmer/special dictionary, phù hợp nội dung mixed Vietnamese/English ở giai đoạn hiện tại.

### 3.6. Optional Chunk Enrichment

Entry point:

- API: `POST /api/documents/{document_id}/enrich`.
- Service: `backend/app/services/chunk_enrichment_service.py`.
- Ingestion queue cũng chạy bước `enrich`.

Mặc định:

- `chunk_enrichment_enabled = False`.
- Nếu không bật và không `force=True` thì skip.

Nếu chạy:

- Mỗi chunk được gửi LLM để trích metadata JSON:
  - summary
  - keywords
  - aliases
  - document_type
  - issuing_org
  - document_code
  - issued_date
  - legal_refs
  - structure_path
  - entities
  - obligations/permissions/prohibitions
  - table_context
  - confidence
- Lưu vào `chunk_metadata.enrichment`.
- Lưu `enriched_content`, sau này `rag_chunk_from_database` có thể dùng làm `embedding_text`.

### 3.7. Vector Indexing

Entry point:

- API: `POST /api/documents/{document_id}/index-vector`.
- Service: `VectorIndexingService` trong `vector_indexing_service.py`.
- Qdrant wrapper: `vector_store.py`.
- Chunk payload builder: `rag_chunk.py`.

Điều kiện:

- Document status phải là `chunked` hoặc `indexed`.
- Phải có chunk.
- Chỉ index chunk thỏa `should_index_chunk`.

Chunk không index nếu:

- `indexable = False`
- `embedding_enabled = False`
- `quality_status` fail/failed/rejected
- `chunk_type` thuộc `administrative_footer`, `header_footer`, `empty`, `parse_error`, `footer`
- text rỗng

Embedding text:

- File: `rag_chunk.py`, hàm `build_embedding_text`.
- Không chỉ embed raw chunk text.
- Thêm các label giàu tín hiệu:
  - Tài liệu
  - Cơ quan
  - Đơn vị
  - Phạm vi
  - Số hiệu/mã
  - Văn bản
  - Ngày
  - Nền tảng
  - Giai đoạn
  - Loại thay đổi
  - Màn hình
  - Mục/heading
  - Bảng/cột bảng
  - Quan hệ
  - Mảng công nghệ
  - Phòng chủ trì
  - Nhân sự
  - Các mảng công nghệ
- Nếu chunk có `enriched_content`, nó được dùng làm body embedding.

Embedding provider:

- Default `embedding_provider = "fake"`.
- Fake provider tạo vector deterministic bằng SHA-256, normalize L2, dimension mặc định `384`.
- OpenAI-compatible gọi `POST {EMBEDDING_BASE_URL}/embeddings`.

Sparse embedding:

- Default `sparse_embedding_enabled = True`.
- Provider default `hashing`.
- Dimensions `1_048_576`.
- Token regex giữ được token Vietnamese và identifier có `_ . / -`.
- Giá trị TF log-normalized.

Qdrant config hiện tại:

- Collection default: `hbrag_chunks_v2`.
- Dense vector name: `dense`.
- Sparse vector name: `sparse`.
- Distance: cosine.
- Embedding dimension: `384`.
- Upsert batch size: `64`.
- Retry count: `2`.
- Hybrid candidate multiplier trong Qdrant: `4`.

Index flow:

1. Load document + chunks.
2. Convert DB chunk sang `RagChunk`.
3. Filter indexable chunks.
4. Build embedding_text.
5. Embed dense.
6. Embed sparse nếu bật.
7. Build Qdrant point id ổn định bằng UUID5 từ tenant/document/version/chunk/content_hash.
8. Xóa point cũ của document trong Qdrant.
9. Upsert point mới.
10. Update metadata ingestion status.
11. Set document status `indexed`.

Payload Qdrant:

- Flat payload, gồm nhiều metadata trực tiếp để filter:
  - `chunk_id`, `semantic_chunk_id`, `document_id`
  - `tenant_id`, `organization_id`, `knowledge_base_id`
  - `chunk_type`, `content_format`, `quality_status`
  - `table_name`, `page_start`, `page_end`, `chunk_index`
  - `identifiers`, `doc_codes`, `dates`
  - `visibility`, `classification`, access fields
  - `text`/`content` tùy setting lưu raw text

## 4. Retrieval Pipeline

### 4.1. Permission Scope Trước Khi Search

File:

- `backend/app/api/routes/search.py`
- `backend/app/api/routes/chat.py`
- `backend/app/services/access_control.py`
- `backend/app/services/permissions.py`

Mỗi request search/chat đều tính `visible_document_ids` dựa trên:

- current user JWT
- organization và descendant organization
- knowledge base scope
- document visibility/access
- optional document_id/organization_id/include_descendants

Sau đó:

- Vector search filter Qdrant theo document ids và access filter.
- Keyword search filter PostgreSQL theo document ids và access filter.
- Chat tiếp tục filter context chunk trước khi prompt.

Mặc định setting `access_read_all_documents = True`, nên access_filter trong vector/keyword bỏ qua nhiều clause. Nếu triển khai production nên review lại setting này.

### 4.2. Vector Search

Entry point:

- API: `POST /api/search/vector`.
- Service: `VectorIndexingService.search`.
- Store: `QdrantVectorStore.search`.

Request:

- `query`
- `top_k` default `5`, max `50`
- optional `knowledge_base_ids`

Cách làm:

1. Build query embedding text bằng `build_query_embedding_text`.
2. Nếu query là identifier/code/nguyên số, thêm hint:

```text
Mã tra cứu / số hiệu văn bản: ...
Identifier exact lookup: ...
```

3. Embed dense query.
4. Embed sparse query nếu bật.
5. Qdrant search:
   - Nếu có sparse: prefetch dense và sparse, mỗi channel lấy `top_k * qdrant_hybrid_candidate_multiplier`, sau đó Qdrant Fusion RRF, limit top_k.
   - Nếu không sparse: dense-only query.
6. Return score, chunk_id, document_id, content_preview 300 ký tự, metadata.

### 4.3. Keyword Search

Entry point:

- API: `POST /api/search/keyword`.
- Service: `KeywordSearchService`.

Cách làm:

- PostgreSQL full-text:

```sql
plainto_tsquery('simple', query)
chunks.search_vector @@ ts_query
ts_rank_cd(...)
```

- Cộng thêm exact ILIKE terms.
- Extract exact terms từ:
  - query trong ngoặc kép
  - entity/person name
  - code pattern
  - membership query person/area
  - n-gram content words từ query
- Mỗi exact term match được boost `10.0`.
- Sort theo exact_score desc, combined_score desc, document_id, chunk_index.
- Filter out chunk footer/parse_error/non-indexable.

### 4.4. Hybrid Search

Entry point:

- API: `POST /api/search/hybrid`.
- Service: `HybridSearchService`.

Request:

- `query`
- `top_k` default `5`, max `50`
- `vector_weight` default `1.0`
- `keyword_weight` default `1.0`

Cách làm:

1. Gọi vector search với depth `top_k * 3`.
2. Gọi keyword search với depth `top_k * 3`.
3. Merge theo `chunk_id`.
4. Fuse bằng Reciprocal Rank Fusion:

```text
score += weight * 1 / (60 + rank)
```

5. Giữ source flags:
   - `vector`
   - `keyword`
   - `lexical_exact`
6. Thêm metadata boosts:
   - structured row/table/section overlap boost
   - schema/procedure/table/field/count boost
   - identifier exact boost
   - person-area membership boost
7. Sort theo fused_score desc.
8. Save retrieval log nếu `save_log=True`.

Boost quan trọng:

- Identifier exact match:
  - content chứa mã/số: +50
  - metadata identifiers/doc_codes chứa mã/số: +50
  - metadata context/title/source chứa mã/số: +12
  - identifier query nhưng chunk không match: penalty nhỏ `0.02`
- Structured row boost:
  - row/table/relationship/chunk có overlap query tokens: +8 đến +30+.
- Schema/procedure boost:
  - match `object_code`, `field_name`, `relationship_name`, `table_name`.
  - count query về table/column/field/layer/object được boost table summary/schema chunks.

### 4.5. Reranking

Entry point:

- API: `POST /api/search/rerank`.
- Service: `RerankingService`.

Request:

- `query`
- `top_k` default `5`, max `50`
- `candidate_k` default `20`, max `200`, phải >= top_k.

Cách làm:

1. Gọi hybrid search với `top_k = candidate_k`, `vector_weight=1`, `keyword_weight=1`, `save_log=False`.
2. Nếu `use_graph=True`, gọi `GraphRetrievalService.expand` để thêm graph candidates.
3. Load full content DB cho các candidate.
4. Filter access trên full chunk nếu có subject_context.
5. Tạo `RerankCandidate(chunk_id, content)`.
6. Gọi reranker.
7. Nếu reranker lỗi, fallback ranking theo fused_score.
8. Sort:
   - identifier lookup: exact identifier priority trước
   - rerank_score desc
   - fused_score desc
   - chunk_id
9. Save retrieval log gồm vector/keyword/hybrid/reranked.

Reranker provider:

- Default `fake`: token overlap query/content, score = overlap / query_tokens.
- `openai_compatible`: `POST {base_url}/{endpoint_path}` với `model`, `query`, `documents`.
- `bge`: optional local BGE cross encoder.

## 5. Chatbot Pipeline

### 5.1. Frontend Chat Flow

File:

- `frontend/lib/api.ts`
- `frontend/lib/streaming.ts`
- `frontend/components/chat-answer-panel.tsx`

Frontend có hai cách gọi:

- Non-stream: `askRagChat` gọi `POST /api/chat/rag`.
- Stream: `streamRagChat` gọi `POST /api/chat/rag/stream`, nhận SSE event:
  - `metadata`
  - `token`
  - `citations`
  - `done`
  - `error`

UI hiển thị:

- Markdown answer.
- Citation marker `[1]`, `[2]` được render thành badge clickable.
- Source list riêng gồm document title, file, chunk index, page/article nếu có, quote, download link.

### 5.2. Chat Route Scope + Runtime Settings

File: `backend/app/api/routes/chat.py`.

Request chat có:

- `query`
- `session_id`
- `session_context`
- `document_id`
- `organization_id`
- `knowledge_base_ids`
- `include_descendants`
- `profile`
- `top_k`
- `candidate_k`
- `use_memory`
- `use_mem0`
- `answer_mode`
- `answer_style`
- `max_context_chars`
- `use_graph`

Flow route:

1. Xác thực user.
2. Tính visible document ids theo permission.
3. Nếu client đưa `session_context.allowed_document_ids`, backend chỉ intersect với visible ids, không bao giờ mở rộng quyền.
4. Tạo subject_context/access_filter.
5. Gather memory nếu request bật `use_memory`.
6. Resolve profile settings:
   - nếu profile auto, ưu tiên saved document_profile của document đang chat
   - nếu không có thì detect từ parsed_text/file
   - lấy top_k/candidate_k/answer mode/style/max context từ profile config
7. Gọi `RagAnswerService.answer` hoặc `answer_stream`.
8. Auto-save memory nếu bật.

### 5.3. Query Scope Router

File: `backend/app/services/query_scope_router.py`.

Trước khi retrieval, service classify query:

- `smalltalk`: ví dụ chào hỏi, trả lời trực tiếp.
- `out_of_scope`: thời tiết, dịch thuật, viết email, giá vàng, bóng đá, phép tính đơn giản.
- `identifier_lookup`: query có mã/số văn bản ngắn.
- `document_question`: câu hỏi tài liệu nội bộ.

Nếu smalltalk/out-of-scope, chatbot bỏ qua retrieval và LLM RAG, lưu assistant message với câu trả lời có sẵn, citation rỗng.

### 5.4. Query Rewrite Cho Follow-up

File: `backend/app/services/query_rewrite_service.py`.

Mục tiêu:

- Nếu user hỏi tiếp kiểu "văn bản này", "bảng đó", "người này", "ở trên", "còn cái này thì sao", service rewrite thành standalone search question.

Nguồn context để rewrite:

- `session_context.recent_messages`
- `last_topic`
- `current_scope`
- `user_scope`
- `current_document_id`
- session summary
- memory context

Cách làm:

- Nếu không có context: không rewrite.
- Nếu query không giống follow-up: không rewrite.
- Nếu cần rewrite: gọi LLM provider với prompt "rewrite follow-up question".
- Nếu LLM lỗi/kết quả không hợp lệ: fallback bằng cách nối query + short-term context hints.
- Context hints không được cite làm evidence.

### 5.5. Retrieval Trong Chat

File: `RagAnswerService.answer`.

Thứ tự:

1. Tạo hoặc lấy chat session.
2. Lưu user message.
3. Scope/direct answer nếu smalltalk/out-of-scope.
4. Rewrite query nếu cần.
5. Gọi reranking search với:
   - retrieval_query
   - top_k/candidate_k theo profile/request
   - document_ids đã scope
   - access_filter
   - use_graph nếu request bật
6. Load full chunks từ DB theo rerank results.
7. Filter access một lần nữa.

### 5.6. Context Expansion

Sau khi có reranked chunks, `RagAnswerService` mở rộng context:

1. Neighbor expansion theo entity/table/article:
   - `ChatRepository.get_entity_coverage_chunks`
   - `ChatRepository.get_table_chunks`
   - `ChatRepository.get_neighbor_chunks`
2. Nếu query là table enumeration/list/count, tăng context budget tới ít nhất `20_000` chars.
3. Nếu chunk có `table_id`, lấy title/header/row cùng table.
4. Nếu chunk có `article_number`, lấy các chunk cùng article.
5. Deduplicate theo chunk id, normalized content và article line.
6. Identifier lookup filter:
   - Nếu query là code/identifier và có chunk chứa literal code, chỉ giữ những chunk đó.
7. Augment person-area context:
   - Extract entity/person name từ query.
   - Tìm exact chunks trong scoped documents.
   - Ưu tiên `entity_profile`, trusted relationship metadata, `table_row`.
8. Augment structured fact context:
   - Chạy với mọi non-empty query.
   - Lấy entity coverage chunks theo n-gram từ query.
   - Ưu tiên structured row/section facts.
9. Nếu query có named entity nhưng context không chứa entity đó, trả lời không đủ căn cứ trực tiếp.

### 5.7. Prompt Building

File: `rag_answer_service.py`, hàm `_build_user_prompt` và `build_system_prompt`.

System prompt:

- Default answer mode: `hybrid`.
- Default answer style: `policy_explainer`.
- Mode:
  - `generative`
  - `extractive`
  - `hybrid`
- Style:
  - `concise`
  - `detailed`
  - `policy_explainer`
  - `table_qa`
- Có rule:
  - chỉ trả lời từ retrieved document context
  - nếu không có thông tin thì nói không đủ thông tin
  - memory/session summary chỉ là background, không cite
  - citation chỉ được từ numbered retrieved chunks
  - không tạo Sources/References section vì app render riêng
  - không output chain-of-thought hay `<think>`

User prompt gồm:

- Language constraint.
- Standalone retrieval question nếu có rewrite.
- Short-term chatbot context nếu có.
- User Memory nếu có.
- Session Summary nếu có.
- Exact identifier evidence policy nếu là identifier lookup.
- `ENTITY_MATCHED_ROWS` nếu có table/entity rows match query.
- `TABLE_SUPPORT` nếu có title/header/caption.
- `COUNT_EVIDENCE` nếu câu hỏi đếm số lượng.
- `Retrieved Document Context` với marker `[1]`, `[2]`.
- Dynamic answer requirements.
- Original `Question`.

### 5.8. LLM Generate Và Clean Answer

Provider:

- Default `fake`: trả lời deterministic "Generated from provided context..."
- `openai_compatible`: gọi `/chat/completions`, có stream support.

Sau khi LLM trả về:

- Remove `<think>...</think>`.
- Remove header `sources/references`.
- Gộp nhiều blank lines.

### 5.9. Citation

File:

- `ChatRepository.create_citations`
- `RagAnswerService._build_citation_response`

Cách tạo citation:

- Mỗi context chunk thành một citation record.
- Quote = 500 ký tự đầu của chunk content.
- Page number lấy từ `metadata.page_number` nếu có.
- Response citation có:
  - citation_index
  - chunk_id
  - document_id
  - document_title
  - file_name
  - chunk_index
  - quote
  - article_number/article_title/chapter_title
  - page_number
  - source_flags
  - metadata

Source flags public:

- `vector`
- `keyword`
- `graph`
- `neighbor`

Alias:

- `lexical_exact` map thành `keyword`
- `entity_exact` không public flag, nhưng raw_source_flags được giữ trong metadata.

### 5.10. Memory

File:

- `backend/app/services/memory/*`
- `backend/app/services/memory/memory_service.py`
- `backend/app/services/memory/session_summary_service.py`

Config default:

- `memory_provider = "local"`
- `memory_enabled = True`
- `memory_auto_save = True`
- `memory_inject_into_prompt = True`
- `memory_top_k = 5`
- `session_summary_every_n_messages = 10`

Memory chỉ được inject khi request `use_memory=True`.

Auto-save chỉ chạy khi message có phrase báo hiệu như:

- "nhớ rằng"
- "từ nay"
- "tôi thích"
- "hãy luôn"
- "ưu tiên"

Memory không bao giờ là citation source.

## 6. GraphRAG Optional

File:

- `backend/app/services/graph/*`
- `backend/app/api/routes/documents.py` endpoint `index-graph`
- `backend/app/services/reranking_service.py`

Mặc định:

- `graph_enabled = False`
- `graph_expansion_enabled = True`

Nếu bật:

- Startup validate Neo4j.
- Document có thể index graph bằng `POST /api/documents/{document_id}/index-graph`.
- Graph indexing trích entity/relation từ chunks, merge entity/relation, lưu graph Neo4j, audit status/log trong PostgreSQL.
- Trong reranking, nếu `use_graph=True`, graph retrieval expand candidates trước reranker.

Vai trò: tăng recall, không thay thế hybrid search. Hybrid RAG vẫn là backbone.

## 7. Ingestion Queue Tự Động

File: `backend/app/services/ingestion_queue.py`.

Pipeline queue:

```text
upload -> parse -> chunk -> enrich -> index
```

Đặc điểm:

- Mỗi step dùng AsyncSession mới, vì các service tự commit/rollback riêng.
- Hỗ trợ upload mới và reingest document cũ.
- Resolve profile sau parse, trước chunk.
- Ghi job logs và step output trong memory của process.
- Enrich step có thể skipped nếu config không bật.

API liên quan nằm trong admin routes, frontend có hàm:

- `enqueueIngestionJob`
- `reingestDocument`
- `getIngestionJob`
- `listIngestionJobs`

## 8. Thông Số Quan Trọng Hiện Tại

Từ `backend/app/core/config.py`:

- Qdrant collection: `hbrag_chunks_v2`
- Dense vector name: `dense`
- Sparse vector name: `sparse`
- Sparse embedding: bật
- Sparse provider: `hashing`
- Sparse dimensions: `1_048_576`
- Embedding provider: `fake`
- Embedding dimension: `384`
- Qdrant upsert batch size: `64`
- Qdrant upsert retry count: `2`
- Qdrant hybrid candidate multiplier: `4`
- Default chunk size: `1000`
- Default chunk overlap: `150`
- Parser provider: `auto`
- Docling enabled: `True`
- Docling V6 chunking enabled: `True`
- Docling max tokens: `350`
- Docling context budget: `80`
- Docling context mode: `metadata`
- Docling OCR mode: `off`
- Docling strict quality: `True`
- Reranker provider: `fake`
- LLM provider: `fake`
- Chunk enrichment enabled: `False`
- Memory enabled: `True`
- Graph enabled: `False`

## 9. Điểm Mạnh Hiện Tại

- Có pipeline RAG đầy đủ từ upload đến answer + citation.
- Xử lý bảng tốt hơn skeleton ban đầu: table row, table header/title, entity profile, staff-area relationship.
- Docling V6 giữ được cấu trúc page/table/heading và artifact để debug.
- Retrieval đã có 3 lớp:
  - Qdrant dense+sparse internal fusion
  - PostgreSQL keyword/exact
  - Hybrid RRF + metadata boost
- Identifier lookup được xử lý riêng, tránh hỏi mã văn bản nhưng bị drift sang chunk liên quan.
- Chat có scope/permission rõ ràng.
- Citation chain từ answer về chunk/document/file được lưu DB và render frontend.
- Profile config có thể sửa runtime từ admin/profile.

## 10. Điểm Cần Lưu Ý Khi Tối Ưu Tiếp

- `README.md` đang mô tả foundation cũ, một số default không còn đúng với code hiện tại. Nên ưu tiên source code và `backend/SYSTEM_OVERVIEW.md`.
- Default provider vẫn là fake cho embedding/reranker/LLM. Nếu test chat thật, cần cấu hình provider thật và recreate/reindex Qdrant nếu đổi dimension.
- Upload service chưa cho `.pptx` dù parser Docling có support.
- Docling strict quality có thể reject document nếu quality critical; cần xem artifact quality JSON khi debug.
- Chunk enrichment mặc định tắt, nên `enriched_content` không có nếu không force/bật config.
- `access_read_all_documents=True` làm access filter trong vector/keyword bỏ qua nhiều clause; nếu production cần review lại.
- Code có nhiều logic domain-specific cho EVN/GIS/staff matrix. Khi thêm domain mới, nên ưu tiên thêm ingestion profile/rules và chunker phụ trợ hơn là hardcode trong retrieval.

## 11. Bản Đồ Pipeline Theo File

| Giai đoạn | File chính | Vai trò |
| --- | --- | --- |
| App startup | `backend/app/main.py` | Load profile config, validate Qdrant/Neo4j, include router |
| Config | `backend/app/core/config.py` | Tất cả default/thông số runtime |
| Auth/permission | `backend/app/api/dependencies/auth.py`, `services/permissions.py`, `services/access_control.py` | JWT, role, document visibility/access |
| Upload | `api/routes/documents.py`, `services/document_service.py` | Validate file, tạo document, upload MinIO |
| Parse orchestration | `services/document_parser_service.py` | Chọn parser, lưu parsed_text/metadata/artifact |
| Parser PDF/DOCX/TXT/MD | `services/parsers/*.py` | Extract text/elements/tables |
| Parser Docling | `services/parsers/docling_parser.py` | Markdown, Docling JSON, page_texts, parsed_elements |
| Profile | `services/document_profiles.py`, `services/ingestion_profiles.py` | Detect profile và lấy config |
| Chunk orchestration | `services/chunking_service.py` | Chọn chunk mode, persist chunks |
| Docling chunking | `services/docling_generic_chunking.py`, `chunkers/docling_router.py` | V6 repair, route legal/table/generic |
| Table chunking | `services/table_aware_chunking.py`, `services/table_relationships.py` | Table rows, entity profile, staff-area |
| Chunk schema/payload | `services/rag_chunk.py` | RagChunk, embedding text, payload, indexable filter |
| Keyword index | `repositories/documents.py` | `to_tsvector('simple', content)` |
| Enrichment | `services/chunk_enrichment_service.py` | Optional LLM metadata/enriched_content |
| Vector index/search | `services/vector_indexing_service.py` | Embed/upsert/search |
| Qdrant | `services/vector_store.py` | Collection, dense/sparse, filters, payload indexes |
| Embeddings | `services/embeddings/*` | Fake/OpenAI dense, hashing sparse |
| Keyword search | `services/keyword_search.py` | FTS + exact ILIKE |
| Hybrid search | `services/hybrid_search.py` | RRF + boosts |
| Rerank | `services/reranking_service.py`, `services/rerankers/*` | Candidate reranking + optional graph expansion |
| Chat route | `api/routes/chat.py` | Scope, memory, profile runtime, stream |
| Query rewrite/scope | `services/query_rewrite_service.py`, `services/query_scope_router.py` | Follow-up rewrite, smalltalk/out-of-scope |
| Answer generation | `services/rag_answer_service.py` | Context expansion, prompt, LLM, citation |
| Chat DB | `repositories/chat.py` | Session/message/citation, neighbor chunks |
| Memory | `services/memory/*` | Local/Mem0 memory, auto-save, summary |
| GraphRAG | `services/graph/*` | Neo4j indexing/retrieval optional |
| Frontend API | `frontend/lib/api.ts`, `frontend/lib/streaming.ts` | REST/SSE client |
| Frontend answer UI | `frontend/components/chat-answer-panel.tsx` | Render markdown answer + citations |

