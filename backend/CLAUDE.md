# HBRag Backend — Ghi nhớ trạng thái & quyết định (để khôi phục ngữ cảnh sau khi clear session)

> Súc tích cố ý (tiết kiệm token). Chi tiết đầy đủ: `docs/PROJECT_OVERVIEW.md` (đọc khi cần),
> schema metadata: `docs/METADATA_SCHEMA.md`. Khi hoàn thành thay đổi đáng kể -> cập nhật
> PROJECT_OVERVIEW (đừng phình to file này).

## Kiến trúc dữ liệu (DOffice 3-DB) — đang dùng
- **PostgreSQL = NGUỒN SỰ THẬT.** Bảng `documents`: `parsed_text` = noi_dung RAW;
  `document_metadata` (JSONB) = source thô + `clean`{noi_dung,tom_tat sạch} + `access`{raw_assignment,
  acl_subjects, acl_deny nén, acl_ver} + cờ `pg_prepared`/`chunk_count`/`qdrant_indexed`. Bảng `chunks` = chunk đã sạch.
- **⚠️ 2026-07-05 ĐỔI TOÀN BỘ tên index/collection retrieval sang nhánh KHO AI DÙNG CHUNG** (xem mục riêng dưới):
  ES full = `kho_ai_dung_chung`, ES chunk = `kho_ai_dung_chung_chunk`, Qdrant = `hbrag_doffice_chunks` +
  `hbrag_doffice_docmeta` (bỏ `_v1`). ES hệ thống chuyển `https://10.72.121.232:9200` (security bật) — settings mới
  `elasticsearch_username/password/verify_ssl`, MỌI httpx client ES đi qua `es_client_kwargs()` (`retrieval_shared.py`).
  Tài khoản `elastic` (role `doffice` bị 403 trên `kho_ai_dung_chung*`). Synonyms_set `vi_abbreviations` đã push.
- **Elasticsearch — 2 nhánh** (BM25, ACL nén, KHÔNG ACL raw): full (`DofficeBm25DocumentStore`) + chunk
  (`DofficeChunkBm25Store`) — tên index đọc từ settings (trên).
- **Qdrant — 2 collection** (chỉ 2 này, đã xóa collection generic): chunk + docmeta (tên trên; vector 4096,
  **CHỈ dense — sparse TẮT 2026-07-04**:
  `sparse_embedding_enabled=False`, lexical = ES BM25, fusion/rerank ở app; KHÔNG cần re-embed/recreate,
  schema sparse cũ trong collection vô hại). Payload có ACL NÉN (acl_subjects/acl_deny, KHÔNG raw) +
  filter `nam/thang/ngay_vb/id_dv_ban_hanh/loai_vb/linh_vuc` (đã tạo index). **Server: `10.72.117.69:6333`**
  (env QDRANT_URL+QDRANT_API_KEY; đổi từ 10.72.113.21 — 2026-07-03). Embed docmeta = trich_yeu+tom_tat+noi_ban_hanh
  (bỏ ten_file). `loai_vb`/`linh_vuc` suy luận ở `ingestion_doffice_business_fields.py`.

## Pipeline (file `app/services/ingestion/ingestion_doffice_unified.py`)
- `prepare_postgres` (RAW) -> `clean_data` (in-memory: normalize+làm sạch+nén ACL) ->
  **`persist_to_postgres`** (ghi PG: clean + chunk vào bảng chunks + ACL nén, đặt `pg_prepared=True`) ->
  `index_elasticsearch` (full) + `index_elasticsearch_chunks` (nhánh chunk) -> **`embed_to_qdrant`** (CHỈ đọc PG -> embed).
- `index_qdrant` = persist + embed (legacy/1 lượt). Xóa chọn lọc: `delete_by_id_vb(id_vb, pg=, es=, qdrant=)`.
- Bỏ qua văn bản > `max_chunks` (mặc định 500): không chunk/embed, không đánh dấu (giữ pending).

## Nhánh KHO AI DÙNG CHUNG (2026-07-05) — pipeline chính hiện tại
- Nguồn: index ES `kho_ai_dung_chung` (~16k doc, mapping STRICT do nhóm BA quản — KHÔNG ghi/sửa; `id`=UUIDv7,
  ACL NÉN SẴN `acl_subjects`/`acl_deny`, nội dung `ocr_content`, schema BA `document_no/title/signer/summary/
  issue_date/doc_group{cv_den,cv_di,cv_noi_bo}...`). Client: `jobs/doffice_sync/clients/kho_client.py`
  (`uuid7()`, scroll, `existing_chunk_id_full`, bulk/mark/unmark). Hằng số field = danh sách spec chốt trong file này.
- **⚠️ KHÔNG dùng PostgreSQL cho trạng thái chunk**: đã/chưa chunk suy TỪ ES (`existing_chunk_id_full`: `id_full`
  đã có trong `kho_ai_dung_chung_chunk` chưa). Bỏ hẳn checkpoint PG.
