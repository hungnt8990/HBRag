# Review ChunkPlan.md so với pipeline chunk hiện tại

Tài liệu này review file `ChunkPlan.md` và so sánh với pipeline chunk hiện tại của dự án HBRag, đặc biệt là nhánh DOFFICE mà dự án đang tối ưu nhiều nhất.

Nhận xét tổng quan: `ChunkPlan.md` là một kế hoạch nghiên cứu khá tốt ở mức định hướng. Nó nhìn rộng hơn pipeline hiện tại, có đề cập benchmark, metric đánh giá, artifact-first retrieval và quality gate. Tuy nhiên, nếu so với phần chunk DOFFICE hiện tại thì kế hoạch vẫn còn khá tổng quát, chưa phản ánh đầy đủ những gì hệ thống hiện tại đã làm được, nhất là xử lý phụ lục, mục cha - mục con, bảng Markdown/HTML, table column, và metadata theo từng loại chunk.

## 1. Pipeline hiện tại đang làm gì

Pipeline chunk DOFFICE hiện tại có thể hiểu ngắn gọn như sau:

```text
DOffice JSON
-> lấy nội dung và metadata
-> chuẩn hóa text
-> nhận diện bảng HTML và bảng Markdown
-> bảo vệ bảng trước khi clean text
-> tách text khỏi bảng
-> nhận diện phụ lục, điều, mục cha, mục con
-> tạo document_header/document_body/document_summary
-> tạo table_parent/table_row/table_group/table_column
-> thêm preamble vào chunk
-> lưu PostgreSQL
-> embedding/index Qdrant + Elasticsearch
```

Các loại chunk hiện tại quan trọng nhất:

```text
document_header
document_summary
document_body
table_parent
table_row
table_group
table_column
footer_signature
```

Điểm mạnh hiện tại là dự án không còn chỉ chunk theo độ dài. Hệ thống đã có hướng structure-aware cho text và table-aware cho bảng.

## 2. Điểm tốt trong ChunkPlan.md nên giữ

### 2.1. Có mục tiêu đúng với bài toán RAG thực tế

Kế hoạch đặt mục tiêu đúng:

- tăng độ chính xác retrieval;
- giữ ngữ cảnh văn bản;
- không làm mất thông tin bảng;
- hỗ trợ hỏi theo số hiệu văn bản, ngày ban hành, người phụ trách, nhiệm vụ, điều khoản;
- giảm token dư thừa khi đưa vào LLM.

Đây là các vấn đề dự án hiện tại đang gặp thật. Ví dụ câu hỏi "ai là người ký văn bản 6515" bị kéo quá nhiều `table_row`, context quá lớn nhưng lại thiếu chunk hành chính. Vì vậy mục tiêu giảm nhiễu và giảm token dư thừa là rất đúng.

### 2.2. Nhìn nhận đúng rằng không nên chỉ có một chiến lược chunk

Kế hoạch đề xuất nhiều kiểu:

```text
fixed-size
recursive
structure-aware
table-aware
legal clause-aware
semantic chunk
artifact-first
```

Quan điểm này đúng. Một hệ thống RAG xử lý văn bản hành chính, bảng, phụ lục, điều khoản không nên dùng một kiểu chunk duy nhất.

Pipeline hiện tại cũng đang đi theo hướng này, chỉ là tên gọi khác:

- DOFFICE dùng route riêng `doffice_admin`;
- text body dùng structure-aware;
- bảng dùng table-aware;
- upload file thường có profile/router riêng;
- Docling router có route cho legal/catalog/mixed document.

### 2.3. Table-aware chunk là hướng đúng

`ChunkPlan.md` nhấn mạnh bảng không nên xử lý như text thường. Đây là điểm rất tốt.

Pipeline hiện tại cũng đã chứng minh điều này là cần thiết. Dự án đã có:

- `table_parent`: tổng quan bảng;
- `table_row`: từng dòng bảng;
- `table_group`: nhóm dòng;
- `table_column`: đọc bảng theo chiều dọc.

Kế hoạch nên giữ chắc hướng table-aware, vì tài liệu DOFFICE có rất nhiều bảng thuộc tính, bảng phân công, bảng phụ lục.

### 2.4. Legal clause-aware là cần thiết

Kế hoạch tách riêng văn bản pháp lý/quy định theo:

```text
Điều -> Khoản -> Điểm
```

Điểm này đúng. Dự án hiện tại cũng đã bắt đầu xử lý quan hệ:

```text
Điều 1
-> 1.
-> 2.
-> 3.
```

