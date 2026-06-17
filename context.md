# Pipeline hiện tại của Enterprise Chatbot

Tài liệu này mô tả pipeline mới nhất của chatbot theo ngôn ngữ tự nhiên, tập trung vào cách hệ thống ingest dữ liệu, hiểu câu hỏi, truy xuất tài liệu, rerank, tạo context, sinh câu trả lời, ghi nhớ phiên hội thoại và ghi log để debug.

Mục tiêu chính của hệ thống là trả lời câu hỏi dựa trên tài liệu nội bộ với độ chính xác cao, hạn chế bịa thông tin, xử lý được nhiều kiểu văn bản khác nhau, và giảm phụ thuộc vào keyword hard-code. LLM được dùng để hiểu ý định và hỗ trợ truy xuất, còn các rule còn lại chủ yếu là fallback hoặc cơ chế bảo vệ khi LLM lỗi.

---

## 1. Tổng quan kiến trúc

Hệ thống gồm hai pipeline lớn:

1. Pipeline offline: ingest và lập chỉ mục tài liệu.
2. Pipeline online: nhận câu hỏi người dùng, truy xuất tài liệu phù hợp, sinh câu trả lời và ghi log.

Luồng tổng quát:

```text
PDF trong thư mục data
    -> chuyển PDF thành markdown/text
    -> tách thành các chunk văn bản, bảng, dòng bảng, nhóm bảng, mục/tiểu mục
    -> trích xuất metadata và entity
    -> tạo embedding
    -> lưu vào Elasticsearch

Người dùng hỏi
    -> backend nhận câu hỏi
    -> lấy memory theo session
    -> kiểm tra cache
    -> rewrite câu hỏi nếu là câu hỏi nối tiếp
    -> LLM router phân tích ý định, phạm vi, entity, constraint
    -> retrieval theo tài liệu trước, rồi mới theo chunk
    -> rerank bằng cross-encoder
    -> mở rộng context nếu cần danh sách/bảng/thống kê
    -> build prompt
    -> LLM sinh câu trả lời hoặc stream token
    -> lưu memory, cache, log
```

Cách thiết kế hiện tại ưu tiên accuracy hơn tốc độ cực nhanh. Những điểm tối ưu tốc độ được đặt ở cache, giới hạn candidate trước rerank, tái sử dụng embedding, route theo intent và chỉ mở rộng context khi thật sự cần.

---

## 2. Các thành phần chính

### `backend.py`

Đây là lớp API. Backend nhận request từ frontend, tạo `session_id`, gọi pipeline trả lời trong `rag.py`, sau đó ghi log.

Các endpoint quan trọng:

- `/chat`: trả lời một lần sau khi xử lý xong.
- `/chat/stream`: stream câu trả lời để người dùng thấy phản hồi sớm hơn, giảm cảm giác chờ.

Backend không tự quyết định logic retrieval. Nó chủ yếu điều phối request/response và gắn log cho từng lượt hỏi.

Các endpoint test dùng cho Google Sheet:

- `/test-one`: test một dòng, có thể nhận `document_name`. Nếu có `document_name`, backend tự chuyển câu hỏi thành dạng `Trong tài liệu "...", ...` trước khi gọi RAG. Mặc định `use_memory=false`, nghĩa là không dùng memory/cache theo session để điểm test công bằng hơn. Endpoint này chấm điểm bằng LLM judge giống batch test, không chạy RAGAS.
- `/test-one-ragas`: test một dòng giống `/test-one` nhưng chạy thêm RAGAS để lấy các metric như faithfulness, answer relevancy, context precision/recall và answer correctness. Endpoint này dùng lazy import RAGAS, chỉ tải RAGAS khi được gọi.
- `/start-batch-test`: test nhiều dòng theo job nền. Request có thể truyền `document_name` ở cấp sheet. Từng testcase cũng có thể có `document_name` riêng, nhưng thông thường dùng tên worksheet làm `document_name`. Batch test luôn gọi RAG với `session_id=None` để từng câu độc lập, không bị câu trước hỗ trợ câu sau.
- `/batch-test-result/{job_id}`: lấy kết quả batch để ghi lại vào Google Sheet.

Quy ước test Google Sheet theo hướng document-scoped công bằng:

1. Mỗi worksheet tương ứng một tài liệu.
2. Tên worksheet nên khớp với `document_name` đã ingest.
3. Apps Script gửi `document_name = sheet.getName()` lên backend.
4. Backend tự thêm scope tài liệu vào câu hỏi nội bộ.
5. Không dùng memory trong test tự động; mỗi câu được chấm độc lập.
6. Câu hỏi gốc vẫn được giữ trong Google Sheet, còn `scoped_question` chỉ dùng nội bộ/debug.

### `frontend.py`

Frontend dùng Chainlit để tạo giao diện chat. Mỗi phiên chat có `session_id` riêng để memory và cache không bị lẫn giữa người dùng hoặc giữa các phiên hỏi khác nhau.

Frontend hỗ trợ streaming. Khi backend trả token dần, giao diện hiển thị từng phần câu trả lời thay vì đợi toàn bộ câu trả lời sinh xong.

### `rag.py`

Đây là bộ điều phối trung tâm của pipeline online. File này quyết định thứ tự xử lý:

1. lấy memory;
2. kiểm tra cache;
3. rewrite câu hỏi nếu cần;
4. gọi LLM router;
5. truy xuất tài liệu;
6. rerank;
7. build context;
8. gọi LLM trả lời;
9. lưu memory/cache/log.

Có thể xem `rag.py` như “xương sống” của chatbot.

### `ingest_data.py`

File này xử lý pipeline offline. Nó đọc PDF, chuyển nội dung thành markdown/text, tách chunk, nhận diện bảng, tạo metadata, embedding, rồi lưu vào Elasticsearch.

Chất lượng retrieval phụ thuộc rất lớn vào file này. Nếu ingest thiếu metadata, chia chunk sai, hoặc không lưu được cấu trúc bảng/mục, thì retrieval rất dễ tìm đúng tài liệu nhưng sai đoạn.

### `llm_router.py`

Đây là bộ định tuyến bằng LLM. Thay vì dựa nhiều vào keyword, hệ thống gửi câu hỏi cho LLM để phân tích:

- người dùng muốn hỏi loại gì;
- cần tìm theo tài liệu, người, bảng, mục, dòng hay nội dung;
- entity chính là gì;
- điều kiện ràng buộc là gì;
- có cần mở rộng bảng hoặc mở rộng section không.

Nếu LLM router lỗi hoặc trả JSON không hợp lệ, hệ thống mới dùng fallback rule cũ trong `intent.py`.

### `intent.py`

Đây là fallback router. File này vẫn còn tồn tại để phòng trường hợp LLM router lỗi, timeout hoặc trả kết quả không dùng được.

