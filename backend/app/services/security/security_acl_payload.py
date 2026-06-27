"""Ánh xạ ACL nén <-> payload chunk (Qdrant/Elasticsearch) và filter phía query.

Bộ trường ACL mới (namespace ``acl_*``) dùng ID danh mục nguyên (id_dv/id_pb/id_nv),
tách biệt với bộ ACL cũ theo org-UUID (allowed_org_ids...). Lưu phẳng (flat) trên
payload để Qdrant/Elasticsearch filter trực tiếp:

    acl_allow_dv : list[int]   # đơn vị được phép
    acl_allow_pb : list[int]   # phòng ban được phép
    acl_allow_nv : list[int]   # cá nhân được phép
    acl_deny_pb  : list[int]   # phòng ban bị loại trừ
    acl_deny_nv  : list[int]   # cá nhân bị loại trừ
    acl_ver      : str         # chữ ký danh mục lúc nén (phục vụ re-compress)

Tính chính xác
==============
Với một người dùng đã biết ``(id_nv, id_dv, id_pb)`` của họ, điều kiện được xem là::

    (id_dv ∈ acl_allow_dv  OR  id_pb ∈ acl_allow_pb  OR  id_nv ∈ acl_allow_nv)
    AND NOT (id_pb ∈ acl_deny_pb  OR  id_nv ∈ acl_deny_nv)

Đây đúng bằng phép giải nén :meth:`CompressedAcl.decompress` cho riêng người đó, nên
filter Qdrant/ES dưới đây là **chính xác tuyệt đối** (không chỉ là coarse pre-filter).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.services.security.security_acl_compressor import CompressedAcl

# Tên trường payload (giữ một chỗ duy nhất để tránh gõ sai rải rác).
F_ALLOW_DV = "acl_allow_dv"
F_ALLOW_PB = "acl_allow_pb"
F_ALLOW_NV = "acl_allow_nv"
F_DENY_PB = "acl_deny_pb"
F_DENY_NV = "acl_deny_nv"
F_VERSION = "acl_ver"
# Trường flatten gộp toàn bộ ALLOW vào 1 list keyword ["dv_{id}","pb_{id}","nv_{id}"].
# Cho phép filter bằng MỘT điều kiện MatchAny/terms thay vì 3 OR riêng -> nhanh + cache tốt.
F_SUBJECTS = "acl_subjects"

ACL_PAYLOAD_FIELDS = (
    F_ALLOW_DV,
    F_ALLOW_PB,
    F_ALLOW_NV,
    F_DENY_PB,
    F_DENY_NV,
    F_VERSION,
    F_SUBJECTS,
)


@dataclass(frozen=True)
class AclSubject:
    """Ngữ cảnh người dùng truy vấn (đã tra từ danh mục nhân viên)."""

    id_nv: int
    id_dv: int | None = None
    id_pb: int | None = None
    is_super_admin: bool = False

    @classmethod
    async def from_session(cls, session, id_nv: int, *, is_super_admin: bool = False) -> "AclSubject | None":
        from sqlalchemy import select

        from app.models.danh_muc import NhanVien

        row = (
            await session.execute(
                select(NhanVien.id_dv, NhanVien.id_pb).where(NhanVien.id_nv == id_nv)
            )
        ).first()
        if row is None:
            return None
        return cls(id_nv=id_nv, id_dv=row[0], id_pb=row[1], is_super_admin=is_super_admin)

    @classmethod
    async def from_app_user(cls, session, user, *, super_admin_roles: set[str] | None = None) -> "AclSubject | None":
        """Dựng từ User ứng dụng qua liên kết ``User.id_nv`` -> danh mục nhân viên.

        Trả None nếu user chưa gắn ``id_nv`` (không map được -> bên gọi quyết định
        không lọc hay từ chối). Cờ super admin lấy từ vai trò của user.
        """
        id_nv = getattr(user, "id_nv", None)
        if id_nv is None:
            return None
        roles = {getattr(r, "name", None) for r in (getattr(user, "roles", None) or [])}
        is_super = bool(super_admin_roles and (roles & super_admin_roles))
        return await cls.from_session(session, int(id_nv), is_super_admin=is_super)


def to_chunk_payload(acl: CompressedAcl, *, version: str | None = None) -> dict[str, Any]:
    """Chuyển ACL nén thành dict trường payload để gắn vào chunk."""
    payload: dict[str, Any] = {
        F_ALLOW_DV: sorted(acl.allow_unit_ids),
        F_ALLOW_PB: sorted(acl.allow_department_ids),
        F_ALLOW_NV: sorted(acl.allow_user_ids),
        F_DENY_PB: sorted(acl.deny_department_ids),
        F_DENY_NV: sorted(acl.deny_user_ids),
    }
    if version is not None:
        payload[F_VERSION] = version
    return payload


def acl_keys_from_acl(acl: CompressedAcl) -> list[str]:
    """Các khóa subject của một chunk (flatten allow): ["dv_{id}","pb_{id}","nv_{id}"]."""
    keys = [f"dv_{i}" for i in acl.allow_unit_ids]
    keys += [f"pb_{i}" for i in acl.allow_department_ids]
    keys += [f"nv_{i}" for i in acl.allow_user_ids]
    return sorted(keys)


def acl_subject_to_keys(subject: AclSubject) -> list[str]:
    """Các khóa của NGƯỜI truy vấn để so với ``acl_subjects`` của chunk.

    Người dùng xem được nếu một trong các khóa này nằm trong ``acl_subjects`` của chunk.
    """
    keys = [f"nv_{subject.id_nv}"]
    if subject.id_pb is not None:
        keys.append(f"pb_{subject.id_pb}")
    if subject.id_dv is not None:
        keys.append(f"dv_{subject.id_dv}")
    return keys


def to_chunk_payload_flat(acl: CompressedAcl, *, version: str | None = None) -> dict[str, Any]:
    """Như :func:`to_chunk_payload` nhưng thêm ``acl_subjects`` (flatten allow).

    Vẫn giữ các trường ``acl_allow_*`` cũ để tương thích ngược.
    """
    payload = to_chunk_payload(acl, version=version)
    payload[F_SUBJECTS] = acl_keys_from_acl(acl)
    return payload


def from_chunk_payload(payload: dict[str, Any]) -> CompressedAcl:
    """Đọc ngược các trường ``acl_*`` trong payload thành CompressedAcl."""
    return CompressedAcl(
        allow_unit_ids=list(payload.get(F_ALLOW_DV) or []),
        allow_department_ids=list(payload.get(F_ALLOW_PB) or []),
        allow_user_ids=list(payload.get(F_ALLOW_NV) or []),
        deny_department_ids=list(payload.get(F_DENY_PB) or []),
        deny_user_ids=list(payload.get(F_DENY_NV) or []),
    )


def subject_can_access(payload: dict[str, Any], subject: AclSubject) -> bool:
    """Kiểm tra chính xác một người dùng có được xem chunk (dựa trên payload acl_*).

    Dùng làm trọng tài sau khi hydrate kết quả tìm kiếm.
    """
    if subject.is_super_admin:
        return True

    allow_dv = set(payload.get(F_ALLOW_DV) or [])
    allow_pb = set(payload.get(F_ALLOW_PB) or [])
    allow_nv = set(payload.get(F_ALLOW_NV) or [])
    deny_pb = set(payload.get(F_DENY_PB) or [])
    deny_nv = set(payload.get(F_DENY_NV) or [])

    allowed = (
        (subject.id_dv is not None and subject.id_dv in allow_dv)
        or (subject.id_pb is not None and subject.id_pb in allow_pb)
        or (subject.id_nv in allow_nv)
    )
    denied = (subject.id_pb is not None and subject.id_pb in deny_pb) or (subject.id_nv in deny_nv)
    return allowed and not denied


def build_es_acl_filter(subject: AclSubject) -> dict[str, Any] | None:
    """Mảnh bool query Elasticsearch để lọc ACL (đưa vào ``filter``).

    Trả về None nếu super admin (không cần lọc).
    """
    if subject.is_super_admin:
        return None

    should: list[dict[str, Any]] = [{"terms": {F_ALLOW_NV: [subject.id_nv]}}]
    if subject.id_pb is not None:
        should.append({"terms": {F_ALLOW_PB: [subject.id_pb]}})
    if subject.id_dv is not None:
        should.append({"terms": {F_ALLOW_DV: [subject.id_dv]}})

    must_not: list[dict[str, Any]] = [{"terms": {F_DENY_NV: [subject.id_nv]}}]
    if subject.id_pb is not None:
        must_not.append({"terms": {F_DENY_PB: [subject.id_pb]}})

    return {"bool": {"should": should, "minimum_should_match": 1, "must_not": must_not}}


def build_qdrant_acl_conditions(subject: AclSubject) -> tuple[list[Any], list[Any]] | None:
    """Trả về (should, must_not) gồm các FieldCondition của qdrant-client.

    Hợp nhất vào Filter hiện có: ``should`` = ít nhất một điều kiện cho phép khớp,
    ``must_not`` = không được nằm trong danh sách loại trừ. Trả None nếu super admin.
    """
    if subject.is_super_admin:
        return None

    from qdrant_client.models import FieldCondition, MatchAny

    should: list[Any] = [FieldCondition(key=F_ALLOW_NV, match=MatchAny(any=[subject.id_nv]))]
    if subject.id_pb is not None:
        should.append(FieldCondition(key=F_ALLOW_PB, match=MatchAny(any=[subject.id_pb])))
    if subject.id_dv is not None:
        should.append(FieldCondition(key=F_ALLOW_DV, match=MatchAny(any=[subject.id_dv])))

    must_not: list[Any] = [FieldCondition(key=F_DENY_NV, match=MatchAny(any=[subject.id_nv]))]
    if subject.id_pb is not None:
        must_not.append(FieldCondition(key=F_DENY_PB, match=MatchAny(any=[subject.id_pb])))

    return should, must_not


def build_qdrant_acl_filter(subject: AclSubject) -> Any | None:
    """Filter Qdrant độc lập chỉ gồm điều kiện ACL (nếu cần dùng riêng)."""
    conditions = build_qdrant_acl_conditions(subject)
    if conditions is None:
        return None
    from qdrant_client.models import Filter

    should, must_not = conditions
    return Filter(should=should, must_not=must_not)


# ---- Biến thể FLAT (dùng acl_subjects: 1 điều kiện thay vì 3 OR) ----

def build_es_acl_filter_flat(subject: AclSubject) -> dict[str, Any] | None:
    """Mảnh bool query ES dùng ``acl_subjects`` (1 terms) + deny. None nếu super admin."""
    if subject.is_super_admin:
        return None
    must_not: list[dict[str, Any]] = [{"terms": {F_DENY_NV: [subject.id_nv]}}]
    if subject.id_pb is not None:
        must_not.append({"terms": {F_DENY_PB: [subject.id_pb]}})
    return {
        "bool": {
            "filter": [{"terms": {F_SUBJECTS: acl_subject_to_keys(subject)}}],
            "must_not": must_not,
        }
    }


def build_qdrant_acl_conditions_flat(subject: AclSubject) -> tuple[list[Any], list[Any]] | None:
    """(should, must_not) Qdrant dùng ``acl_subjects`` (1 MatchAny) + deny. None nếu super admin."""
    if subject.is_super_admin:
        return None
    from qdrant_client.models import FieldCondition, MatchAny

    should: list[Any] = [FieldCondition(key=F_SUBJECTS, match=MatchAny(any=acl_subject_to_keys(subject)))]
    must_not: list[Any] = [FieldCondition(key=F_DENY_NV, match=MatchAny(any=[subject.id_nv]))]
    if subject.id_pb is not None:
        must_not.append(FieldCondition(key=F_DENY_PB, match=MatchAny(any=[subject.id_pb])))
    return should, must_not


def build_qdrant_acl_filter_flat(subject: AclSubject) -> Any | None:
    """Filter Qdrant độc lập (flat) chỉ gồm điều kiện ACL."""
    conditions = build_qdrant_acl_conditions_flat(subject)
    if conditions is None:
        return None
    from qdrant_client.models import Filter

    should, must_not = conditions
    return Filter(should=should, must_not=must_not)