Tức là nếu có `Điều 1`, các mục `1.`, `2.`, `3.` sau đó có thể được hiểu là con của `Điều 1`, không bị hiểu nhầm thành mục cấp cao nhất.

### 2.5. Có benchmark và metric đánh giá

Đây là phần rất đáng giữ.

Kế hoạch đề xuất:

```text
Recall@5
Recall@10
MRR
Precision@5
Hit Rate
Wrong Document Rate
Wrong Row Rate
```

Các metric này phù hợp với RAG. Đặc biệt:

- `Wrong Document Rate` rất quan trọng vì các văn bản có mã số và nội dung gần giống nhau.
- `Wrong Row Rate` rất quan trọng vì bảng dễ lấy nhầm dòng.
- `MRR` giúp biết chunk đúng có nằm cao trong kết quả retrieval không.

Nếu có benchmark cố định, dự án sẽ bớt đánh giá bằng cảm giác.

### 2.6. Có ý tưởng artifact-first

Đây là phần mạnh nhất trong `ChunkPlan.md`.

Ý tưởng:

```text
raw chunk chỉ là fallback
knowledge artifact / QA packet / evidence packet là lớp truy hồi chính
```

Hướng này tốt cho các câu hỏi hay lặp lại:

- văn bản này ban hành ngày nào;
- ai là người ký;
- ai phụ trách nhiệm vụ này;
- bảng này có bao nhiêu dòng;
- người A tham gia những mảng nào;
- đơn vị B phải làm gì.

Với các câu hỏi dạng fact lookup, artifact-first thường tốt hơn đưa nhiều chunk thô vào LLM.

## 3. Điểm ChunkPlan.md đang hơn pipeline hiện tại

### 3.1. Có tư duy đánh giá hệ thống rõ hơn

Pipeline hiện tại đã code khá nhiều, nhưng phần đánh giá vẫn còn thủ công: hỏi thử, xem log, xem chunk PostgreSQL.

`ChunkPlan.md` đề xuất hẳn:

```text
benchmark_questions.jsonl
expected_evidence.jsonl
retrieval_eval_report.md
answer_eval_report.md
```

Đây là điểm hơn rõ ràng. Dự án hiện tại nên học theo phần này.

### 3.2. Có quality gate trước khi index

Pipeline hiện tại có xử lý chunk nhưng chưa có một lớp quality gate thật rõ cho DOFFICE trước khi embedding/index.

`ChunkPlan.md` đề xuất checklist:

- không quá dài;
- không quá ngắn;
- không mất tiêu đề;
- không mất header bảng;
- có metadata;
- có source span;
- không ghép sai dòng bảng.

Đây là phần nên bổ sung vào dự án hiện tại. Nó giúp phát hiện lỗi trước khi index, thay vì đợi chatbot trả lời sai mới đi dò.

### 3.3. Có ý tưởng source span

Kế hoạch đề xuất metadata:

```json
"source_span": {
  "start": 0,
  "end": 0
}
```

Hiện tại chunk DOFFICE chủ yếu mạnh ở metadata ngữ nghĩa như `heading_path`, `table_name`, `row_number`, nhưng source span chưa phải trọng tâm.

Nếu có source span, việc debug sẽ tốt hơn:

- biết chunk lấy từ đoạn nào của text gốc;
- đối chiếu với raw DOFFICE dễ hơn;
- kiểm tra chunk có bỏ sót nội dung không.

### 3.4. Artifact-first giải quyết tốt câu hỏi hành chính

Vấn đề thực tế hiện tại: hỏi "ai ký văn bản 6515" nhưng retrieval kéo 110 chunk bảng, không kéo đúng `document_header`.

Nếu có artifact-first, hệ thống có thể tạo artifact kiểu:

```json
{
  "artifact_type": "document_admin_metadata",
  "doc_code": "6515/EVNCPC-VTCNTT+KD+KT",
  "issued_date": "...",
  "issuer": "...",
  "signer": "..."
}
```

Khi hỏi người ký, retrieval lấy artifact này trước, không cần đưa hàng trăm `table_row` vào LLM.

Đây là điểm kế hoạch hơn pipeline hiện tại.

## 4. Điểm ChunkPlan.md đang thua hoặc thiếu so với pipeline hiện tại

### 4.1. Kế hoạch chưa phản ánh đầy đủ xử lý DOFFICE hiện tại

Kế hoạch nói nhiều về strategy chung, nhưng chưa mô tả rõ nhánh DOFFICE hiện tại đã có:

- `document_header`;
- `document_summary`;
- `document_body`;
- `table_parent`;
- `table_row`;
- `table_group`;
- `table_column`;
- `footer_signature`.

