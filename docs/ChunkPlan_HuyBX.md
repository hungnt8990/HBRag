# KẾ HOẠCH NGHIÊN CỨU VÀ TRIỂN KHAI CHUNK TRONG HỆ THỐNG RAG

## 1. Mục tiêu

Nghiên cứu và xây dựng chiến lược chunk phù hợp cho hệ thống RAG, đặc biệt với dữ liệu văn bản hành chính/DOffice, nhằm:

- Tăng độ chính xác khi truy hồi tài liệu.
- Giữ nguyên ngữ cảnh quan trọng của văn bản.
- Không làm mất thông tin trong bảng, điều khoản, danh sách phân công.
- Hỗ trợ truy vấn theo số hiệu văn bản, người phụ trách, nhiệm vụ, thời hạn, điều khoản.
- Giảm nhiễu và giảm token dư thừa khi đưa vào LLM.
- Làm nền tảng cho pipeline mới:

```text
DOffice
→ normalize
→ chunk/evidence
→ compile knowledge_artifacts hoặc QA packets
→ index artifact/packet
→ index chunk fallback
```

---

## 2. Phạm vi nghiên cứu

### 2.1. Văn bản hành chính

Ví dụ:

- Công văn
- Thông báo
- Báo cáo
- Kế hoạch
- Văn bản chỉ đạo
- Văn bản có số hiệu, ngày ban hành, cơ quan ban hành

Mục tiêu:

- Truy xuất đúng số hiệu văn bản.
- Truy xuất đúng nội dung chính.
- Truy xuất đúng ngày ban hành, cơ quan ban hành, trích yếu.
- Tránh nhầm lẫn giữa các văn bản có nội dung tương tự.

---

### 2.2. Văn bản có bảng

Ví dụ:

- Bảng phân công nhiệm vụ
- Bảng nhân sự
- Bảng chỉ tiêu
- Bảng tiến độ
- Bảng chấm điểm
- Bảng quyền lợi/chế độ

Mục tiêu:

- Không làm mất header bảng.
- Không ghép sai dòng.
- Không lấy nhầm thông tin của người này sang người khác.
- Truy hồi tốt theo tên người, phòng ban, nhiệm vụ, STT, chỉ tiêu.

---

### 2.3. Văn bản pháp lý/quy định

Ví dụ:

- Điều
- Khoản
- Điểm
- Mục
- Quy chế
- Thỏa ước
- Quy định chế độ, quyền lợi

Mục tiêu:

- Không cắt rời điều khoản.
- Giữ được quan hệ giữa Điều → Khoản → Điểm.
- Trả lời chính xác các câu hỏi cụ thể như số ngày nghỉ, đối tượng áp dụng, điều kiện hưởng.

---

### 2.4. Báo cáo dài/thuyết minh

Ví dụ:

- Báo cáo tổng hợp
- Báo cáo tháng
- Báo cáo cải cách hành chính
- Văn bản giải trình
- Nội dung mô tả dài

Mục tiêu:

- Giữ được ý theo mục lớn.
- Không cắt ngang câu.
- Không làm mất tiêu đề mục.
- Hỗ trợ tốt cho câu hỏi tóm tắt và câu hỏi tổng hợp.

---

## 3. Các chiến lược chunk cần nghiên cứu

### 3.1. Fixed-size chunk

Cắt văn bản theo số token hoặc số ký tự cố định.

Ví dụ cấu hình:

```yaml
strategy: fixed_size
chunk_size: 512
overlap: 100
```

Vai trò:

- Dùng làm baseline.
- Dễ triển khai.
- Dễ so sánh với các chiến lược khác.

Hạn chế:

- Dễ cắt ngang câu.
- Dễ cắt ngang bảng.
- Không hiểu cấu trúc văn bản.
- Không phù hợp làm chiến lược chính cho văn bản hành chính.

---

### 3.2. Recursive chunk

Cắt văn bản theo thứ tự ưu tiên:

```text
heading
→ paragraph
→ sentence
→ token
```

Vai trò:

- Là baseline tốt hơn fixed-size.
- Phù hợp với văn bản thường.
- Có thể dùng làm fallback khi không phát hiện được cấu trúc rõ ràng.

Hạn chế:

- Chưa đủ tốt với bảng.
- Chưa đủ tốt với điều khoản pháp lý.
- Vẫn có thể làm mất ngữ cảnh nếu không gắn metadata.

---

### 3.3. Structure-aware chunk

Cắt văn bản dựa trên cấu trúc:

