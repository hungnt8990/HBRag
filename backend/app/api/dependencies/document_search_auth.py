"""Hook xác thực cho API tìm kiếm văn bản (chokepoint để gắn middleware quyền).

Đặt riêng để sau này thay bằng kiểm tra quyền thực (JWT / mTLS / danh sách service
được phép...) mà KHÔNG đụng tới logic tìm kiếm. Route chỉ cần
``dependencies=[Depends(require_document_search_access)]``.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Header, HTTPException, status

from app.core.config import settings


async def require_document_search_access(
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> None:
    """Chặn truy cập API tìm kiếm văn bản nếu không có quyền.

    Hiện dùng API key tĩnh: nếu ``settings.document_search_api_key`` được cấu hình thì
    request PHẢI gửi header ``X-API-Key`` khớp; nếu CHƯA cấu hình -> cho qua (dev/local).
    Thay phần thân hàm này bằng logic quyền thật khi cần (vẫn giữ nguyên chữ ký để route
    không phải đổi).
    """
    expected = settings.document_search_api_key
    if not expected:
        return
    if x_api_key != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Thiếu hoặc sai X-API-Key.",
        )
