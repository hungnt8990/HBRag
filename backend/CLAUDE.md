# HBRag Backend — Ghi nhớ trạng thái & quyết định (để khôi phục ngữ cảnh sau khi clear session)

> Súc tích cố ý (tiết kiệm token). Chi tiết đầy đủ: `docs/PROJECT_OVERVIEW.md` (đọc khi cần),
> schema metadata: `docs/METADATA_SCHEMA.md`. Khi hoàn thành thay đổi đáng kể -> cập nhật
> PROJECT_OVERVIEW (đừng phình to file này).

## Kiến trúc dữ liệu (DOffice 3-DB) — đang dùng
- **PostgreSQL = NGUỒN SỰ THẬT.** Bảng `documents`: `parsed_text` = noi_dung RAW;
  `document_metadata` (JSONB) = source thô + `clean`{noi_dung,tom_tat sạch} + `access`{raw_assignment,
  acl_subjects, acl_deny nén, acl_ver} + cờ `pg_prepared`/`chunk_count`/`qdrant_indexed`. Bảng `chunks` = chunk đã sạch.
- **Elasticsearch — 2 nhánh** (BM25, ACL nén, KHÔNG ACL raw): full `hbrag_doffice_documents_v1`
  (`DofficeBm25DocumentStore`) + **chunk `hbrag_doffice_chunks_es_v1`** (`DofficeChunkBm25Store`).
- **Qdrant — 2 collection** (chỉ 2 này, đã xóa collection generic): chunk `hbrag_doffice_chunks_v1` +
  docmeta `hbrag_doffice_docmeta_v1` (vector 4096). Payload có ACL + filter `nam/thang/ngay_vb/id_dv_ban_hanh` (đã tạo index).

## Pipeline (file `app/services/ingestion/ingestion_doffice_unified.py`)
- `prepare_postgres` (RAW) -> `clean_data` (in-memory: normalize+làm sạch+nén ACL) ->
  **`persist_to_postgres`** (ghi PG: clean + chunk vào bảng chunks + ACL nén, đặt `pg_prepared=True`) ->
  `index_elasticsearch` (full) + `index_elasticsearch_chunks` (nhánh chunk) -> **`embed_to_qdrant`** (CHỈ đọc PG -> embed).
- `index_qdrant` = persist + embed (legacy/1 lượt). Xóa chọn lọc: `delete_by_id_vb(id_vb, pg=, es=, qdrant=)`.
- Bỏ qua văn bản > `max_chunks` (mặc định 500): không chunk/embed, không đánh dấu (giữ pending).

## Jobs (`jobs/doffice_sync/`)
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

## Document-search fusion (2026-07-02)
- `/api/document-search/search` + `DOFFICE_RETRIEVAL_ENABLED=true` -> `run_semantic_document_fusion`
  (`document_semantic_search.py`): multi-query LLM -> embed 1 lần -> Qdrant chunks+docmeta + ES chunk BM25
  (ACL + filter nam/thang từ query) -> RRF (rank THEO TỪNG query) -> context ±1 + chunk CHA heading ->
  rerank Qwen3-Reranker -> CRAG hybrid (rule + LLM chấm ambiguous, retry 1 vòng) -> `evidence_summary`.
  Trọng số/ngưỡng: settings `document_search_fusion_*`/`_crag_*`/`_rerank_*`. Plan: `PLAN_DOCUMENT_SEARCH_UPGRADE.md`.
  Đã verify live: exact/ref ~30-350ms, fusion ~2.6-3s warm (lần đầu 6-8s cold). ⚠️ candidate_k GIỮ 30
  (60 -> sparse prefetch Qdrant candidate=240 thỉnh thoảng treo 3-4s lúc cache lạnh). Log `fusion timings(ms)`.
- **Sparse học được**: `embedding_sparse_learned.py` (độc lập, HTTP, fallback hashing); bật
  `SPARSE_EMBEDDING_PROVIDER=learned` + `SPARSE_LEARNED_BASE_URL`; đổi provider PHẢI re-embed (run_qdrant).
- ⚠️ TODO: route decode JWT KHÔNG verify chữ ký (giả ID_NV = bypass ACL) — cần JWKS khi ra khỏi gateway nội bộ.

## Trang xem (route backend)
- `GET /architecture` — sơ đồ kiến trúc (HTML tĩnh `app/static/architecture.html`).
- `GET /data-stores` — liệt kê data từng store (`app/static/data-stores.html`).
- WS `/collab/{room}` — Yjs real-time cho trang đồng chỉnh ở frontend (`app/services/collab/`, `app/api/routes/collab.py`).

## Chạy
- venv: `.venv\Scripts\python.exe`. Backend: `uvicorn app.main:app --port 8000`. ES live, Qdrant live (xem config).
