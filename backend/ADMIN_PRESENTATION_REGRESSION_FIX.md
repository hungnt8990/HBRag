# Admin / Presentation Regression Fix

## Thay đổi

- Chỉ dùng native page 1 làm document context khi tài liệu được nhận diện là presentation-like.
- Văn bản hành chính và tài liệu schema tiếp tục dùng preamble lấy từ DoclingDocument.
- Loại bỏ việc tạo `document_preamble_missing_from_body` song song trước HybridChunker.
  Điều này tránh duplicate, chunk bị cụt và section-state leakage.
- Giữ native page reconstruction riêng trong nhánh presentation.
- Sửa Vietnamese glyph repair để không nối hai từ đều đã có dấu, ví dụ:
  - `đã có ở` không thành `đã cóở`
  - `chuyển đổi ở` không thành `chuyển đổiở`
- Thêm regression test cho ranh giới từ trước từ có dấu.

## Kiểm tra đã thực hiện

- `python -m compileall -q app tests`: đạt.
- Không còn dòng dài quá 100 ký tự trong hai file sửa.
- Không chạy được pytest đầy đủ trong container vì thiếu SQLAlchemy.
