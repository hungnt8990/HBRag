# So sánh và đề xuất tối ưu chunking

Tài liệu này so sánh phương pháp chunking trong đoạn code ingest riêng của bạn với phương pháp chunking hiện tại của HBRag. Trọng tâm không phải là bê nguyên code riêng vào HBRag, mà là lấy các ý tưởng tốt nhất để cải thiện hệ thống hiện tại một cách an toàn, dễ bảo trì và phù hợp với pipeline RAG đang có.

## 1. Tóm tắt nhanh

Code riêng của bạn đang đi theo hướng:

```text
File -> convert_to_md -> adaptive_chunk_markdown
-> nhận diện text/table
-> nếu là text thì recursive chunk
-> nếu là bảng thì tạo nhiều loại chunk: parent/overview, group rows, row chunks
-> sinh metadata giàu: table_id, row_start, row_end, row_data, structured_rows, authors, years, entities
-> embed từng chunk
-> lưu vào Elasticsearch
```

HBRag hiện tại đang đi theo hướng:

```text
File/Document -> parsed_text + parsed_elements
-> ChunkingService chọn mode theo profile/parser
-> recursive / legal_article / table_aware / hybrid_structured / slide_page / docling_router / docling_v6
-> chuẩn hóa thành Chunk trong PostgreSQL
-> rag_chunk_from_database chuyển thành RagChunk
-> build_embedding_text thêm metadata vào text embedding
-> index vào Qdrant
-> retrieval + rerank + answer
```

Nhìn tổng quát:

- Code riêng của bạn mạnh ở xử lý bảng sau khi đã có markdown/HTML, đặc biệt là bảng HTML có rowspan/colspan, chunk theo nhiều tầng parent-group-row và metadata phục vụ search.
- HBRag mạnh ở kiến trúc pipeline, profile-based routing, Docling-first chunking, quality gate, metadata chuẩn hóa xuyên suốt đến retrieval/citation, và có nhiều chunker chuyên biệt theo loại tài liệu.
- Cách tốt nhất là không thay toàn bộ chunking hiện tại, mà bổ sung một lớp table normalization/table semantic chunking tốt hơn vào nhánh `hybrid_structured/table_aware/docling_router`, lấy ý tưởng parent-group-row và structured metadata từ code riêng của bạn.

## 2. Các file chunking quan trọng trong HBRag

Các file điều phối chính:

- `backend/app/services/chunking_service.py`
  - Trung tâm của bước chunking.
  - Nhận `document_id`, đọc `parsed_text`, `document_metadata`, `parsed_elements`.
  - Chọn chunk mode theo profile hoặc parser.
  - Tạo `ChunkCreate` và lưu chunk vào PostgreSQL.

- `backend/app/services/ingestion_profiles.py`
  - Khai báo cấu hình mặc định cho từng profile: `legal_admin`, `catalog_table`, `staff_technology_matrix`, `general`, `spreadsheet`, `slide`.
  - Mỗi profile có `chunk_mode`, `chunk_size`, `chunk_overlap`, `top_k`, `candidate_k`, `answer_mode`, `answer_style`, `max_context_chars`.

- `backend/app/services/document_profiles.py`
  - Tự phát hiện profile từ nội dung/file type.
  - Nếu profile là `auto`, hệ thống sẽ tự phân loại sang `general`, `spreadsheet`, `slide`, `catalog_table`, `staff_technology_matrix`, ...

- `backend/app/services/rag_chunk.py`
  - Chuẩn hóa chunk trong DB thành `RagChunk`.
  - Thêm nhiều metadata retrieval như `table_name`, `row_start`, `row_end`, `field_names`, `source_systems`, `identifiers`, `doc_codes`, `dates`, `embedding_text`.
  - Hàm `build_embedding_text()` quyết định nội dung thật sự gửi sang embedding.

Các chunker/chunking chuyên biệt:

- `backend/app/services/table_aware_chunking.py`
  - Nhận diện bảng dạng serialized table, pipe table, aligned text table.
  - Tạo table title/header/block/row chunks.
  - Tạo entity index, entity summary, entity profile chunks.
  - Có xử lý riêng bảng nhân sự/phòng chủ trì/mảng công nghệ.

