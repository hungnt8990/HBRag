# Thay đổi retrieval: LLM semantic router chính thức

Tài liệu này mô tả bản nâng cấp retrieval mới của HBRag. Mục tiêu là lấy ý tưởng tốt từ dự án `rag_research`: trước khi search chunk, hệ thống dùng LLM router để hiểu câu hỏi đang hỏi gì, hỏi tài liệu nào, hỏi phần nào, cần loại chunk nào.

Điểm quan trọng: bản này không dùng regex/rule-based để tự hiểu document scope nữa. LLM router sẽ trả JSON có cấu trúc, pipeline chỉ validate JSON và dùng các trường đó để retrieval.

## 1. Vì sao cần LLM router

Trước đây hệ thống dùng rule/regex để đoán:

- câu hỏi có hỏi mã văn bản không;
- có phải hỏi bảng không;
- có phải hỏi số lượng/danh sách không;
- có nên khóa vào một document cụ thể không.

Cách này chạy nhanh nhưng dễ thiếu trường hợp, vì văn bản DOFFICE có nhiều kiểu hỏi khác nhau:

- `văn bản 3665 được ban hành bởi ai?`
- `mục tiêu của phụ lục 02 là gì?`
- `CPCIT cần làm gì trong phụ lục 01?`
- `cột Giá trúng thầu có nội dung gì?`

LLM router xử lý tốt hơn vì nó đọc cả câu hỏi và trả về ý định truy xuất theo schema.

## 2. Pipeline retrieval mới

Luồng hiện tại sau thay đổi:

```text
Người dùng hỏi
-> rewrite câu hỏi nếu cần
-> LLM semantic router trả JSON route
-> tạo query strategy từ route của LLM
-> Document-first scope từ route của LLM
-> nếu route khóa được document: search trong document đó
-> hybrid search vector + keyword
-> reranker chọn top_k chunk
-> load đúng top_k chunk từ PostgreSQL
-> build prompt
-> LLM trả lời
-> ghi log semantic_route, document_scope, rerank, context
```

Vì bạn đang muốn baseline chỉ dùng context từ reranker, các bước làm giàu context vẫn có thể tắt bằng `.env`:

```env
ENABLE_CONTEXT_EXPANSION=false
ENABLE_CONTEXT_AUGMENTATION=false
ENABLE_ARTIFACT_FIRST_RETRIEVAL=false
ENABLE_NEIGHBOR_EXPANSION=false
ENABLE_KNOWLEDGE_ARTIFACT_COMPILATION=false
MEMORY_ENABLED=false
MEMORY_AUTO_SAVE=false
MEMORY_INJECT_INTO_PROMPT=false
```

Như vậy context cuối chủ yếu là các chunk do reranker kéo về.

## 3. LLM router trả về gì

Service mới:

```text
backend/app/services/llm_query_router.py
```

LLM router trả về `SemanticRoute`, gồm các thông tin chính:

```text
intent
question_scope
answer_need
document_reference
document_identifiers
id_vb_values
document_codes
document_titles
primary_entities
lookup_entities
constraints
requested_fields
preferred_chunk_types
requires_table_expansion
requires_section_expansion
confidence
reason
```

Ví dụ câu hỏi:

```text
Mục tiêu của Phụ lục 02 trong văn bản 6515 là gì?
```

Route mong muốn:

```json
{
  "intent": "question_answer",
  "question_scope": "section_level",
  "answer_need": "direct_answer",
  "document_reference": "explicit_document",
  "document_codes": ["6515"],
  "constraints": [
    {"type": "appendix", "value": "Phụ lục 02"},
    {"type": "section", "value": "Mục tiêu"}
  ],
  "preferred_chunk_types": ["document_body", "document_summary"],
  "requires_table_expansion": false,
  "requires_section_expansion": true
}
```

Ví dụ câu hỏi:

```text
Ai là người ký văn bản 3665?
```

Route mong muốn:

```json
{
  "intent": "question_answer",
  "question_scope": "document_level",
  "answer_need": "direct_answer",
  "document_reference": "explicit_document",
  "document_codes": ["3665"],
  "requested_fields": ["signer"],
  "preferred_chunk_types": ["document_header", "footer_signature"]
}
```

## 4. Document-first scope hoạt động thế nào

File:

```text
backend/app/services/document_scope_service.py
```

Trước đây file này tự dùng regex/rule để đọc câu hỏi. Bản mới dùng method:

```text
resolve_from_semantic_route(...)
```

Nghĩa là:

```text
LLM router đọc câu hỏi
-> trả document_codes/id_vb/document_titles
-> DocumentScopeService dùng các giá trị đó để tìm document trong PostgreSQL
-> nếu chỉ có một document khớp chắc: mode = hard
-> nếu nhiều document hoặc chưa đủ chắc: mode = soft/none
```

