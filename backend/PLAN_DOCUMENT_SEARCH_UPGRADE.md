# Plan: Kiểm tra kết quả codex + nâng cấp retrieval cho /api/document-search/search

> Trạng thái: ĐÃ HOÀN THÀNH CODE (2026-07-02) — B1-B9 đã triển khai, 454 test pass, chi tiết kết quả
> xem `docs/PROJECT_OVERVIEW.md` (mục "Cập nhật gần nhất 2026-07-02 (b)"). Còn chờ: endpoint sparse
> thật trên gateway CPC (mục B9) + verify live với ES/Qdrant/gateway đang chạy.

## Bối cảnh

Codex đã cài pipeline fusion mới cho `/api/document-search/search` (file mới **chưa commit**
`app/services/retrieval/document_semantic_search.py` + sửa `document_search_service.py`,
`tests/test_document_search.py`). Nhiệm vụ đợt này:

1. Kiểm tra và sửa lỗi trong code codex.
2. Nâng cấp retrieval theo hướng: hybrid BM25 + dense + sparse, lọc metadata, mở rộng ngữ cảnh
   cha-con (parent-child), rerank cross-encoder, CRAG kiểm tra căn cứ.
3. **(Bổ sung)** Nâng sparse từ hashing tự chế lên **sparse học được (BGE-M3/SPLADE)** — thiết kế
   thành module ĐỘC LẬP để khi Qdrant đổi cấu trúc lưu metadata/chunk sau này vẫn tái dùng được,
   và job `jobs/doffice_sync/run_qdrant.bat` dùng được ngay để re-embed.

### Hạ tầng đã sẵn (không xây mới)
- Qdrant named vectors **dense (4096, Qwen/Qwen3-Embedding-8B) + sparse**, RRF server-side trong
  `QdrantVectorStore.search` (`app/services/vector/vector_store.py`), ACL flat áp đủ ở `_payload_filter`.
- ES BM25 2 nhánh tiếng Việt đã tinh chỉnh: `DofficeBm25DocumentStore` (doc) + `DofficeChunkBm25Store`
  (chunk) trong `app/services/retrieval/retrieval_doffice_bm25.py`.
- Reranker **Qwen/Qwen3-Reranker-8B** qua gateway: `LLMGateway.rerank(query, candidates)`
  (`app/services/llm_gateway/llm_gateway_gateway.py`) — đã cấu hình `.env`, chưa được document-search dùng.
- LLM `Qwen/Qwen3.5-9B` qua `LLMGateway.generate` — đã dùng cho query expansion.
- Fusion đang BẬT: `.env` có `DOFFICE_RETRIEVAL_ENABLED=true`.

### Quyết định phạm vi
1. **Sparse**: LÀM sparse học được (BGE-M3/SPLADE) trong đợt này (yêu cầu bổ sung của anh Hùng) —
   xem mục B9. Provider hashing vẫn giữ làm fallback.
2. **Rerank**: BẬT cross-encoder rerank sau fusion, có cờ settings để tắt.
3. **CRAG**: hybrid — rule token-overlap chấm trước, chỉ gọi LLM chấm các candidate `ambiguous` ở top.
4. **JWT không verify chữ ký** (`app/api/routes/document_search.py`, giả `ID_NV` là bypass ACL):
   KHÔNG sửa code đợt này, ghi cảnh báo + TODO vào `docs/PROJECT_OVERVIEW.md`.

---

## Phần A — Kết quả kiểm tra code codex

Codex đã cài **đủ 7 thành phần** yêu cầu (LLM expansion, multi-query, vector Qdrant, RRF weighted
fusion, dedup, CRAG-lite, context builder hàng xóm ±1). **ACL parity OK**: nhánh vector truyền
`acl_subject` → `build_qdrant_acl_conditions_flat`, khớp nhánh ES.

Vấn đề đã xác minh (file `app/services/retrieval/document_semantic_search.py`):

