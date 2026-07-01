# Schema Metadata chuẩn hoá — HBRag (PostgreSQL · Elasticsearch · Qdrant)

> Mục tiêu: một bộ tên trường **dùng chung** giữa PostgreSQL (nguồn sự thật), Elasticsearch
> (BM25/keyword) và Qdrant (vector: Col1 `hbrag_doffice_chunks_v1` + Col2 `hbrag_doffice_docmeta_v1`),
> để **filter không lệch nhau** và phục vụ chatbot + các bài toán sau.
>
> Tài liệu này mô tả **từng metadata + mục đích**, đánh dấu trường **hiện có** vs **đề xuất thêm**.
> Cập nhật gần nhất: 2026-06-30.

---

## 1. Nguyên tắc (đọc trước khi sửa)

1. **PostgreSQL = nguồn sự thật.** Mọi metadata gốc nằm ở PG (`documents.document_metadata` JSONB).
   ES/Qdrant chỉ là bản sao **đã chọn lọc** để tìm kiếm.
2. **Qdrant payload chỉ giữ 2 loại trường:**
   - **FILTER** — trường để lọc/sắp xếp lúc search (quyền, thời gian, loại VB, đơn vị, trạng thái).
   - **CITE** — trường tối thiểu để **dẫn nguồn / dựng câu trả lời** từ top-K mà không cần truy vấn thêm.
   Mọi trường khác (audit, debug, quan hệ) **để ở PG**, cần thì join theo `document_id`.
3. **KHÔNG embed** trường phân quyền, ngày tháng, ID kỹ thuật. Vector chỉ sinh từ **nội dung ngữ nghĩa**.
4. **Lưu ≠ lọc nhanh.** Trường dùng để FILTER phải được **tạo payload index** trong Qdrant (xem §7).
5. **Tên trường thống nhất 3 kho.** Cùng một khái niệm → cùng một tên (vd `ngay_vb`, không lẫn `issued_date`).

Ký hiệu **Mục đích**: 🔒 = lọc quyền · 🧭 = filter/sort/facet · 📌 = dẫn nguồn/hiển thị ·
🧠 = sinh embedding (KHÔNG để làm payload filter) · 🗄️ = chỉ giữ ở PG (nguồn).

Cột **Nơi lưu**: PG · ES · C1 (Qdrant chunks) · C2 (Qdrant docmeta). ✓ = nên có; – = không.
Cột **TT** (trạng thái): `có` = đang lưu thật · `+` = đề xuất thêm (khi nguồn dữ liệu cung cấp).

---

## 2. Định danh & liên kết

| Trường | Kiểu | PG | ES | C1 | C2 | Mục đích | TT | Ghi chú |
|---|---|:--:|:--:|:--:|:--:|---|:--:|---|
| `document_id` | uuid | ✓ | ✓ | ✓ | ✓ | 📌 liên kết PG/ES/Qdrant | có | Khóa nối mọi kho |
| `id_vb` | string | ✓ | ✓ | ✓ | ✓ | 🧭📌 khóa nghiệp vụ | có | ID văn bản DOffice |
| `chunk_id` | uuid | ✓ | ✓ | ✓ | – | 📌 định danh đoạn | có | **Chỉ giữ 1 tên** (bỏ alias) |
| `chunk_index` | int | ✓ | ✓ | ✓ | – | 📌 thứ tự đoạn | có | Truy vết về văn bản |
| `source_type` | string | ✓ | ✓ | ✓ | ✓ | 🧭 phân loại nguồn | có | vd `doffice_elasticsearch` |
| `content_hash` | string | – | – | ✓ | – | (kỹ thuật) chống trùng/đổi | có | Giữ ở C1, không bắt buộc |

---

## 3. Nghiệp vụ

| Trường | Kiểu | PG | ES | C1 | C2 | Mục đích | TT | Ghi chú |
|---|---|:--:|:--:|:--:|:--:|---|:--:|---|
| `ky_hieu` | string | ✓ | ✓ | ✓ | ✓ | 🧭📌🧠 số/ký hiệu | có | Boost cao khi BM25 |
| `trich_yeu` | string | ✓ | ✓ | ✓ | ✓ | 📌🧠 tiêu đề ngắn | có | Vào embed docmeta |
| `tom_tat` | string | ✓ | ✓ | – | ✓ | 📌🧠 tóm tắt | có | **Chỉ doc-level (C2)** |
| `noi_ban_hanh` | string | ✓ | ✓ | ✓ | ✓ | 🧭📌🧠 cơ quan ban hành | có | |
| `nguoi_ky` | string | ✓ | ✓ | – | ✓ | 📌 người ký | có | Doc-level |
| `ten_file` | string | ✓ | ✓ | ✓ | ✓ | 📌 tên file gốc | có | |
| `duong_dan` | string | ✓ | ✓ | ✓ | ✓ | 📌 đường dẫn file | có | |
| `loai_vb` | string | ✓ | ✓ | ✓ | ✓ | 🧭 loại văn bản | + | Quyết định/Thông báo/Công văn… |
| `linh_vuc` | string\|list | ✓ | ✓ | ✓ | ✓ | 🧭 lĩnh vực/chủ đề | + | Lọc theo mảng nghiệp vụ |

