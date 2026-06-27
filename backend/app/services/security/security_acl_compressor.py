"""Bộ nén ACL thông minh theo danh mục tổ chức EVNCPC.

Bài toán
========
Một văn bản (DOffice/eOffice) sau khi resolve sẽ có một *tập người được xem*
(effective audience) — có thể tới hàng nghìn người. Lưu nguyên danh sách user vào
payload của từng chunk (Qdrant/Elasticsearch) vừa nặng vừa khó lọc.

Bộ nén này biến tập user đó thành biểu diễn gọn nhất có thể, dựa trên ba cấp:

    đơn vị (unit) ⊃ phòng ban (department) ⊃ cá nhân (user)

Quy tắc (đúng như mô tả nghiệp vụ):
- Nếu **cả phòng** đều được xem -> lưu *id phòng ban*, không lưu từng người.
- Nếu **9/10** người trong phòng được xem -> lưu *id phòng ban* + *deny* 1 người.
- Tương tự ở cấp đơn vị: nếu nhiều phòng trong đơn vị được xem -> gộp thành
  *id đơn vị* + loại trừ (deny) các phòng/người không được xem.
- Người lẻ không gộp được -> lưu *id cá nhân*.

Tính đúng đắn
=============
Mỗi nhân viên thuộc ĐÚNG MỘT đơn vị và MỘT phòng ban (tính phân hoạch đã kiểm
chứng trên dữ liệu thật). Nhờ đó các nhánh gộp không chồng lấn và thuật toán
tham lam cho kết quả nhất quán. Ngoài ra mọi kết quả nén đều được **verify** bằng
cách giải nén lại và so khớp tuyệt đối với tập đầu vào; nếu lệch sẽ raise để bên
gọi fallback an toàn (liệt kê user). Nén chỉ nhằm *gọn payload*, không bao giờ
được phép làm *sai quyền*.

Lưu ý ngữ nghĩa (snapshot vs động)
==================================
Khi gộp lên cấp phòng ban/đơn vị, biểu diễn nén phản ánh *thành viên hiện tại*
của phòng/đơn vị. Nếu nhân sự thay đổi (vào/ra/chuyển phòng), cần nén lại
(re-compress) cho các văn bản liên quan. Trường :attr:`OrgCatalog.version` giúp
phát hiện danh mục đã đổi để kích hoạt nén lại.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field


@dataclass(frozen=True)
class OrgCatalog:
    """Chỉ mục tra cứu thành viên theo phòng ban / đơn vị.

    Dùng :meth:`from_employees` để dựng từ danh sách (id_nv, id_dv, id_pb),
    hoặc :meth:`from_session` để dựng trực tiếp từ DB.
    """

    department_members: Mapping[int, frozenset[int]]
    unit_departments: Mapping[int, frozenset[int]]
    unit_members: Mapping[int, frozenset[int]]
    user_location: Mapping[int, tuple[int | None, int | None]]
    version: str | None = None

    @classmethod
    def from_employees(
        cls,
        employees: Iterable[tuple[int, int | None, int | None]],
        *,
        version: str | None = None,
    ) -> OrgCatalog:
        """Dựng catalog từ iterable (id_nv, id_dv, id_pb)."""
        dept_members: dict[int, set[int]] = defaultdict(set)
        unit_depts: dict[int, set[int]] = defaultdict(set)
        unit_members: dict[int, set[int]] = defaultdict(set)
        user_location: dict[int, tuple[int | None, int | None]] = {}

        for id_nv, id_dv, id_pb in employees:
            user_location[id_nv] = (id_dv, id_pb)
            if id_pb is not None:
                dept_members[id_pb].add(id_nv)
                if id_dv is not None:
                    unit_depts[id_dv].add(id_pb)
            if id_dv is not None:
                unit_members[id_dv].add(id_nv)

        return cls(
            department_members={k: frozenset(v) for k, v in dept_members.items()},
            unit_departments={k: frozenset(v) for k, v in unit_depts.items()},
            unit_members={k: frozenset(v) for k, v in unit_members.items()},
            user_location=user_location,
            version=version,
        )

    @classmethod
    async def from_session(cls, session, *, version: str | None = None) -> OrgCatalog:
        """Dựng catalog từ bảng ``dm_nhan_vien`` (chỉ nhân viên đang active)."""
        from sqlalchemy import select

        from app.models.danh_muc import NhanVien

        result = await session.execute(
            select(NhanVien.id_nv, NhanVien.id_dv, NhanVien.id_pb).where(NhanVien.is_active.is_(True))
        )
        return cls.from_employees(result.all(), version=version)


@dataclass
class CompressedAcl:
    """Biểu diễn ACL đã nén: 3 list cho phép + 2 list loại trừ.

    Giải nén (xem :meth:`decompress`)::

        allowed = (∪ thành viên đơn vị trong allow_unit_ids)
                ∪ (∪ thành viên phòng ban trong allow_department_ids)
                ∪ allow_user_ids
                − (∪ thành viên phòng ban trong deny_department_ids)
                − deny_user_ids

    Loại trừ luôn thắng cho phép (deny wins).
    """

    allow_unit_ids: list[int] = field(default_factory=list)
    allow_department_ids: list[int] = field(default_factory=list)
    allow_user_ids: list[int] = field(default_factory=list)
    deny_department_ids: list[int] = field(default_factory=list)
    deny_user_ids: list[int] = field(default_factory=list)

    def cost(self) -> int:
        """Số id phải lưu — thước đo để so sánh độ gọn."""
        return (
            len(self.allow_unit_ids)
            + len(self.allow_department_ids)
            + len(self.allow_user_ids)
            + len(self.deny_department_ids)
            + len(self.deny_user_ids)
        )

    def decompress(self, catalog: OrgCatalog) -> set[int]:
        allowed: set[int] = set()
        for unit_id in self.allow_unit_ids:
            allowed |= catalog.unit_members.get(unit_id, frozenset())
        for dept_id in self.allow_department_ids:
            allowed |= catalog.department_members.get(dept_id, frozenset())
        allowed |= set(self.allow_user_ids)
        for dept_id in self.deny_department_ids:
            allowed -= catalog.department_members.get(dept_id, frozenset())
        allowed -= set(self.deny_user_ids)
        return allowed

    def to_dict(self) -> dict[str, list[int]]:
        return {
            "allow_unit_ids": sorted(self.allow_unit_ids),
            "allow_department_ids": sorted(self.allow_department_ids),
            "allow_user_ids": sorted(self.allow_user_ids),
            "deny_department_ids": sorted(self.deny_department_ids),
            "deny_user_ids": sorted(self.deny_user_ids),
        }


class AclCompressionError(ValueError):
    """Nén ra kết quả không khớp đầu vào — bên gọi nên fallback liệt kê user."""


def compress_allow(
    allowed_users: Iterable[int],
    catalog: OrgCatalog,
    *,
    verify: bool = True,
) -> CompressedAcl:
    """Nén tập user được phép thành :class:`CompressedAcl` gọn nhất.

    Thuật toán: gộp cấp phòng ban trước, sau đó cân nhắc gộp lên cấp đơn vị
    (unit roll-up), phần còn lại để dạng cá nhân. Mỗi quyết định gộp đều dựa trên
    so sánh chi phí (số id phải lưu): chỉ gộp khi rẻ hơn liệt kê cá nhân.

    User không có trong danh mục được giữ nguyên ở ``allow_user_ids``.
    """
    requested = set(allowed_users)
    known = {u for u in requested if u in catalog.user_location}
    unknown = requested - known

    remaining = set(known)  # user được phép nhưng chưa được nhánh gộp nào phủ
    allow_unit: list[int] = []
    allow_dept: list[int] = []
    deny_dept: set[int] = set()
    deny_user: set[int] = set()

    # Các phòng ban / đơn vị có ít nhất một người được phép -> ứng viên gộp.
    candidate_depts = {
        catalog.user_location[u][1] for u in known if catalog.user_location[u][1] is not None
    }
    candidate_units = {
        catalog.user_location[u][0] for u in known if catalog.user_location[u][0] is not None
    }

    # ---- PASS 1: gộp cấp phòng ban ----
    chosen_depts: set[int] = set()
    for pb in sorted(candidate_depts):
        members = catalog.department_members.get(pb, frozenset())
        inside = members & known
        if not inside:
            continue
        excluded = members - known
        if 1 + len(excluded) < len(inside):  # rẻ hơn liệt kê từng người
            chosen_depts.add(pb)
            allow_dept.append(pb)
            deny_user |= excluded
            remaining -= inside

    # ---- PASS 2: gộp lên cấp đơn vị (roll-up) ----
    for unit_id in sorted(candidate_units):
        depts = catalog.unit_departments.get(unit_id, frozenset())
        unit_member_set = catalog.unit_members.get(unit_id, frozenset())

        # Chi phí hiện tại mà đơn vị này đang đóng góp.
        cur_chosen = [pb for pb in depts if pb in chosen_depts]
        cur_excluded = sum(
            len(catalog.department_members.get(pb, frozenset()) - known) for pb in cur_chosen
        )
        cur_leftover = len(unit_member_set & remaining)
        current_cost = len(cur_chosen) + cur_excluded + cur_leftover

        # Chi phí nếu biểu diễn bằng cả đơn vị + loại trừ phần không được phép.
        unit_deny_dept: set[int] = set()
        unit_deny_user: set[int] = set()
        for pb in depts:
            members = catalog.department_members.get(pb, frozenset())
            if not members:
                continue
            excluded = members - known
            if not excluded:
                continue  # phòng được phép toàn bộ
            if members & known:
                unit_deny_user |= excluded  # phòng được phép một phần -> deny lẻ
            else:
                unit_deny_dept.add(pb)  # phòng không ai được phép -> deny cả phòng
        unit_cost = 1 + len(unit_deny_dept) + len(unit_deny_user)

        if unit_cost < current_cost:
            # Thay thế: bỏ các phòng đã chọn của đơn vị này, gộp thành đơn vị.
            for pb in cur_chosen:
                chosen_depts.discard(pb)
                allow_dept.remove(pb)
                deny_user -= (catalog.department_members.get(pb, frozenset()) - known)
            allow_unit.append(unit_id)
            deny_dept |= unit_deny_dept
            deny_user |= unit_deny_user
            remaining -= (unit_member_set & known)

    # ---- PASS 3: phần còn lại để dạng cá nhân ----
    allow_user = sorted(remaining) + sorted(unknown)

    result = CompressedAcl(
        allow_unit_ids=sorted(allow_unit),
        allow_department_ids=sorted(allow_dept),
        allow_user_ids=allow_user,
        deny_department_ids=sorted(deny_dept),
        deny_user_ids=sorted(deny_user),
    )

    if verify:
        produced = result.decompress(catalog)
        # decompress không biết user unknown qua nhóm; chúng nằm ở allow_user.
        if produced != requested:
            raise AclCompressionError(
                "ACL nén không khớp đầu vào: "
                f"thiếu={sorted(requested - produced)[:10]} thừa={sorted(produced - requested)[:10]}"
            )

    return result
