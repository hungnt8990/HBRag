"""Soi quyền (ACL) của 1 văn bản để kiểm chứng — từ ES document index hoặc Postgres.

- ES: trả ``acl_subjects``/``acl_deny_*`` đang dùng để LỌC khi tìm kiếm.
- Postgres (nguồn sự thật): trả block ``access`` (acl nén + raw_assignment 3 list DOffice
  + quyen_checksum) và tính lại ``acl_subjects`` từ đó.

Tuỳ chọn truyền ``subject`` (id_nv/id_pb/id_dv) -> tính luôn người đó CÓ xem được không
(đúng logic filter: khớp 1 key allow VÀ không bị deny).
"""

from __future__ import annotations

import logging
from typing import Any, Literal

import httpx
from pydantic import BaseModel, Field
from sqlalchemy import String, cast, select

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.models.document import Document
from app.services.retrieval.document_search_service import DocumentSearchError
from app.services.retrieval.retrieval_document_index import DocumentIndexStore
from app.services.security.security_acl_compressor import CompressedAcl
from app.services.security.security_acl_payload import AclSubject, acl_keys_from_acl, acl_subject_to_keys

logger = logging.getLogger("document_acl_inspect")
DOFFICE_SOURCE_TYPE = "doffice_elasticsearch"


class AclInspectRequest(BaseModel):
    id_vb: str = Field(description="Mã văn bản cần soi quyền")
    source: Literal["es", "postgres"] = Field(default="es", description="Nguồn: es | postgres")
    id_nv: int | None = Field(default=None, description="Mã nhân viên (để kiểm tra người này có xem được không)")
    id_pb: int | None = Field(default=None, description="Mã phòng ban")
    id_dv: int | None = Field(default=None, description="Mã đơn vị")


class AclSubjectCheck(BaseModel):
    id_nv: int
    id_pb: int | None
    id_dv: int | None
    subject_keys: list[str]
    matched_keys: list[str]
    denied_by_nv: bool
    denied_by_pb: bool
    allowed: bool
    reason: str


class AclInspectResponse(BaseModel):
    id_vb: str
    source: str  # es | postgres
    found: bool
    document_id: str | None = None
    ky_hieu: str | None = None
    trich_yeu: str | None = None
    acl_subjects: list[str] = Field(default_factory=list)
    acl_deny_pb: list[int] = Field(default_factory=list)
    acl_deny_nv: list[int] = Field(default_factory=list)
    raw: dict | None = None
    subject_check: AclSubjectCheck | None = None


def _check_subject(
    acl_subjects: list[str], acl_deny_pb: list[int], acl_deny_nv: list[int], subject: AclSubject
) -> AclSubjectCheck:
    keys = acl_subject_to_keys(subject)
    matched = sorted(set(keys) & set(acl_subjects))
    denied_nv = subject.id_nv in (acl_deny_nv or [])
    denied_pb = subject.id_pb is not None and subject.id_pb in (acl_deny_pb or [])
    allowed = bool(matched) and not denied_nv and not denied_pb
    if not matched:
        reason = "Không có key allow nào khớp (không thuộc đơn vị/phòng ban/cá nhân được cấp)."
    elif denied_nv:
        reason = f"Bị chặn: id_nv={subject.id_nv} nằm trong acl_deny_nv."
    elif denied_pb:
        reason = f"Bị chặn: id_pb={subject.id_pb} nằm trong acl_deny_pb."
    else:
        reason = f"Được xem: khớp {matched}."
    return AclSubjectCheck(
        id_nv=subject.id_nv, id_pb=subject.id_pb, id_dv=subject.id_dv,
        subject_keys=keys, matched_keys=matched,
        denied_by_nv=denied_nv, denied_by_pb=denied_pb, allowed=allowed, reason=reason,
    )


async def _from_es(id_vb: str) -> dict[str, Any] | None:
    store = DocumentIndexStore(url=settings.two_stage_document_index_url or settings.elasticsearch_url)
    body = {
        "size": 1,
        "_source": [
            "document_id", "id_vb", "ky_hieu", "trich_yeu",
            "acl_subjects", "acl_deny_pb", "acl_deny_nv",
        ],
        "query": {"term": {"id_vb": str(id_vb)}},
    }
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(f"{store.url}/{store.index_name}/_search", json=body)
        if resp.status_code == 404:
            return None
        if resp.status_code >= 400:
            raise DocumentSearchError(f"ES lỗi HTTP {resp.status_code}: {resp.text[:200]}")
        hits = resp.json().get("hits", {}).get("hits", [])
    except DocumentSearchError:
        raise
    except Exception as exc:
        raise DocumentSearchError(f"Lỗi kết nối ES: {exc}") from exc
    if not hits:
        return None
    src = hits[0].get("_source") or {}
    return {
        "document_id": src.get("document_id"),
        "ky_hieu": src.get("ky_hieu"),
        "trich_yeu": src.get("trich_yeu"),
        "acl_subjects": list(src.get("acl_subjects") or []),
        "acl_deny_pb": list(src.get("acl_deny_pb") or []),
        "acl_deny_nv": list(src.get("acl_deny_nv") or []),
        "raw": {k: src.get(k) for k in ("acl_subjects", "acl_deny_pb", "acl_deny_nv")},
    }


async def _from_postgres(id_vb: str) -> dict[str, Any] | None:
    id_vb_col = cast(Document.document_metadata["id_vb"].astext, String)
    async with AsyncSessionLocal() as session:
        doc = (
            await session.execute(
                select(Document).where(
                    Document.source_type == DOFFICE_SOURCE_TYPE, id_vb_col == str(id_vb)
                )
            )
        ).scalars().first()
    if doc is None:
        return None
    meta = doc.document_metadata or {}
    access = meta.get("access") or {}
    acl = access.get("acl") or {}
    compressed = CompressedAcl(
        allow_unit_ids=list(acl.get("allow_unit_ids", [])),
        allow_department_ids=list(acl.get("allow_department_ids", [])),
        allow_user_ids=list(acl.get("allow_user_ids", [])),
        deny_department_ids=list(acl.get("deny_department_ids", [])),
        deny_user_ids=list(acl.get("deny_user_ids", [])),
    )
    return {
        "document_id": str(doc.id),
        "ky_hieu": meta.get("ky_hieu"),
        "trich_yeu": meta.get("trich_yeu"),
        "acl_subjects": acl_keys_from_acl(compressed),
        "acl_deny_pb": sorted(compressed.deny_department_ids),
        "acl_deny_nv": sorted(compressed.deny_user_ids),
        "raw": access,  # nguồn sự thật: acl + raw_assignment + quyen_checksum
    }


async def inspect_document_acl(
    id_vb: str, *, source: Literal["es", "postgres"] = "es", subject: AclSubject | None = None
) -> AclInspectResponse:
    data = await (_from_postgres(id_vb) if source == "postgres" else _from_es(id_vb))
    if data is None:
        return AclInspectResponse(id_vb=str(id_vb), source=source, found=False)
    check = None
    if subject is not None:
        check = _check_subject(data["acl_subjects"], data["acl_deny_pb"], data["acl_deny_nv"], subject)
    return AclInspectResponse(
        id_vb=str(id_vb), source=source, found=True,
        document_id=data.get("document_id"), ky_hieu=data.get("ky_hieu"),
        trich_yeu=data.get("trich_yeu"), acl_subjects=data["acl_subjects"],
        acl_deny_pb=data["acl_deny_pb"], acl_deny_nv=data["acl_deny_nv"],
        raw=data.get("raw"), subject_check=check,
    )