Mục tiêu hiện tại không phải xóa hoàn toàn `intent.py`, mà là để nó làm lớp an toàn cuối cùng. Bình thường pipeline ưu tiên LLM router.

### `retrieval.py`

Đây là phần tìm tài liệu và chunk phù hợp. Retrieval hiện tại không chỉ dùng một kiểu search, mà dùng nhiều nhánh phối hợp:

- tìm tài liệu ứng viên trước;
- vector search theo semantic similarity;
- BM25 lexical search;
- entity phrase search;
- author/person search;
- constraint search trong phạm vi tài liệu;
- section/table search;
- merge kết quả;
- rerank lại bằng cross-encoder;
- mở rộng context nếu câu hỏi cần danh sách, bảng hoặc thống kê.

Đây là file ảnh hưởng trực tiếp nhất tới việc chatbot có tìm đúng đoạn hay không.

### `llm.py`

File này cấu hình model sinh câu trả lời và prompt hệ thống. Prompt yêu cầu model chỉ trả lời dựa trên context truy xuất được, không tự bịa, không dùng memory làm bằng chứng, và nếu thiếu thông tin thì phải nói rõ.

File này cũng hỗ trợ streaming.

### `memory.py`

File này quản lý memory theo session:

- lưu recent turns;
- lưu summary khi cuộc hội thoại dài;
- lưu active anchors như tài liệu, tác giả, năm, loại văn bản, bảng gần nhất;
- lưu exact cache;
- lưu semantic cache có kiểm soát.

Memory giúp hệ thống hiểu câu hỏi nối tiếp như “còn mục đó thì sao”, “liệt kê tiếp”, “người này có bao nhiêu sáng kiến”.

### `question_rewrite.py`

File này rewrite câu hỏi nối tiếp thành câu hỏi độc lập hơn. Ví dụ người dùng hỏi “còn của bộ phận hành chính thì sao”, hệ thống sẽ dùng memory để viết lại thành câu hỏi đầy đủ hơn trước khi retrieval.

Rewrite chỉ nên chạy khi cần, vì gọi LLM thêm sẽ tăng thời gian phản hồi.

### `chat_logger.py`

File này ghi log hai dạng:

- `logs/chat_logs.jsonl`: log thô dạng JSONL, phù hợp để phân tích bằng code.
- `logs/chat_logs_readable.md`: log dễ đọc cho người phát triển.

Log hiện tại có nhiều thông tin debug quan trọng như route source, entity, constraint, document candidates, top sources, search type, timing và response.

---

## 3. Pipeline ingest dữ liệu

Pipeline ingest là bước chuẩn bị dữ liệu trước khi chatbot có thể trả lời. Đây là phần quyết định nền tảng chất lượng retrieval.

### Bước 1: Đọc PDF từ thư mục dữ liệu

Tài liệu nguồn nằm trong thư mục `enterprise_chatbot/data`. Mỗi file PDF được đọc và chuyển sang dạng text/markdown để xử lý tiếp.

Mục đích:

- lấy nội dung văn bản ra khỏi PDF;
- giữ lại càng nhiều cấu trúc càng tốt;
- chuẩn bị cho bước chia chunk và trích metadata.

### Bước 2: Chuyển PDF sang markdown/text

Hệ thống dùng hàm chuyển PDF thành markdown/text để giữ lại cấu trúc gần với tài liệu gốc hơn so với chỉ lấy plain text.

Việc giữ heading, bảng, dòng bảng và bố cục có ý nghĩa rất lớn. Nếu văn bản có bảng 36 dòng, hệ thống cần biết đó là một bảng, chứ không chỉ là nhiều đoạn text rời rạc.

### Bước 3: Nhận diện thông tin cấp tài liệu

Mỗi tài liệu được gắn metadata tổng quát như:

- mã tài liệu nội bộ;
- tên file;
- tiêu đề;
- chủ đề;
- loại văn bản;
- năm xuất hiện trong văn bản;
- các tag hỗ trợ tìm kiếm.

Metadata này giúp retrieval chọn đúng tài liệu trước khi chọn đúng chunk. Đây là điểm quan trọng để tránh lỗi “đúng câu hỏi nhưng lấy nhầm tài liệu”.

### Bước 4: Tách chunk theo nhiều loại nội dung

Hệ thống không chỉ chia văn bản thành các đoạn đều nhau. Nó cố gắng giữ cấu trúc tự nhiên của tài liệu.

Các loại chunk chính:

- chunk văn bản thường;
- chunk theo heading/mục/tiểu mục;
- chunk tổng quan bảng;
- chunk toàn bộ bảng;
- chunk nhóm trong bảng;
- chunk từng dòng bảng.

Mục đích là để chatbot xử lý được nhiều kiểu câu hỏi:

- hỏi một điều khoản cụ thể;
- hỏi nội dung của một mục;
- hỏi danh sách trong bảng;
- hỏi tổng số dòng;
- hỏi một người hoặc một đơn vị xuất hiện trong bảng;
- hỏi chi tiết của một khóa học, sáng kiến, hồ sơ, phụ lục hoặc biểu mẫu.

### Bước 5: Lưu cấu trúc mục, bảng và dòng

Với chunk văn bản, hệ thống cố gắng lưu các thông tin như:

- tiêu đề gần nhất;
- đường dẫn heading;
- nhãn trường nếu đoạn nằm dưới một trường thông tin;
- tên item nếu đoạn thuộc một item cụ thể.

Với chunk bảng, hệ thống lưu thêm:

- mã bảng;
- tiêu đề bảng;
- header của bảng;
- context xung quanh bảng;
- nội dung từng dòng;
- dữ liệu dòng ở dạng có cấu trúc;
- nhãn nhóm;
- mã dòng nếu phát hiện được;
- section chứa bảng.

Điểm quan trọng: hệ thống không nên phụ thuộc vào một từ cố định như “Phụ lục”. Nếu tài liệu khác dùng “Biểu mẫu”, “Danh mục”, “Bảng kê”, “Mục”, “Khoản”, “Chương” hoặc cách đặt tên khác, pipeline vẫn cần dựa trên cấu trúc và metadata tổng quát.

### Bước 6: Trích xuất entity

Trong lúc ingest, hệ thống trích các thực thể xuất hiện trong chunk, ví dụ:

- tên người;
- tên đơn vị;
- tên khóa học;
- tên sáng kiến;
- mã hồ sơ;
- năm;
- tên tài liệu;
- cụm danh từ quan trọng.

Các entity được lưu cả dạng gốc và dạng chuẩn hóa. Chuẩn hóa giúp so khớp tốt hơn khi người dùng gõ không dấu, viết tắt, viết hoa/thường khác nhau hoặc đặt câu khác văn bản gốc.

### Bước 7: Tạo embedding

Mỗi chunk được tạo embedding bằng model multilingual embedding. Query dùng tiền tố dạng `query:` và passage dùng tiền tố dạng `passage:` theo kiểu của embedding model.

