"""Redis cache cho kết quả search.

Key = SHA256(query | id_pb | id_dv | super_admin | top_k)[:16] — user cùng phòng ban
(và cùng quyền) chia sẻ cache. TTL mặc định 300s. Mặc định TẮT; chỉ bật khi
``search_cache_enabled`` và có ``redis_url`` và đã cài thư viện ``redis``.
"""

from __future__ import annotations

import hashlib
import logging
from typing import TYPE_CHECKING, Any, TypeVar

from app.core.config import settings

if TYPE_CHECKING:
    from app.services.security.security_acl_payload import AclSubject

logger = logging.getLogger(__name__)

T = TypeVar("T")


class SearchResultCache:
    def __init__(self, *, redis_url: str, ttl_seconds: int = 300) -> None:
        import redis.asyncio as aioredis  # lazy: chỉ cần khi cache bật

        self._redis = aioredis.from_url(redis_url, decode_responses=True)
        self._ttl = ttl_seconds

    @staticmethod
    def _cache_key(query: str, acl_subject: "AclSubject", top_k: int) -> str:
        su = 1 if getattr(acl_subject, "is_super_admin", False) else 0
        raw = f"{query}|{acl_subject.id_pb}|{acl_subject.id_dv}|{su}|{top_k}"
        return "search:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    async def get(self, query: str, acl_subject: "AclSubject", top_k: int, model_cls: type[T]) -> T | None:
        try:
            raw = await self._redis.get(self._cache_key(query, acl_subject, top_k))
        except Exception as exc:  # Redis lỗi -> coi như miss
            logger.warning("search cache get lỗi: %s", exc)
            return None
        if raw is None:
            return None
        try:
            return model_cls.model_validate_json(raw)  # type: ignore[attr-defined]
        except Exception:  # payload cache cũ/không hợp lệ
            return None

    async def set(self, query: str, acl_subject: "AclSubject", top_k: int, result: Any) -> None:
        try:
            await self._redis.set(
                self._cache_key(query, acl_subject, top_k),
                result.model_dump_json(),
                ex=self._ttl,
            )
        except Exception as exc:
            logger.warning("search cache set lỗi: %s", exc)

    async def invalidate_by_document(self, document_id: str) -> None:
        """Xoá thô toàn bộ cache search (best-effort) khi một văn bản được re-index."""
        try:
            async for key in self._redis.scan_iter(match="search:*"):
                await self._redis.delete(key)
        except Exception as exc:
            logger.warning("search cache invalidate lỗi: %s", exc)


_cache_singleton: SearchResultCache | None = None
_cache_initialized = False


def get_search_cache() -> SearchResultCache | None:
    """Trả về cache singleton nếu được bật + cấu hình hợp lệ; None nếu tắt/không cài redis."""
    global _cache_singleton, _cache_initialized
    if _cache_initialized:
        return _cache_singleton
    _cache_initialized = True
    if not settings.search_cache_enabled or not settings.redis_url:
        _cache_singleton = None
        return None
    try:
        _cache_singleton = SearchResultCache(
            redis_url=settings.redis_url,
            ttl_seconds=settings.search_cache_ttl_seconds,
        )
        logger.info("Search cache BẬT (TTL=%ss)", settings.search_cache_ttl_seconds)
    except Exception as exc:  # thiếu redis lib hoặc url sai
        logger.warning("Không bật được search cache: %s", exc)
        _cache_singleton = None
    return _cache_singleton
