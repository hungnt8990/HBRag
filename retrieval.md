# Pipeline retrieval hiện tại của HBRag

Tài liệu này mô tả lại pipeline retrieval hiện tại của dự án HBRag theo ngôn ngữ tự nhiên. Mục tiêu là giúp người đọc hiểu từ lúc người dùng đặt câu hỏi trên giao diện cho đến lúc hệ thống lấy context, đưa vào LLM và trả lời.

Nói ngắn gọn, retrieval hiện tại không chỉ là "lấy top_k chunk từ Qdrant". Hệ thống đang dùng một luồng hybrid khá nhiều bước:

```text
Câu hỏi người dùng
-> kiểm tra scope câu hỏi
-> rewrite câu hỏi để tìm kiếm tốt hơn
-> phân loại chiến lược retrieval
-> artifact-first retrieval nếu bật
-> fallback sang hybrid search nếu cần
-> vector search bằng Qdrant
-> keyword search bằng Elasticsearch hoặc PostgreSQL fallback
-> gộp điểm vector + keyword + boost metadata
-> rerank
-> load chunk đầy đủ từ PostgreSQL
-> mở rộng context liên quan nếu cần
-> lọc quyền truy cập, lọc nhiễu, dedupe
-> build prompt
-> LLM sinh câu trả lời
-> lưu message, citation, log
```

## 1. Các file chính trong pipeline retrieval

### `backend/app/api/routes/chat.py`

Đây là lớp API nhận request chat từ giao diện.

File này không trực tiếp đi tìm chunk. Nó nhận câu hỏi, thông tin session, top_k, candidate_k, chế độ stream hoặc non-stream rồi gọi vào `RagAnswerService`.

Các luồng chính:

- chat thường gọi `service.answer(...)`;
- chat stream gọi `service.answer_stream(...)`.

### `backend/app/services/rag_answer_service.py`

Đây là trung tâm của pipeline hỏi đáp.

File này điều phối gần như toàn bộ luồng:

- lưu câu hỏi vào chat session;
- kiểm tra câu hỏi có nằm ngoài phạm vi RAG không;
- rewrite câu hỏi;
- phân loại chiến lược retrieval;
- gọi artifact-first hoặc hybrid retrieval;
- load chunk từ PostgreSQL;
- mở rộng context;
- build prompt;
- gọi LLM;
- lưu câu trả lời, citation và log.

Nếu muốn debug vì sao chatbot trả lời sai hoặc lấy quá nhiều context, đây là file cần xem đầu tiên.

### `backend/app/services/query_rewrite_service.py`

File này phục vụ bước rewrite câu hỏi.

Ví dụ người dùng hỏi tiếp trong hội thoại:

```text
Vậy người ký là ai?
```

Hệ thống có thể rewrite thành câu rõ hơn dựa trên session/memory:

```text
Người ký văn bản 6515/EVNCPC-VTCNTT+KD+KT là ai?
```

Mục đích là làm câu truy vấn retrieval tự đứng riêng được, tránh phụ thuộc quá nhiều vào câu hỏi trước.

### `backend/app/services/query_strategy.py`

File này phân loại câu hỏi để chọn cách retrieval phù hợp hơn.

Một câu hỏi có thể được gắn các strategy như:

- `exact_lookup`: hỏi theo mã, số hiệu, định nghĩa, truy xuất chính xác;
- `overview_summary`: hỏi tổng quan, cấu trúc, nội dung chính;
- `count_list`: hỏi số lượng, danh sách, bao gồm những gì;
- `table_detail`: hỏi bảng, dòng, cột, trường, thuộc tính;
- `comparison`: hỏi so sánh;
- `procedure`: hỏi quy trình, các bước;
- `calculation`: hỏi tính toán;
- `multi_hop`: hỏi cần nối nhiều phần thông tin;
- `semantic_search`: mặc định nếu không rơi vào nhóm trên.

Strategy này ảnh hưởng tới top_k, candidate_k, query enrichment và giới hạn context.

### `backend/app/services/artifact_first_retrieval.py`

Đây là lớp retrieval ưu tiên artifact trước.

Artifact có thể hiểu là các mảnh tri thức đã được trích xuất có cấu trúc hơn chunk thô. Ví dụ:

- thông tin hành chính văn bản;
- fact về bảng;
- nhiệm vụ/người phụ trách;
- thông tin có cấu trúc khác.

Luồng artifact-first hiện tại:

```text
câu hỏi
-> build query contract
-> tìm artifact phù hợp
-> nếu artifact đủ tin cậy thì dùng artifact
-> nếu artifact chưa đủ hoặc không có thì fallback sang chunk retrieval
```

Điểm quan trọng: artifact-first không thay thế hoàn toàn chunk retrieval. Nó chỉ là lớp ưu tiên. Nếu không tìm được artifact tốt, hệ thống vẫn fallback về hybrid search trên chunk.

### `backend/app/services/reranking_service.py`

File này chạy pipeline tìm candidate và rerank.

Luồng chính:

```text
query
-> hybrid search lấy candidate_k kết quả
-> nếu bật graph thì mở rộng thêm candidate liên quan
-> load full content từ PostgreSQL
-> lọc quyền truy cập nếu có user
-> gửi candidate cho reranker
-> sort lại theo rerank_score + fused_score
-> lấy top_k kết quả cuối
```

Nếu reranker lỗi, hệ thống không dừng hẳn. Nó fallback về thứ hạng hybrid đã fuse trước đó.

### `backend/app/services/hybrid_search.py`

Đây là nơi gộp kết quả vector search và keyword search.

Luồng chính:

```text
query
-> gọi vector search
-> gọi keyword search
-> lấy nhiều hơn top_k theo depth = top_k * 3
-> fuse bằng RRF
-> cộng thêm các boost theo metadata / exact match
-> trả về danh sách candidate
```

Hybrid search hiện tại dùng cả:

- semantic vector search;
- keyword/BM25 search;
- exact lexical match;
- metadata boost;
- identifier boost;
- structured row/table boost;
- enrichment boost nếu bật.

### `backend/app/services/vector_indexing_service.py` và `backend/app/services/vector_store.py`

Đây là phần tìm kiếm vector qua Qdrant.

Nội dung chunk đã được embedding và lưu vector trong Qdrant. Khi retrieval, câu hỏi được embedding, sau đó Qdrant trả về các chunk gần nghĩa nhất.

Vector search mạnh với các câu hỏi diễn đạt tự nhiên, ví dụ:

```text
Văn bản nói gì về mục tiêu triển khai GIS?
```

Nhưng vector search có thể yếu hơn với các câu hỏi cần khớp chính xác mã số, tên trường, số hiệu hoặc tên riêng.

### `backend/app/services/elasticsearch_keyword_search.py`

Đây là keyword search chính nếu bật Elasticsearch.

Elasticsearch dùng BM25 và các field keyword/metadata để tìm theo chữ, cụm từ, mã văn bản, tên cột, tên bảng, số hiệu...

File này có cơ chế:

```text
Elasticsearch search thành công
-> dùng kết quả Elasticsearch

Elasticsearch lỗi và cho phép fallback
-> fallback về PostgreSQL keyword search
```

Vì vậy khi log có dòng:

```text
Elasticsearch keyword search failed; falling back to PostgreSQL keyword search.
```

thì nghĩa là keyword search vẫn chạy tiếp bằng PostgreSQL, nhưng chất lượng/tốc độ có thể khác so với Elasticsearch.

### `backend/app/services/keyword_search.py`

Đây là keyword search fallback bằng PostgreSQL.

Nó dùng full-text search PostgreSQL và exact match trên content/metadata. Khi Elasticsearch không dùng được, hệ thống vẫn có thể tìm theo keyword trong bảng `chunks`.

### `backend/app/services/rag_interaction_logger.py`

File này ghi log câu hỏi, câu trả lời và thông tin retrieval.

Log giúp kiểm tra:

- câu hỏi gốc;
- câu retrieval sau rewrite;
- strategy;
- top_k/candidate_k;
- context chunks được đưa vào LLM;
- source flags;
- selected artifacts;
- citations;
- latency;
- câu trả lời cuối.

## 2. Pipeline retrieval từ đầu đến cuối

### Bước 1: Người dùng hỏi trên giao diện

Người dùng đặt câu hỏi trong UI, ví dụ:

```text
Ai là người ký văn bản 6515?
```

