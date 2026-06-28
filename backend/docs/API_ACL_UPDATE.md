# API cập nhật ACL văn bản (DOffice → HBRag)

> API để hệ thống **DOffice** chủ động đẩy cập nhật quyền (ACL) cho 1 văn bản, thay cho việc
> chạy job sync thủ công. Mỗi khi DOffice thay đổi quyền, gọi API này → hệ tự **nén ACL** và
> cập nhật đồng thời **PostgreSQL** + **Elasticsearch**.

## Tổng quan

| Mục | Giá trị |
|---|---|
| **Endpoint** | `POST /api/doffice/acl/update` |
| **Content-Type** | `application/json` |
| **Xác thực** | Header `X-API-Key` (nếu `DOFFICE_ACL_API_KEY` được cấu hình; rỗng = mở cho dev) |
| **Hàm lõi** | `update_document_acl()` trong `app/services/retrieval/document_acl_update_service.py` (thuần domain, KHÔNG dính xác thực — thêm phân quyền sau ở route/dependency không ảnh hưởng hàm này) |
| **Tự tạo mới** | Nếu văn bản **chưa có** trong hệ thống, API tạo mới theo luồng **đơn lẻ** (khác sync lấy batch): fetch **nội dung** từ `doffice_vanban` (term theo id_vb) + fetch **quyền** từ `doffice_vanban_quyen` (term theo id_vb) → embedding (BBQ) → tạo mới PG + ES. ACL khi tạo lấy từ **quyền nguồn** (`doffice_vanban_quyen`); nếu nguồn không có record quyền thì dùng params. Chỉ trả 404 khi văn bản cũng **không có ở `doffice_vanban`**. |

## Tham số (request body)

| Trường | Kiểu | Bắt buộc | Mô tả |
|---|---|---|---|
| `id_vb` | string | ✅ | Mã văn bản DOffice cần cập nhật quyền |
| `don_vi_list` | int[] | ❌ (mặc định `[]`) | Danh sách id **đơn vị** được cấp |
| `phong_ban_list` | int[] | ❌ (mặc định `[]`) | Danh sách id **phòng ban** được cấp |
| `ca_nhan_list` | int[] | ❌ (mặc định `[]`) | Danh sách id **nhân viên** được cấp |

> **Quy tắc nén (giống job sync):** nếu `ca_nhan_list` có người → người nhận = các cá nhân đó
> (`phong_ban_list`/`don_vi_list` chỉ là phạm vi). Nếu `ca_nhan_list` trống → cấp cho cả phòng
> trong `phong_ban_list`. Bộ nén tự gộp lên phòng/đơn vị + sinh deny khi cần.

## Kết quả (response 200)

| Trường | Kiểu | Mô tả |
|---|---|---|
| `id_vb` | string | Mã văn bản |
| `document_id` | string (UUID) | ID nội bộ của văn bản |
| `action` | string | `created` (tự fetch + embed + tạo mới) hoặc `acl_updated` (đã có, chỉ đổi quyền) |
| `acl_source` | string | Nguồn ACL: `params` (DOffice đẩy — luồng update) hoặc `doffice_vanban_quyen` (fetch từ nguồn — luồng create) |
| `updated` | bool | Đã ghi **PostgreSQL** (`document_metadata.access`) |
| `es_updated` | bool | Đã ghi **Elasticsearch** (`false` nếu doc đã có ở PG nhưng thiếu record ES) |
| `has_embedding` | bool | Có vector BBQ (chỉ liên quan khi `action=created`) |
| `acl_subjects` | string[] | Khoá ALLOW phẳng đã nén (vd `["pb_40036","nv_90263"]`) — dùng để LỌC khi search |
| `acl_deny_pb` | int[] | Phòng ban bị chặn (ngoại lệ) |
| `acl_deny_nv` | int[] | Nhân viên bị chặn (ngoại lệ) |
| `quyen_checksum` | string | SHA-256 của 3 list (sorted/unique) — audit/idempotent |
| `warnings` | string[] | Cảnh báo (vd cá nhân ngoài phạm vi…) |

## Mã trạng thái

| Code | Khi nào |
|---|---|
| `200` | Cập nhật quyền thành công (`action=acl_updated`) hoặc tự tạo mới thành công (`action=created`) |
| `401` | Thiếu/sai `X-API-Key` (khi đã cấu hình `DOFFICE_ACL_API_KEY`) |
| `404` | Văn bản không có ở PostgreSQL **LẪN** nguồn DOffice (không thể tạo) |
| `502` | Lỗi khi ghi Elasticsearch / tạo mới văn bản |
| `422` | Body sai định dạng (FastAPI validation) |

## Ví dụ

```bash
curl --location 'http://localhost:8000/api/doffice/acl/update' \
--header 'Content-Type: application/json' \
--header 'X-API-Key: <doffice_acl_api_key>' \
--data '{
    "id_vb": "1084300",
    "don_vi_list": [256],
    "phong_ban_list": [40035, 40036, 41633, 42102, 42208],
    "ca_nhan_list": [90263, 90288, 90255]
}'
```

Phản hồi:

```json
{
    "id_vb": "1084300",
    "document_id": "4c8bb660-9d02-479a-96b8-de0315bd1791",
    "action": "acl_updated",
    "acl_source": "params",
    "updated": true,
    "es_updated": true,
    "has_embedding": true,
    "acl_subjects": ["nv_90263", "pb_40036", "pb_42102", "pb_42208"],
    "acl_deny_pb": [],
    "acl_deny_nv": [90251, 90252],
    "quyen_checksum": "4b806f9306fe86c1...",
    "warnings": []
}
```

## Ghi chú triển khai

- **Hai luồng:**
  - *Đã có* (`acl_updated`, `acl_source=params`): nén ACL từ **params** → cập nhật PG `access` +
    **partial update** 3 trường ACL trên ES (không đụng nội dung/embedding).
  - *Chưa có* (`created`): luồng đơn lẻ — `_fetch_vanban()` (nội dung, term id_vb) +
    `_fetch_quyen()` (quyền từ `doffice_vanban_quyen`, term id_vb) → `_embed()` (BBQ) →
    `_create_document()` tạo PG + ES đầy đủ. ACL ưu tiên **quyền nguồn**
    (`acl_source=doffice_vanban_quyen`); nguồn trống thì fallback params. KHÁC sync: sync lấy
    **batch/scroll**, API này lấy **1 văn bản theo id_vb**.
- **Cập nhật được ghi:** PG `documents.document_metadata.access` (`acl` nén + `raw_assignment` 3 list
  gốc + `quyen_checksum` + `acl_ver` = phiên bản danh mục); ES: 3 trường
  `acl_subjects` / `acl_deny_pb` / `acl_deny_nv` (+ nội dung & embedding khi tạo mới).
- **Thứ tự:** cập nhật PG (commit) → cập nhật ES. Nếu ES lỗi → trả 502 (PG đã ghi; gọi lại idempotent
  sẽ đồng bộ lại do `quyen_checksum` ổn định).
- **Phân quyền sau này:** chỉ cần sửa `require_acl_update_access`
  (`app/api/dependencies/acl_update_auth.py`) — không đụng `update_document_acl`.
- **Cấu hình API key:** đặt biến môi trường `DOFFICE_ACL_API_KEY` (rỗng = không yêu cầu key).
