Tôi sẽ đề xuất theo hướng “phân quyền tài liệu trước, RAG chỉ được truy hồi trong phạm vi quyền đó”, rồi ánh xạ vào mô hình Tổng công ty → Công ty → Phòng/ban → vai trò/cá nhân.

Cốt lõi là không nên tạo quyền theo từng file thủ công. Nên gắn “thuộc tính” cho người dùng và tài liệu, sau đó để policy engine tự quyết định được truy cập hay không.

Đã suy nghĩ trong 50s

Bạn nên thiết kế phân quyền RAG theo mô hình **RBAC + ABAC + ACL theo tài liệu/chunk**, không nên chỉ phân quyền kiểu “ai thuộc công ty nào thì thấy công ty đó”. Với cơ cấu Tổng công ty điện lực miền Trung, phân quyền phải vừa theo **cấp tổ chức**, vừa theo **chức năng chuyên môn**, vừa theo **độ mật của tài liệu**.

Nói ngắn gọn:

> **Người dùng có thuộc tính gì? Tài liệu có thuộc tính gì? Hành động là gì? Chính sách có cho phép không? Nếu có thì RAG mới được truy hồi.**

Mô hình này phù hợp với RBAC, tức quyền gắn với vai trò, và ABAC, tức quyền được quyết định bằng thuộc tính của người dùng, tài nguyên, hành động và điều kiện môi trường. NIST mô tả RBAC là mô hình kiểm soát quyền dựa trên vai trò, còn ABAC là mô hình xét thuộc tính của chủ thể, đối tượng, thao tác và điều kiện để ra quyết định truy cập.

---

## 1\. Cấu trúc phân quyền tổng thể nên như sau

    EVNCPC├── Tổng công ty│   ├── Ban Kỹ thuật│   │   ├── Trưởng ban│   │   ├── Phó ban│   │   └── Chuyên viên│   ├── Ban Kinh doanh│   ├── Ban CNTT│   └── ...│├── Các Công ty Điện lực│   ├── PC Đà Nẵng│   │   ├── Ban Giám đốc│   │   ├── Phòng Kỹ thuật│   │   ├── Phòng Kinh doanh│   │   └── ...│   ├── PC Quảng Nam│   └── ...│├── Công ty CNTT Điện lực miền Trung│   ├── Ban Giám đốc│   ├── Phòng Phần mềm│   ├── Phòng Hạ tầng│   └── ...│└── Trung tâm/Công ty Chăm sóc khách hàng    ├── Ban Giám đốc    ├── Phòng Tổng đài    ├── Phòng Nghiệp vụ    └── ...

Nhưng trong hệ thống RAG, đừng chỉ lưu cây tổ chức. Nên lưu thêm **nhóm quyền nghiệp vụ**.

Ví dụ:

    Người dùng A:- Thuộc đơn vị: PC Đà Nẵng- Thuộc phòng: Phòng Kinh doanh- Chức vụ: Chuyên viên- Nhóm chức năng: Kinh doanh- Nhóm hệ thống: user_internal- Mức truy cập: Nội bộ đơn vị- Dự án tham gia: EVN CSKH, Chuyển đổi số 2026

---

## 2\. Nên chia quyền theo 6 lớp

### Lớp 1: Cấp tổ chức

Dùng để biết người dùng thuộc đâu.

| Cấp              | Ví dụ                            | Ý nghĩa phân quyền                          |
| ---------------- | -------------------------------- | ------------------------------------------- |
| Tổng công ty     | EVNCPC                           | Có thể có tài liệu dùng chung toàn hệ thống |
| Ban Tổng công ty | Ban Kỹ thuật, Ban Kinh doanh     | Có quyền chuyên môn theo tuyến dọc          |
| Công ty Điện lực | PC Đà Nẵng, PC Quảng Nam         | Có quyền trong phạm vi đơn vị               |
| Công ty hậu cần  | CPCIT, CSKH                      | Có quyền theo chức năng hỗ trợ              |
| Phòng/ban        | Phòng Kỹ thuật, Phòng Kinh doanh | Có quyền chi tiết nội bộ phòng              |
| Cá nhân          | Nguyễn Văn A                     | Có quyền theo hồ sơ, nhiệm vụ, dự án        |

---

### Lớp 2: Vai trò/chức vụ

Đây là **RBAC**.

Ví dụ nhóm vai trò:

| Nhóm vai trò                           | Quyền điển hình                                            |
| -------------------------------------- | ---------------------------------------------------------- |
| Lãnh đạo Tổng công ty                  | Xem nhiều nhóm tài liệu cấp Tổng công ty, báo cáo tổng hợp |
| Trưởng/phó ban Tổng công ty            | Xem tài liệu của ban mình và tài liệu chuyên môn tuyến dọc |
| Chuyên viên ban Tổng công ty           | Xem tài liệu chuyên môn được phân quyền                    |
| Giám đốc/phó giám đốc Công ty Điện lực | Xem tài liệu cấp công ty mình, báo cáo đơn vị              |
| Trưởng/phó phòng Công ty Điện lực      | Xem tài liệu phòng mình và tài liệu liên quan              |
| Chuyên viên phòng                      | Xem tài liệu nghiệp vụ được giao                           |
| Quản trị hệ thống RAG                  | Quản trị kỹ thuật, không mặc định được xem nội dung mật    |

Điểm quan trọng: **Admin hệ thống không nên mặc định có quyền đọc mọi tài liệu**. Admin có thể quản trị index, tài khoản, trạng thái xử lý, nhưng tài liệu mật/nhân sự/tài chính vẫn phải tách quyền đọc.

---

### Lớp 3: Thuộc tính nghiệp vụ

Đây là **ABAC**.

Mỗi người dùng nên có các thuộc tính:

    {  "user_id": "u001",  "org_path": "/EVNCPC/PC_DANANG/PHONG_KINH_DOANH",  "unit_type": "power_company",  "department": "kinh_doanh",  "position_level": "specialist",  "business_domain": ["kinh_doanh", "cskh"],  "projects": ["evn_cskh_2026"],  "clearance": "internal",  "employment_status": "active"}

Mỗi tài liệu cũng có thuộc tính:

    {  "doc_id": "doc001",  "owner_org_path": "/EVNCPC/BAN_KINH_DOANH",  "scope": "functional",  "business_domain": ["kinh_doanh", "cskh"],  "classification": "internal",  "allowed_roles": ["dept_head", "deputy_head", "specialist"],  "allowed_orgs": ["/EVNCPC/BAN_KINH_DOANH", "/EVNCPC/*/PHONG_KINH_DOANH"],  "denied_groups": ["external", "inactive_user"]}

Khi người dùng hỏi, hệ thống kiểm tra:

    Người dùng có đang hoạt động không?Người dùng thuộc đơn vị/phòng nào?Người dùng có vai trò gì?Tài liệu thuộc đơn vị nào?Tài liệu thuộc mảng nghiệp vụ nào?Tài liệu có mức mật gì?Người dùng có nằm trong phạm vi được xem không?

---

## 3\. Nên chia tài liệu thành các mức phạm vi

Tôi đề xuất dùng các mức sau:

| Mức tài liệu          | Ai được xem                                                                              |
| --------------------- | ---------------------------------------------------------------------------------------- |
| `PUBLIC_INTERNAL`     | Toàn bộ nhân sự EVNCPC đã đăng nhập                                                      |
| `CORP_WIDE`           | Toàn EVNCPC, nhưng chỉ nội bộ                                                            |
| `FUNCTIONAL_VERTICAL` | Theo tuyến nghiệp vụ, ví dụ Ban Kinh doanh Tổng công ty + Phòng Kinh doanh các PC + CSKH |
| `UNIT_ONLY`           | Chỉ trong một công ty/đơn vị                                                             |
| `DEPARTMENT_ONLY`     | Chỉ trong một phòng/ban                                                                  |
| `PROJECT_ONLY`        | Chỉ thành viên dự án/tổ công tác                                                         |
| `LEADERSHIP_ONLY`     | Chỉ lãnh đạo cấp được chỉ định                                                           |
| `CONFIDENTIAL`        | Phải cấp quyền cụ thể, không kế thừa tự động                                             |
| `PERSONAL_DATA`       | Hồ sơ cá nhân, nhân sự, khách hàng, thông tin định danh                                  |
| `SECRET/REGULATED`    | Tài liệu mật/nhạy cảm, nên tách kho hoặc tách index riêng                                |

---

## 4\. Cấu trúc quyền hợp lý cho EVNCPC

### Trường hợp 1: Tài liệu dùng chung toàn Tổng công ty