- `backend/app/services/docling_v6_chunking.py`
  - Chunking theo Docling HybridChunker.
  - Có repair pipeline cho tiếng Việt, bảng, slide, administrative document, technical schema.
  - Có quality report, coverage report, token guard.
  - Dùng document context, page, heading, table serializer, repeat table header.

- `backend/app/services/chunkers/docling_router.py`
  - Router sau Docling.
  - Nếu là legal article thì dùng legal chunker.
  - Nếu phát hiện catalog table hoặc staff relationship thì thêm supplemental semantic records.

- `backend/app/services/chunkers/catalog_table_chunker.py`
  - Chunker chuyên cho bảng danh mục công nghệ/platform/framework.
  - Tạo summary chunk, group chunk, row chunk.

- `backend/app/services/chunkers/table_relationship_chunker.py`
  - Tạo semantic records cho bảng quan hệ nhân sự - mảng công nghệ.

- `backend/app/services/chunkers/legal_article_chunker.py`
  - Chunk theo Điều/Chương.
  - Tạo legal article chunk và legal table row chunk nếu có bảng quyền lợi.

- `backend/app/services/gis_chunking.py`
  - Parser/chunk helper chuyên cho tài liệu GIS/schema.
  - Trích xuất object schema, field rows, attribute tables, relationship schemas.

## 3. Phương pháp chunking trong code riêng của bạn

Đoạn code riêng của bạn có thể hiểu theo các tầng sau.

### 3.1. Đầu vào

Đầu vào là file trong thư mục `data/**/*` với extension:

```text
.pdf, .docx, .xls, .xlsx
```

File được đưa qua:

```python
convert_to_md(file_path)
```

Sau đó hệ thống dùng markdown text làm đầu vào cho chunking:

```python
chunks = adaptive_chunk_markdown(md_text)
```

Điểm đáng chú ý: chunker của bạn không phụ thuộc trực tiếp vào parser element dạng object. Nó xử lý trực tiếp markdown/HTML string.

### 3.2. Recursive text chunking

Text thường dùng:

```python
RecursiveCharacterTextSplitter(
    chunk_size=1500,
    chunk_overlap=300,
    separators=["\n\n", "\n", ". ", "; ", ": ", ", ", " ", ""]
)
```

Ưu điểm:

- Dễ hiểu, dễ chạy.
- Có overlap khá lớn nên ít mất ngữ cảnh giữa các đoạn văn.
- Separator khá nhiều cấp, giúp tránh cắt ngang câu quá thô.

Nhược điểm:

- Không có token guard theo embedding model.
- `chunk_size=1500` là ký tự, không phải token.
- Với bảng hoặc văn bản có cấu trúc, recursive splitter vẫn có thể cắt sai nếu không được tách bảng trước.

### 3.3. Nhận diện bảng

Code riêng nhận diện hai loại bảng chính:

- HTML table:

```text
<table> ... </table>
```

- Markdown table:

```text
| cột 1 | cột 2 |
| --- | --- |
```

Hàm trung tâm:

```python
adaptive_chunk_markdown(md_text)
```

Cách hoạt động:

```text
Duyệt từng dòng markdown
-> nếu gặp HTML table start thì flush text trước đó
-> gom toàn bộ table vào buffer
-> khi gặp table end thì flush table
-> nếu gặp markdown table thì cũng flush text và gom table riêng
-> cuối cùng flush phần còn lại
```

Ưu điểm:

- Không để bảng bị recursive splitter cắt lẫn với văn bản.
- Giữ được ngữ cảnh trước bảng bằng `extract_table_context`.
- Làm tốt với tài liệu mà parser xuất bảng thành HTML.

Nhược điểm:

- Phụ thuộc pattern `<table>` và markdown table line.
- Nếu bảng HTML không đóng chuẩn, hoặc parser tạo HTML một dòng quá dài, có thể khó kiểm soát.
- Markdown table được phát hiện bằng dấu `|`, nhưng một số bảng từ PDF có thể là aligned text không có `|`.

### 3.4. Parse HTML table

Bạn có `TableHTMLParser` tự viết bằng `HTMLParser`.

Nó xử lý:

- `thead`, `tbody`
- `tr`
- `td`, `th`
- `br`
- `colspan`
- `rowspan`
- clean text trong cell