Embedding giúp tìm những đoạn gần nghĩa dù không trùng từ khóa tuyệt đối.

### Bước 8: Lưu vào Elasticsearch

Elasticsearch lưu cả hai nhóm thông tin:

1. Trường phục vụ lexical search như content, heading, table title, entity, document name.
2. Vector embedding phục vụ semantic search.

Nhờ đó retrieval có thể kết hợp BM25 và vector search. Đây gọi là hybrid retrieval.

---

## 4. Pipeline trả lời câu hỏi online

Khi người dùng hỏi, hệ thống đi qua nhiều bước có kiểm soát.

### Bước 1: Nhận câu hỏi và session

Frontend gửi câu hỏi cùng `session_id` về backend. `session_id` dùng để phân biệt memory/cache giữa các phiên chat.

Nếu không có session riêng, câu hỏi của người này có thể bị ảnh hưởng bởi ngữ cảnh của người khác hoặc lượt hỏi trước không liên quan.

### Bước 2: Lấy memory của phiên hiện tại

Hệ thống lấy recent turns, summary và active anchors.

Memory giúp xử lý câu hỏi nối tiếp. Ví dụ:

- “người đó có bao nhiêu sáng kiến?”;
- “liệt kê tiếp”;
- “còn mục f thì sao?”;
- “trong tài liệu vừa rồi thì thời gian thế nào?”.

Memory không được dùng như bằng chứng để trả lời. Nó chỉ dùng để hiểu câu hỏi và định hướng retrieval.

### Bước 3: Kiểm tra exact cache

Nếu người dùng hỏi lại đúng câu đã hỏi trong cùng session, hệ thống có thể trả lại kết quả đã lưu ngay lập tức.

Exact cache có độ an toàn cao vì câu hỏi giống hệt nhau. Nó không làm giảm accuracy.

### Bước 4: Rewrite câu hỏi nếu cần

Nếu câu hỏi có dấu hiệu phụ thuộc ngữ cảnh trước đó, hệ thống rewrite thành câu hỏi độc lập hơn.

Ví dụ:

```text
Câu trước: Cho tôi biết khóa học Lập trình nâng cao Webportal trên nền tảng MS SharePoint.
Câu sau: Nội dung đào tạo gồm gì?

Câu rewrite: Nội dung đào tạo của khóa học Lập trình nâng cao Webportal trên nền tảng MS SharePoint gồm gì?
```

Rewrite giúp retrieval không bị thiếu entity chính.

### Bước 5: LLM router phân tích câu hỏi

Đây là bước rất quan trọng trong phiên bản hiện tại. Hệ thống gửi câu hỏi cho LLM router để phân tích ngữ nghĩa.

LLM router trả về thông tin dạng JSON, gồm:

- loại ý định của câu hỏi;
- phạm vi câu hỏi;
- nhu cầu trả lời;
- entity chính;
- entity cần lookup;
- loại entity;
- constraint;
- tham chiếu tài liệu (`document_reference`);
- trường thông tin người dùng muốn lấy;
- có cần mở rộng bảng không;
- có cần mở rộng section không.

Ví dụ, với câu hỏi:

```text
Nội dung đào tạo của khóa học Lập trình nâng cao Webportal trên nền tảng MS SharePoint gồm gì?
```

Router nên hiểu:

- entity chính là tên khóa học;
- constraint là mục “Nội dung đào tạo”;
- cần tìm đúng section nằm trong phạm vi khóa học;
- không được lấy nhầm mục “thời gian”, “địa điểm” hoặc khóa học khác.

`document_reference` giúp retrieval hiểu câu hỏi đang nhắm vào loại phạm vi tài liệu nào:

- `none`: không có tham chiếu tài liệu rõ ràng;
- `explicit_document`: câu hỏi nêu rõ tên/số/tiêu đề một tài liệu cụ thể;
- `current_document`: câu hỏi nối tiếp bằng “tài liệu này”, “văn bản này”, “quyết định này”, “phần đó”;
- `corpus_wide`: câu hỏi cần xét nhiều hoặc toàn bộ tài liệu.

### Bước 6: Semantic cache có kiểm soát

Semantic cache dùng embedding của câu hỏi mới để so với câu hỏi cũ trong cùng session. Nếu hai câu gần nghĩa và an toàn, hệ thống có thể dùng lại câu trả lời.

Tuy nhiên semantic cache không áp dụng bừa bãi. Các dạng câu hỏi rủi ro cao thường bị bỏ qua, ví dụ:

- câu hỏi danh sách;
- câu hỏi thống kê;
- câu hỏi so sánh;
- câu hỏi lọc theo thời gian;
- câu hỏi cần nguồn cụ thể;
- câu hỏi liên quan bảng hoặc tài chính.

Lý do là những câu hỏi gần nghĩa chưa chắc cùng yêu cầu. Ví dụ “Nguyễn Thị Tùng có những sáng kiến nào?” và “Nguyễn Thị Tùng có bao nhiêu sáng kiến?” gần nhau về semantic, nhưng một câu cần danh sách, một câu cần số lượng.

### Bước 7: Retrieval lần đầu

Hệ thống dùng kết quả từ router để tìm tài liệu và chunk phù hợp.

Điểm chính của retrieval hiện tại là “document-first có kiểm soát”. Nghĩa là hệ thống tìm tài liệu ứng viên trước, đánh giá độ tin cậy, rồi mới quyết định có khóa vào một tài liệu hay không.

Logic document-first gồm 3 chế độ:

- `none`: không tìm được tài liệu ứng viên đủ rõ. Hệ thống chạy global search bình thường trên toàn index.
- `soft`: có tài liệu ứng viên nhưng chưa đủ tin cậy để khóa. Hệ thống vẫn search toàn index, đồng thời search thêm trong top tài liệu ứng viên và cộng bonus cho chunk thuộc các tài liệu đó.
- `hard`: hệ thống đủ tin cậy rằng câu hỏi đang nhắm tới một tài liệu cụ thể. Khi đó hệ thống chỉ search chunk trong `document_id` đã khóa, không merge global search vào nữa.

Điều kiện vào `hard` không hard-code theo tên file cụ thể. Hệ thống dựa trên các tín hiệu chung:

- câu hỏi nhắc trực tiếp tên tài liệu;
- router nhận diện câu hỏi đang lookup tài liệu hoặc tiêu đề tài liệu;
- router đánh dấu `document_reference` là `explicit_document` hoặc `current_document`;
- tài liệu top 1 có điểm cao và cách biệt rõ với tài liệu top 2.

Ngưỡng hiện tại:

- lookup tài liệu: top 1 >= 50 điểm và top 1/top 2 >= 1.3;
- `explicit_document`: top 1 >= 55 điểm và top 1/top 2 >= 1.25;
- `current_document`: top 1 >= 60 điểm, top 1/top 2 >= 1.4, và router phải khôi phục được entity/tài liệu chính từ memory;
- fallback tham chiếu tài liệu bằng pattern chỉ chạy khi LLM router lỗi: top 1 >= 60 điểm và top 1/top 2 >= 1.4;
- tín hiệu tài liệu rất mạnh: top 1 >= 80 điểm và top 1/top 2 >= 1.8;
- nếu câu hỏi nhắc trực tiếp tên tài liệu thì khóa luôn vào tài liệu đó.

Với các câu hỏi mang tính toàn kho như đếm, liệt kê hoặc so sánh trên nhiều tài liệu, hệ thống không tự khóa cứng vào một tài liệu nếu không có tín hiệu tài liệu rõ ràng. Điều này tránh lỗi chỉ trả lời trong một file khi câu hỏi thật ra cần toàn bộ corpus.

Các pattern regex còn lại trong retrieval không phải đường chính. Chúng chỉ được dùng khi `route_source` không phải `llm`, tức là LLM router lỗi hoặc pipeline phải dùng fallback rule. Khi router chạy thành công, hệ thống tin `semantic_route` thay vì tự đoán entity/tác giả/tài liệu bằng keyword.

Các log liên quan:

- `document_candidates`: danh sách tài liệu ứng viên;
- `document_reference`: loại tham chiếu tài liệu do router trả về;
- `document_scope_mode`: `none`, `soft` hoặc `hard`;
- `document_lock_reason`: lý do hệ thống khóa hoặc không khóa tài liệu;
- `locked_document_id`, `locked_document_name`: tài liệu bị khóa khi ở chế độ `hard`;
- `document_scope_top_score`, `document_scope_second_score`: điểm top 1 và top 2 để kiểm tra độ cách biệt.

### Bước 8: Lazy rewrite và retry nếu retrieval yếu

Nếu retrieval lần đầu yếu, hệ thống có thể rewrite câu hỏi bằng memory rồi thử lại.

Đây gọi là lazy rewrite vì rewrite không chạy ngay cho mọi câu hỏi. Nó chỉ chạy khi cần, để tránh tăng thời gian phản hồi không cần thiết.

### Bước 9: Rerank kết quả

Sau khi có danh sách candidate từ nhiều nhánh search, hệ thống dùng reranker để sắp xếp lại.

Reranker đọc cặp “câu hỏi - candidate” và chấm điểm mức độ liên quan. Đây là bước quan trọng để chọn đúng đoạn trong nhiều đoạn gần giống nhau.

### Bước 10: Mở rộng context nếu cần

Nếu câu hỏi cần danh sách đầy đủ, thống kê, bảng hoặc nội dung của cả một mục, hệ thống không chỉ lấy một chunk top đầu. Nó sẽ mở rộng thêm các chunk liên quan trong cùng bảng, cùng section hoặc cùng tài liệu.

Mục tiêu là tránh lỗi trả lời thiếu, ví dụ văn bản có 36 hồ sơ nhưng chatbot chỉ lấy vài dòng đầu.

### Bước 11: Build context đưa vào LLM

Các chunk được chọn sẽ được chuyển thành context rõ ràng, có kèm metadata như:

- tên tài liệu;
- loại chunk;
- tiêu đề;
- heading;
- section;
- bảng;
- dòng bảng;
- entity match;
- nội dung.

Context càng rõ, LLM càng ít nhầm giữa các đoạn cùng tên hoặc cùng chủ đề.

### Bước 12: LLM sinh câu trả lời

LLM nhận system prompt, câu hỏi và context. Prompt yêu cầu:

- chỉ trả lời dựa trên context;
- không tự suy diễn khi thiếu bằng chứng;
- nếu không tìm thấy thì nói rõ;
- memory không phải nguồn chứng cứ;
- nêu nguồn theo tên tài liệu.

Nếu dùng streaming, token được trả dần về frontend.

### Bước 13: Lưu memory, cache và log

Sau khi trả lời xong, hệ thống lưu:

- câu hỏi;
- câu trả lời;
- route;
- nguồn đã dùng;
- timing;
- memory anchors;
- cache nếu phù hợp;
- log debug.

Log giúp đánh giá khi câu trả lời sai: sai do router, sai do retrieval, sai do rerank, sai do thiếu context hay sai do LLM diễn giải.

---

## 5. LLM router: hiểu câu hỏi thay vì bám keyword

Trước đây hệ thống có nhiều keyword để đoán câu hỏi thuộc loại nào. Cách đó nhanh nhưng không bao quát, vì người dùng có thể diễn đạt cùng một ý bằng rất nhiều cách khác nhau.

Phiên bản hiện tại ưu tiên LLM router.

### LLM router làm gì?

LLM router đọc câu hỏi và trả về cấu trúc điều hướng. Nó không trả lời câu hỏi cuối cùng. Nó chỉ giúp retrieval biết nên tìm theo hướng nào.

Các nhóm thông tin quan trọng:

### Ý định câu hỏi

Router xác định câu hỏi thuộc dạng nào, ví dụ:

- hỏi thông tin cụ thể;
- hỏi danh sách;
- hỏi số lượng/thống kê;
- hỏi so sánh;
- hỏi theo người/tác giả;
- hỏi theo đơn vị;
- hỏi theo mục/section;
- hỏi theo bảng;
- hỏi nguồn hoặc tài liệu.

Ý định giúp hệ thống quyết định có cần mở rộng context hay không. Câu hỏi danh sách và thống kê thường cần nhiều context hơn câu hỏi đơn lẻ.

### Entity chính

Entity chính là đối tượng trung tâm mà câu hỏi đang nói tới.

Ví dụ:

```text
Khóa học Lập trình nâng cao Webportal trên nền tảng MS SharePoint có nội dung đào tạo gì?
```

Entity chính là tên khóa học. Nếu retrieval không giữ entity chính này, nó có thể lấy nhầm nội dung đào tạo của khóa học khác.

### Constraint

Constraint là điều kiện hoặc phần cụ thể người dùng muốn hỏi trong phạm vi entity chính.

Ví dụ:

- “nội dung đào tạo”;
- “đối tượng tham gia”;
- “thời gian”;
- “địa điểm”;
- “mục f”;
- “bộ phận hành chính”;
- “năm 2025”.

Constraint không nhất thiết là keyword để search toàn cục. Nó nên được hiểu là điều kiện nằm trong phạm vi entity chính hoặc tài liệu chính.

### Requested fields

Requested fields là những trường người dùng muốn lấy ra, ví dụ:

- tên;
- số lượng;
- tác giả;
- thời hạn bảo quản;
- địa điểm;
- thời gian;
- nội dung;
- đối tượng.

Thông tin này giúp LLM trả lời đúng trọng tâm và giúp retrieval ưu tiên chunk chứa đúng trường.