Nếu người đọc chỉ đọc `ChunkPlan.md`, họ có thể tưởng dự án chưa có table-aware hoặc structure-aware, trong khi thực tế đã có nhiều phần.

### 4.2. Table-aware trong kế hoạch còn đơn giản hơn hiện tại

Kế hoạch đề xuất:

```text
table_summary_chunk
table_header_chunk
table_row_chunk
```

Nhưng pipeline hiện tại đã phong phú hơn:

```text
table_parent
table_row
table_group
table_column
```

Đặc biệt `table_column` là phần kế hoạch chưa nhắc rõ. Trong tài liệu có bảng so sánh nhiều bên như `CPCIT` và `Các CTDL`, đọc theo cột là rất cần.

Vì vậy phần table-aware trong kế hoạch nên nâng cấp để bao gồm:

- chunk tổng quan bảng;
- chunk dòng;
- chunk nhóm dòng;
- chunk cột;
- chunk artifact cho fact quan trọng nếu cần.

### 4.3. Chưa nói rõ bảng Markdown và HTML

Pipeline hiện tại đã xử lý cả:

```text
HTML table
Markdown table
```

Kế hoạch chỉ nói chung về bảng, chưa nói rõ dữ liệu DOFFICE có thể trả về nhiều dạng:

- bảng HTML;
- bảng Markdown;
- text có lẫn `<br>`;
- bảng OCR lỗi;
- bảng bị flatten thành text.

Phần này nên bổ sung để kế hoạch sát dữ liệu thật hơn.

### 4.4. Chưa nhắc phụ lục như một cấu trúc quan trọng

Trong thực tế, dự án gặp lỗi với:

```text
Phụ lục 01
Phụ lục 02
1. Mục tiêu
```

Pipeline hiện tại đã phải sửa để không cắt bỏ phụ lục và để phụ lục làm cha của các mục bên trong.

`ChunkPlan.md` có nói section/article/clause, nhưng chưa nhấn mạnh `Phụ lục` là một cấu trúc riêng rất quan trọng trong DOFFICE.

Nên bổ sung:

```text
appendix-aware chunk
Phụ lục -> Mục -> Tiểu mục -> Bảng
```

### 4.5. Chưa nói rõ quan hệ mục cha - mục con linh hoạt

Pipeline hiện tại có logic quan trọng:

```text
Nếu có Điều 1 -> 1., 2., 3. là con của Điều 1
Nếu không có Điều -> 1., 2., 3. là mục cấp cao
Nếu trong Phụ lục -> 1. Mục tiêu là con của Phụ lục
```

Đây là điểm thực tế rất quan trọng. Kế hoạch chỉ nói chung "Điều -> Khoản -> Điểm", chưa nói các trường hợp nhập nhằng như:

- `1.` lúc là mục cha;
- `1.` lúc là con của `Điều`;
- `1.` lúc là con của `Phụ lục`;
- `1.1` lúc là con của `1.`;
- OCR có thể làm sai `Điều`.

Kế hoạch nên bổ sung phần này.

### 4.6. Chưa nhắc vấn đề context quá lớn sau retrieval

Một lỗi thực tế đã thấy: top_k request là 5 nhưng context đưa vào LLM thành hơn 100 chunk, hơn 300k ký tự.

`ChunkPlan.md` có nói giảm token dư thừa, nhưng chưa đưa ra rule cụ thể:

```text
final context limiter
intent-aware context filtering
metadata/admin question shortcut
không để mã văn bản kéo toàn bộ table_row
```

Đây không chỉ là vấn đề chunking, mà là vấn đề retrieval/answer layer. Nhưng kế hoạch chunk nên ghi nhận vì chunk prefix "Văn bản: 6515..." có thể làm mọi chunk cùng match mã văn bản.

### 4.7. Metadata đề xuất chưa khớp schema hiện tại

Kế hoạch dùng các tên:

```text
doc_id
doc_code
issuing_org
table_title
```

Trong pipeline hiện tại, metadata thường có:

```text
id_vb
document_code
ky_hieu
trich_yeu
issued_date
issuer
signer
nguoi_ky
section_title
heading_path
table_name
row_number
column_name
field_name
```

Không phải kế hoạch sai, nhưng nên map rõ tên mới với tên đang dùng, tránh sau này sinh thêm metadata song song gây rối.

## 5. Điểm cần cải thiện trong ChunkPlan.md

### 5.1. Tách rõ "kế hoạch nghiên cứu" và "kế hoạch triển khai"

