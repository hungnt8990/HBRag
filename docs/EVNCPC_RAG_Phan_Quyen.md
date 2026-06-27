# Đề xuất mô hình phân quyền dữ liệu/tài liệu cho hệ thống RAG nội bộ EVNCPC

## 1. Bối cảnh

EVNCPC có cơ cấu tổ chức nhiều cấp, gồm:

```text
EVNCPC
├── Cấp Tổng công ty
│   ├── Các ban chuyên môn
│   │   ├── Trưởng ban
│   │   ├── Phó ban
│   │   └── Chuyên viên
│   └── Các đơn vị/ban chức năng khác
│
├── Cấp Công ty Điện lực
│   ├── Giám đốc
│   ├── Phó giám đốc
│   ├── Các phòng ban chuyên môn
│   │   ├── Trưởng phòng
│   │   ├── Phó phòng
│   │   └── Chuyên viên/nhân viên
│   └── Các Điện lực/đơn vị trực thuộc nếu có
│
├── Công ty CNTT Điện lực miền Trung
│   ├── Giám đốc/Phó giám đốc
│   ├── Các phòng chuyên môn
│   └── Trưởng/phó phòng, chuyên viên
│
└── Công ty/Trung tâm Chăm sóc khách hàng
    ├── Giám đốc/Phó giám đốc
    ├── Các phòng chuyên môn
    └── Trưởng/phó phòng, chuyên viên
```

Khi triển khai RAG nội bộ, hệ thống không chỉ cần trả lời đúng mà còn phải **trả lời trong phạm vi quyền truy cập của người hỏi**.

Nguyên tắc quan trọng:

> Người dùng chỉ được truy hồi, đọc, tóm tắt và nhận câu trả lời từ những tài liệu/chunk mà họ có quyền truy cập.

---

## 2. Mô hình phân quyền khuyến nghị

Nên kết hợp 3 lớp phân quyền:

```text
RBAC + ABAC + ACL
```

Trong đó:

| Thành phần | Viết tắt | Ý nghĩa | Ví dụ |
|---|---|---|---|
| RBAC | Role-Based Access Control | Phân quyền theo vai trò/chức vụ | Giám đốc, Trưởng phòng, Chuyên viên |
| ABAC | Attribute-Based Access Control | Phân quyền theo thuộc tính | Đơn vị, phòng ban, lĩnh vực, dự án, mức độ mật |
| ACL | Access Control List | Danh sách cho phép/cấm cụ thể | User A được xem tài liệu X, nhóm B bị cấm |

Với hệ thống RAG của EVNCPC, không nên chỉ dùng RBAC vì cơ cấu tổ chức phức tạp, có nhiều trường hợp tài liệu liên quan theo tuyến nghiệp vụ hoặc theo dự án liên đơn vị.

Ví dụ:

- Ban Kinh doanh Tổng công ty cần xem tài liệu kinh doanh của các Công ty Điện lực.
- Công ty CNTT cần xem tài liệu kỹ thuật hệ thống nhưng không mặc định xem tài liệu nhân sự/tài chính mật.
- Công ty Chăm sóc khách hàng cần xem tài liệu nghiệp vụ khách hàng nhưng không nên xem tài liệu kỹ thuật nội bộ không liên quan.
- Thành viên dự án chuyển đổi số có thể đến từ nhiều đơn vị khác nhau nhưng cùng cần truy cập một nhóm tài liệu dự án.

---

## 3. Nguyên tắc phân quyền tổng quát

Công thức kiểm tra quyền:

```text
User được xem Chunk khi:

User.status = active
AND User.role phù hợp
AND User.org_path nằm trong phạm vi cho phép
AND User.business_domain khớp với tài liệu
AND User.clearance >= Document.classification
AND User không nằm trong deny list
AND action được phép
```

Hệ thống RAG phải kiểm tra quyền ở các bước:

```text
1. Người dùng đăng nhập
2. Hệ thống xác định thông tin người dùng
3. Người dùng đặt câu hỏi
4. Hệ thống xác định phạm vi quyền truy cập
5. Retrieval chỉ tìm kiếm trong tài liệu/chunk được phép
6. Rerank chỉ xử lý chunk hợp lệ
7. LLM chỉ nhận context đã lọc quyền
8. Câu trả lời chỉ trích dẫn nguồn mà người dùng có quyền xem
9. Hệ thống ghi audit log
```

Tuyệt đối không nên làm theo cách:

```text
Vector search toàn bộ kho dữ liệu
→ lấy top_k kết quả
→ sau đó mới lọc quyền
```

Cách đúng là:

```text
Tính quyền của user
→ tạo filter theo quyền
→ search trong phạm vi được phép
→ rerank
→ đưa chunk hợp lệ vào LLM
```

---

## 4. Phân quyền theo cấp tổ chức

### 4.1. Cấp Tổng công ty

Đối tượng:

- Lãnh đạo Tổng công ty
- Trưởng/phó ban Tổng công ty
- Chuyên viên các ban Tổng công ty

Quyền có thể gồm:

| Vai trò | Phạm vi truy cập gợi ý |
|---|---|
| Lãnh đạo Tổng công ty | Tài liệu toàn EVNCPC, báo cáo tổng hợp, tài liệu chỉ đạo điều hành, tài liệu chuyên đề được phân quyền |
| Trưởng/phó ban | Tài liệu của ban mình, tài liệu tuyến dọc theo lĩnh vực phụ trách |
| Chuyên viên ban | Tài liệu chuyên môn trong phạm vi được giao |

Ví dụ:

```text
Ban Kinh doanh Tổng công ty:
- Xem tài liệu kinh doanh cấp Tổng công ty
- Xem tài liệu nghiệp vụ kinh doanh của các Công ty Điện lực
- Xem tài liệu CSKH nếu được cấu hình cùng tuyến nghiệp vụ
```

### 4.2. Cấp Công ty Điện lực

Đối tượng:

- Giám đốc
- Phó giám đốc
- Trưởng/phó phòng
- Chuyên viên/nhân viên các phòng

Quyền có thể gồm:

| Vai trò | Phạm vi truy cập gợi ý |
|---|---|
| Giám đốc/phó giám đốc | Tài liệu nội bộ công ty, báo cáo công ty, tài liệu các phòng thuộc phạm vi quản lý |
| Trưởng/phó phòng | Tài liệu của phòng, tài liệu liên quan đến chức năng phòng |
| Chuyên viên/nhân viên | Tài liệu nghiệp vụ được cấp quyền hoặc tài liệu dùng chung |

Ví dụ:

```text
PC Đà Nẵng / Phòng Kinh doanh:
- Được xem tài liệu nội bộ Phòng Kinh doanh PC Đà Nẵng
- Được xem tài liệu kinh doanh dùng chung
- Không mặc định xem tài liệu Phòng Kỹ thuật hoặc Phòng Tài chính nếu không liên quan
```

### 4.3. Công ty CNTT Điện lực miền Trung

Đối tượng:

- Giám đốc/phó giám đốc
- Trưởng/phó phòng
- Chuyên viên kỹ thuật, phần mềm, hạ tầng, an toàn thông tin

Quyền có thể gồm:

| Nhóm | Phạm vi truy cập gợi ý |
|---|---|
| Ban giám đốc | Tài liệu nội bộ công ty CNTT, tài liệu dự án được giao |
| Phòng phần mềm | Tài liệu hệ thống phần mềm, API, CSDL, triển khai ứng dụng |
| Phòng hạ tầng | Tài liệu máy chủ, mạng, bảo mật, vận hành |
| Nhóm vận hành RAG | Quản trị kỹ thuật hệ thống nhưng không mặc định đọc toàn bộ tài liệu mật |

Lưu ý quan trọng:

> Quản trị hệ thống không đồng nghĩa với quyền đọc tất cả tài liệu.

