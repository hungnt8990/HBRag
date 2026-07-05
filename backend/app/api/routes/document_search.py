"""Route mỏng cho API tìm kiếm văn bản.

POST /api/document-search/search — logic ở ``document_search_service``; route chỉ:
(1) BẮT BUỘC đăng nhập (Bearer JWT) qua ``get_current_user`` — không có token -> 401,
(2) gọi service, (3) ánh xạ exception domain -> HTTP status.

Caller truyền identity (id_nv/id_pb/id_dv) qua body để LỌC QUYỀN ở ES; còn việc "ai được
gọi API này" do xác thực Bearer quyết định (thay cho X-API-Key tĩnh trước đây).
"""

from __future__ import annotations

import base64
import json
import time
from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import get_current_user
from app.db.session import get_db_session
from app.models.user import User
from app.repositories.api_request_logs import ApiRequestLogRepository
from app.services.api_request_log_service import log_api_request
from app.services.retrieval.document_acl_inspect_service import (
    AclInspectRequest,
    AclInspectResponse,
    inspect_document_acl,
)
from app.services.retrieval.document_chat_service import (
    ChatStreamEvent,
    DocumentChatRequest,
    stream_document_chat,
)
from app.services.retrieval.document_search_service import (
    DocumentSearchError,
    DocumentSearchRequest,
    DocumentSearchResponse,
    DocumentSearchUnavailable,
    execute_document_search,
    resolve_acl_subject,
)
from app.services.security.security_acl_payload import AclSubject

router = APIRouter(prefix="/api/document-search", tags=["document-search"])

_LOG_MAX_HITS = 50  # số hit tóm tắt lưu vào response_summary (JSONB) — đủ để soi, không phình


def _sanitize_params(request: DocumentSearchRequest) -> dict[str, Any]:
    """Tham số vào để LƯU log — bỏ ``jwtToken`` (nhạy cảm), thay bằng cờ ``has_jwt``."""
    data = request.model_dump()
    data.pop("jwtToken", None)
    data["has_jwt"] = bool(request.jwtToken)
    return data


def _summarize_response(resp: DocumentSearchResponse | None) -> dict[str, Any] | None:
    """Tóm tắt kết quả để lưu (JSONB): cờ tổng + danh sách hit gọn (KHÔNG kèm highlight/context nặng)."""
    if resp is None:
        return None
    return {
        "search_type": resp.search_type,
        "mode_used": resp.mode_used,
        "used_vector": resp.used_vector,
        "evidence_summary": resp.evidence_summary,
        "total": resp.total,
        "hits": [
            {
                "document_id": h.document_id,
                "id_vb": h.id_vb,
                "ky_hieu": h.ky_hieu,
                "trich_yeu": h.trich_yeu,
                "score": h.score,
                "rerank_score": h.rerank_score,
            }
            for h in resp.results[:_LOG_MAX_HITS]
        ],
    }


async def _log_search(
    *,
    request: DocumentSearchRequest,
    response: DocumentSearchResponse | None,
    fallback_id_nv: int | None,
    status_str: str,
    error: str | None,
    duration_ms: int,
    client_ip: str | None,
) -> None:
    """Gom dữ liệu 1 lời gọi ``/search`` -> ghi ``api_request_logs`` (không bao giờ raise)."""
    await log_api_request(
        endpoint="document-search/search",
        method="POST",
        client_ip=client_ip,
        actor_id_nv=(response.id_nv if response else fallback_id_nv),
        actor_id_pb=(response.id_pb if response else None),
        actor_id_dv=(response.id_dv if response else None),
        query=request.query,
        search_type=(response.search_type if response else None),
        mode=(response.mode_used if response else request.mode),
        used_vector=(response.used_vector if response else None),
        result_total=(response.total if response else None),
        duration_ms=duration_ms,
        status=status_str,
        error=error,
        request_params=_sanitize_params(request),
        response_summary=_summarize_response(response),
    )


def _id_nv_from_jwt(token: str | None) -> int:
    """Decode payload JWT (KHÔNG verify chữ ký) để lấy ``ID_NV``.

    Token do hệ thống NGOÀI cấp (vd iss=CPC, RS256) — mình không quản lý khóa ký, chỉ
    decode payload như jwt.io để lấy ``ID_NV`` rồi dùng làm id_nv lọc ACL.
    """
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Thiếu jwtToken.")
    parts = token.split(".")
    if len(parts) != 3:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="jwtToken không đúng định dạng JWT.")
    try:
        seg = parts[1] + "=" * (-len(parts[1]) % 4)
        payload = json.loads(base64.urlsafe_b64decode(seg).decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="jwtToken không decode được.") from exc
    raw = payload.get("ID_NV", payload.get("id_nv"))
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="jwtToken không chứa ID_NV hợp lệ."
        ) from exc