Sau đó có hàm `_expand_table_spans()` để bung rowspan/colspan, giúp mỗi dòng có đủ cell hơn.

Đây là điểm mạnh rất đáng chú ý.

Ưu điểm:

- Xử lý bảng HTML tốt hơn regex đơn giản.
- Có khả năng bảo toàn cấu trúc hàng/cột.
- Có thể tạo `row_data` theo header.

Nhược điểm:

- Chỉ xử lý tốt khi HTML table tương đối chuẩn.
- Nếu parser xuất markdown table hoặc text table thì HTML parser không giúp được.
- Logic này hiện nằm trong một script ingest lớn, chưa tách thành module dùng lại/test độc lập.

### 3.5. Multi-granularity table chunking

Với bảng, code riêng tạo nhiều loại chunk:

```text
table_parent / overview chunk
table_group chunk
table_row chunk
```

Các chunk bảng có metadata:

```text
table_id
table_title
table_headers
table_context
row_start
row_end
row_text
row_data
structured_rows
section_title
section_path
group_label
row_code
authors
years
```

Đây là phần rất tốt cho RAG, vì một câu hỏi có thể cần nhiều mức ngữ cảnh khác nhau:

- Hỏi tổng quan bảng: dùng parent/overview chunk.
- Hỏi một nhóm dòng: dùng table_group chunk.
- Hỏi chi tiết một dòng: dùng table_row chunk.
- Hỏi theo người/năm/mã dòng: dùng metadata và entities.

Ưu điểm:

- Giảm rủi ro retrieval chỉ lấy một dòng thiếu header.
- Hỗ trợ câu hỏi tổng hợp tốt hơn row-only chunking.
- Metadata giàu giúp hybrid search/filter/boost.

Nhược điểm:

- Số chunk tăng nhiều.
- Nếu parent/group/row cùng chứa thông tin trùng nhau, retrieval có thể lấy nhiều chunk lặp.
- Chưa thấy quality gate để kiểm tra chunk quá dài/quá ngắn/token vượt ngưỡng.

### 3.6. Metadata phục vụ search

Code riêng tạo nhiều trường normalized:

```text
content_norm
row_text_norm
table_headers_norm
table_title_norm
table_context_norm
section_title_norm
section_path_norm
entities
entities_norm
authors_norm
author_aliases_norm
years
tags
```

Ưu điểm:

- Rất tốt cho Elasticsearch keyword search.
- Tìm theo tên người, alias, năm, mã dòng, tiêu đề bảng dễ hơn.
- Có lợi cho tiếng Việt vì có normalize bỏ dấu.

Nhược điểm:

- Metadata rất nhiều, có thể phình index.
- Một số logic domain-specific như author extraction không phải tài liệu nào cũng cần.
- Nếu chuyển sang HBRag, cần map vào schema metadata chung, tránh tạo quá nhiều field không được retrieval dùng đến.

## 4. Phương pháp chunking hiện tại của HBRag

### 4.1. RecursiveTextChunker

File:

```text
backend/app/services/chunking_service.py
```

Cách làm:

```text
parsed_text
-> split_tables_and_text
-> nếu là GIS schema table thì giữ nguyên thành gis_table chunk
-> text thường thì cắt theo chunk_size/chunk_overlap
```

Thông số:

```text
general: chunk_size=1000, chunk_overlap=150
fallback: chunk_size=1000, chunk_overlap=150
```

Ưu điểm:

- Đơn giản, ổn định.
- Có start/end char.
- Có split boundary tránh cắt quá sớm, dùng `MIN_SPLIT_RATIO = 0.85`.
- Có xử lý riêng GIS schema table bằng regex.

Nhược điểm:

- Recursive thuần chưa hiểu bảng HTML/markdown nếu parser không tạo parsed_elements.
- Với tài liệu bảng phức tạp, dễ tạo chunk quá tổng quát hoặc mất header.
- GIS table regex có tính domain-specific.

### 4.2. LegalArticleChunker

File:

```text
backend/app/services/chunking_service.py
backend/app/services/chunkers/legal_article_chunker.py
```

Cách làm:

```text
detect heading rules
-> chia theo Điều/Chương
-> nếu điều quá dài thì split tiếp
-> giữ metadata article_number, article_title, chapter_title
```

Ưu điểm:

- Rất phù hợp văn bản pháp lý/hành chính có Điều/Chương.
- Retrieval theo Điều chính xác hơn recursive.
- Có sibling/part metadata giúp gom lại các phần của một điều.

Nhược điểm:

- Không phù hợp tài liệu không có cấu trúc Điều/Chương.
- Phụ thuộc heading rules.
- Không giải quyết triệt để bảng phức tạp nếu bảng không được parse riêng.

### 4.3. Hybrid structured / table-aware

File:

```text
backend/app/services/chunking_service.py
backend/app/services/table_aware_chunking.py
```

Cách làm trong `ChunkingService`:

```text
parsed_elements có table/table_row
-> mode tự chuyển thành hybrid_structured
-> prose chunks từ title/heading/paragraph/page
-> table chunks từ parsed table/table_row elements
-> entity profile chunks từ table rows
```

Trong `table_aware_chunking.py`, hệ thống có thể:

- Detect serialized table.
- Detect pipe table.
- Detect aligned text table.
- Tạo table title chunk.
- Tạo table header chunk.
- Tạo table block chunk.
- Tạo table row chunk.
- Tạo entity summary/profile chunks.
- Có logic riêng cho bảng nhân sự - mảng công nghệ.

Ưu điểm:

- Kiến trúc tốt hơn script riêng vì tách module rõ.
- Tích hợp với parsed_elements từ parser.
- Có nhiều mức chunk: title/header/block/row/entity.
- Metadata được đưa vào `RagChunk` và retrieval.
- Có no-overlap cho row/structured chunk.

Nhược điểm:

- Với HTML table thô trong `parsed_text` nhưng không có `parsed_elements`, khả năng parse bảng không mạnh bằng code riêng của bạn.
- Table parent/group chunk tổng quan chưa nhất quán cho mọi loại bảng.
- Metadata `structured_rows`, `row_data`, `row_code`, `table_context` chưa được chuẩn hóa rộng như code riêng.

### 4.4. Docling v6 / Docling router

File:

```text
backend/app/services/docling_v6_chunking.py
backend/app/services/chunkers/docling_router.py
```

Cách làm:

```text
DoclingDocument
-> HybridChunker
-> contextualize chunk
-> repair_records
-> enforce token limit
-> quality report / coverage report
-> router bổ sung legal/catalog/staff semantic records nếu phù hợp
```

Thông số nổi bật:

```text
DEFAULT_MAX_TOKENS = 350
DEFAULT_CONTEXT_BUDGET = 80
repeat_table_header=True
merge_peers=True
```

Ưu điểm:

- Hiểu layout tốt hơn text-only.
- Có page/headings/doc item types.
- Có token guard, quality gate, coverage report.
- Có repair cho tiếng Việt, slide, administrative/table/schema.
- Dễ debug bằng artifact chunks/quality/coverage.

Nhược điểm:

- Phức tạp, khó sửa nếu chưa quen.
- Phụ thuộc chất lượng Docling output.
- Một số logic domain-specific đang nằm trong file lớn.
- Nếu tài liệu không đi qua Docling hoặc không có artifact, không dùng được nhánh này.

### 4.5. GIS chunking

File:

```text
backend/app/services/gis_chunking.py
```

Cách làm:

- Parse procedure rows.
- Parse schema objects.
- Parse schema field rows.
- Parse attribute tables.
- Parse relationship schemas.

Ưu điểm:

- Rất đúng domain GIS/schema.
- Có metadata field-level như `field_name`, `data_type`, `source_data`, `convert_to_gis`.
- Phù hợp câu hỏi kiểu "lớp này có bao nhiêu trường", "trường X kiểu dữ liệu gì", "nguồn dữ liệu từ đâu".

Nhược điểm:

- Domain-specific.
- Cần kết nối rõ hơn với chunking pipeline chính để mọi tài liệu GIS đều sinh row/object/summary chunks nhất quán.

## 5. So sánh ưu điểm và nhược điểm

### 5.1. Code riêng của bạn

Ưu điểm:

- Dễ hiểu từ đầu đến cuối vì toàn bộ ingest nằm trong một flow.
- Xử lý bảng HTML tốt, có parser riêng cho `thead/tbody/tr/td/th`.
- Có xử lý rowspan/colspan, đây là điểm HBRag chưa thấy có ở tầng table-aware tổng quát.
- Tạo chunk bảng nhiều tầng: overview/parent, group rows, row chunks.
- Metadata rất giàu cho keyword/hybrid search.
- Có normalize bỏ dấu và build entities, phù hợp Elasticsearch.
- Có table_context từ đoạn text trước bảng, giúp row/group chunk không bị rơi khỏi ngữ cảnh.

Nhược điểm:

- Script lớn, nhiều trách nhiệm trong một file: đọc file, detect type, chunk, embed, index.
- Khó test từng phần vì logic chưa tách module rõ.
- Không có token guard theo model embedding/LLM.
- Không có quality report/coverage report.
- Không có chuẩn metadata thống nhất như `RagChunk`.
- Dễ tạo nhiều chunk trùng lặp giữa parent/group/row nếu không có dedup hoặc retrieval policy.
- Tối ưu nhiều cho Elasticsearch, trong khi HBRag dùng PostgreSQL + Qdrant + rerank.
- Metadata như authors/years/tags tốt nhưng không phải tài liệu nào cũng cần, nếu đưa hết vào HBRag có thể làm index rối.

### 5.2. HBRag hiện tại

Ưu điểm:

- Có kiến trúc production hơn: parser -> chunking -> DB chunks -> vector index -> retrieval.
- Có profile-based routing.
- Có nhiều chunk mode phù hợp nhiều loại tài liệu.
- Tích hợp Docling v6, quality gate, token guard, coverage artifact.
- Metadata chunk được chuẩn hóa qua `RagChunk`.
- Có access control, citation, document status, pipeline log.
- Có các chunker chuyên biệt cho legal, catalog table, staff matrix, GIS.
- Có cơ chế bổ sung metadata vào embedding text, giúp retrieval tốt hơn dense-only.

Nhược điểm:

- Table chunking tổng quát còn phân tán nhiều nơi.
- Nếu dữ liệu đầu vào là HTML table thô trong markdown/text, chưa có một HTML table parser tổng quát mạnh như code riêng.
- Multi-granularity table chunking chưa thống nhất cho mọi bảng: có bảng thì sinh row/header/block, có bảng chuyên biệt thì sinh summary/group/row, có bảng khác lại chỉ là table_block.
- Một số logic rất domain-specific nằm trong file lớn như `docling_v6_chunking.py`, khó bảo trì.
- Với câu hỏi tổng hợp bảng, nếu retrieval chỉ lấy row chunks mà thiếu summary/group chunk, LLM có thể trả lời thiếu.

## 6. Code riêng tốt hơn HBRag ở điểm nào?

### 6.1. Xử lý HTML table thô

Code riêng tốt hơn ở việc parse HTML table bằng `HTMLParser`, đọc `thead`, `tbody`, `rowspan`, `colspan`.

HBRag hiện có table-aware parser tốt cho serialized/pipe/aligned table và parsed_elements, nhưng nếu DOffice/Markdown đưa nguyên HTML table vào `parsed_text`, HBRag chưa có lớp chuyển HTML table thành structured rows thật sự mạnh ở chunking.

Nên lấy sang:

```text
HTML table parser độc lập
-> convert HTML table thành TableBlock/rows/headers
-> đưa vào table_aware_chunking hoặc parser table_serialization
```

### 6.2. Table context trước bảng

Code riêng có `extract_table_context(buffer)`, lấy đoạn văn bản trước bảng làm ngữ cảnh.

Điểm này rất hữu ích. Ví dụ bảng chỉ ghi field rows, nhưng câu hỏi cần biết bảng đó thuộc lớp GIS nào. Nếu row chunk không có context, retrieval có thể lấy đúng row nhưng LLM không biết row thuộc đối tượng nào.

HBRag có `document_context`, `heading_path`, `section_path`, nhưng với HTML table thô, context trước bảng chưa chắc được gắn vào từng row/group.

Nên lấy sang:

```text
table_context
section_path
nearby_heading
object/layer context
```

### 6.3. Parent-group-row chunking thống nhất

Code riêng có tư duy rõ:

```text
parent/overview chunk
group rows chunk
row chunk
```

HBRag cũng có pattern tương tự trong `catalog_table_chunker.py`, nhưng chưa áp dụng thống nhất cho mọi bảng.