```text
document_preamble
section
article
clause
bullet
paragraph
```

Áp dụng cho:

- Công văn
- Báo cáo
- Kế hoạch
- Văn bản pháp lý
- Văn bản có mục/điều/khoản rõ ràng

Mục tiêu:

- Giữ tiêu đề mục trong chunk.
- Giữ quan hệ giữa các phần.
- Không tách rời nội dung khỏi bối cảnh văn bản.

Ví dụ chunk:

```text
Văn bản: 3113/EVN-KDMBĐ
Ngày ban hành: 02/06/2026
Cơ quan ban hành: ...
Mục: Nội dung triển khai

Nội dung chunk...
```

---

Plan bổ sung cho heading tree:

```text
Document
-> Phụ lục
-> Mục
-> Tiểu mục
-> Điều
-> Khoản / số thứ tự
-> Điểm / bullet
-> Paragraph
```

Mục tiêu của heading tree là biết mỗi đoạn đang nằm ở đâu trong cây cấu trúc của tài liệu. Ví dụ nếu một chunk thuộc `Phụ lục 02 -> 1. Mục tiêu`, metadata và nội dung chunk cần giữ đường dẫn này để retrieval không nhầm sang phụ lục hoặc mục khác.

Plan xử lý heading linh hoạt:

```text
Trường hợp có "Điều n":
Điều n = level 1
n.     = level 2
n.m    = level 3
n.m.k  = level 4

Trường hợp không có "Điều n":
n.     = level 1
n.m    = level 2
n.m.k  = level 3
```

Ví dụ nếu văn bản có:

```text
Điều 1. Phê duyệt kết quả lựa chọn nhà thầu
1. Tên gói thầu
2. Thông tin nhà thầu trúng thầu
```

thì `1.` và `2.` được hiểu là con của `Điều 1`.

Nếu văn bản chỉ có:

```text
1. Mục tiêu
1.1. Phạm vi
1.2. Nội dung
```

thì `1.` được hiểu là level 1, còn `1.1`, `1.2` là level 2.

Plan này giúp chunk text giữ được ngữ cảnh cha-con tốt hơn, đặc biệt với văn bản DOFFICE có phụ lục, điều khoản, mục và tiểu mục không đồng nhất giữa các tài liệu.

---

### 3.4. Table-aware chunk

Cắt bảng theo cách riêng, không xử lý như văn bản thường.

Các loại chunk cần tạo:

```text
table_summary_chunk
table_header_chunk
table_row_chunk
table_group_chunk
table_column_chunk
```

Trong đó:

- `table_summary_chunk`: mô tả tổng quan bảng, tên bảng, số dòng, danh sách cột và ngữ cảnh trước bảng.
- `table_header_chunk`: giữ header/cấu trúc cột để các chunk dòng/cột không mất nghĩa.
- `table_row_chunk`: lưu từng dòng bảng theo chiều ngang, phù hợp khi câu hỏi hỏi một đối tượng hoặc một dòng cụ thể.
- `table_group_chunk`: gom các `table_row_chunk` liên quan lại với nhau, ví dụ theo nhóm logic, section trong bảng hoặc khoảng dòng. Loại chunk này giúp tránh việc một dòng bảng bị lấy rời rạc và thiếu ngữ cảnh.
- `table_column_chunk`: gom nội dung theo chiều dọc của một cột. Nếu đã có `table_row_chunk`, thêm `table_column_chunk` giúp LLM biết đối tượng đó nằm ở cột nào và nội dung riêng của cột đó là gì. Cách này giảm lỗi trả lời dư khi một row chứa nhiều cột là nhiều đối tượng khác nhau.

Cấu trúc đề xuất cho table row chunk:

```text
Văn bản: ...
Bảng: Bảng phân công nhiệm vụ
Header: STT | Họ tên | Mảng | Phòng | Ghi chú
Dòng 3: Nguyễn Quang Lâm | RAG | PTUD | ...
```

Áp dụng cho:

- Bảng phân công
- Bảng chấm điểm
- Bảng chỉ tiêu
- Bảng nhân sự
- Bảng quyền lợi

Mục tiêu:

- Hỏi theo tên người phải lấy đúng dòng.
- Hỏi theo chỉ tiêu phải lấy đúng dòng chỉ tiêu.
- Hỏi theo phòng ban phải lấy đúng nhóm nhiệm vụ.
- Không lấy nhầm dòng lân cận.

Cấu trúc đề xuất cho table group chunk:

```text
Văn bản: ...
Bảng: ...
Nhóm dòng: Rows 1-10 hoặc nhóm logic trong bảng
Header: ...

Các dòng liên quan:
- Dòng 1: ...
- Dòng 2: ...
- Dòng 3: ...
```

Cấu trúc đề xuất cho table column chunk:

```text
Văn bản: ...
Bảng: ...
Cột bảng: CPCIT
Cột dùng làm ngữ cảnh hàng: STT, Dữ liệu

Nội dung cột theo từng dòng:
| Dòng | Ngữ cảnh hàng | Nội dung cột |
| --- | --- | --- |
| 1 | Dữ liệu: GIS 110kV | Nội dung thuộc CPCIT |
| 2 | Dữ liệu: GIS trung thế | Nội dung thuộc CPCIT |
```

---

### 3.5. Legal clause-aware chunk

Chiến lược chunk riêng cho văn bản pháp lý/quy định.

Cấu trúc:

```text
Điều
→ Khoản
→ Điểm
→ Nội dung
```

Nguyên tắc:

- Không tách Điều khỏi tên Điều.
- Không tách Khoản khỏi Điều.
- Không tách Điểm khỏi Khoản nếu nội dung ngắn.
- Nếu Điều quá dài, chia nhỏ nhưng mỗi chunk vẫn phải giữ metadata Điều/Khoản.

Ví dụ:

```text
Văn bản: Thỏa ước lao động tập thể EVNCPC
Điều 10: Chế độ nghỉ việc riêng
Khoản: ...
Điểm: ...

Nội dung: Con đẻ, con nuôi kết hôn được nghỉ ...
```

---

### 3.6. Semantic chunk

Cắt văn bản dựa trên sự liên quan ngữ nghĩa giữa các câu/đoạn.

Áp dụng cho:

- Báo cáo dài
- Nội dung thuyết minh
- Văn bản không có bảng
- Văn bản không có cấu trúc rõ

Không nên áp dụng chính cho:

- Bảng
- Điều khoản pháp lý
- Danh sách phân công
- Văn bản có format chặt

Vai trò:

- Dùng bổ sung cho nội dung dài.
- Không thay thế structure-aware chunk.

---

### 3.7. Artifact-first chunk

Đây là hướng mục tiêu cho hệ thống.

Thay vì chỉ lưu chunk thô, hệ thống sẽ tạo thêm các đơn vị tri thức đã biên dịch:

```text
knowledge_artifact
QA packet
evidence packet
table evidence
legal evidence
```

Pipeline đề xuất:

```text
DOffice
→ normalize
→ detect structure
→ create evidence chunks
→ compile knowledge_artifacts
→ index artifact first
→ index raw chunks as fallback
```

Ví dụ artifact:

```json
{
  "artifact_type": "person_assignment",
  "subject": "Nguyễn Quang Lâm",
  "answer_facts": [
    "STT 3 - RAG - PTUD",
    "STT 4 - OCR - PM",
    "STT 5 - Kho dữ liệu AI - VH",
    "STT 6 - Platform AI - PTUD"
  ],
  "source_doc": "...",
  "evidence_rows": [3, 4, 5, 6]
}
```

---

## 4. Thiết kế metadata cho chunk

Mỗi chunk cần có metadata đầy đủ để hỗ trợ retrieval và citation.

Metadata đề xuất:

```json
{
  "doc_id": "...",
  "doc_code": "...",
  "issued_date": "...",
  "issuing_org": "...",
  "document_type": "...",
  "document_title": "...",
  "section_title": "...",
  "article_number": "...",
  "clause_number": "...",
  "table_title": "...",
  "row_index": "...",
  "chunk_type": "...",
  "entities": [],
  "keywords": [],
  "source_span": {
    "start": 0,
    "end": 0
  }
}
```

Các `chunk_type` nên có:

```text
document_preamble
paragraph
section
article
clause
bullet
table_summary
table_header
table_row
table_group
table_column
assignment_section
legal_clause
qa_packet
knowledge_artifact
fallback_chunk
```

---

## 5. Bộ benchmark đánh giá chunk

Cần xây bộ câu hỏi test cố định để đánh giá công bằng.

### 5.1. Nhóm câu hỏi theo số hiệu văn bản

Ví dụ:

```text
3113 là văn bản gì?
Văn bản 3113 ban hành ngày nào?
Cơ quan nào ban hành văn bản 3113?
```

---

### 5.2. Nhóm câu hỏi theo người

Ví dụ:

```text
Nguyễn Quang Lâm tham gia mảng nào?
Phước Lâm phụ trách những mảng nào?
Ai phụ trách mảng RAG?
```

---

### 5.3. Nhóm câu hỏi theo bảng

Ví dụ:

```text
Chỉ tiêu này được giao cho đơn vị nào?
Phòng nào chịu trách nhiệm nhiệm vụ này?
STT 3 trong bảng phân công là nội dung gì?
```

---

### 5.4. Nhóm câu hỏi theo điều khoản

Ví dụ:

```text
Con đẻ kết hôn được nghỉ mấy ngày?
Cha mẹ kết hôn được nghỉ mấy ngày?
Điều 10 quy định nội dung gì?
```

---

### 5.5. Nhóm câu hỏi tổng hợp

Ví dụ:

```text
Tóm tắt nội dung chính của văn bản này.
Các nhiệm vụ chính được giao trong công văn là gì?
Những đơn vị nào phải báo cáo trước ngày 24/6/2026?
```

---

## 6. Chỉ số đánh giá

### 6.1. Đánh giá chất lượng chunk

Các tiêu chí:

```text
- Chunk có bị cắt ngang câu không?
- Chunk có bị mất tiêu đề không?
- Chunk có bị mất header bảng không?
- Chunk có bị ghép sai dòng bảng không?
- Chunk có quá dài không?
- Chunk có quá ngắn không?
- Metadata có đầy đủ không?
- Có giữ được source span không?
```

---

### 6.2. Đánh giá retrieval

Các metric cần đo:

```text
Recall@5
Recall@10
MRR
Precision@5
Hit Rate
Wrong Document Rate
Wrong Row Rate
```

Ý nghĩa:

- `Recall@5`: top 5 có chứa chunk đúng không.
- `MRR`: chunk đúng đứng ở vị trí bao nhiêu.
- `Wrong Row Rate`: có lấy nhầm dòng bảng không.
- `Wrong Document Rate`: có lấy nhầm văn bản không.

---

### 6.3. Đánh giá câu trả lời cuối

Các tiêu chí:

```text
Answer correctness
Faithfulness
Citation correctness
Completeness
No hallucination
No wrong row
No wrong document
```

Plan đánh giá đề xuất:

```text
Answer correctness  -> LLM-as-judge / RAGAS
Completeness        -> LLM-as-judge / RAGAS
Faithfulness        -> LLM-as-judge / RAGAS
No hallucination    -> LLM-as-judge / RAGAS
Citation correctness -> rule + human spot check
No wrong row        -> rule nếu có expected evidence, human spot check nếu case phức tạp
No wrong document   -> rule theo expected_doc_id / expected_doc_code
```

Lý do nên dùng LLM-as-judge/RAGAS cho các tiêu chí answer-level là vì câu trả lời cuối thường không chỉ đúng/sai theo exact string. Một câu trả lời có thể diễn đạt khác expected answer nhưng vẫn đúng, đầy đủ và faithful với context. Vì vậy dùng LLM-as-judge phù hợp hơn rule cứng cho các tiêu chí như `Answer correctness`, `Completeness`, `Faithfulness`, `No hallucination`.

Rule-based evaluation vẫn nên dùng cho các tiêu chí có thể kiểm tra trực tiếp, ví dụ:

- có lấy đúng document không;
- có citation đúng chunk không;
- có lấy đúng row/table không nếu benchmark đã gắn expected evidence;
- context có chứa chunk expected không.

Human review nên dùng để kiểm tra mẫu, đặc biệt với các câu hỏi khó, câu hỏi tổng hợp hoặc khi LLM-as-judge và rule-based evaluation cho kết quả mâu thuẫn.

---

## 7. Ma trận thử nghiệm

| Mã thử nghiệm | Chiến lược         | Chunk size          | Overlap   | Mục đích                     |
| ------------- | ------------------ | ------------------- | --------- | ---------------------------- |
| S1            | Fixed-size         | 512                 | 100       | Baseline                     |
| S2            | Fixed-size         | 1024                | 150       | So sánh chunk lớn            |
| S3            | Recursive          | 512                 | 100       | Baseline nâng cao            |
| S4            | Recursive          | 800                 | 100       | Giữ nhiều ngữ cảnh hơn       |
| S5            | Structure-aware    | Adaptive            | Ít        | Văn bản hành chính           |
| S6            | Table-aware        | Row + group + column | 0         | Bảng phân công/bảng chỉ tiêu |
| S7            | Legal clause-aware | Theo điều/khoản     | Ít        | Văn bản pháp lý              |
| S8            | Semantic chunk     | Adaptive            | 0         | Báo cáo dài                  |
| S9            | Hybrid chunk       | Adaptive            | Theo loại | Phương án tổng hợp           |
| S10           | Artifact-first     | Artifact + fallback | Adaptive  | Phương án mục tiêu           |