@router.post(
    "/search",
    response_model=DocumentSearchResponse,
    summary="Tìm kiếm văn bản theo type (DO = DOffice, decode jwtToken lấy ID_NV; EO = làm sau)",
)
async def document_search(request: DocumentSearchRequest, http_request: Request) -> DocumentSearchResponse:
    """KHÔNG yêu cầu Bearer. Body: ``query``, ``top_n``, ``jwtToken``, ``type`` (EO|DO).

    - ``type=DO``: decode ``jwtToken`` (không verify) lấy ``ID_NV`` -> tra cứu DOffice (ES BM25 + ACL).
    - ``type=EO``: chưa hỗ trợ (trả rỗng) — làm sau.

    MỌI lượt gọi (thành công lẫn lỗi) được ghi vào ``api_request_logs`` (ai hỏi, tham số, lọc
    kiểu gì, kết quả ra sao) qua ``_log_search`` ở ``finally`` — ghi log KHÔNG làm hỏng response.
    """
    started = time.monotonic()
    response: DocumentSearchResponse | None = None
    status_str = "success"
    error_msg: str | None = None
    id_nv: int | None = None
    try:
        doc_type = (request.type or "").upper()
        if doc_type == "EO":
            response = DocumentSearchResponse(
                query=request.query, id_nv=None, id_pb=None, id_dv=None,
                search_type="eo", mode_used="list", used_vector=False, total=0, results=[],
            )
            return response
        if doc_type != "DO":
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="type phải là 'DO' hoặc 'EO'."
            )

        id_nv = _id_nv_from_jwt(request.jwtToken)
        request = request.model_copy(update={"id_nv": id_nv})
        try:
            response = await execute_document_search(request)
            return response
        except DocumentSearchUnavailable as exc:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
        except DocumentSearchError as exc:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    except HTTPException as exc:
        status_str = "error"
        error_msg = f"HTTP {exc.status_code}: {exc.detail}"
        raise
    except Exception as exc:  # noqa: BLE001 — vẫn ghi log rồi re-raise
        status_str = "error"
        error_msg = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        await _log_search(
            request=request,
            response=response,
            fallback_id_nv=id_nv,
            status_str=status_str,
            error=error_msg,
            duration_ms=int((time.monotonic() - started) * 1000),
            client_ip=(http_request.client.host if http_request.client else None),
        )


def _format_chat_sse(event: ChatStreamEvent) -> str:
    """Định dạng 1 sự kiện SSE: ``event: <name>\\ndata: <json>\\n\\n``."""
    return f"event: {event.event}\ndata: {json.dumps(event.data, ensure_ascii=False)}\n\n"