| # | Mức | Vấn đề | Vị trí |
|---|-----|--------|--------|
| A1 | **Cao** | RRF dùng rank trong list ĐÃ NỐI nhiều query thay vì rank theo từng query: `_add_vector_like_results` dùng `enumerate(results)` bỏ qua field `rank`/`query_index` mà `_search_qdrant_store` đã ghi đúng → kết quả của query mở rộng thứ 2+ bị phạt điểm oan | L292, L267 |
| A2 | Vừa | Embed trùng ×2 và tuần tự: `_search_qdrant_chunks` và `_search_qdrant_docmeta` mỗi hàm tự embed lại cùng bộ expanded queries (dense+sparse), vòng for await tuần tự | L180-195 |
| A3 | Vừa | CRAG retry yếu: khi top-3 weak chỉ chạy lại BM25 chunk sâu hơn, tái dùng vector results cũ; thiếu cờ tổng evidence cấp response ("thiếu căn cứ") | L94-106 |
| A4 | Vừa | Trọng số RRF, RRF_K, ngưỡng CRAG (0.35/0.15), stopwords đều hardcode; lệch bộ trọng số nhánh cũ trong `document_search_service.py` | L29-35, L434-441 |
| A5 | Thấp | `_load_db_context` N+1 query (mỗi seed một query hàng xóm riêng) | L375-386 |
| A6 | Thấp | `used_vector` sai ngữ nghĩa: vector rỗng nhưng BM25 chunk có → vẫn fusion nhưng `used_vector=False` | L76 |
| A7 | Thấp | Nhánh fusion truyền query THÔ vào chunk BM25, nhánh cũ dùng `_strip_org_tokens` → hành vi lệch nhau | L233 |
| A8 | Ghi nhận | JWT decode không verify chữ ký + endpoint không Bearer → giả ID_NV là bypass ACL (làm sau) | `document_search.py:39-53` |

**Nghi vấn đã bác bỏ**: "payload Qdrant Col1 thiếu `id_vb` gây key-mismatch fusion" — SAI. Chuỗi đầy đủ:
`_document_chunk_metadata` (chunker) đưa `id_vb` vào chunk_metadata (nằm trong `CHUNK_METADATA_ALLOWLIST`)
→ `rag_chunk_from_database` merge metadata → `qdrant_payload` giữ `id_vb`. Fusion merge đúng theo `id_vb`.
(Vẫn thêm guard nhỏ ở `_doc_key` phòng chunk cũ index trước khi metadata đầy đủ.)

---

## Phần B — Các thay đổi

### B1. Sửa bug codex (`app/services/retrieval/document_semantic_search.py`)
1. **Fix RRF theo từng query (A1)**: `_add_vector_like_results` dùng `item["rank"]` sẵn có thay
   `enumerate`; đóng góp RRF = Σ theo từng query `weight/(K + rank_trong_query)`.
2. **Embed một lần, dùng chung (A2)**: tách `_embed_queries(queries)` chạy `asyncio.gather`,
   truyền vector vào cả 2 lượt search Qdrant (chunks + docmeta). Giảm 2×N call tuần tự → N call song song.
3. **used_vector (A6)**: tính lại theo nguồn thực dùng.
4. **Strip org (A7)**: dùng `_strip_org_tokens` cho `_search_chunk_bm25` giống nhánh cũ.
5. **Guard `_doc_key`**: chuẩn hoá key ưu tiên `id_vb`; candidate mới có id_vb trùng document_id
   của candidate cũ → gộp.

### B2. Đưa tham số ra settings (`app/core/config.py`) (A4)
Nhóm `document_search_fusion_*`:
- `document_search_fusion_rrf_k=60`, `..._w_bm25_doc=1.0`, `..._w_bm25_chunk=0.55`,
  `..._w_vector_chunk=1.15`, `..._w_vector_docmeta=0.75`
- `document_search_fusion_candidate_k=60`, `document_search_fusion_max_expansions=4`
- `document_search_crag_strong_coverage=0.35`, `..._ambiguous_coverage=0.15`
- `document_search_rerank_enabled=True`, `document_search_rerank_top_k=30`
- `document_search_crag_llm_grading=True`

### B3. Lọc metadata từ query (mục 5 phương pháp)
`_extract_metadata_filters(query)` bằng regex (không LLM): bắt **năm** ("năm 2025", "/2025/"),
**tháng**, **khoảng ngày** khi rõ ràng. Áp vào:
- Qdrant: mở rộng `QdrantVectorStore.search()` nhận filter `nam/thang/ngay_vb` (payload đã có index).
  ⚠️ Chạy impact analysis trước khi sửa (nhiều caller).