Hiện tại `ChunkPlan.md` vừa nói nghiên cứu, vừa nói kiến trúc mục tiêu, vừa nói lộ trình 4 tuần, vừa nói config. Nội dung tốt nhưng hơi rộng.

Nên chia thành 2 phần:

```text
Phần A: Research plan
Phần B: Implementation plan cho HBRag hiện tại
```

Research plan có thể giữ nhiều chiến lược. Implementation plan nên nói rõ áp dụng vào DOFFICE như thế nào.

### 5.2. Thêm trạng thái hiện tại của dự án

Nên thêm một mục:

```text
Hiện tại hệ thống đã có gì
```

Ví dụ:

```text
Đã có:
- DOFFICE normalizer
- document_header
- document_body theo heading
- table_parent/table_row/table_group/table_column
- Markdown table clean
- chunk preamble
- metadata heading_path

Chưa có:
- source_span đầy đủ
- quality gate
- benchmark tự động
- artifact-first cho admin metadata
- final context limiter
```

Phần này giúp người đọc không nhầm kế hoạch là làm lại từ đầu.

### 5.3. Bổ sung appendix-aware chunk

Nên thêm chiến lược:

```text
Appendix-aware chunk
```

Vì DOFFICE hay có phụ lục và bảng nằm trong phụ lục. Cấu trúc nên là:

```text
Phụ lục
-> tiêu đề phụ lục
-> mục trong phụ lục
-> bảng trong phụ lục
```

Metadata cần có:

```text
appendix_title
appendix_number
heading_path
table_context
```

### 5.4. Bổ sung table-column chunk vào table-aware

Nên sửa phần table-aware từ:

```text
table_summary_chunk
table_header_chunk
table_row_chunk
```

thành:

```text
table_parent
table_row
table_group
table_column
table_artifact nếu cần
```

Lý do: `table_column` rất quan trọng với bảng có nhiều cột là nhiều đối tượng khác nhau.

### 5.5. Bổ sung document admin artifact

Trong phần artifact-first, nên thêm artifact đầu tiên và dễ làm nhất:

```text
document_admin_metadata artifact
```

Nó chứa:

```text
số văn bản
ngày ban hành
cơ quan ban hành
trích yếu
người ký
id_vb
```

Đây là artifact có giá trị cao, chi phí thấp và giải quyết lỗi thực tế hiện tại.

### 5.6. Bổ sung final context control

Nên thêm vào pipeline mục tiêu:

```text
Hybrid retrieval
-> rerank
-> intent-aware filter
-> final context limiter
-> answer
```

Nếu không có bước này, chunk tốt vẫn có thể bị đưa quá nhiều vào LLM.

Rule đề xuất:

```text
Câu hỏi hành chính: tối đa 3-5 context
Câu hỏi bảng chi tiết: tối đa 8-12 context
Câu hỏi tổng hợp: tối đa 15-20 context
Luôn giới hạn max_context_chars sau mọi bước expand
```

### 5.7. Bổ sung mapping metadata với hệ thống hiện tại

Nên có bảng:

| Trong ChunkPlan | Trong hệ thống hiện tại | Ghi chú |
| --- | --- | --- |
| `doc_code` | `document_code` / `ky_hieu` | nên thống nhất |
| `issuing_org` | `issuer` / `noi_ban_hanh` | nên dùng `issuer` |
| `table_title` | `table_name` | đang dùng `table_name` |
| `article_number` | có thể thêm | dùng cho legal |
| `source_span` | chưa đầy đủ | nên bổ sung |

Việc này giúp tránh tạo metadata trùng nghĩa.

### 5.8. Bổ sung test theo lỗi thật đã gặp

Benchmark nên thêm các case thực tế:

```text
Ai là người ký văn bản 6515?
Mục tiêu của Phụ lục 02 là gì?
CPCIT cần làm gì trong bảng Phụ lục 01?
Khung CSDL GIS hạ thế gồm bao nhiêu lớp?
Trường TenKhachHang có kiểu dữ liệu gì?
Có bao nhiêu table được nêu trong nội dung văn bản?
```

Các câu này phản ánh đúng lỗi hiện tại hơn là chỉ ví dụ chung.

## 6. So sánh ngắn gọn