@router.post(
    "/chat",
    summary="Chat (stream/SSE) hỏi-đáp trên văn bản — lọc theo danh sách văn bản hoặc toàn bộ ACL",
    description=(
        "Hỏi-đáp RAG có **stream** (Server-Sent Events) trên kho văn bản DOffice, dùng đúng luồng "
        "truy hồi của dự án (fusion: Qdrant dense + ES BM25 + RRF + rerank + CRAG) rồi LLM sinh câu "
        "trả lời grounded trên các đoạn đánh số `[i]`.\n\n"
        "**Phạm vi văn bản** (`document_ids`):\n"
        "- Có danh sách `document_id` → CHỈ lọc & hỏi trên nội dung nhóm văn bản đó.\n"
        "- Rỗng/`null` → hỏi trên **TOÀN BỘ** văn bản người dùng được ACL cho phép.\n\n"
        "Người hỏi lấy từ `jwtToken` (decode `ID_NV`, không verify chữ ký) → quyền ACL luôn được áp.\n\n"
        "**Luồng SSE** (`text/event-stream`), các `event`:\n"
        "- `meta`  : phạm vi truy hồi (`documents`|`all`).\n"
        "- `sources`: danh sách nguồn `[i]` (document_id, ký hiệu, trích yếu, điểm...).\n"
        "- `delta` : từng đoạn văn bản trả lời (`{\"text\": \"...\"}`) — ghép lại thành câu trả lời.\n"
        "- `done`  : câu trả lời đầy đủ + `evidence_summary` + tổng nguồn.\n"
        "- `error` : thông điệp lỗi (nếu có)."
    ),
    response_class=StreamingResponse,
)
async def document_chat(request: DocumentChatRequest, http_request: Request) -> StreamingResponse:
    """Stream câu trả lời RAG. Body: `query`, `jwtToken`, `type=DO`, `document_ids` (list|null), `top_n`.

    Mọi lượt được ghi vào `api_request_logs` (endpoint `document-search/chat`) ở cuối stream.
    """
    started = time.monotonic()
    doc_type = (request.type or "DO").upper()
    if doc_type != "DO":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="type phải là 'DO' (EO chưa hỗ trợ cho chat).",
        )
    # Parse ID_NV + resolve ACL TRƯỚC khi stream (lỗi token -> trả 401/403 sạch, không mở stream).
    id_nv = _id_nv_from_jwt(request.jwtToken)
    acl_subject = await resolve_acl_subject(id_nv)
    scope_ids = {str(d).strip() for d in (request.document_ids or []) if str(d).strip()} or None
    client_ip = http_request.client.host if http_request.client else None

    async def event_stream() -> Any:
        status_str = "success"
        error_msg: str | None = None
        total_sources = 0
        answer_chars = 0
        evidence: str | None = None
        used_vector: bool | None = None
        try:
            async for ev in stream_document_chat(
                query=request.query,
                acl_subject=acl_subject,
                document_ids=scope_ids,
                top_n=request.top_n,
                answer_mode=request.answer_mode,
                answer_style=request.answer_style,
            ):
                if ev.event == "done":
                    total_sources = int(ev.data.get("total_sources") or 0)
                    answer_chars = len(ev.data.get("answer") or "")
                    evidence = ev.data.get("evidence_summary")
                    used_vector = ev.data.get("used_vector")
                elif ev.event == "error":
                    status_str = "error"
                    error_msg = ev.data.get("message")
                yield _format_chat_sse(ev)
        except Exception as exc:  # noqa: BLE001 — luôn đóng stream sạch + ghi log
            status_str = "error"
            error_msg = f"{type(exc).__name__}: {exc}"
            yield _format_chat_sse(ChatStreamEvent("error", {"message": "Lỗi hệ thống."}))
        finally:
            await log_api_request(
                endpoint="document-search/chat",
                method="POST",
                client_ip=client_ip,
                actor_id_nv=id_nv,
                actor_id_pb=acl_subject.id_pb,
                actor_id_dv=acl_subject.id_dv,
                query=request.query,
                search_type="chat",
                mode="stream",
                used_vector=used_vector,
                result_total=total_sources,
                duration_ms=int((time.monotonic() - started) * 1000),
                status=status_str,
                error=error_msg,
                request_params={
                    "top_n": request.top_n,
                    "type": doc_type,
                    "has_jwt": bool(request.jwtToken),
                    "scope": "documents" if scope_ids else "all",
                    "document_ids": sorted(scope_ids) if scope_ids else None,
                    "answer_mode": request.answer_mode,
                    "answer_style": request.answer_style,
                },
                response_summary={
                    "total_sources": total_sources,
                    "answer_chars": answer_chars,
                    "evidence_summary": evidence,
                    "used_vector": used_vector,
                },
            )

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post(
    "/acl",
    response_model=AclInspectResponse,
    summary="Soi quyền 1 văn bản (ES hoặc Postgres) để kiểm chứng",
)
async def inspect_acl(
    request: AclInspectRequest,
    current_user: Annotated[User, Depends(get_current_user)],
) -> AclInspectResponse:
    """Trả ACL của văn bản (acl_subjects + deny) từ ``source`` (es | postgres).

    Truyền thêm ``id_nv`` (kèm id_pb/id_dv) trong body -> tính luôn người đó CÓ xem được không.
    """
    subject = (
        AclSubject(id_nv=request.id_nv, id_pb=request.id_pb, id_dv=request.id_dv, is_super_admin=False)
        if request.id_nv is not None
        else None
    )
    try:
        return await inspect_document_acl(request.id_vb, source=request.source, subject=subject)
    except DocumentSearchError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


# ────────────────────────── Đọc log lời gọi API (api_request_logs) ──────────────────────────


