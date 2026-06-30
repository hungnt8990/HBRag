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
_STATIC_DIR = Path(__file__).resolve().parents[2] / "static"
_ARCHITECTURE_HTML = _STATIC_DIR / "architecture.html"
_DATA_STORES_HTML = _STATIC_DIR / "data-stores.html"


def _serve_html(path: Path, what: str) -> FileResponse | HTMLResponse:
    if not path.is_file():
        return HTMLResponse(f"<h1>Không tìm thấy {what}</h1><p>Thiếu file: {path}</p>", status_code=404)
    return FileResponse(path, media_type="text/html; charset=utf-8")


@router.get("/architecture", include_in_schema=False, response_model=None)
async def architecture_page() -> FileResponse | HTMLResponse:
    """Trả trang sơ đồ kiến trúc RAG (HTML)."""
    return _serve_html(_ARCHITECTURE_HTML, "sơ đồ kiến trúc")


@router.get("/data-stores", include_in_schema=False, response_model=None)
async def data_stores_page() -> FileResponse | HTMLResponse:
    """Trả trang liệt kê data các store (PostgreSQL / Elasticsearch / Qdrant)."""
    return _serve_html(_DATA_STORES_HTML, "trang data stores")
