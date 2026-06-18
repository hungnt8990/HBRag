# Tổng quan hệ thống backend HBRag

Tài liệu này được tổng hợp từ GitNexus ở commit `39fb6bf` và kiểm tra trực tiếp source trong `backend/`. GitNexus status tại thời điểm viết: index up-to-date.

## 1. Vai trò tổng thể

Backend là một API FastAPI phục vụ hệ thống RAG theo tài liệu nội bộ. Các trách nhiệm chính:

- Quản lý xác thực người dùng, tổ chức, quyền xem/tải tài liệu và knowledge base.
- Nhận tài liệu upload, lưu file gốc, parse nội dung, chunk tài liệu, index vector vào Qdrant.
- Cung cấp tìm kiếm vector, keyword, hybrid và rerank.
- Trả lời chat RAG có citation, memory, session context, profile theo loại tài liệu và graph expansion tùy cấu hình.
- Tùy chọn trích xuất graph entity/relation sang Neo4j để hỗ trợ graph retrieval.

Entrypoint chính là `app/main.py`. App đăng ký các router:

- `/api/auth` trong `app/api/routes/auth.py`
- `/api/documents` trong `app/api/routes/documents.py`
- `/api/search` trong `app/api/routes/search.py`
- `/api/chat` trong `app/api/routes/chat.py`
- `/api/knowledge-bases` trong `app/api/routes/knowledge_bases.py`
- `/api/memory` trong `app/api/routes/memory.py`
- `/api/admin` trong `app/api/routes/admin.py`
- health check trong `app/api/routes/health.py`

Khi startup, app kiểm tra cấu hình Qdrant collection qua `get_vector_store().validate_collection_config(...)`. Nếu `settings.graph_enabled = true`, app cũng verify kết nối Neo4j.

## 2. Cấu hình và hạ tầng ngoài

Cấu hình nằm ở `app/core/config.py`, chỉ đọc `backend/.env` thông qua Pydantic Settings.

Các dependency hạ tầng chính:

- PostgreSQL: lưu user, organization, document metadata, chunks, chat session/message, citations, logs, memory, knowledge base.
- MinIO/S3-compatible storage: lưu file tài liệu gốc.
- Qdrant: lưu dense vector, sparse vector và payload chunk để search.
- Neo4j: graph store tùy chọn cho entity/relation extraction và graph retrieval.
- LLM provider: `fake` hoặc OpenAI-compatible.
- Embedding provider: `fake` hoặc OpenAI-compatible.
- Reranker provider: `fake`, BGE hoặc OpenAI-compatible.
- Memory provider: local DB, Mem0 hoặc hybrid.

Các provider được dựng qua factory trong `app/services/embeddings`, `app/services/llms`, `app/services/rerankers`, `app/services/memory`.

## 3. Các lớp chính theo tầng

### API routes

Routes nhận request, kiểm tra auth/permission, dựng service qua FastAPI dependency injection, chuyển lỗi domain thành HTTP error.

- `documents.py`: upload, batch upload, parse, chunk, vector index, graph index, list/detail/delete document, stream pipeline status.
- `search.py`: vector search, keyword search, hybrid search, rerank search.
- `chat.py`: RAG chat thường và streaming, resolve document scope, memory context, profile runtime.
- `auth.py`: login/JWT và thông tin user.
- `knowledge_bases.py`: quản lý knowledge base và membership.
- `memory.py`: quản lý memory người dùng.
- `admin.py`: các endpoint quản trị và vận hành.

### Schemas

Pydantic schemas nằm trong `app/schemas`:

- `documents.py`: response/request cho upload, parse, chunk, vector index, graph index, document detail, search result.
- `chat.py`: `RagChatRequest`, `RagChatStreamRequest`, `RagChatResponse`, citation và session context.
- `auth.py`, `knowledge_bases.py`, `memory.py`: schema cho từng miền tương ứng.

### Models và repositories

SQLAlchemy models nằm trong `app/models`. Repository nằm trong `app/repositories` và là lớp truy cập DB chính.

Các model đáng chú ý:

- `Document`, `DocumentFile`, `Chunk`
- `ChatSession`, `ChatMessage`, `Citation`
- `User`, `Role`, `Organization`
- `KnowledgeBase`, `KnowledgeBaseMember`
- `DocumentPipelineLog`, `DocumentAccessLog`, `RetrievalLog`
- `UserMemory`, `SessionSummary`
- `GraphExtractionLog`, `GraphDocumentStatus`

GitNexus context cho thấy `DocumentRepository` là dependency trung tâm, được import bởi routes documents/search/chat/admin và các service parse/chunk/index/graph/rerank. Nó chịu trách nhiệm tạo tài liệu, cập nhật parsed content/metadata/status, tạo/list chunk, list document theo quyền, commit/rollback.

## 4. Luồng upload và ingestion tài liệu

Luồng cơ bản:

1. Client gọi `POST /api/documents/upload`.
2. `documents.py` kiểm tra `get_current_user`, quyền upload, visibility, organization và knowledge base.
3. `DocumentService.upload_document(...)` kiểm tra file rỗng, type hỗ trợ, trùng file, lưu file qua storage, tạo record document/document_file.
4. Route ghi `DocumentPipelineLog` action `upload` và commit DB.
5. Các bước parse/chunk/index có thể gọi riêng qua API hoặc chạy qua `IngestionQueue`.

`IngestionQueue` trong `app/services/ingestion_queue.py` gom pipeline bất đồng bộ cho upload/reingestion. Nó quản lý job, step log và gọi các service theo thứ tự parse, chunk, vector index, graph index tùy cấu hình/yêu cầu.

## 5. Luồng parse tài liệu

Service chính: `DocumentParserService` trong `app/services/document_parser_service.py`.

Nhiệm vụ:

- Lấy document và file gốc từ repository/storage.
- Chọn parser phù hợp từ `build_default_parsers()`.
- Parse PDF/DOCX/text/markdown hoặc Docling nếu bật.
- Chuẩn hóa text qua `_sanitize_parsed_text`.
- Cập nhật `parsed_text`, `parser_provider`, metadata và trạng thái document.

Parser nằm trong `app/services/parsers`:

- `pdf_parser.py`
- `docx_parser.py`
- `text_parser.py`
- `docling_parser.py`
- `optional_adapters.py`
- `table_serialization.py`

Docling và table serialization là phần quan trọng vì hệ thống cần giữ cấu trúc bảng, heading, page metadata và các record đặc thù để RAG trả lời chính xác hơn.

## 6. Luồng chunking

Service chính: `ChunkingService` trong `app/services/chunking_service.py`.

Nhiệm vụ:

- Kiểm tra document đã parse và có nội dung.
- Chọn profile/chunker theo loại tài liệu.
- Tạo chunk text và metadata.
- Xóa chunk cũ của document và ghi chunk mới vào DB.
- Cập nhật document profile, trạng thái chunking và thông tin thống kê.

Các chunker/phần hỗ trợ đáng chú ý:

- `RecursiveTextChunker`: chunk text tổng quát theo size/overlap.
- `LegalArticleChunker` và `app/services/chunkers/legal_article_chunker.py`: tách tài liệu pháp lý theo điều/khoản và bảng quyền lợi.
- `app/services/chunkers/catalog_table_chunker.py`: nhận diện/chuyển bảng danh mục thành record có cấu trúc.
- `app/services/chunkers/table_relationship_chunker.py`: record quan hệ người/khu vực hoặc bảng nghiệp vụ.
- `app/services/chunkers/docling_router.py`: route chunk Docling sang chunker chuyên biệt nếu phù hợp.
- `app/services/rag_chunk.py`: biểu diễn/metadata chunk cho RAG.

Profile tài liệu nằm trong `document_profiles.py` và `ingestion_profiles.py`. Chat route cũng dùng profile để tự chọn `top_k`, `candidate_k`, `answer_mode`, `answer_style`, `max_context_chars`.

## 7. Luồng vector indexing và Qdrant

Service chính: `VectorIndexingService` trong `app/services/vector_indexing_service.py`.

GitNexus context cho thấy class này được dùng bởi:

- `app/api/routes/documents.py` để index tài liệu.
- `app/api/routes/search.py` để vector search.
- `app/services/ingestion_queue.py` trong pipeline ingestion.
- `app/services/hybrid_search.py` trong hybrid retrieval.
- script `scripts/maintenance/reindex_vectors.py`.

