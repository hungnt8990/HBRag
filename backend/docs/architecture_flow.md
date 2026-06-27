# Luồng hệ thống RAG CPC/EVN — đọc theo BƯỚC

> Mô tả **trạng thái hiện tại**: kiến trúc lưu trữ, **luồng ingest (3 bước)**, **luồng hỏi-đáp (2 bước)** —
> mỗi bước nêu rõ **input → thuật toán → output**. Lý do/đo đạc để ở ghi chú (`▸`), không trộn vào thuật toán.
> Kế hoạch chi tiết & lịch sử quyết định: `…/.claude/plans/y-l-project-t-i-harmonic-seal.md` (mục 0–35).

---

## 0. Tổng quan

- Fork **Haystack 2.x** đã **rebrand `haystack` → `cpcit`** (white-label). Sửa thẳng trên source kể cả lõi.
- **On-prem hoàn toàn**: mọi model gọi qua **gateway OpenAI-compatible nội bộ** `https://stag-llm-gateway.cpc.vn/v1`.
  App không cần GPU, data không ra ngoài. LLM `Qwen3.5-9B` · embedding `Qwen3-Embedding-8B` (**4096d**) · reranker
  `Qwen3-Reranker-8B` · OCR `dots.ocr`.
- **2 kho vector**: **Elasticsearch** (10.72.121.200:9200 — lexical/BM25 + metadata + vector summary) và
  **Qdrant** (10.72.117.208:6333 — vector chunk).

```
NGUỒN (read-only)        NHÁNH CHUẨN HÓA (truy vấn)                       2 LUỒNG HỎI-ĐÁP
┌───────────────┐  B1   ┌──────────────┐  B2 embed  ┌─────────────┐
│ doffice_vanban│ ────► │ _full (1/VB) │ ─────────► │ summary kNN │  ── ĐỊNH DANH toàn kho (discover_full)
│  ~1.12tr VB   │ sync  │ ES + BBQ     │            └─────────────┘
└───────────────┘       └──────┬───────┘
                          B3 chunk hóa (chọn VB)
                    ┌───────────┴─────────────────────────────────┐
                    ▼              ▼                ▼              ▼
              ES chunk        Qdrant           ES _rows       ES _parents
            (lexical/BM25)  (vector kNN)     (bảng→hàng)     (auto-merge)
                    └──────── HỎI-ĐÁP AGENT 2 bước: ĐỊNH DANH → TRẢ LỜI ───────┘
```

---

## 1. Kiến trúc lưu trữ — ai giữ gì (5 nhánh)

| Nhánh (index/collection) | Giữ gì | Vector? | Dùng cho |
|---|---|---|---|
| `doffice_vanban` (**nguồn**) | VB gốc đã OCR (`noi_dung`, `id_vb`, `ky_hieu`, `trich_yeu`, `tom_tat`…) | — | **CHỈ ĐỌC** để đồng bộ. KHÔNG truy vấn, KHÔNG ghi |
| `{base}_full` | **1 doc/VB**: content + metadata + `embedding` (tóm tắt) | ✅ ES **BBQ** (4096d, binary÷32) | Định danh **toàn kho 1.12M** (`discover_full`) |
| `{base}` (**chunk**) | chunk: content + metadata (so_vb/nguoi_ky/loai_vb/chu_de/tu_khoa/section_path…) | ❌ **KHÔNG** (mục 32) | **Lexical/BM25** + lọc metadata cho chunk đã nạp |
| **Qdrant** `rag_doffice_chunk` | vector chunk + payload (`doc_id`, `_hs_id`, facet) | ✅ binary quant | **kNN ngữ nghĩa** chunk (chân semantic) |
| `{base}_rows` | **1 hàng bảng/Document** (`meta.fields={cột:giá trị}`, `flattened`) | ❌ | **Structured**: đếm/tra chính xác (tên người, cột) |
| `{base}_parents` | parent (gộp nhiều leaf, ≈ trọn 1 Điều/mục) | ❌ | Auto-merge (ngữ cảnh rộng) |

▸ **Vì sao ES chunk KHÔNG giữ vector (mục 32):** ES lo lexical/metadata, Qdrant lo vector → 2 chân chạy song song gộp RRF.
Bỏ `dense_vector` khỏi ES chunk: index 55MB→8MB (giảm ~85%), nhẹ khi chunk triệu VB. *Chỉ `_full` còn vector ES* (cho kNN toàn kho).
▸ `base` = `DOFFICE_INDEX` (`.env`). Nguồn ≠ full ≠ chunk — guard tên chính xác khi xóa, không wildcard.

---