### Có cần mở rộng bảng hoặc section không?

Router đánh dấu khi câu hỏi cần lấy nhiều dòng hoặc cả một mục.

Ví dụ:

- “liệt kê tất cả”;
- “bao gồm những gì”;
- “có bao nhiêu”;
- “danh mục gồm những hồ sơ nào”;
- “nội dung của mục này là gì”.

Nếu có dấu hiệu cần mở rộng, retrieval không nên chỉ lấy top 3 chunk.

---

## 6. Fallback intent: lớp an toàn khi LLM router lỗi

`intent.py` vẫn được giữ lại, nhưng không phải đường chính.

Nó dùng khi:

- LLM router timeout;
- LLM router trả JSON lỗi;
- LLM router thiếu trường quan trọng;
- hệ thống cần fallback tối thiểu để không hỏng toàn bộ pipeline.

Fallback này nên được viết theo hướng tổng quát nhất có thể. Không nên cố bao phủ mọi keyword, vì điều đó sẽ quay lại vấn đề cũ. Vai trò của fallback là đảm bảo hệ thống vẫn chạy được, không phải đạt chất lượng cao nhất.

Nếu sau này LLM router ổn định tuyệt đối, có thể giảm vai trò của `intent.py`, nhưng chưa nên xóa hẳn vì nó là lớp bảo vệ khi provider/model có sự cố.

---

## 7. Retrieval: tìm đúng tài liệu trước, đúng đoạn sau

Retrieval là phần quan trọng nhất để cải thiện lỗi “không tìm thấy”, “đúng tài liệu nhưng sai đoạn”, hoặc “lấy nhầm tài liệu”.

### Tư tưởng hiện tại

Không tìm chunk trực tiếp trên toàn bộ index ngay từ đầu. Thay vào đó, hệ thống cố xác định tài liệu ứng viên trước.

Quy trình:

```text
Câu hỏi + route từ LLM
    -> tìm document candidates
    -> quyết định document scope: none / soft / hard
    -> nếu hard: chỉ tìm chunk trong document_id đã khóa
    -> nếu soft/none: dùng global search hoặc kết hợp global + scoped search
    -> rerank
    -> mở rộng bảng/section nếu cần
```

Cách này giúp giảm nhiễu khi nhiều tài liệu có cùng từ như “đào tạo”, “nội dung”, “thời gian”, “danh mục”, “sáng kiến”, “EVNCPC”.

### Document candidate search

Hệ thống dùng entity chính, tên tài liệu, chủ đề và câu hỏi đã được làm sạch để tìm những tài liệu có khả năng liên quan nhất.

Ví dụ nếu người dùng hỏi về một khóa học cụ thể, document candidate search phải ưu tiên tài liệu chứa tên khóa học đó, không phải tài liệu khác cũng có từ “đào tạo”.

Kết quả document candidates được đưa vào retrieval để search có scope rõ hơn.

Với số hiệu văn bản, hệ thống tạo thêm biến thể tổng quát để tránh lệch do cách viết khác nhau. Ví dụ:

```text
03/QĐ-HĐTV
03-QĐ-HĐTV
03 qd hdtv
3 qd hdtv
quyết định số 3 qd hdtv
```

Các biến thể này được boost mạnh ở `document_name_norm`, `document_name_text` và `title`. Mục tiêu là đưa đúng tài liệu lên rank cao trước khi quyết định `hard/soft`.

### Hybrid retrieval

Retrieval hiện tại dùng nhiều nhánh search song song hoặc gần song song:

1. Vector search.
2. BM25 search.
3. Entity phrase search.
4. Author/person search.
5. Constraint search trong phạm vi tài liệu.
6. Section/table search.
7. Scoped search theo document candidates.

Không nhánh nào là hoàn hảo. Kết hợp nhiều nhánh giúp bù điểm yếu cho nhau.

Với câu hỏi không thật sự cần bảng, retrieval không tự cộng điểm mạnh cho `table_full` hoặc `table_overview` chỉ vì câu hỏi có dạng liệt kê. Table chỉ được ưu tiên khi router đánh dấu phạm vi bảng, hàng bảng, table expansion hoặc section expansion. Điều này giảm lỗi bị kéo vào bảng tổng hợp không liên quan khi câu hỏi thật ra cần đoạn quy định dạng text.

### Vector search

Vector search dùng embedding để tìm chunk gần nghĩa với câu hỏi.

Ưu điểm:

- hiểu được câu hỏi khác chữ nhưng cùng nghĩa;
- hữu ích khi người dùng diễn đạt tự nhiên;
- giảm phụ thuộc keyword.

Nhược điểm:

- có thể lấy đoạn gần nghĩa nhưng sai đối tượng;
- dễ nhầm nếu nhiều tài liệu cùng chủ đề.

Vì vậy vector search cần đi kèm document scope và rerank.

### BM25 search

BM25 là tìm kiếm lexical. Nó tốt khi câu hỏi có cụm từ xuất hiện rõ trong tài liệu.

Ưu điểm:

- mạnh với tên riêng, mã hồ sơ, số hiệu, cụm từ chính xác;
- nhanh;
- dễ giải thích.

Nhược điểm:

- không hiểu tốt nếu người dùng diễn đạt khác văn bản;
- phụ thuộc vào token và cách tách từ.

### Entity phrase search

Entity phrase search ưu tiên các chunk chứa entity chính hoặc entity lookup chính xác.

Ví dụ:

- tên khóa học;
- tên người;
- tên đơn vị;
- tên sáng kiến;
- tên hồ sơ.

Nhánh này giúp tránh việc chỉ match constraint như “nội dung đào tạo” mà quên mất khóa học nào.

Khi LLM router chạy thành công, entity phrase search chỉ dùng entity do router trả về trong `semantic_route`. Fallback trích cụm trong dấu ngoặc kép chỉ chạy khi router lỗi hoặc route không đến từ LLM.

### Author/person search

Với câu hỏi về người hoặc tác giả, retrieval dùng các trường tác giả/entity đã chuẩn hóa để tìm chính xác hơn.

Ví dụ:

```text
Nguyễn Thị Tùng có bao nhiêu sáng kiến?
Tác giả Nguyễn Thị Tùng có những sáng kiến nào?
```

Hai câu này gần nhau nhưng yêu cầu khác nhau. Retrieval cần tìm đúng người, còn phần trả lời cần dựa vào intent để biết trả danh sách hay số lượng.

Đường chính hiện tại là router quyết định `lookup_entity_type` là `person` hoặc `author`, sau đó retrieval dùng `lookup_entities` từ router để search trong `author_aliases_norm`. Các pattern đoán tên người trong câu hỏi chỉ còn là fallback khi LLM router lỗi.

### Constraint search trong scope

Constraint chỉ nên được dùng mạnh sau khi đã có scope.

Ví dụ:

```text
Nội dung đào tạo của khóa học A là gì?
```

“Nội dung đào tạo” là constraint. Nếu search toàn cục bằng constraint này, hệ thống dễ lấy mục nội dung đào tạo của khóa học B. Vì vậy retrieval cần giữ khóa học A làm primary entity rồi mới tìm constraint trong phạm vi đó.

Với constraint dài, hệ thống không chỉ search nguyên câu. Nó tách thành các cụm thông tin cao dạng n-gram để bắt đúng bằng chứng hơn.

Ví dụ constraint:

```text
trình cơ quan nhà nước có thẩm quyền chấp thuận chủ trương đầu tư đối với dự án
```

có thể sinh các cụm như:

```text
trinh co quan nha nuoc tham quyen
chap thuan chu truong dau tu
chu truong dau tu doi voi du an
```

Log source có thêm `matched_constraint` để kiểm tra chunk đang bám đúng constraint nào.

Ngoài việc biết chunk match constraint nào, hệ thống còn tính mức độ bao phủ constraint:

- `constraint_match_count`: số constraint của câu hỏi mà chunk match được;
- `constraint_match_total`: tổng số constraint cần xét;
- `constraint_match_ratio`: tỷ lệ bao phủ;
- `matched_constraints`: danh sách constraint đã match.

Candidate match được nhiều constraint cùng lúc sẽ được cộng điểm trong rank fusion, prefilter trước rerank và rerank score. Nếu câu hỏi không yêu cầu bảng, text chunk match nhiều constraint sẽ được ưu tiên hơn, còn `table_group`, `table_full`, `table_overview` bị trừ nhẹ để tránh bảng tổng hợp lấn át đoạn giải thích/ngưỡng/định nghĩa.

### Section soft search

Section soft search tìm các mục/tiểu mục theo nghĩa và theo nhãn cấu trúc.

Nó dùng nhiều trường:

- heading title;
- heading path;
- section title;
- section path;
- group label;
- field label;
- item title;
- table title;
- table context;
- row text;
- content.

Điểm quan trọng là không chỉ tìm đúng chữ. Nếu người dùng hỏi hơi khác cách viết trong tài liệu, hệ thống vẫn cần tìm được section gần nghĩa.

### Rank fusion

Vì nhiều nhánh search có thể trả về cùng một chunk, hệ thống merge kết quả theo chunk id. Khi merge, nó cộng hoặc giữ lại các tín hiệu như:

- chunk này đến từ nhánh nào;
- điểm search ban đầu;
- matched entity;
- document scope rank;
- search type.

Một chunk xuất hiện ở nhiều nhánh thường đáng tin hơn chunk chỉ xuất hiện ở một nhánh yếu.

---

## 8. Rerank: chọn đoạn tốt nhất sau khi retrieval rộng

Sau hybrid retrieval, hệ thống có nhiều candidate. Reranker sẽ chấm lại mức liên quan giữa câu hỏi và từng candidate.

Model reranker hiện tại là cross-encoder. Cross-encoder đọc trực tiếp cặp câu hỏi và đoạn văn, nên thường chính xác hơn embedding similarity thuần.

### Vì sao cần rerank?

Vector search có thể tìm đoạn gần nghĩa nhưng sai entity. BM25 có thể tìm đoạn trùng chữ nhưng sai ngữ cảnh. Rerank giúp chọn đoạn cân bằng hơn giữa nghĩa, entity và constraint.

### Candidate text cho reranker

Candidate đưa vào reranker được rút gọn, không quá dài. Điều này giúp giảm thời gian chấm điểm nhưng vẫn giữ thông tin chính:

- tên tài liệu;
- heading;
- bảng/section;
- entity;
- nội dung chính.

Nếu đưa candidate quá dài, reranker chậm và có thể bị nhiễu.

### Các tín hiệu cộng điểm bổ sung

Ngoài điểm reranker, hệ thống còn thêm các tín hiệu nghiệp vụ:

- chunk thuộc tài liệu ứng viên tốt;
- chunk chứa entity chính;
- chunk chứa tác giả/người được hỏi;
- chunk phù hợp với intent do LLM router phân tích;
- chunk vừa match entity chính vừa match section/constraint;
- chunk thuộc loại bảng khi câu hỏi cần danh sách hoặc thống kê.

Các tín hiệu này không thay thế reranker. Chúng giúp reranker không bị lệch khi các đoạn có câu chữ na ná nhau.

### Primary-scoped section rerank

Đây là cải tiến quan trọng cho lỗi “đúng tài liệu nhưng sai mục”.

Khi câu hỏi có entity chính và section constraint, hệ thống ưu tiên chunk thỏa cả hai điều kiện:

- đúng entity chính;
- đúng section/constraint người dùng hỏi.

Ví dụ:

```text
Nội dung đào tạo của khóa học A gồm gì?
```

Chunk chỉ chứa khóa học A nhưng nói về thời gian/địa điểm không nên đứng đầu. Chunk chỉ nói “nội dung đào tạo” nhưng của khóa học B cũng không nên đứng đầu. Chunk tốt nhất phải nằm ở giao giữa khóa học A và mục nội dung đào tạo.

Nếu người dùng nêu mục cụ thể như “mục f”, hệ thống cũng ưu tiên đúng marker đó để tránh lấy nhầm mục e hoặc mục khác có tiêu đề gần giống.

---

## 9. Mở rộng context: tránh trả lời thiếu

Một lỗi phổ biến của RAG là retrieval lấy đúng vài dòng nhưng thiếu toàn bộ bảng hoặc toàn bộ section. Với câu hỏi cần danh sách đầy đủ, vài chunk top đầu thường không đủ.

Vì vậy hệ thống có bước expand.

### Khi nào cần expand?

Expand thường cần khi câu hỏi có dạng:

- “liệt kê tất cả”;
- “bao gồm những gì”;
- “có bao nhiêu”;
- “danh mục gồm những hồ sơ nào”;
- “các sáng kiến của đơn vị X”;
- “nội dung của mục Y”;
- “thống kê theo đơn vị/năm/loại”.

### Expand bảng

Nếu chunk liên quan thuộc một bảng, hệ thống có thể lấy thêm:

- chunk tổng quan bảng;
- chunk toàn bộ bảng;
- các dòng trong cùng bảng;
- nhóm bảng liên quan.

Mục tiêu là khi văn bản có 36 dòng, chatbot có đủ dữ liệu để trả đủ 36 dòng thay vì chỉ vài dòng.

### Expand section

Nếu câu hỏi nhắm vào một mục hoặc tiểu mục, hệ thống có thể lấy thêm chunk trong cùng section.

Điều này giúp câu trả lời đầy đủ hơn, nhất là với văn bản hành chính có cấu trúc mục a, b, c hoặc I, II, III.

---

## 10. Build context cho LLM