- **run_kho_chunk** (`run_kho_chunk.bat`): quét nguồn theo batch (`--issuer-org` lọc đơn vị) -> mỗi batch kiểm tra
  `existing_chunk_id_full` -> CHỈ chunk văn bản CHƯA có chunk -> làm sạch `ocr_content` (`normalize_doffice_source`
  qua `build_doffice_style_source`) -> `build_doffice_chunks` -> ghi `kho_ai_dung_chung_chunk`. Chunk KHÔNG lưu PG,
  KHÔNG đụng Qdrant. **Hiển thị 1 BẢNG in-place** (`cs.Spinner(self._status)`: tổng nguồn/đã chunk/chưa chunk/đang
  xử lý). **Dừng an toàn**: Ctrl-C set cờ `_STOP`, kiểm tra GIỮA các văn bản -> dừng SAU khi chunk xong văn bản hiện
  tại (Ctrl-C lần 2 = buộc thoát). **LOOP**: `--interval`/`KHO_JOB_INTERVAL` mặc định 300s (5 phút); 0 = 1 lượt rồi
  thoát. `--full-scan` = chunk lại tất cả. Env `KHO_JOB_*`.
- **run_kho_qdrant** (`run_kho_qdrant.bat`): SỞ HỮU 2 collection Qdrant — `ensure_qdrant_collections()` (kiểm tra
  tồn tại + tạo dense-only) ở `_build_ctx`; đây là NƠI DUY NHẤT tạo/recreate 2 collection. Quét chunk pending
  (`qdrant_indexed!=true`, agg theo `id_full`, `_refresh` trước) — TUẦN TỰ từng doc: embed docmeta TRƯỚC
  (`title+signer+summary` qua `clean_for_chunking`) rồi TỪNG chunk (không làm sạch lại), CHỈ DENSE ->
  `hbrag_doffice_docmeta` + `hbrag_doffice_chunks` -> đánh dấu `qdrant_indexed=true`. Chạy 1 lượt rồi dừng. Env
  `KHO_QDRANT_*`; `--id-full`, `--embed-batch` (mặc định 1).
- **⚠️ Idempotent re-embed chunk (fix 2026-07-06)**: point id chunk = UUIDv7 sinh MỚI mỗi lần re-chunk -> upsert
  KHÔNG đè point cũ -> point mồ côi tích tụ ở Qdrant (đã dọn 15.717 point). `_process_document` nay gọi
  `chunks_store.delete_points_by_field("id_full", id_full)` TRƯỚC khi upsert chunk (docmeta không cần: point id =
  id doc nguồn, ổn định). Cần payload index `id_full` (đã thêm vào `PAYLOAD_KEYWORD_FIELDS`, `_ensure_payload_indexes`
  tự tạo trên collection sẵn có). ⚠️ `_cat/indices docs.count` của `kho_ai_dung_chung` (~69k) ĐẾM CẢ nested
  `reference_documents` — số văn bản THẬT = `_count` = 16.354 (job đếm đúng, KHÔNG phải bug).
- **SCHEMA field CHỐT (2026-07-05 rev2 — lưu ĐÚNG, không dư)**:
  - **`org_list` (array, 2026-07-05)**: TẤT CẢ đơn vị liên quan văn bản (issuer + cv_den/cv_di/cv_noi_bo — nguồn
    `kho_ai_dung_chung` gộp sẵn, keyword array, phủ 100%). LƯU ở CẢ 3 nơi (ES chunk + Qdrant docmeta + Qdrant chunk
    doc-level). **Bộ lọc đơn vị (`--issuer-org`/`KHO_JOB_ISSUER_ORG`) nay lọc theo `org_list`** (văn bản khớp nếu 1
    đơn vị lọc ∈ org_list) — KHÔNG còn `issuer_org_id`. Backfill chunk/point cũ: chạy full (xem mục Chạy full dưới).
  - ES chunk `kho_ai_dung_chung_chunk`: `id, id_full, document_id, title, source_system, doc_group, doc_type,
    doc_category, issue_date, owner_department_id, security_level, org_list, acl_subjects, acl_deny, chunk_id, chunk_order,
    chunk_text, chunk_type, section_path, content_hash` + 2 field cơ chế **bắt buộc**: `table_context` (để dựng
    payload Qdrant chunk) + `qdrant_indexed` (cờ đánh dấu). `id`=UUIDv7 chunk, `id_full`=`id` doc nguồn.
  - Qdrant docmeta `hbrag_doffice_docmeta` (point id=`id` doc nguồn): `id, document_id, source_system,
    issuer_org_id, issuer_org_name, org_list, doc_group, doc_type, doc_category, keywords, issue_date, expiry_date,
    owner_department_id, security_level, acl_subjects, acl_deny, related_document_ids, reference_document_ids, priority`.
  - Qdrant chunk `hbrag_doffice_chunks` (point id=`id` chunk): `id, id_full, document_id, source_system, org_list, doc_group,
    doc_type, doc_category, issue_date, owner_department_id, security_level, acl_subjects, acl_deny,
    related_document_ids, reference_document_ids, priority, chunk_id, chunk_order, chunk_text, chunk_type,
    table_context, section_path, content_hash`. Doc-level lấy từ record ES chunk (bù related/reference/priority từ
    doc nguồn), chunk-level từ record ES chunk. **Payload Qdrant GHI ĐỦ KHUNG field theo spec** (2026-07-05):
    field rỗng ở nguồn VẪN ghi để schema đồng nhất mọi doc — list rỗng -> `[]`, scalar rỗng -> `null`
    (`build_docmeta_payload`/`build_chunk_payload` + `_LIST_PAYLOAD_FIELDS` trong `run_kho_qdrant.py`). Nay 9 field
    nghiệp vụ chưa điền ở nguồn (doc_type/doc_category/keywords/expiry_date/owner_department_id/security_level/
    related_document_ids/reference_document_ids/priority — kiểm chứng 0/16354 doc có giá trị) hiện dạng null/[],
    tự có giá trị khi API nguồn bổ sung. ⚠️ Point Qdrant cũ (ghi theo lối bỏ field rỗng) phải re-embed
    (`run_kho_qdrant --reset 9`) mới đủ khung. (Riêng record ES `kho_ai_dung_chung_chunk` vẫn bỏ field rỗng.)