## 2. LUỒNG INGEST — 3 bước

Hàm lõi: [app/doffice.py](../app/doffice.py); CLI [scripts/doffice_sync.py](../scripts/doffice_sync.py) +
[scripts/doffice_chunk_batch.py](../scripts/doffice_chunk_batch.py). **Nguồn `doffice_vanban` không bao giờ bị ghi.**

### BƯỚC 1 — Đồng bộ nguồn → `_full`  (`sync_doffice_full`)
- **Input:** filter `nam`/`donvi`/`all`.
- **Thuật toán:** ES **`_reindex`** server-side (KHÔNG kéo về Python) với **painless script** map field:
  `doc_id←id_vb`, `content←noi_dung`, `so_vb←ky_hieu`, `doc_title←trich_yeu`, `source="doffice"`; bỏ vector cũ của nguồn.
  `dest._id = id_vb` ⇒ chạy lại = ghi đè, không nhân đôi. Async + báo tiến độ.
- **Output:** `{base}_full` (1 doc/VB, **chưa embed**). BM25 + lọc metadata chạy ngay.

### BƯỚC 2 — Embed tóm tắt cho `_full`  (`embed_doffice_full`)
- **Input:** VB trong full **chưa có** `embedding` (filter `nam`/`ids`/`limit`).
- **Thuật toán:** lấy theo lô → embed `trich_yeu + tom_tat` bằng **Qwen 4096d** → **bulk update** field `embedding`.
  Mapping full = **`bbq_hnsw`** (binary quant ÷32 → 1–2tr vector ≈ ~1GB RAM, float32 trên đĩa để rescore).
- **Output:** full có vector → `discover_full` chạy **hybrid BM25 + kNN** (bắt cả câu diễn đạt khác từ khóa).

### BƯỚC 3 — Chunk hóa (chọn VB) → chunk + Qdrant + rows + parents  (`chunk_from_full`)
- **Input:** danh sách `ids` (hoặc `nam`/`limit`). *Chọn lô thông minh:* [scripts/doffice_chunk_batch.py](../scripts/doffice_chunk_batch.py)
  `--n N` → chọn N VB **mới** (loại VB đã chunk) **đa dạng, tránh tiêu đề generic** (trích yếu ≥6 từ — bài học: "Kế hoạch"×28 bất khả định danh).
- **Thuật toán (các bước con):**
  1. Đọc full theo `doc_id` → **clean**: bỏ mốc `--- Page N ---`, chuẩn hóa NFC.
  2. **Split** ([evn_hierarchical_splitter](../cpcit/components/preprocessors/evn_hierarchical_splitter.py) + [evn_adaptive_splitter](../cpcit/components/preprocessors/evn_adaptive_splitter.py)):
     tách **block** (giữ `<table>` nguyên khối, nhận heading/Điều/PHỤ LỤC) → **bảng lớn > `MAX_TABLE_TOKENS`** tách theo hàng,
     **lặp `<thead>`** mỗi mảnh → gắn **breadcrumb `section_path`** (`PHỤ LỤC 1 › … › ĐƠN VỊ: …`) vào **metadata** (content sạch) →
     gói thành **parent** (≈ trọn 1 Điều/mục, ≤ `MAX_PARENT_TOKENS`) + **leaf** (~`LEAF_TOKENS`).
     *(Đếm token có **fallback char-based** khi tiktoken vỡ stack trên chuỗi bệnh lý — mục 34.)*
  3. **Làm giàu metadata** (`_enrich_vb_meta` → `LLMMetadataExtractor` qua gateway): thêm `loai_vb` (Quyết định/Công văn/…),
     `chu_de`, `tu_khoa`, `thuc_the` → propagate xuống leaf + rows.
  4. **Embed leaf** (Qwen 4096, `meta_fields_to_embed=["section_path"]` → vector nhúng cả breadcrumb).
  5. **DUAL-WRITE (upsert theo `doc_id`, xóa cũ→ghi):**
     - **ES chunk** `{base}`: content + metadata (**KHÔNG embedding**).
     - **Qdrant** `rag_doffice_chunk`: vector + payload (`doc_id`, `_hs_id`=ES id, facet).
     - **`_parents`**: parent docs.
     - **`_rows`**: nổ mỗi `<table>` thành 1 Document/hàng (`meta.fields`, `flattened` → không nổ trần ES field — mục 31).
- **Output:** VB sẵn sàng **Q&A sâu**. `doffice_vanban` + full **không đổi**.

▸ Đổi cách cắt / `section_path` / sizing ⇒ **phải chunk lại** (vector + parent thay đổi). Upsert theo `doc_id` nên không orphan.

