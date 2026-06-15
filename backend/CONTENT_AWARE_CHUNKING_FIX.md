# Content-aware Docling chunking fix

## Mục tiêu

Giữ kiến trúc Docling-first, không khôi phục `ChunkingRouter`, nhưng bổ sung nhận diện profile tài liệu và lựa chọn cách xử lý theo từng segment.

## Thay đổi chính

- Thêm `detect_document_profile()` với các profile:
  - `presentation`
  - `administrative`
  - `administrative_with_tables`
  - `technical_schema`
  - `mixed_with_tables`
  - `general`
- Thêm `classify_segment_strategy()` để ghi nhận chiến lược theo segment:
  - `presentation_page`
  - `administrative_section`
  - `cross_page_table`
  - `schema_table`
  - `table_row_group`
  - `docling_hybrid`
- Thêm `repair_cross_page_table_continuations()`:
  - nhận diện bảng tiếp tục qua trang bằng cùng header;
  - kế thừa các giá trị bị ẩn do merged cell ở các cột như `STT`, `Hệ thống`, `Đơn vị`, `Đối tượng`, `Nhóm`;
  - giữ chung section và table continuation group;
  - đánh dấu `cross_page_table_continuation=true`.
- `object_group_key()` ưu tiên continuation group để các phần bảng liên trang được repack cùng nhau.
- Quality gate không còn kiểm tra câu prose bị cụt trên chunk bảng.
- `avoidable_single_row_table_chunk` được hạ thành warning khi đó là bảng liên trang đã được repair.
- Mỗi record bổ sung:
  - `detected_document_profile`
  - `segment_chunk_strategy`
- Thêm regression tests cho:
  - bảng Web/app CSKH tiếp tục từ trang 3 sang trang 4;
  - carry-forward `STT=3`, `Hệ thống=Web/app CSKH`;
  - nhận diện `administrative_with_tables`.

## Kiểm tra đã chạy

- `python -m compileall -q app tests`: đạt.
- Chạy trực tiếp ba regression tests mới: đạt.
- Chưa chạy toàn bộ pytest trong container vì thiếu `sqlalchemy`.