---

## 8. Quy trình triển khai

### Giai đoạn 1: Rà soát chunk hiện tại

Công việc:

- Kiểm tra chunk đang sinh ra trong hệ thống.
- Tìm lỗi cắt ngang câu.
- Tìm lỗi mất bảng.
- Tìm lỗi mất metadata.
- Tìm lỗi chunk quá dài/quá ngắn.
- Tìm lỗi retrieval sai do chunk kém.

Đầu ra:

```text
current_chunk_analysis.md
Danh sách lỗi chunk phổ biến
Ví dụ lỗi cụ thể
```

---

### Giai đoạn 2: Xây bộ benchmark

Công việc:

- Chọn 30–50 văn bản mẫu.
- Tạo 100–150 câu hỏi kiểm thử.
- Gắn expected answer.
- Gắn expected evidence.
- Phân loại câu hỏi theo nhóm.

Đầu ra:

```text
benchmark_questions.jsonl
expected_evidence.jsonl
```

Ví dụ:

```json
{
  "question": "Nguyễn Quang Lâm tham gia mảng nào?",
  "question_type": "table_person_lookup",
  "expected_answer": "Nguyễn Quang Lâm tham gia 04 mảng...",
  "expected_doc_code": "...",
  "expected_evidence": ["row_3", "row_4", "row_5", "row_6"]
}
```

---

### Giai đoạn 3: Implement các chiến lược chunk

Công việc:

- Implement fixed-size chunk.
- Implement recursive chunk.
- Implement structure-aware chunk.
- Implement heading tree parser cho cấu trúc `Phụ lục -> Mục -> Tiểu mục -> ...`.
- Implement heading level linh hoạt: có `Điều n` thì `Điều n` là level 1, `n.` là level 2; nếu không có `Điều n` thì `n.` là level 1.
- Implement table-aware chunk.
- Implement `table_group` để gom các dòng bảng liên quan.
- Implement `table_column` để đọc bảng theo chiều dọc, bổ sung cho `table_row`.
- Implement legal clause-aware chunk.
- Implement semantic chunk.
- Implement artifact-first chunk.

Đầu ra:

```text
chunking strategies
chunking config
unit tests
```

---

### Giai đoạn 4: Index và đánh giá retrieval

Công việc:

- Tạo index riêng cho từng chiến lược.
- Chạy toàn bộ benchmark.
- Ghi lại top-k retrieval.
- Đo Recall@5, Recall@10, MRR.
- Phân tích lỗi retrieval.

Đầu ra:

```text
retrieval_eval_report.md
retrieval_results.csv
error_analysis.md
```

---

### Giai đoạn 5: Đánh giá câu trả lời cuối

Công việc:

- Chạy cùng một bộ câu hỏi.
- Giữ nguyên LLM, prompt, reranker.
- Chỉ thay đổi chiến lược chunk.
- Đánh giá câu trả lời.
- So sánh hallucination, citation, độ đầy đủ.

Đầu ra:

```text
answer_eval_report.md
answer_results.csv
```

---

### Giai đoạn 6: Chốt chiến lược chunk chính thức

Công việc:

- Chọn chiến lược tốt nhất theo từng loại văn bản.
- Thiết kế adaptive chunk router.
- Chuẩn hóa metadata.
- Viết quality gate.
- Viết regression test.
- Tích hợp vào pipeline chính.

Đầu ra:

```text
final_chunking_strategy.md
chunking_config.yaml
quality_gate_tests
regression_tests
```

---

## 9. Kiến trúc chunk mục tiêu

Kiến trúc đề xuất:

```text
DOffice Source
→ Normalize
→ Extract metadata
→ Detect document structure
→ Build heading tree: Phụ lục / Điều / Mục / Tiểu mục
→ Detect tables
→ Detect legal clauses
→ Build evidence chunks
→ Build table row chunks
→ Build table group chunks
→ Build table column chunks
→ Build legal clause chunks
→ Compile knowledge artifacts / QA packets
→ Index artifacts into Qdrant / Elasticsearch / PostgreSQL
→ Index fallback chunks
→ Hybrid retrieval
→ Rerank
→ Generate answer with citations
```

---

