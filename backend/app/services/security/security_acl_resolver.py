"""Lớp resolver: ACL thô (assignment) -> tập user hiệu lực -> ACL nén.

Luồng phân quyền cho một văn bản:

    DOffice/eOffice raw  --(adapter)-->  RawAssignment (theo danh mục EVNCPC)
                         --(resolve)-->  effective_users: set[id_nv]
                         --(compress)->  CompressedAcl  (xem security_acl_compressor)

Trạng thái hiện tại: DOffice **chưa** export ACL (chỉ có ``id_dv_ban_hanh``). Vì vậy
:class:`RawAssignment` là đầu vào chuẩn hóa mà một *adapter* (từ DOffice/eOffice hoặc
nhập tay) sẽ điền. Khi DOffice bổ sung trường phân quyền, chỉ cần viết adapter map
về :class:`RawAssignment`, phần resolve/compress giữ nguyên.

Quy ước:
- Giao cho **đơn vị** mặc định bao gồm cả **đơn vị con** (subtree) — bật/tắt bằng
  ``RawAssignment.include_subtree``.
- Loại trừ (deny) luôn thắng cho phép (deny wins).
- User cho phép nhưng không có trong danh mục vẫn được giữ (đưa vào allow cá nhân).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from app.services.security.security_acl_compressor import (
    CompressedAcl,
    OrgCatalog,
    compress_allow,
)


@dataclass(frozen=True)
class RawAssignment:
    """ACL dương đã chuẩn hóa về danh mục tổ chức EVNCPC.

    Tất cả id là id danh mục: ``id_dv`` (đơn vị), ``id_pb`` (phòng ban), ``id_nv``
    (nhân viên).
    """

    allow_unit_ids: frozenset[int] = frozenset()
    allow_department_ids: frozenset[int] = frozenset()
    allow_user_ids: frozenset[int] = frozenset()
    deny_unit_ids: frozenset[int] = frozenset()
    deny_department_ids: frozenset[int] = frozenset()
    deny_user_ids: frozenset[int] = frozenset()
    include_subtree: bool = True

    @classmethod
    def from_dict(cls, data: Mapping[str, object] | None) -> RawAssignment:
        """Dựng từ dict (ví dụ document_metadata["access"]["raw_assignment"])."""
        data = data or {}

        def _ints(key: str) -> frozenset[int]:
            value = data.get(key) or []
            if isinstance(value, (str, bytes)):
                return frozenset()
            try:
                return frozenset(int(x) for x in value)  # type: ignore[union-attr]
            except (TypeError, ValueError):
                return frozenset()

        return cls(
            allow_unit_ids=_ints("allow_unit_ids"),
            allow_department_ids=_ints("allow_department_ids"),
            allow_user_ids=_ints("allow_user_ids"),
            deny_unit_ids=_ints("deny_unit_ids"),
            deny_department_ids=_ints("deny_department_ids"),
            deny_user_ids=_ints("deny_user_ids"),
            include_subtree=bool(data.get("include_subtree", True)),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "allow_unit_ids": sorted(self.allow_unit_ids),
            "allow_department_ids": sorted(self.allow_department_ids),
            "allow_user_ids": sorted(self.allow_user_ids),
            "deny_unit_ids": sorted(self.deny_unit_ids),
            "deny_department_ids": sorted(self.deny_department_ids),
            "deny_user_ids": sorted(self.deny_user_ids),
            "include_subtree": self.include_subtree,
        }


@dataclass(frozen=True)
class UnitTree:
    """Cây đơn vị (id_dv -> đơn vị con trực tiếp) để mở rộng subtree."""

    children: Mapping[int, frozenset[int]]

    @classmethod
    def from_pairs(cls, pairs: Iterable[tuple[int, int | None]]) -> UnitTree:
        """Dựng từ iterable (id_dv, id_dv_cha)."""
        children: dict[int, set[int]] = defaultdict(set)
        for id_dv, id_dv_cha in pairs:
            if id_dv_cha is not None:
                children[id_dv_cha].add(id_dv)
        return cls(children={k: frozenset(v) for k, v in children.items()})

    @classmethod
    async def from_session(cls, session) -> UnitTree:
        from sqlalchemy import select

        from app.models.danh_muc import DonVi

        result = await session.execute(select(DonVi.id_dv, DonVi.id_dv_cha))
        return cls.from_pairs(result.all())

    def descendants(self, unit_id: int) -> set[int]:
        """Trả về chính ``unit_id`` cùng toàn bộ đơn vị con cháu."""
        out: set[int] = set()
        stack = [unit_id]
        while stack:
            cur = stack.pop()
            if cur in out:
                continue
            out.add(cur)
            stack.extend(self.children.get(cur, ()))
        return out


def _expand_units(unit_ids: Iterable[int], tree: UnitTree | None, *, subtree: bool) -> set[int]:
    units: set[int] = set()
    for unit_id in unit_ids:
        if subtree and tree is not None:
            units |= tree.descendants(unit_id)
        else:
            units.add(unit_id)
    return units


def resolve_effective_users(
    raw: RawAssignment,
    catalog: OrgCatalog,
    *,
    unit_tree: UnitTree | None = None,
) -> set[int]:
    """Khai triển assignment thô thành tập id_nv được phép xem.

    Áp deny sau allow (deny wins). Mở rộng subtree cho đơn vị nếu bật.
    """
    allowed: set[int] = set()

    for dv in _expand_units(raw.allow_unit_ids, unit_tree, subtree=raw.include_subtree):
        allowed |= catalog.unit_members.get(dv, frozenset())
    for pb in raw.allow_department_ids:
        allowed |= catalog.department_members.get(pb, frozenset())
    # User cho phép: cả người có trong danh mục lẫn người lạ (giữ nguyên).
    allowed |= set(raw.allow_user_ids)

    denied: set[int] = set()
    for dv in _expand_units(raw.deny_unit_ids, unit_tree, subtree=raw.include_subtree):
        denied |= catalog.unit_members.get(dv, frozenset())
    for pb in raw.deny_department_ids:
        denied |= catalog.department_members.get(pb, frozenset())
    denied |= set(raw.deny_user_ids)

    return allowed - denied


def resolve_and_compress(
    raw: RawAssignment,
    catalog: OrgCatalog,
    *,
    unit_tree: UnitTree | None = None,
) -> CompressedAcl:
    """Resolve rồi nén — đầu ra dùng để gắn vào payload chunk."""
    effective = resolve_effective_users(raw, catalog, unit_tree=unit_tree)
    return compress_allow(effective, catalog)


@dataclass(frozen=True)
class DofficeAclResolution:
    """Kết quả phân giải ACL từ 3 list thô của DOffice."""

    assignment: RawAssignment
    warnings: list[str] = field(default_factory=list)


def build_assignment_from_doffice(
    *,
    don_vi_list: Iterable[int] | None,
    phong_ban_list: Iterable[int] | None,
    ca_nhan_list: Iterable[int] | None,
    catalog: OrgCatalog,
) -> DofficeAclResolution:
    """Phân giải 3 list DOffice -> RawAssignment, có kiểm tra phân cấp trên danh mục.

    Quy tắc (đã chốt với nghiệp vụ):
    - Người nhận = ``ca_nhan_list`` (lá) — nguồn quyền chính; bộ nén tự gộp lên phòng/đơn vị.
    - ``phong_ban_list`` chỉ dùng làm **dự phòng** khi ``ca_nhan_list`` trống (phát cả phòng).
    - ``don_vi_list`` chỉ để kiểm tra phạm vi, KHÔNG cấp quyền cả đơn vị.
    - Không có phòng ban lẫn cá nhân nhận -> KHÔNG ai được xem (assignment rỗng).

    Kiểm tra (ghi vào ``warnings``, không chặn): phòng ban có thuộc đơn vị khai báo không;
    cá nhân có trong danh mục và đúng phạm vi không.
    """

    def _to_int_set(values: Iterable[int] | None) -> set[int]:
        out: set[int] = set()
        for value in values or []:
            try:
                out.add(int(value))
            except (TypeError, ValueError):
                continue
        return out

    don_vi = _to_int_set(don_vi_list)
    phong_ban = _to_int_set(phong_ban_list)
    ca_nhan = _to_int_set(ca_nhan_list)
    warnings: list[str] = []

    # 1) Phòng ban có thuộc đơn vị khai báo không.
    for pb in sorted(phong_ban):
        owner = next(
            (dv for dv in don_vi if pb in catalog.unit_departments.get(dv, frozenset())),
            None,
        )
        if don_vi and owner is None:
            warnings.append(
                f"Phòng ban {pb} không thuộc đơn vị nào trong don_vi_list {sorted(don_vi)}."
            )

    # 2) Cá nhân: có trong danh mục và đúng phạm vi không.
    known: set[int] = set()
    unknown: set[int] = set()
    for user_id in ca_nhan:
        loc = catalog.user_location.get(user_id)
        if loc is None:
            unknown.add(user_id)
            continue
        known.add(user_id)
        user_dv, user_pb = loc
        in_scope = (user_pb in phong_ban) or (user_dv in don_vi)
        if (phong_ban or don_vi) and not in_scope:
            warnings.append(
                f"Cá nhân {user_id} (phòng {user_pb}, đơn vị {user_dv}) ngoài phạm vi khai báo."
            )
    if unknown:
        warnings.append(
            f"{len(unknown)} cá nhân không có trong danh mục (bỏ qua): {sorted(unknown)[:10]}"
        )

    # 3) Dựng assignment theo thứ tự ưu tiên: cá nhân -> cả phòng -> rỗng.
    if known:
        assignment = RawAssignment(allow_user_ids=frozenset(known))
    elif phong_ban:
        warnings.append("ca_nhan_list trống -> phát cho cả phòng ban (phong_ban_list).")
        assignment = RawAssignment(allow_department_ids=frozenset(phong_ban))
    else:
        warnings.append("Không có phòng ban/cá nhân nhận -> KHÔNG ai được xem.")
        assignment = RawAssignment()

    return DofficeAclResolution(assignment=assignment, warnings=warnings)


def resolve_doffice_and_compress(
    *,
    don_vi_list: Iterable[int] | None,
    phong_ban_list: Iterable[int] | None,
    ca_nhan_list: Iterable[int] | None,
    catalog: OrgCatalog,
    unit_tree: UnitTree | None = None,
) -> tuple[CompressedAcl, RawAssignment, list[str]]:
    """Tiện ích: phân giải 3 list DOffice -> nén. Trả (acl, assignment, warnings)."""
    resolution = build_assignment_from_doffice(
        don_vi_list=don_vi_list,
        phong_ban_list=phong_ban_list,
        ca_nhan_list=ca_nhan_list,
        catalog=catalog,
    )
    acl = resolve_and_compress(resolution.assignment, catalog, unit_tree=unit_tree)
    return acl, resolution.assignment, resolution.warnings