Admin kỹ thuật có thể quản trị pipeline, index, trạng thái xử lý, nhưng tài liệu mật về nhân sự, tài chính, khách hàng vẫn cần quyền đọc riêng.

### 4.4. Công ty/Trung tâm Chăm sóc khách hàng

Đối tượng:

- Giám đốc/phó giám đốc
- Phòng nghiệp vụ CSKH
- Phòng tổng đài
- Phòng kỹ thuật/hỗ trợ nếu có

Quyền có thể gồm:

| Nhóm | Phạm vi truy cập gợi ý |
|---|---|
| Ban giám đốc | Tài liệu nội bộ CSKH, báo cáo nghiệp vụ |
| Phòng nghiệp vụ | Quy trình CSKH, kịch bản xử lý, chính sách khách hàng |
| Tổng đài viên | Hướng dẫn trả lời, quy trình tra cứu, tài liệu dùng cho chăm sóc khách hàng |
| Kỹ thuật hỗ trợ | Tài liệu hệ thống liên quan vận hành CSKH |

---

## 5. Metadata của người dùng

Mỗi người dùng nên có profile phân quyền dạng sau:

```json
{
  "user_id": "u001",
  "full_name": "Nguyễn Văn A",
  "email": "a@cpc.vn",
  "org_path": "/EVNCPC/PC_DANANG/PHONG_KINH_DOANH",
  "unit_type": "power_company",
  "department_code": "kinh_doanh",
  "position_level": "specialist",
  "business_domains": ["kinh_doanh", "cskh"],
  "project_codes": ["evn_cskh_2026"],
  "roles": ["UNIT_USER"],
  "groups": ["pc_danang_kinh_doanh"],
  "clearance_level": "internal",
  "employment_status": "active"
}
```

Các thuộc tính nên có:

| Thuộc tính | Ý nghĩa |
|---|---|
| `user_id` | Mã định danh người dùng |
| `org_path` | Đường dẫn tổ chức của người dùng |
| `unit_type` | Loại đơn vị: Tổng công ty, Công ty Điện lực, Công ty CNTT, CSKH |
| `department_code` | Mã phòng/ban |
| `position_level` | Cấp chức vụ |
| `business_domains` | Lĩnh vực nghiệp vụ được tham gia |
| `project_codes` | Dự án/tổ công tác người dùng tham gia |
| `roles` | Vai trò hệ thống |
| `groups` | Nhóm quyền |
| `clearance_level` | Mức truy cập dữ liệu |
| `employment_status` | Trạng thái nhân sự |

---

## 6. Metadata của tài liệu

Mỗi tài liệu nên có metadata phân quyền như sau:

```json
{
  "doc_id": "doc001",
  "title": "Quy trình kinh doanh điện năng",
  "owner_org_path": "/EVNCPC/BAN_KINH_DOANH",
  "scope": "functional_vertical",
  "classification": "internal",
  "business_domains": ["kinh_doanh", "cskh"],
  "project_codes": [],
  "allowed_org_paths": [
    "/EVNCPC/BAN_KINH_DOANH",
    "/EVNCPC/*/PHONG_KINH_DOANH",
    "/EVNCPC/CSKH/*"
  ],
  "allowed_roles": [
    "board_head",
    "board_deputy",
    "department_head",
    "department_deputy",
    "specialist"
  ],
  "allowed_groups": [],
  "allowed_users": [],
  "denied_groups": ["external"],
  "inherit_permission": true
}
```

Các thuộc tính nên có:

| Thuộc tính | Ý nghĩa |
|---|---|
| `owner_org_path` | Đơn vị sở hữu tài liệu |
| `scope` | Phạm vi sử dụng tài liệu |
| `classification` | Mức độ nhạy cảm/mật của tài liệu |
| `business_domains` | Lĩnh vực nghiệp vụ |
| `project_codes` | Dự án liên quan |
| `allowed_org_paths` | Danh sách tổ chức được phép |
| `allowed_roles` | Vai trò được phép |
| `allowed_groups` | Nhóm quyền được phép |
| `allowed_users` | Người dùng được phép cụ thể |
| `denied_*` | Danh sách bị cấm |
| `inherit_permission` | Có kế thừa quyền từ document xuống chunk hay không |