## 10. Cấu hình chunk đề xuất ban đầu

```yaml
chunking:
  default_strategy: structure_aware
  fallback_strategy: recursive

  default:
    max_tokens: 700
    overlap_tokens: 80
    min_tokens: 80

  recursive:
    chunk_size: 700
    overlap: 80
    separators:
      - "\n## "
      - "\n### "
      - "\n\n"
      - ". "
      - " "

  heading_tree:
    enabled: true
    appendix_aware: true
    flexible_article_numbering: true
    rule:
      with_article: "Điều n = level 1, n. = level 2, n.m = level 3"
      without_article: "n. = level 1, n.m = level 2"

  table:
    strategy: row_level_with_context
    include_document_preamble: true
    include_table_title: true
    include_header: true
    include_row_index: true
    build_table_group: true
    build_table_column: true
    table_group_size: 10
    table_column_include_row_context: true
    max_rows_per_chunk: 1
    overlap_rows: 0

  legal:
    strategy: article_clause_aware
    keep_article_title: true
    keep_clause_context: true
    split_long_article: true
    max_tokens: 900

  report:
    strategy: semantic_section
    fallback: recursive
    max_tokens: 800

  artifact:
    enabled: true
    index_first: true
    fallback_to_raw_chunk: true
```

---

## 11. Quality gate cho chunk

Trước khi index, mỗi chunk cần qua kiểm tra chất lượng.

Checklist:

```text
[ ] Không vượt quá token limit
[ ] Không quá ngắn nếu không có metadata bổ trợ
[ ] Không cắt ngang câu bất thường
[ ] Không mất document preamble
[ ] Không mất số hiệu văn bản
[ ] Không mất ngày ban hành
[ ] Không mất cơ quan ban hành
[ ] Không mất tiêu đề mục
[ ] Có heading_path / heading_tree khi văn bản có phụ lục, điều, mục, tiểu mục
[ ] Không mất header bảng
[ ] Không ghép sai dòng bảng
[ ] Bảng dài có table_group phù hợp
[ ] Bảng có cột quan trọng có table_column phù hợp
[ ] Có chunk_type
[ ] Có doc_id
[ ] Có source_span
[ ] Có metadata đủ để citation
```

---

## 12. Lộ trình thực hiện

### Tuần 1: Khảo sát và benchmark

- Rà soát chunk hiện tại.
- Chọn bộ tài liệu mẫu.
- Tạo bộ câu hỏi benchmark.
- Gắn expected answer và expected evidence.

Kết quả cần có:

```text
current_chunk_analysis.md
benchmark_questions.jsonl
expected_evidence.jsonl
```

---

### Tuần 2: Xây chiến lược chunk

- Implement fixed-size baseline.
- Implement recursive baseline.
- Implement structure-aware chunk.
- Implement table-aware chunk.
- Implement legal clause-aware chunk.
- Bắt đầu artifact-first chunk.

Kết quả cần có:

```text
chunking_strategies.py
chunking_config.yaml
unit_tests
```

---

### Tuần 3: Đánh giá retrieval

- Index từng chiến lược.
- Chạy benchmark retrieval.
- Đo Recall@5, Recall@10, MRR.
- Phân tích lỗi theo từng nhóm câu hỏi.

Kết quả cần có:

```text
retrieval_eval_report.md
retrieval_results.csv
retrieval_error_analysis.md
```

---

### Tuần 4: Đánh giá answer và chốt giải pháp

- Chạy answer benchmark.
- So sánh các chiến lược.
- Chốt hybrid/adaptive chunking.
- Viết regression test.
- Tích hợp vào pipeline chính.

Kết quả cần có:

```text
answer_eval_report.md
final_chunking_strategy.md
chunking_config_final.yaml
regression_tests
```

---

## 13. Định hướng chốt

Chiến lược chunk cuối cùng không nên là một kiểu duy nhất.

Đề xuất hướng chốt:

```text
Văn bản thường        → structure-aware + recursive fallback
Bảng                  → table-aware row-level chunk
Điều khoản pháp lý    → legal clause-aware chunk
Báo cáo dài           → semantic section + recursive fallback
Truy vấn quan trọng   → knowledge artifact / QA packet first
```

Pipeline mục tiêu:

```text
Artifact-first retrieval
→ Evidence chunk
→ Raw chunk fallback
```

Kết luận:

> Chunk thô chỉ nên là lớp dự phòng.  
> Evidence chunk và knowledge artifact nên là lớp truy hồi chính trong hệ thống RAG.