Sau retrieval và expand, hệ thống build context thành dạng dễ đọc cho LLM.

Mỗi đoạn context nên có đủ metadata để LLM hiểu vị trí của đoạn trong tài liệu:

- tên tài liệu;
- loại chunk;
- tiêu đề;
- heading path;
- section;
- bảng;
- dòng bảng;
- entity match;
- nội dung.

Context không chỉ là text thô. Nó là gói bằng chứng có cấu trúc.

Điều này giúp LLM phân biệt:

- cùng một cụm “nội dung đào tạo” nhưng thuộc khóa học nào;
- cùng một tên người nhưng là tác giả hay người áp dụng;
- cùng một đơn vị nhưng trong bảng nào;
- dòng bảng thuộc nhóm nào;
- số lượng là tổng trong điều khoản hay chỉ số dòng được retrieval.

---

## 11. Prompt sinh câu trả lời

Prompt trong `llm.py` yêu cầu model trả lời dựa trên context.

Nguyên tắc chính:

1. Chỉ dùng thông tin có trong context được truy xuất.
2. Không tự bịa khi thiếu dữ liệu.
3. Nếu không đủ context, nói rõ là chưa tìm thấy hoặc tài liệu được truy xuất chưa đủ thông tin.
4. Không dùng memory làm nguồn chứng cứ.
5. Trả lời bằng tiếng Việt rõ ràng, ngắn gọn nhưng đủ ý.
6. Nêu nguồn theo tên tài liệu.

Điều này quan trọng vì RAG có hai loại lỗi:

- retrieval không đưa đủ context;
- LLM tự suy diễn ngoài context.

Prompt giúp giảm lỗi thứ hai, còn retrieval/ingest giúp giảm lỗi thứ nhất.

---

## 12. Memory và cache

Memory và cache giúp chatbot nhanh hơn và hiểu hội thoại tốt hơn, nhưng phải dùng cẩn thận để không làm sai câu trả lời.

### Recent turns

Recent turns lưu các lượt hỏi đáp gần nhất trong session.

Dùng để hiểu câu hỏi nối tiếp, ví dụ:

```text
Câu 1: Cho tôi biết khóa học A.
Câu 2: Nội dung đào tạo gồm gì?
```

Câu 2 cần dựa vào câu 1 để biết “khóa học A”.

### Summary

Khi hội thoại dài, hệ thống tóm tắt bớt các lượt cũ để giữ memory gọn.

Summary giúp không phải lưu toàn bộ lịch sử quá dài, tránh làm prompt phình to.

### Active anchors

Active anchors là các thông tin đang “được nhắc tới” trong phiên chat, như:

- tài liệu gần nhất;
- tác giả/người gần nhất;
- năm gần nhất;
- loại tài liệu;
- bảng gần nhất;
- intent gần nhất.

Chúng hỗ trợ rewrite và route câu hỏi nối tiếp.

### Exact cache

Exact cache lưu câu trả lời cho câu hỏi giống hệt trong cùng session.

Ưu điểm:

- rất nhanh;
- an toàn;
- không làm sai nếu câu hỏi y hệt.

### Semantic cache

Semantic cache so embedding của câu hỏi mới với câu hỏi cũ. Nếu rất giống và thuộc loại an toàn, có thể dùng lại câu trả lời.

Nhưng semantic cache bị giới hạn với các câu hỏi rủi ro cao. Lý do là gần nghĩa không đồng nghĩa với cùng yêu cầu trả lời.

Ví dụ:

```text
Tác giả Nguyễn Thị Tùng có những sáng kiến nào?
Nguyễn Thị Tùng có bao nhiêu sáng kiến?
```

Hai câu gần nhau, nhưng một câu cần danh sách, một câu cần số lượng. Nếu dùng cache sai, chatbot trả rất nhanh nhưng sai mục tiêu.

---

## 13. Ghi log và debug

Hệ thống ghi log để biết chính xác lỗi nằm ở đâu.

### `chat_logs.jsonl`

Đây là log thô dạng JSONL. Mỗi dòng là một lượt hỏi đáp. File này phù hợp để phân tích bằng script.

Các trường quan trọng:

- câu hỏi gốc;
- câu hỏi đã rewrite;
- câu trả lời;
- tổng thời gian xử lý;
- thời gian retrieval/rerank/LLM;
- route source;
- route confidence;
- primary entities;
- constraints;
- document candidates;
- selected sources;
- search type;
- rerank score;
- retrieval control.

### `chat_logs_readable.md`

Đây là bản log dễ đọc hơn cho người phát triển. Khi test, nên mở file này trước vì nhìn nhanh hơn JSONL.

### Các thông tin debug quan trọng

Khi câu trả lời sai, nên kiểm tra theo thứ tự:

1. Router hiểu đúng câu hỏi chưa?
2. Primary entity có đúng không?
3. Constraint có đúng không?
4. Document candidates có đúng tài liệu không?
5. Top sources có nằm trong đúng tài liệu không?
6. Top sources có đúng section/bảng không?
7. Rerank score có đẩy nhầm đoạn lên đầu không?
8. Context đưa vào LLM có đủ bằng chứng không?
9. LLM có trả ngoài context không?

Nếu sai tài liệu, thường lỗi ở document candidate search hoặc entity extraction.

Nếu đúng tài liệu nhưng sai đoạn, thường lỗi ở section/constraint retrieval hoặc rerank.

Nếu đúng tài liệu đúng đoạn nhưng trả thiếu, thường cần expand bảng/section hoặc ingest đang chia chunk chưa đủ tốt.

Nếu context đúng mà câu trả lời sai, cần chỉnh prompt hoặc kiểm tra model sinh trả lời.

---

## 14. Cách đọc lỗi thường gặp

### Lỗi “không tìm thấy”

Có thể do:

- câu hỏi thiếu entity chính;
- router không nhận ra primary entity;
- document candidate search không đưa đúng tài liệu vào top;
- chunk chứa câu trả lời chưa được ingest tốt;
- metadata section/table bị thiếu;
- threshold retrieval/rerank quá chặt;
- câu hỏi dùng cách diễn đạt khác xa tài liệu gốc.

Hướng xử lý tổng quát:

- kiểm tra log xem document candidates có đúng không;
- nếu sai tài liệu, cải thiện document-level retrieval;
- nếu đúng tài liệu nhưng không có chunk đúng, kiểm tra ingest và section metadata;
- nếu chunk đúng có xuất hiện nhưng bị xếp thấp, chỉnh rerank/bonus;
- nếu không chunk nào chứa nội dung, cần reingest hoặc cải thiện parser.

### Lỗi “đúng tài liệu nhưng sai mục”

Có thể do:

- constraint bị search toàn cục thay vì trong phạm vi entity chính;
- section title không được lưu rõ;
- bảng/heading bị tách khỏi nội dung;
- reranker ưu tiên đoạn có từ giống câu hỏi nhưng sai entity.