---

## 7. Phân loại phạm vi tài liệu

Nên có các mức `scope` sau:

| Scope | Ý nghĩa | Ví dụ |
|---|---|---|
| `public_internal` | Nội bộ toàn EVNCPC đều xem được | Tin tức nội bộ, hướng dẫn chung |
| `corp_wide` | Dùng chung cấp Tổng công ty | Quy định, quy chế, thông báo toàn EVNCPC |
| `functional_vertical` | Theo tuyến nghiệp vụ | Kinh doanh, kỹ thuật, CSKH, CNTT |
| `unit_only` | Chỉ trong một công ty/đơn vị | Kế hoạch riêng của PC Đà Nẵng |
| `department_only` | Chỉ trong phòng/ban | Phân công nội bộ Phòng Kỹ thuật |
| `project_only` | Chỉ thành viên dự án | Dự án chuyển đổi số, dự án OCR, dự án RAG |
| `leadership_only` | Chỉ lãnh đạo cấp được chỉ định | Báo cáo điều hành |
| `explicit_acl` | Chỉ người/nhóm được chỉ định cụ thể | Hồ sơ mật, tài liệu nhân sự, tài chính |

---

## 8. Phân loại mức độ nhạy cảm của tài liệu

Nên có các mức `classification` sau:

| Classification | Ý nghĩa | Gợi ý xử lý |
|---|---|---|
| `public_internal` | Dùng chung nội bộ | Cho phép rộng trong EVNCPC |
| `internal` | Nội bộ bình thường | Theo tổ chức/nghiệp vụ |
| `restricted` | Hạn chế | Cần vai trò hoặc nhóm phù hợp |
| `confidential` | Mật/nội bộ nhạy cảm | Nên explicit ACL |
| `personal_data` | Dữ liệu cá nhân/khách hàng/nhân sự | Kiểm soát chặt, log truy cập |
| `secret` | Tài liệu mật đặc biệt | Nên tách kho hoặc collection riêng |

Nguyên tắc:

```text
classification càng cao thì quyền truy cập càng phải rõ ràng.
```

Với `confidential`, `personal_data`, `secret`, không nên cho kế thừa quyền rộng theo đơn vị nếu chưa được cấu hình cụ thể.

---

## 9. Phân quyền ở cấp chunk

Trong RAG, không nên chỉ phân quyền ở cấp file.

Lý do: một file có thể chứa nhiều phần với độ nhạy cảm khác nhau.

Ví dụ:

```text
Báo cáo kinh doanh tháng
├── Phần 1: Tổng quan toàn EVNCPC
├── Phần 2: Số liệu PC Đà Nẵng
├── Phần 3: Số liệu PC Quảng Nam
├── Phần 4: Danh sách khách hàng nợ tiền
└── Phần 5: Kiến nghị xử lý nội bộ
```

Nếu chỉ gắn quyền cho cả file thì có thể gây rò rỉ phần nhạy cảm.

Nên gắn quyền theo cấu trúc:

```text
Document
└── Section
    └── Chunk
        └── Table row nếu cần
```

Ví dụ:

| Chunk | Nội dung | Quyền |
|---|---|---|
| Chunk 1 | Tổng quan toàn EVNCPC | Lãnh đạo, ban chuyên môn |
| Chunk 2 | Số liệu PC Đà Nẵng | PC Đà Nẵng + ban liên quan |
| Chunk 3 | Danh sách khách hàng | Nhóm được cấp quyền cụ thể |
| Chunk 4 | Kiến nghị xử lý | Lãnh đạo + phòng chuyên môn |

Metadata chunk nên có:

```json
{
  "chunk_id": "chunk001",
  "document_id": "doc001",
  "section_title": "Danh sách khách hàng nợ tiền",
  "page_number": 12,
  "access": {
    "scope": "explicit_acl",
    "classification": "personal_data",
    "owner_org_path": "/EVNCPC/PC_DANANG/PHONG_KINH_DOANH",
    "business_domains": ["kinh_doanh", "cskh"],
    "project_codes": [],
    "allowed_org_paths": [],
    "allowed_roles": [],
    "allowed_groups": ["debt_collection_team"],
    "allowed_users": [],
    "denied_groups": [],
    "inherit_permission": false
  }
}
```

---

## 10. Ma trận quyền gợi ý

| Người dùng | Tài liệu toàn EVNCPC | Tài liệu đơn vị mình | Tài liệu phòng mình | Tài liệu tuyến dọc | Tài liệu mật |
|---|---:|---:|---:|---:|---:|
| Lãnh đạo Tổng công ty | Có | Có chọn lọc | Có chọn lọc | Có | Theo phân quyền |
| Trưởng/phó ban Tổng công ty | Có | Có nếu liên quan | Có nếu liên quan | Có | Theo phân quyền |
| Chuyên viên ban Tổng công ty | Có | Có nếu được giao | Có nếu liên quan | Có trong mảng | Theo phân quyền |
| Giám đốc/phó giám đốc Công ty Điện lực | Có | Có | Có nếu thuộc phạm vi quản lý | Có nếu liên quan | Theo phân quyền |
| Trưởng/phó phòng Công ty Điện lực | Có | Có trong phạm vi | Có | Có trong mảng | Theo phân quyền |
| Chuyên viên Công ty Điện lực | Có | Có giới hạn | Có nếu cùng phòng | Có nếu được cấp | Không mặc định |
| Nhân sự Công ty CNTT | Có | Tài liệu Công ty CNTT | Theo phòng | Có nếu vận hành/dự án | Không mặc định |
| Nhân sự CSKH | Có | Tài liệu CSKH | Theo phòng | Mảng CSKH/Kinh doanh | Không mặc định |

---

## 11. Các action cần tách riêng

Trong RAG, không nên gộp tất cả thành một quyền `read`.

Nên tách các hành động:

| Action | Ý nghĩa |
|---|---|
| `search` | Được tìm thấy tài liệu/chunk |
| `read_answer` | Được dùng nội dung để sinh câu trả lời |
| `view_citation` | Được thấy nguồn trích dẫn |
| `open_document` | Được mở file gốc |
| `download` | Được tải file |
| `ingest` | Được nạp tài liệu |
| `approve` | Được duyệt tài liệu vào kho RAG |
| `manage_acl` | Được sửa quyền tài liệu |
| `delete` | Được xóa tài liệu khỏi kho |

Ví dụ:

```text
Chuyên viên có thể được hỏi đáp từ quy trình
nhưng không nhất thiết được tải file gốc.
```

---

## 12. Kiến trúc xử lý quyền trong RAG

Kiến trúc khuyến nghị:

```text
[SSO/AD/LDAP/HRM]
        ↓
[User Profile Service]
        ↓
[Policy Engine: RBAC + ABAC + ACL]
        ↓
[RAG API]
        ↓
[Permission-aware Retriever]
        ↓
[Vector DB / Keyword Index / Graph DB]
        ↓
[Reranker]
        ↓
[LLM]
        ↓
[Answer + Citation + Audit Log]
```

Trong đó:

| Thành phần | Vai trò |
|---|---|
| SSO/AD/LDAP/HRM | Xác thực và lấy thông tin người dùng |
| User Profile Service | Chuẩn hóa thông tin người dùng |
| Policy Engine | Ra quyết định cho phép/từ chối |
| RAG API | Nhận câu hỏi, truyền access context |
| Permission-aware Retriever | Chỉ truy hồi dữ liệu được phép |
| Vector DB/Keyword Index | Lưu chunk và metadata quyền |
| Reranker | Xếp hạng lại chunk hợp lệ |
| LLM | Sinh câu trả lời từ context hợp lệ |
| Audit Log | Lưu vết truy cập, phục vụ kiểm tra |

