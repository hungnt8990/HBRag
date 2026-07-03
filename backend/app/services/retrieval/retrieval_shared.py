"""Tiện ích dùng chung cho HOT PATH tìm kiếm (/api/document-search/search).

1. ``get_es_http_client()`` — httpx.AsyncClient dùng CHUNG theo event loop.
   Trước đây mỗi call ES tạo ``httpx.AsyncClient`` mới (bắt tay TCP/TLS lại từ đầu).
   Client gắn với loop tạo ra nó nên cache THEO LOOP (WeakKeyDictionary): API (uvicorn)
   1 loop -> tái dùng kết nối keep-alive mãi; job/test tạo loop riêng -> client riêng,
   loop bị GC -> entry tự biến mất. KHÔNG đóng client trả về (chủ sở hữu là cache).

2. ``TtlCache`` — cache TTL nhỏ, thuần dict (không thêm dependency), dùng cho
   ACL subject / query expansion / query embedding. An toàn trong 1 event loop
   (asyncio không preempt giữa các thao tác dict).
"""

from __future__ import annotations

import asyncio
import time
import weakref
from typing import Any

import httpx

_ES_CLIENT_TIMEOUT_S = 30.0
_ES_CLIENTS: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, httpx.AsyncClient]" = (
    weakref.WeakKeyDictionary()
)


def get_es_http_client() -> httpx.AsyncClient:
    """Client HTTP (keep-alive) dùng chung cho các call ES trong event loop hiện tại."""
    loop = asyncio.get_running_loop()
    client = _ES_CLIENTS.get(loop)
    # Test có thể patch httpx.AsyncClient bằng fake không có ``is_closed`` -> getattr.
    if client is None or getattr(client, "is_closed", False):
        client = httpx.AsyncClient(
            timeout=_ES_CLIENT_TIMEOUT_S,
            limits=httpx.Limits(max_connections=50, max_keepalive_connections=20),
        )
        _ES_CLIENTS[loop] = client
    return client


class TtlCache:
    """Cache key -> value với TTL, giới hạn kích thước (evict entry cũ nhất khi đầy)."""

    def __init__(self, *, maxsize: int, ttl_seconds: float) -> None:
        self._maxsize = max(1, int(maxsize))
        self._ttl = float(ttl_seconds)
        self._data: dict[Any, tuple[float, Any]] = {}

    def get(self, key: Any) -> Any | None:
        entry = self._data.get(key)
        if entry is None:
            return None
        expires, value = entry
        if time.monotonic() >= expires:
            self._data.pop(key, None)
            return None
        return value

    def put(self, key: Any, value: Any) -> None:
        if len(self._data) >= self._maxsize:
            now = time.monotonic()
            expired = [k for k, (exp, _) in self._data.items() if now >= exp]
            for k in expired:
                self._data.pop(k, None)
            while len(self._data) >= self._maxsize:  # vẫn đầy -> bỏ entry cũ nhất (FIFO)
                self._data.pop(next(iter(self._data)), None)
        self._data[key] = (time.monotonic() + self._ttl, value)

    def clear(self) -> None:
        self._data.clear()
