# Docling slide pipeline refactor

## Thay đổi kiến trúc

- Xóa `app/services/chunking_router.py`.
- Xóa `app/services/segment_router.py`.
- Xóa `app/services/slide_aware_chunking.py`.
- Xóa các test router tương ứng.
- Xóa cấu hình `chunk_router_provider`.
- `ChunkingService` chỉ còn hai đường chạy:
  1. Docling-first khi có `DoclingDocument` artifact.
  2. Recursive fallback khi tài liệu không được Docling parse.

## Cải tiến PDF dạng slide

- Nhận diện tài liệu presentation-like trực tiếp trong `docling_v6_chunking.py`.
- Giữ ranh giới từng trang/slide, không merge xuyên slide.
- Dùng `pdfplumber` trích text theo từng trang làm lớp fallback có coverage cao hơn.
- Tạo chunk `presentation_slide` với metadata `slide_number`, `slide_title`, `document_profile`.
- Loại placeholder `<!-- image -->` trước embedding.
- Slide chỉ có tiêu đề/DEMO được đánh `indexable=false`, `embedding_enabled=false`.
- Sửa lỗi khoảng trắng glyph tiếng Việt thường gặp trong PDF slide.
- Trang vượt token limit chỉ split nội bộ, không gộp với trang kế tiếp.

## File thay đổi chính

- `app/services/chunking_service.py`
- `app/services/docling_v6_chunking.py`
- `app/services/parsers/docling_parser.py`
- `app/services/hybrid_search.py`
- `app/core/config.py`
- `tests/test_docling_v6_repairs.py`

## Kiểm tra đã thực hiện

- `python -m compileall -q app tests`: đạt.
- Kiểm tra độc lập hàm sửa glyph tiếng Việt: đạt với các mẫu của tài liệu GIS Hạ thế.
- Không chạy được `ruff` và toàn bộ `pytest` trong container do môi trường không có lệnh `ruff` và package Docling đầy đủ; test module đã có cơ chế stub Docling.
