"""Test lớp resolver và ánh xạ payload ACL."""

from __future__ import annotations

from app.services.security.security_acl_compressor import OrgCatalog, compress_allow
from app.services.security.security_acl_payload import (
    AclSubject,
    build_es_acl_filter,
    from_chunk_payload,
    subject_can_access,
    to_chunk_payload,
)
from app.services.security.security_acl_resolver import (
    RawAssignment,
    UnitTree,
    build_assignment_from_doffice,
    resolve_and_compress,
    resolve_effective_users,
)


def _catalog() -> OrgCatalog:
    # Đơn vị 1 (cha) -> đơn vị 2 (con). Mỗi đơn vị 1 phòng, mỗi phòng 3 người.
    employees = [
        (1, 1, 10), (2, 1, 10), (3, 1, 10),
        (4, 2, 20), (5, 2, 20), (6, 2, 20),
    ]
    return OrgCatalog.from_employees(employees, version="t")


def _unit_tree() -> UnitTree:
    return UnitTree.from_pairs([(1, None), (2, 1)])


def test_resolve_unit_subtree():
    catalog = _catalog()
    tree = _unit_tree()
    raw = RawAssignment(allow_unit_ids=frozenset({1}), include_subtree=True)
    users = resolve_effective_users(raw, catalog, unit_tree=tree)
    assert users == {1, 2, 3, 4, 5, 6}  # gồm cả đơn vị con


def test_resolve_unit_khong_subtree():
    catalog = _catalog()
    tree = _unit_tree()
    raw = RawAssignment(allow_unit_ids=frozenset({1}), include_subtree=False)
    users = resolve_effective_users(raw, catalog, unit_tree=tree)
    assert users == {1, 2, 3}  # chỉ đơn vị 1


def test_resolve_deny_thang_allow():
    catalog = _catalog()
    raw = RawAssignment(
        allow_department_ids=frozenset({10}),
        deny_user_ids=frozenset({2}),
    )
    users = resolve_effective_users(raw, catalog)
    assert users == {1, 3}


def test_resolve_deny_department():
    catalog = _catalog()
    tree = _unit_tree()
    raw = RawAssignment(
        allow_unit_ids=frozenset({1}),
        deny_department_ids=frozenset({20}),
        include_subtree=True,
    )
    users = resolve_effective_users(raw, catalog, unit_tree=tree)
    assert users == {1, 2, 3}


def test_resolve_user_la_giu_nguyen():
    catalog = _catalog()
    raw = RawAssignment(allow_user_ids=frozenset({1, 99999}))
    users = resolve_effective_users(raw, catalog)
    assert users == {1, 99999}


def test_resolve_and_compress_roundtrip():
    catalog = _catalog()
    tree = _unit_tree()
    raw = RawAssignment(allow_unit_ids=frozenset({1}), include_subtree=True)
    acl = resolve_and_compress(raw, catalog, unit_tree=tree)
    assert acl.decompress(catalog) == {1, 2, 3, 4, 5, 6}


def test_payload_round_trip():
    catalog = _catalog()
    acl = compress_allow({1, 2, 3}, catalog)
    payload = to_chunk_payload(acl, version="sha256:abc")
    assert payload["acl_ver"] == "sha256:abc"
    back = from_chunk_payload(payload)
    assert back.decompress(catalog) == {1, 2, 3}


def test_subject_can_access_khop_voi_decompress():
    catalog = _catalog()
    # Cho phép phòng 10 nhưng deny người 2.
    acl = compress_allow({1, 3, 4, 5, 6}, catalog)  # cả phòng 10 trừ 2, + cả phòng 20
    payload = to_chunk_payload(acl)
    allowed_set = acl.decompress(catalog)
    for id_nv, (id_dv, id_pb) in catalog.user_location.items():
        subject = AclSubject(id_nv=id_nv, id_dv=id_dv, id_pb=id_pb)
        assert subject_can_access(payload, subject) == (id_nv in allowed_set)


def test_subject_super_admin_luon_thay():
    catalog = _catalog()
    acl = compress_allow(set(), catalog)  # không cho ai
    payload = to_chunk_payload(acl)
    admin = AclSubject(id_nv=999, is_super_admin=True)
    assert subject_can_access(payload, admin) is True


def test_es_filter_cau_truc():
    subject = AclSubject(id_nv=7, id_dv=1, id_pb=10)
    flt = build_es_acl_filter(subject)
    assert flt is not None
    bool_q = flt["bool"]
    assert bool_q["minimum_should_match"] == 1
    should_fields = {list(s["terms"].keys())[0] for s in bool_q["should"]}
    assert should_fields == {"acl_allow_nv", "acl_allow_pb", "acl_allow_dv"}
    must_not_fields = {list(s["terms"].keys())[0] for s in bool_q["must_not"]}
    assert must_not_fields == {"acl_deny_nv", "acl_deny_pb"}


def test_es_filter_super_admin_none():
    assert build_es_acl_filter(AclSubject(id_nv=1, is_super_admin=True)) is None


# ---- build_assignment_from_doffice ----

def test_doffice_ca_nhan_la_nguon_quyen_chinh():
    catalog = _catalog()
    res = build_assignment_from_doffice(
        don_vi_list=[1], phong_ban_list=[10], ca_nhan_list=[1, 2], catalog=catalog
    )
    assert res.assignment.allow_user_ids == frozenset({1, 2})
    assert res.assignment.allow_department_ids == frozenset()
    assert res.warnings == []


def test_doffice_phong_khong_thuoc_don_vi_canh_bao():
    catalog = _catalog()
    # phòng 20 thuộc đơn vị 2, nhưng don_vi khai báo là 1.
    res = build_assignment_from_doffice(
        don_vi_list=[1], phong_ban_list=[20], ca_nhan_list=[1], catalog=catalog
    )
    assert any("Phòng ban 20" in w for w in res.warnings)


def test_doffice_ca_nhan_trong_thi_phat_ca_phong():
    catalog = _catalog()
    res = build_assignment_from_doffice(
        don_vi_list=[1], phong_ban_list=[10], ca_nhan_list=[], catalog=catalog
    )
    assert res.assignment.allow_department_ids == frozenset({10})
    assert res.assignment.allow_user_ids == frozenset()


def test_doffice_khong_co_gi_thi_khong_ai_xem():
    catalog = _catalog()
    res = build_assignment_from_doffice(
        don_vi_list=[], phong_ban_list=[], ca_nhan_list=[], catalog=catalog
    )
    assert res.assignment == RawAssignment()
    assert any("KHÔNG ai được xem" in w for w in res.warnings)


def test_doffice_ca_nhan_la_ngoai_danh_muc_bi_canh_bao():
    catalog = _catalog()
    res = build_assignment_from_doffice(
        don_vi_list=[1], phong_ban_list=[10], ca_nhan_list=[1, 99999], catalog=catalog
    )
    assert res.assignment.allow_user_ids == frozenset({1})
    assert any("không có trong danh mục" in w for w in res.warnings)
