"""Ghi log lời gọi API vào PostgreSQL (bảng ``api_request_logs``) — DÙNG CHUNG, ĐỘNG.

Mọi API tra cứu (hiện tại: ``document-search/search``; sau này: các API động khác) gọi
``log_api_request(...)`` để lưu 1 dòng: ai hỏi, tham số vào, lọc kiểu gì, kết quả ra sao.

Nguyên tắc: hàm TỰ mở session và TUYỆT ĐỐI không ném lỗi ra ngoài (ghi log thất bại chỉ
cảnh báo) -> không bao giờ làm hỏng response của API vì việc ghi log phụ.
"""

from __future__ import annotations

import logging
from typing import Any

from app.db.session import AsyncSessionLocal
from app.repositories.api_request_logs import ApiRequestLogRepository

logger = logging.getLogger("api_request_log")


async def log_api_request(
    *,
    endpoint: str,
    method: str | None = None,
    client_ip: str | None = None,
    actor_id_nv: int | None = None,
    actor_id_pb: int | None = None,
    actor_id_dv: int | None = None,
    query: str | None = None,
    search_type: str | None = None,
    mode: str | None = None,
    used_vector: bool | None = None,
    result_total: int | None = None,
    duration_ms: int | None = None,
    status: str = "success",
    error: str | None = None,
    request_params: dict[str, Any] | None = None,
    response_summary: dict[str, Any] | None = None,
) -> None:
    """Ghi 1 dòng ``api_request_logs``. KHÔNG bao giờ raise (nuốt lỗi + cảnh báo)."""
    try:
        async with AsyncSessionLocal() as session:
            repo = ApiRequestLogRepository(session)
            await repo.create(
                endpoint=endpoint,
                method=method,
                client_ip=client_ip,
                actor_id_nv=actor_id_nv,
                actor_id_pb=actor_id_pb,
                actor_id_dv=actor_id_dv,
                query=query,
                search_type=search_type,
                mode=mode,
                used_vector=used_vector,
                result_total=result_total,
                duration_ms=duration_ms,
                status=status,
                error=(error[:2000] if error else None),
                request_params=request_params,
                response_summary=response_summary,
            )
            await session.commit()
    except Exception:  # noqa: BLE001 — ghi log phụ KHÔNG được làm hỏng API chính
        logger.warning("Ghi api_request_logs thất bại (endpoint=%s) — bỏ qua.", endpoint, exc_info=True)
