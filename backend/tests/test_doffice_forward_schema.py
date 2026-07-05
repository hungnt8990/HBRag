"""Test lớp ánh xạ schema chuẩn BA "đi trước 1 bước" (ingestion_doffice_forward_schema)."""

from __future__ import annotations

from app.services.ingestion.ingestion_doffice_forward_schema import (
    SOURCE_SYSTEM_DOFFICE,
    build_forward_document_fields,
)


def test_empty_source_chi_co_source_system():
    """Source rỗng -> chỉ có hằng source_system (field rỗng KHÔNG ghi -> payload gọn)."""
    out = build_forward_document_fields({})
    assert out == {"source_system": SOURCE_SYSTEM_DOFFICE}


def test_map_ten_cu_sang_ten_chuan_ba():
    """Nguồn hôm nay (ky_hieu/ngay_vb) -> tên chuẩn BA (document_no/issue_date)."""
    out = build_forward_document_fields(
        {"ky_hieu": "258/QĐ-IT", "ngay_vb": "2024-12-03", "trich_yeu": "An toàn điện"}
    )
    assert out["document_no"] == "258/QĐ-IT"
    assert out["issue_date"] == "2024-12-03"
    assert out["source_system"] == "DOFFICE"


def test_alias_ten_moi_uu_tien_hon_ten_cu():
    """Khi API tương lai trả cả tên mới lẫn cũ -> ưu tiên tên mới (đi trước 1 bước)."""
    out = build_forward_document_fields(
        {"document_no": "999/MOI", "ky_hieu": "258/QĐ-IT", "issue_date": "2025-01-01", "ngay_vb": "x"}
    )
    assert out["document_no"] == "999/MOI"
    assert out["issue_date"] == "2025-01-01"


def test_doc_type_va_doc_category_suy_luan_khi_thieu():
    """doc_type suy từ ky_hieu, doc_category suy từ trich_yeu khi nguồn chưa trả tường minh."""
    out = build_forward_document_fields(
        {"ky_hieu": "258/QĐ-IT", "trich_yeu": "Quy định vận hành lưới điện tại trạm biến áp"}
    )
    assert out["doc_type"] == "Quyết định"
    assert out["doc_category"] == "Vận hành lưới điện"


def test_doc_type_ton_trong_gia_tri_tuong_minh():
    """Nếu nguồn đã trả doc_type/loai_vb -> dùng luôn, không suy lại."""
    out = build_forward_document_fields({"loai_vb": "Báo cáo", "ky_hieu": "258/QĐ-IT"})
    assert out["doc_type"] == "Báo cáo"


def test_field_tuong_lai_xuat_hien_khi_api_tra_du_lieu():
    """priority/owner_department_id chưa có hôm nay -> tự xuất hiện khi API bắt đầu trả."""
    empty = build_forward_document_fields({"ky_hieu": "1/CV"})
    assert "priority" not in empty
    assert "owner_department_id" not in empty

    filled = build_forward_document_fields(
        {"ky_hieu": "1/CV", "do_khan": "HOA_TOC", "id_pb_soan_thao": "1045"}
    )
    assert filled["priority"] == "HOA_TOC"
    assert filled["owner_department_id"] == "1045"


def test_danh_sach_vb_can_cu_lien_quan_chuan_hoa():
    """reference/related nhận cả id trần lẫn dạng {id, relation}; bỏ rỗng, giữ thứ tự, khử trùng."""
    out = build_forward_document_fields(
        {
            "ky_hieu": "1/CV",
            "vb_can_cu": ["705311", {"id": "705311", "relation": "THAY_THE"}, ""],
            "related_document_ids": [{"id": "719712", "relation": "BI_THAY_THE"}],
        }
    )
    assert out["reference_document_ids"] == ["705311"]
    assert out["related_document_ids"] == ["719712"]
