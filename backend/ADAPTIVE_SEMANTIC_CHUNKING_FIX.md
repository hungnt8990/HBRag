# Adaptive semantic chunking fix

## Mục tiêu

Không áp dụng một chiến lược chunk duy nhất cho toàn bộ tài liệu. Docling vẫn làm nhiệm vụ parse và nhận diện cấu trúc; pipeline lựa chọn chiến lược theo từng segment.

## Thay đổi

- Giữ nguyên pipeline slide cho tài liệu trình chiếu.
- Giữ nguyên schema/table chunking cho bảng kỹ thuật, GIS và spreadsheet.
- Chỉ kích hoạt semantic administrative chunking khi document profile là `administrative*`.
- Phần thân công văn có danh sách i), ii), iii) được tái tạo thành:
  - `administrative_introduction`
  - `administrative_issue_overview`
  - `administrative_directive`
- Chỉ bảng có các cột nghiệp vụ `Hệ thống`, `Tình trạng`, `Nguyên nhân`, `Yêu cầu thực hiện` mới được chuyển từng hàng thành `administrative_incident` dạng key-value.
- Các loại bảng khác không bị chuyển đổi.
- Xóa ordinal nội bộ giả như `2. ii)`, `4. tơ`, `7. iii)` khỏi nội dung embedding.
- Ghép lại line-wrap bị cắt giữa câu trước khi chia semantic section.
- Reset `section_path` cho từng incident: `Phụ lục > Hệ thống > Tình trạng`.
- Lưu song song `raw_text`, `fields`, `field_names`, `responsible_units`, `incident_type`.
- Loại bỏ chunk cầu nối rất nhỏ chỉ chứa chữ ký hoặc `PHỤ LỤC`.

## Kiểm thử đã bổ sung

- Bảng sự cố hành chính được tách thành từng semantic incident.
- Bảng schema kỹ thuật không bị áp dụng chiến lược hành chính.
- Phần thân công văn được chia theo ý nghĩa, không theo fragment Docling.
- Regression cho lỗi cắt `công` / `tơ` và ordinal giả.

## Xác minh trong môi trường hiện tại

- `python -m compileall` thành công.
- Không chạy được full `pytest`/`ruff` trong container hiện tại vì thiếu dependency dự án (`sqlalchemy`, `docling`, executable `ruff`).
