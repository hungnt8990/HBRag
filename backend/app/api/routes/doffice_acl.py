"""Route cho DOffice cập nhật ACL văn bản (PostgreSQL + Elasticsearch).

POST /api/doffice/acl/update — DOffice gọi mỗi khi quyền văn bản thay đổi. Route chỉ:
(1) chặn truy cập qua ``require_acl_update_access`` (chokepoint quyền — gắn phân quyền sau),
(2) gọi hàm lõi ``update_document_acl`` (thuần domain), (3) map exception -> HTTP status.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.dependencies.acl_update_auth import require_acl_update_access
from app.services.retrieval.document_acl_update_service import (
    AclUpdateError,
    AclUpdateRequest,
    AclUpdateResponse,
    DocumentNotFoundError,
    update_document_acl,
)

router = APIRouter(prefix="/api/doffice", tags=["doffice-acl"])


@router.post(
    "/acl/update",
    response_model=AclUpdateResponse,
    summary="DOffice cập nhật ACL 1 văn bản (PostgreSQL + Elasticsearch)",
    dependencies=[Depends(require_acl_update_access)],
)
async def update_acl(request: AclUpdateRequest) -> AclUpdateResponse:
    """Nhận id_vb + 3 list quyền (đơn vị/phòng ban/nhân viên) -> nén ACL -> cập nhật PG + ES.

    Văn bản phải đã được đồng bộ trước (job sync) — nếu chưa có trả 404.
    """
    try:
        return await update_document_acl(
            request.id_vb,
            don_vi_list=request.don_vi_list,
            phong_ban_list=request.phong_ban_list,
            ca_nhan_list=request.ca_nhan_list,
        )
    except DocumentNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except AclUpdateError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
