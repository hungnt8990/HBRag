"""Test suy luận loai_vb / linh_vuc cho văn bản DOffice."""

from app.services.ingestion.ingestion_doffice_business_fields import (
    derive_business_fields,
    derive_linh_vuc,
    derive_loai_vb,
)


def test_loai_vb_from_ky_hieu() -> None:
    assert derive_loai_vb("258/QĐ-IT") == "Quyết định"
    assert derive_loai_vb("12/TB-EVNCPC") == "Thông báo"
    assert derive_loai_vb("45/QC-EVNCPC+TCNS") == "Quy chế"
    # Ký hiệu KHÔNG mang mã loại (token là tên đơn vị) -> None (không đoán bừa).
    assert derive_loai_vb("6515/EVNCPC-VTCNTT+KD+KT") is None
    assert derive_loai_vb("907/EVNICT-TTPM") is None
    assert derive_loai_vb(None) is None


def test_linh_vuc_keyword() -> None:
    assert derive_linh_vuc("Về việc đảm bảo an toàn điện mùa mưa bão") == "An toàn điện"
    assert derive_linh_vuc("Kế hoạch sửa chữa lưới điện trạm biến áp") == "Vận hành lưới điện"
    assert derive_linh_vuc("Quy định ghi chỉ số công tơ và giá điện") == "Kinh doanh điện năng"
    assert derive_linh_vuc("Bổ nhiệm cán bộ và đào tạo nhân sự") == "Tổ chức - Nhân sự"
    assert derive_linh_vuc("Cập nhật phần mềm hệ thống thông tin") == "CNTT - Viễn thông"
    assert derive_linh_vuc("") is None
    assert derive_linh_vuc("nội dung chung chung không rõ") is None


def test_derive_business_fields_merges_only_present() -> None:
    out = derive_business_fields(
        {"ky_hieu": "258/QĐ-IT", "trich_yeu": "Quy trình phát triển phần mềm"}
    )
    assert out == {"loai_vb": "Quyết định", "linh_vuc": "CNTT - Viễn thông"}
    # Không suy được gì -> dict rỗng (không chèn key None).
    assert derive_business_fields({"ky_hieu": "6515/EVNCPC-KD", "trich_yeu": "abc xyz"}) == {}
