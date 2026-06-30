"""Phục vụ trang sơ đồ kiến trúc RAG (HTML tĩnh) để xem trực tiếp khi chạy backend.

Mở ``http://<host>:<port>/architecture`` trên trình duyệt là thấy sơ đồ. File HTML nằm ở
``app/static/architecture.html`` — sửa nội dung ở đó, không cần đổi route.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse, HTMLResponse

router = APIRouter(tags=["architecture"])

# app/api/routes/architecture.py -> parents[2] = app/ ; file tĩnh ở app/static/.
_ARCHITECTURE_HTML = Path(__file__).resolve().parents[2] / "static" / "architecture.html"


@router.get("/architecture", include_in_schema=False, response_model=None)
async def architecture_page() -> FileResponse | HTMLResponse:
    """Trả trang sơ đồ kiến trúc RAG (HTML)."""
    if not _ARCHITECTURE_HTML.is_file():
        return HTMLResponse(
            "<h1>Không tìm thấy sơ đồ kiến trúc</h1>"
            f"<p>Thiếu file: {_ARCHITECTURE_HTML}</p>",
            status_code=404,
        )
    return FileResponse(_ARCHITECTURE_HTML, media_type="text/html; charset=utf-8")