- ES doc + chunk: nối `filter` term/range vào `bool.filter` cạnh ACL.
- Bảo thủ: chỉ áp khi match chắc chắn; nếu filter làm kết quả < 3 → bỏ filter chạy lại (fallback).

### B4. Mở rộng ngữ cảnh cha-con (mục 6 phương pháp)
Nâng `_load_db_context`:
- Gộp N+1 (A5) thành 1 query OR các cặp `(document_id, chunk_index±1)`.
- **Chunk cha theo heading/điều-mục**: từ `chunk_metadata` của seed lấy `section_path`/`section_title`;
  kéo thêm chunk cùng document có section là phần cha (JSONB query), giới hạn 2-3 chunk, ưu tiên
  `chunk_type` in (`legal_clause`, `document_section`), đánh dấu `source="parent_context"`.
- Sắp xếp context theo `(document_id, chunk_index)`; giữ cap 8 mục + `MAX_CONTEXT_CHARS_PER_CHUNK`.

### B5. Cross-encoder rerank sau fusion (mục 8 phương pháp)
Sau `_fuse_candidates` (trước CRAG), nếu `document_search_rerank_enabled`:
- Dựng `RerankCandidate(chunk_id=key, content=trich_yeu + highlight + chunk tốt nhất, cap ~1500 ký tự)`;
  gọi `get_llm_gateway().rerank(query, candidates)`.
- Điểm cuối = `0.6*rerank_chuẩn_hoá + 0.4*rrf_chuẩn_hoá` (min-max); reranker lỗi → giữ thứ tự RRF
  (mẫu fallback: `app/services/rerankers/reranker_service.py`).

### B6. CRAG hybrid + retry tốt hơn (A3)
- Rule grading giữ làm vòng 1 (ngưỡng từ settings).
- Candidate top-k `ambiguous` (tối đa 5): 1 call LLM batch (`task_name="document_search_crag_grading"`,
  trả JSON `[{key, verdict, reason}]`), lỗi/parse fail → giữ verdict rule.
- Retry khi top-3 weak: chạy lại **cả vector** (depth ×2) + BM25 fuzzy, tối đa 1 vòng.
- Cờ tổng cấp response: `evidence_summary: "strong"|"partial"|"insufficient"` — client hiển thị
  "thiếu căn cứ" khi insufficient.

### B7. Nối vào service/route + schema response
- `document_search_service.py`: map `evidence_summary` + `rerank_score` vào
  `DocumentSearchHit`/`DocumentSearchResponse`.
- `app/api/routes/document_search.py`: không đổi logic, chỉ nhận field mới qua schema.

### B8. Tests + docs
- `tests/test_document_search.py`: test RRF per-query, metadata filter, parent context, rerank
  fallback, evidence_summary insufficient, CRAG LLM grading parse.
- Cập nhật `docs/PROJECT_OVERVIEW.md` (pipeline mới + TODO JWT verify) + `backend/CLAUDE.md` nếu lệch.

### B9. Sparse học được BGE-M3/SPLADE — module ĐỘC LẬP (yêu cầu bổ sung)
Mục tiêu: thay hashing sparse bằng sparse học được, nhưng **tách riêng khỏi cấu trúc Qdrant** để khi
đổi cấu trúc lưu metadata/chunk (recreate collection) sau này vẫn dùng lại nguyên vẹn.

Thiết kế:
1. **File mới `app/services/embeddings/embedding_sparse_learned.py`** — độc lập, KHÔNG import gì từ
   vector_store/ingestion. Class `LearnedSparseEmbeddingProvider` implement protocol
   `SparseEmbeddingProvider` (`embed_texts`, `embed_query` → `SparseEmbedding(indices, values)`):
   - Gọi HTTP endpoint cấu hình được (gateway CPC hoặc server TEI/Infinity tự host) trả trọng số
     sparse; hỗ trợ 2 dạng response phổ biến: `{indices, values}` và `{token: weight}` (map token→id
     bằng hash cùng không gian — cấu hình `mode`).
   - Batch + retry + timeout như `ExternalLLMClient`; lỗi → raise để caller quyết fallback.