---

## 13. Luồng truy vấn RAG có phân quyền

```text
User hỏi:
"Quy định về chăm sóc khách hàng mới nhất là gì?"

Bước 1: Xác định user
- Thuộc đơn vị: CSKH
- Phòng: Nghiệp vụ
- Vai trò: Chuyên viên
- Domain: cskh, kinh_doanh
- Clearance: internal

Bước 2: Tạo access filter
- Chỉ lấy tài liệu internal trở xuống
- Domain phải là cskh hoặc kinh_doanh
- Scope có thể là public_internal, corp_wide, functional_vertical, unit_only nếu phù hợp
- Loại tài liệu confidential/personal_data nếu không có explicit ACL

Bước 3: Search
- Vector search có filter theo quyền
- Keyword search có filter theo quyền

Bước 4: Rerank
- Chỉ rerank chunk đã qua kiểm tra quyền

Bước 5: LLM
- Chỉ đưa chunk hợp lệ vào prompt

Bước 6: Trả lời
- Có citation
- Citation cũng phải nằm trong phạm vi quyền của user
```

---

## 14. Ví dụ chính sách phân quyền

### 14.1. Tài liệu dùng chung toàn EVNCPC

```json
{
  "scope": "corp_wide",
  "classification": "internal",
  "allowed_org_paths": ["/EVNCPC/*"],
  "inherit_permission": true
}
```

Ai được xem:

```text
Tất cả người dùng nội bộ đang active.
```

### 14.2. Tài liệu nội bộ Công ty Điện lực Đà Nẵng

```json
{
  "scope": "unit_only",
  "classification": "internal",
  "owner_org_path": "/EVNCPC/PC_DANANG",
  "allowed_org_paths": ["/EVNCPC/PC_DANANG/*"],
  "inherit_permission": true
}
```

Ai được xem:

```text
Người thuộc PC Đà Nẵng.
```

Không được xem:

```text
Người thuộc PC Quảng Nam, PC Quảng Ngãi, đơn vị khác,
trừ khi được cấp thêm quyền.
```

### 14.3. Tài liệu tuyến nghiệp vụ Kinh doanh

```json
{
  "scope": "functional_vertical",
  "classification": "internal",
  "business_domains": ["kinh_doanh", "cskh"],
  "allowed_org_paths": [
    "/EVNCPC/BAN_KINH_DOANH",
    "/EVNCPC/*/PHONG_KINH_DOANH",
    "/EVNCPC/CSKH/*"
  ],
  "inherit_permission": true
}
```

Ai được xem:

```text
- Ban Kinh doanh Tổng công ty
- Phòng Kinh doanh các Công ty Điện lực
- CSKH nếu thuộc mảng nghiệp vụ liên quan
```

Không được xem:

```text
- Phòng Kỹ thuật nếu không được cấp thêm quyền
- Phòng Nhân sự nếu không liên quan
```

### 14.4. Tài liệu dự án liên đơn vị

```json
{
  "scope": "project_only",
  "classification": "restricted",
  "project_codes": ["chuyen_doi_so_2026"],
  "allowed_groups": ["project_chuyen_doi_so_2026"],
  "inherit_permission": true
}
```

Ai được xem:

```text
Thành viên dự án chuyển đổi số 2026,
dù thuộc Tổng công ty, CPCIT, CSKH hay Công ty Điện lực.
```

### 14.5. Tài liệu nhạy cảm

```json
{
  "scope": "explicit_acl",
  "classification": "confidential",
  "allowed_users": ["u001", "u002"],
  "allowed_groups": ["board_leadership"],
  "denied_groups": ["external", "contractor"],
  "inherit_permission": false
}
```

Nguyên tắc:

```text
Chỉ người/nhóm được cấp quyền cụ thể mới được xem.
Không kế thừa rộng theo đơn vị.
Deny luôn ưu tiên hơn allow.
```

---

## 15. Thiết kế bảng dữ liệu gợi ý

### 15.1. Bảng tổ chức

