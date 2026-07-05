"""Suy luận field nghiệp vụ (``loai_vb``, ``linh_vuc``) cho văn bản DOffice.

Nguồn ``doffice_vanban`` KHÔNG trả sẵn 2 field này -> suy luận bằng LUẬT (rule) rẻ, tất định:
- ``loai_vb``  : rút viết tắt loại VB từ ``ky_hieu`` (vd ``258/QĐ-IT`` -> "Quyết định").
- ``linh_vuc`` : phân loại lĩnh vực ngành điện theo từ khoá trong ``trich_yeu`` + ``tom_tat``.

Cả 2 lưu vào payload FILTER (keyword) của Qdrant Col1+Col2 + ES (index đã khai báo sẵn ở
``vector_store.PAYLOAD_KEYWORD_FIELDS``) -> facet/thu hẹp truy hồi cho chatbot/tìm kiếm.
Không suy được -> để trống (KHÔNG gán "Khác" bừa để tránh nhiễu filter).
"""

from __future__ import annotations

import re
from typing import Any

# --- loai_vb: viết tắt (sau dấu "/" đầu, trước "-") -> tên đầy đủ --------------
# Khớp KHÔNG dấu-hoa-thường; giữ đúng chính tả tiếng Việt ở giá trị đầu ra.
_LOAI_VB_MAP: dict[str, str] = {
    "QĐ": "Quyết định",
    "TB": "Thông báo",
    "CV": "Công văn",
    "QC": "Quy chế",
    "QĐ-QC": "Quy chế",
    "QT": "Quy trình",
    "HD": "Hướng dẫn",
    "HĐ": "Hợp đồng",
    "TTr": "Tờ trình",
    "NQ": "Nghị quyết",
    "KH": "Kế hoạch",
    "BC": "Báo cáo",
    "CT": "Chỉ thị",
    "GM": "Giấy mời",
    "BB": "Biên bản",
    "PA": "Phương án",
    "ĐA": "Đề án",
    "QĐ-ĐL": "Quyết định",
    "NĐ": "Nghị định",
    "TT": "Thông tư",
}
# Token đầu tiên sau dấu "/" (loại VB thường nằm ở đây). Vd "258/QĐ-IT" -> "QĐ".
_KY_HIEU_TYPE_RE = re.compile(r"/\s*([A-Za-zĐđ.]{1,6})")


def derive_loai_vb(ky_hieu: str | None) -> str | None:
    """Rút loại văn bản từ ``ky_hieu``. None nếu không nhận ra viết tắt hợp lệ.

    KHÔNG mặc định "Công văn" khi không rõ (nhiều ký hiệu không mang mã loại) -> tránh gán sai."""
    if not ky_hieu:
        return None
    match = _KY_HIEU_TYPE_RE.search(str(ky_hieu))
    if not match:
        return None
    token = match.group(1).strip(".").upper()
    return _LOAI_VB_MAP.get(token)


# --- linh_vuc: từ khoá -> lĩnh vực ngành điện (ưu tiên theo thứ tự khai báo) ---
# Danh sách (lĩnh vực, [từ khoá không dấu-hoa-thường]). Khớp cụm đầu tiên -> gán.
_LINH_VUC_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("An toàn điện", ("an toàn điện", "an toàn lao động", "bảo hộ lao động", "pccc",
                       "phòng cháy", "hành lang an toàn", "atlđ", "an toàn vệ sinh")),
    ("Vận hành lưới điện", ("vận hành", "lưới điện", "trạm biến áp", "đường dây", "điều độ",
                            "sự cố", "mất điện", "đóng điện", "cắt điện", "rơ le", "scada",
                            "quá tải", "sửa chữa lưới")),
    ("Kinh doanh điện năng", ("kinh doanh điện", "điện năng", "khách hàng", "hoá đơn", "hóa đơn",
                              "công tơ", "ghi chỉ số", "giá điện", "cskh", "chăm sóc khách hàng",
                              "hợp đồng mua bán điện", "tiền điện", "thu tiền")),
    ("Đầu tư XDCB", ("đầu tư", "xây dựng cơ bản", "xdcb", "dự án", "công trình", "đấu thầu",
                     "khlcnt", "lựa chọn nhà thầu", "quyết toán công trình", "thi công")),
    ("Tổ chức - Nhân sự", ("nhân sự", "cán bộ", "bổ nhiệm", "điều động", "tuyển dụng", "đào tạo",
                           "tiền lương", "thi đua", "khen thưởng", "kỷ luật", "tổ chức bộ máy",
                           "biên chế", "nghỉ hưu", "bồi dưỡng")),
    ("Tài chính - Kế toán", ("tài chính", "kế toán", "kinh phí", "quyết toán", "ngân sách",
                             "chi phí", "thanh toán", "dự toán", "vốn", "thuế")),
    ("CNTT - Viễn thông", ("công nghệ thông tin", "cntt", "phần mềm", "viễn thông", "hạ tầng số",
                           "chuyển đổi số", "ứng dụng", "hệ thống thông tin", "cơ sở dữ liệu",
                           "an toàn thông tin", "website", "máy chủ")),
    ("Pháp chế - Thanh tra", ("pháp chế", "thanh tra", "kiểm tra", "văn bản quy phạm",
                              "khiếu nại", "tố cáo", "phòng chống tham nhũng")),
]


def derive_linh_vuc(*texts: str | None) -> str | None:
    """Phân loại lĩnh vực từ ``trich_yeu``/``tom_tat``. None nếu không khớp luật nào."""
    blob = " ".join(t for t in texts if t).lower()
    if not blob.strip():
        return None
    for linh_vuc, keywords in _LINH_VUC_RULES:
        if any(kw in blob for kw in keywords):
            return linh_vuc
    return None


def derive_business_fields(source: dict[str, Any]) -> dict[str, str]:
    """Trả dict chỉ gồm field suy được (bỏ None) để merge vào source/metadata."""
    out: dict[str, str] = {}
    loai_vb = derive_loai_vb(source.get("ky_hieu"))
    if loai_vb:
        out["loai_vb"] = loai_vb
    linh_vuc = derive_linh_vuc(source.get("trich_yeu"), source.get("tom_tat"))
    if linh_vuc:
        out["linh_vuc"] = linh_vuc
    return out