Nhiệm vụ indexing:

1. Lấy document và chunks đã tạo.
2. Validate document/chunk đủ điều kiện index.
3. Sinh dense embedding qua `EmbeddingProvider`.
4. Sinh sparse embedding nếu `sparse_embedding_enabled`.
5. Build point payload gồm document/chunk ids, text preview, metadata, profile, page/source info.
6. Xóa point cũ theo document, upsert point mới vào Qdrant theo batch.
7. Cập nhật metadata/status index trong DB.

`QdrantVectorStore` trong `app/services/vector_store.py` bọc logic collection, named vector dense/sparse, upsert, delete và search. Startup của app validate collection config để tránh sai dimension/vector name.

## 8. Luồng search

Routes chính trong `app/api/routes/search.py`:

- `POST /api/search/vector`
- `POST /api/search/keyword`
- `POST /api/search/hybrid`
- `POST /api/search/rerank`

Trước khi search, route tính `visible_document_ids` dựa trên user, organization, descendants, knowledge base và permission. Client chỉ được search trong tập document đã được phép xem.

Các service:

- `VectorIndexingService.search(...)`: dense/sparse vector search trong Qdrant.
- `KeywordSearchService`: tạo SQL keyword search trên DB chunk.
- `HybridSearchService`: fuse kết quả vector và keyword, có boost cho structured row, legal leave metadata, schema/procedure, identifier lookup.
- `RerankingService`: lấy candidate từ hybrid, gọi reranker, ghi retrieval log, có thể dùng graph retrieval để mở rộng context.

## 9. Luồng RAG chat

Route chính: `POST /api/chat/rag` và endpoint streaming trong `app/api/routes/chat.py`.

Luồng xử lý:

1. Xác thực user bằng JWT qua `get_current_user`.
2. Tính `visible_document_ids` theo document/organization/knowledge base scope.
3. Nếu client gửi `session_context.allowed_document_ids`, backend chỉ intersect với tập document được phép, không cho mở rộng quyền.
4. Lấy memory context và session summary nếu bật.
5. Resolve profile runtime từ request, saved document profile hoặc auto-detection.
6. Gọi `RagAnswerService.answer(...)` hoặc `answer_stream(...)`.
7. Tự động lưu memory nếu message phù hợp và cấu hình cho phép.

`RagAnswerService` là class lớn nhất của phần RAG. GitNexus cho thấy nó được import bởi `chat.py` và nhiều test RAG/memory/table. Các trách nhiệm chính:

- Tạo hoặc load chat session.
- Gọi `RerankingService` để lấy context chunks.
- Mở rộng context bằng neighbor chunks, table relationships, structured evidence, legal leave logic và graph expansion nếu bật.
- Build system prompt theo `answer_mode`/`answer_style`.
- Gọi `LLMProvider.generate` hoặc `stream_generate`.
- Tạo citations và lưu chat messages.
- Trả `RagChatResponse` hoặc stream event gồm metadata/token/citations/done.

Hệ thống có lớp structured answer trong `app/services/structured`, dùng khi dữ liệu dạng bảng/record có cấu trúc đủ rõ để render câu trả lời deterministic hơn thay vì phụ thuộc hoàn toàn LLM.

## 10. Memory

Memory nằm trong `app/services/memory` và `app/repositories/memory.py`.

Các thành phần:

- `MemoryProvider` protocol.
- `LocalMemoryProvider`: lưu/search memory trong DB local.
- `Mem0Provider`: tích hợp Mem0 nếu bật.
- `HybridMemoryProvider`: phối hợp local và Mem0.
- `memory_service.py`: gather memory context và auto-save memory.
- `session_summary_service.py`: tóm tắt session sau một số lượng message nhất định.

Chat route gọi `_gather_memory(...)` trước RAG và `_auto_save(...)` sau khi có response.

## 11. Graph RAG tùy chọn

Graph nằm trong `app/services/graph`.

Các thành phần:

- `GraphIndexingService`: đọc chunks, extract entity/relation, merge và ghi graph.
- `GraphMergeService`: chuẩn hóa/merge entity và relation.
- `GraphRetrievalService`: lấy context liên quan từ graph khi search/rerank/chat.
- `Neo4jClient`: bọc truy cập Neo4j.
- `extractors`: fake hoặc LLM-based extractor.

Graph chỉ hoạt động khi `settings.graph_enabled = true`. Startup sẽ verify Neo4j connectivity. Documents route có endpoint graph index, ingestion queue cũng có thể chạy graph index trong pipeline.

## 12. Auth và permission

Auth dependency chính nằm ở `app/api/dependencies/auth.py`:

- Đọc bearer token.
- Decode JWT qua `app/core/security.py`.
- Lấy user bằng `AuthRepository.get_user_by_id`.
- Từ chối nếu token lỗi, user không tồn tại hoặc inactive.

Permission logic nằm trong `app/services/permissions.py`. Routes dùng các hàm như:

- `can_upload_document`
- `can_assign_upload_organization`
- `can_upload_to_knowledge_base`
- `can_view_document`
- `can_view_knowledge_base`
- `can_manage_document`

Điểm quan trọng: permission được kiểm ở route trước khi service thao tác, nhất là upload/search/chat để tránh truy cập document ngoài organization/knowledge base được phép.

## 13. Logging và observability nghiệp vụ

Backend có nhiều loại log nghiệp vụ:

- `DocumentPipelineLog`: ghi upload/parse/chunk/index/graph step.
- `DocumentAccessLog`: ghi truy cập tài liệu.
- `RetrievalLog`: ghi search/rerank/RAG retrieval.
- Chat messages và citations: lưu lại session, answer và nguồn trích dẫn.

Các log này phục vụ UI trạng thái pipeline, audit và debug chất lượng retrieval.

## 14. Test và script vận hành

Tests nằm trong `backend/tests`, bao phủ các vùng chính:

- document upload/library/profile/parse/chunking
- vector indexing/vector store
- hybrid search/rerank/RAG chat/streaming
- memory
- graph services
- structured/table/legal chunking logic

Script vận hành đáng chú ý:

- `scripts/maintenance/reindex_vectors.py`: reindex vector cho tài liệu.
- `scripts/maintenance/qdrant_create_payload_indexes.py`: tạo payload index cho Qdrant.
- `scripts/sql/20260613_embedding_metadata_indexes.sql`: index DB liên quan metadata embedding.

## 15. Bản đồ đọc code nhanh

Nếu cần hiểu hoặc sửa một luồng, bắt đầu ở các file sau:

- Startup/API composition: `app/main.py`
- Config: `app/core/config.py`
- Auth dependency: `app/api/dependencies/auth.py`
- Upload/list/detail document: `app/api/routes/documents.py`
- Search API: `app/api/routes/search.py`
- Chat RAG API: `app/api/routes/chat.py`
- DB document/chunk access: `app/repositories/documents.py`
- Upload service: `app/services/document_service.py`
- Parse service: `app/services/document_parser_service.py`
- Chunk service: `app/services/chunking_service.py`
- Vector/Qdrant: `app/services/vector_indexing_service.py`, `app/services/vector_store.py`
- Hybrid/rerank: `app/services/hybrid_search.py`, `app/services/reranking_service.py`
- RAG answer: `app/services/rag_answer_service.py`
- Graph: `app/services/graph/*`
- Memory: `app/services/memory/*`

## 16. Ghi chú từ GitNexus

Các truy vấn GitNexus dùng khi tạo tài liệu:

- `status`: index up-to-date tại commit `39fb6bf`.
- `query "backend API routes request flow"`: nổi bật flow `rag_chat` và các route backend.
- `query "RAG chat answer service retrieval generation"`: xác nhận `RagAnswerService`, `LLMProvider`, tests RAG/streaming là vùng trung tâm.
- `query "document ingestion parsing chunking vector indexing"`: xác nhận các flow list document/search/vector indexing/ingestion.
- `context RagAnswerService`: class RAG chính, import bởi `chat.py` và tests RAG/memory/table.
- `context VectorIndexingService`: dùng bởi documents route, search route, ingestion queue, hybrid search và script reindex.
- `context DocumentRepository`: repository trung tâm cho documents route, search/chat/admin và nhiều service xử lý tài liệu.
