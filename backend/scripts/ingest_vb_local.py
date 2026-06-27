"""Ingest văn bản DOffice từ file JSON cục bộ (data/vb) để thử nghiệm phân quyền.

Các bước:
1. (tùy chọn) Dọn sạch Qdrant collection + Elasticsearch index của project.
2. Ingest văn bản từ file JSON (response ES thô của DOffice) qua đúng pipeline thật
   (chunk + embedding + index Qdrant/ES), TẮT enrichment & artifact (không gọi LLM).
3. Dựng ACL từ phong_ban_list + ca_nhan_list (hợp nhất các phiếu) -> resolve + compress
   theo danh mục -> gắn trường acl_* lên point Qdrant + lưu raw_assignment vào document.
4. Kiểm chứng: đếm số point một người TRONG danh sách nhận thấy được vs người NGOÀI.

Lưu ý: theo quyết định nghiệp vụ, ``don_vi_list`` chỉ là đơn vị nhận, KHÔNG cấp quyền
cho cả đơn vị; người xem = thành viên phòng ban nhận + cá nhân nhận.

    python -m scripts.ingest_vb_local
    python -m scripts.ingest_vb_local --no-clean
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from pathlib import Path
from typing import Any

import httpx

from app.core.config import settings
from app.db.session import AsyncSessionLocal, engine
from app.models.document import Document
from app.repositories.documents import DocumentRepository
from app.services.document_sources.document_source_doffice_elasticsearch_source import (
    DofficeDocument,
    DofficeDocumentNotFoundError,
    DofficeElasticsearchSource,
    _optional_int,
    _optional_string,
)
from app.services.chunkers.chunker_chunking_service import ChunkingService
from app.services.documents.document_storage import get_storage_client
from app.services.embeddings.embedding_sparse_factory import get_sparse_embedding_provider
from app.services.llm_gateway import get_llm_gateway
from app.services.ingestion.ingestion_doffice_content_normalizer import normalize_doffice_source
from app.services.ingestion.ingestion_doffice_ingestion_service import (
    DofficeIngestionService,
    DofficeIngestOptions,
)
from app.services.retrieval.retrieval_elasticsearch_keyword_search import (
    get_elasticsearch_keyword_store,
)
from app.services.security.security_acl_compressor import OrgCatalog
from app.services.security.security_acl_payload import (
    AclSubject,
    build_qdrant_acl_filter,
    subject_can_access,
    to_chunk_payload,
)
from app.services.security.security_acl_recompress import catalog_signature
from app.services.security.security_acl_resolver import (
    RawAssignment,
    UnitTree,
    build_assignment_from_doffice,
    resolve_and_compress,
)
from app.services.vector.vector_indexing_service import VectorIndexingService
from app.services.vector.vector_store import get_vector_store

logger = logging.getLogger("ingest_vb_local")

VB_DIR = Path(__file__).resolve().parent.parent / "data" / "vb"


class LocalJsonDofficeSource(DofficeElasticsearchSource):
    """Đọc DofficeDocument từ file JSON cục bộ thay vì gọi DOffice ES."""

    def __init__(self, files: dict[str, Path]) -> None:
        super().__init__()
        self._files = files

    async def fetch_document_by_id_vb(self, id_vb: str) -> DofficeDocument:
        clean_id = " ".join(str(id_vb or "").split()).strip()
        path = self._files.get(clean_id)
        if path is None:
            raise DofficeDocumentNotFoundError(f"Không có file cục bộ cho id_vb={clean_id}")
        data = json.loads(path.read_text(encoding="utf-8"))
        source = self._select_source(data, clean_id)
        raw_noi_dung = str(source.get("noi_dung") or "")
        normalized = normalize_doffice_source(source)
        return DofficeDocument(
            id_vb=str(source.get("id_vb") or clean_id),
            ky_hieu=_optional_string(source.get("ky_hieu")),
            trich_yeu=_optional_string(source.get("trich_yeu")),
            id_dv_ban_hanh=source.get("id_dv_ban_hanh"),
            noi_ban_hanh=_optional_string(source.get("noi_ban_hanh")),
            nguoi_ky=_optional_string(source.get("nguoi_ky")),
            ten_file=_optional_string(source.get("ten_file")),
            duong_dan=_optional_string(source.get("duong_dan")),
            ngay_vb=_optional_string(source.get("ngay_vb")),
            ngay_tao=_optional_string(source.get("ngay_tao")),
            ngay_capnhat=_optional_string(source.get("ngay_capnhat")),
            nam=_optional_int(source.get("nam")),
            thang=_optional_int(source.get("thang")),
            tom_tat=_optional_string(source.get("tom_tat")),
            raw_noi_dung=raw_noi_dung,
            clean_text=normalized.clean_text,
            raw_source=source,
        )


def _load_sources() -> tuple[dict[str, Path], dict[str, dict[str, set[int]]]]:
    """Quét data/vb -> map id_vb->file (cho nội dung) và gom audience hợp nhất theo id_vb.

    Ngữ nghĩa ACL DOffice (đã chốt): người nhận = ``ca_nhan_list`` (danh sách cá nhân
    cụ thể). ``phong_ban_list`` chỉ dùng làm dự phòng khi văn bản phát cho cả phòng mà
    không liệt kê cá nhân. ``don_vi_list`` là đơn vị nhận, KHÔNG cấp quyền.
    """
    files: dict[str, Path] = {}
    audience: dict[str, dict[str, set[int]]] = {}
    for path in sorted(VB_DIR.glob("*.json")):
        if path.name == "acl_preview.json":
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            continue  # bỏ qua file không phải response DOffice
        hits = data.get("hits", {}).get("hits", [])
        if not hits:
            continue
        src = hits[0].get("_source") or {}
        id_vb = str(src.get("id_vb"))
        files.setdefault(id_vb, path)  # file đầu tiên dùng làm nội dung
        bucket = audience.setdefault(id_vb, {"units": set(), "depts": set(), "users": set()})
        bucket["units"].update(int(x) for x in (src.get("don_vi_list") or []))
        bucket["depts"].update(int(x) for x in (src.get("phong_ban_list") or []))
        bucket["users"].update(int(x) for x in (src.get("ca_nhan_list") or []))
    return files, audience


async def _clean_stores(vector_store) -> None:
    logger.info("Dọn Qdrant collection %s ...", settings.qdrant_collection_name)
    await vector_store.recreate_collection()
    logger.info("Dọn Elasticsearch index %s ...", settings.elasticsearch_index_name)
    url = f"{settings.elasticsearch_url.rstrip('/')}/{settings.elasticsearch_index_name}"
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.delete(url)
            logger.info("  ES DELETE %s -> %s", url, resp.status_code)
    except httpx.HTTPError as exc:
        logger.warning("  ES delete bỏ qua: %s", exc)
    store = get_elasticsearch_keyword_store()
    if store is not None:
        await store.ensure_index()


def _build_service(session) -> DofficeIngestionService:
    repository = DocumentRepository(session)
    storage = get_storage_client()
    vector_store = get_vector_store()
    files, _ = _load_sources()
    vector_indexing_service = VectorIndexingService(
        repository=repository,
        llm_gateway=get_llm_gateway(),
        vector_store=vector_store,
        sparse_embedding_provider=get_sparse_embedding_provider(),
        keyword_index_store=(
            get_elasticsearch_keyword_store() if settings.elasticsearch_enabled else None
        ),
    )
    return DofficeIngestionService(
        repository=repository,
        source=LocalJsonDofficeSource(files),
        chunking_service=ChunkingService(repository=repository, storage=storage),
        vector_indexing_service=vector_indexing_service,
        vector_store=vector_store,
        enrichment_service=None,          # tắt LLM enrichment
        keyword_index_store=(
            get_elasticsearch_keyword_store() if settings.elasticsearch_enabled else None
        ),
        knowledge_artifact_repository=None,  # bỏ qua artifact (không gọi LLM)
        artifact_indexing_service=None,
    )


async def _attach_acl(
    session,
    vector_store,
    *,
    document_id,
    raw: RawAssignment,
    catalog: OrgCatalog,
    unit_tree: UnitTree,
) -> dict[str, Any]:
    acl = resolve_and_compress(raw, catalog, unit_tree=unit_tree)
    signature = catalog_signature(catalog)
    payload = to_chunk_payload(acl, version=signature)

    await vector_store.set_acl_payload_for_document(document_id, payload)

    document = await session.get(Document, document_id)
    meta = dict(document.document_metadata or {})
    access = dict(meta.get("access") or {})
    access["raw_assignment"] = raw.to_dict()
    access["acl"] = acl.to_dict()
    access["acl_ver"] = signature
    meta["access"] = access
    document.document_metadata = meta
    await session.commit()

    return {"acl": acl, "payload": payload, "catalog": catalog, "signature": signature}


async def _count_visible(vector_store, subject: AclSubject) -> int:
    flt = build_qdrant_acl_filter(subject)
    result = await vector_store._client.count(
        collection_name=vector_store.collection_name,
        count_filter=flt,
        exact=True,
    )
    return result.count


async def _find_document_id_by_id_vb(session, id_vb: str):
    from sqlalchemy import text

    row = (
        await session.execute(
            text(
                "select id from documents where document_metadata->>'id_vb' = :idvb "
                "order by created_at desc limit 1"
            ),
            {"idvb": id_vb},
        )
    ).first()
    return row[0] if row else None


async def _verify(session, vector_store, *, info: dict[str, Any], bucket: dict[str, set[int]]) -> None:
    acl = info["acl"]
    catalog: OrgCatalog = info["catalog"]
    payload = info["payload"]
    allowed_set = acl.decompress(catalog)

    # insider: một cá nhân trong ca_nhan_list.
    insider_id = next(iter(bucket["users"]), None) or next(iter(allowed_set))
    insider = await AclSubject.from_session(session, insider_id)
    # denied member: thành viên một phòng được allow nhưng bị deny (nếu có).
    denied_id = next(iter(acl.deny_user_ids), None)
    # outsider: người không nằm trong tập được phép.
    outsider_id = next(u for u in catalog.user_location if u not in allowed_set)
    outsider = await AclSubject.from_session(session, outsider_id)

    in_pts = await _count_visible(vector_store, insider)
    out_pts = await _count_visible(vector_store, outsider)
    logger.info(
        "  KIỂM CHỨNG insider nv=%s (pb=%s): can_access=%s, points=%d",
        insider.id_nv, insider.id_pb, subject_can_access(payload, insider), in_pts,
    )
    logger.info(
        "  KIỂM CHỨNG outsider nv=%s (pb=%s): can_access=%s, points=%d",
        outsider.id_nv, outsider.id_pb, subject_can_access(payload, outsider), out_pts,
    )
    if denied_id is not None:
        denied = await AclSubject.from_session(session, denied_id)
        d_pts = await _count_visible(vector_store, denied)
        logger.info(
            "  KIỂM CHỨNG denied nv=%s (pb=%s, thuộc phòng được allow nhưng bị deny): can_access=%s, points=%d",
            denied.id_nv, denied.id_pb, subject_can_access(payload, denied), d_pts,
        )


async def run(*, clean: bool, ingest: bool) -> None:
    files, audience = _load_sources()
    if not files:
        logger.error("Không tìm thấy file JSON trong %s", VB_DIR)
        return
    logger.info("Phát hiện %d văn bản: %s", len(files), list(files))

    vector_store = get_vector_store()
    if clean and ingest:
        await _clean_stores(vector_store)

    async with AsyncSessionLocal() as session:
        service = _build_service(session) if ingest else None
        catalog = await OrgCatalog.from_session(session)
        unit_tree = await UnitTree.from_session(session)
        for id_vb in files:
            if ingest:
                logger.info("=== Ingest id_vb=%s ===", id_vb)
                resp = await service.ingest_doffice_document(
                    id_vb,
                    DofficeIngestOptions(force_refresh=True, enable_enrichment=False),
                    uploaded_by_user_id=None,
                    organization_id=None,
                    knowledge_base_id=None,
                    access=None,
                )
                document_id = resp.document_id
                logger.info("  -> document_id=%s, chunks=%s", document_id, getattr(resp, "chunks_created", "?"))
            else:
                document_id = await _find_document_id_by_id_vb(session, id_vb)
                if document_id is None:
                    logger.error("Không tìm thấy document đã ingest cho id_vb=%s", id_vb)
                    continue
                logger.info("=== Gắn lại ACL cho id_vb=%s (document_id=%s) ===", id_vb, document_id)

            bucket = audience[id_vb]
            resolution = build_assignment_from_doffice(
                don_vi_list=bucket["units"],
                phong_ban_list=bucket["depts"],
                ca_nhan_list=bucket["users"],
                catalog=catalog,
            )
            logger.info(
                "  DOffice: đơn vị=%s, phòng=%s, cá nhân=%d người",
                sorted(bucket["units"]), sorted(bucket["depts"]), len(bucket["users"]),
            )
            for w in resolution.warnings:
                logger.warning("  [validate] %s", w)
            info = await _attach_acl(
                session, vector_store, document_id=document_id,
                raw=resolution.assignment, catalog=catalog, unit_tree=unit_tree,
            )
            acl = info["acl"]
            logger.info(
                "  ACL nén: allow_dv=%s allow_pb=%s allow_nv=%s deny_pb=%s deny_nv=%s (cost=%d, ver=%s)",
                acl.allow_unit_ids, acl.allow_department_ids, acl.allow_user_ids,
                acl.deny_department_ids, acl.deny_user_ids, acl.cost(), info["signature"],
            )
            await _verify(session, vector_store, info=info, bucket=bucket)

    await engine.dispose()
    logger.info("Hoàn tất.")


def cli() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Ingest văn bản DOffice từ JSON cục bộ + gắn ACL.")
    parser.add_argument("--no-clean", action="store_true", help="Không dọn Qdrant/ES trước khi ingest.")
    parser.add_argument(
        "--no-ingest",
        action="store_true",
        help="Bỏ qua ingest (không nhúng lại), chỉ gắn lại ACL cho document đã có + kiểm chứng.",
    )
    args = parser.parse_args()
    asyncio.run(run(clean=not args.no_clean, ingest=not args.no_ingest))


if __name__ == "__main__":
    cli()