Nên lấy sang:

```text
Mọi bảng quan trọng nên có:
1. table_summary/table_overview chunk
2. table_group chunk theo nhóm dòng hoặc theo token budget
3. table_row chunk cho từng dòng quan trọng
```

### 6.4. Metadata search_entities

Code riêng build `entities`, `entities_norm`, aliases, normalized fields.

HBRag đã có `extract_search_metadata()` trong `rag_chunk.py`, nhưng thiên về document code/date/identifier. Với bảng, HBRag có thể học thêm ý tưởng `search_entities`.

Nên lấy sang có chọn lọc:

```text
table_search_terms
row_search_terms
entity_names
entity_names_normalized
row_code
row_code_normalized
```

Không nên bê toàn bộ `authors/tags/years` cho mọi tài liệu nếu retrieval chưa dùng.

## 7. HBRag tốt hơn code riêng ở điểm nào?

### 7.1. Kiến trúc rõ và dễ tích hợp backend

HBRag có service/repository/schema/model rõ:

```text
Document
Chunk
Pipeline log
Vector indexing
RagChunk
Citation
Access control
```

Code riêng là script ingest độc lập, phù hợp prototype nhưng nếu đưa vào backend production sẽ khó kiểm soát quyền, trạng thái, lỗi, retry, logging.

### 7.2. Profile/router tốt hơn

HBRag có:

```text
auto profile detection
legal_admin
catalog_table
staff_technology_matrix
spreadsheet
slide
general
```

Code riêng cũng có `detect_document_type`, nhưng chủ yếu keyword scoring trong script. HBRag tốt hơn vì profile được dùng xuyên suốt chunking và answering.

### 7.3. Token guard và quality report

Docling v6 của HBRag có:

```text
max_tokens
context_budget
enforce_token_limit
quality report
coverage report
strict quality gate
```

Code riêng chưa có phần này. Đây là điểm rất quan trọng để tránh chunk quá dài làm embedding lỗi hoặc LLM context bị tràn.

### 7.4. Metadata chuẩn hóa đến retrieval

HBRag có `RagChunk` làm lớp chuẩn hóa. Một metadata nếu được thêm đúng vào `RagChunk`, nó có thể đi tiếp đến:

```text
embedding text
Qdrant payload
filter
rerank
citation
answer prompt
```

Code riêng lưu metadata vào Elasticsearch, nhưng metadata đó chưa nằm trong một contract chung cho toàn hệ thống.

## 8. Đề xuất cách làm tốt nhất cho HBRag

Không nên thay toàn bộ chunking của HBRag bằng code riêng. Cách tốt nhất là thêm một lớp "general table semantic chunking" lấy ý tưởng tốt từ code riêng và cắm vào pipeline hiện tại.

### 8.1. Mục tiêu

Mục tiêu tối ưu chunking tổng quát:

```text
1. Không làm hỏng upload/parse/chunk hiện tại.
2. Bảng HTML/markdown/text đều được chuẩn hóa thành cấu trúc rows/headers.
3. Mỗi bảng có đủ summary/group/row chunks.
4. Row chunk luôn có context: title, heading, table_context, headers.
5. Metadata bảng được chuẩn hóa vào RagChunk/Qdrant payload.
6. Có token guard và quality checks.
```

### 8.2. Đề xuất kiến trúc mới

Thêm module mới:

```text
backend/app/services/table_semantic_chunking.py
```

Module này nên cung cấp các hàm:

```python
detect_table_blocks(text, parsed_elements=None) -> list[TableBlock]
parse_html_table(html) -> TableBlock
parse_markdown_table(markdown) -> TableBlock
table_to_semantic_chunks(table, table_context, chunk_size, max_tokens) -> list[dict]
```

Không nên để logic này trong `ChunkingService` trực tiếp, vì `ChunkingService` đã đủ lớn.

### 8.3. Luồng xử lý đề xuất

Pipeline đề xuất:

```text
parsed_text + parsed_elements
-> detect profile
-> nếu có Docling artifact: chạy docling_router/docling_v6 như hiện tại
-> nếu không có Docling artifact:
   -> detect structured tables từ parsed_elements
   -> detect HTML/markdown tables từ parsed_text
   -> normalize tất cả thành TableBlock chung
   -> sinh semantic table chunks:
      - table_overview
      - table_group
      - table_row
   -> prose text bỏ vùng bảng đã detect
   -> recursive/heading chunk phần text còn lại
-> chuẩn hóa qua RagChunk
-> embedding/index
```