Frontend gửi request tới API chat ở backend. Request thường có:

- `query`: câu hỏi;
- `session_id`: phiên chat nếu có;
- `top_k`: số kết quả muốn lấy sau rerank;
- `candidate_k`: số candidate trước rerank;
- `answer_mode`, `answer_style`;
- thông tin user nếu đã đăng nhập;
- danh sách tài liệu được phép truy cập nếu có scope.

### Bước 2: API chuyển request vào `RagAnswerService`

API không tự retrieval. Nó gọi:

```text
RagAnswerService.answer(...)
```

hoặc với stream:

```text
RagAnswerService.answer_stream(...)
```

Từ đây, `RagAnswerService` bắt đầu điều phối toàn bộ pipeline.

### Bước 3: Tạo hoặc lấy chat session

Hệ thống lấy session hiện tại hoặc tạo session mới.

Sau đó lưu message của người dùng vào database:

```text
role = user
content = câu hỏi
```

Việc lưu này giúp hệ thống có lịch sử hội thoại, citation và log về sau.

### Bước 4: Kiểm tra scope câu hỏi

Hệ thống gọi `classify_query_scope(query)`.

Mục đích là xem câu hỏi có cần retrieval không. Có một số câu hỏi có thể trả lời trực tiếp, ví dụ câu hỏi ngoài phạm vi tài liệu hoặc câu hỏi chào hỏi.

Nếu có direct answer, hệ thống trả lời ngay và không chạy retrieval.

Nếu cần dùng dữ liệu tài liệu, pipeline đi tiếp.

### Bước 5: Rewrite câu hỏi để retrieval tốt hơn

Hệ thống gọi query rewrite.

Câu hỏi người dùng đôi khi ngắn hoặc phụ thuộc ngữ cảnh hội thoại. Rewrite giúp biến nó thành câu truy vấn rõ hơn.

Ví dụ:

```text
Câu hỏi gốc:
Người ký là ai?

Câu retrieval:
Người ký văn bản 6515/EVNCPC-VTCNTT+KD+KT là ai?
```

Pipeline sau đó có 2 biến quan trọng:

- `retrieval_query`: câu dùng để tìm kiếm;
- `evidence_query`: câu dùng để kiểm tra bằng chứng và build context.

Thông thường chúng giống nhau, nhưng trong một số trường hợp hệ thống giữ câu gốc để tránh rewrite làm lệch ý.

### Bước 6: Phân loại query strategy

Hệ thống gọi `classify_query_strategy(evidence_query)`.

Strategy giúp hệ thống hiểu câu hỏi thuộc dạng nào:

```text
exact_lookup
overview_summary
count_list
table_detail
comparison
procedure
calculation
multi_hop
semantic_search
```

Ví dụ:

- hỏi "ai ký văn bản 6515" thường là `exact_lookup`;
- hỏi "phụ lục 2 có mục tiêu gì" có thể là `overview_summary` hoặc `semantic_search`;
- hỏi "cột CPCIT cần làm gì" là `table_detail`;
- hỏi "có bao nhiêu table" là `count_list` + có thể liên quan `table_detail`.

### Bước 7: Enrich query nếu câu hỏi cần thêm ngữ cảnh tìm kiếm

Nếu strategy yêu cầu overview, table detail hoặc comparison, hệ thống có thể thêm các search terms vào retrieval query.

Các từ này chỉ dùng để tìm kiếm, không được xem là dữ kiện trả lời.

Ví dụ query có thể được bổ sung các cụm như:

```text
table summary
table header
row
column
field
heading outline
section summary
```

Mục đích là kéo về các chunk tổng quan, chunk bảng hoặc chunk heading tốt hơn.

### Bước 8: Điều chỉnh top_k, candidate_k và giới hạn context

Người dùng hoặc frontend có thể gửi `top_k` và `candidate_k`, nhưng hệ thống có thể điều chỉnh lại theo strategy.

Ví dụ:

- câu hỏi overview có thể cần nhiều context hơn;
- câu hỏi table detail có thể cần lấy nhiều dòng/cột hơn;
- câu hỏi semantic đơn giản có thể dùng top_k nhỏ hơn.

Các biến quan trọng:

- `top_k`: số kết quả cuối sau rerank;
- `candidate_k`: số candidate đưa vào reranker;
- `effective_top_k`: top_k sau khi hệ thống điều chỉnh;
- `effective_candidate_k`: candidate_k sau khi hệ thống điều chỉnh;
- `max_context_chars`: giới hạn context ban đầu;
- `effective_max_context_chars`: giới hạn context sau điều chỉnh.

### Bước 9: Tạo access filter nếu có user đăng nhập

Nếu có user, hệ thống tạo `subject_context` và `access_filter`.

Mục đích:

- người dùng chỉ thấy tài liệu được phép;
- chunk không hợp lệ quyền truy cập bị lọc;
- log truy cập tài liệu được ghi lại.

Nếu user đã đăng nhập mà không có danh sách document scope phù hợp, hệ thống có thể báo lỗi vì không biết người đó được xem tài liệu nào.

### Bước 10: Chạy artifact-first retrieval

Hệ thống gọi:

```text
_retrieve_artifact_first_or_rerank(...)
```

Nếu `ArtifactFirstRetrievalService` khả dụng, hệ thống thử tìm artifact trước.

Artifact-first làm:

```text
query
-> build query contract
-> xác định intent, loại artifact ưu tiên, threshold
-> tìm artifact exact trong PostgreSQL
-> tìm artifact vector nếu cần
-> kiểm tra artifact có đủ tin cậy không
-> nếu đủ thì dùng artifact
-> nếu chưa đủ thì fallback sang chunk retrieval
```

`query_contract` quyết định những thứ như:

- intent của câu hỏi;
- loại artifact ưu tiên;
- có cho fallback sang chunk không;
- có cho neighbor expansion không;
- token budget retrieval.

### Bước 11: Nếu artifact không đủ, fallback sang chunk reranking

Nếu artifact không đủ hoặc chức năng artifact-first tắt, hệ thống gọi `RerankingService.search(...)`.

Đây là luồng chunk retrieval chính.

```text
query
-> hybrid search
-> candidate_k kết quả
-> reranker
-> top_k kết quả
```

### Bước 12: Hybrid search gọi song song hai hướng tìm kiếm

Hybrid search gồm hai nhánh chính.

Nhánh 1 là vector search:

```text
query
-> embedding
-> Qdrant
-> chunk gần nghĩa
```

Nhánh 2 là keyword search:

```text
query
-> Elasticsearch BM25 nếu bật
-> nếu Elasticsearch lỗi thì PostgreSQL full-text fallback
-> chunk khớp chữ / cụm từ / metadata
```

Hệ thống lấy nhiều candidate hơn top_k bằng công thức:

```text
depth = top_k * 3
```

Ví dụ `candidate_k = 20`, hybrid có thể lấy sâu hơn trong vector và keyword trước khi fuse.

### Bước 13: Fuse vector và keyword bằng RRF

Kết quả vector và keyword được gộp bằng RRF.

Hiểu đơn giản:

- chunk xuất hiện càng cao trong vector search thì được cộng điểm;
- chunk xuất hiện càng cao trong keyword search thì được cộng điểm;
- nếu chunk xuất hiện ở cả hai nguồn thì càng mạnh;
- điểm cuối là `fused_score`.

Mỗi chunk có thể có `source_flags`:

- `vector`: đến từ Qdrant vector search;
- `keyword`: đến từ Elasticsearch/PostgreSQL keyword search;
- `lexical_exact`: có exact match hoặc boost metadata/chữ chính xác;
- `graph`: nếu được thêm từ graph expansion ở reranking;
- `neighbor`: nếu được thêm ở bước mở rộng context;
- `artifact`: nếu đến từ source chunk của artifact.

### Bước 14: Cộng thêm boost theo metadata và exact match

Sau RRF, hệ thống cộng thêm một số boost.

Các boost quan trọng:

```text
identifier_exact_match_boost
schema_or_procedure_metadata_boost
structured_row_metadata_boost
enrichment_metadata_boost
membership_boost
```

Ý nghĩa dễ hiểu:

- nếu câu hỏi có mã văn bản/số hiệu và chunk chứa đúng mã đó, chunk được đẩy lên;
- nếu câu hỏi hỏi bảng/schema/procedure và metadata chunk phù hợp, chunk được đẩy lên;
- nếu chunk là row/table và khớp từ khóa trong câu hỏi, chunk được đẩy lên;
- nếu enrichment được bật và metadata enrichment khớp, chunk được đẩy lên.

