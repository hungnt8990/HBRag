"""Máy chủ collaboration real-time (Yjs CRDT) cho sơ đồ kiến trúc — nhúng TRONG FastAPI.

Một process duy nhất: WebSocket endpoint ``/collab/{room}`` bắc cầu Starlette WebSocket vào
``pycrdt.websocket.WebsocketServer`` (cùng giao thức y-websocket mà client trình duyệt dùng).
Nhiều người mở cùng 1 ``room`` -> kéo thả thấy nhau real-time (CRDT tự merge).

Lưu trữ: mỗi room giữ 1 ``Doc`` trong RAM (đồng bộ real-time). Khi doc đổi, đánh dấu "bẩn";
một vòng lặp nền cứ ~2s lưu trạng thái đầy đủ (``Doc.get_update()``) ra file nhị phân
``data/collab/<room>.ybin``. Khi tạo lại room (vd sau restart) -> ``apply_update`` từ file ->
khôi phục nguyên trạng. Không phụ thuộc nội bộ YStore -> kiểm soát & test được hoàn toàn.
"""

from __future__ import annotations

import asyncio
import logging
import re
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

import anyio
from pycrdt import Doc
from pycrdt.websocket import WebsocketServer, YRoom

logger = logging.getLogger(__name__)

# backend/data/collab/  (parents: collab -> services -> app -> backend)
_DATA_DIR = Path(__file__).resolve().parents[3] / "data" / "collab"
_SAVE_INTERVAL_SECONDS = 2.0
# Tên room an toàn cho tên file (tránh path traversal). Chỉ chữ/số/_/-.
_ROOM_RE = re.compile(r"[^A-Za-z0-9_-]")


class _FastAPIChannel:
    """Adapter giao thức ``Channel`` của pycrdt trên Starlette/FastAPI WebSocket."""

    def __init__(self, websocket: Any, path: str) -> None:
        self._ws = websocket
        self._path = path
        self._send_lock = anyio.Lock()

    @property
    def path(self) -> str:
        return self._path

    def __aiter__(self) -> "_FastAPIChannel":
        return self

    async def __anext__(self) -> bytes:
        try:
            return await self.recv()
        except Exception:  # WebSocketDisconnect / đóng kết nối -> kết thúc vòng lặp serve.
            raise StopAsyncIteration

    async def send(self, message: bytes) -> None:
        async with self._send_lock:
            await self._ws.send_bytes(message)

    async def recv(self) -> bytes:
        return bytes(await self._ws.receive_bytes())


class DiagramCollab:
    """Quản lý vòng đời máy chủ Yjs WS + persistence cho các room sơ đồ."""

    def __init__(self) -> None:
        # auto_clean_rooms=False: giữ room sống dù không còn client (để còn lưu/khôi phục).
        self._server = WebsocketServer(auto_clean_rooms=False)
        self._exit_stack: AsyncExitStack | None = None
        self._saver_task: asyncio.Task[None] | None = None
        self._docs: dict[str, Doc] = {}
        self._subs: dict[str, Any] = {}
        self._dirty: set[str] = set()
        self._running = False

    # ----------------------------------------------------------------- lifecycle
    async def start(self) -> None:
        """Khởi động trong lifespan FastAPI: vào task group của WS server + vòng lưu nền."""
        if self._running:
            return
        self._exit_stack = AsyncExitStack()
        await self._exit_stack.enter_async_context(self._server)
        self._saver_task = asyncio.create_task(self._saver_loop())
        self._running = True
        logger.info("DiagramCollab started (data dir=%s)", _DATA_DIR)

    async def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        for room in list(self._docs):  # lưu lần cuối trước khi tắt
            try:
                self._save_room(room)
            except Exception:
                logger.exception("Lưu room %s khi tắt thất bại", room)
        for sub in self._subs.values():
            try:
                sub.drop()
            except Exception:
                pass
        self._subs.clear()
        if self._saver_task is not None:
            self._saver_task.cancel()
            try:
                await self._saver_task
            except asyncio.CancelledError:
                pass
            self._saver_task = None
        if self._exit_stack is not None:
            await self._exit_stack.aclose()
            self._exit_stack = None
        logger.info("DiagramCollab stopped")

    # -------------------------------------------------------------------- serving
    async def serve(self, websocket: Any, room: str) -> None:
        """Phục vụ 1 client WebSocket cho ``room`` (đã accept() trước khi gọi)."""
        room = self._safe_room(room)
        self._ensure_room(room)
        channel = _FastAPIChannel(websocket, room)
        await self._server.serve(channel)

    # --------------------------------------------------------------- persistence
    def _safe_room(self, room: str) -> str:
        room = _ROOM_RE.sub("-", (room or "").strip())[:80]
        return room or "default"

    def _room_path(self, room: str) -> Path:
        return _DATA_DIR / f"{room}.ybin"

    def _ensure_room(self, room: str) -> None:
        if room in self._server.rooms:
            return
        doc = Doc()
        path = self._room_path(room)
        if path.is_file():
            try:
                doc.apply_update(path.read_bytes())
                logger.info("Khôi phục room %s từ %s", room, path.name)
            except Exception:
                logger.exception("Đọc snapshot room %s lỗi -> bắt đầu rỗng", room)
        # Đăng ký room (đã nạp doc) TRƯỚC khi server.serve gọi get_room -> dùng đúng doc này.
        self._server.rooms[room] = YRoom(ydoc=doc, ready=True)
        self._docs[room] = doc

        def _on_update(_event: Any, _room: str = room) -> None:
            self._dirty.add(_room)

        self._subs[room] = doc.observe(_on_update)

    def _save_room(self, room: str) -> None:
        doc = self._docs.get(room)
        if doc is None:
            return
        data = doc.get_update()
        path = self._room_path(room)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".ybin.tmp")
        tmp.write_bytes(data)
        tmp.replace(path)  # ghi nguyên tử (tránh file hỏng giữa chừng)

    async def _saver_loop(self) -> None:
        while True:
            await asyncio.sleep(_SAVE_INTERVAL_SECONDS)
            for room in list(self._dirty):
                self._dirty.discard(room)
                try:
                    self._save_room(room)
                except Exception:
                    logger.exception("Lưu room %s thất bại", room)


# Singleton dùng chung cho route + lifespan.
diagram_collab = DiagramCollab()
