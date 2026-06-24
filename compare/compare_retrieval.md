# So sánh pipeline retrieval giữa HBRag hiện tại và rag_research

Tài liệu này so sánh phần retrieval của hai dự án:

- Dự án hiện tại: `HBRag`
- Dự án tham khảo: `rag_research`

Mục tiêu không phải chọn một bên thắng tuyệt đối, mà là hiểu rõ mỗi dự án đang làm retrieval theo hướng nào, ưu nhược điểm ra sao, và HBRag hiện tại có thể học được gì từ `rag_research` để cải thiện chất lượng trả lời.

## 1. Nhận xét tổng quan

Hai dự án có triết lý retrieval khá khác nhau.

HBRag hiện tại giống một hệ thống production hơn. Nó có PostgreSQL để lưu document/chunk/metadata/chat/citation, Qdrant để lưu vector, Elasticsearch để tìm keyword/BM25, có access control, có artifact-first retrieval, có log và citation tương đối đầy đủ.

`rag_research` giống một phòng thí nghiệm retrieval hơn. Nó tập trung nhiều vào việc hiểu câu hỏi trước khi tìm kiếm: dùng LLM router để phân tích intent, entity, constraint, document scope, rồi mới quyết định tìm tài liệu/chunk theo hướng nào.

Có thể hiểu ngắn gọn:

```text
HBRag hiện tại:
Production-oriented retrieval
-> dữ liệu/chunk/index/log/access/citation chắc hơn

rag_research:
Research-oriented retrieval
-> routing, document scope, entity/constraint search thông minh hơn
```

Vì vậy không nên thay HBRag bằng `rag_research`. Hướng tốt hơn là giữ nền HBRag hiện tại, rồi chọn lọc một số ý tưởng retrieval tốt từ `rag_research`.

## 2. Pipeline retrieval của HBRag hiện tại

Pipeline retrieval hiện tại của HBRag có thể mô tả như sau:

```text
Người dùng hỏi
-> API chat nhận request
-> RagAnswerService xử lý
-> kiểm tra scope câu hỏi
-> rewrite câu hỏi nếu cần
-> phân loại query strategy bằng rule
-> điều chỉnh top_k/candidate_k/context limit
-> artifact-first retrieval nếu bật
-> nếu artifact chưa đủ thì fallback sang chunk retrieval
-> hybrid search: Qdrant vector + Elasticsearch/PostgreSQL keyword
-> fuse điểm vector + keyword bằng RRF
-> cộng boost theo metadata/exact match
-> reranker chấm lại candidate
-> load chunk đầy đủ từ PostgreSQL
-> thêm source chunk của artifact nếu có
-> mở rộng neighbor/table/article/entity context nếu cần
-> dedupe, lọc quyền truy cập, lọc identifier/context
-> build prompt
-> LLM sinh câu trả lời
-> lưu assistant message, citation và log
```

Các thành phần chính:

- `rag_answer_service.py`: điều phối pipeline hỏi đáp.
- `query_rewrite_service.py`: rewrite câu hỏi.
- `query_strategy.py`: phân loại dạng câu hỏi bằng rule.
- `artifact_first_retrieval.py`: thử lấy artifact trước chunk thô.
- `reranking_service.py`: gọi hybrid search, reranker, trả top_k.
- `hybrid_search.py`: gộp vector search và keyword search.
- `vector_indexing_service.py`, `vector_store.py`: tìm bằng Qdrant.
- `elasticsearch_keyword_search.py`: tìm keyword bằng Elasticsearch.
- `keyword_search.py`: PostgreSQL keyword fallback.
- `rag_interaction_logger.py`: ghi log câu hỏi, câu trả lời và context.

Điểm đáng chú ý là HBRag dùng nhiều lớp lưu trữ rõ ràng:

```text
PostgreSQL: document, chunk, metadata, chat, citation, artifact
Qdrant: vector embedding
Elasticsearch: keyword/BM25 index
```

Đây là nền tốt cho một hệ thống dùng lâu dài.

## 3. Pipeline retrieval của rag_research

Pipeline retrieval của `rag_research` có thể mô tả như sau:

```text
Người dùng hỏi
-> backend nhận request
-> lấy memory theo session
-> kiểm tra exact cache / semantic cache
-> rewrite sớm nếu câu hỏi nối tiếp
-> LLM router phân tích câu hỏi
-> tạo semantic_route: intent, scope, entity, constraint, document_reference
-> tìm document candidates
-> quyết định document scope: none / soft / hard
-> nếu hard: chỉ search trong tài liệu đã khóa
-> nếu soft: search global + search trong tài liệu ứng viên + boost
-> nếu none: search toàn kho
-> chạy nhiều nhánh retrieval:
   vector search
   BM25 search
   entity phrase search
   author/person alias search
   constraint search trong scope
   section/table search
-> merge kết quả theo chunk_id
-> cộng điểm theo document scope, entity, constraint, chunk type
-> prefilter candidate trước reranker
-> rerank bằng cross-encoder
-> mở rộng bảng/section nếu intent cần
-> build context
-> LLM sinh câu trả lời
-> lưu memory, cache và log
```

Các thành phần chính:

- `rag.py`: điều phối pipeline online.
- `llm_router.py`: dùng LLM để phân tích câu hỏi.
- `intent.py`: fallback rule khi LLM router lỗi.
- `retrieval.py`: document-first retrieval, nhiều nhánh search, rerank, expansion.
- `question_rewrite.py`: rewrite câu hỏi nối tiếp.
- `memory.py`: lưu memory, active anchors, cache.
- `llm.py`: build prompt và gọi LLM.
- `chat_logger.py`: ghi log JSONL và Markdown dễ đọc.

Điểm mạnh nhất của `rag_research` nằm ở bước trước retrieval: nó cố hiểu câu hỏi rất kỹ trước khi tìm chunk.

LLM router trả về các thông tin như:

```json
{
  "intent": "question_answer",
  "question_scope": "table_level",
  "answer_need": "direct_answer",
  "document_reference": "explicit_document",
  "primary_entities": ["6515/EVNCPC-VTCNTT+KD+KT"],
  "lookup_entities": ["Phụ lục 02"],
  "constraints": [
    {"type": "section", "value": "Mục tiêu"}
  ],
  "requires_table_expansion": false,
  "requires_section_expansion": true
}
```

Nhờ vậy retrieval không chỉ tìm chunk gần nghĩa, mà biết:

- đang hỏi tài liệu nào;
- đang hỏi entity nào;
- điều kiện phụ là gì;
- có cần mở rộng bảng/section không;
- nên tìm global hay khóa vào một tài liệu.

## 4. Khác biệt lớn nhất giữa hai dự án

### 4.1. HBRag mạnh ở nền production

HBRag có kiến trúc dữ liệu rõ hơn:

```text
PostgreSQL + Qdrant + Elasticsearch
```

Nó cũng có:

- access control;
- citation;
- artifact-first retrieval;
- log RAG interaction;
- fallback Elasticsearch sang PostgreSQL;
- chunk metadata phong phú;
- pipeline DOFFICE chuyên biệt.

Điều này làm HBRag phù hợp để vận hành thật hơn.

### 4.2. rag_research mạnh ở hiểu câu hỏi

`rag_research` mạnh ở phần routing:

```text
LLM router
-> intent
-> entity
-> constraint
-> document_reference
-> document scope
```

Nó giúp retrieval bám đúng tài liệu và đúng phần trong tài liệu hơn.

Ví dụ với câu:

```text
Mục tiêu của Phụ lục 02 là gì?
```

`rag_research` có xu hướng phân tích:

- constraint 1: `Phụ lục 02`;
- constraint 2: `Mục tiêu`;
- nếu câu hỏi nối tiếp thì dùng memory để biết đang nói về tài liệu nào;
- sau đó search trong document scope phù hợp.

HBRag hiện tại có query strategy nhưng chưa tách rõ entity chính và constraint phụ như vậy.

### 4.3. HBRag có artifact-first, rag_research có document-first

HBRag có artifact-first:

```text
Tìm artifact có cấu trúc trước
-> nếu đủ tin cậy thì dùng
-> nếu không thì fallback chunk
```

Điều này tốt cho câu hỏi fact lookup:

- ai ký văn bản;
- ngày ban hành;
- cơ quan ban hành;
- thông tin hành chính;
- fact đã trích xuất.

`rag_research` có document-first:

```text
Tìm document candidates trước
-> quyết định none / soft / hard
-> rồi mới tìm chunk
```

Điều này tốt để tránh lấy nhầm tài liệu, đặc biệt khi nhiều tài liệu cùng chủ đề.

Hai hướng này không loại trừ nhau. Thực tế HBRag nên giữ artifact-first và bổ sung document-first.

## 5. So sánh theo từng tiêu chí

| Tiêu chí | HBRag hiện tại | rag_research |
| --- | --- | --- |
| Mức độ production | Mạnh hơn | Yếu hơn |
| Cấu trúc DB | Rõ hơn, có PostgreSQL trung tâm | Đơn giản hơn |
| Vector store | Qdrant riêng | Elasticsearch dense vector |
| Keyword search | Elasticsearch + PostgreSQL fallback | Elasticsearch BM25 |
| Access control | Có | Không nổi bật |
| Citation | Có | Có source/log nhưng không production bằng |
| Artifact-first | Có | Không phải trọng tâm |
| Document-first | Chưa mạnh | Rất mạnh |
| Query strategy | Rule-based | LLM router + rule fallback |
| Entity/constraint extraction | Còn nhẹ | Mạnh |
| Hybrid retrieval | Vector + keyword + boost | Nhiều nhánh hơn |
| Rerank | Có | Có, kèm prefilter và compact candidate |
| Context expansion | Có nhưng dễ phình | Có điều kiện theo intent/router |
| Cache | Không phải trọng tâm | Có exact/semantic cache |
| Memory | Có memory/session | Memory/anchor/cache rõ hơn |
| Logging | RAG interaction log tốt | Log route/retrieval rất giàu |
| Dễ bảo trì | Tốt hơn | Phức tạp hơn |
| Tốc độ | Có thể ổn hơn nếu không expand quá nhiều | Dễ chậm hơn do LLM router/cache/retry |

## 6. Ưu điểm của HBRag hiện tại

HBRag có các ưu điểm sau:

- Kiến trúc phù hợp production hơn.
- Dữ liệu không dồn hết vào Elasticsearch.
- PostgreSQL giữ document/chunk/metadata/citation rõ ràng.
- Qdrant chuyên xử lý vector.
- Elasticsearch chuyên xử lý keyword/BM25.
- Có fallback khi Elasticsearch lỗi.
- Có access control theo user/organization.
- Có artifact-first retrieval.
- Có citation gắn với chunk.
- Có log RAG interaction để debug.
- Chunking DOFFICE hiện tại rất mạnh, nhất là bảng/phụ lục/mục cha-con.

Nói ngắn gọn: HBRag có nền vận hành tốt hơn.

## 7. Nhược điểm của HBRag hiện tại

Các điểm còn yếu:

- Query strategy còn dựa nhiều vào rule.
- Chưa có LLM router hiểu sâu entity/constraint.
- Chưa có document scope `none / soft / hard` rõ như `rag_research`.
- Với câu hỏi theo mã văn bản, nhiều chunk cùng match preamble nên dễ kéo quá nhiều chunk.
- Câu hỏi hành chính đôi khi vẫn kéo `table_row`.
- Context sau rerank có thể phình to do neighbor/augment.
- Chưa có retry rõ ràng khi retrieval lần đầu yếu.
- Chưa có cơ chế prefilter candidate cho reranker tinh như `rag_research`.
- Chưa tách mạnh giữa câu hỏi cần text, câu hỏi cần bảng, câu hỏi cần metadata hành chính.

Những điểm này không phải lỗi nền tảng. Chúng là các điểm có thể cải thiện ở tầng retrieval.

## 8. Ưu điểm của rag_research

`rag_research` có nhiều ý tưởng retrieval rất đáng học:

### 8.1. LLM router

Thay vì chỉ dùng keyword/rule, nó dùng LLM để phân tích câu hỏi thành JSON.

Thông tin router giúp retrieval biết:

- intent là gì;
- câu hỏi cần trả lời dạng direct/count/list/compare;
- câu hỏi ở cấp document/table/row/section;
- entity chính là gì;
- constraint phụ là gì;
- có tham chiếu tài liệu hiện tại không;
- có cần mở rộng bảng/section không.