```sql
org_units
- id
- code
- name
- type
- parent_id
- path
- business_domain
```

Ví dụ `type`:

```text
corporation
board
power_company
support_company
customer_service_company
department
```

### 15.2. Bảng người dùng

```sql
users
- id
- employee_code
- full_name
- email
- org_unit_id
- position_title
- position_level
- status
```

### 15.3. Bảng nhóm quyền

```sql
groups
- id
- code
- name
- type
```

Ví dụ `type`:

```text
role
project
functional
system
```

### 15.4. Bảng thành viên nhóm

```sql
user_group_memberships
- user_id
- group_id
- valid_from
- valid_to
```

### 15.5. Bảng tài liệu

```sql
documents
- id
- title
- owner_org_id
- document_type
- business_domain
- scope
- classification
- status
- effective_date
- expired_date
- created_by
```

### 15.6. Bảng chunk

```sql
document_chunks
- id
- document_id
- chunk_text
- section_title
- page_number
- business_domain
- classification
- scope
- acl_policy_id
- embedding_vector
```

### 15.7. Bảng chính sách quyền

```sql
access_policies
- id
- policy_name
- effect
- subject_condition
- resource_condition
- action
- priority
```

Ví dụ policy:

```json
{
  "policy_name": "Ban Kinh doanh TCT xem tài liệu kinh doanh tuyến dọc",
  "effect": "allow",
  "subject_condition": {
    "org_path": "/EVNCPC/BAN_KINH_DOANH/*"
  },
  "resource_condition": {
    "business_domain": "kinh_doanh",
    "scope": ["functional_vertical", "corp_wide"]
  },
  "actions": ["search", "read_answer", "view_citation"]
}
```

---

## 16. Yêu cầu đối với Vector Database / Qdrant

Khi index chunk vào vector database, payload cần có đủ metadata để filter quyền.

Ví dụ payload:

```json
{
  "document_id": "doc001",
  "chunk_id": "chunk001",
  "owner_org_id": "org001",
  "owner_org_path": "/EVNCPC/PC_DANANG/PHONG_KINH_DOANH",
  "scope": "department_only",
  "classification": "internal",
  "business_domains": ["kinh_doanh"],
  "project_codes": [],
  "allowed_org_paths": ["/EVNCPC/PC_DANANG/PHONG_KINH_DOANH/*"],
  "allowed_role_names": ["department_head", "department_deputy", "specialist"],
  "allowed_group_codes": [],
  "allowed_user_ids": [],
  "denied_org_paths": [],
  "denied_role_names": [],
  "denied_group_codes": [],
  "denied_user_ids": [],
  "inherit_permission": true
}
```

Các field nên tạo payload index:

```text
document_id
owner_org_id
scope
classification
business_domains
project_codes
allowed_org_paths
allowed_role_names
allowed_group_codes
allowed_user_ids
denied_org_paths
denied_role_names
denied_group_codes
denied_user_ids
```

---

## 17. Yêu cầu đối với keyword search

Keyword search cũng phải lọc quyền, không chỉ vector search.

Nguyên tắc:

```text
PostgreSQL full-text search hoặc keyword search
phải join/where theo document/chunk mà user có quyền.
```

Nếu chưa filter được đầy đủ ở SQL, tối thiểu phải:

```text
1. Tính visible_document_ids theo user
2. Search keyword chỉ trong visible_document_ids
3. Kiểm tra tiếp chunk-level ACL
4. Chỉ trả kết quả hợp lệ
```

---

## 18. Các tình huống cần test

### Case 1: Tài liệu toàn EVNCPC

```text
Tài liệu scope=corp_wide, classification=internal.
Mọi active user nội bộ được search/read_answer.
```

### Case 2: Tài liệu theo đơn vị

```text
Tài liệu scope=unit_only của PC_DANANG.

- User thuộc PC_DANANG: được xem
- User thuộc PC_QUANGNAM: không được xem
- Ban Tổng công ty liên quan: được xem nếu có policy tuyến dọc
```

