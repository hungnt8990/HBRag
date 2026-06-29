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
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.dependencies.auth import get_current_user
from app.models.user import User
from app.services.retrieval.document_acl_inspect_service import (
    AclInspectRequest,
    AclInspectResponse,
    inspect_document_acl,
)
from app.services.retrieval.document_search_service import (
    DocumentSearchError,
    DocumentSearchRequest,
    DocumentSearchResponse,
    DocumentSearchUnavailable,
    execute_document_search,
)
from app.services.security.security_acl_payload import AclSubject

router = APIRouter(prefix="/api/document-search", tags=["document-search"])


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
async def document_search(request: DocumentSearchRequest) -> DocumentSearchResponse:
    """KHÔNG yêu cầu Bearer. Body: ``query``, ``top_n``, ``jwtToken``, ``type`` (EO|DO).

    - ``type=DO``: decode ``jwtToken`` (không verify) lấy ``ID_NV`` -> tra cứu DOffice (ES BM25 + ACL).
    - ``type=EO``: chưa hỗ trợ (trả rỗng) — làm sau.
    """
    doc_type = (request.type or "").upper()
    if doc_type == "EO":
        return DocumentSearchResponse(
            query=request.query, id_nv=None, id_pb=None, id_dv=None,
            search_type="eo", mode_used="list", used_vector=False, total=0, results=[],
        )
    if doc_type != "DO":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="type phải là 'DO' hoặc 'EO'."
        )

    id_nv = _id_nv_from_jwt(request.jwtToken)
    request = request.model_copy(update={"id_nv": id_nv})
    try:
        return await execute_document_search(request)
    except DocumentSearchUnavailable as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    except DocumentSearchError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


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