Hướng xử lý:

- dùng primary-scoped section retrieval;
- đảm bảo entity chính và constraint được giữ cùng nhau;
- tăng tín hiệu cho chunk match cả entity chính và section;
- giảm ưu tiên chunk chỉ match một phần.

### Lỗi “trả lời thiếu danh sách”

Có thể do:

- retrieval chỉ lấy vài row đầu;
- câu hỏi không được route là dạng list/table/statistic;
- expand bảng chưa lấy đủ table_full hoặc table rows;
- ingest không lưu đủ dòng bảng;
- context limit quá nhỏ.

Hướng xử lý:

- kiểm tra `requires_table_expansion` và `requires_section_expansion`;
- kiểm tra top source có table_id không;
- lấy thêm table_full/table_group/table_row;
- nếu index cũ chưa có table metadata tốt, cần reingest.

### Lỗi “trả lời nhanh nhưng sai do cache”

Có thể do semantic cache hit nhầm.

Hướng xử lý:

- không dùng semantic cache cho câu hỏi rủi ro cao;
- tăng threshold semantic cache;
- kiểm tra intent và answer_need trước khi cache hit;
- chỉ cache khi câu hỏi thật sự cùng yêu cầu trả lời.

---

## 15. Khi nào cần chạy lại ingest?

Không phải thay đổi nào cũng cần reingest.

### Không cần reingest khi sửa

- prompt LLM;
- LLM router;
- retrieval logic;
- rerank bonus;
- logging;
- memory/cache;
- frontend/backend.

Các thay đổi này dùng index hiện có.

### Cần reingest khi sửa

- cách parse PDF;
- cách chia chunk;
- cách nhận diện bảng;
- metadata được lưu vào index;
- field mới trong Elasticsearch;
- cách tạo entity/author/table/section metadata;
- mapping index.

Nếu index cũ thiếu section/table metadata, sửa retrieval không đủ. Lúc đó phải ingest lại để index có dữ liệu tốt hơn.

### Lưu ý về ingest file cũ

Nếu pipeline ingest có cơ chế bỏ qua file đã ingest theo `file_path`, muốn cập nhật metadata/chunk cho tài liệu cũ thì cần xóa index cũ, tạo index mới hoặc ép reingest.

---

## 16. Cân bằng tốc độ và độ chính xác

Hệ thống hiện tại chọn hướng cân bằng:

- dùng cache cho câu hỏi an toàn;
- dùng LLM router để giảm hard-code;
- document-first retrieval có 3 chế độ `none`, `soft`, `hard` để tránh sai tài liệu nhưng vẫn giữ fallback khi chưa đủ tin cậy;
- hybrid search để không phụ thuộc một kỹ thuật;
- rerank để giữ accuracy;
- expand có điều kiện để tránh thiếu context;
- streaming để giảm cảm giác chờ.

Nếu muốn nhanh hơn nữa, có thể tối ưu:

- giảm số candidate trước rerank;
- cache embedding query;
- chỉ gọi rewrite khi retrieval yếu;
- chỉ expand bảng/section khi route yêu cầu;
- stream sớm;
- tách model router nhỏ hơn nếu có model phù hợp.

Nếu muốn chính xác hơn nữa, có thể tối ưu:

- tăng chất lượng ingest;
- lưu section/table hierarchy tốt hơn;
- cải thiện entity extraction;
- thêm document-level index riêng;
- đánh giá retrieval bằng bộ câu hỏi chuẩn;
- log top-k đầy đủ để xem chunk đúng có nằm trong candidate không.

---

## 17. Nguyên tắc phát triển tiếp

1. Ưu tiên sửa ingest nếu dữ liệu trong index thiếu hoặc sai cấu trúc.
2. Ưu tiên sửa retrieval nếu tài liệu đúng nhưng chunk sai.
3. Ưu tiên sửa rerank nếu chunk đúng có trong candidate nhưng bị xếp thấp.
4. Ưu tiên sửa prompt nếu context đúng nhưng LLM trả lời sai.
5. Ưu tiên sửa cache nếu phản hồi quá nhanh nhưng không chính xác.
6. Không thêm keyword hard-code cho từng tài liệu cụ thể nếu có thể giải quyết bằng metadata, entity, LLM router hoặc cấu trúc chung.
7. Luôn đọc log trước khi sửa, vì cùng một triệu chứng “không tìm thấy” có thể đến từ nhiều nguyên nhân khác nhau.

---

## 18. Checklist debug nhanh

Khi test một câu hỏi bị sai, kiểm tra theo thứ tự này:

```text
1. Câu hỏi gốc là gì?
2. Câu hỏi có bị rewrite không? Rewrite đúng không?
3. route_source là llm hay fallback?
4. primary_entities có đúng đối tượng chính không?
5. constraints có đúng phần cần hỏi không?
6. document_candidates có đúng tài liệu không?
7. document_scope_mode là none, soft hay hard?
8. Nếu hard, locked_document_name có đúng tài liệu không?
9. top sources thuộc tài liệu nào?
10. search_type của top sources là gì?
11. top sources có đúng section/bảng/dòng không?
12. rerank score có bất thường không?
13. context đưa vào LLM có đủ bằng chứng không?
14. response có bám context không?
```

Nếu làm theo checklist này, thường sẽ xác định được lỗi nằm ở router, retrieval, rerank, ingest hay LLM answer.

---

## 19. Tóm tắt ngắn gọn pipeline hiện tại

Pipeline hiện tại có thể hiểu đơn giản như sau:

1. Ingest cố giữ cấu trúc tài liệu: heading, section, bảng, dòng bảng, entity và metadata.
2. Khi người dùng hỏi, hệ thống lấy memory để hiểu ngữ cảnh phiên chat.
3. Cache được kiểm tra trước, nhưng semantic cache chỉ dùng khi an toàn.
4. LLM router phân tích câu hỏi thành entity, constraint, intent và nhu cầu context.
5. Retrieval chọn tài liệu ứng viên trước; nếu đủ tin cậy thì khóa vào một `document_id`, nếu chưa đủ thì giữ global fallback.
6. Hybrid search kết hợp semantic search, lexical search, entity search và section/table search.
7. Reranker chọn lại các chunk tốt nhất bằng cả điểm model và tín hiệu nghiệp vụ.
8. Nếu câu hỏi cần danh sách, thống kê hoặc bảng, hệ thống mở rộng context để tránh thiếu dữ liệu.
9. LLM chỉ được trả lời dựa trên context đã truy xuất.
10. Mọi lượt hỏi được ghi log để debug nguyên nhân khi có lỗi.

Định hướng chính của hệ thống là: ít hard-code hơn, nhiều hiểu ngữ nghĩa hơn, nhưng vẫn giữ fallback và log đủ sâu để kiểm soát chất lượng.
