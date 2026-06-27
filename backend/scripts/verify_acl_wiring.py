"""Kiểm tra wiring ACL: DofficeIngestionService TỰ gắn acl_* khi ingest (Qdrant + ES),
và filter phía query lọc đúng (insider thấy / outsider 0).

    python -m scripts.verify_acl_wiring
"""

from __future__ import annotations

import asyncio
import logging

import httpx

from app.core.config import settings
from app.db.session import AsyncSessionLocal, engine
from app.services.ingestion.ingestion_doffice_ingestion_service import DofficeIngestOptions
from app.services.security.security_acl_compressor import OrgCatalog
from app.services.security.security_acl_payload import AclSubject, build_qdrant_acl_filter
from app.services.security.security_acl_resolver import UnitTree, resolve_doffice_and_compress
from app.services.vector.vector_store import get_vector_store
from scripts.ingest_vb_local import _build_service, _clean_stores, _load_sources

logger = logging.getLogger("verify_acl_wiring")

ACL_KEYS = ("acl_allow_dv", "acl_allow_pb", "acl_allow_nv", "acl_deny_pb", "acl_deny_nv")
ID_VB = "1459570"


async def main() -> None:
    files, audience = _load_sources()
    vs = get_vector_store()
    await _clean_stores(vs)

    async with AsyncSessionLocal() as session:
        service = _build_service(session)
        logger.info("Ingest id_vb=%s qua DofficeIngestionService (tự gắn ACL)...", ID_VB)
        resp = await service.ingest_doffice_document(
            ID_VB,
            DofficeIngestOptions(force_refresh=True, enable_enrichment=False),
            uploaded_by_user_id=None,
            organization_id=None,
            knowledge_base_id=None,
            access=None,
        )
        doc_id = resp.document_id
        logger.info("document_id=%s chunks=%s", doc_id, getattr(resp, "chunks_created", "?"))

        # 1) Qdrant: point có acl_* do SERVICE gắn không?
        points, _ = await vs._client.scroll(
            collection_name=vs.collection_name, limit=1, with_payload=True
        )
        qpayload = points[0].payload if points else {}
        logger.info("ACL trên point Qdrant: %s", {k: qpayload.get(k) for k in ACL_KEYS})

        # 2) Elasticsearch: doc có acl_* không?
        es_acl = {}
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                f"{settings.elasticsearch_url.rstrip('/')}/{settings.elasticsearch_index_name}/_search",
                json={"size": 1, "query": {"term": {"document_id": str(doc_id)}}},
            )
            hits = r.json().get("hits", {}).get("hits", [])
            if hits:
                src = hits[0]["_source"]
                es_acl = {k: src.get(k) for k in ACL_KEYS}
        logger.info("ACL trên doc Elasticsearch: %s", es_acl)

        # 3) Filter insider/outsider trên Qdrant
        catalog = await OrgCatalog.from_session(session)
        unit_tree = await UnitTree.from_session(session)
        b = audience[ID_VB]
        acl, _a, _w = resolve_doffice_and_compress(
            don_vi_list=b["units"], phong_ban_list=b["depts"], ca_nhan_list=b["users"],
            catalog=catalog, unit_tree=unit_tree,
        )
        allowed = acl.decompress(catalog)
        insider_id = next(iter(allowed))
        outsider_id = next(u for u in catalog.user_location if u not in allowed)
        insider = await AclSubject.from_session(session, insider_id)
        outsider = await AclSubject.from_session(session, outsider_id)
        for name, subj in (("insider", insider), ("outsider", outsider)):
            cnt = await vs._client.count(
                collection_name=vs.collection_name,
                count_filter=build_qdrant_acl_filter(subj),
                exact=True,
            )
            logger.info("%s nv=%s (pb=%s) -> points=%d", name, subj.id_nv, subj.id_pb, cnt.count)

    await engine.dispose()
    logger.info("XONG.")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(main())