### 8.4. Chuẩn chunk bảng nên có

Mỗi bảng nên có 3 tầng chunk.

#### Tầng 1: table_overview

Dùng cho câu hỏi tổng quan:

```text
Bảng gì?
Thuộc mục nào?
Có bao nhiêu dòng?
Có những cột nào?
Phạm vi dữ liệu là gì?
```

Nội dung mẫu:

```text
Bảng: F08_CotDien_HT - Lớp cột điện
Ngữ cảnh: Chi tiết dữ liệu chuyển đổi sang GIS hạ thế
Cột: TT, Tên trường, Mô tả, Kiểu dữ liệu, Miền giá trị, Độ rộng, Nguồn dữ liệu, Chuyển đổi sang GIS
Số dòng: 30
Phạm vi dòng: 1-30
```

Metadata:

```text
chunk_type=table_overview
table_id
table_name
table_title
table_context
table_columns
row_start
row_end
row_count
section_path
page_range
```

#### Tầng 2: table_group

Dùng cho câu hỏi cần liệt kê nhiều dòng nhưng không cần từng row riêng lẻ.

Group theo:

- token budget;
- hoặc nhóm logic;
- hoặc range dòng, ví dụ 1-10, 11-20, 21-30.

Nội dung mẫu:

```text
Bảng: F08_CotDien_HT - Lớp cột điện
Cột: ...
Phạm vi dòng: 1-10
Dữ liệu:
1. ID: ...
2. IdPMIS: ...
...
```

Metadata:

```text
chunk_type=table_group
table_id
table_name
row_start
row_end
structured_rows
table_columns
table_context
```

#### Tầng 3: table_row

Dùng cho câu hỏi chi tiết một trường/một dòng.

Nội dung mẫu:

```text
Bảng: F08_CotDien_HT - Lớp cột điện
Dòng 4
Tên trường: ChieuCaoCot
Mô tả: Chiều cao cột
Kiểu dữ liệu: Short Integer
Nguồn dữ liệu: PMIS/Biên tập
Chuyển đổi sang GIS: Có
```

Metadata:

```text
chunk_type=table_row
table_id
row_index
row_start
row_end
row_data
field_name
field_names
data_type
source_data
convert_to_gis
table_context
```

### 8.5. Nên lấy gì từ code riêng sang HBRag?

Nên lấy:

1. HTML table parser:

```text
TableHTMLParser
_expand_table_spans
parse_html_table
build_row_data
```

Nhưng cần tách thành module nhỏ và viết test.

2. Table context:

```text
extract_table_context(buffer)
infer_table_title(...)
infer_text_section_metadata(...)
```

Đưa vào HBRag dưới dạng `table_context`, `section_path`, `heading_path`.

3. Multi-granularity chunks:

```text
build_table_parent_chunks
split_html_table_by_rows
build_table_row_chunks
```

Đổi tên và chuẩn hóa thành:

```text
table_overview
table_group
table_row
```

4. Structured metadata:

```text
row_data
structured_rows
row_code
row_code_prefix
row_code_suffix
row_code_number
```

Map sang `RagChunk` nếu thật sự retrieval cần.

5. Normalized search terms:

```text
entities_norm
table_headers_norm
row_text_norm
```

Không nhất thiết lưu tất cả, nhưng nên dùng để build `identifiers`, `field_names`, `source_systems`, `table_columns`, `entity`.

### 8.6. Không nên lấy nguyên gì?

Không nên bê nguyên:

- Toàn bộ script ingest monolithic.
- Logic Elasticsearch indexing.
- Logic detect document type bằng keyword cho mọi tài liệu nếu HBRag đã có profile system.
- Mọi metadata như `authors`, `author_aliases`, `years`, `tags` cho tất cả tài liệu.
- Embedding trực tiếp trong ingest script.

Lý do:

```text
HBRag đã có pipeline backend riêng:
Document -> Chunk -> VectorIndexingService -> Qdrant.
Nếu đưa nguyên script vào sẽ song song hai pipeline, khó debug và dễ lệch dữ liệu.
```