---

## 4. Hiệu lực & thời gian

| Trường | Kiểu | PG | ES | C1 | C2 | Mục đích | TT | Ghi chú |
|---|---|:--:|:--:|:--:|:--:|---|:--:|---|
| `ngay_vb` | date (ISO) | ✓ | ✓ | ✓ | ✓ | 🧭 ngày văn bản | có C2 / **+ C1** | C1 đang dùng `issued_date` (chuỗi) → đổi tên `ngay_vb` |
| `nam` | int | ✓ | ✓ | ✓ | ✓ | 🧭 facet năm | có C2 / **+ C1** | Lọc nhanh theo năm |
| `thang` | int | ✓ | ✓ | ✓ | ✓ | 🧭 facet tháng | có C2 / **+ C1** | |
| `ngay_hieu_luc` | date | ✓ | ✓ | ✓ | ✓ | 🧭 còn/không hiệu lực | + | Khi nguồn có |
| `ngay_het_hieu_luc` | date | ✓ | ✓ | ✓ | ✓ | 🧭 hết hiệu lực | + | Khi nguồn có |
| `trang_thai_hieu_luc` | string | ✓ | ✓ | ✓ | ✓ | 🧭 lọc "còn hiệu lực" | + | con_hieu_luc / het_hieu_luc / thay_the |
| `ngay_tao` | datetime | ✓ | – | – | ✓ | 🗄️ audit | có C2 | Nên để PG; C2 không bắt buộc |
| `ngay_capnhat` | datetime | ✓ | – | – | ✓ | 🗄️ audit | có C2 | Nên để PG |

> Lưu ý: hôm nay C1 chỉ có `issued_date` (chuỗi), thiếu `nam/thang/ngay_vb` → **không filter thời gian
> ở cấp chunk được**. Bổ sung 3 trường này vào C1 là phần chuẩn hoá quan trọng nhất cho chatbot.

---

## 5. Phân quyền (ACL)

| Trường | Kiểu | PG | ES | C1 | C2 | Mục đích | TT | Ghi chú |
|---|---|:--:|:--:|:--:|:--:|---|:--:|---|
| `acl_subjects` | list[str] | ✓ | ✓ | ✓ | ✓ | 🔒 allow (`dv_/pb_/nv_`) | có | Subject ĐƯỢC xem |
| `acl_deny` | list[str] | ✓ | ✓ | ✓ | ✓ | 🔒 deny (`pb_/nv_`) | có | Subject BỊ chặn |
| `id_dv_ban_hanh` | int | ✓ | ✓ | ✓ | ✓ | 🧭 lọc theo đơn vị | có C2 / **+ C1** | |
| `acl_ver` | string | ✓ | ✓ | meta | meta | (kỹ thuật) version ACL | có | Để biết khi nào cần recompress |

> 🔒 **Luôn áp filter `acl_subjects`/`acl_deny` NGAY khi search** (cả ES lẫn Qdrant), không lọc sau.
> Tuyệt đối **không đưa các trường ACL vào text embedding**.

---

## 6. Cấu trúc đoạn (chỉ Col1 — chunk-level)

| Trường | Kiểu | PG | ES | C1 | C2 | Mục đích | TT | Ghi chú |
|---|---|:--:|:--:|:--:|:--:|---|:--:|---|
| `text` | string | – | ✓ | ✓ | – | 📌 nội dung đoạn | có | Dẫn nguồn/hiển thị (ES: `chunk_text`) |
| `chunk_type` | string | ✓ | ✓ | ✓ | – | 🧭 loại đoạn | có | legal_clause/table/header/footer… |
| `section_path` | list[str] | – | ✓ | ✓ | – | 📌 vị trí mục (chương>điều) | có | ES: `heading_path` |
| `table_name` | string | – | ✓ | ✓ | – | 📌 tên bảng | có | Chunk bảng |
| `table_columns` | list[str] | – | ✓ | ✓ | – | 📌 cột bảng | có | Chunk bảng |
| `row_start`/`row_end` | int | – | ✓ | ✓ | – | 📌 dải hàng | có | Chunk bảng |
| `pages` | list[int] | – | ✓ | ✓ | – | 📌 trang nguồn | có | Nếu có |

---

## 7. Quan hệ văn bản (chỉ PostgreSQL — fetch theo `document_id`)

Không đưa vào payload Qdrant/ES; truy vấn PG khi cần dựng "căn cứ/sửa đổi".

