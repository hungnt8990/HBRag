"""Xác thực Active Directory (LDAP) + tra cứu nhân viên từ ``dm_nhan_vien``.

Tương đương ``CheckUserAD`` ở api_ktht_v2.0 (LoginController.cs): bind LDAP với
``domain\\username`` + mật khẩu. Bind thành công = tài khoản hợp lệ. KHÔNG lưu mật khẩu.

Sau khi xác thực, map ``username`` AD -> ``dm_nhan_vien`` để lấy ``id_nv`` (dùng cho ACL).
"""

from __future__ import annotations

import logging
from typing import Any

import ldap3
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings

logger = logging.getLogger(__name__)


def authenticate_ad(username: str, password: str) -> bool:
    """True nếu (username, password) bind được vào AD. Bind dạng ``domain\\username``."""
    username = (username or "").strip()
    if not username or not password:
        return False
    bare = username.split("\\")[-1].strip()  # bỏ tiền tố domain nếu người dùng tự gõ
    bind_user = f"{settings.ad_domain}\\{bare}"
    try:
        server = ldap3.Server(
            settings.ad_domain,
            port=settings.ad_port,
            use_ssl=settings.ad_use_ssl,
            get_info=ldap3.NONE,
            connect_timeout=settings.ad_timeout_seconds,
        )
        conn = ldap3.Connection(
            server,
            user=bind_user,
            password=password,
            authentication=ldap3.SIMPLE,
            receive_timeout=settings.ad_timeout_seconds,
        )
        try:
            ok = conn.bind()
        finally:
            try:
                conn.unbind()
            except Exception:
                pass
        if not ok:
            logger.info("AD bind THẤT BẠI cho user=%s", bare)
        return bool(ok)
    except Exception:
        logger.warning("AD bind LỖI (server/mạng) cho user=%s", bare, exc_info=True)
        return False


async def lookup_nhan_vien(session: AsyncSession, username: str) -> dict[str, Any] | None:
    """Tra ``dm_nhan_vien`` theo username AD -> dict(id_nv, ho_ten, email, id_dv, id_pb).

    ``dm_nhan_vien.username`` thường có dạng ``evncpc\\hungnt``; người dùng gõ ``hungnt``.
    Khớp theo: username đầy đủ, phần sau dấu ``\\``, hoặc email ``<bare>@cpc.vn``.
    """
    bare = (username or "").split("\\")[-1].strip()
    if not bare:
        return None
    row = (
        await session.execute(
            text(
                "SELECT id_nv, ho_ten, email, id_dv, id_pb FROM dm_nhan_vien "
                "WHERE lower(username) = lower(:full) "
                "   OR lower(split_part(username, '\\', 2)) = lower(:bare) "
                "   OR lower(email) = lower(:email) "
                "LIMIT 1"
            ),
            {"full": username, "bare": bare, "email": f"{bare}{settings.ad_email_suffix}"},
        )
    ).first()
    if row is None:
        return None
    return {
        "id_nv": row.id_nv,
        "ho_ten": row.ho_ten,
        "email": row.email,
        "id_dv": row.id_dv,
        "id_pb": row.id_pb,
    }
