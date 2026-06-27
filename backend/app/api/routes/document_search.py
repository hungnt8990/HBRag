"""Route mỏng cho API tìm kiếm văn bản.

POST /api/document-search/search — logic ở ``document_search_service``; route chỉ:
(1) chặn truy cập qua ``require_document_search_access`` (chokepoint quyền),
(2) gọi service, (3) ánh xạ exception domain -> HTTP status.

Caller tự truyền identity (id_nv/id_pb/id_dv) qua body để LỌC QUYỀN ở ES; còn việc
"ai được gọi API này" do dependency xác thực ở trên quyết định.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.dependencies.document_search_auth import require_document_search_access
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


@router.post(
    "/search",
    response_model=DocumentSearchResponse,
    summary="Tìm kiếm văn bản (exact / BM25 / hybrid kNN+BM25 + ACL)",
    dependencies=[Depends(require_document_search_access)],
)
async def document_search(request: DocumentSearchRequest) -> DocumentSearchResponse:
    """Tự phát hiện: số ký hiệu -> exact; từ khoá ngắn -> BM25; câu hỏi -> hybrid.
    ACL filter chỉ trả văn bản id_nv/id_pb/id_dv có quyền xem.
    """
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
    dependencies=[Depends(require_document_search_access)],
)
async def inspect_acl(request: AclInspectRequest) -> AclInspectResponse:
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
