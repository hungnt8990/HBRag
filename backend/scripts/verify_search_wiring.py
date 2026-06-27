"""Kiểm tra wiring FILTER phía search: user (qua User.id_nv) -> AclSubject -> lọc Qdrant + ES.

Giả định đã ingest sẵn 1 văn bản có acl_* (chạy verify_acl_wiring trước).
Gán tạm id_nv cho user 'admin' để test insider/outsider.

    python -m scripts.verify_search_wiring
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy import select, text

from app.core.config import settings
from app.db.session import AsyncSessionLocal, engine
from app.models.user import User
from app.services.embeddings.embedding_factory import get_embedding_provider
from app.services.retrieval.retrieval_elasticsearch_keyword_search import (
    ElasticsearchKeywordSearchService,
    get_elasticsearch_keyword_store,
)
from app.services.security.security_acl_payload import AclSubject
from app.services.vector.vector_store import get_vector_store

logger = logging.getLogger("verify_search_wiring")

INSIDER = 118786   # phòng 43310 (được phép)
OUTSIDER = 116642  # phòng 42965 (ngoài)
QUERY = "phần mềm hệ thống"


async def main() -> None:
    vs = get_vector_store()
    es_service = ElasticsearchKeywordSearchService(store=get_elasticsearch_keyword_store())
    embedder = get_embedding_provider()
    qvec = await embedder.embed_query(QUERY)

    async with AsyncSessionLocal() as session:
        # 1) Gán tạm id_nv cho 'admin' rồi test from_app_user
        await session.execute(text("update users set id_nv=:n where username='admin'"), {"n": INSIDER})
        await session.commit()
        admin = (await session.execute(select(User).where(User.username == "admin"))).scalar_one()
        subj_admin = await AclSubject.from_app_user(
            session, admin, super_admin_roles=set(settings.permission_admin_roles or [])
        )
        logger.info("from_app_user(admin id_nv=%s) -> %s", admin.id_nv, subj_admin)

        insider = await AclSubject.from_session(session, INSIDER)
        outsider = await AclSubject.from_session(session, OUTSIDER)

        for name, subj in (("insider", insider), ("outsider", outsider)):
            # Qdrant qua search() (đường wiring thật)
            vres = await vs.search(query_vector=qvec, top_k=100, acl_subject=subj)
            # ES qua service.search() (đường wiring thật)
            eres = await es_service.search(query=QUERY, top_k=100, acl_subject=subj)
            logger.info(
                "%s nv=%s (pb=%s): Qdrant=%d kết quả | ES=%d kết quả",
                name, subj.id_nv, subj.id_pb, len(vres), len(eres.results),
            )

        # khôi phục
        await session.execute(text("update users set id_nv=NULL where username='admin'"))
        await session.commit()

    await engine.dispose()
    logger.info("XONG.")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(main())