---

## 3. LUỒNG HỎI-ĐÁP (AGENT) — 2 bước: ĐỊNH DANH → TRẢ LỜI

[app/doffice_agent.py](../app/doffice_agent.py). **Agent** (tool-calling, Qwen) đọc câu hỏi → gọi **tool deterministic**
theo trình tự (số đếm do ES tính, LLM KHÔNG tự đếm). Đây là **2 bước TUẦN TỰ**, agent điều phối bằng thứ tự gọi tool —
**không** phải router gửi câu sang nhánh A/B.

```
Câu hỏi ─► [B1 ĐỊNH DANH] find_documents ─► doc_id ─► [B2 TRẢ LỜI] doc_info | find_section | count_rows | semantic_search ─► đáp án
```

### BƯỚC 1 — ĐỊNH DANH đúng VB  (`find_documents`)
Chọn đúng VB từ câu hỏi (sai VB = sai hết). **2 chân CHẠY SONG SONG** (`ThreadPoolExecutor`):
- **Input:** `query` (+ tùy chọn facet `loai_vb/nam/nguoi_ky/don_vi`, `prefer_recent`).
- **Thuật toán:**
  1. **Chân ES (lexical)** ‖ **Chân Qdrant (semantic)** chạy đồng thời:
     - ES: `QueryExpander` (LLM sinh biến thể) → BM25 `multi_match` trên `trich_yeu/tom_tat/content/so_vb/nguoi_ky` +
       **facet BOOST mềm** (loai_vb/nam/đơn vị) → collapse theo `doc_id`.
     - Qdrant: embed query → `query_points` kNN → gom theo `doc_id`.
  2. **RRF theo `doc_id`** (`_rrf_doc_ids`) hợp nhất 2 danh sách.
  3. **Resolver số-VB exact**: nếu câu hỏi chứa nguyên văn số VB của 1 ứng viên → ghim lên đầu (khớp chuỗi, deterministic).
  4. **NHẬP NHẰNG**: nếu ≥2 ứng viên top **trùng tiêu đề** → gắn cờ → agent **liệt kê (số VB+ngày) và HỎI**, không đoán bừa.
  5. **Recency**: nếu `prefer_recent` (câu "mới nhất") → `MetaFieldRanker(ngay_vb)` blend mềm.
- **Output:** danh sách VB ứng viên `{doc_id, so_vb, loai_vb, ngay_vb, nguoi_ky, trich_yeu}` → agent chọn `doc_id` đúng.

▸ **Vì sao 2 chân:** tên/số/mã → ES lexical bắt (vector kém với tên riêng); câu chủ đề/diễn đạt khác → Qdrant kNN bắt → RRF cộng hưởng.
Qdrant lỗi → degrade **BM25-only** (mục 32, không fallback ES-kNN vì ES chunk không có vector).

### BƯỚC 2 — TRẢ LỜI (scope theo `doc_id` đã chọn)
Agent chọn tool theo **loại câu hỏi**:

| Loại câu | Tool | Thuật toán | Output |
|---|---|---|---|
| Metadata (ai ký / số / ngày / cơ quan / loại VB) | `doc_info(doc_id)` | đọc thẳng field metadata (ES agg top_hits) | giá trị |
| Đếm/liệt kê theo **đơn vị/mục** | `find_section(doc_id, keyword)` | ES agg `section_path.keyword`, match mọi từ (`and`→fallback 70%) → **số hàng/mục** | con số chính xác |
| Đếm/tra theo **cột/giá trị** (tên người…) | `count_rows`/`list_rows(doc_id, field, value)` | ES `_rows` `match_phrase` trên content (route `fields.*`→content vì flattened) → count / hàng | số / hàng đầy đủ |
| **Nội dung/diễn giải** | `semantic_search(doc_id, query)` | **2 chân song song** BM25(ES)‖kNN(Qdrant) → **RRF** → **rerank** (Qwen3-Reranker, ghép `section_path`) → **LostInTheMiddle** (chunk mạnh ra 2 đầu) | đoạn văn |

- Agent tổng hợp → đáp án tiếng Việt + **tự trích nguồn** (số VB/trích yếu). LLM **không tự đếm** — số do ES tính.

---

## 4. Luồng phụ

- **`discover_full(query)` — ĐỊNH DANH TOÀN KHO 1.12M** ([app/doffice.py](../app/doffice.py)): LLM mở rộng biến thể →
  ES `match_phrase` `content`+`trich_yeu` (+ **kNN** trên `embedding` summary nếu đã embed) → hybrid điểm → (tùy) facet boost mềm →
  danh sách VB. *Dùng khi tìm VB chưa chunk trên toàn kho (UI "Tìm trên full").* Đo thực: Recall@1 ~45–56%, @10 ~72%
  (trần do near-duplicate generic — cần disambiguator, không phải retrieval).
