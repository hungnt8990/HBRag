"""Test bộ nén ACL theo danh mục tổ chức (app.services.security.security_acl_compressor)."""

from __future__ import annotations

import pytest

from app.services.security.security_acl_compressor import (
    AclCompressionError,
    CompressedAcl,
    OrgCatalog,
    compress_allow,
)


def _catalog() -> OrgCatalog:
    """Danh mục mẫu:

    Đơn vị 1: phòng 10 (u1..u5), phòng 11 (u6..u10)
    Đơn vị 2: phòng 20 (u20..u24), phòng 21 (u30)
    """
    employees: list[tuple[int, int, int]] = []
    for u in range(1, 6):
        employees.append((u, 1, 10))
    for u in range(6, 11):
        employees.append((u, 1, 11))
    for u in range(20, 25):
        employees.append((u, 2, 20))
    employees.append((30, 2, 21))
    return OrgCatalog.from_employees(employees, version="test")


def _roundtrip(acl: CompressedAcl, catalog: OrgCatalog, expected: set[int]) -> None:
    assert acl.decompress(catalog) == expected


def test_ca_phong_gop_thanh_id_phong():
    catalog = _catalog()
    allowed = {1, 2, 3, 4, 5}  # cả phòng 10
    acl = compress_allow(allowed, catalog)
    assert acl.allow_department_ids == [10]
    assert acl.allow_user_ids == []
    assert acl.deny_user_ids == []
    _roundtrip(acl, catalog, allowed)


def test_9_tren_10_gop_phong_va_deny_mot_nguoi():
    catalog = _catalog()
    allowed = set(range(1, 11)) - {7}  # 9/10 của đơn vị 1 (2 phòng), thiếu u7
    acl = compress_allow(allowed, catalog)
    # Đơn vị 1 chỉ có 2 phòng -> roll-up lên đơn vị rẻ hơn (1 + 1 deny = 2).
    assert acl.allow_unit_ids == [1]
    assert acl.deny_user_ids == [7]
    assert acl.allow_user_ids == []
    _roundtrip(acl, catalog, allowed)


def test_9_tren_10_trong_mot_phong_lon():
    # Một phòng 10 người, 9 được xem -> lưu phòng + deny 1.
    employees = [(u, 5, 50) for u in range(100, 110)]
    catalog = OrgCatalog.from_employees(employees)
    allowed = set(range(100, 110)) - {105}
    acl = compress_allow(allowed, catalog)
    assert acl.allow_department_ids == [50] or acl.allow_unit_ids == [5]
    assert acl.deny_user_ids == [105]
    _roundtrip(acl, catalog, allowed)


def test_ca_don_vi_gop_len_unit():
    catalog = _catalog()
    allowed = set(range(1, 11))  # toàn bộ đơn vị 1
    acl = compress_allow(allowed, catalog)
    assert acl.allow_unit_ids == [1]
    assert acl.allow_department_ids == []
    assert acl.deny_user_ids == []
    _roundtrip(acl, catalog, allowed)


def test_khong_over_group_khi_phong_le_chi_mot_nguoi():
    # Đơn vị 8: phòng 80 đầy đủ (5/5) + phòng 81 chỉ 1/5 người được xem.
    # Kỳ vọng: allow phòng 80 + allow cá nhân, KHÔNG gộp lên đơn vị
    # (gộp đơn vị phải deny 4 người phòng 81 -> đắt hơn liệt kê).
    employees = [(u, 8, 80) for u in range(800, 805)]
    employees += [(u, 8, 81) for u in range(810, 815)]
    catalog = OrgCatalog.from_employees(employees)
    allowed = set(range(800, 805)) | {810}
    acl = compress_allow(allowed, catalog)
    assert acl.allow_unit_ids == []
    assert acl.allow_department_ids == [80]
    assert acl.allow_user_ids == [810]
    _roundtrip(acl, catalog, allowed)


def test_unit_voi_phong_bi_loai_hoan_toan_dung_deny_department():
    # 3 phòng đầy đủ + 1 phòng không ai được xem -> allow_unit + deny phòng đó.
    employees: list[tuple[int, int, int]] = []
    for pb in (10, 11, 12):
        for u in range(pb * 10, pb * 10 + 5):
            employees.append((u, 7, pb))
    for u in range(130, 135):  # phòng 13, không ai được xem
        employees.append((u, 7, 13))
    catalog = OrgCatalog.from_employees(employees)
    allowed = {u for (u, _, pb) in employees if pb != 13}
    acl = compress_allow(allowed, catalog)
    assert acl.allow_unit_ids == [7]
    assert acl.deny_department_ids == [13]
    assert acl.deny_user_ids == []
    _roundtrip(acl, catalog, allowed)


def test_nguoi_le_rai_rac_giu_dang_ca_nhan():
    catalog = _catalog()
    allowed = {1, 6, 20}  # mỗi phòng một người
    acl = compress_allow(allowed, catalog)
    assert acl.allow_unit_ids == []
    assert acl.allow_department_ids == []
    assert acl.allow_user_ids == [1, 6, 20]
    _roundtrip(acl, catalog, allowed)


def test_user_ngoai_danh_muc_giu_o_allow_user():
    catalog = _catalog()
    allowed = {1, 2, 3, 4, 5, 99999}  # 99999 không có trong danh mục
    acl = compress_allow(allowed, catalog)
    assert 99999 in acl.allow_user_ids
    assert acl.allow_department_ids == [10]
    _roundtrip(acl, catalog, allowed)


def test_rong():
    catalog = _catalog()
    acl = compress_allow(set(), catalog)
    assert acl.cost() == 0
    _roundtrip(acl, catalog, set())


def test_nen_luon_re_hon_hoac_bang_liet_ke():
    catalog = _catalog()
    allowed = set(range(1, 11)) | {20}
    acl = compress_allow(allowed, catalog)
    assert acl.cost() <= len(allowed)
    _roundtrip(acl, catalog, allowed)


def test_deny_thang_allow_khi_giai_nen():
    # Cho phép cả phòng nhưng deny cá nhân -> người đó không có trong kết quả.
    catalog = _catalog()
    acl = CompressedAcl(allow_department_ids=[10], deny_user_ids=[3])
    assert acl.decompress(catalog) == {1, 2, 4, 5}


def test_verify_raise_khi_acl_xay_dung_sai():
    # Mô phỏng lỗi nội bộ: ép verify thất bại để chắc nó thật sự kiểm tra.
    catalog = _catalog()
    with pytest.raises(AclCompressionError):
        # Patch tạm decompress trả về tập rỗng -> lệch đầu vào -> raise.
        import app.services.security.security_acl_compressor as mod

        original = mod.CompressedAcl.decompress
        try:
            mod.CompressedAcl.decompress = lambda self, cat: set()  # type: ignore[assignment]
            compress_allow({1, 2, 3, 4, 5}, catalog, verify=True)
        finally:
            mod.CompressedAcl.decompress = original  # type: ignore[assignment]