Đây là lý do một số chunk có `source_flags = lexical_exact` dù không nhất thiết chỉ đến từ keyword search.

### Bước 15: Reranker chấm lại candidate

Sau hybrid search, hệ thống load full content của candidate từ PostgreSQL rồi đưa vào reranker.

Reranker nhận:

```text
query + danh sách candidate content
```

Reranker trả về điểm phù hợp từng chunk với câu hỏi.

Sau đó hệ thống sort theo:

```text
exact priority nếu là identifier lookup
-> rerank_score
-> fused_score
```

Cuối cùng lấy `top_k` chunk.

Nếu reranker lỗi, hệ thống fallback về ranking từ hybrid search.

### Bước 16: Load chunk đầy đủ từ PostgreSQL

Kết quả rerank chỉ là danh sách id và preview. Hệ thống phải load lại chunk đầy đủ từ PostgreSQL.

PostgreSQL là nơi lưu:

- `chunks.content`;
- `chunks.metadata`;
- `document_id`;
- `chunk_index`;
- `token_count`;
- `enriched_content`.

Đây cũng là lý do chunk hiển thị trên giao diện thường lấy từ PostgreSQL.

### Bước 17: Load thêm source chunk của artifact

Nếu artifact-first chọn được artifact, artifact có thể trỏ tới các source chunk gốc.

Hệ thống load thêm các chunk này và đưa vào context với flag:

```text
artifact
```

Mục đích là nếu trả lời dựa trên artifact, LLM vẫn có bằng chứng nguồn từ chunk gốc.

### Bước 18: Lọc quyền truy cập lần nữa

Sau khi load context, hệ thống lọc lại bằng access control.

Lý do là context có thể được thêm từ nhiều nguồn:

- rerank;
- artifact source chunks;
- neighbor expansion;
- augment context.

Vì vậy hệ thống lọc nhiều lần để tránh đưa chunk không được phép vào prompt.

### Bước 19: Mở rộng context bằng neighbor nếu được phép

Nếu setting bật `enable_context_expansion` và `query_contract` cho phép, hệ thống mở rộng context.

Các kiểu mở rộng chính:

```text
entity coverage chunks
table neighbor chunks
article neighbor chunks
```

Ví dụ:

- nếu lấy được một `table_row`, hệ thống có thể lấy thêm `table_parent`, `table_group` hoặc các dòng/cột liên quan cùng bảng;
- nếu lấy được một chunk thuộc `Điều 1`, hệ thống có thể lấy thêm chunk cùng điều;
- nếu câu hỏi cần overview, hệ thống có thể lấy thêm chunk bao phủ nhiều entity/heading hơn.

Đây là bước giúp LLM có thêm ngữ cảnh, nhưng cũng là nơi có thể làm context phình to nếu không kiểm soát kỹ.

### Bước 20: Dedupe và lọc context theo identifier

Sau mở rộng, hệ thống:

```text
deduplicate context chunks
-> lọc identifier context
```

`dedupe` để tránh cùng một chunk xuất hiện nhiều lần từ nhiều nguồn.

`filter_identifier_context` dùng cho câu hỏi có mã/số hiệu. Mục tiêu là ưu tiên chunk có liên quan trực tiếp tới identifier, tránh kéo nhầm tài liệu gần nghĩa.

### Bước 21: Augment context cho các trường hợp đặc biệt

Pipeline hiện tại có một số bước augment thêm:

```text
_augment_person_area_context
_augment_legal_leave_context
```

Tên `_augment_legal_leave_context` là tên cũ để tương thích, nhưng logic hiện tại không còn chỉ phục vụ một loại tài liệu pháp lý cụ thể. Nó tìm các row/section facts trực tiếp hơn dựa trên search terms từ câu hỏi.

Mục đích chung của các bước augment:

- kéo thêm chunk có fact trực tiếp;
- bổ sung bảng/dòng/section bị retrieval ban đầu bỏ sót;
- hỗ trợ câu hỏi về người, đơn vị, khu vực, bảng hoặc dữ liệu có cấu trúc.