- **Router baseline `route_and_answer`** ([app/pipelines.py](../app/pipelines.py), mục 20): tách `entity`/`topic` → 4 nhánh
  **structured / topic / combined / semantic**. *Vẫn dùng ở tab "Test RAG File"* (sáng kiến/bảng). Đã đo **agent > router** (95% vs 57%)
  nên D-Office dùng **agent** là chính.

---

## 5. Thuật toán chi tiết vài component (phần hay "đọc chả hiểu")

- **`EvnAdaptiveSplitter`** ([file](../cpcit/components/preprocessors/evn_adaptive_splitter.py)): tách **block nguyên tử** (table/heading/đoạn) →
  **sinh nhiều ứng viên** cắt (S1 ~600tok, S2 ~1100tok, S3 mở chunk mỗi heading) → **chấm điểm** mỗi ứng viên = SizeCompliance + BlockIntegrity
  (+ Cohesion ngữ nghĩa qua embedding nếu bật) → **chọn cao nhất**. Bảng > `max_table_tokens` → cắt theo `<tr>`, lặp `<thead>`.
  `_keep_headings_with_following`: heading cuối chunk dời sang đầu chunk sau (heading luôn đi với nội dung).
- **`EvnTableRowIndexer`** ([file](../cpcit/components/preprocessors/evn_table_row_indexer.py)): mỗi `<table>` → đọc `<thead>` lấy **tên cột** →
  mỗi `<tr>` body → 1 Document `content="Cột: giá trị"`, `meta.fields={cột:giá trị}` (kiểu **flattened** ở ES → không nổ trần 1000 field), `is_row=True`.
- **Structured count data-driven** (router, mục 20d): so `count_phrase(entity)` trên **content** (giá trị ô) vs **`section_path`** (nhóm/đơn vị)
  → nhánh nhiều hơn = loại entity. Đếm **số hàng distinct** + **breakdown theo cột** (tên cột TỪ DATA). KHÔNG hardcode cột/keyword, KHÔNG wildcard
  (token `match_phrase` đúng ranh giới từ: "AI" không khớp "hai").
- **RRF (Reciprocal Rank Fusion)**: điểm = Σ `1/(k + rank)` qua các danh sách → hợp nhất 2 chân theo `doc_id` mà không cần chuẩn hóa thang điểm.
- **Disambiguation (mục 28/33)**: ≥2 ứng viên trùng `trich_yeu` → liệt kê + hỏi; có số VB trong câu → ghim exact.

---

## 6. Sizing & cấu hình (`.env` → [app/config.py](../app/config.py))

| Biến | Ý nghĩa | Mặc định |
|---|---|---|
| `CPCIT_GATEWAY_URL` / `_API_KEY` | gateway + token (qua `Secret`, không log) | stag-llm-gateway.cpc.vn/v1 |
| `CPCIT_LLM_MODEL`/`_EMBED_MODEL`/`_RERANK_MODEL` | model Qwen | 3.5-9B / Embedding-8B / Reranker-8B |
| `CPCIT_EMBED_DIM` | chiều vector | 4096 |
| `CPCIT_TOP_K_RETRIEVE` / `_RERANK` | top_k retrieve / rerank | 30 / 10 |
| `CPCIT_LEAF_TOKENS` / `_MAX_TABLE_TOKENS` / `_PARENT_TOKENS` / `_MAX_PARENT_TOKENS` | sizing chunk (mục 19) | 400 / 1200 / 1500 / 4000 |
| `CPCIT_RECENCY_WEIGHT` | trọng số recency mềm (chỉ khi `prefer_recent`) | 0.5 |
| `DOFFICE_SOURCE_INDEX` / `DOFFICE_INDEX` | nguồn read-only / base nhánh chuẩn hóa | doffice_vanban / test_rag_doffice |
| `QDRANT_URL` / `QDRANT_DOFFICE_COLLECTION` | Qdrant vector chunk | 10.72.117.208:6333 / rag_doffice_chunk |
| `CPCIT_TELEMETRY_ENABLED` | telemetry (mặc định TẮT, on-prem) | False |

▸ **Sizing:** chỉ **leaf** được embed/truy hồi (vùng ngọt Qwen ~256–512 tok); parent chỉ dùng sau auto-merge→rerank. 1 hàng bảng ≈ 1 chunk
(mỗi hàng ~600–850 tok). Câu "liệt kê/đếm toàn bộ" giải bằng **rows/section_path + số ở tiêu đề**, KHÔNG bằng chunk to hơn.

