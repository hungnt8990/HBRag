# Log hỏi đáp RAG

Chức năng log hiện tại ghi mỗi lượt hỏi đáp ra 2 file:

```text
log/rag_chat_logs.jsonl
log/rag_chat_logs.md
```

- `rag_chat_logs.jsonl`: mỗi dòng là một JSON độc lập. File này phù hợp nếu muốn dùng Python, notebook hoặc tool khác để phân tích tự động.
- `rag_chat_logs.md`: file Markdown dễ đọc hơn. File này phù hợp để mở trực tiếp và xem câu hỏi, câu trả lời, retrieval lấy gì, context nào được đưa vào LLM, citation nào được dùng.

Nếu bạn hỏi trên giao diện bằng stream, hệ thống cũng sẽ ghi log vào cả 2 file này sau khi câu trả lời hoàn tất.

Log này chỉ phục vụ debug và đánh giá chất lượng RAG. Nếu ghi log lỗi, hệ thống chat vẫn tiếp tục chạy bình thường.

## 1. Thông tin phiên hỏi đáp

- `timestamp`: thời điểm ghi log.
- `session_id`: phiên chat.
- `user_message_id`: id tin nhắn người dùng.
- `assistant_message_id`: id tin nhắn chatbot.
- `user_id`: người hỏi, nếu có đăng nhập.
- `organization_id`: đơn vị của người hỏi.
- `document_ids`: danh sách tài liệu đang được phép truy vấn trong lượt hỏi.
- `question`: câu hỏi gốc của người dùng.
- `answer`: câu trả lời cuối cùng của chatbot.
- `answer_status`: trạng thái trả lời, ví dụ `answered` hoặc `direct_answer`.
- `error_message`: thông tin lỗi nếu có.

## 2. Thông tin xử lý câu hỏi

Nằm trong trường `query`.

- `retrieval_query`: câu hỏi dùng để đi retrieval sau bước rewrite/enrich.
- `evidence_query`: câu hỏi dùng để kiểm tra bằng chứng và sinh prompt.
- `query_strategy`: chiến lược truy vấn mà hệ thống phân loại được.
- `query_contract`: ý định truy vấn mà hệ thống phát hiện được.
- `rewrite_used`: câu hỏi có được rewrite hay không.
- `rewrite_reason`: lý do rewrite nếu có.

Nhóm này giúp kiểm tra câu hỏi có bị rewrite sai hoặc phân loại sai ý định hay không.

## 3. Thông số cấu hình lúc hỏi

Nằm trong trường `settings`.

- `top_k`: số chunk cuối mong muốn theo request hoặc profile.
- `candidate_k`: số ứng viên retrieval ban đầu.
- `effective_top_k`: top_k thực tế sau khi hệ thống tự điều chỉnh theo loại câu hỏi.
- `effective_candidate_k`: candidate_k thực tế sau khi hệ thống tự điều chỉnh.
- `max_context_chars`: giới hạn context theo request/profile.
- `effective_max_context_chars`: giới hạn context thực tế sau khi tự điều chỉnh.
- `answer_mode`: chế độ trả lời.
- `answer_style`: phong cách trả lời.
- `llm_model`: model sinh câu trả lời.
- `embedding_model`: model embedding.
- `reranker_model`: model rerank.

Nhóm này dùng để biết cùng một câu hỏi đang chạy với cấu hình nào.

## 4. Thông tin retrieval/rerank

Nằm trong trường `retrieval`.

- `rerank_query`: query đưa vào retrieval/rerank.
- `rerank_top_k`: top_k trong response rerank.
- `rerank_candidate_k`: candidate_k trong response rerank.
- `selected_artifact_count`: số artifact được chọn nếu có.
- `used_chunk_fallback`: có fallback sang chunk thường hay không.
- `reranked_results`: danh sách chunk sau retrieval/rerank.

Mỗi item trong `reranked_results` có các trường quan trọng:

- `chunk_id`: id chunk.
- `document_id`: id document.
- `rerank_score`: điểm rerank.
- `fused_score`: điểm sau khi merge/hybrid.
- `vector_score`: điểm vector search nếu có.
- `keyword_score`: điểm keyword/BM25 nếu có.
- `source_flags`: chunk đến từ vector, keyword, lexical exact, graph...
- `chunk_type`: loại chunk như `document_body`, `table_row`, `table_column`.
- `section_title`: mục hiện tại.
- `heading_path`: đường dẫn mục cha - mục con.
- `table_name`: tên bảng nếu là chunk bảng.
- `row_number`: số dòng nếu là table row.
- `column_name`: tên cột nếu là table column.
- `content_preview`: đoạn xem nhanh nội dung chunk.

Nhóm này giúp xem retrieval ban đầu lấy đúng hay sai.

## 5. Context thật sự đưa vào LLM

Nằm trong trường `context`.

- `final_context_count`: số chunk cuối cùng được đưa vào LLM.
- `context_char_count`: tổng số ký tự context.
- `context_approx_token_count`: ước lượng số token context.
- `chunks`: danh sách chunk thật sự được đưa vào prompt.

Mỗi item trong `chunks` gồm:

- `citation_index`: số citation trong prompt.
- `chunk_id`: id chunk.
- `document_id`: id document.
- `document_title`: tên tài liệu.
- `chunk_index`: thứ tự chunk trong tài liệu.
- `chunk_type`: loại chunk.
- `source_type`: nguồn chính của chunk.
- `source_flags`: vector/keyword/neighbor/artifact...
- `section_title`: mục hiện tại.
- `heading_path`: đường dẫn mục cha - mục con.
- `table_name`: tên bảng.
- `row_number`: số dòng bảng.
- `column_name`: tên cột bảng.
- `field_name`: tên trường nếu có.
- `metadata`: metadata rút gọn quan trọng.
- `content_length`: độ dài nội dung chunk.
- `content_preview`: đoạn xem nhanh nội dung chunk.

Đây là nhóm quan trọng nhất để kiểm tra: LLM đã thật sự được đọc những chunk nào.

## 6. Citation

Nằm trong trường `citations`.

- `citation_index`: số citation.
- `chunk_id`: chunk được trích dẫn.
- `document_id`: tài liệu được trích dẫn.
- `document_title`: tên tài liệu.
- `chunk_index`: thứ tự chunk.
- `source_flags`: nguồn retrieval.
- `chunk_type`: loại chunk.
- `section_title`: mục hiện tại.
- `heading_path`: đường dẫn mục.
- `table_name`: tên bảng.
- `row_number`: số dòng.
- `column_name`: tên cột.
- `quote`: đoạn trích ngắn.

Nhóm này giúp đối chiếu câu trả lời có dựa vào nguồn đúng không.

## 7. Thời gian

- `latency_ms`: tổng thời gian xử lý lượt hỏi đáp, tính bằng mili giây.

## 8. Cách đọc nhanh bằng Python

```python
import json
from pathlib import Path

path = Path("log/rag_chat_logs.jsonl")
records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

last = records[-1]
print(last["question"])
print(last["answer"])

for chunk in last["context"]["chunks"]:
    print(chunk["citation_index"], chunk["chunk_type"], chunk["section_title"], chunk["content_preview"])
```

## 9. Khi nào dùng log này

Dùng khi cần kiểm tra:

- chatbot trả lời sai do retrieval lấy sai chunk hay do LLM suy luận sai;
- top_k/candidate_k hiện tại có đủ không;
- vector search và keyword search có lấy đúng nội dung không;
- chunk bảng có được đưa vào LLM không;
- chunk mục/phụ lục có giữ đúng ngữ cảnh không;
- câu hỏi có bị rewrite sai không;
- câu trả lời có citation đúng không.