### Bước 22: Kiểm tra context có liên quan không

Trước khi gọi LLM, hệ thống kiểm tra context/artifact có còn liên quan chủ đề với câu hỏi không.

Nếu không có context phù hợp, hệ thống không cố bịa. Nó trả lời theo hướng thiếu ngữ cảnh hoặc không đủ bằng chứng.

Có các nhánh như:

```text
không có context
-> trả lời không tìm thấy ngữ cảnh phù hợp

câu hỏi cần direct evidence nhưng context không có bằng chứng trực tiếp
-> trả lời không đủ bằng chứng
```

### Bước 23: Build prompt cho LLM

Khi đã có context, hệ thống gọi `_build_user_prompt(...)`.

Prompt thường gồm:

- câu hỏi gốc;
- standalone/evidence query;
- context chunks;
- artifact nếu có;
- session context;
- memory context nếu bật;
- summary hội thoại nếu có;
- query strategy;
- query contract.

Sau đó gọi:

```text
LLM provider.generate(...)
```

hoặc với stream:

```text
LLM provider.stream_generate(...)
```

### Bước 24: LLM sinh câu trả lời

LLM chỉ nên trả lời dựa trên context đã đưa vào.

Sau khi có câu trả lời, hệ thống làm sạch output bằng `_clean_llm_answer(...)`.

### Bước 25: Lưu assistant message và citations

Hệ thống lưu câu trả lời vào chat messages:

```text
role = assistant
content = answer
```

Sau đó tạo citations từ các context chunks đã dùng.

Citation liên kết:

- assistant message;
- chunk_id;
- document_id;
- quote ngắn từ chunk;
- page_number nếu metadata có.

### Bước 26: Ghi log retrieval và RAG interaction

Cuối cùng hệ thống ghi log qua `log_rag_interaction(...)`.

Log hiện tại rất quan trọng để debug. Nó có thể chứa:

- câu hỏi gốc;
- câu retrieval sau rewrite;
- evidence query;
- top_k/candidate_k;
- effective_top_k/effective_candidate_k;
- max_context_chars/effective_max_context_chars;
- query strategy;
- query contract;
- rewrite result;
- rerank response;
- context chunks;
- selected artifacts;
- artifact result;
- citations;
- latency;
- answer.

Ngoài ra `RerankingService` cũng lưu retrieval logs riêng gồm:

- vector results;
- keyword results;
- hybrid results;
- reranked results.

## 3. Vector, keyword, hybrid khác nhau thế nào

### Vector search

Vector search tìm theo ý nghĩa.

Ví dụ người dùng hỏi:

```text
Mục tiêu của phụ lục 2 là gì?
```

Dù chunk không chứa y nguyên cụm "mục tiêu của phụ lục 2", vector search vẫn có thể tìm ra đoạn có ý nghĩa liên quan.

Điểm mạnh:

- tốt với câu hỏi diễn đạt tự nhiên;
- tốt khi người dùng dùng từ khác tài liệu.

Điểm yếu:

- có thể nhầm nếu nhiều văn bản gần giống nhau;
- không mạnh bằng keyword với mã số, tên trường, số hiệu, tên riêng.

### Keyword search

Keyword search tìm theo chữ.

Ví dụ:

```text
TenKhachHang
6515/EVNCPC-VTCNTT+KD+KT
CPCIT
Phụ lục 02
```

Điểm mạnh:

- tốt với mã, tên riêng, tên cột, số hiệu;
- tốt với exact match;
- dễ debug hơn vector.

Điểm yếu:

- nếu OCR lỗi hoặc người dùng hỏi bằng từ đồng nghĩa thì có thể hụt.

### Hybrid search

Hybrid search kết hợp cả hai.

Ý tưởng:

```text
vector bắt ý nghĩa
keyword bắt chữ chính xác
metadata boost bắt cấu trúc
reranker chọn lại kết quả tốt nhất
```

Đây là hướng phù hợp với tài liệu DOFFICE vì dữ liệu vừa có:

- văn bản hành chính;
- bảng;
- mã số;
- phụ lục;
- tên cột;
- nội dung OCR/Markdown/HTML;
- câu hỏi vừa tổng quan vừa chi tiết.

## 4. Các `source_flags` thường gặp

### `vector`