### 8.2. Document-first retrieval

Trước khi tìm chunk, nó tìm tài liệu ứng viên.

Sau đó quyết định:

```text
none: không rõ tài liệu, search toàn kho
soft: có tài liệu nghi ngờ, search toàn kho nhưng boost tài liệu ứng viên
hard: rất chắc tài liệu, chỉ search trong tài liệu đó
```

Cách này rất hữu ích khi nhiều tài liệu có từ khóa giống nhau.

### 8.3. Entity + constraint search

`rag_research` phân biệt:

```text
entity chính: đối tượng cần hỏi
constraint: điều kiện/phần/mục/trường cần lọc trong entity đó
```

Ví dụ:

```text
Nội dung đào tạo của khóa học A là gì?
```

- entity chính: `khóa học A`;
- constraint: `Nội dung đào tạo`.

Với DOFFICE:

```text
Mục tiêu của Phụ lục 02 là gì?
```

- document/entity chính: văn bản đang hỏi;
- constraint: `Phụ lục 02`, `Mục tiêu`.

Cách tách này giúp retrieval không bị lấy nhầm phần có cùng chữ nhưng sai đối tượng.

### 8.4. Nhiều nhánh search

`rag_research` không chỉ vector + BM25. Nó có thêm:

- entity phrase search;
- author/person alias search;
- constraint phrase search;
- section soft search;
- scoped search theo document candidates.

Nhờ đó retrieval có nhiều cửa để tìm đúng chunk.

### 8.5. Lazy rewrite khi retrieval yếu

Nó không rewrite mọi câu hỏi một cách tốn kém.

Luồng là:

```text
nếu câu hỏi nối tiếp rõ -> rewrite sớm
nếu retrieval lần đầu yếu -> rewrite rồi thử lại
```

Cách này cân bằng giữa tốc độ và chất lượng.

### 8.6. Context expansion có điều kiện

Nó chỉ mở rộng bảng/section mạnh khi intent hoặc router cho thấy cần:

- list;
- statistic;
- compare;
- table_level;
- table_section_level;
- requires_table_expansion;
- requires_section_expansion.

Điều này giảm nguy cơ context bị phình không cần thiết.

## 9. Nhược điểm của rag_research

`rag_research` cũng có nhiều hạn chế:

- Phụ thuộc LLM router, nên tốn thời gian và chi phí hơn.
- Nếu LLM router sai, retrieval có thể lệch mạnh.
- Pipeline nhiều rule/boost, dễ khó bảo trì.
- Một số logic được tối ưu theo bài toán nghiên cứu cũ, cần lọc trước khi đưa vào HBRag.
- Elasticsearch gánh nhiều vai trò hơn, không tách vector store rõ như HBRag.
- Access control/citation/DB schema không production bằng HBRag.
- Code có nhiều heuristic, nếu bê nguyên sang dễ làm hệ thống phức tạp.
- Một số file/prompt có dấu hiệu mojibake, cần cẩn thận khi tái sử dụng.

Nói ngắn gọn: `rag_research` thông minh ở retrieval, nhưng không nên bê nguyên vì nó phức tạp và kém production hơn HBRag.

## 10. HBRag nên học gì từ rag_research

HBRag nên học 4 nhóm chính.

### 10.1. LLM semantic router nhẹ

HBRag hiện có `query_strategy.py`, nhưng vẫn là rule-based. Có thể bổ sung một lớp router nhẹ để tạo `semantic_route`.

Router không cần trả lời câu hỏi. Nó chỉ cần phân tích:

```json
{
  "intent": "question_answer",
  "question_scope": "document_level",
  "answer_need": "direct_answer",
  "document_reference": "explicit_document",
  "primary_entities": ["6515/EVNCPC-VTCNTT+KD+KT"],
  "lookup_entities": [],
  "constraints": [
    {"type": "appendix", "value": "Phụ lục 02"},
    {"type": "section", "value": "Mục tiêu"}
  ],
  "requires_table_expansion": false,
  "requires_section_expansion": true,
  "confidence": 0.82
}
```

Lý do nên làm:

- giúp retrieval hiểu câu hỏi tốt hơn;
- giảm phụ thuộc keyword;
- biết câu hỏi cần text, bảng, metadata hay list;
- giảm context thừa.

### 10.2. Document-first scope

HBRag nên thêm bước tìm document candidates trước chunk retrieval.

Luồng đề xuất:

```text
retrieval_query
-> tìm document candidates theo id_vb, ký hiệu, tiêu đề, trích yếu
-> tính điểm top 1/top 2
-> quyết định:
   none: search toàn kho
   soft: search toàn kho + boost tài liệu ứng viên
   hard: chỉ search trong document_id đã khóa
```

Lý do nên làm:

- tránh lấy nhầm tài liệu;
- giảm context dư;
- rất phù hợp với DOFFICE vì người dùng hay hỏi theo số/ký hiệu văn bản;
- hỗ trợ tốt câu hỏi nối tiếp như "văn bản này", "phụ lục đó".

### 10.3. Entity/constraint-aware retrieval

HBRag nên tách rõ:

```text
primary entity: đối tượng chính
constraint: phần/mục/trường/điều kiện cần lọc
```

Ví dụ:

```text
CPCIT cần làm gì trong Phụ lục 01?
```

- primary entity: `CPCIT`;
- constraint: `Phụ lục 01`;
- question scope: có thể là table/detail.

Ví dụ:

```text
Mục tiêu của Phụ lục 02 là gì?
```

- primary entity: văn bản hiện tại hoặc mã văn bản nếu có;
- constraint: `Phụ lục 02`, `Mục tiêu`;
- preferred chunk type: `document_body`/section, không phải `table_row`.

Lý do nên làm:

- tránh search theo mỗi từ "mục tiêu";
- tránh lấy bảng khi câu hỏi cần text;
- tránh lấy đúng phụ lục nhưng sai mục;
- cải thiện câu hỏi bảng theo cột/dòng.

### 10.4. Intent-aware context expansion

HBRag nên kiểm soát expansion theo intent rõ hơn.

Đề xuất:

```text
Hỏi hành chính:
-> ưu tiên document_header/admin artifact
-> không mở rộng table neighbor
-> context tối đa 3-5 chunk

Hỏi chi tiết bảng:
-> lấy table_row/table_column/table_parent liên quan
-> context tối đa 8-12 chunk

Hỏi liệt kê/tổng hợp:
-> cho phép table_group/section expansion
-> context tối đa 15-20 chunk hoặc giới hạn ký tự rõ ràng

Hỏi exact lookup:
-> ưu tiên exact metadata/chunk
-> không expand rộng nếu đã có bằng chứng trực tiếp
```

Lý do nên làm:

- giảm context quá lớn;
- giảm tốc độ chậm của LLM;
- giảm trả lời lẫn thông tin;
- làm top_k có ý nghĩa hơn.

## 11. Phương pháp cải tiến đề xuất cho HBRag

Không nên thay toàn bộ retrieval. Nên cải tiến theo từng lớp.

### Giai đoạn 1: Thêm final context limiter

Đây là bước nên làm sớm nhất.

Sau mọi bước:

```text
rerank
-> artifact chunks
-> neighbor expansion
-> augment context
```

cần có một lớp cuối:

```text
final context limiter
```

Nó giới hạn:

- số chunk tối đa;
- tổng ký tự/tokens;
- ưu tiên chunk theo intent;
- loại bỏ chunk kém liên quan.

Lý do:

- giải quyết ngay lỗi context quá lớn;
- ít rủi ro hơn thay router;
- dễ đo log trước/sau.

### Giai đoạn 2: Thêm document candidate search

Tạo một service nhẹ:

```text
DocumentCandidateService
```

Nó tìm document theo:

- `id_vb`;
- `ky_hieu`;
- `document_code`;
- `title`;
- `trich_yếu`;
- metadata DOFFICE.

Sau đó đưa ra:

```text
document_scope_mode = none / soft / hard
```

Lý do:

- giúp câu hỏi theo văn bản chạy sạch hơn;
- giảm việc mã văn bản kéo toàn bộ table_row;
- là cải tiến có giá trị cao với DOFFICE.

