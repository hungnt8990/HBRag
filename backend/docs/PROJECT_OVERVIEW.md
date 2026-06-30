# HBRag Backend — Tổng quan dự án

> Tài liệu này mô tả tổng thể backend để người mới (hoặc Claude ở phiên sau) đọc là
> hiểu dự án có gì. **Mỗi khi hoàn thành một thay đổi đáng kể, phải cập nhật file này.**
>
> 📐 **Schema metadata chuẩn hoá** (PG/ES/Qdrant C1+C2): xem [`docs/METADATA_SCHEMA.md`](METADATA_SCHEMA.md).
> **Đã áp**: Col1 chunk gắn thêm field lọc cấp văn bản `nam/thang/ngay_vb/id_dv_ban_hanh` (qua
> `_build_c1_doc_filter_payload` + `set_acl_payload_for_document` trong `index_qdrant`) — filter thời gian/đơn vị
> ở cấp chunk, đồng bộ Col2. Tạo **payload index** cả 2 collection (`nam/thang/id_dv_ban_hanh` integer;
> `ngay_vb/loai_vb/linh_vuc/trang_thai_hieu_luc/acl_subjects/acl_deny` keyword) trong `vector_store.PAYLOAD_*`.
> **HOÃN** strip field debug (nằm trong hợp đồng payload + retrieval dùng; lợi ích dung lượng ~0).
>
> 🧹 **Dọn Qdrant + startup**: chỉ dùng 2 collection DOffice (`hbrag_doffice_chunks_v1`, `hbrag_doffice_docmeta_v1`).
> Đã xóa 2 collection generic rỗng (`hbrag_chunks_qwen3_8b_v1`, `hbrag_artifacts_qwen3_8b_v1`). Tắt validate generic
> khi startup: setting `validate_generic_vector_store_on_startup=False` + guard ở `main._validate_vector_store_on_startup`
> -> không tự tạo lại collection generic. (Bảng PG rỗng GIỮ NGUYÊN — đều gắn ORM model + query, drop sẽ vỡ app/alembic.)
>
> 🔁 **Tái cấu trúc pipeline (Giai đoạn 1)** — PG là NGUỒN SỰ THẬT: `run_unified` làm sạch + chunk + nén ACL rồi
> **GHI PG** (`persist_to_postgres`: `metadata["clean"]` nội dung sạch, `metadata["access"].acl_subjects/acl_deny`
> nén cạnh raw, bảng `chunks`, cờ `pg_prepared`); `run_qdrant` **chỉ ĐỌC PG** (`embed_to_qdrant`: chunk/clean/ACL từ
> PG -> embed, không re-chunk/re-clean; legacy chưa prepared thì tự persist). ES nhánh full giữ nguyên (đã ACL nén,
> không lưu ACL raw). Verify: persist 1 doc -> PG có clean+chunk+ACL nén; 181 test pass.
>
> 🔁 **Giai đoạn 2 — nhánh ES CHUNK** (`DofficeChunkBm25Store`, index `hbrag_doffice_chunks_es_v1`): `run_unified`
> index TỪNG chunk vào ES ngay sau khi chunk (`index_elasticsearch_chunks`: chunk_text + section_path + chunk_type +
> doc-level kế thừa `ky_hieu/trich_yeu/nam/thang/ngay_vb/id_dv_ban_hanh` + **ACL nén**, KHÔNG ACL raw). Mapping
> `vi_bm25` + ACL keyword. `_es_worker` gọi cả 2 nhánh (full + chunk); `_delete_everywhere` dọn cả ES chunk; idempotent
> (delete_by_id_vb trước bulk). Verify E2E trên ES live: ensure_index + bulk + search BM25 + ACL fields + 1 doc thật
> 3 chunk khớp. 183 test pass. (run_qdrant KHÔNG index ES — chỉ embed Qdrant.)
>
> Cập nhật gần nhất: 2026-06-30 — **Sửa bug nổ chunk ở `_split_by_boundaries`** (`chunker_adaptive_chunking.py`).
> Khi đuôi văn bản có 1 đoạn ngắn hơn `overlap_chars` sau ranh giới cuối, `_best_boundary` luôn trả về cùng vị trí
> `end` còn `start = max(end - overlap, start + 1)` chỉ tiến +1 ký tự/vòng -> nổ hàng trăm chunk gần trùng (vd
> id_vb=412876, 24K ký tự, 0 bảng, 3 điều dài -> **1336 chunk**). Fix: nếu `end - overlap <= start` thì bỏ overlap
> (`next_start = end`) để bảo đảm tiến cửa sổ. Sau fix: 412876 -> **33 chunk**. 26 test chunker pass. **Văn bản đã
> ingest trước fix (có điều/mục dài) cần re-sync để dọn chunk rác.** Đã reset toàn bộ (script
> `scripts/reset_doffice_for_rechunk.py`): 192 cờ `qdrant_indexed` -> false + wipe Col1 Qdrant -> 15.569 doc chờ
> chunk lại. `jobs/doffice_sync/run_qdrant.py` (mặc định TUẦN TỰ — 1 văn bản/lần, embed TỪNG chunk, KHÔNG song
> song để tránh gãy gateway embedding): dashboard cập nhật tại chỗ `_status_seq` 2 cột — trái = tiến độ + văn bản
> đang chạy (embed d/N) + vài văn bản gần đây kèm số chunk; **phải = ô "view" văn bản nhiều chunk (>N)**. Theo dõi
> văn bản > ngưỡng (env `DOFFICE_QDRANT_BIG_CHUNK`/`--big-chunk`, mặc định 100) + ghi **log riêng `chunks_big.log`**
> (logger con `doffice_sync.chunks`). Mode song song (`_status`) dùng chung ô qua `_big_chunk_box`. stdout không phải
> tty -> in gọn 1 dòng/văn bản. Script soi: `scripts/inspect_doffice_chunk_state.py`.
>
> **Bỏ qua văn bản quá nhiều chunk**: `index_qdrant(max_chunks=...)` — nếu số chunk > ngưỡng (env
> `DOFFICE_QDRANT_MAX_CHUNK`/`--max-chunk`, mặc định **500**) thì đặt `item.skipped=True` và **return sớm**: KHÔNG
> embed, KHÔNG đụng PG/Qdrant, **KHÔNG đánh dấu `qdrant_indexed`** (văn bản giữ pending để đánh giá sau). Job đếm
> riêng `stats.skipped`, hiện đỏ ⊘ trong ô "view" + ghi `chunks_big.log`. Vì không đánh dấu, văn bản bỏ qua sẽ
> được quét lại mỗi vòng (re-chunk nhanh rồi bỏ qua tiếp).
>
> **Làm sạch trước chunk** (`chunker_text_cleaning.clean_for_chunking`, tham khảo `clean_text.py`): thêm chuẩn hoá
> dấu câu/khoảng trắng đặc biệt về ASCII (NBSP, soft hyphen, smart-quote, en/em dash — qua `_PUNCT_TRANS` khoá
> ordinal) cho CẢ prose lẫn bảng; riêng PROSE bỏ thêm nhấn mạnh `**`/`*` + dòng số trang trần. Bảng gọi với
> `preserve_markdown=True` (giữ `| --- |`, `*`, dòng-chỉ-số trong ô). KHÔNG dùng TCVN3-convert (0/800 doc, marker
> `© « » µ` trùng Latin-1 hợp lệ -> hỏng text), KHÔNG gỡ HTML (normalizer đã strip), KHÔNG bỏ quốc hiệu (header đã
> tách). ⚠️ KHÔNG dùng Write ghi đè cả file này — `_FOREIGN_SCRIPT_RE`/`_CONTROL_RE` chứa dải Unicode hiếm dễ lệch
> byte; chỉ Edit chèn thêm. **ES cũng làm sạch**: `clean_data` đổi `clean_noi_dung = clean_for_chunking(clean_text)`
> (trước chỉ `.strip()`) -> token BM25 sạch & nhất quán với chunk Qdrant + `tom_tat`.
>
> **Sơ đồ kiến trúc real-time đồng chỉnh (React Flow + Yjs)**: trang FE `/architecture-flow` (`app/architecture-flow/`,
> `@xyflow/react` + `yjs` + `y-websocket`, render client-only) — nhiều người kéo thả/sửa/nối node thấy nhau real-time
> + con trỏ + presence. Backend nhúng **Yjs WebSocket server TRONG FastAPI** (1 process): `app/services/collab/`
> (`pycrdt` + `pycrdt-websocket`) + route WS `/collab/{room}` (`app/api/routes/collab.py`). Lưu trữ thủ công:
> `Doc.observe` -> snapshot `Doc.get_update()` ra `backend/data/collab/<room>.ybin` mỗi ~2s, khôi phục bằng
> `apply_update` khi tạo lại room. Khởi/tắt trong lifespan `main.py`. Đã verify E2E (2 client Python: sync 2 chiều +
> persist + restore PASS). FE build/tsc/lint pass. Sơ đồ tĩnh cũ vẫn ở `GET /architecture` (`app/static/architecture.html`).
>
> **Filter "đã có point Qdrant" (FE + API)**: `GET /api/documents` thêm query `qdrant_indexed` (true/false) ->
> `DocumentRepository.list_documents` lọc SQL `coalesce(document_metadata->>'qdrant_indexed','false')='true'`.
> FE `lib/api.ts:listDocuments({qdrantIndexed})` + checkbox "Chỉ văn bản đã có point trên Qdrant" trong
> `DocumentSearchView` (`app/page.tsx`). Mục đích: lọc văn bản đã embed để soi chất lượng point sau khi chạy job.
>
> Cập nhật trước: 2026-06-29 (af) — **Job Qdrant: embed TỪNG chunk (1 request/chunk) + log tiến độ chunk**.
> `VectorIndexingService.index_document` thêm `embed_batch_size` + `on_embed_progress` -> embed theo lô (=1 -> từng
> chunk, request nhỏ dễ qua gateway yếu; None -> cả doc 1 lần như cũ, không đổi ingestion khác). Config
> `doffice_embed_request_batch_size=1`. `index_qdrant` truyền batch_size + callback; `run_sequential` in
> `· đã embed i/n chunk`. Lưu ý: 50 chunk = **50 point** Qdrant (batch chỉ gộp lúc TÍNH embedding, không đổi số
> point). Verify (mock): 7 embed call cho 7 chunk. 430 test pass.
>
> Cập nhật trước: 2026-06-29 (ae) — **Job Qdrant: chế độ TUẦN TỰ + log chi tiết + --limit**.
> Thêm `run_sequential()` (cờ `--sequential` / `DOFFICE_QDRANT_SEQUENTIAL=1`): xử lý 1 văn bản/lần (KHÔNG song
> song), IN log từng bước: `[n/total] id_vb=X — làm sạch… | chunk=Y | lưu PG + đang embed… | ✓ XONG Y chunk
> trong Zs | tổng: a/total doc · b chunk · cs`. Lỗi -> in `✗ LỖI sau Zs: <loại>` -> thấy rõ kẹt ở bước nào. Thêm
> `--limit`/`DOFFICE_QDRANT_LIMIT` (xử lý tối đa N văn bản rồi dừng — test). Tách `_build_ctx`/`_make_ingestor`.
> run_qdrant.bat: bật SEQUENTIAL=1, WORKERS=1. Verify (mock embed): log đúng, done=2 chunk=9. 430 test pass.
>
> Cập nhật trước: 2026-06-29 (ad) — **Job Qdrant: LƯU chunk vào PG (đổi yêu cầu)**.
> Yêu cầu mới: run_qdrant = làm sạch -> chunk -> **lưu chunk vào PostgreSQL** -> embedding. Thêm cờ
> `doffice_store_chunks_in_pg` (giờ = **True** = giữ chunk; trước thử False/xoá). `index_qdrant`: xoá chunk cũ của
> doc -> create_chunks (lưu PG) -> VectorIndexingService embed -> GIỮ chunk (không xoá khi flag True). 430 test pass.
>
> Cập nhật trước: 2026-06-29 (ac) — **document-search: jwtToken là token NGOÀI (CPC), decode-only lấy ID_NV**.
> `_id_nv_from_jwt` đổi từ verify-HS256-của-mình sang **decode payload KHÔNG verify** (token do hệ thống ngoài cấp:
> iss=CPC, RS256 — mình không giữ khóa). Lấy field `ID_NV` trong payload (vd ID_NV="90288", IDDONVI="256") -> int ->
> lọc ACL. Hàm thành sync, bỏ get_db_session/AuthRepository khỏi route. Verify: token thật của user -> ID_NV=90288
> -> trả đúng VB quỹ phúc lợi (108/QĐ-IT). Lỗi: thiếu/sai định dạng -> 401; không có ID_NV -> 403. Test mock
> `_id_nv_from_jwt` (sync). 430 test pass. (LƯU Ý: decode-only KHÔNG verify chữ ký -> chỉ tin token từ upstream CPC.)
>
> Cập nhật trước: 2026-06-29 (ab) — **document-search đổi hợp đồng: bỏ Bearer, nhận type + jwtToken**.
> `POST /api/document-search/search` KHÔNG còn yêu cầu Bearer (bỏ `get_current_user`). Body mới: `query`, `top_n`,
> `jwtToken`, `type` (Literal EO|DO). `type=DO` -> `_id_nv_from_jwt` decode jwtToken (verify chữ ký+hạn) lấy
> `sub`=User UUID -> load User -> `id_nv` -> tra cứu DOffice (ES BM25 + ACL). `type=EO` -> trả rỗng (làm sau).
> `type` khác -> 422; token rác/hết hạn -> 401; user không có id_nv -> 403. `DocumentSearchRequest`: id_nv thành
> optional (tự lấy từ token), thêm jwtToken+type. Test rewrite: mock `_id_nv_from_jwt`, bỏ 2 test Bearer/X-API-Key
> cũ, thêm test EO/missing-type/bad-token. Verify: DO+token hungnt -> id_nv=90288 -> 20 kết quả (top 108/QĐ-IT).
> 430 test pass.
>
> Cập nhật trước: 2026-06-29 (aa) — **API document-search: BM25 + ACL only (bỏ embed) + sửa nhầm index**.
> `/api/document-search/search` chậm (treo 30s) vì: (1) embed câu hỏi cho hybrid kNN nhưng model embedding chết;
> (2) query NHẦM index rỗng `hbrag_documents_v1` (dữ liệu DOffice ở `hbrag_doffice_documents_v1`). Sửa: thêm cờ
> `document_search_bm25_only` (mặc định True, env `DOCUMENT_SEARCH_BM25_ONLY`) → `execute_document_search` hạ
> "hybrid"→"bm25" (KHÔNG embed); và trỏ store sang `settings.doffice_documents_index_name` (bỏ ensure_index, job
> quản lý index). Kết quả: 0.5–1.2s, BM25 + ACL filter, trả đúng (query "chi quỹ phúc lợi" → "108/QĐ-IT Về việc chi
> từ Quỹ phúc lợi"). Đổi cờ về false để dùng lại hybrid khi gateway embed khỏe. 429 test pass.
>
> Cập nhật trước: 2026-06-29 (z) — **Tách 2 JOB riêng (PG+ES) và (Qdrant) + chạy lặp định kỳ**.
> Vì model embedding `Qwen3-Embedding-8B` chập chờn (gateway treo, model khác + chat OK), tách ingest thành 2 job
> độc lập: **Job 1 `run_pg_es.bat`** (`run_unified --skip-qdrant`): source ES → PG (raw) + Làm sạch + ES, KHÔNG embed
> → chạy được cả khi model embedding chết. **Job 2 `run_qdrant.bat`** (`run_qdrant.py` MỚI): đọc PG (doc có cờ
> `qdrant_indexed != true`) → clean → chunk → embed → Qdrant Col1+Col2 → set `qdrant_indexed=true`. Cờ điều phối:
> `prepare_postgres` đặt False (re-sync tạo lại doc → False → embed lại), `index_qdrant` set True sau khi embed.
> Cả 2 job **chạy LẶP định kỳ**: quét xong đứng im chờ `--interval` giây (mặc định 300s) rồi quét lại; env
> `DOFFICE_JOB_INTERVAL`/`DOFFICE_QDRANT_INTERVAL`. `JobConfig.skip_qdrant`; runner bỏ luồng Qdrant khi skip
> (`_on_stage_done` coi xong = chỉ cần ES). Verify: Job 1 chạy PG+ES không kẹt gateway; Job 2 scan pending→embed
> (mock)→cờ flip→pending giảm. 444 test pass. (run_unified.bat 4-luồng vẫn dùng được khi gateway khỏe.)
>
> Cập nhật trước: 2026-06-29 (y) — **Pipeline ingest DOffice 4 LUỒNG tách bạch (PG raw → Clean → ES → Qdrant)**.
> Đổi từ 3 luồng (PG làm hết: normalize+chunk) sang 4 luồng theo trách nhiệm: (1) **PG** lưu THÔ — parsed_text=
> noi_dung thô, metadata=trường source thô + ACL THÔ (raw_assignment), KHÔNG sạch/nén/chunk; (2) **Làm sạch** (mới,
> in-memory) normalize noi_dung + làm sạch tom_tat + NÉN ACL (allow[]/deny[]); (3) **ES** lưu nội dung ĐÃ SẠCH +
> ACL ĐÃ NÉN (bỏ ACL thô); (4) **Qdrant** chunk in-memory (`build_doffice_chunks`) → embed → Col1+Col2 (chunk ghi
> PG tạm để embed rồi xóa → PG chỉ giữ raw). `DofficeUnifiedIngestor`: 4 method `prepare_postgres/clean_data/
> index_elasticsearch/index_qdrant`; `DofficeJobItem` mang dữ liệu 4 giai đoạn. Runner thêm `_clean_worker` +
> `q_clean` (PG→Clean→{ES,Qdrant}); `JobConfig.clean_workers`, env `DOFFICE_JOB_CLEAN_WORKERS`; dashboard 4 dòng.
> **ĐÃ XOÁ sạch dữ liệu DOffice 3 DB** (PG 3316→0, Qdrant 2 collection, ES index) + checkpoint/progress để đồng bộ
> lại. Verify: PG raw, ES sạch+ACL nén, chunk sinh ra, ACL nén đúng với quyền thật. 444 test pass. (Lỗi embed khi
> chạy = gateway ReadTimeout, hạ tầng — không phải pipeline.)
>
> Cập nhật trước: 2026-06-29 (x) — **Đăng nhập Active Directory (LDAP) + auto-map dm_nhan_vien**.
> Thêm `POST /api/auth/login-ad`: (1) xác thực AD bằng LDAP bind `domain\username` (`app/services/ad_auth.py:
> authenticate_ad`, dùng `ldap3`, tương đương `CheckUserAD` của api_ktht_v2.0; domain `cpc-ad.evncpc.vn`); (2)
> `lookup_nhan_vien` map username AD -> `dm_nhan_vien` lấy `id_nv` (xử lý tiền tố `evncpc\`, hoa/thường, email);
> (3) `_provision_ad_user` TỰ TẠO/cập nhật User gắn `id_nv` + vai trò UNIT_USER (không mật khẩu local, không cần
> tạo tài khoản thủ công); (4) trả JWT thường -> chat dùng `id_nv` áp ACL. Config `ad_*` + .env `AD_ENABLED=true`/
> `AD_DOMAIN`. Verify map hungnt->id_nv=90288, provision không trùng, luồng login-ad (mock bind) ra token đúng.
> 429 test pass. (Bind AD thật chỉ test được trong mạng có AD.)
>
> Cập nhật trước: 2026-06-29 (w) — **FIX HIỆU NĂNG O(n²): normalize văn bản lớn 45s -> 2.6s**.
> Luồng 1 (PG) chậm vì `infer_table_name` gọi `html_to_plain_text(raw_text[:table_start])` cho MỖI bảng -> chuyển
> TOÀN BỘ nội dung trước bảng sang plain text (bảng cuối ~3.3MB) = O(bảng × kích thước) = O(n²); 10/974 văn bản
> >500KB (max 3.3MB) mất 25-45s/cái -> nghẽn cả PG. Fix: (1) `infer_table_name` chỉ chuyển CỬA SỔ `_TABLE_NAME_WINDOW
> =8000` ký tự trước bảng (đủ lấy heading); (2) gate `has_appendix` tính 1 LẦN ở `parse_html_tables` thay vì search
> `before` mỗi bảng. id_vb=220697: 45.7s -> 2.66s (~17×). Tên bảng giữ nguyên (52 test pass). + thêm retry Qdrant
> (ReadTimeout gateway) 3 lần backoff. (Lưu ý pg_workers=2 khi WORKERS=8 -> tăng `DOFFICE_JOB_PG_WORKERS` nếu cần.)
>
> Cập nhật trước: 2026-06-29 (v) — **Resume theo từng VB + fix delete-theo-đơn-vị + xoá .bat job cũ**.
> (1) FIX QUAN TRỌNG delete-theo-đơn-vị: `run_delete._id_vbs_for_don_vi` đổi từ `id_dv_ban_hanh` (đơn vị BAN HÀNH,
> sai) sang `access.raw_assignment.don_vi_list` (đơn vị QUẢN LÝ/NHẬN — KHỚP với `--don-vi` lúc sync). Trước: VB do
> 251 ban hành gửi tới 256 -> `delete --don-vi 256` (id_dv_ban_hanh) chỉ khớp 4 VB; nay khớp 436 VB. Đã verify
> Qdrant delete THẬT (3 point -> 0). (2) RESUME: `ProgressStore` (file `logs/doffice_unified/.progress.txt`) ghi
> id_vb đã xong CẢ ES+Qdrant; runner load lúc đầu -> feeder bỏ qua; ghi từng VB hoàn tất; xoá file khi cả run xong
> (kill giữa chừng -> file còn -> lần sau resume, VB đang embed làm lại từ đầu vì idempotent). `--full-scan` xoá tiến
> độ. Summary thêm "Bỏ qua". (3) Xoá `jobs/run_doffice_sync.bat` (job CŨ run.py, đã thay bằng run_unified). 41 test pass.
>
> Cập nhật trước: 2026-06-29 (u) — **Dashboard job: chỉ báo "đang nạp/đã nạp hết" + "đang embed" (không tưởng treo)**.
> Job KHÔNG treo — Luồng 3 (Qdrant/embed) là nút cổ chai (~5s/VB) nên số chạy chậm, dễ tưởng dừng. PG (Luồng 1)
> CHẠY ĐỘC LẬP vượt trước (backpressure maxsize=200). Thêm: `UnifiedStats.qdrant_current` (id_vb đang embed),
> `runner.feeding_done` (True khi nạp hết). Dashboard: Luồng 1 hiện "đang nạp…/đã nạp hết ✓", Luồng 3 hiện
> "· đang embed <id_vb>" -> thấy rõ còn sống + 3 luồng độc lập (PG=200 / ES=180 / Qdrant=15). Verify --don-vi 256
> --limit 10 hoàn tất 10/10/10 51s. 15 job-test pass.
>
> Cập nhật trước: 2026-06-29 (t) — **Thêm CLI + .bat XOÁ văn bản DOffice (PG + ES + Qdrant)**.
> `jobs/doffice_sync/run_delete.py` + `run_delete.bat` (ASCII/CRLF): xoá theo **văn bản** (`--id-vb` /
> `DOFFICE_DEL_ID_VB`) hoặc theo **đơn vị** (`--don-vi` / `DOFFICE_DEL_DON_VI` = lọc theo `id_dv_ban_hanh` trong PG).
> Mỗi văn bản xoá khỏi PostgreSQL (Document+chunks) + Elasticsearch (theo id_vb) + Qdrant (chunks+docmeta theo
> document_id) — tái dùng method mới `DofficeUnifiedIngestor.delete_by_id_vb`. Có xác nhận 'yes' (trừ `--yes` /
> `DOFFICE_DEL_YES=1`), spinner + summary box. Verify: xoá 1479029 -> PG=0/ES=404, re-ingest khôi phục OK. 41 test pass.
>
> Cập nhật trước: 2026-06-29 (s) — **Job: dashboard 3 luồng "đứng im" + hết WARNING spam**.
> Trước: console job bị flood `WARNING Unable to determine exact DOffice source_span...` (chèn ngang spinner ->
> "chạy loạn"). Fix: (1) hạ warning source_span -> `logger.debug` (fallback bình thường của summary/header/footer);
> (2) `_quiet_console` console-level -> ERROR (chi tiết vẫn vào file qua `setup_job_logging`, logger `doffice_sync.*`
> propagate=False nên không mất); (3) `cs.Spinner` nâng cấp ĐA DÒNG — render khối nhiều dòng cập nhật TẠI CHỖ (cursor
> up), frame quay ở dòng đầu, xóa sạch khi stop; (4) `_status_line` -> dashboard 3 luồng (PG/ES/Qdrant) mỗi dòng:
> xong / đang chờ (scanned - done) / lỗi. Job chạy thật sạch, 444 test pass.
>
> Cập nhật trước: 2026-06-29 (r) — **`/api/document-search` bắt buộc Bearer auth (bỏ X-API-Key)**.
> Trước: `require_document_search_access` chỉ chặn khi `document_search_api_key` được cấu hình; chưa cấu hình ->
> gọi TỰ DO không cần auth (lỗ hổng). Fix: 2 route (`/search`, `/acl`) dùng `Depends(get_current_user)` (Bearer JWT
> bắt buộc -> 401 nếu thiếu token), bỏ X-API-Key. Test cũ (X-API-Key) rewrite -> kiểm Bearer (pop override
> get_current_user -> 401). 429 test pass. CHƯA làm: body vẫn nhận `id_nv` tự do -> user đăng nhập vẫn tra theo
> id_nv bất kỳ (nên gắn id_nv theo token để chống giả mạo — TODO).
>
> Cập nhật trước: 2026-06-29 (q) — **Chunk thân giữ heading cha trong "Mục:" (vd "1. CPCIT")**.
> Triệu chứng: chunk "1.1. GIS 110kV..." mất "CPCIT" (đơn vị chịu trách nhiệm). Gốc: `section_path` ĐÃ chứa cha
> `['1. CPCIT:', '1.1...']` nhưng dòng "Mục:" (build_body_evidence_chunks) chỉ in `section_title` (con). Fix: dòng
> "Mục:" nối CẢ `section_path` (cha > con) cho chunk thân (artifact != appendix; appendix giữ section_title vì
> `_merge_appendix_preamble` lo tiền tố "Phụ lục NN"). Verify 6515: "Mục: 1. CPCIT: > 1.1...", "2. Các CTĐL > 2.1",
> "3. KHoPC: > 3.1". 429 test pass.
>
> Cập nhật trước: 2026-06-29 (p) — **Job unified thành PIPELINE 3 LUỒNG (producer-consumer)**.
> `DofficeUnifiedIngestor.ingest()` tách 3 giai đoạn: `prepare_postgres` (Luồng 1: ACL+normalize+ghi PG Document&chunks,
> trả `DofficeJobItem`) -> `index_elasticsearch` (Luồng 2: BM25 doc) + `index_qdrant` (Luồng 3: embed chunk từ PG +
> Qdrant chunks/docmeta+ACL). `UnifiedJobRunner` dựng 3 pool worker + hàng đợi asyncio (`q_pg`→`q_es`+`q_qdrant`,
> maxsize=200 backpressure); Luồng 1 đẩy item vào CẢ 2 hàng đợi cho Luồng 2&3 chạy song song. Mỗi worker session DB
> riêng. Số worker: `pg_workers`/`es_workers`/`qdrant_workers` (mặc định suy từ max_workers; Qdrant đông nhất) qua env
> `DOFFICE_JOB_PG/ES/QDRANT_WORKERS`. Checkpoint LƯU CUỐI (incremental updated_after; ngắt giữa chừng -> quét lại từ mốc
> cũ, idempotent). Summary/spinner hiện 3 luồng (PG/ES/Qdrant). Smoke-test 50 item qua đủ 3 luồng, không deadlock; 41 test pass.
>
> Cập nhật trước: 2026-06-29 (o) — **Fix thứ tự chunk triệt để: thống nhất tọa độ qua `reading_pos`**.
> Gốc: source_span ở 3 hệ tọa độ khác nhau (body theo base_clean_text, prose phụ lục theo tọa độ CỤC BỘ của
> appendix_text 0..N, bảng theo raw_text HTML) -> prose phụ lục (vd "1. Mục tiêu" start=181) chen vào GIỮA body.
> Fix: thêm `reading_pos` (vị trí trong base_plain_text — hệ nhất quán có placeholder [[TABLE_N]]): bảng lấy vị trí
> placeholder (`normalize`), prose phụ lục = `appendix_span.start + span cục bộ` (`_prose_reading_pos`), body/footer
> dùng source_span (base_clean ≈ tiền tố base_plain). `_reorder_chunks_by_source` sort theo `reading_pos`.
> Verify 6515: header→body→footer→Phụ lục 01 bảng→Phụ lục 02 prose→bảng F-class xen kẽ ĐÚNG thứ tự đọc. 429 test pass.
>
> Cập nhật trước: 2026-06-29 (n) — **Fix thứ tự chunk: footer thiếu source_span**.
> Triệu chứng: chunk "Phụ lục: DANH SÁCH..." (heading phụ lục) chen giữa body và footer, lẽ ra phải ở DƯỚI footer.
> Gốc: element `footer_signature` tạo KHÔNG có `source_span` (None) -> `_reorder_chunks_by_source` đặt nó sau chunk
> thực liền trước (sai). Fix: `normalize_doffice_source` tính `footer_span` = `_source_span_for_text(base_plain_text,
> footer_text)` (footer bị `split_footer_signature` tách khỏi body nên phải tìm lại trong base_plain_text, cùng hệ
> tọa độ với appendix/table), truyền qua `build_elements` -> gắn `source_span` cho element footer. Verify 3730:
> body → footer(start=1275) → phụ lục heading(1499) → table(1597). 429 test pass. (Ghi chú: span body/header theo
> base_clean_text, appendix/footer/table theo base_plain_text — 2 hệ tọa độ nhưng base_clean_text ≈ prefix nên sắp đúng.)
>
> Cập nhật trước: 2026-06-29 (m) — **Bổ sung tài liệu Swagger/OpenAPI cho toàn bộ API**.
> `app/main.py`: thêm `description` (hướng dẫn auth + mô tả nhóm API) + `openapi_tags` (10 nhóm có mô tả) cho
> `FastAPI(...)`. Thêm `summary` tiếng Việt cho 56/56 endpoint (documents 17, admin 13, knowledge-bases 7, memory 5,
> auth/search 4, health 1; chat/document-search/doffice-acl đã có). Authorize (HTTPBearer) sẵn có. Xem tại `/docs`
> (Swagger) hoặc `/redoc`. 237 route-test pass.
>
> Cập nhật trước: 2026-06-29 (l) — **Bỏ chunk tóm tắt khỏi collection chunk DOffice**.
> `build_doffice_chunks` bỏ qua element `document_summary` -> collection `hbrag_doffice_chunks_v1` chỉ còn nội dung
> thật (header + body + table + appendix). Summary VẪN được `build_document_summary` sinh + lọc PII (dùng cho
> docmeta embed/metadata), chỉ không thành chunk. 2 test cập nhật (kiểm PII trên `normalized.summary_text`). 46 test
> pass. (Ghi chú: "Đợt 2" lặp trong bảng 3730 do ô rowspan trong DỮ LIỆU bị giãn từng dòng; "Đợt 1" không lặp vì
> nằm ở DÒNG HEADER "STT Đợt 1" — bất đối xứng từ cấu trúc bảng OCR, không phải lỗi chunk.)
>
> Cập nhật trước: 2026-06-29 (k) — **Nối doffice retrieval vào chat + admin bypass + checkbox FE**.
> Khi `doffice_retrieval_enabled=True`: `get_rag_answer_service` dùng `build_doffice_two_stage_search` làm
> `reranking_service` (drop-in) + `artifact_first_retrieval_service=None` -> chat tìm trong collection doffice mới.
> `RagAnswerService.answer/answer_stream` thêm tham số `acl_subject` (truyền vào `_run_reranking_search`; tách khỏi
> artifact retrieve). Chat route dựng `acl_subject` qua `_build_acl_subject` (super_admin_roles); checkbox FE
> `admin_view_all` (mặc định True): user LÀ admin -> ép `is_super_admin` (bỏ lọc ACL, xem tất cả); user thường tick
> vô hiệu. `_filter_accessible_context_chunks` không chặn doffice (chunk không có key `access`). 429 test pass, tsc OK.
> ⚠️ Phải đặt `DOFFICE_RETRIEVAL_ENABLED=true` trong .env để chat dùng doffice.
>
> Cập nhật trước: 2026-06-29 (j) — **Sắp chunk DOffice theo thứ tự đọc + chẩn đoán chat chưa nối doffice**.
> (1) `build_doffice_chunks` thêm `_reorder_chunks_by_source`: 2 vòng prose+bảng sinh [prose]+[bảng] lệch thứ tự đọc
> -> sắp lại theo `source_span.start` (summary/header ghim đầu; chunk fallback document-scope giữ ngay sau chunk thực
> trước) rồi gán lại chunk_index. KHÔNG đổi content -> overlap/embedding/ACL nguyên vẹn. Neighbor expansion theo
> `article_number` (DOffice không có) nên không bị ảnh hưởng. 216 test pass. (2) CHẨN ĐOÁN: chat `/api/chat/rag`
> dùng artifact-first + rerank, KHÔNG dùng `doffice_retrieval_enabled` (chỉ `/api/search` dùng) -> chat KHÔNG tìm
> nội dung doffice (collection mới). + admin chưa bypass ACL ở chunk-level. TODO: nối doffice two-stage vào answer
> service + super_admin bypass + checkbox FE.
>
> Cập nhật trước: 2026-06-28 (i) — **ACL 2-list (acl_subjects + acl_deny) + FE mảng 1 dòng**.
> Gộp deny thành 1 list keyword prefixed `acl_deny` ["pb_/nv_"] (bỏ `acl_deny_pb`/`acl_deny_nv` số); allow vẫn là
> `acl_subjects` ["dv_/pb_/nv_"]. `security_acl_payload`: thêm `F_DENY`+`acl_deny_keys_from_acl`+`acl_subject_to_deny_keys`
> +`_parse_prefixed_ids`; `to_chunk_payload_flat` chỉ còn {acl_subjects, acl_deny}; `from_chunk_payload`/`subject_can_access`
> hỗ trợ cả mới & cũ; filter flat (Qdrant+ES) must_not dùng `acl_deny` + GIỮ `acl_deny_pb/nv` cũ (tương thích dữ liệu chưa
> reindex — KHÔNG lộ quyền). Cập nhật `DofficeBm25DocumentStore` (mapping+upsert+update_acl), unified ingestor `_resolve_acl`,
> `vector_store` payload index (+acl_deny keyword). Verify allow/deny/round-trip. (B) FE: viewer Qdrant dùng `formatCompactJson`
> — mảng toàn primitive (acl_subjects/don_vi_list...) gom về 1 DÒNG. 429 test pass, tsc OK. CHƯA reindex dữ liệu cũ.
>
> Cập nhật trước: 2026-06-28 (h) — **Gọn metadata Qdrant + gộp tiêu đề Phụ lục 02**.
> (1) ACL gọn: `DofficeUnifiedIngestor._resolve_acl` chỉ ghi `acl_subjects`(=allow_list prefixed)+`acl_deny_pb`
> +`acl_deny_nv`, BỎ `acl_allow_dv/pb/nv` (trùng `acl_subjects`, filter không dùng — verify `subject_can_access`
> không nằm ở đường retrieval). (2) `qdrant_payload`: chunk doffice (source_type==doffice_elasticsearch) bỏ
> `DOFFICE_REDUNDANT_PAYLOAD_FIELDS` (ACL hệ cũ allowed_*/denied_*/owner_*/scope/..., alias doc_code/doc_codes/
> identifiers/issuer/issuing_org/subject, vài mảng rỗng); GIỮ metadata nghiệp vụ (id_vb/ky_hieu/document_code/
> trich_yeu/table_name/platform...). Pipeline cũ (non-doffice) KHÔNG đổi. (3) Phụ lục 02: tiêu đề mỏng "Phụ lục NN"
> không có bảng -> gộp vào dòng "Mục:" của chunk kế (`_merge_appendix_preamble`), hết chunk tiêu đề mỏng đứng riêng.
> CHỜ chốt: gộp deny thành 1 `deny_list` prefixed (đụng filter chung — cần test bảo mật). 80 test pass.
>
> Cập nhật trước: 2026-06-28 (g) — **Gộp tiêu đề Phụ lục vào chunk bảng (không tách)**.
> Trước: "Phụ lục 01" bị tách 2 chunk — chunk prose tiêu đề + chunk bảng. Fix: (1) `infer_table_name` gộp
> marker "Phụ lục NN" + dòng mô tả ngay dưới -> tên bảng đầy đủ "Phụ lục 01 — PHƯƠNG ÁN SÁP NHẬP DỮ LIỆU GIS
> 110kV, GIS TRUNG THẾ"; (2) `build_doffice_chunks` bỏ chunk prose phụ lục có tiêu đề trùng/khớp-prefix tên một
> chunk bảng (giữ các mục nội dung thật: "1. Mục tiêu", "2. Chi tiết...", "3./4. Khởi tạo..."). Kết quả: Phụ lục
> 01 = 1 chunk (tiêu đề đầy đủ + bảng). Verify 6515. 52 test pass.
>
> Cập nhật trước: 2026-06-28 (f) — **FE tra cứu văn bản: id_vb/ký hiệu/point Qdrant + nút xóa 3-DB**.
> (1) List API (`GET /api/documents`) trả thêm `id_vb`, `ky_hieu`, `qdrant_point_count` (đếm point collection
> chunks mới per-doc, best-effort gather); thêm `QdrantVectorStore.count_points_for_document`. (2) Route
> `DELETE /api/documents/{id}` mở rộng: ngoài Qdrant cũ + ES chunk cũ + storage + PG, nay xóa thêm 2 collection
> doffice mới (`get_doffice_chunks/docmeta_vector_store`) + ES BM25 doc-level (`DofficeBm25DocumentStore.delete_by_id_vb`).
> (3) FE `DocumentSearchView`: card hiện ký hiệu + id_vb + "Qdrant: N point" + nút xóa (Trash2, confirm) gọi
> `deleteDocument` rồi reload; modal subtitle hiện ký hiệu/id_vb. CHẨN ĐOÁN: Qdrant ĐÃ ghi đúng (6515 id_vb=1068586:
> 34 chunk point + docmeta 3 point) — job chạy OK; trước đây FE chưa hiện count nên tưởng chưa ghi. 49 test pass.
>
> Cập nhật trước: 2026-06-28 (e) — **Fix tên bảng phụ lục DOffice (infer_table_name)**.
> Bug: regex `APPENDIX_TABLE_HEADING_PATTERN` có `\(\d+\)\b` — sau `)` là dấu cách (đều non-word) nên `\b`
> luôn fail -> heading `(N) F0X_...` trượt, rơi về "Bảng DOffice N" (F01/F03/F02 chỉ khớp tình cờ nhờ
> `"_HT"`). Thêm: heading OCR có tiền tố `####` không bị gỡ -> bảng quan hệ lấy nhầm caption. Sửa
> `ingestion_doffice_content_normalizer.py`: `_appendix_heading_name` (gỡ `#/>`, bắt `^(\d+)`, keyword,
> mã `F\d{2}_`); bảng tách trang (phần 2+, đánh số tiếp >1, cùng cột) kế thừa tên cha + "(tiếp theo)"
> (`_is_continuation_table`). Verify trên 6515 (id_vb=1068586): 19/19 bảng có tên đúng (F08..F10, Mối quan
> hệ, HinhAnh), hết "Bảng DOffice N". Phụ lục 01 KHÔNG mất (vẫn là table#0). 26 test pass.
>
> Cập nhật trước: 2026-06-28 (d) — **Chunking DOffice tune được qua DB + dọn FE**.
> (1) Profile **`doffice_admin`** thêm vào `ingestion_profiles.BOOTSTRAP_PROFILE_CONFIGS` với khóa
> `doffice_body_max_chars=3200` / `doffice_body_overlap=300` / `doffice_table_max_chars=3500`;
> `build_doffice_chunks` + `_table_chunks`/`_split_table_markdown`/`_rows_per_chunk`/`_expanded_element_chunks`
> nhận tham số (default = hằng số cũ nên không đổi hành vi nếu không tune); `chunker_chunking_service._chunk_doffice_document`
> đọc 3 khóa từ `get_profile_config("doffice_admin")`; `unified_runner` gọi `load_profile_configs` (seed+load DB)
> trước khi chunk. Seed DB ngay: `scripts/seed_doffice_admin_profile.py` (upsert). LƯU Ý: profile thường
> (`chunk_size/chunk_overlap`) KHÔNG áp dụng cho DOffice — chunker DOffice là chuyên biệt. (2) `.env`/`.env.example`
> thêm `STORE_CHUNKS_IN_PG` + tên 2 collection + `DOFFICE_DOCUMENTS_INDEX_NAME` + `DOFFICE_RETRIEVAL_ENABLED`.
> (3) FE `frontend/app/page.tsx`: gỡ 2 form DOffice ("Chạy job DOffice" + "Lấy văn bản") — giờ chỉ chạy job CLI.
> Văn bản job-ingest **ẩn ở list do ACL** (`_has_explicit_access_metadata` + user ngoài ACL) — dùng super_admin /
> `DOFFICE_SYNTHETIC_ACL_ENABLED=false` / tài khoản trong ACL để thấy. 70 test liên quan pass.
>
> Cập nhật trước: 2026-06-28 (c) — **Thiết kế lại job đồng bộ 3-DB (Phase 1–5 xong)**.
> Kiến trúc mới: **PG** = văn bản thô (full markdown) + ACL + cấu trúc normalized; **ES** = `hbrag_doffice_documents_v1`
> BM25 cấp văn bản (mọi trường + full noi_dung ĐÃ LÀM SẠCH, **không vector, không chunk**) + ACL; **Qdrant** = 2
> collection: `hbrag_doffice_chunks_v1` (vector dense+sparse từng chunk) + `hbrag_doffice_docmeta_v1` (1 point/VB,
> vector dense+sparse của metadata mọi-trường-trừ-noi_dung), payload mỗi point = ACL + id_vb + metadata truy hồi.
> Đã thêm: settings (`qdrant_chunks/docmeta_collection_name`, `doffice_documents_index_name`, `store_chunks_in_pg`),
> `app/services/vector/vector_store.py::get_doffice_chunks/docmeta_vector_store`, `retrieval_doffice_bm25.py`
> (DofficeBm25DocumentStore), `scripts/reset_all_stores.py` (XÓA 3 DB giữ danh mục/ingestion, cờ `--yes`),
> `ingestion_doffice_unified.py` (DofficeUnifiedIngestor: 1 VB → 3 DB, idempotent, enrichment TẮT, tái dùng
> ChunkingService + VectorIndexingService + DofficeIngestionService._document_metadata), `jobs/doffice_sync/
> sync/unified_runner.py` + `run_unified.py` (modes id lẻ / --don-vi / all + batch + concurrency). 41 test cũ pass.
> **Phase 5 (retrieval) ĐÃ XONG**: `retrieval_doffice_two_stage.py` — `DofficeStage1Resolver` (RRF hợp nhất ES
> BM25 doc ∪ Qdrant docmeta → top-N document_id) + `NoOpKeywordSearchService` (ES không còn chunk) + tái dùng
> `TwoStageHybridSearchService` (Stage-2 = Qdrant chunks dense+sparse trong N văn bản). Cờ `doffice_retrieval_enabled`
> (mặc định TẮT) bật ở `search.py::get_hybrid_search_service` -> áp cho cả `/search` lẫn chat (qua RerankingService).
> Env mode job: `DOFFICE_JOB_ID_VB` / `DOFFICE_JOB_DON_VI` / (trống=all) + `DOFFICE_JOB_BATCH_SIZE/WORKERS/LIMIT`
> (xem `jobs/doffice_sync/run_unified.bat`). 110 test pass. Verify e2e cần PG/ES/Qdrant/DOffice live + `EMBEDDING_DIMENSION=4096`.
>
> Cập nhật trước: 2026-06-28 (b) — **Ingest DOffice theo trạng thái PG+ES (đã có job sync)**.
> Job sync đã đưa văn bản (metadata + ACL + embedding BBQ) vào PG `documents` và ES `hbrag_documents_v1`
> nhưng KHÔNG đẩy Qdrant. `DofficeIngestionService.ingest_doffice_document` giờ điều phối theo trạng thái:
> (1) **cả 2 DB đều có** → `_ingest_existing_for_retrieval`: đọc `noi_dung_raw` từ PG (job sync lưu;
> thiếu thì fetch DOffice 1 lần + cache), normalize → cập nhật document ĐANG CÓ (không tạo mới) → chunk →
> index Qdrant + chunk BM25 ES `hbrag_chunks_bm25_v1`; gắn ACL chunk-only (`_attach_acl_from_source(...,
> write_document_index=False)` — KHÔNG đè `hbrag_documents_v1`). Đã có chunk thì skip. (2) **lệch 1 DB
> (XOR)** → xóa phần thừa (PG `_delete_existing` / ES `DocumentIndexStore.delete_by_id_vb` mới) rồi
> `_full_ingest_from_doffice`. (3) **cả 2 đều không có** / `force_refresh` → `_full_ingest_from_doffice`
> (pipeline cũ: tạo Document + chunk + index + ghi `hbrag_documents_v1`). Job sync (`jobs/doffice_sync/
> sync/processor.py`) nay lưu thêm `document_metadata["noi_dung_raw"]` (full). FE "Lấy văn bản"
> (`POST /api/documents/doffice/ingest-jobs`) KHÔNG đổi — logic mới hoàn toàn ở server. Ràng buộc mới:
> ingest cần ES (`hbrag_documents_v1`) reachable để check tồn tại (lỗi kết nối → fail, tránh xóa nhầm).
> +6 test nhánh (both/both-no-chunk/fallback/XOR-pg/XOR-es) + assert `noi_dung_raw` ở test sync.
>
> Cập nhật trước: 2026-06-28 — **Fix chunk phụ lục DOffice (ngữ cảnh bảng + prose phụ lục)**.
> (1) `_table_context_line` (`chunker_doffice_chunking.py`) giờ dựng khối ngữ cảnh đa dòng giống chunk
> prose (Văn bản+trích yếu, Ngày ban hành, Cơ quan ban hành, Phụ lục/Mục, Bảng) thay vì 1 dòng
> `Bảng: X | Văn bản: Y`; mảnh bảng dài dùng dòng `Phần: i/N`. (2) `infer_table_name`
> (`ingestion_doffice_content_normalizer.py`) nhận diện thêm heading phụ lục (Phụ lục NN, `(N) F0X_...`,
> "Tên bảng dữ liệu", "Mối quan hệ"...) **chỉ khi bảng nằm sau marker phụ lục** → bảng phụ lục có tên
> thật, không còn "Bảng DOffice N". (3) `extract_appendix_text` trích prose phụ lục (gỡ placeholder bảng
> + khối chữ ký) thành element `document_body`/`artifact_type=appendix` → trước đây `split_footer_signature`
> cắt body tại "PHỤ LỤC" làm mất hẳn tiêu đề/Mục tiêu/heading lớp dữ liệu của phụ lục; appendix bypass
> `_is_section_title_only_chunk`. +1 test (`test_doffice_appendix_prose_and_table_titles_are_captured`),
> sửa 2 test format `Phần:`. 101 test chunker/ingest pass.
>
> Cập nhật trước: 2026-06-27 — **API DOffice cập nhật ACL** (`POST /api/doffice/acl/update`).
> Nhận `id_vb` + 3 list (đơn vị/phòng ban/cá nhân) -> nén ACL (dùng chung `resolve_doffice_and_compress`)
> -> ghi PG (`document_metadata.access`) + ES (3 trường ACL phẳng). **Tự tạo mới** (đơn lẻ, KHÁC sync
> lấy batch): nếu chưa có ở PG, `_fetch_vanban` (nội dung, `doffice_vanban` term id_vb) + `_fetch_quyen`
> (quyền, `doffice_vanban_quyen` term id_vb) + `_embed` (BBQ) + `_create_document`; ACL khi TẠO lấy từ
> **quyền nguồn** (`acl_source=doffice_vanban_quyen`, fallback params), khi UPDATE lấy từ params; 404 chỉ
> khi không có ở `doffice_vanban`. Tách lớp: hàm lõi `update_document_acl` thuần domain
> (`app/services/retrieval/document_acl_update_service.py`); route mỏng + chokepoint `require_acl_update_access`
> (`doffice_acl_api_key`) để gắn phân quyền sau. Bảng mô tả: `docs/API_ACL_UPDATE.md`. +7 test.
>
> Trước đó — **Năm + đơn vị trong câu hỏi**. `build_query_body`: NĂM tường minh
> (`_extract_years`, regex 19xx/20xx) -> **FILTER cứng `terms nam`** áp cả lên knn (vì vector ngữ nghĩa
> KHÔNG phân biệt năm; chỉ boost sẽ bị knn lấn ở hybrid) + TẮT recency. MÃ ĐƠN VỊ (`_extract_orgs`,
> `_ORG_CODES`: evncpc/evn/dnpc… cpcit→'it') -> (a) `detect_search_type` ép **bm25** (chính xác lexical,
> không bị knn lấn) + (b) boost mềm `match ky_hieu <org>`. Super_admin: 'Qđ chi quỹ phúc lợi năm 2025
> của evncpc' -> 3231/QĐ-EVNCPC #1. LƯU Ý: kết quả thực tế vẫn qua ACL — nhân viên CPCIT (dv_256)
> KHÔNG thấy QĐ welfare nội bộ EVNCPC (acl_subjects = nhân viên EVNCPC tổng) là ĐÚNG quyền. +3 test.
>
> Trước đó — **Recency rerank (ưu tiên văn bản mới)**. Thêm sub-field
> `ngay_vb.date` (kiểu date, populate bằng `scripts/add_ngay_vb_date.py --apply` → `_update_by_query`
> reindex tại chỗ, KHÔNG re-embed). `build_query_body` nhánh bm25/hybrid bọc `function_score` gauss
> decay theo `ngay_vb.date` (origin=now, offset 30d, scale 365d, decay 0.5, boost_mode=multiply) →
> điểm = liên_quan × độ_mới; ref/exact KHÔNG áp (tra cứu cụ thể). Tham số request `prefer_recent`
> (mặc định True). Kết quả 'quy trinh phat trien': văn bản 2026 (258/QĐ-IT) lên #1 thay vì 2023. +1 test.
>
> Trước đó — **Tra cứu số/ký hiệu rời (search_type "ref")**. `detect_search_type`
> thêm nhánh `ref`: query dạng '<loại?> <số>' (vd `qd 258`, `258`, `kh 80` — mọi token là số/'so'/
> mã thể thức `_DOC_TYPE_ABBR`) -> `build_query_body` dùng `match_phrase ky_hieu "<số> <loại>"`
> (boost 12, đảo về thứ tự "258/QĐ") + match số (4) + term id_vb (3); KHÔNG đụng noi_dung nên
> "quyết định" phổ biến không làm nhiễu BM25. Kết quả: `qd 258` -> 258/QĐ-IT lên đầu (137 vs ~32).
> Câu có số kèm từ thường ('phụ cấp 2023') vẫn là bm25. +1 test. (Ghi chú: **embedding chỉ embed
> trich_yeu + tom_tat**, không embed noi_dung; noi_dung chỉ index BM25.)
>
> Trước đó — **Search chịu lỗi gõ (fuzzy/không dấu/viết tắt)**.
> (1) `build_query_body` nhánh bm25/hybrid: ký hiệu match thường (không fuzzy), nội dung dùng
> `multi_match best_fields` + `fuzziness=AUTO` (gõ sai 1-2 ký tự) + `minimum_should_match="2<70%"`
> (chịu gõ thiếu/nhiễu) + 1 nhánh `phrase_prefix` (gõ dở từ cuối); nhánh exact GIỮ nguyên (mã
> phải chính xác). (2) **Gõ không dấu ĐÃ chạy sẵn** — `vi_bm25` có sẵn `asciifolding` (đ→d),
> query có/không dấu cho kết quả y hệt; KHÔNG cần reindex. (3) Viết tắt: thêm analyzer search-time
> `vi_bm25_search` (lowercase→asciifolding→`synonym_graph`, `VI_SYNONYMS` 18 rule TB/QĐ/KH/ATLĐ…)
> + `search_analyzer` cho trich_yeu/tom_tat/keywords/noi_dung. **Viết tắt khai báo ở
> `config/vi_synonyms.txt`** (nguồn sự thật, version-control), đẩy vào **ES synonyms_set
> `vi_abbreviations`** (Synonyms API, `updateable=True`) qua `scripts/sync_es_synonyms.py --apply`:
> lần đầu close/open chuyển analyzer sang set-based; các lần sau chỉ cập nhật set + reload analyzer
> → KHÔNG downtime, KHÔNG reindex. +2 test query body.
>
> Trước đó — **Search ACL: id_nv là nguồn sự thật**. `document_search_service`
> bỏ `id_pb`/`id_dv` khỏi request; thêm `resolve_acl_subject(id_nv)` → tra `dm_nhan_vien`
> (tái dùng `AclSubject.from_session`) lấy id_pb/id_dv THẬT (không tin client → chống leo quyền + khớp
> đúng văn bản cấp cả phòng/đơn vị). id_nv không có trong danh mục → nv-only (~0 kết quả).
> Inspect `/acl`: ES source bỏ `raw` trùng lặp (raw=None). +2 test (resolve / unknown-nv).
>
> Trước đó — **Job DOffice sync (2 ES nguồn) + API search nâng cấp**.
> (1) `jobs/doffice_sync/` viết lại đọc TRỰC TIẾP từ `doffice_vanban` (1.26M docs, scroll
> search_after sort `ngay_capnhat.keyword`+`id_vb` — vì ngay_capnhat là text) + `doffice_vanban_quyen`
> (ACL: don_vi/phong_ban/ca_nhan_list + `quyen_checksum`), host `https://10.72.121.232:9200`
> (Basic auth `doffice`, verify=False). 5 case (created/acl_updated/emb_updated/skipped/no_acl) +
> BBQ embed graceful, KHÔNG Qdrant. Checkpoint resume + 3 bảng (job_sync_runs/checkpoints/retries,
> Base riêng, idempotent create + migration 0015). Worker session riêng (không share session như
> prompt). CASE 4 re-resolve từ quyen MỚI (không recompress_document). +15 test. CLI:
> `--full-scan/--dry-run/--id-vb/--retry-only/--don-vi/--workers/--batch/--limit`. (2)
> `DocumentIndexStore`: mapping +`noi_dung`(offsets)/`noi_ban_hanh`/`nguoi_ky`/`ten_file`;
> `upsert_document` +các field đó (noi_dung truncate 50K); +`update_document_embedding` (CASE 3).
> (3) `document_search.py` viết lại: tự phát hiện exact/bm25/hybrid + mode list/excerpt + highlight
> noi_dung + ACL trong 1 query. +13 test. **Rollout:** phải recreate `hbrag_documents_v1` để có
> mapping mới (noi_dung/embedding) trước khi chạy job thật.
>
> Trước đó (thay thế) — **Job CLI đồng bộ DOffice → PG + ES document index**
> (`jobs/doffice_sync/`, chạy `python jobs/doffice_sync/run.py`): quét `DofficeRawDocument`
> (sync_status fetched/failed) + retry đến hạn → batch-check tồn tại (PG + ES) → resolve ACL từ
> `raw_payload.don_vi_list/phong_ban_list/ca_nhan_list` (KHÔNG `phan_quyen.chunk_payload` như giả
> định ban đầu) qua `resolve_doffice_and_compress` → tạo Document + `upsert_document` ES (embedding
> None, **KHÔNG Qdrant**); ACL đổi → partial `update_acl` (PG + ES, giữ embedding); chưa có quyền →
> `job_sync_retries`. Concurrent (semaphore, session/worker), idempotent, graceful per-VB. 2 bảng
> `job_sync_runs`/`job_sync_retries` (Base RIÊNG, tạo idempotent lúc start — KHÔNG đụng alembic_version
> do divergence; +migration `0015`). 4 file log/lần chạy (full/info/warning/error). +17 unit test.
> CLI: `--limit/--workers/--batch/--dry-run/--id-vb/--retry-only/--force-reindex`.
>
> Trước đó — **API tìm kiếm văn bản trực tiếp**: endpoint mới
> `POST /api/document-search/search` (`app/api/routes/document_search.py`) tìm document-level
> trên ES `hbrag_documents_v1` (hybrid BBQ kNN + BM25 + ACL trong 1 query). Input id_nv + query
> (id_pb/id_dv resolve từ dm_nhan_vien, không qua get_current_user); output list văn bản +
> metadata + score, đã lọc ACL. `DocumentIndexStore` thêm `_build_search_body()` (dùng chung) +
> `search_documents_with_detail()` (trả full metadata). Fallback BM25-only nếu embed lỗi/tắt
> (`used_vector=False`); 503 nếu ES tắt, 502 nếu ES lỗi. +10 unit test. (`search_documents` giữ
> tương thích ngược.)
>
> Trước đó — **BBQ vector + ACL trong ES document index (two-stage)**:
> `DocumentIndexStore` thêm field `embedding` (dense_vector **bbq_hnsw** 4096d, dot_product —
> ES 9.4.2 + vector chuẩn hoá L2=1) với graceful fallback BM25-only nếu ES không hỗ trợ;
> `update_acl(doc_id, ...)` partial-update 3 field ACL (ES `_update`); `upsert_document()` ghi
> record đầy đủ; `search_documents(query_vector=)` → hybrid kNN(BBQ)+BM25+ACL-filter 1 query.
> `TwoStageHybridSearchService` embed query (qua `llm_gateway`, gate `two_stage_document_embedding_enabled`)
> trước Stage 1. Ingest DOffice ghi document index (embed trich_yeu+tom_tat). `recompress_*` →
> partial-update ES doc index, Qdrant bỏ qua. Script `build_document_index.py` đọc từ **Postgres**
> + embed + `--recreate`. **An toàn:** giữ ACL ở Stage 2 (phòng thủ nhiều lớp — KHÔNG bỏ như prompt).
> Mặc định TẮT (`two_stage_document_embedding_enabled=false`). **Rollout:** phải `--recreate` index
> `hbrag_documents_v1` để thêm field embedding. +3 unit test (tổng 12 ở test_two_stage_retrieval).
>
> Trước đó — **Two-stage retrieval wired**: `get_hybrid_search_service`
> (search.py) bọc `TwoStageHybridSearchService` khi `TWO_STAGE_RETRIEVAL_ENABLED=true`
> (mặc định false → giữ nguyên). TwoStage giờ expose **cả `run_search` lẫn `search`** để
> là drop-in cho `HybridSearchService` (consumer `RerankingService` gọi `run_search`).
> Stage 1 (`search_documents`) bỏ `minimum_should_match` (câu hỏi ngữ nghĩa vẫn lọt, ACL ở
> filter context). Thêm `two_stage_stage1_min_results` (=3): Stage 1 < ngưỡng → fallback full
> search. Recompress ACL: `recompress_document/all` nhận `document_index_store`; two-stage mode
> chỉ **partial-update** ACL ở document index ES (`DocumentIndexStore.update_acl`, dùng ES
> `update` không `index` để khỏi xóa field tìm kiếm), Qdrant bỏ qua. +9 unit test. **Lưu ý:**
> đường rerank không truyền `acl_subject` nên Stage 1 ở đó chưa lọc ACL (Stage 2 `access_filter`
> vẫn an toàn); route `/hybrid` trực tiếp thì Stage 1 lọc ACL đầy đủ.
>
> Trước đó — **3 fix chunking DOffice**: (1) bỏ chunk
> `document_section` chỉ là tiêu đề mục rỗng (`_is_section_title_only_chunk`, strip
> các dòng ngữ cảnh "Văn bản:/Ngày ban hành:/Cơ quan ban hành:/Mục:" rồi so với
> section_title); (2) bảng lớn cắt nhiều mảnh nay thêm hậu tố "(phần X/Y)" và gộp
> mảnh cuối < 3 hàng dữ liệu vào mảnh trước, ngưỡng cắt 2800 → 3500; (3)
> `parse_issued_date_from_text` bắt thêm ngày dạng số có tiền tố "Ngày 11/8/2025",
> `normalize_doffice_source` thử lại trên plain text đầy đủ (gồm footer) khi thiếu
> issued_date; bỏ issued_date thô khỏi văn bản tóm tắt (tránh `_sanitize` băm thành
> "11/"). VB 1068586: 33 → 29 chunk, issued_date "11/08/2025". +9 unit test.
>
> Trước đó — **Xóa văn bản dọn đủ cả 3 store + hợp nhất LLMGateway**:
> route `DELETE /api/documents/{id}` giờ xóa vector ở **cả** Qdrant chunk collection lẫn
> **artifact collection**, và xóa khỏi **Elasticsearch** keyword store; lọc chỉ theo
> `document_id` (bỏ lọc `tenant_id` để không sót dữ liệu cũ index lúc chưa gắn tenant). Trước
> đó route chỉ xóa chunk collection → còn sót artifact Qdrant + toàn bộ ES (tích lũy orphan,
> vd id ES `602e1eca`=408 docs). Hai store mới được inject qua `Depends` để test override được.
> Ngoài ra: LLM/embedding/reranker đã hợp nhất đi qua facade `LLMGateway` (vector/knowledge
> indexing + reranking nhận `LLMGateway` thay vì provider rời).
>
> Trước đó (chunker DOffice v2): mỗi bảng thành 1 `chunk_type="table"` (cắt dòng bằng
> `chonkie.TableChunker` nếu > 2800 ký tự); module `chunker_text_cleaning.clean_for_chunking`;
> cờ `DOFFICE_CHUNKER_V2_ENABLED` (mặc định True). VB 1068586: 409 → 33 chunk.
>
> Cập nhật trước: 2026-06-26 (b) — **tối ưu quy mô ~2M văn bản / ~20M chunk**: flatten
> ACL thành `acl_subjects` (1 điều kiện filter), payload index `acl_*` cho Qdrant + ES mapping,
> config Qdrant quantization/HNSW/on_disk + ES shard/refresh, two-stage retrieval và Redis cache
> (mặc định TẮT). Xem mục "Tối ưu quy mô lớn" cuối file.
>
> Cập nhật trước: 2026-06-26 — chốt **ngữ nghĩa ĐỘNG theo nhóm**: chỉ lưu bộ quyền
> rút gọn `acl_*`, xoá 3 list input, không lưu danh sách nhân viên dài; người mới vào
> phòng/đơn vị tự được xem, không cần re-compress khi nhân sự đổi. (Trước đó: hệ phân
> quyền theo danh mục EVNCPC + bộ nén + resolver + payload + ingest thử từ JSON cục bộ.)

## 1. Dự án là gì

Backend RAG (Retrieval-Augmented Generation) cho EVNCPC: ingest văn bản (chủ yếu từ
DOffice), chunk + embedding, lưu vào Qdrant (vector + sparse) và Elasticsearch (BM25),
truy hồi hybrid (vector + keyword + RRF + rerank) rồi sinh câu trả lời có trích dẫn.

- **Framework:** FastAPI. Entrypoint: `app/main.py`.
- **CSDL nghiệp vụ:** PostgreSQL (SQLAlchemy async + Alembic).
- **Vector store:** Qdrant `http://10.72.113.21:6333`, collection `hbrag_chunks_qwen3_8b_v1`
  (dense 4096-dim Qwen3-Embedding-8B + sparse).
- **Keyword store:** Elasticsearch `http://10.72.113.21:9200`, index `hbrag_chunks_bm25_v1`.
- **Nguồn DOffice:** Elasticsearch `https://10.72.121.232:9200/doffice_vanban` (HTTPS + Basic auth
  `DOFFICE_ES_USERNAME`/`DOFFICE_ES_PASSWORD`, self-signed -> `DOFFICE_ES_VERIFY_SSL=false`).
  **API hiện CHƯA trả ACL** (`don_vi_list/phong_ban_list/ca_nhan_list` = null) → có **ACL giả định**
  (`DOFFICE_SYNTHETIC_ACL_ENABLED`, mặc định bật; mẫu: đơn vị 269, phòng 43310) để bộ quyền vẫn
  chạy khi ingest. Tắt synthetic khi API trả ACL thật.

## 2. Bố cục thư mục `app/`

| Thư mục | Vai trò |
|---|---|
| `api/routes/` | Endpoint: admin, auth, chat, documents, health, knowledge_bases, memory, search |
| `core/` | `config.py` — cấu hình (Pydantic settings) |
| `db/` | `base.py` (Base + naming convention), `session.py` (engine, `AsyncSessionLocal`) |
| `models/` | SQLAlchemy ORM models |
| `repositories/` | Tầng truy cập dữ liệu |
| `schemas/` | Pydantic request/response |
| `services/` | Nghiệp vụ: `retrieval/`, `vector/`, `security/`, `ingestion/`, `chunkers/`, `rag/`, `document_sources/`, ... |

## 3. Luồng dữ liệu chính

1. **Ingest** (`services/ingestion/ingestion_doffice_ingestion_service.py`): lấy văn bản
   DOffice theo `id_vb` → chuẩn hóa (`ingestion_doffice_content_normalizer.py`) → tạo
   `Document` → chunk (`services/chunkers/`) → enrich (summary/keywords) → index.
2. **Index**: payload mỗi chunk dựng tại `services/rag/rag_chunk.py::qdrant_payload` (line ~982),
   đẩy vào Qdrant (`services/vector/vector_store.py`) và ES
   (`services/retrieval/retrieval_elasticsearch_keyword_search.py`).
3. **Retrieval** (`services/retrieval/retrieval_hybrid_search.py`): vector + keyword + RRF
   (k=60) + rerank tùy chọn.

## 4. Hệ thống phân quyền

Có **hai hệ ACL song song** — cần biết để không nhầm:

### 4.1. Hệ ACL cũ (org-UUID) — đang tồn tại

- File: `services/security/security_access_control.py`.
- Khóa theo **org UUID / role_names / group_codes / user_ids**, có `scope`,
  `classification` (rank 0–5), allow/deny lists, hai tầng (filter Qdrant + `can_access_resource`).
- Trường payload: `allowed_org_ids`, `allowed_org_paths`, `allowed_role_names`,
  `allowed_group_codes`, `allowed_user_ids`, `denied_*`, `owner_org_id`, `scope`, `classification`.
- Filter Qdrant: `vector_store.py::_payload_filter` (line ~425).
- **Lưu ý:** `settings.access_read_all_documents = True` (config.py:197) đang **TẮT toàn bộ
  ACL ở luồng đọc**. ES **chưa** áp filter ACL (lỗ hổng đã biết).

### 4.2. Hệ ACL mới (theo danh mục EVNCPC) — đang xây

Khóa theo **ID danh mục nguyên**: `id_dv` (đơn vị) ⊃ `id_pb` (phòng ban) ⊃ `id_nv` (cá nhân).
Mỗi nhân viên thuộc **đúng 1 đơn vị + 1 phòng ban** (tính phân hoạch — đã kiểm chứng trên
dữ liệu thật, là nền tảng cho tính đúng của bộ nén).

**Danh mục tổ chức** (`models/danh_muc.py`, bảng `dm_don_vi` / `dm_phong_ban` / `dm_nhan_vien`):
- Nguồn: `data/DM_DONVI.xlsx` (335 đơn vị, 3.361 phòng ban) + `data/DM_NHANVIEN.xlsx`
  (11.485 nhân viên). Gốc cây đơn vị: `id_dv=251` (EVNCPC).
- Nạp bằng `scripts/load_danh_muc.py` (đọc Excel → upsert, idempotent, tự tạo bảng nếu thiếu).
- `dm_don_vi.org_path` là materialized path `/251/.../<id_dv>/` để truy vấn subtree.

**Các lớp (trong `services/security/`):**

| File | Vai trò |
|---|---|
| `security_acl_compressor.py` | `OrgCatalog` (chỉ mục thành viên) + `compress_allow()` → `CompressedAcl` (3 list allow + 2 list deny). Gộp cả phòng/đơn vị thành 1 id, có deny ngoại lệ; **verify bằng giải nén** để không bao giờ sai quyền. |
| `security_acl_resolver.py` | `RawAssignment` + `UnitTree` (subtree) → `resolve_effective_users()` → `resolve_and_compress()`. **`build_assignment_from_doffice()`** phân giải 3 list DOffice (`don_vi_list`/`phong_ban_list`/`ca_nhan_list`) có kiểm tra phân cấp trên danh mục (phòng thuộc đơn vị?, cá nhân có/đúng phạm vi?, không ai nhận → rỗng) và trả `warnings`. `resolve_doffice_and_compress()` gộp cả chuỗi. |
| `security_acl_payload.py` | Ánh xạ `CompressedAcl` ↔ payload `acl_*` (`acl_allow_dv/pb/nv`, `acl_deny_pb/nv`, `acl_ver`); `AclSubject`; `subject_can_access()` (kiểm tra chính xác); `build_qdrant_acl_*` / `build_es_acl_filter` (filter chính xác phía query). |
| `security_acl_recompress.py` | (ÍT DÙNG dưới mô hình động) `catalog_signature()` + `recompress_all()` — chỉ cần khi cấu trúc danh mục đổi; thay đổi nhân sự thông thường KHÔNG cần vì `acl_allow_pb`/`acl_allow_dv` đã động theo thành viên hiện tại. |

**Tính chất quan trọng:** với người dùng đã biết `(id_nv, id_dv, id_pb)`, điều kiện được xem là
`(id_dv∈allow_dv OR id_pb∈allow_pb OR id_nv∈allow_nv) AND NOT(id_pb∈deny_pb OR id_nv∈deny_nv)`
— đúng bằng phép giải nén cho riêng người đó, nên filter Qdrant/ES là **chính xác tuyệt đối**.

**Ngữ nghĩa ĐỘNG theo nhóm (đã chốt 2026-06-26):** chỉ lưu **bộ quyền rút gọn** (`acl_*`),
**xoá** 3 list input (đơn vị/phòng ban/nhân viên). Khai triển ra cá nhân chỉ là **tạm thời
trong bộ nhớ** để bộ nén tính "9/10", KHÔNG bao giờ lưu danh sách nhân viên dài.
- `acl_allow_pb=[X]` nghĩa là "thành viên **hiện tại** của phòng X (trừ deny) được xem".
- "Giao cả phòng" = `acl_allow_pb=[X]` (đúng 1 id), không lưu danh sách người.
- Người mới vào phòng/đơn vị **tự động** được xem; người rời đi tự mất quyền.
- ⇒ **Không cần** re-compress khi nhân sự đổi (nhóm là "sống"). Chỉ cần làm lại khi cấu
  trúc danh mục đổi (phòng đổi đơn vị) hoặc dữ liệu DOffice được sửa — khi đó ingest lại từ nguồn.

**ACL thô từ DOffice (ĐÃ XÁC NHẬN có):** `_source` của `doffice_vanban` chứa
`don_vi_list` (đơn vị nhận), `phong_ban_list` (phòng ban nhận), `ca_nhan_list` (cá nhân
nhận) — đúng không gian ID danh mục. **Quy ước nghiệp vụ đã chốt:**
- Người xem = **`ca_nhan_list`** (danh sách cá nhân cụ thể) — đây là nguồn quyền chính.
- `phong_ban_list` chỉ là **dự phòng** khi văn bản phát cho cả phòng mà không liệt kê cá nhân.
- `don_vi_list` là đơn vị nhận, KHÔNG cấp quyền.
- Adapter: `RawAssignment(allow_user_ids=ca_nhan_list)` (fallback `allow_department_ids=phong_ban_list`
  nếu `ca_nhan_list` trống). **Không** map `phong_ban_list` thành allow-cả-phòng — để bộ nén tự
  phát hiện khi danh sách cá nhân ≈ cả phòng thì gộp thành allow phòng + deny số ít.
  Ví dụ thực tế: 20/22 thành viên phòng 43310 nhận → `allow_pb=[43310] + deny_nv=[2 người]`;
  3/20 thành viên phòng 43298 nhận → giữ `allow_nv=[3 người]` (không gộp).

**Ingest thử nghiệm:** `scripts/ingest_vb_local.py` đọc văn bản DOffice từ `data/vb/*.json`
(response ES thô), ingest qua pipeline thật (tắt enrichment & artifact, không gọi LLM),
rồi gắn `acl_*` lên point Qdrant bằng `set_acl_payload_for_document` + lưu `raw_assignment`
vào `document_metadata["access"]`. Đã kiểm chứng: người nhận thấy đủ chunk, người ngoài 0 chunk.

**ĐÃ WIRE (2026-06-26):**
- `DofficeIngestionService._attach_acl_from_source` — mỗi lần ingest tự đọc `don_vi_list/phong_ban_list/ca_nhan_list`
  từ `source_document.raw_source`, resolve+compress, gắn `acl_*` lên **cả Qdrant và Elasticsearch**
  (`set_acl_payload_for_document` — payload-only, không nhúng lại). ES refresh trước `_update_by_query`
  để tránh race với bulk index. Kiểm chứng: `scripts/verify_acl_wiring.py` (insider thấy đủ, outsider 0).
- ES có `set_acl_payload_for_document` (qua `_update_by_query` painless).

**ĐÃ WIRE FILTER SEARCH (2026-06-26):**
- `User.id_nv` (cột mới, FK mềm tới `dm_nhan_vien.id_nv`) liên kết user ứng dụng ↔ danh mục.
- `AclSubject.from_app_user(session, user)` map user → (id_nv, id_dv, id_pb) + cờ super_admin (từ vai trò).
- Route search (`app/api/routes/search.py`) dựng `acl_subject` qua `_acl_subject(...)` rồi truyền vào
  hybrid search → `vector_store.search` (`_payload_filter`) **và** ES `ElasticsearchKeywordSearchService.search`
  (`_build_query`) — cả hai áp filter `acl_*` CHÍNH XÁC, độc lập với `access_read_all_documents`.
- Kiểm chứng: `scripts/verify_search_wiring.py` (insider thấy đủ ở cả Qdrant+ES, outsider 0; super_admin bypass).

**Còn để ngỏ (TODO):**
- Gán `User.id_nv` khi tạo/đồng bộ user thật (hiện app user test chưa gắn → `from_app_user` trả None → không lọc).
- Đồng bộ ES trong `recompress_all` (nếu cần recompute cấu trúc danh mục).
- Quyết định: thay thế hay chạy song song hệ ACL cũ (org-UUID); lộ trình tắt `access_read_all_documents`.

## 5. Migration / DB

- Alembic: `alembic/versions/`. Migration danh mục: `0014_add_danh_muc_to_chuc.py`.
- **CẢNH BÁO divergence:** DB chia sẻ đang stamp ở revision `0014_typed_idea_blocks`
  (thuộc branch khác, không có trong branch `OCR-AI-DO-Document`) → `alembic upgrade head`
  fail. Vì vậy `load_danh_muc.py` tạo bảng bằng `create_all(checkfirst)`. Khi merge branch
  cần xử lý trùng số hiệu 0014 + thêm merge migration.

## 6. Chạy thường dùng

```bash
# Nạp danh mục tổ chức từ Excel
.venv/Scripts/python.exe -m scripts.load_danh_muc

# Re-compress ACL sau khi danh mục đổi
.venv/Scripts/python.exe -m scripts.recompress_acl          # theo chữ ký
.venv/Scripts/python.exe -m scripts.recompress_acl --force  # tính lại tất cả

# Ingest thử văn bản DOffice từ data/vb/*.json + gắn ACL (dọn Qdrant/ES trước)
.venv/Scripts/python.exe -m scripts.ingest_vb_local
.venv/Scripts/python.exe -m scripts.ingest_vb_local --no-clean  # không dọn store

# Xem trước CHỈ lớp phân quyền cho data/vb/*.json (KHÔNG ingest) -> data/vb/acl_preview.json
.venv/Scripts/python.exe -m scripts.export_acl_preview
.venv/Scripts/python.exe -m scripts.export_acl_preview --no-clean

# Kiểm chứng wiring ingest: ingest qua service (tự gắn ACL Qdrant+ES)
.venv/Scripts/python.exe -m scripts.verify_acl_wiring
# Kiểm chứng wiring search: User.id_nv -> AclSubject -> lọc Qdrant + ES
.venv/Scripts/python.exe -m scripts.verify_search_wiring

# Test phân quyền
.venv/Scripts/python.exe -m pytest tests/test_acl_compressor.py tests/test_acl_resolver_payload.py -q
```

## 7. Test liên quan phân quyền

- `tests/test_acl_compressor.py` — bộ nén (toàn phòng, 9/10, roll-up đơn vị, không over-group...).
- `tests/test_acl_resolver_payload.py` — resolver (subtree, deny-wins) + payload + `subject_can_access`.
- `tests/test_access_control.py` — hệ ACL cũ.

## 8. Tối ưu quy mô lớn (~2M văn bản / ~20M chunk)

Tất cả tính năng mới **mặc định TẮT**, bật qua `.env`; không phá tương thích.

**ACL flatten (`acl_subjects`):** allow gộp thành 1 list keyword `["dv_{id}","pb_{id}","nv_{id}"]`
→ filter bằng MỘT điều kiện (`MatchAny`/`terms`) thay vì 3 OR, cache tốt hơn. Deny vẫn riêng
(`acl_deny_pb`/`acl_deny_nv`). Hàm: `to_chunk_payload_flat`, `build_qdrant_acl_conditions_flat`,
`build_es_acl_filter_flat`, `acl_subject_to_keys` (security_acl_payload.py). Ingest dùng
`to_chunk_payload_flat`; `_payload_filter` (Qdrant) và `_build_query` (ES) dùng biến thể flat.

**Payload index:** Qdrant index `acl_subjects` (KEYWORD) + `acl_allow_*`/`acl_deny_*` (INTEGER)
trong `_ensure_payload_indexes` + `scripts/maintenance/qdrant_create_payload_indexes.py`.
ES mapping khai báo các trường `acl_*` trong `_index_definition`.

**Qdrant performance (config):** `qdrant_quantization_enabled` (INT8), `qdrant_vector_on_disk`,
`qdrant_hnsw_m/ef_construct/on_disk`, `qdrant_search_hnsw_ef`, `qdrant_quantization_rescore/oversampling`,
`qdrant_shard_number`, `qdrant_replication_factor`, `qdrant_memmap_threshold`. Áp dụng tại
`_create_collection()` (lúc tạo/recreate) và `search()` (SearchParams + QuantizationSearchParams).

**Elasticsearch (config):** `elasticsearch_number_of_shards/replicas/refresh_interval` áp dụng khi tạo
index mới; index cũ cập nhật bằng `scripts/maintenance/es_update_settings.py`.
(Lưu ý: KHÔNG hạ `max_result_window` vì làm hỏng bulk delete_by_query khi re-ingest.)

**Two-stage retrieval:** `app/services/retrieval/retrieval_document_index.py` — `DocumentIndexStore`
(index `hbrag_documents_v1`, 1 record/văn bản; search BM25 boost ky_hieu 5 / trich_yeu 3 / tom_tat 2 /
keywords 1.5 + lọc ACL flat) + `TwoStageHybridSearchService` (Stage1 tìm document → Stage2 search chunk
trong document_ids). Nạp index: `scripts/maintenance/build_document_index.py` — nguồn từ **ES chunk index**
(collapse `document_id` + search_after, đọc `acl_*`/`ky_hieu`/`trich_yeu` trực tiếp, nhẹ ở quy mô 2M).
Đã kiểm chứng lọc ACL ở document-level (insider phòng nhận thấy, outsider 0). Config:
`two_stage_retrieval_enabled`, `two_stage_stage1_top_n`, `two_stage_chunk_threshold`,
`two_stage_document_index_url`. (Wiring chọn service trong route theo config là bước tích hợp tiếp theo.)

**Redis cache:** `app/services/cache/search_cache.py` — `SearchResultCache` + `get_search_cache()`
(singleton, None nếu tắt/thiếu redis). Key = SHA256(query|id_pb|id_dv|super_admin|top_k). Đã tích hợp
vào route `/hybrid` (cache get/set, log HIT/MISS). Config: `redis_url`, `search_cache_enabled`,
`search_cache_ttl_seconds`. Cần cài `redis` (chưa có trong venv) khi bật.

**Kiểm chứng:** `scripts/verify_acl_wiring.py` + `scripts/verify_search_wiring.py` (flat filter:
insider 62/62 Qdrant+ES, outsider 0/0). 28/28 test ACL pass.