2. **Settings mới** (`app/core/config.py`): `sparse_embedding_provider` nhận thêm `"learned"`;
   `sparse_learned_base_url`, `sparse_learned_model` (vd `BAAI/bge-m3`), `sparse_learned_endpoint_path`,
   `sparse_learned_api_key`, `sparse_learned_timeout`, `sparse_learned_fallback_hashing=True`
   (lỗi gateway → dùng hashing để job không gãy, log warning).
3. **Factory** `embedding_sparse_factory.py`: thêm nhánh `"learned"`; giữ `"hashing"` nguyên trạng.
4. **Job `run_qdrant.bat` dùng được ngay**: đường embed của job đã đi qua
   `get_sparse_embedding_provider()` (VectorIndexingService + `_index_docmeta` trong
   `ingestion_doffice_unified.py`) → chỉ cần đặt env `SPARSE_EMBEDDING_PROVIDER=learned` là job
   re-embed sparse mới. KHÔNG sửa cấu trúc collection: vẫn dùng named vector `sparse` hiện có
   (SparseVectorParams không phụ thuộc dimension) → không phải recreate, chỉ cần chạy lại
   `scripts/reset_doffice_for_rechunk.py --yes` + `run_qdrant.bat` để embed lại.
5. **Query-side**: `document_semantic_search._embed_queries` dùng cùng factory → tự khớp provider
   với dữ liệu đã index. Ghi chú vào docs: dữ liệu index bằng provider nào thì query phải cùng
   provider (thêm ghi chú `sparse_provider` vào payload docmeta để tự kiểm tra lệch — optional).

Việc cần anh Hùng xác nhận sau (không chặn code): endpoint sparse thực tế trên gateway CPC
(URL + format). Code viết theo config, test bằng fake/mock.

---

## Files sửa chính
- `app/services/retrieval/document_semantic_search.py` — B1, B3-B6 (phần lớn thay đổi)
- `app/core/config.py` — B2, B9 settings
- `app/services/retrieval/document_search_service.py` — B7, dùng chung `_strip_org_tokens`
- `app/services/vector/vector_store.py` — B3: `search()` nhận filter nam/thang/ngay_vb (⚠️ impact trước)
- `app/services/retrieval/retrieval_doffice_bm25.py` — B3: metadata filter cho 2 store ES
- `app/services/embeddings/embedding_sparse_learned.py` (MỚI) + `embedding_sparse_factory.py` — B9
- `tests/test_document_search.py`, `tests/` cho sparse provider — B8, B9
- `docs/PROJECT_OVERVIEW.md`

## Quy trình bắt buộc (CLAUDE.md dự án)
- Trước khi sửa mỗi symbol: `gitnexus_impact({target, direction: "upstream"})` — index đang được
  dựng lại (`npx gitnexus analyze`); trong lúc chờ dùng grep rà caller thủ công.
- Trước khi commit: `gitnexus_detect_changes()`.
- KHÔNG Write đè file có dải Unicode hiếm (`chunker_text_cleaning.py`...) — chỉ Edit.

## Kiểm chứng (Verification)
1. `.venv\Scripts\python.exe -m pytest tests/test_document_search.py -q` (+ test sparse mới).
2. Chạy `uvicorn app.main:app --port 8000`, gọi `POST /api/document-search/search` với 3 dạng query:
   - mã văn bản: "3113" → BM25/exact thắng;
   - ngữ nghĩa: "người lao động kết hôn nghỉ mấy ngày" → fusion + context có chunk cha;
   - có thời gian: "quyết định năm 2025" → metadata filter nam=2025.
   Kiểm tra `search_type="fusion"`, `expanded_queries`, `evidence`, `evidence_summary`.
3. ACL: gọi với jwtToken của nhân viên bị deny một văn bản → văn bản không xuất hiện ở nhánh vector.
4. Sparse learned: đặt `SPARSE_EMBEDDING_PROVIDER=learned` + endpoint test → chạy `run_qdrant.bat`
   trên 1-2 văn bản, xác nhận point có sparse vector mới; query khớp provider.
5. Đo latency trước/sau (rerank + LLM grading thêm ~2 call); quá chậm → hạ `document_search_rerank_top_k`.