- **`--reset 9` TÁCH theo stage** (TUYỆT ĐỐI không đụng `kho_ai_dung_chung`; guard tên trong `delete_chunk_index`):
  - `run_kho_chunk --reset 9` (`KHO_JOB_RESET`): `reset_es_chunk_stage` = xoá + tạo lại RỖNG ES chunk. KHÔNG đụng
    Qdrant/PostgreSQL. Vì trạng thái suy từ ES -> xoá index chunk = coi như chưa chunk gì.
  - `run_kho_qdrant --reset 9` (`KHO_QDRANT_RESET`): `reset_qdrant_stage` = recreate 2 collection Qdrant dense-only
    + `unmark_all_chunks` (bỏ cờ `qdrant_indexed` mọi chunk ES) để embed lại. GIỮ nguyên ES chunk.
  - `--reset 0` (mặc định) = chạy theo trạng thái đã có.
- **CHẠY FULL / backfill field mới (2026-07-05)**: thêm field vào chunk/point cũ (vd `org_list`) cần reprocess.
  `run_kho_chunk` có cờ `--full-scan` HOẶC env `KHO_JOB_FULL_SCAN=1` (mới) = chunk LẠI tất cả (bỏ kiểm tra "đã
  chunk"; delete_by_id_full + upsert -> chunk id MỚI). Backfill sạch: (1) `run_kho_chunk.bat` FULL_SCAN=1 chạy 1
  lượt (re-chunk ES chunk + đánh dấu pending) -> (2) `run_kho_qdrant.bat` RESET=9 (recreate 2 collection + re-embed
  all, tránh point cũ mồ côi do chunk id đổi). ⚠️ Xong backfill: đặt lại `KHO_JOB_FULL_SCAN=0` (full-scan lặp
  trong loop = re-chunk 16k mỗi vòng, phí) + `KHO_QDRANT_RESET=0`. Cả 2 bat HIỆN đang để FULL (=1/=9).
- **✅ Retrieval ĐÃ remap schema BA (2026-07-06)**: mọi query/filter/boost dùng tên BA
  (`document_id/document_no/title/summary/signer/issuer_org_name/issue_date/issue_year/id_full/chunk_order`),
  bỏ hẳn tên cũ (`id_vb/ky_hieu/trich_yeu/nam/thang/ngay_vb`). Chi tiết mục **Retrieval nâng cấp** dưới.

## Jobs (`jobs/doffice_sync/`) — nguồn doffice_vanban CŨ (giữ tham khảo)
- **run_unified/run_pg_es** (`run_pg_es.bat`=`--skip-qdrant`): pipeline **6 LUỒNG VẬT LÝ tách rời** (mỗi luồng 1 pool+queue):
  PG-raw -> **Làm sạch** (`clean_only`+`persist_clean`) -> **Nén ACL** (`compress_acl`+`persist_acl`) -> **Chunking**
  (`persist_chunks`) -> **ES 2 nhánh** -> [Qdrant]. Worker env: `DOFFICE_JOB_{PG,CLEAN,ACL,CHUNK,ES,QDRANT}_WORKERS`.
  ⚠️ `_make_ingestor` đặt `chunking_service=None` (doffice KHÔNG dùng ChunkingService -> tránh tạo MinioStorageClient
  mỗi văn bản gây CHẬM/TREO). run_qdrant đọc PG -> embed.
  Mode: env `DOFFICE_JOB_MODE=once|loop` (+ `DOFFICE_JOB_INTERVAL` giây khi loop). Checkpoint incremental dùng
  `gte` + nhớ id_vb mốc trong `checkpoint.search_after` để KHÔNG lặp văn bản mốc. ⚠️ Checkpoint/progress/pending
  TÁCH theo PHẠM VI quét (`_scope_suffix`: `_dv258` cho đơn vị, `''` cho tất cả) -> đổi đơn vị KHÔNG tái dùng mốc
  `updated_after` của đơn vị trước (trước đây dùng chung 1 key -> đổi đơn vị bị lọc sạch "quét không ra"). Dashboard
  hiện "Phạm vi" (đơn vị/id lẻ/tất cả) đang quét.
  **Chưa ACL = BỎ QUA, KHÔNG chờ** (feeder `_enqueue_acl_filtered`): VB chưa có ACL (đơn vị/phòng ban/nhân viên
  list rỗng) -> bỏ qua + ghi id_vb vào `log/doffice_unified/.pending_acl.txt` (`PendingAclStore`); đầu lần chạy sau
  `_retry_pending_acl` fetch lại theo id_vb (KHÔNG qua scroll `gte`) rồi thử tiếp. Dashboard hiện số "chưa ACL" +
  số batch đã/đang chạy (`UnifiedStats.acl_pending/acl_skipped/batches_fed/batch_size`). (Trước đây chờ vô hạn ->
  treo khi VB không bao giờ có ACL; đã bỏ cơ chế chờ + env `DOFFICE_ACL_WAIT_*`.)
  **Log**: TẤT CẢ job ghi vào `jobs/doffice_sync/log/<tên_job>/<run_stamp>/` (neo theo `logger.LOG_ROOT`, độc lập cwd);
  VB bỏ qua vì > max_chunks liệt kê ở `vanban_bo_qua_qua_chunk.log` (logger con `doffice_sync.oversize`).
- **run_qdrant** (`run_qdrant.bat`): mặc định TUẦN TỰ (1 doc/lần, embed từng chunk, KHÔNG song song -> tránh gãy
  gateway). Dashboard 2 cột + ô "Nhiều chunk (>100)" + log riêng `chunks_big.log`. Đọc PG (pg_prepared) rồi embed.
  Lọc PHẠM VI theo đơn vị như run_pg_es: `--don-vi 269 258` / env `DOFFICE_QDRANT_DON_VI` (lọc trên
  `document_metadata->access->raw_assignment->don_vi_list`, cùng field run_pg_es lọc); trống = tất cả. Dashboard hiện "Phạm vi".
- **run_delete** (`run_delete.bat`): xóa theo `--id-vb`/`--don-vi` + **chọn store** (menu PG/ES/Qdrant: gõ 1/2/3 bật/tắt,
  4=chạy, q=hủy; hoặc `--stores pg es qdrant --yes`). CHẬM cho nhiều doc -> wipe toàn bộ dùng script dưới.

## Scripts hữu ích (`scripts/`)
- `reset_all_stores.py --yes` [--keep-pg/--keep-es/--keep-qdrant]: **wipe NHANH** toàn bộ dữ liệu văn bản 3 DB
  (TRUNCATE PG + recreate ES full+chunk + recreate 2 collection Qdrant). Giữ dm_*/users/config.
- `reset_doffice_for_rechunk.py --yes`: reset cờ qdrant_indexed + wipe Qdrant Col1 để chunk lại.
- `inspect_doffice_chunk_state.py`: soi phân bố chunk/văn bản trên Qdrant.

## Quyết định/bài học quan trọng
- Bug nổ chunk: `_split_by_boundaries` (`chunker_adaptive_chunking.py`) đuôi đoạn < overlap -> bò +1 ký tự/vòng.
  Đã fix (next_start=end khi overlap kéo lùi). 412876: 1336 -> 33 chunk.
- **Fix 4 bug MẤT NỘI DUNG chunk (2026-07-03, cần re-chunk + re-embed dữ liệu cũ)**: (1) `_legal_chunks` bỏ text
  trước "Điều 1" -> giờ giữ `document_preamble`; (2) section 1-dòng bị vứt "chỉ-tiêu-đề" -> gộp section <200 ký tự
  vào section kế (`_combine_sections`) + chỉ lược khi tiêu đề theo `section_path` chunk sau; (3) OCR "Ð" (Eth) ≠ "Đ"
  -> chuẩn hoá ở `apply_spacing_fixes`; (4) `_is_feature_change_table` lỏng -> bảng thường bị ép schema 5 cột CPCIT
  mất cột. Chi tiết: PROJECT_OVERVIEW 2026-07-03 (e). Lỗi OCR bảng nguồn (rowspan lệch, ký tự Cyrillic) NGOÀI chunker.
- **Làm sạch rác OCR BẢNG trước chunk (2026-07-04, cần re-chunk + re-embed)**: ô nguồn bị OCR nổ nội dung nhiều
  dòng / lặp rác (`'H[`, cụm từ, hàng `| H[ |`) trăm-nghìn lần -> vỡ markdown -> TableChunker không cắt -> chunk bảng
  30k-53k ký tự. Fix Ở LUỒNG DOFFICE trong `ingestion_doffice_content_normalizer.py`: `_sanitize_table_cell` (gộp `\n`
  ->space, `|`->`/`, cắt fragment JSON docling `[{"bbox"...`, nén token/cụm lặp) + `_collapse_repeated_rows` áp trong
  `table_to_markdown`/`table_to_text`; bbox cắt thêm ở `strip_markdown_noise`. `_table_chunks` gắn `quality=degraded`
  nếu mảnh > 2×table_max. Kết quả: mọi chunk bảng <=~5800 ký tự, dữ liệu thật còn nguyên. Test: `tests/Chunk/chunk_test.py
  <id_vb>` (cache raw `_source` vào `tests/Chunk/raw/<id_vb>.json`, `--refresh` để tải lại).
- **Bộ test chunk có KHO VECTOR + RETRIEVAL (`tests/Chunk/`)**: `run_chunk_test.bat` giờ 2 bước — (1)
  `chunk_test.py` xuất text `output/`; (2) `useChunk/build_vector_store.py` EMBED chunk vào **Qdrant SERVER thật**
  (URL/API key từ .env) trên **collection TEST riêng** `chunk_test_chunks`+`chunk_test_docmeta` (KHÔNG đụng prod
  `hbrag_doffice_*`; override tên qua env `CHUNK_TEST_CHUNKS_COLLECTION`/`_DOCMETA_COLLECTION`) — MỖI LẦN CHẠY
  XOÁ+TẠO LẠI 2 collection test (`SKIP_EMBED=1` để bỏ bước embed). Tái dùng ĐÚNG `rag_chunk_from_database`+
  `build_embedding_text`+`qdrant_payload`+`QdrantVectorStore` (fake ORM, KHÔNG đụng PG). `run_retrieval.bat "<query>"`
  (`useChunk/retrieve.py`) = **RAG đầy đủ**: `build_query_embedding_text`->embed dense(+sparse theo manifest)->hybrid
  RRF->rerank bge->**LLM sinh câu trả lời** (`build_system_prompt`+`LLMGateway.generate`, passage đánh số `[i]` như
  `RagAnswerService`) + in nguồn. Cờ: `--no-answer` (chỉ xem chunk), `--no-rerank`, `--docmeta`, `--top-k`.
  `data/manifest.json` lưu collection+cờ sparse để build/retrieve khớp cấu hình.
- **Fix footer nuốt VB ĐÍNH KÈM (2026-07-04, cần re-chunk + re-embed)**: `footer_signature` từng nuốt cả văn bản
  đính kèm sau khối chữ ký (202570: 41443, 1479942: 34816 ký tự). Fix ở `ingestion_doffice_content_normalizer.py`:
  `_attached_document_start` (2 tín hiệu: quốc hiệu sau marker footer; hoặc sau dòng "Lưu:" + <=4 dòng chức danh/tên
  người ký mà còn nội dung dài/bảng) -> `split_footer_signature` cắt footer tại đó; `extract_attached_document_text`
  đưa phần đính kèm thành element `document_body` (`artifact_type=attached_document`, đi cùng đường phụ lục,
  `_prose_reading_pos` cộng base). Kèm: `_strip_toc_lines` lọc dòng MỤC LỤC (chấm leader >=4 + số trang) khỏi prose
  body/phụ lục/đính kèm (KHÔNG đụng `strip_markdown_noise` — dùng chung cho ô bảng); gỡ `[[TABLE_n]]` khỏi footer.
  Kiểm chứng 102 văn bản (`tests/Chunk/data.txt`): 0 chunk >6000, 0 degraded, footer max <1500; 26 test ingestion pass.
- Làm sạch `clean_for_chunking` (`chunker_text_cleaning.py`): chuẩn hoá smart-quote/dash/NBSP qua `_PUNCT_TRANS`
  (ordinal); prose bỏ `**`/`*` + dòng số trang; **bảng dùng `preserve_markdown=True`** (giữ `| --- |`). KHÔNG TCVN3,
  KHÔNG gỡ HTML, KHÔNG bỏ quốc hiệu. ⚠️ KHÔNG Write đè file này (regex chứa dải Unicode hiếm dễ lệch byte; chỉ Edit).
- Payload chunk Qdrant (nhánh doffice): ĐÃ bỏ nhóm an toàn (`database_chunk_id`/`parser`/`chunker`/`source_file` +
  list rỗng + `enriched=false`) qua `DOFFICE_REDUNDANT_PAYLOAD_FIELDS`+`DOFFICE_EMPTY_SUPPRESS_FIELDS` (`rag_chunk.py`).
  VẪN GIỮ field retrieval/citation/boost dùng (`structure_path`/`document_code`/`document_title`/`issued_date`/
  `quality_status`...) — muốn bỏ tiếp phải sửa kèm code retrieval. Xem `docs/METADATA_SCHEMA.md §9`.
- Startup KHÔNG tạo lại collection Qdrant generic: `validate_generic_vector_store_on_startup=False` (config) + guard ở
  `main._validate_vector_store_on_startup`. Chỉ dùng DOffice.
- Bảng PG rỗng (citations, graph_*, document_files...) ĐỪNG drop: gắn ORM model + query (list_documents), drop sẽ vỡ app + lệch alembic.
- Alembic: DB chia sẻ có revision không trên branch hiện tại -> ĐỪNG `alembic upgrade` mù.

## Retrieval nâng cấp schema BA + profile + chat multi-turn (2026-07-06)
- **Remap toàn bộ retrieval sang schema BA** (`document_id/document_no/title/summary/signer/issuer_org_name/
  issue_date/issue_year/id_full/chunk_order`) — bỏ tên cũ. Sửa: `retrieval_doffice_bm25.py` (doc + chunk BM25 field/
  boost/`_source`), `document_search_service.py` (ref/exact/content/org-boost/recency `issue_date`/filter năm
  `issue_year`), `document_semantic_search.py` (`_source_from_metadata`, `_rerank_content`, identifier boost,
  CRAG grading), `document_chat_service.py` (`_citation`/`_passage_text`). `DocumentSearchHit` GIỮ field cũ
  (map BA->cũ ở boundary) nên FE không vỡ; `_citation` trả CẢ key BA + key cũ song song.
- **⚠️ Fix lỗ hổng ACL doc-BM25**: `build_acl_filters` cũ dùng `acl_deny_nv/pb` (số) -> DENY KHÔNG áp trên index BA
  (`acl_deny` chuỗi `pb_/nv_`). Nay dùng `build_es_acl_filter_flat` (siết đúng). Verify: ID_NV lạ -> 0 kết quả.
- **Filter năm/tháng Qdrant qua `issue_date`** (payload không có `nam/thang`): `_payload_filter` thêm nhánh
  `date_field` -> `DatetimeRange`; 2 collection kho AI đặt `date_filter_field="issue_date"` + index payload đổi
  KEYWORD->DATETIME (`_ensure_datetime_indexes` tự migrate; đã migrate live 2 collection). KHÔNG re-embed. ES chunk/doc
  lọc range trên `issue_date` (chunk) / `terms issue_year` (doc).
- **Context expansion chuyển PG -> ES** (pipeline kho AI KHÔNG ghi chunk vào PG): `DofficeChunkBm25Store.fetch_context_chunks`
  (`_msearch` theo `id_full`+`chunk_order`±1 + chunk CHA, kèm ACL). Bỏ import PG `Chunk`/`AsyncSessionLocal`.
- **Enrich doc-source**: ES chunk + Qdrant payload KHÔNG có `document_no/summary/signer` -> `_enrich_doc_sources`
  1 call `fetch_doc_sources` trên index nguồn (chỉ đọc, kèm ACL) bù metadata cho rerank/citation/identifier boost.
- **Guard index BA/job**: store KHÔNG PUT mapping / ghi / xoá index tên `kho_ai_dung_chung*` (`_is_protected_index`) —
  thiếu index thì search trả rỗng, không tự tạo sai mapping.
- **Retrieval profile config-driven** (`retrieval_profile.py`): dataclass `RetrievalProfile` gom field/boost/lexicon
  (`org_codes`/`org_alias`/`doc_type_abbr`)/chunk_type weights/limits; `KHO_AI_PROFILE` mặc định, chọn qua setting
  `document_search_retrieval_profile`. Bài toán mới = thêm 1 profile (`register_retrieval_profile`), KHÔNG sửa lõi.
  Trọng số RRF/CRAG/rerank vẫn ở settings.
- **Chat parity + multi-turn** (`document_chat_service.py`): (1) `/chat` nay chạy nhánh BM25 doc-level SONG SONG
  (`run_doc_bm25` tách từ search — recency + org boost + identifier) như `/search`, fallback dùng hits ES khi fusion
  rỗng. (2) Request thêm `history` (list `{role,content}`, backward-compat) -> `_condense_query` LLM viết lại câu nối
  tiếp thành câu độc lập (tái dùng `should_rewrite_with_context`/`QueryRewriteService`, `asyncio.wait_for` timeout
  `document_chat_condense_timeout_s`=2.5s, lỗi/timeout -> query gốc). `meta.rewritten_query` khi khác gốc; prompt trả
  lời chèn 2-4 turn cuối; log `history_len`/`condensed`. Verify live: câu "văn bản này do ai ký?" + history ->
  condense đầy đủ ngữ cảnh -> trả lời đúng người ký.
- **Session + short-term memory server-side (2026-07-07)**: `/chat` nay backend TỰ quản hội thoại (không bắt FE gửi
  `history`). **1 BẢNG DUY NHẤT** `doffice_chat_messages` (mỗi lượt user/assistant = 1 dòng, `session_id` UUID nhóm
  hội thoại; KHÔNG bảng session riêng — chủ/thời điểm lượt cuối/thứ tự đều suy từ message: `actor_id_nv`, `created_at`
  mới nhất, `seq`). TÁCH khỏi `chat_sessions`/`chat_messages` legacy (gắn `users.id` UUID + Citation; ở đây người hỏi =
  `ID_NV` int từ JWT). Model `app/models/doffice_chat.py`, repo `repositories/doffice_chat.py`, service
  `document_chat_session_service.py` (`resolve_session`/`persist_turn` — tự mở session, NUỐT lỗi, không chặn chat).
  **Luồng**: request thêm `session_id` (optional) -> LẦN ĐẦU rỗng -> sinh `session_id` mới (chưa ghi row), trả ở
  **`meta.session_id`** (SSE); LẦN SAU gửi lại -> nạp short-term (tối đa `document_chat_session_load_messages`=20
  message cuối) làm `history`. Row chỉ sinh ở `persist_turn` cuối lượt (ghi user + assistant). **Ngưỡng 4h**
  (`document_chat_session_short_term_ttl_h`): lượt cuối cách hiện tại > ttl -> KHÔNG nạp short-term nhưng VẪN ghi tiếp
  vào cùng session (giữ TOÀN BỘ lịch sử — gồm câu trả lời LLM — để đánh giá). **Bind theo chủ**: session khớp
  `actor_id_nv` mới nạp; lệch chủ -> `session_id` mới (chống rò rỉ); `session_id` lạ chưa có message -> dùng lại id đó
  (vô hại). Backward-compat: không có `session_id` mà FE vẫn gửi `history` -> dùng `history` như cũ. Assistant kèm
  citations/evidence vào cột `meta` JSONB; phạm vi lượt hỏi vào `document_ids`. Bảng tạo lúc **startup**
  (`_ensure_doffice_chat_tables_on_startup`, checkfirst — KHÔNG alembic mù) + migration `0017` có guard. Log
  `api_request_logs` thêm `session_id`/`session_is_new`/`short_term_used`. Verify live 7 kịch bản (tạo/tái dùng/hết
  hạn vẫn ghi tiếp/bind chủ/id lạ) PASS. 476 unit test pass.
- **Cải tiến chất lượng**: (a) docmeta->chunk expansion (`_expand_chunkless_candidates`: candidate chỉ trúng
  docmeta/BM25 doc-level -> BM25 top-3 chunk của doc làm passage); (b) dedup context theo `content_hash`;
  (c) ngân sách context theo KÝ TỰ (`max_context_total_chars`=12000, `_select_context_by_budget`); (d) adaptive
  passage chat (evidence strong + top-1 rerank>=`document_chat_strong_rerank_min` -> chỉ `document_chat_strong_top_n`=5).
- **Fix `neće` (2026-07-06)**: LLM sinh token lạc (language leakage) do payload gateway không set temperature ->
  thêm `llm_temperature`(=0)/`llm_top_p` (`config.py`) đưa vào payload `ExternalLLMClient.chat/stream_chat`.
- Verify live (JWT ID_NV 90288): mã `40/QĐ-IT` -> exact 0.33s; `Võ Văn Hòa nâng lương` -> fusion strong, citation đủ
  title/signer; `năm 2025` -> mọi hit đều 2025; chủ đề EVNCPC -> context_items>0. 472 unit test pass.
- **TODO còn lại**: 9 field nghiệp vụ (`doc_type/doc_category/keywords/expiry_date/owner_department_id/security_level/
  related/reference_document_ids/priority`) hiện null ở nguồn -> filter/boost theo chúng chưa có tác dụng đến khi API
  nguồn bổ sung. Route decode JWT không verify chữ ký (bypass ACL nếu giả ID_NV) — cần JWKS khi ra khỏi gateway nội bộ.

## Document-search fusion (2026-07-02)
- `/api/document-search/search` + `DOFFICE_RETRIEVAL_ENABLED=true` -> `run_semantic_document_fusion`
  (`document_semantic_search.py`): multi-query LLM -> embed 1 lần -> Qdrant chunks+docmeta + ES chunk BM25
  (ACL + filter nam/thang từ query) -> RRF (rank THEO TỪNG query) -> context ±1 + chunk CHA heading ->
  rerank Qwen3-Reranker -> CRAG hybrid (rule + LLM chấm ambiguous, retry 1 vòng) -> `evidence_summary`.
  Trọng số/ngưỡng: settings `document_search_fusion_*`/`_crag_*`/`_rerank_*`. Plan: `PLAN_DOCUMENT_SEARCH_UPGRADE.md`.
  Đã verify live: exact/ref ~30-350ms, fusion ~1.7-3.3s warm (lần đầu 6-8s cold). ⚠️ candidate_k GIỮ 30
  (60 -> sparse prefetch Qdrant candidate=240 thỉnh thoảng treo 3-4s lúc cache lạnh — nay sparse TẮT,
  search Qdrant dense-only nên không còn nhánh prefetch đó). Log `fusion timings(ms)`.
- **3 bug hạ tầng semantic đã sửa (2026-07-03, KHÔNG cần re-embed)**:
  (1) Qwen3-Embedding-8B instruction-tuned -> query phải bọc `Instruct:...\nQuery:...` (`build_query_embedding_text`,
  setting `embedding_query_instruction`), chỉ phía query. (2) ⚠️ Qwen3-Reranker-8B bị NHÃN metadata trong content
  đánh lừa (doc lạc đề 0.79) -> ĐÃ ĐỔI `RERANKER_MODEL=BAAI/bge-reranker-v2-m3` (miễn nhiễm) + `_clean_rerank_text`.
  (3) CRAG dùng `rerank_score` thay token-overlap (settings `_crag_*_rerank`); rerank weight 0.8 + điểm thô.
- **Sparse Qdrant TẮT hẳn (2026-07-04)**: kiến trúc ES=BM25 lexical / Qdrant=dense / app=fusion+rerank.
  `sparse_embedding_enabled=False` -> factory trả None, point mới chỉ dense, search dense-only. Bối cảnh:
  BGE-M3 KHÔNG trả sparse qua gateway CPC (chỉ dense 1024) -> learned sparse bất khả thi; hashing sparse
  giá trị thấp khi ES BM25 đã lo lexical. Qwen3-Embedding-8B (4096) > BGE-M3 (1024) -> GIỮ dense hiện tại.
- **Query hỗn hợp nội dung+mã**: `exact` CHỈ khi thuần mã (`_is_pure_code_query`); mã kèm nội dung -> hybrid/fusion.
  Trong fusion `_apply_identifier_boost` (sau rerank): candidate khớp `ky_hieu`/`id_vb` với mã/số trong query được
  boost lên top (settings `document_search_identifier_code_boost`/`_number_boost`) + evidence=strong, giữ semantic dưới.
- **Sparse học được (DORMANT — sparse đã tắt)**: `embedding_sparse_learned.py` giữ nguyên code; muốn dùng phải
  bật `SPARSE_EMBEDDING_ENABLED=true` + `SPARSE_EMBEDDING_PROVIDER=learned` + `SPARSE_LEARNED_BASE_URL`
  và re-embed (run_qdrant).
- **Tối ưu latency fusion (2026-07-03, đo live)**: query lặp ~3.9s -> **~0.6s**, query mới (process ấm)
  -> **~1.6s**. Gồm: (1) BM25 doc-level chạy SONG SONG fusion (`bm25_hits_task`, await muộn trước RRF);
  (2) `retrieval_shared.py`: httpx client keep-alive CHUNG theo event-loop (hết bắt tay TCP mỗi call ES)
  + `TtlCache`; (3) cache ACL subject theo id_nv (TTL 300s, `_ACL_SUBJECT_CACHE`); (4) LLM expansion:
  deadline `document_search_fusion_expansion_timeout_s` (2.5s) + cache 600s; (5) cache embed query 600s;
  (6) ⚠️ CRAG LLM grading CHỈ chạy khi top-3 chưa có strong (trước chiếm ~80% latency warm ~3.2s/req,
  verdict không đổi khi top đã strong). Identifier boost chuyển lên TRƯỚC LLM grading.
- ⚠️ TODO: route decode JWT KHÔNG verify chữ ký (giả ID_NV = bypass ACL) — cần JWKS khi ra khỏi gateway nội bộ.
- **Log lời gọi API vào PostgreSQL (2026-07-05, ĐỘNG)**: bảng CHUNG `api_request_logs` — mỗi lượt `/api/document-search/search`
  (kể cả lỗi) ghi 1 dòng: `endpoint/method/client_ip`, `actor_id_nv/pb/dv` (người hỏi), `query`, `search_type`
  (exact|ref|bm25|hybrid|fusion), `mode`, `used_vector`, `result_total`, `duration_ms`, `status`(success|error)`/error`
  + 2 cột JSONB `request_params` (bỏ `jwtToken`, thêm `has_jwt`) & `response_summary` (cờ tổng + tối đa 50 hit gọn,
  KHÔNG kèm highlight/context nặng). Model `app/models/api_request_log.py`, repo `api_request_logs.py`, service DÙNG
  CHUNG `app/services/api_request_log_service.py` (`log_api_request(...)` — tự mở session, NUỐT lỗi, không làm hỏng
  response). Route `document_search` bọc try/finally gọi `_log_search`. **Thiết kế động cho API mới sau này**: chỉ cần
  gọi `log_api_request(endpoint=..., request_params=..., response_summary=...)` — cột JSONB tự do, KHÔNG cần migration.
  Bảng tạo lúc **startup** (`_ensure_api_log_table_on_startup`, `create(checkfirst=True)` — CHỈ bảng này, tránh
  `alembic upgrade` mù trên DB chia sẻ); migration `0016_add_api_request_logs` có guard "bảng đã tồn tại -> bỏ qua".
  **Đọc log**: `GET /api/document-search/logs` (BẮT BUỘC Bearer JWT) — phân trang (`limit`/`offset`, trả `total`) +
  lọc động `id_nv`/`search_type`/`status`/`q`(ilike nội dung)/`endpoint`(mặc định `document-search/search`, rỗng=mọi
  endpoint)/`created_from`/`created_to`; `include_payload=true` mới kèm 2 cột JSONB (mặc định gọn). Repo
  `list_logs`, models `ApiRequestLogItem`/`ApiRequestLogPage` (có mô tả Swagger đầy đủ).
- **Chat stream trên văn bản (2026-07-05)**: `POST /api/document-search/chat` — hỏi-đáp RAG **stream SSE**
  (`text/event-stream`), TÁI DÙNG `run_semantic_document_fusion` (Qdrant dense + ES BM25 + RRF + rerank + CRAG) rồi
  LLM sinh câu trả lời grounded trên passage đánh số `[i]` (giống `RagAnswerService`/`useChunk/retrieve.py`), STREAM
  từng đoạn. **Bộ lọc phạm vi** `document_ids` (list `document_id`): có list = chỉ hỏi trên nhóm văn bản đó; rỗng/null
  = TOÀN BỘ văn bản người dùng được ACL cho phép. `jwtToken` decode `ID_NV` -> `resolve_acl_subject` (ACL luôn áp).
  Service `document_chat_service.py` (`stream_document_chat` -> `ChatStreamEvent`: `meta`/`sources`/`delta`/`done`/
  `error`). **Scope tới retrieval qua ContextVar** `_DOC_SCOPE` trong `document_semantic_search.py` (set đầu
  `run_semantic_document_fusion`, đọc ở `_search_qdrant_store` + `_search_chunk_bm25`) -> áp CỨNG ở Qdrant
  (`document_ids=` payload `document_id`) + ES BM25 (`terms document_id`), KHÔNG bị nhánh filter-fallback/CRAG bỏ,
  reset sạch sau mỗi call (an toàn đa request). Mỗi lượt ghi `api_request_logs` (endpoint `document-search/chat`,
  `search_type=chat`) ở cuối stream. `search_chunks` (`retrieval_doffice_bm25.py`) thêm param `document_ids`.

## Trang xem (route backend)
- `GET /architecture` — sơ đồ kiến trúc (HTML tĩnh `app/static/architecture.html`).
- `GET /data-stores` — liệt kê data từng store (`app/static/data-stores.html`).
- WS `/collab/{room}` — Yjs real-time cho trang đồng chỉnh ở frontend (`app/services/collab/`, `app/api/routes/collab.py`).

## Chạy
- venv: `.venv\Scripts\python.exe`. Backend: `uvicorn app.main:app --port 8000`. ES live, Qdrant live (xem config).