## 9. Roadmap triển khai đề xuất

### Giai đoạn 1: Chuẩn hóa bảng HTML/Markdown thành TableBlock

Thêm file:

```text
backend/app/services/table_semantic_chunking.py
```

Nội dung:

- `TableBlock`
- `TableRow`
- `parse_html_table`
- `parse_markdown_table`
- `detect_html_table_blocks`
- `detect_markdown_table_blocks`

Test nên có:

- HTML table thường.
- HTML table có `<thead>/<tbody>`.
- HTML table có `<br>`.
- HTML table có rowspan/colspan.
- Markdown table.
- Table có cell rỗng.

### Giai đoạn 2: Sinh table_overview/table_group/table_row chunks

Thêm hàm:

```python
table_to_semantic_chunks(table, table_context, chunk_size, max_tokens)
```

Output là list dict tương thích HBRag:

```python
{
    "content": "...",
    "metadata": {
        "chunk_type": "table_row",
        "table_id": "...",
        ...
    }
}
```

### Giai đoạn 3: Cắm vào `ChunkingService._chunks_from_hybrid_elements`

Hiện tại:

```text
prose_chunks
table_chunks từ parsed_elements
entity_profile_chunks
```

Đề xuất:

```text
prose_chunks
table_chunks từ parsed_elements
html/markdown table semantic chunks từ parsed_text nếu chưa có trong parsed_elements
entity_profile_chunks
```

Điều quan trọng: phải tránh duplicate. Nếu parser đã sinh `table_row` đầy đủ thì không parse lại HTML table cùng vùng text.

### Giai đoạn 4: Mở rộng `RagChunk`

Nếu thêm metadata mới, nên bổ sung có chọn lọc vào `RagChunk`:

```text
row_data
structured_rows
table_context
row_code
row_code_normalized
field_name
data_type
source_data
convert_to_gis
```

Nhưng không nên thêm quá nhiều nếu chưa dùng trong retrieval.

### Giai đoạn 5: Tối ưu embedding text

Trong `build_embedding_text()` nên thêm context cho table chunks:

```text
Tài liệu: ...
Mục: ...
Bảng: ...
Ngữ cảnh bảng: ...
Cột bảng: ...
Dòng: ...
Nội dung dòng: ...
```

Mục tiêu: row chunk khi embedding không bị mất nghĩa.

### Giai đoạn 6: Đánh giá retrieval

Tạo bộ câu hỏi test:

- Hỏi tổng số lớp/bảng.
- Hỏi một lớp có bao nhiêu trường.
- Hỏi trường X thuộc bảng nào.
- Hỏi kiểu dữ liệu của trường X.
- Hỏi nguồn dữ liệu của trường X.
- Hỏi các trường chuyển đổi sang GIS.
- Hỏi quan hệ giữa bảng A và bảng B.

So sánh trước/sau:

```text
Recall@k
MRR
Số citation đúng
Tỷ lệ câu trả lời đủ ý
Số chunk bị trùng/lặp trong context
```

## 10. Kết luận

Code riêng của bạn không nên thay thế toàn bộ chunking hiện tại, nhưng có nhiều ý tưởng rất đáng đưa vào HBRag:

- Parse HTML table thật sự.
- Bung rowspan/colspan.
- Gắn context trước bảng vào row/group chunks.
- Tạo multi-granularity chunks thống nhất: overview/group/row.
- Lưu structured rows/row_data để retrieval và answer chính xác hơn.
- Tạo search terms normalized cho bảng.

HBRag nên giữ:

- `ChunkingService` làm trung tâm điều phối.
- Profile system.
- Docling v6/router.
- `RagChunk` làm contract metadata.
- Token guard và quality report.
- PostgreSQL/Qdrant pipeline hiện tại.

Cách tốt nhất là xây một module trung gian:

```text
table_semantic_chunking.py
```

Sau đó cắm module này vào nhánh `hybrid_structured/table_aware/docling_router` thay vì viết một pipeline ingest mới. Như vậy hệ thống hiện tại vẫn ổn định, nhưng khả năng xử lý bảng, đặc biệt bảng HTML/markdown từ DOffice/PDF/Word, sẽ mạnh hơn rõ rệt.