| Trường | Kiểu | Mục đích |
|---|---|---|
| `can_cu_vb` | list[str] | Văn bản làm căn cứ |
| `tham_chieu_vb` | list[str] | Văn bản tham chiếu |
| `vb_thay_the` | list[str] | Văn bản này thay thế VB nào |
| `vb_bi_thay_the` | list[str] | Văn bản này bị VB nào thay thế |
| `vb_lien_quan` | list[str] | Văn bản liên quan |

---

## 8. Trường để EMBEDDING (sinh vector — KHÔNG dùng làm payload filter)

| Collection | Text dùng để embed |
|---|---|
| **C2 docmeta** (`vector_full`) | `ky_hieu` + `trich_yeu` + `tom_tat` + `noi_ban_hanh` + `ten_file` |
| **C1 chunk** (`vector_chunk`) | heading/`section_path` (ngữ cảnh) + `text` (nội dung đoạn) |

> Nguyên tắc: Full vector → tìm **văn bản** liên quan tổng thể; Chunk vector → tìm **đoạn** chứng cứ.

---

## 9. BỎ khỏi payload Qdrant — **ĐÃ DỌN NHÓM AN TOÀN** (2026-07-02)

**Đã strip (chỉ nhánh chunk doffice, `qdrant_payload` → `DOFFICE_REDUNDANT_PAYLOAD_FIELDS`):**
`database_chunk_id` (trùng `chunk_id`), `parser`/`chunker` (luôn `"unknown"`), `source_file` (hằng `"document"`),
và ẩn list rỗng `section_path`/`pages`/`table_columns`/`enrichment_keywords` + `enriched=false`
(`DOFFICE_EMPTY_SUPPRESS_FIELDS`). Đã verify không nơi nào ở `app/services/retrieval/` đọc các key này.

**VẪN GIỮ (retrieval/citation/boost dùng — để dành "dọn sâu"):** `quality_status`, `document_version`,
`content_format`, `visibility`, `semantic_chunk_id`, `document_code`, `document_title`, `issued_date`,
`structure_path` (boost `retrieval_hybrid_search.py`/`retrieval_keyword_search.py`).

Lý do (nhóm giữ):
- `document_title`/`document_code`/`source_file`/`visibility`/`content_format`/`document_version`/
  `semantic_chunk_id`/`database_chunk_id` **được retrieval/citation đọc** (ES boost + dựng nguồn).
- `enriched` (và nhóm trên) nằm trong **hợp đồng payload đã có test** (`tests/test_vector_indexing.py`).
- **Lợi ích dung lượng không đáng kể**: vector 4096 chiều ≈ 16KB/point chiếm ~98%, metadata vài trăm byte.

=> Giữ nguyên payload hiện tại. Chỉ thực hiện phần **bổ sung** (§4 filter cấp văn bản) + **tạo index** (§10),
là phần mang lại giá trị thật cho filter chatbot mà không phá retrieval.

---

## 10. Payload index nên tạo trong Qdrant (để filter nhanh)

| Trường | Kiểu index | Collection |
|---|---|---|
| `acl_subjects`, `acl_deny` | keyword | C1 + C2 |
| `id_vb`, `source_type`, `chunk_type` | keyword | C1 + C2 |
| `loai_vb`, `linh_vuc`, `trang_thai_hieu_luc` | keyword | C1 + C2 |
| `nam`, `thang`, `id_dv_ban_hanh` | integer | C1 + C2 |
| `ngay_vb`, `ngay_hieu_luc`, `ngay_het_hieu_luc` | datetime | C1 + C2 |

---

## 11. Việc cần làm để áp schema này (tóm tắt mapping)

**Col2 docmeta** (`_DOCMETA_FIELDS` trong `app/services/ingestion/ingestion_doffice_unified.py`):
- Giữ nguyên phần lớn; cân nhắc chuyển `ngay_tao`/`ngay_capnhat` sang chỉ-PG.
- Thêm khi nguồn có: `loai_vb`, `linh_vuc`, `ngay_hieu_luc`, `ngay_het_hieu_luc`, `trang_thai_hieu_luc`.

**Col1 chunks** (`qdrant_payload` ở `app/services/rag/rag_chunk.py` + allowlist ở
`app/services/chunkers/chunker_doffice_chunking.py`):
- **Thêm**: `nam`, `thang`, `ngay_vb` (đổi từ `issued_date`), `id_dv_ban_hanh`, (khi có) `loai_vb`/`linh_vuc`/`trang_thai_hieu_luc`.
- **Bỏ**: danh sách ở §9; gộp alias ID về `chunk_id`; bỏ `document_code`/`document_title` trùng.

**Chung**: tạo payload index ở §10; đảm bảo ES dùng **cùng tên trường** cho filter.

---

> Đây là **đề xuất schema chuẩn**, chưa sửa code. Khi đồng ý, các thay đổi nằm gọn ở 3 file:
> `ingestion_doffice_unified.py` (C2), `rag_chunk.py` + `chunker_doffice_chunking.py` (C1),
> và bước tạo payload index ở `vector_store.py`.
