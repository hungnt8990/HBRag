# 1\. Mục tiêu dự án

Tên dự án:

    HBRag (Hybrid RAG Platform)

Mục tiêu:

    Cho phép người dùng upload tài liệu→ phân tích→ chia chunk→ embedding→ tìm kiếm hybrid→ reranking→ sinh câu trả lời có dẫn nguồn

Khác với RAG cơ bản:

    Question→ Vector Search→ LLM

Hệ thống của bạn là:

    Question→ Vector Search→ Keyword Search→ Hybrid Fusion→ Reranking→ Context Builder→ LLM→ Citation

* * *

# 2\. Kiến trúc tổng thể

    Frontend (NextJS)        │        ▼FastAPI Backend        │ ┌──────┼──────┐ │      │      │ ▼      ▼      ▼Postgres Qdrant MinIO        │        ▼OpenAI Compatible Models

* * *

# 3\. Công nghệ Backend

## 3.1 FastAPI

Framework chính:

    FastAPI

Vai trò:

    REST APIDependency InjectionPydantic ValidationOpenAPI DocumentationAsync Processing

Ví dụ endpoint:

    POST /uploadPOST /parsePOST /chunkPOST /index-vectorPOST /search/vectorPOST /search/hybridPOST /search/rerankPOST /chat/rag

* * *

## 3.2 SQLAlchemy 2.x

ORM chính:

    SQLAlchemy Async

Vai trò:

    Mapping PostgreSQLAsync database accessRepository pattern

* * *

## 3.3 Alembic

Migration system:

    Alembic

Vai trò:

    Version hóa schemaDatabase migrationRollback

* * *

# 4\. PostgreSQL

Sử dụng cho:

    Metadata StorageChat HistoryRetrieval LogsCitation Storage

* * *

## Bảng documents

    documents

Lưu:

    Tên tài liệuTrạng tháiparsed_texttimestamps

* * *

## Bảng chunks

    chunks

Lưu:

    Chunk contentChunk metadataChunk index

* * *

## Bảng chat\_sessions

    chat_sessions

Lưu:

    Conversation

* * *

## Bảng chat\_messages

    chat_messages

Lưu:

    User messageAssistant message

* * *

## Bảng citations

    citations

Lưu:

    Answer ↔ Chunk mapping

* * *

## Bảng retrieval\_logs

    retrieval_logs

Lưu:

    Vector resultsKeyword resultsHybrid resultsReranked results

Đây là điểm rất quan trọng.

Nhiều RAG không lưu retrieval log.

Hệ thống của bạn có thể debug:

    Tại sao AI trả lời sai

* * *

# 5\. MinIO

Object Storage

Vai trò:

    Lưu file gốc

Ví dụ:

    PDFDOCXTXTMarkdown

Thay vì lưu trong DB.

* * *

# 6\. Parsing Layer

Parser abstraction.

Hỗ trợ:

    TXTMDPDFDOCX

Thư viện:

    pypdfpython-docx

* * *

# 7\. Chunking Layer

Sau khi parse:

    Raw Text

được chuyển thành:

    Chunks

* * *

## Chiến lược chunk

    Recursive Chunking

Ưu tiên:

    ParagraphSentenceWordCharacter

* * *

## Cấu hình hiện tại

    Chunk Size = 1000Overlap = 150

* * *

# 8\. Embedding Layer

Provider abstraction:

    EmbeddingProvider

* * *

## FakeEmbeddingProvider

Dùng cho test.

    Hash → Vector

* * *

## Production Embedding

Hiện tại:

    BAAI/bge-m3

* * *

Thông số:

    Dimension = 1024

* * *

Provider:

    EMBEDDING_PROVIDER=openai_compatible

* * *

# 9\. Qdrant

Vector Database

Collection:

    hbrag_chunks

* * *

Distance:

    Cosine Similarity

* * *

Payload:

    chunk_iddocument_idchunk_indexmetadatacontent

* * *

# 10\. Keyword Search

Không dùng Elasticsearch.

Dùng:

    PostgreSQL Full Text Search

* * *

Công nghệ:

    tsvectortsqueryGIN Index

* * *

Lợi ích:

    Đơn giảnNhanhKhông thêm hạ tầng

* * *

# 11\. Hybrid Search

Đây là phần quan trọng nhất.

* * *

## Vector Search

Tìm:

    Semantic Similarity

* * *

## Keyword Search

Tìm:

    Exact Match

* * *

## Hybrid Fusion

Thuật toán:

    RRFReciprocal Rank Fusion

Công thức:

score\=∑i1k+rankiscore=\\sum\_i\\frac{1}{k+rank\_i}score\=∑i​k+ranki​1​

* * *

Kết quả:

    Vector + BM25

* * *

# 12\. Reranking

Provider abstraction:

    RerankerProvider

* * *

Hiện tại:

    BAAI/bge-reranker-v2-m3

* * *

Vai trò:

    Top 20 chunks↓Re-score↓Top 5 chunks

* * *

# 13\. LLM Layer

Provider abstraction:

    LLMProvider

* * *

Hiện tại:

    Qwen/Qwen3.5-9B

(OpenAI Compatible API)

* * *

Không phụ thuộc:

    OpenAIClaudeGeminivLLMOllama

* * *

# 14\. RAG Answer Pipeline

Luồng hiện tại:

    User Question        │        ▼Hybrid Search        │        ▼Reranking        │        ▼Context Builder        │        ▼Prompt Assembly        │        ▼LLM        │        ▼Answer        │        ▼Citation Creation

* * *

# 15\. Citation System

Điểm mạnh hiện tại.

Mỗi answer lưu:

    chunk_iddocument_idquote

Có thể:

    Trace sourceAudit answerExplain hallucination

* * *

# 16\. Test Coverage

Hiện tại:

    51 tests

bao gồm:

    UploadParseChunkEmbeddingQdrantKeyword SearchHybrid SearchRerankingRAG ChatRuntime ConfigAdmin APIs

* * *

# 17\. Mức độ hoàn thiện

Nếu đánh giá như một hệ thống RAG thực tế:

    Backend Core:      90%Retrieval Layer:   95%Observability:     70%Frontend:          40%Auth:               0%Multi-user:         0%Evaluation:         0%Production Ops:    30%

Tức là phần khó nhất của RAG:

    Hybrid Retrieval Engine

bạn đã xây dựng gần như hoàn chỉnh. Phần còn lại chủ yếu là vận hành, UI, quản trị tri thức và tối ưu chất lượng retrieval/answer.