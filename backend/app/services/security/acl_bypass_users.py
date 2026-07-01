"""Danh sách ID nhân viên được BỎ QUA ACL (xem được TẤT CẢ văn bản).

Nguồn sự thật: file text ``config/acl_bypass_users.txt`` — mỗi dòng 1 ``id_nv``
(bỏ qua dòng trống và dòng bắt đầu bằng '#'). Khi ``id_nv`` của người gọi nằm
trong danh sách này thì ``AclSubject.is_super_admin`` được bật -> ``build_acl_filters``
trả rỗng -> KHÔNG lọc quyền, người đó lọc/tìm được toàn bộ.

ĐỌC ĐỘNG: file được đọc lại mỗi khi ``mtime`` thay đổi (cache theo mtime), nên
thêm/bớt id trong file có hiệu lực NGAY mà KHÔNG cần khởi động lại backend.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# backend/app/services/security/<file> -> parents[3] = backend/
ACL_BYPASS_USERS_FILE = Path(__file__).resolve().parents[3] / "config" / "acl_bypass_users.txt"

# Cache: (mtime_ns đã đọc, tập id_nv). mtime_ns = None nghĩa là chưa đọc/đã mất file.
_cache: tuple[int | None, frozenset[int]] = (None, frozenset())


def _parse(text: str) -> frozenset[int]:
    ids: set[int] = set()
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        # Cho phép ghi chú cuối dòng: "258  # trưởng phòng CNTT".
        token = line.split("#", 1)[0].strip()
        if not token:
            continue
        try:
            ids.add(int(token))
        except ValueError:
            logger.warning("acl_bypass_users: dòng không phải id_nv hợp lệ -> bỏ qua: %r", raw)
    return frozenset(ids)


def load_bypass_user_ids(path: Path | None = None) -> frozenset[int]:
    """Trả tập ``id_nv`` được bỏ qua ACL, ĐỌC LẠI file khi mtime đổi (không cache cứng).

    File thiếu -> tập rỗng (không ai được bỏ qua). Lỗi đọc -> giữ cache cũ (an toàn).
    """
    global _cache
    p = path or ACL_BYPASS_USERS_FILE
    try:
        mtime = p.stat().st_mtime_ns
    except FileNotFoundError:
        if _cache[0] is not None:
            logger.info("acl_bypass_users: không thấy file %s -> danh sách rỗng", p)
        _cache = (None, frozenset())
        return _cache[1]
    except OSError:
        logger.warning("acl_bypass_users: không stat được %s -> giữ cache cũ", p, exc_info=True)
        return _cache[1]

    if mtime == _cache[0]:
        return _cache[1]

    try:
        ids = _parse(p.read_text(encoding="utf-8"))
    except OSError:
        logger.warning("acl_bypass_users: không đọc được %s -> giữ cache cũ", p, exc_info=True)
        return _cache[1]

    _cache = (mtime, ids)
    logger.info("acl_bypass_users: nạp %d id_nv bỏ qua ACL từ %s", len(ids), p)
    return ids


def is_bypass_user(id_nv: int | None, path: Path | None = None) -> bool:
    """True nếu ``id_nv`` nằm trong danh sách bỏ qua ACL."""
    if id_nv is None:
        return False
    return id_nv in load_bypass_user_ids(path)
