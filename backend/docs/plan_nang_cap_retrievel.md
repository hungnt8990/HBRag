 Plan: Nâng cấp retrieval cho hỏi-đáp văn bản (/api/document-search/chat)

     Context

     Dữ liệu đã chunk + embed xong theo pipeline kho AI dùng chung: ES chunk kho_ai_dung_chung_chunk, ES doc nguồn kho_ai_dung_chung (mapping strict, CHỈ ĐỌC), Qdrant hbrag_doffice_chunks +
     hbrag_doffice_docmeta (dense 4096, Qwen3-Embedding-8B). Khảo sát cho thấy retrieval vẫn query schema CŨ (doffice_vanban) trong khi dữ liệu là schema BA — nhiều nhánh đang chết:

     ┌───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┬──────────────────────────────────────────────────────────────────────────────────┐
     │                                                  Vấn đề đã xác minh                                                   │                                     Hậu quả                                      │
     ├───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┼──────────────────────────────────────────────────────────────────────────────────┤
     │ BM25 doc-level query ky_hieu^6/trich_yeu^3/tom_tat/noi_ban_hanh/noi_dung — index có                                   │ Nhánh doc-level chết; gauss decay trên ngay_vb.date (field không tồn tại) có thể │
     │ document_no/title/summary/signer/ocr_content                                                                          │  gây lỗi ES                                                                      │
     ├───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┼──────────────────────────────────────────────────────────────────────────────────┤
     │ BM25 chunk boost ky_hieu^4/trich_yeu^2, filter nam/thang                                                              │ Boost + lọc năm/tháng chết; title có trong index nhưng KHÔNG được query          │
     ├───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┼──────────────────────────────────────────────────────────────────────────────────┤
     │ Qdrant _payload_filter match nam/thang int — payload chỉ có issue_date string                                         │ Filter năm loại SẠCH kết quả Qdrant (may có filter-fallback cứu)                 │
     ├───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┼──────────────────────────────────────────────────────────────────────────────────┤
     │ _build_context đọc PostgreSQL bảng chunks — pipeline kho AI KHÔNG ghi PG                                              │ Context expansion (hàng xóm ±1 + chunk cha) trả rỗng                             │
     ├───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┼──────────────────────────────────────────────────────────────────────────────────┤
     │ _source_from_metadata fallback sai tên (document_title/document_code/subject)                                         │ Citation/rerank thiếu metadata                                                   │
     ├───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┼──────────────────────────────────────────────────────────────────────────────────┤
     │ Identifier boost khớp ky_hieu/id_vb                                                                                   │ Boost theo mã văn bản chết                                                       │
     ├───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┼──────────────────────────────────────────────────────────────────────────────────┤
     │ build_acl_filters (doc BM25) must_not dùng acl_deny_nv/acl_deny_pb (số, cũ) — index BA dùng acl_deny (chuỗi pb_/nv_)  │ DENY không được áp ở nhánh doc BM25 — lỗ hổng ACL, phải siết                     │
     ├───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┼──────────────────────────────────────────────────────────────────────────────────┤
     │ Chat không truyền bm25_hits_task                                                                                      │ Chat thiếu nhánh BM25 doc-level (recency/org boost) so với /search               │
     ├───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┼──────────────────────────────────────────────────────────────────────────────────┤
     │ Request /chat không có history                                                                                        │ Không hỏi nối tiếp được                                                          │
     ├───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┼──────────────────────────────────────────────────────────────────────────────────┤
     │ _ORG_CODES, _DOC_TYPE_ABBR, MAX_PASSAGE_CHARS, chunk_type weights... hard-code                                        │ Khó tái dùng cho bài toán khác                                                   │
     └───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┴──────────────────────────────────────────────────────────────────────────────────┘

     Lưu ý kiến trúc quan trọng (đã xác minh): ES chunk index + Qdrant payload KHÔNG có document_no/signer/summary (KHO_ES_CHUNK_DOC_FIELDS kho_client.py:53 chỉ có document_id/title/...) → phải thêm bước
     enrich doc-source đọc từ index nguồn kho_ai_dung_chung.

     Quyết định đã chốt với user:
     1. Remap CHỈ theo schema BA mới (bỏ tên cũ trong luồng kho AI; KHÔNG đụng các file RAG generic: retrieval_hybrid_search/keyword_search/elasticsearch_keyword_search/artifact_first_retrieval).
     2. Thêm multi-turn (history + condense rewrite, backward-compatible).
     3. Config-driven retrieval profile (1 dataclass + registry, không plugin system).

     Giai đoạn 1 — Fix schema mismatch (ưu tiên cao nhất, tự test được)

     1.0 Tiền kiểm (read-only)

     - curl -sk mapping kho_ai_dung_chung + kho_ai_dung_chung_chunk: xác nhận type từng field (document_no keyword hay text? format issue_date?) → quyết term vs match.
     - Scroll 5–10 point Qdrant mỗi collection: xác nhận format issue_date payload → chọn phương án 1.3 A/B.
     - Kiểm nghi vấn gauss decay ngay_vb.date gây lỗi ES hiện tại.

     1.1 ES chunk BM25 — app/services/retrieval/retrieval_doffice_bm25.py (DofficeChunkBm25Store)

     - _CHUNK_TEXT_SEARCH_FIELDS (:345): → chunk_text^1, section_path^1.5, title^2, table_context^1.2 (bỏ ky_hieu — chunk index không có mã VB; identifier lo ở doc-level).
     - Phrase boost (:538): → chunk_text^2, section_path^3, title^3.
     - _source (:546): → document_id, id, id_full, chunk_id, chunk_order, chunk_type, chunk_text, section_path, title, issue_date, content_hash.
     - Filter năm/tháng (:505): terms nam/thang → range trên issue_date (năm Y: gte {Y}-01-01, lte {Y}-12-31; năm+tháng: range đầu/cuối tháng, bool should nhiều năm).
     - Guard ensure_index (:405): TUYỆT ĐỐI không PUT mapping vào index kho_ai_dung_chung* — chỉ HEAD.
     - Method mới fetch_context_chunks(seeds, acl_subject) (cho 1.4) + fetch_doc_sources(document_ids) (cho 1.5 — query index nguồn, _source: document_id, document_no, title, summary, signer, issuer_org_name,
     issue_date, issue_year).
     - _TEXT_SEARCH_FIELDS doc-level (:31) + search_documents (:298): remap → document_no^6, title^3, summary^2, issuer_org_name^1.5, signer^1, ocr_content^1.2; filter issue_year/issue_month.

     1.2 Doc-level BM25 — app/services/retrieval/document_search_service.py

     - _SOURCE_FIELDS (:90) → field BA; _HIGHLIGHT (:95): noi_dung/trich_yeu → ocr_content/title.
     - Ref/exact query (:377–412): ky_hieu → document_no, id_vb → document_id.
     - content_fields (:422): → title^4, summary^2.5, signer^1.5, ocr_content^1.0, issuer_org_name^0.5; phrase/phrase_prefix tương ứng.
     - Filter năm (:494): terms nam → terms issue_year; _RECENCY_FIELD (:304): ngay_vb.date → issue_date.
     - _org_boost_functions (:334): ky_hieu → document_no, noi_ban_hanh → issuer_org_name.
     - build_acl_filters (:288) → thay bằng build_es_acl_filter_flat(acl_subject) (security_acl_payload.py:288) — fix deny (SIẾT ACL).
     - _hit_key (:542) + _apply_chunk_rerank (:567): id_vb or document_id → document_id.
     - Map response (:725–751): GIỮ DocumentSearchHit field cũ (không vỡ API/FE): ky_hieu←document_no, trich_yeu←title, tom_tat←summary, noi_ban_hanh←issuer_org_name, nguoi_ky←signer, ngay_vb←issue_date[:10],
     nam←issue_year.

     1.3 Filter năm/tháng trên Qdrant — app/services/vector/vector_store.py (_payload_filter :642)

     - Phương án A (ưu tiên, không re-embed/backfill): tạo payload index type datetime cho issue_date trên 2 collection (chỉ-index, không đụng vector) → filter DatetimeRange(gte/lte). Điều kiện: issue_date
     parse được (kiểm 1.0).
     - Phương án B (nếu format bẩn): script jobs/doffice_sync/run_kho_backfill_dates.py — scroll + set_payload thêm issue_year/issue_month int (không re-embed); sửa build_docmeta_payload/build_chunk_payload
     (run_kho_qdrant.py:146,164) ghi cho dữ liệu mới; thêm vào PAYLOAD_INTEGER_FIELDS.
     - _payload_filter GIỮ nhánh nam/thang cho store khác — chỉ thêm nhánh mới theo cấu hình store.
     - Interim: nếu chưa sẵn sàng → fusion bỏ filter năm phía Qdrant (ES vẫn lọc), log warning.

     1.4 Context expansion PG → ES — app/services/retrieval/document_semantic_search.py

     - Thay _build_context/_load_db_context/_chunk_model_context (:691–815): seed (id_full, chunk_order) từ _chunk_context_seed (:672 — sửa chunk_index→chunk_order, thêm id_full).
     - Dùng fetch_context_chunks mới: hàng xóm = term id_full + range chunk_order [n-1, n+1]; chunk CHA = cùng id_full, terms chunk_type ∈ PARENT_CHUNK_TYPES, chunk_order ∈ [n-40, n), chọn nearest trong
     Python. Kèm build_es_acl_filter_flat vào query (không nới ACL).
     - Bỏ import AsyncSessionLocal/Chunk khỏi file nếu hết dùng.

     1.5 Enrich doc-source (bước MỚI bắt buộc)

     - _enrich_doc_sources(candidates) trong document_semantic_search.py, gọi sau _fuse_candidates (:175, + sau re-fuse filter-fallback :182 + CRAG retry :218): 1 call fetch_doc_sources trên index nguồn, merge
     vào candidate.source (không đè giá trị đã có, kèm ACL filter cho chắc).
     - Nhờ đó rerank/citation/identifier boost/LLM grading có document_no/title/summary/signer.

     1.6 Remap consumer trong document_semantic_search.py

     - _doc_key (:650): id_vb or document_id → document_id or id_full or id.
     - _search_qdrant_store key (:485): chunk_id or id_vb or document_id → chunk_id or id or document_id.
     - _source_from_metadata (:654): viết lại theo key BA chuẩn: document_id, id_full, document_no, title, summary, issuer_org_name, signer, issue_date[:10], issue_year.
     - Highlight bm25 (:570): noi_dung/trich_yeu → ocr_content/title.
     - _rerank_content (:892): trich_yeu/tom_tat → title/summary.
     - _candidate_identifier_match (:944): ky_hieu → document_no, id_vb → document_id.
     - _llm_grade_ambiguous (:1061): key JSON → document_no/title.

     1.7 Chat consumer — app/services/retrieval/document_chat_service.py

     - _passage_text (:54): trich_yeu/tom_tat → title/summary.
     - _citation (:70): đọc source BA; giữ key JSON cũ (ky_hieu/trich_yeu/ngay_vb) map giá trị mới + thêm key mới song song (document_no/title/issue_date) để FE chuyển dần.

     Test GĐ1: unit test _source_from_metadata, filter builder (ES year range, Qdrant DatetimeRange), fetch_context_chunks (mock httpx); cập nhật fixture tests/test_two_stage_retrieval.py,
     tests/test_doffice_two_stage.py.

     Giai đoạn 2 — Retrieval profile (config-driven)

     - File mới app/services/retrieval/retrieval_profile.py: dataclass frozen RetrievalProfile gồm: tên index/collection; doc_text_fields/chunk_text_fields (tuple (field, boost)); phrase fields; _source
     fields; doc_identifier_field="document_no", doc_key_field="document_id", year_field/month_field/recency_date_field; chunk_type_weights (function_score); parent_chunk_types; doc_link_field="id_full",
     chunk_order_field="chunk_order"; lexicon domain (org_codes, org_alias, org_issuer_query, doc_type_abbr); limits (max_context_items=8, max_context_chars_per_chunk=1800, max_passage_chars=2000,
     parent_lookback_chunks=40); qdrant_date_filter="datetime"|"year_int"|"off".
     - KHO_AI_PROFILE = giá trị GĐ1; registry _PROFILES + get_retrieval_profile(name).
     - Setting mới document_search_retrieval_profile: str = "kho_ai" (config.py, cạnh nhóm document_search_*). Trọng số RRF/CRAG/rerank GIỮ ở settings (đã config-driven).
     - Inject: 2 BM25 store nhận profile ở __init__ (mặc định get_retrieval_profile()); document_semantic_search + document_search_service + document_chat_service đọc hằng số từ profile thay hard-code
     (_ORG_CODES :183, _DOC_TYPE_ABBR :123, MAX_CONTEXT_*, MAX_PASSAGE_CHARS :25...).
     - Bài toán mới = thêm 1 instance profile. KHÔNG plugin system, KHÔNG YAML.

     Test GĐ2: refactor thuần — snapshot test JSON query body trước/sau để chứng minh không đổi hành vi.

     Giai đoạn 3 — Chat parity + multi-turn

     3.1 BM25 doc-level cho chat

     - Tách hàm tái dùng run_doc_bm25(query, *, top_n, acl_subject, document_ids=None, prefer_recent=True) trong document_search_service.py (gói build_query_body + _search_es + fuzzy fallback :652–669; thêm
     filter terms document_id khi có scope).
     - stream_document_chat (:87): bm25_task = asyncio.ensure_future(run_doc_bm25(...)) → truyền bm25_hits_task vào fusion (:113). Lỗi ES nhánh này: nuốt + log, trả rỗng (không fail stream).

     3.2 Multi-turn

     - DocumentChatRequest (:28): thêm history: list[ChatHistoryMessage] | None (role user|assistant, content max 8000, max 20 message) — backward-compatible. Route document_search.py:229 truyền xuống.
     - _condense_query(query, history) trong document_chat_service.py: không history → trả gốc; tái dùng should_rewrite_with_context (query_rewrite_service.py:36) để bỏ qua khi không cần; ngược lại LLM
     condense (prompt kiểu QueryRewriteService: giữ nguyên mã/số văn bản, trả 1 câu độc lập tiếng Việt, context 6 message cuối cắt 900 ký tự) với asyncio.wait_for timeout; lỗi/timeout → fallback query gốc.
     - Settings mới: document_chat_condense_timeout_s: float = 2.5, document_chat_history_max_messages: int = 6.
     - Retrieval dùng câu condensed; event meta thêm rewritten_query khi khác gốc; prompt trả lời chèn 2–4 turn cuối trước "Document Text", câu hỏi dùng query GỐC.
     - Log api_request_logs: thêm history_len, condensed vào request_params.

     Giai đoạn 4 — Cải tiến chất lượng (chọn lọc)

     1. docmeta→chunk expansion: candidate chỉ có cờ vector_docmeta → fetch top-3 chunk của doc từ ES chunk (match chunk_text + term id_full, kèm ACL) để passage có nội dung thật.
     2. Dedup passage theo content_hash trong _append_context (:818).
     3. Ngân sách context theo ký tự (max_context_total_chars ~12000 trong profile) thay MAX_CONTEXT_ITEMS cứng.
     4. Adaptive top_n passage cho chat: evidence strong + top-1 rerank ≥ 0.7 → chỉ 4–5 passage (giảm nhiễu + latency).
     5. (Tuỳ chọn) boost section_path.raw/title khi query chứa "điều/khoản/mục X".
     - KHÔNG làm: sparse re-embed, HyDE, graph.

     Verification (end-to-end)

     # Tiền kiểm mapping/payload rồi chạy server
     .venv\Scripts\python.exe -m uvicorn app.main:app --port 8000

     # /search — 4 lớp truy vấn (JWT thật):
     #  "40/QĐ-IT"                  → search_type exact/fusion, hit đầu document_no khớp, evidence strong
     #  "Võ Văn Hòa nâng lương"     → signer + chunk_text; citations có title (không null)
     #  "quyết định năm 2025"       → filter issue_year/issue_date áp cả ES + Qdrant, kết quả toàn 2025
     #  "quy chế công tác phí EVNCPC" → hits[].context KHÁC RỖNG (ES neighbor expansion — trước rỗng)

     # /chat SSE (curl -N): sources có document_no/title; delta stream; done.evidence_summary
     # Multi-turn: lượt 2 "văn bản này do ai ký?" + history → meta.rewritten_query độc lập, trả lời nêu signer
     # ACL: JWT user không quyền → 0 kết quả; user trong acl_deny → không thấy doc (cả /search + /chat)
     # pytest: tests/test_two_stage_retrieval.py, tests/test_doffice_two_stage.py + test mới

     Rủi ro / Lưu ý

     - ACL chỉ siết, không nới: fix deny doc-BM25 bằng build_es_acl_filter_flat; context expansion + enrich đều kèm ACL filter.
     - KHÔNG re-embed; KHÔNG đụng index nguồn kho_ai_dung_chung (chỉ _search; guard ensure_index không PUT kho_ai_dung_chung*).
     - Không vỡ /search: DocumentSearchHit giữ field cũ, map BA→cũ tại boundary.
     - KHÔNG đụng các file RAG generic (retrieval_hybrid_search, retrieval_keyword_search, retrieval_elasticsearch_keyword_search, retrieval_artifact_first_retrieval); _payload_filter giữ nhánh nam/thang cho
     store khác.
     - retrieval_doffice_two_stage.py dùng search_documents chung → hành vi đổi theo schema BA (chấp nhận, cập nhật test).
     - Sau khi xong: cập nhật docs/PROJECT_OVERVIEW.md + mục retrieval trong backend/CLAUDE.md (xoá TODO remap).