Chunk được lấy từ Qdrant vector search.

Nghĩa là chunk gần nghĩa với câu hỏi theo embedding.

### `keyword`

Chunk được lấy từ keyword search.

Nếu Elasticsearch bật và chạy tốt, nguồn keyword thường là Elasticsearch. Nếu Elasticsearch lỗi, kết quả keyword có thể đến từ PostgreSQL fallback.

### `lexical_exact`

Chunk có dấu hiệu khớp chính xác với câu hỏi hoặc được boost bằng metadata.

Ví dụ:

- khớp mã văn bản;
- khớp tên trường;
- khớp tên bảng;
- khớp cụm từ exact;
- metadata row/table/schema phù hợp.

### `graph`

Chunk được thêm từ graph retrieval nếu bật `use_graph`.

### `neighbor`

Chunk không nhất thiết nằm trong top rerank ban đầu, nhưng được thêm vào vì liên quan gần với chunk đã chọn.

Ví dụ cùng bảng, cùng điều, cùng article number hoặc cùng nhóm entity.

### `artifact`

Chunk là source chunk của artifact được chọn ở artifact-first retrieval.

## 5. Vì sao context có thể nhiều hơn top_k

`top_k` chỉ là số kết quả cuối sau rerank ban đầu.

Nhưng sau đó hệ thống còn có thể thêm:

- source chunks của artifact;
- neighbor chunks cùng bảng;
- neighbor chunks cùng điều/article;
- entity coverage chunks;
- augmented structured row chunks;
- person/area context;
- legal/table fact context.

Vì vậy câu hỏi gửi `top_k = 5` không có nghĩa prompt cuối chắc chắn chỉ có 5 chunk.

Đây là điểm cần chú ý khi debug tốc độ LLM. Nếu context sau cùng quá lớn, LLM sẽ:

- đọc chậm hơn;
- tốn token hơn;
- dễ bị nhiễu hơn;
- có nguy cơ trả lời lẫn thông tin từ chunk không cần thiết.

## 6. PostgreSQL, Qdrant và Elasticsearch đóng vai trò gì trong retrieval

### PostgreSQL

PostgreSQL là nguồn lưu dữ liệu chính của chunk.

Nó lưu:

- document;
- chunk content;
- chunk metadata;
- enriched content;
- chat messages;
- citations;
- retrieval logs;
- knowledge artifacts nếu có.

Khi giao diện hiển thị chunk, thường là đang xem dữ liệu từ PostgreSQL.

### Qdrant

Qdrant lưu vector embedding của chunk/artifact.

Nó phục vụ semantic search:

```text
câu hỏi -> embedding -> tìm vector gần nhất
```

Qdrant không phải nơi đọc nội dung chunk cuối cùng. Nó chủ yếu trả về id và score. Nội dung đầy đủ vẫn được load lại từ PostgreSQL.

### Elasticsearch

Elasticsearch lưu index keyword/BM25 của chunk.

Nó phục vụ tìm theo chữ:

- mã văn bản;
- tên riêng;
- tên cột;
- cụm từ chính xác;
- metadata searchable;
- content/enriched content.

Nếu Elasticsearch lỗi và bật fallback, hệ thống dùng PostgreSQL keyword search.

## 7. Retrieval hiện tại tốt ở đâu

Pipeline hiện tại có nhiều điểm mạnh:

- không phụ thuộc hoàn toàn vào vector search;
- có keyword/BM25 để bắt mã số, tên trường, tên bảng;
- có reranker để chọn lại candidate tốt hơn;
- có metadata boost cho bảng/schema/procedure;
- có artifact-first để ưu tiên dữ kiện có cấu trúc;
- có access control;
- có log chi tiết để debug;
- có neighbor expansion để không mất ngữ cảnh bảng/điều/mục.

## 8. Điểm cần lưu ý hoặc dễ gây lỗi

### 8.1. Context có thể phình to sau rerank

Sau rerank, hệ thống còn mở rộng context. Nếu không giới hạn tốt, một câu hỏi đơn giản có thể đưa rất nhiều chunk vào LLM.

Ví dụ câu hỏi hành chính như:

```text
Ai là người ký văn bản 6515?
```