### Case 3: Tài liệu theo phòng ban

```text
Tài liệu scope=department_only của PC_DANANG/PHONG_KINH_DOANH.

- Chuyên viên phòng kinh doanh: được xem
- Phòng kỹ thuật cùng PC: không được xem nếu không có allow thêm
- Giám đốc PC: được xem nếu policy leadership/unit admin cho phép
```

### Case 4: Tài liệu theo tuyến nghiệp vụ

```text
Tài liệu scope=functional_vertical, business_domain=kinh_doanh.

- Ban Kinh doanh Tổng công ty: được xem
- Phòng Kinh doanh các PC: được xem
- CSKH: được xem nếu domain gồm cskh hoặc được allow group
- Phòng Kỹ thuật: không được xem
```

### Case 5: Tài liệu dự án

```text
Tài liệu scope=project_only, project_code=chuyen_doi_so_2026.

- User thuộc project: được xem
- User không thuộc project: không được xem
```

### Case 6: Tài liệu nhạy cảm

```text
Tài liệu classification=confidential hoặc personal_data.

- Không được kế thừa global/subtree rộng
- Chỉ explicit ACL hoặc clearance phù hợp mới được xem
- deny_user/deny_group override allow
```

### Case 7: Chống leak trong RAG

Khi user hỏi nội dung chỉ nằm trong chunk không có quyền:

```text
- Search không trả chunk đó
- Rerank không nhận chunk đó
- LLM prompt không chứa chunk đó
- Response nói không tìm thấy trong phạm vi quyền
- Citation không chứa document/chunk đó
```

### Case 8: Client không được tự mở rộng quyền

Nếu client gửi `allowed_document_ids` gồm cả tài liệu không được phép:

```text
Backend phải intersect với tập tài liệu user thật sự được phép.
Tài liệu không có quyền phải bị loại.
Không được leak qua answer hoặc citation.
```

---

## 19. Audit log

Cần ghi log các hành động:

```text
search
read_answer
view_citation
open_document
download
ingest
manage_acl
delete
denied_access
```

Thông tin log nên gồm:

```text
user_id
action
document_id
chunk_id nếu có
decision allowed/denied
reason
timestamp
request_id/session_id
```

Không nên log toàn bộ nội dung tài liệu nhạy cảm vào audit log.

---

## 20. Quy tắc triển khai an toàn

Các quy tắc bắt buộc:

```text
1. Không để LLM tự quyết định quyền.
2. Không đưa chunk không có quyền vào prompt.
3. Không citation tài liệu người dùng không được xem.
4. Không vector search toàn kho rồi mới lọc quyền.
5. Deny luôn ưu tiên hơn allow.
6. Tài liệu confidential/personal_data/secret phải có explicit ACL hoặc policy rõ.
7. Admin hệ thống không mặc định được đọc mọi tài liệu.
8. Quyền download phải tách khỏi quyền hỏi đáp.
9. Mọi truy cập nhạy cảm phải ghi audit log.
10. Metadata quyền phải được index cùng chunk.
```

---

## 21. Kết luận

Mô hình phù hợp cho EVNCPC là:

```text
Một kho RAG thống nhất
nhưng mỗi document/chunk đều có metadata phân quyền.
Retriever bắt buộc lọc theo quyền trước khi đưa dữ liệu cho LLM.
Tài liệu cực nhạy cảm nên tách collection hoặc kho riêng.
```

Cách triển khai nên đi theo hướng:

```text
RBAC để quản lý vai trò lớn
+
ABAC để xử lý đơn vị, phòng ban, tuyến nghiệp vụ, dự án, mức độ mật
+
ACL để xử lý trường hợp đặc biệt hoặc tài liệu nhạy cảm
```

Tóm lại:

> Phân quyền RAG không phải chỉ là “ai được mở file nào”, mà là “ai được truy hồi chunk nào, dùng chunk đó để sinh câu trả lời nào, thấy citation nào, và có được mở/tải file gốc hay không”.
