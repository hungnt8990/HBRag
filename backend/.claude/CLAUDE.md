<!-- CODEGRAPH_START -->
## CodeGraph

In repositories indexed by CodeGraph (a `.codegraph/` directory exists at the repo root), reach for it BEFORE grep/find or reading files when you need to understand or locate code:

- **MCP tools** (when available): `codegraph_explore` answers most code questions in one call — the relevant symbols' verbatim source plus the call paths between them. `codegraph_node` returns one symbol's source + callers, or reads a whole file with line numbers. If the tools are listed but deferred, load them by name via tool search.
- **Shell** (always works): `codegraph explore "<symbol names or question>"` and `codegraph node <symbol-or-file>` print the same output.

If there is no `.codegraph/` directory, skip CodeGraph entirely — indexing is the user's decision.
<!-- CODEGRAPH_END -->

## Tổng quan dự án (BẮT BUỘC đọc & cập nhật)

- **Trước khi làm việc:** đọc `docs/PROJECT_OVERVIEW.md` để nắm kiến trúc, hệ phân quyền, các điểm còn để ngỏ và cảnh báo (vd. alembic divergence).
- **Sau khi hoàn thành mỗi thay đổi đáng kể** (thêm/sửa module, model, migration, luồng ingest/retrieval/phân quyền): **cập nhật lại `docs/PROJECT_OVERVIEW.md`** cho khớp thực tế — sửa phần liên quan, đổi dòng "Cập nhật gần nhất" ở đầu file, và cập nhật mục TODO. Coi đây là một phần của định nghĩa "đã xong", không phải bước tùy chọn.
- Giữ tài liệu súc tích, đúng sự thật (chỉ ghi điều đã kiểm chứng trong code), không phình to.