class ApiRequestLogItem(BaseModel):
    """Một dòng log lời gọi API tra cứu."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    created_at: datetime = Field(description="Thời điểm gọi (UTC, có timezone)")
    endpoint: str = Field(description="Định danh API, vd 'document-search/search'")
    method: str | None = Field(default=None, description="HTTP method")
    client_ip: str | None = Field(default=None, description="IP client (nếu lấy được)")
    actor_id_nv: int | None = Field(default=None, description="ID nhân viên (người hỏi)")
    actor_id_pb: int | None = Field(default=None, description="ID phòng ban (resolve từ danh mục)")
    actor_id_dv: int | None = Field(default=None, description="ID đơn vị (resolve từ danh mục)")
    query: str | None = Field(default=None, description="Nội dung tra cứu")
    search_type: str | None = Field(default=None, description="Kiểu tra cứu: exact|ref|bm25|hybrid|fusion")
    mode: str | None = Field(default=None, description="Chế độ: list|excerpt")
    used_vector: bool | None = Field(default=None, description="Có dùng vector/semantic không")
    result_total: int | None = Field(default=None, description="Số kết quả trả về")
    duration_ms: int | None = Field(default=None, description="Thời gian xử lý (ms)")
    status: str = Field(description="success | error")
    error: str | None = Field(default=None, description="Thông điệp lỗi (nếu status=error)")
    request_params: dict[str, Any] | None = Field(
        default=None, description="Toàn bộ tham số vào (đã bỏ jwtToken) — chỉ có khi include_payload=true"
    )
    response_summary: dict[str, Any] | None = Field(
        default=None, description="Tóm tắt kết quả (cờ tổng + hit gọn) — chỉ có khi include_payload=true"
    )


class ApiRequestLogPage(BaseModel):
    """Trang kết quả log (phân trang)."""

    total: int = Field(description="Tổng số bản ghi KHỚP bộ lọc (bỏ phân trang)")
    limit: int = Field(description="Số bản ghi tối đa mỗi trang")
    offset: int = Field(description="Vị trí bắt đầu (số bản ghi bỏ qua)")
    items: list[ApiRequestLogItem]


@router.get(
    "/logs",
    response_model=ApiRequestLogPage,
    summary="Xem lịch sử lời gọi API tra cứu (ai hỏi, tham số, lọc kiểu gì, kết quả ra sao)",
    description=(
        "Đọc bảng `api_request_logs` — mỗi lượt gọi `/api/document-search/search` (kể cả lỗi) "
        "được ghi 1 dòng. Hỗ trợ LỌC ĐỘNG + phân trang.\n\n"
        "**Yêu cầu:** đăng nhập Bearer JWT (đây là dữ liệu vận hành nhạy cảm).\n\n"
        "**Bộ lọc** (bỏ trống = không lọc theo tiêu chí đó): `id_nv` (người hỏi), `search_type` "
        "(exact|ref|bm25|hybrid|fusion), `status` (success|error), `q` (chứa chuỗi trong nội dung "
        "tra cứu, không phân biệt hoa/thường), `endpoint` (mặc định 'document-search/search'; truyền "
        "rỗng để xem MỌI endpoint), `created_from`/`created_to` (khoảng thời gian ISO-8601).\n\n"
        "**Payload nặng** (`request_params`/`response_summary`) chỉ trả khi `include_payload=true` "
        "để danh sách gọn; sắp xếp mới nhất trước."
    ),
)
async def list_search_logs(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
    id_nv: Annotated[int | None, Query(description="Lọc theo ID nhân viên (người hỏi)")] = None,
    search_type: Annotated[
        str | None, Query(description="Lọc theo kiểu tra cứu: exact|ref|bm25|hybrid|fusion")
    ] = None,
    status_filter: Annotated[
        str | None, Query(alias="status", description="Lọc theo trạng thái: success | error")
    ] = None,
    q: Annotated[
        str | None, Query(description="Lọc nội dung tra cứu CHỨA chuỗi (không phân biệt hoa/thường)")
    ] = None,
    endpoint: Annotated[
        str | None,
        Query(description="Lọc theo endpoint (mặc định 'document-search/search'; rỗng = MỌI endpoint)"),
    ] = "document-search/search",
    created_from: Annotated[
        datetime | None, Query(description="Chỉ lấy log TỪ thời điểm này (ISO-8601)")
    ] = None,
    created_to: Annotated[
        datetime | None, Query(description="Chỉ lấy log ĐẾN thời điểm này (ISO-8601)")
    ] = None,
    include_payload: Annotated[
        bool, Query(description="true = kèm request_params/response_summary (nặng hơn)")
    ] = False,
    limit: Annotated[int, Query(ge=1, le=200, description="Số bản ghi mỗi trang (1–200)")] = 50,
    offset: Annotated[int, Query(ge=0, description="Số bản ghi bỏ qua (phân trang)")] = 0,
) -> ApiRequestLogPage:
    """Truy vấn log có phân trang. Trả `total` (tổng khớp bộ lọc) để client tính số trang."""
    rows, total = await ApiRequestLogRepository(session).list_logs(
        endpoint=endpoint,
        actor_id_nv=id_nv,
        search_type=search_type,
        status=status_filter,
        query_contains=q,
        created_from=created_from,
        created_to=created_to,
        limit=limit,
        offset=offset,
    )
    items: list[ApiRequestLogItem] = []
    for row in rows:
        item = ApiRequestLogItem.model_validate(row)
        if not include_payload:
            item.request_params = None
            item.response_summary = None
        items.append(item)
    return ApiRequestLogPage(total=total, limit=limit, offset=offset, items=items)