đáng ra chỉ cần `document_header` hoặc artifact hành chính. Nhưng nếu mã văn bản xuất hiện trong preamble của nhiều chunk, retrieval có thể kéo thêm nhiều `table_row`.

### 8.2. Mã văn bản xuất hiện trong mọi chunk có thể gây nhiễu

Do chunk có preamble như:

```text
Văn bản: 6515/...
```

nên khi hỏi theo mã văn bản, nhiều chunk cùng match mã đó.

Điều này tốt để không lấy nhầm tài liệu, nhưng cũng có mặt trái: nó làm mọi chunk của cùng văn bản đều có vẻ liên quan.

Với câu hỏi hành chính, nên ưu tiên `document_header` hoặc admin artifact hơn là kéo toàn bộ chunk bảng.

### 8.3. Elasticsearch lỗi thì fallback được, nhưng chất lượng có thể khác

Nếu Elasticsearch báo lỗi, hệ thống fallback về PostgreSQL keyword search.

Điều này giúp hệ thống vẫn chạy, nhưng ranking keyword có thể khác so với Elasticsearch.

### 8.4. Reranker không phải lúc nào cũng cứu được retrieval

Reranker chỉ xếp hạng lại các candidate đã được lấy về.

Nếu candidate ban đầu thiếu chunk đúng, reranker không thể chọn chunk đúng.

Vì vậy chất lượng phụ thuộc cả:

- query rewrite;
- vector search;
- keyword search;
- metadata boost;
- candidate_k;
- index có đủ dữ liệu không;
- chunking có giữ đúng nội dung không.

## 9. Cách kiểm tra pipeline retrieval khi debug

### 9.1. Xem log RAG interaction

File log Markdown dễ đọc:

```text
log/rag_chat_logs.md
```

Nên kiểm tra:

- câu hỏi gốc;
- retrieval query;
- evidence query;
- query strategy;
- effective_top_k;
- effective_candidate_k;
- context chunks;
- selected artifacts;
- source_flags;
- câu trả lời cuối.

### 9.2. Xem retrieval logs trong database

Nếu cần sâu hơn, xem retrieval log trong PostgreSQL để biết:

- vector trả về gì;
- keyword trả về gì;
- hybrid fuse ra sao;
- reranker chọn gì.

### 9.3. Xem chunk gốc trong PostgreSQL

Ví dụ kiểm tra chunk được đưa vào context:

```sql
select
    c.id,
    c.document_id,
    c.chunk_index,
    c.metadata->>'chunk_type' as chunk_type,
    c.metadata->>'table_name' as table_name,
    c.metadata->>'section_title' as section_title,
    c.content
from chunks c
where c.id = 'chunk-id-can-kiem-tra';
```

### 9.4. So sánh source flags

Nếu chunk đúng chỉ có `vector`, nghĩa là nó được tìm nhờ semantic.

Nếu chunk đúng có `keyword` hoặc `lexical_exact`, nghĩa là keyword/metadata đang hỗ trợ tốt.

Nếu chunk đúng chỉ xuất hiện nhờ `neighbor`, nghĩa là retrieval ban đầu chưa lấy trực tiếp chunk đó, nhưng expansion đã cứu lại.

Nếu chunk đúng không xuất hiện ở đâu, cần kiểm tra lại:

- chunking có tạo chunk đúng không;
- PostgreSQL có chunk không;
- Qdrant đã index chưa;
- Elasticsearch đã index chưa;
- candidate_k có quá thấp không;
- query rewrite có làm lệch câu hỏi không.

## 10. Tóm tắt một dòng

Pipeline retrieval hiện tại của HBRag là:

```text
Chat API
-> RagAnswerService
-> scope check
-> query rewrite
-> query strategy
-> artifact-first retrieval
-> hybrid vector + keyword search
-> metadata/exact boost
-> reranker
-> load chunk từ PostgreSQL
-> artifact/neighbor/context expansion
-> access filter + dedupe + relevance check
-> build prompt
-> LLM answer
-> citations + logs
```

Nói đơn giản: chunking quyết định dữ liệu được chia và lưu như thế nào; retrieval quyết định khi người dùng hỏi thì hệ thống kéo những mảnh nào lên, xếp hạng ra sao, bổ sung ngữ cảnh thế nào và đưa bao nhiêu context vào LLM.