| Tiêu chí | Pipeline hiện tại | ChunkPlan.md |
| --- | --- | --- |
| Mức độ triển khai | Đã có code thực tế | Chủ yếu là kế hoạch |
| DOFFICE-specific | Mạnh | Chưa mô tả đủ |
| Phụ lục | Đã xử lý | Chưa nhấn mạnh |
| Mục cha - mục con | Đã có logic linh hoạt | Có ý tưởng nhưng còn chung |
| Bảng HTML/Markdown | Đã xử lý | Chưa nói rõ |
| Table row | Đã có | Có |
| Table group | Đã có | Chưa rõ |
| Table column | Đã có | Chưa có |
| Document header | Đã có | Có ý tưởng preamble nhưng chưa cụ thể |
| Artifact-first | Chưa hoàn chỉnh | Định hướng tốt |
| Benchmark | Chưa đầy đủ | Đề xuất tốt |
| Quality gate | Chưa rõ | Đề xuất tốt |
| Final context limiter | Cần cải thiện | Chưa nói đủ |

## 7. Nên giữ gì từ ChunkPlan.md

Nên giữ các phần sau:

```text
benchmark cố định
metric retrieval
metric answer correctness
quality gate
artifact-first retrieval
raw chunk fallback
adaptive chunk router theo loại tài liệu
legal clause-aware
table-aware
structure-aware
```

Đây là các hướng đúng và có thể đưa vào roadmap.

## 8. Nên chỉnh gì trong ChunkPlan.md

Nên cập nhật `ChunkPlan.md` theo hướng:

```text
Không viết như thể dự án chưa có gì.
Ghi rõ hiện tại đã có DOFFICE chunker.
Ghi rõ phần nào là giữ lại, phần nào là cải tiến.
Thêm appendix-aware.
Thêm table_column.
Thêm document_admin_metadata artifact.
Thêm final context limiter.
Thêm mapping metadata với hệ thống hiện tại.
Thêm benchmark dựa trên lỗi thật.
```

## 9. Đề xuất roadmap thực tế hơn

Nếu tiếp tục từ trạng thái hiện tại, mình đề xuất thứ tự ưu tiên:

### Bước 1: Review và benchmark hiện tại

Tạo bộ câu hỏi test cố định, khoảng 30-50 câu trước, chưa cần 150 câu ngay.

Nhóm câu hỏi:

```text
hành chính văn bản
phụ lục/mục tiêu
bảng theo dòng
bảng theo cột
schema/trường dữ liệu
tổng hợp nội dung
```

### Bước 2: Thêm document admin artifact

Tạo artifact cho:

```text
id_vb
số/ký hiệu
ngày ban hành
cơ quan ban hành
trích yếu
người ký
```

Đây là bước nhỏ nhưng hiệu quả cao.

### Bước 3: Thêm final context limiter

Giới hạn context sau rerank/expand để tránh tình trạng một câu hỏi đơn giản đưa 100+ chunk vào LLM.

### Bước 4: Thêm quality gate cho chunk

Kiểm tra trước khi index:

```text
chunk quá dài
chunk quá ngắn
table row thiếu header
table context quá dài
document_header thiếu signer/ngày/cơ quan nếu metadata có
```

### Bước 5: Mở rộng artifact-first

Sau admin artifact mới mở rộng sang:

```text
table evidence artifact
person assignment artifact
legal clause artifact
schema field artifact
```

Không nên làm artifact-first quá rộng ngay từ đầu.

## 10. Kết luận

`ChunkPlan.md` là một kế hoạch tốt về mặt định hướng nghiên cứu. Điểm mạnh nhất của nó là có tư duy benchmark, quality gate và artifact-first retrieval. Đây là những thứ pipeline hiện tại còn thiếu hoặc chưa rõ.

Tuy nhiên, so với pipeline chunk DOFFICE hiện tại, kế hoạch vẫn còn tổng quát và chưa ghi nhận đủ các phần đã làm được như:

- phụ lục-aware;
- heading cha-con linh hoạt;
- bảng HTML/Markdown;
- `table_group`;
- `table_column`;
- `document_header`;
- preamble cho text/table chunk;
- metadata `heading_path`, `section_title`, `row_number`, `column_name`.

Vì vậy không nên dùng `ChunkPlan.md` như bản thay thế pipeline hiện tại. Nên xem nó là roadmap nghiên cứu và cải tiến tiếp theo.

Hướng tốt nhất là:

```text
Giữ pipeline DOFFICE hiện tại làm nền
-> bổ sung benchmark
-> bổ sung quality gate
-> bổ sung artifact-first cho metadata hành chính
-> bổ sung final context limiter
-> sau đó mới mở rộng artifact cho bảng/pháp lý/schema
```

Nói ngắn gọn: pipeline hiện tại tốt hơn ở phần xử lý cụ thể và đã chạy được; `ChunkPlan.md` tốt hơn ở phần định hướng đánh giá và kiến trúc mục tiêu. Việc nên làm là hợp nhất hai hướng này, không thay cái hiện tại bằng kế hoạch mới.
