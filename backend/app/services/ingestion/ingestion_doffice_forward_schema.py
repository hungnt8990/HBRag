"""Lớp ánh xạ tên trường sang schema chuẩn BA (nhóm 07) — "đi trước 1 bước".

Nguồn ``doffice_vanban`` HÔM NAY trả field tên cũ (``id_vb``, ``ky_hieu``, ``ngay_vb``...). Kế hoạch
BA (``Kế hoạch nhóm 07.BA.xlsx`` – sheet ``Metadata_Standard``) chuẩn hoá sang tên mới
(``document_id``/``document_no``/``issue_date``...) và bổ sung nhiều field mà API CHƯA trả
(``priority``, ``reference_document_ids``, ``related_document_ids``, ``owner_department_id``,
``doc_group``, ``doc_type``, ``doc_category``, ``source_system``).

Module này là NƠI DUY NHẤT khai báo ánh xạ: mỗi tên chuẩn -> danh sách alias nguồn (tên hôm nay
+ tên API dự kiến đổi trong tương lai). Khi API đổi tên field, CHỈ cần thêm alias vào đây — pipeline
ingest và các hàm dựng payload (Qdrant chunk Col1, docmeta Col2, ES chunk) KHÔNG phải sửa.

Nguyên tắc:
- Field rỗng KHÔNG ghi vào payload (giữ Qdrant gọn, đồng nhất ``exclude_none`` hiện hành). Khi API
  bắt đầu trả dữ liệu cho field đó, nó TỰ xuất hiện trong payload mà không cần sửa code -> "đi trước".
- ``doc_type``/``doc_category`` suy luận rẻ, tất định từ ``ky_hieu``/``trich_yeu`` (dùng lại
  ``ingestion_doffice_business_fields``) khi nguồn chưa có field tường minh.
- ``source_system`` = hằng ``"DOFFICE"`` cho toàn bộ nguồn này (BA field #5).

⚠️ KHÔNG đụng tới ``document_id``/``id``/``id_vb`` ở đây: việc rename khoá (``document_id`` nội bộ
-> ``id``, ``id_vb`` -> ``document_id`` theo BA) là thay đổi BREAKING (phải sửa retrieval + re-embed),
để pipeline/quyết định riêng. Module này chỉ THÊM field mới thuần bổ sung.
"""

from __future__ import annotations

from typing import Any

from app.services.ingestion.ingestion_doffice_business_fields import (
    derive_linh_vuc,
    derive_loai_vb,
)

# Hằng: hệ thống nguồn của toàn bộ văn bản đi qua pipeline này (BA field #5 source_system).
SOURCE_SYSTEM_DOFFICE = "DOFFICE"

# Ánh xạ tên-chuẩn-BA -> danh sách alias nguồn, ưu tiên theo thứ tự (giá trị non-empty ĐẦU TIÊN
# thắng). Đặt tên MỚI (API dự kiến) trước tên cũ để khi API đổi thì tự ưu tiên tên mới.
_SCALAR_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    # BA #3 — số/ký hiệu văn bản. Hôm nay nguồn trả ``ky_hieu``.
    "document_no": ("document_no", "so_cong_van", "ky_hieu"),
    # BA #18 — ngày ban hành. Hôm nay nguồn trả ``ngay_vb`` (giữ nguyên định dạng nguồn; việc
    # convert serial Excel -> ISO để bước ETL sau, tránh suy diễn sai ở đây).
    "issue_date": ("issue_date", "ngay_cong_van", "ngay_vb"),
    # BA #30 — độ khẩn. Nguồn DO hiện thiếu -> thường rỗng (bỏ). Để sẵn alias cho khi API bổ sung.
    "priority": ("priority", "do_khan", "muc_do_khan", "ma_do_khan"),
    # BA #11 — loại công văn (cv đến/đi/nội bộ). Nguồn hiện thiếu tường minh.
    "doc_group": ("doc_group", "doi_tuong_cong_van"),
    # BA #22 — phòng ban chủ trì. Nguồn DO: ``id_pb_soan_thao`` (nếu API trả).
    "owner_department_id": ("owner_department_id", "id_pb_soan_thao", "phong_ban_trinh_id"),
}

# Field dạng DANH SÁCH id (BA #28/#29 — VB liên quan / VB căn cứ). Nguồn hiện thiếu -> [] (bỏ).
_LIST_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "reference_document_ids": ("reference_document_ids", "vb_can_cu", "can_cu"),
    "related_document_ids": ("related_document_ids", "vb_lien_quan", "lien_quan"),
}


def _first_present(source: dict[str, Any], aliases: tuple[str, ...]) -> Any:
    """Giá trị non-empty đầu tiên theo thứ tự alias; None nếu không có."""
    for alias in aliases:
        value = source.get(alias)
        if value not in (None, "", [], {}):
            return value
    return None


def _as_id_list(value: Any) -> list[str]:
    """Chuẩn hoá về list[str] id (bỏ rỗng, giữ thứ tự)."""
    if value in (None, "", [], {}):
        return []
    items = value if isinstance(value, (list, tuple, set)) else [value]
    out: list[str] = []
    for item in items:
        # Hỗ trợ cả dạng nâng cao {"id": ..., "relation": ...} lẫn id trần.
        raw = item.get("id") if isinstance(item, dict) else item
        text = str(raw or "").strip()
        if text and text not in out:
            out.append(text)
    return out


def build_forward_document_fields(source: dict[str, Any]) -> dict[str, Any]:
    """Trả dict field cấp VĂN BẢN theo tên chuẩn BA, CHỈ gồm field có giá trị (bỏ rỗng).

    Dùng chung cho payload Qdrant chunk (Col1), docmeta (Col2) và ES chunk. An toàn để merge:
    không chứa khoá định danh (``id``/``document_id``/``id_vb``) — những khoá đó do pipeline quản.
    """
    out: dict[str, Any] = {"source_system": SOURCE_SYSTEM_DOFFICE}

    for standard_name, aliases in _SCALAR_FIELD_ALIASES.items():
        value = _first_present(source, aliases)
        if value not in (None, "", [], {}):
            out[standard_name] = value

    for standard_name, aliases in _LIST_FIELD_ALIASES.items():
        ids = _as_id_list(_first_present(source, aliases))
        if ids:
            out[standard_name] = ids

    # BA #12 doc_type — loại văn bản. Nếu nguồn chưa trả tường minh -> suy từ ky_hieu (tất định).
    doc_type = _first_present(source, ("doc_type", "loai_vb")) or derive_loai_vb(
        source.get("ky_hieu")
    )
    if doc_type:
        out["doc_type"] = doc_type

    # BA #13 doc_category — nhóm nghiệp vụ. Nếu nguồn chưa trả -> suy từ trich_yeu/tom_tat.
    doc_category = _first_present(source, ("doc_category", "linh_vuc", "ma_dk")) or derive_linh_vuc(
        source.get("trich_yeu"), source.get("tom_tat")
    )
    if doc_category:
        out["doc_category"] = doc_category

    return out
