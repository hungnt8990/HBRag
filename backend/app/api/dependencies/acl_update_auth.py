"""Hook xác thực cho API cập nhật ACL (DOffice gọi) — chokepoint để gắn phân quyền sau.

Đặt riêng để sau này thay bằng kiểm tra quyền thật (API key DOffice / mTLS / IP allowlist…)
mà KHÔNG đụng tới ``update_document_acl``. Route chỉ cần
``dependencies=[Depends(require_acl_update_access)]``.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Header, HTTPException, status

from app.core.config import settings


async def require_acl_update_access(
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> None:
    """Chặn truy cập API cập nhật ACL nếu không có quyền.

    Hiện dùng API key tĩnh: nếu ``settings.doffice_acl_api_key`` được cấu hình thì request
    PHẢI gửi ``X-API-Key`` khớp; chưa cấu hình -> cho qua (dev/local). Thay thân hàm bằng
    logic quyền thật khi cần (giữ nguyên chữ ký để route không phải đổi).
    """
    expected = settings.doffice_acl_api_key
    if not expected:
        return
    if x_api_key != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Thiếu hoặc sai X-API-Key.",
        )
