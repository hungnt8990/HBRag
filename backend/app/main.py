import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes.admin import router as admin_router
from app.api.routes.architecture import router as architecture_router
from app.api.routes.auth import router as auth_router
from app.api.routes.chat import router as chat_router
from app.api.routes.collab import router as collab_router
from app.api.routes.document_search import router as document_search_router
from app.api.routes.documents import router as documents_router
from app.api.routes.doffice_acl import router as doffice_acl_router
from app.api.routes.health import router as health_router
from app.api.routes.knowledge_bases import router as knowledge_bases_router
from app.api.routes.memory import router as memory_router
from app.api.routes.search import router as search_router
from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.repositories.ingestion_profiles import IngestionProfileRepository
from app.repositories.rag_runtime_config import RagRuntimeConfigRepository
from app.services.collab import diagram_collab
from app.services.graph import get_neo4j_client
from app.services.ingestion.ingestion_profiles import load_profile_configs
from app.services.rag.rag_runtime_config import load_rag_runtime_config
from app.services.vector.vector_store import get_vector_store

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    await _load_ingestion_profiles_on_startup()
    await _load_rag_runtime_config_on_startup()
    await _validate_vector_store_on_startup()
    await _validate_graph_store_on_startup()
    try:
        await diagram_collab.start()
    except Exception:
        logger.exception("Failed to start diagram collaboration server.")
    try:
        yield
    finally:
        try:
            await diagram_collab.stop()
        except Exception:
            logger.exception("Failed to stop diagram collaboration server.")
        try:
            await get_neo4j_client().close()
        except Exception:
            logger.exception("Failed to close Neo4j driver on shutdown.")

async def _load_ingestion_profiles_on_startup() -> None:
    try:
        async with AsyncSessionLocal() as session:
            repository = IngestionProfileRepository(session)
            await load_profile_configs(repository)
            await repository.commit()
    except Exception:
        logger.exception("Failed to load ingestion profile configs from Postgres.")

async def _load_rag_runtime_config_on_startup() -> None:
    try:
        async with AsyncSessionLocal() as session:
            repository = RagRuntimeConfigRepository(session)
            await load_rag_runtime_config(repository)
            await repository.commit()
    except Exception:
        logger.exception("Failed to load RAG runtime config from Postgres.")


async def _validate_vector_store_on_startup() -> None:
    # Dự án chỉ dùng DOffice (2 collection riêng tự ensure_collection). Bỏ qua validate
    # collection RAG generic -> KHÔNG tự tạo lại hbrag_chunks_qwen3_8b_v1 rỗng khi startup.
    if not settings.validate_generic_vector_store_on_startup:
        logger.info("Bỏ qua validate Qdrant collection generic khi startup (chỉ dùng DOffice).")
        return
    try:
        collection_info = await get_vector_store().validate_collection_config(
            auto_recreate=settings.auto_recreate_collection,
        )
    except Exception:
        logger.exception("Failed to validate Qdrant collection configuration on startup.")
        return

    logger.info(
        "Qdrant vector store startup: collection=%s vector_size=%s expected_vector_size=%s "
        "distance=%s",
        collection_info.collection_name,
        collection_info.vector_size,
        collection_info.expected_vector_size,
        collection_info.distance,
    )


async def _validate_graph_store_on_startup() -> None:
    if not settings.graph_enabled:
        return
    try:
        await get_neo4j_client().verify_connectivity()
    except Exception:
        logger.exception("Failed to verify Neo4j connectivity on startup.")
        return

    logger.info(
        "Neo4j graph store startup: provider=%s uri=%s",
        settings.graph_provider,
        settings.neo4j_uri,
    )


API_DESCRIPTION = """
**HBRag** — API hỏi-đáp RAG trên kho văn bản hành chính EVNCPC (DOffice) với phân quyền theo
đơn vị/phòng ban/cá nhân (ACL).

### Xác thực
Hầu hết API yêu cầu **Bearer JWT**. Lấy token qua `POST /api/auth/login`, rồi bấm **Authorize**
(góc trên phải) và dán `Bearer <token>`.

### Nhóm API
- **documents**: upload, parse, chunk, index, xóa, tra cứu chunk + metadata Qdrant của văn bản.
- **search / document-search**: tìm kiếm hybrid/two-stage (gồm retrieval DOffice 3-DB khi bật cờ).
- **chat**: hỏi-đáp RAG (đồng bộ + streaming) có trích dẫn nguồn.
- **doffice-acl**: cập nhật/đồng bộ ACL văn bản DOffice theo `id_vb`.
- **knowledge-bases / memory / admin / auth / health**: quản trị, bộ nhớ hội thoại, xác thực, sức khỏe.
"""

OPENAPI_TAGS = [
    {"name": "auth", "description": "Đăng nhập, đăng ký, làm mới token (Bearer JWT)."},
    {"name": "documents", "description": "Quản lý văn bản: upload, parse, chunk, index, xóa, tra cứu chunk + metadata Qdrant."},
    {"name": "search", "description": "Tìm kiếm hybrid/two-stage; gồm retrieval DOffice 3-DB khi bật `DOFFICE_RETRIEVAL_ENABLED`."},
    {"name": "document-search", "description": "Tìm văn bản DOffice ở mức tài liệu (Stage-1) phục vụ kiểm thử/ACL."},
    {"name": "chat", "description": "Hỏi-đáp RAG (đồng bộ + streaming), có trích dẫn nguồn và bộ nhớ hội thoại."},
    {"name": "doffice-acl", "description": "Cập nhật/đồng bộ ACL (allow/deny) cho văn bản DOffice theo id_vb."},
    {"name": "knowledge-bases", "description": "Quản lý kho tri thức (knowledge base) và phân quyền."},
    {"name": "memory", "description": "Bộ nhớ hội thoại phục vụ chat (ghi nhớ/truy hồi ngữ cảnh người dùng)."},
    {"name": "admin", "description": "Quản trị hệ thống: người dùng, vai trò, đơn vị, cấu hình RAG runtime."},
    {"name": "health", "description": "Kiểm tra tình trạng dịch vụ (liveness/readiness)."},
]


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description=API_DESCRIPTION,
        openapi_tags=OPENAPI_TAGS,
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allowed_origins,
        allow_origin_regex=settings.cors_allowed_origin_regex,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(admin_router)
    app.include_router(architecture_router)
    app.include_router(auth_router)
    app.include_router(chat_router)
    app.include_router(collab_router)
    app.include_router(documents_router)
    app.include_router(document_search_router)
    app.include_router(doffice_acl_router)
    app.include_router(health_router)
    app.include_router(knowledge_bases_router)
    app.include_router(memory_router)
    app.include_router(search_router)
    return app


app = create_app()