### Giai đoạn 3: Thêm semantic router nhẹ

Không nên đưa LLM router quá nặng ngay.

Ban đầu chỉ cần router trả về các trường tối thiểu:

```json
{
  "answer_need": "direct_answer | count | enumerate | compare | summarize",
  "question_scope": "document_level | section_level | table_level | row_level | general",
  "document_reference": "none | explicit_document | current_document | corpus_wide",
  "primary_entities": [],
  "constraints": [],
  "requires_table_expansion": false,
  "requires_section_expansion": false
}
```

Nếu router lỗi, fallback về `query_strategy.py` hiện tại.

Lý do:

- tận dụng được sự thông minh của LLM;
- vẫn giữ an toàn nhờ fallback;
- không phải thay toàn bộ retrieval.

### Giai đoạn 4: Entity/constraint search trong scope

Sau khi có semantic route và document scope, mới thêm:

- entity phrase search;
- constraint phrase search;
- section soft search;
- appendix/heading search.

Lý do không nên làm trước:

- nếu chưa có router/scope, entity/constraint search dễ thành heuristic rối;
- làm sau thì rõ mục tiêu hơn.

### Giai đoạn 5: Benchmark retrieval

Dùng bộ câu hỏi thật đã gặp:

```text
Ai là người ký văn bản 6515?
Mục tiêu của Phụ lục 02 là gì?
CPCIT cần làm gì trong Phụ lục 01?
Có bao nhiêu table được nêu trong nội dung văn bản?
Trường TenKhachHang có kiểu dữ liệu gì?
```

Đo:

- chunk đúng có xuất hiện trong top_k không;
- có lấy nhầm tài liệu không;
- context cuối có bao nhiêu chunk/ký tự;
- answer có đúng không;
- latency có tăng nhiều không.

## 12. Vì sao nên cải tiến theo hướng kết hợp

Nếu chỉ giữ HBRag hiện tại, hệ thống có nền tốt nhưng retrieval vẫn có thể bị rộng và nhiễu.

Nếu bê nguyên `rag_research`, hệ thống có thể thông minh hơn nhưng rủi ro:

- phức tạp hơn;
- tốn LLM router;
- nhiều heuristic;
- khó ghép với access control/artifact/citation hiện tại.

Vì vậy hướng kết hợp hợp lý nhất là:

```text
Giữ HBRag làm nền production
-> thêm document-first từ rag_research
-> thêm semantic router nhẹ
-> thêm entity/constraint-aware retrieval
-> thêm intent-aware final context limiter
-> giữ artifact-first, Qdrant, Elasticsearch, PostgreSQL, citation, access control
```

Cách này tận dụng điểm mạnh của cả hai:

- HBRag giữ độ chắc và khả năng vận hành.
- `rag_research` bổ sung độ thông minh ở tầng hiểu câu hỏi và định hướng retrieval.

## 13. Kết luận

HBRag hiện tại không yếu. Nó đã có nền retrieval khá tốt và phù hợp production. Vấn đề chính không nằm ở việc thiếu vector search hay thiếu keyword search, mà nằm ở việc retrieval chưa hiểu đủ rõ:

- câu hỏi đang hỏi tài liệu nào;
- hỏi phần nào trong tài liệu;
- entity chính là gì;
- constraint phụ là gì;
- có cần bảng không;
- có cần mở rộng context không;
- nên đưa bao nhiêu context vào LLM.

`rag_research` có nhiều câu trả lời tốt cho các câu hỏi này, đặc biệt là:

- LLM router;
- document scope none/soft/hard;
- entity/constraint search;
- lazy rewrite khi retrieval yếu;
- context expansion theo intent.

Do đó, hướng cải tiến tốt nhất là không thay pipeline HBRag, mà nâng cấp retrieval layer của HBRag theo các ý tưởng chọn lọc từ `rag_research`.

Ưu tiên thực tế nên là:

```text
1. Final context limiter
2. Document-first scope
3. Semantic router nhẹ
4. Entity/constraint search trong scope
5. Benchmark retrieval cố định
```

Nếu làm theo thứ tự này, hệ thống có thể cải thiện chất lượng trả lời mà không phá nền kiến trúc hiện tại.