Khi `mode = hard`, retrieval sẽ chỉ chạy trong document đó. Điều này giúp giảm nhiễu khi nhiều chunk cùng chứa mã văn bản trong preamble.

## 5. Query strategy bây giờ lấy từ LLM route

Trước đây:

```text
evidence_query -> classify_query_strategy bằng rule/regex
```

Bây giờ:

```text
evidence_query -> LLM semantic route -> query_strategy_from_semantic_route
```

`QueryStrategy` vẫn còn vì các hàm cũ trong `RagAnswerService` cần object này để tương thích. Nhưng nội dung strategy được map từ JSON của LLM, không tự phân tích câu hỏi bằng regex nữa.

Ví dụ:

- `answer_need = count` -> strategy có `count_list`;
- `question_scope = table_level` -> strategy có `table_detail`;
- `answer_need = summarize` -> strategy có `overview_summary`;
- nếu không rõ -> `semantic_search`.

## 6. Log mới cần xem

File log:

```text
log/rag_chat_logs.md
```

Phần `Query/Retrieval Query` bây giờ có thêm:

```text
semantic_route
document_scope
query_strategy
```

Khi debug, nên nhìn theo thứ tự:

```text
1. semantic_route: LLM hiểu câu hỏi đúng chưa?
2. document_scope: có khóa đúng document không?
3. reranked_results: reranker kéo chunk nào?
4. context.final_context_count: cuối cùng đưa bao nhiêu chunk vào LLM?
```

Nếu `semantic_route.document_codes` trống khi câu hỏi có mã văn bản, lỗi nằm ở router prompt hoặc LLM output.

Nếu `document_codes` có nhưng `document_scope.mode = none`, lỗi có thể nằm ở metadata document hoặc document chưa được index/lưu đúng code.

## 7. Các file đã thay đổi

### `backend/app/services/llm_query_router.py`

File mới.

Vai trò:

- gọi LLM để route câu hỏi;
- yêu cầu LLM trả JSON;
- parse JSON;
- chuẩn hóa thành `SemanticRoute`;
- tạo `QueryStrategy` tương thích từ route của LLM.

### `backend/app/services/document_scope_service.py`

Thêm method:

```text
resolve_from_semantic_route(...)
```

Method này không tự regex câu hỏi. Nó chỉ dùng các trường mà LLM router đã trả về.

### `backend/app/services/rag_answer_service.py`

Thay đổi chính:

- sau rewrite, gọi LLM router;
- tạo query strategy từ LLM route;
- resolve document scope từ LLM route;
- log route vào message access log;
- áp dụng cho cả chat thường và stream.

### `backend/app/services/rag_interaction_logger.py`

Log thêm:

```text
semantic_route
```

để dễ kiểm tra router.

## 8. Cách kiểm tra nhanh

Sau khi restart backend, hỏi thử:

```text
văn bản 3665 được ban hành bởi ai?
văn bản 3665 nói gì?
mục tiêu của phụ lục 02 là gì?
CPCIT cần làm gì trong phụ lục 01?
cột Giá trúng thầu trong văn bản 660/QĐ-IT là gì?
```

Sau đó mở:

```text
log/rag_chat_logs.md
```

Kiểm tra:

```text
semantic_route.document_reference
semantic_route.document_codes
semantic_route.constraints
semantic_route.preferred_chunk_types
document_scope.mode
context.final_context_count
```

Với câu hỏi có mã văn bản rõ, mong muốn:

```text
document_reference = explicit_document
document_codes có mã văn bản
document_scope.mode = hard nếu DB chỉ khớp một văn bản
```

## 9. Lưu ý

Bản này đã chuyển phần hiểu retrieval sang LLM router, nhưng chưa bê toàn bộ heuristic retrieval của `rag_research`.

Hiện tại vẫn ưu tiên an toàn:

- không dùng memory dài hạn;
- không bật context augmentation nếu `.env` đang tắt;
- không tự mở rộng neighbor nếu `.env` đang tắt;
- document scope chỉ hard lock khi DB match đủ rõ.

Các bước có thể làm tiếp:

```text
1. Cho hybrid/reranker nhận semantic_route để boost chunk_type theo preferred_chunk_types.
2. Thêm soft document scope boost thay vì chỉ hard lock.
3. Thêm entity/constraint-aware search trong document scope.
4. Thêm benchmark retrieval cố định để đo trước/sau.
```

## 10. Tóm tắt

Trước đây:

```text
câu hỏi -> rule/regex strategy + document scope -> retrieval
```

Bây giờ:

```text
câu hỏi -> LLM semantic route -> document scope + strategy -> retrieval
```

Nói ngắn gọn: LLM router là lớp hiểu câu hỏi chính. Retrieval không tự regex câu hỏi để quyết định document scope nữa.