---

## 7. File lõi

| File | Vai trò |
|---|---|
| [app/doffice_agent.py](../app/doffice_agent.py) | **Agent Q&A 2 bước** (find_documents + tools, 2 chân song song, disambiguation/recency) |
| [app/doffice.py](../app/doffice.py) | ingest D-Office: `sync_doffice_full`, `embed_doffice_full`, `chunk_from_full`, `discover_full`, `list_chunked_docs`, `doffice_status` |
| [app/pipelines.py](../app/pipelines.py) | factory store (`new_doffice_store`/`new_doffice_qdrant_store`), `embed_and_write`, `hierarchical_split`, `route_and_answer` (router baseline) |
| [app/config.py](../app/config.py) | `.env` → `Settings` |
| [cpcit/document_stores/elasticsearch/document_store.py](../cpcit/document_stores/elasticsearch/document_store.py) | ES store (cờ `store_vectors`, `flattened_meta`, `count_phrase`/`search_phrase`) |
| [cpcit/document_stores/qdrant/document_store.py](../cpcit/document_stores/qdrant/document_store.py) + [retrievers](../cpcit/components/retrievers/qdrant/retrievers.py) | Qdrant store + retriever (kNN + payload filter) |
| [cpcit/components/preprocessors/evn_adaptive_splitter.py](../cpcit/components/preprocessors/evn_adaptive_splitter.py) · [evn_hierarchical_splitter.py](../cpcit/components/preprocessors/evn_hierarchical_splitter.py) · [evn_table_row_indexer.py](../cpcit/components/preprocessors/evn_table_row_indexer.py) | cắt chunk giữ bảng/breadcrumb · parent/leaf · bảng→hàng |
| [cpcit/components/converters/evn_pdf.py](../cpcit/components/converters/evn_pdf.py) | PDF→markdown giữ bảng (fuzzy) / dots.ocr (scan) — dùng cho tab File |
| [cpcit/components/rankers/evn_gateway.py](../cpcit/components/rankers/evn_gateway.py) | reranker gọi gateway + `meta_fields_to_rank` |

---

## 8. Chạy & Test

```bash
pip install -e .                       # cài editable
pip install -r app/requirements.txt    # streamlit, elasticsearch, qdrant-client, pdfplumber…
streamlit run app/streamlit_app.py     # UI: tab "Test RAG File" + "Test RAG D-Office"
```
**CLI D-Office (chạy lại sau reset):**
```bash
python scripts/doffice_sync.py  sync --nam 2025      # nguồn → full (reindex)
python scripts/doffice_sync.py  embed --limit 1000   # embed tóm tắt full
python scripts/doffice_chunk_batch.py --n 20         # CHỌN LÔ 20 VB mới đa dạng + chunk (dual-write)
python scripts/doffice_chunk_batch.py --ids 1238194  # chunk đúng VB
python scripts/doffice_sync.py  status               # đếm các nhánh (+ Qdrant)
python scripts/qdrant_setup.py  backfill             # đẩy vector ES chunk → Qdrant (nếu lệch)
```
**Đo chất lượng:**
```bash
python scripts/inspect_chunks.py --ids …             # audit chunk: token/bảng/parity ES↔Qdrant + cờ đỏ
python scripts/gen_qa_doffice.py --ids …             # sinh ~14 câu test/VB (metadata + nội dung)
python scripts/qa_test_doffice.py --path agent --id …  [--diagnose]   # chấm PASS + Recall@1 (+ retrieval vs grounding)
```
▸ Test nhiều VB: chạy **song song** `… | xargs -P 20` (đo: ≤20 luồng an toàn; 50 luồng làm gateway staging rớt VB).

---

## 9. Trạng thái đã verify (đo thật)

- **Agent 2 bước** trên ~120 VB (5 lô): chunk parity ES↔Qdrant 100%; Recall@1 ~64–74% trên VB **tiêu đề đặc thù**
  (near-duplicate/tiêu đề generic là trần đã hiểu — disambiguation xử lý).
- Structured (rows flattened): "Quảng Trị có mấy sáng kiến" = **11**; "Nguyễn Xuân Tiến" = **7**; bảng chấm công đọc đúng ô (hệ số 7.130, ngày công 19).
- Mục 32: ES chunk index 55MB→**8MB** (bỏ vector); 2 chân song song; Qdrant lỗi → degrade BM25-only không crash.
- `doffice_vanban` (1.122.396) + `_full` (1.122.232) **không bị đụng** qua mọi thao tác chunk/rebuild.