Ví dụ: quy trình ISO chung, quy chế nội bộ, thông báo chung.

    scope = CORP_WIDEclassification = INTERNALallowed_org = /EVNCPC/*

Ai xem được: toàn bộ nhân sự nội bộ đã đăng nhập.

---

### Trường hợp 2: Tài liệu chuyên môn theo tuyến dọc

Ví dụ: tài liệu kinh doanh, chăm sóc khách hàng, kỹ thuật, an toàn điện.

    scope = FUNCTIONAL_VERTICALbusiness_domain = KINH_DOANHallowed_orgs =- /EVNCPC/BAN_KINH_DOANH- /EVNCPC/*/PHONG_KINH_DOANH- /EVNCPC/CSKH/*

Ai xem được:

| Người dùng                       | Có nên xem?                  |
| -------------------------------- | ---------------------------- |
| Ban Kinh doanh Tổng công ty      | Có                           |
| Phòng Kinh doanh PC Đà Nẵng      | Có                           |
| Công ty CSKH liên quan nghiệp vụ | Có                           |
| Phòng Kỹ thuật PC khác           | Không, trừ khi được cấp thêm |
| Phòng Nhân sự                    | Không, nếu không liên quan   |

Đây là phân quyền rất quan trọng, vì EVNCPC có nhiều tuyến chuyên môn. Không nên chỉ phân theo “đơn vị”, vì Ban Tổng công ty cần nhìn được tài liệu chuyên môn của các đơn vị cấp dưới.

---

### Trường hợp 3: Tài liệu nội bộ một Công ty Điện lực

Ví dụ: kế hoạch sản xuất kinh doanh riêng của PC Đà Nẵng.

    scope = UNIT_ONLYowner_org = /EVNCPC/PC_DANANGallowed_org = /EVNCPC/PC_DANANG/*

Ai xem được: người trong PC Đà Nẵng.

Có thể cấp thêm cho Ban Tổng công ty liên quan:

    allow_extra_orgs =- /EVNCPC/BAN_KINH_DOANH- /EVNCPC/BAN_KY_THUAT

---

### Trường hợp 4: Tài liệu nội bộ phòng

Ví dụ: phân công nhân sự nội bộ Phòng Kỹ thuật PC Đà Nẵng.

    scope = DEPARTMENT_ONLYowner_org = /EVNCPC/PC_DANANG/PHONG_KY_THUATallowed_org = /EVNCPC/PC_DANANG/PHONG_KY_THUAT/*

Ai xem được: người trong phòng, trưởng/phó phòng, lãnh đạo đơn vị nếu chính sách cho phép.

---

### Trường hợp 5: Tài liệu dự án liên đơn vị

Ví dụ: dự án triển khai RAG, EVN CSKH, chuyển đổi số, OCR, kho dữ liệu AI.

    scope = PROJECT_ONLYproject = CHUYEN_DOI_SO_2026allowed_groups =- project_cds_2026_member- project_cds_2026_manager

Ai xem được: thành viên dự án, dù họ đến từ Tổng công ty, CPCIT, CSKH hay các Công ty Điện lực.

---

### Trường hợp 6: Tài liệu nhạy cảm

Ví dụ: nhân sự, lương, kỷ luật, hợp đồng, tài chính, hồ sơ khách hàng, thông tin cá nhân.

    classification = CONFIDENTIALscope = EXPLICIT_ACLinherit_permission = falseallowed_users = [...]allowed_groups = [...]

Nguyên tắc: **không kế thừa tự động theo cấp tổ chức**.

Ví dụ, một người là trưởng phòng không có nghĩa là được xem toàn bộ hồ sơ nhân sự hoặc dữ liệu khách hàng nếu không được phân quyền rõ.

---

## 5\. Trong RAG, quyền phải gắn tới từng chunk, không chỉ từng file

Đây là điểm rất quan trọng.

Một file có thể có nhiều phần:

    Báo cáo kinh doanh tháng├── Phần 1: Tổng quan toàn EVNCPC├── Phần 2: Số liệu PC Đà Nẵng├── Phần 3: Số liệu PC Quảng Nam├── Phần 4: Danh sách khách hàng nợ tiền└── Phần 5: Kiến nghị xử lý nội bộ

Không nên gắn một quyền duy nhất cho cả file nếu trong file có phần nhạy cảm.

Nên gắn quyền ở cấp:

    Document → Section → Chunk → Table row nếu cần

Ví dụ:

| Chunk   | Nội dung              | Quyền                        |
| ------- | --------------------- | ---------------------------- |
| Chunk 1 | Tổng quan toàn EVNCPC | Ban lãnh đạo, ban chuyên môn |
| Chunk 2 | Số liệu PC Đà Nẵng    | PC Đà Nẵng + Ban liên quan   |
| Chunk 3 | Danh sách khách hàng  | Nhóm được cấp quyền rõ       |
| Chunk 4 | Kiến nghị xử lý       | Lãnh đạo + phòng chuyên môn  |

---

## 6\. Luồng kiểm tra quyền trong hệ thống RAG

Không được để LLM tự quyết định quyền. LLM chỉ trả lời sau khi hệ thống đã lọc tài liệu.

Luồng nên là:

    1. User đăng nhập SSO/AD/LDAP2. Hệ thống lấy thuộc tính người dùng3. User đặt câu hỏi4. Query router xác định lĩnh vực câu hỏi5. Retrieval filter lọc tài liệu theo quyền6. Vector search chỉ chạy trong phạm vi được phép7. Rerank các chunk hợp lệ8. LLM chỉ nhận chunk hợp lệ9. Câu trả lời có trích nguồn10. Ghi audit log

Theo kiến trúc Zero Trust của NIST, truy cập nên đi qua thành phần ra quyết định chính sách và thành phần thực thi chính sách, thường gọi là PDP và PEP. Với RAG, có thể hiểu:

| Thành phần                     | Trong hệ thống RAG                            |
| ------------------------------ | --------------------------------------------- |
| PDP - Policy Decision Point    | Service kiểm tra quyền                        |
| PEP - Policy Enforcement Point | API/retriever bắt buộc lọc quyền              |
| PIP - Policy Information Point | HRM/AD/LDAP/cơ cấu tổ chức/project membership |
| Resource                       | Document, chunk, bảng, file nguồn             |
| Subject                        | Người dùng đang hỏi                           |
| Action                         | search, read, summarize, download, cite       |

---

## 7\. Cấu trúc bảng dữ liệu nên có

### Bảng tổ chức

    org_units- id- code- name- type -- corporation, board, power_company, support_company, department- parent_id- path -- /EVNCPC/PC_DANANG/PHONG_KINH_DOANH- business_domain

### Bảng người dùng

    users- id- employee_code- full_name- email- org_unit_id- position_title- position_level -- director, deputy_director, head, deputy_head, specialist- status

### Bảng nhóm quyền

    groups- id- code- name- type -- role, project, functional, system

### Bảng thành viên nhóm

    user_group_memberships- user_id- group_id- valid_from- valid_to

### Bảng tài liệu

    documents- id- title- owner_org_id- document_type- business_domain- scope- classification- status- effective_date- expired_date- created_by

### Bảng chunk

    document_chunks- id- document_id- chunk_text- section_title- page_number- business_domain- classification- scope- acl_policy_id- embedding_vector

### Bảng chính sách quyền

    access_policies- id- policy_name- effect -- allow/deny- subject_condition- resource_condition- action- priority

Ví dụ một policy:

    {  "policy_name": "Ban Kinh doanh TCT xem tài liệu kinh doanh tuyến dọc",  "effect": "allow",  "subject_condition": {    "org_path": "/EVNCPC/BAN_KINH_DOANH/*"  },  "resource_condition": {    "business_domain": "kinh_doanh",    "scope": ["FUNCTIONAL_VERTICAL", "CORP_WIDE"]  },  "actions": ["search", "read", "summarize"]}

---

## 8\. Quy tắc quan trọng khi truy hồi vector

Khi query vào vector database, phải filter trước:

    where = {    "classification": {"$in": user.allowed_classifications},    "allowed_org_paths": {"$contains_any": user.org_paths},    "business_domain": {"$contains_any": user.business_domains},    "deny_groups": {"$not_contains_any": user.groups}}

Sai lầm thường gặp là:

    Vector search toàn bộ kho → lấy top 20 → sau đó mới lọc quyền

Cách này có thể làm lộ thông tin qua metadata, tiêu đề, snippet hoặc ranking.

Nên làm:

    Lọc quyền trước hoặc trong lúc search → vector search chỉ trên tài liệu được phép → rerank → LLM

OWASP cũng cảnh báo các ứng dụng LLM có rủi ro prompt injection dẫn tới truy cập trái phép, rò rỉ dữ liệu hoặc quyết định sai lệch, nên không được dựa vào lời nhắc kiểu “không được tiết lộ tài liệu mật” như lớp bảo vệ chính.

---

## 9\. Gợi ý ma trận quyền thực tế cho EVNCPC

| Người dùng                   | Tài liệu toàn EVNCPC | Tài liệu đơn vị mình | Tài liệu phòng mình | Tài liệu tuyến dọc       | Tài liệu mật    |
| ---------------------------- | -------------------- | -------------------- | ------------------- | ------------------------ | --------------- |
| Lãnh đạo Tổng công ty        | Có                   | Có chọn lọc          | Có chọn lọc         | Có                       | Theo phân quyền |
| Trưởng/phó ban Tổng công ty  | Có                   | Có nếu liên quan     | Có nếu liên quan    | Có                       | Theo phân quyền |
| Chuyên viên ban Tổng công ty | Có                   | Có nếu được giao     | Có nếu liên quan    | Có trong mảng            | Theo phân quyền |
| Giám đốc/phó giám đốc PC     | Có                   | Có                   | Có                  | Có nếu liên quan         | Theo phân quyền |
| Trưởng/phó phòng PC          | Có                   | Có trong phạm vi     | Có                  | Có trong mảng            | Theo phân quyền |
| Chuyên viên PC               | Có                   | Có giới hạn          | Có                  | Có nếu được cấp          | Không mặc định  |
| Nhân sự CPCIT                | Có                   | Tài liệu CPCIT       | Theo phòng          | Có nếu vận hành hệ thống | Không mặc định  |
| Nhân sự CSKH                 | Có                   | Tài liệu CSKH        | Theo phòng          | Mảng CSKH/KD             | Không mặc định  |

---

## 10\. Nên tách quyền đọc, quyền hỏi, quyền tải file

Trong RAG, “được hỏi” và “được tải file gốc” không nên là một quyền.

Nên có các action riêng:

| Action          | Ý nghĩa                                |
| --------------- | -------------------------------------- |
| `search`        | Được tìm thấy tài liệu/chunk           |
| `read_answer`   | Được dùng nội dung để sinh câu trả lời |
| `view_citation` | Được thấy nguồn trích dẫn              |
| `open_document` | Được mở file gốc                       |
| `download`      | Được tải file                          |
| `ingest`        | Được nạp tài liệu                      |
| `approve`       | Được duyệt tài liệu vào kho RAG        |
| `manage_acl`    | Được sửa quyền tài liệu                |
| `delete`        | Được xóa tài liệu khỏi kho             |

Ví dụ: chuyên viên có thể được hỏi đáp từ quy trình, nhưng không được tải bản hợp đồng gốc.

---

## 11\. Kiến trúc khuyến nghị

    [SSO/AD/LDAP/HRM]        ↓[User Profile Service]        ↓[Policy Engine: RBAC + ABAC + ACL]        ↓[RAG API]        ↓[Permission-aware Retriever]        ↓[Vector DB / Graph DB / Keyword Index]        ↓[Reranker]        ↓[LLM]        ↓[Answer + Citation + Audit Log]

NIST CSF 2.0 cũng đặt quản lý định danh, xác thực và kiểm soát truy cập trong nhóm chức năng bảo vệ, cùng với bảo mật dữ liệu và bảo vệ nền tảng.

---

## 12\. Kết luận triển khai cho hệ thống của anh

Với EVNCPC, tôi khuyến nghị mô hình sau:

    Phân quyền người dùng:- Theo cây tổ chức- Theo chức vụ- Theo phòng/ban- Theo mảng nghiệp vụ- Theo dự án/tổ công tác- Theo mức truy cập dữ liệuPhân quyền tài liệu:- Theo đơn vị sở hữu- Theo phạm vi sử dụng- Theo mảng nghiệp vụ- Theo độ mật- Theo trạng thái tài liệu- Theo ACL cụ thể nếu nhạy cảmPhân quyền RAG:- Lọc quyền trước khi retrieval- Gắn ACL tới từng chunk- Không đưa chunk không có quyền vào prompt- Không để LLM tự quyết định quyền- Ghi log toàn bộ câu hỏi, tài liệu truy hồi, người hỏi, thời điểm

Công thức ngắn gọn nên áp dụng:

    User được xem Chunk khi:User.status = activeAND User.role phù hợpAND User.org_path nằm trong phạm vi cho phépAND User.business_domain khớp với tài liệuAND User.clearance >= Document.classificationAND User không nằm trong deny listAND action được phép

Thiết kế tốt nhất cho nội bộ EVNCPC là:

> **Một kho RAG thống nhất, nhưng mỗi document/chunk đều có metadata phân quyền; retriever bắt buộc lọc theo quyền trước khi đưa dữ liệu cho LLM. Với tài liệu cực nhạy cảm thì tách collection/kho riêng.**
