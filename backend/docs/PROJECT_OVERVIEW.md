# HBRag Backend — Tổng quan dự án

> Tài liệu này mô tả tổng thể backend để người mới (hoặc Claude ở phiên sau) đọc là
> hiểu dự án có gì. **Mỗi khi hoàn thành một thay đổi đáng kể, phải cập nhật file này.**
>
> Cập nhật gần nhất: 2026-06-27 — **API DOffice cập nhật ACL** (`POST /api/doffice/acl/update`).
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
