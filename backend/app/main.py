import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes.admin import router as admin_router
from app.api.routes.auth import router as auth_router
from app.api.routes.chat import router as chat_router
from app.api.routes.documents import router as documents_router
from app.api.routes.health import router as health_router
from app.api.routes.knowledge_bases import router as knowledge_bases_router
from app.api.routes.memory import router as memory_router
from app.api.routes.search import router as search_router
from app.core.config import settings
from app.services.graph import get_neo4j_client
from app.services.vector_store import get_vector_store

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    await _validate_vector_store_on_startup()
    await _validate_graph_store_on_startup()
    try:
        yield
    finally:
        try:
            await get_neo4j_client().close()
        except Exception:
            logger.exception("Failed to close Neo4j driver on shutdown.")


async def _validate_vector_store_on_startup() -> None:
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


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
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
    app.include_router(auth_router)
    app.include_router(chat_router)
    app.include_router(documents_router)
    app.include_router(health_router)
    app.include_router(knowledge_bases_router)
    app.include_router(memory_router)
    app.include_router(search_router)
    return app


app = create_app()
