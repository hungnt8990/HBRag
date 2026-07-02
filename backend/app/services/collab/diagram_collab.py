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
        self._server_task: asyncio.Task[None] | None = None
        self._saver_task: asyncio.Task[None] | None = None
        self._start_lock = asyncio.Lock()  # serialize start() (nhiều connection self-heal cùng lúc)
        self._docs: dict[str, Doc] = {}
        self._subs: dict[str, Any] = {}
        self._dirty: set[str] = set()
        self._running = False

    # ----------------------------------------------------------------- lifecycle
    async def start(self) -> None:
        """Khởi động WS server (task nền chuyên dụng) + vòng lưu nền.

        Dùng API "background task" pycrdt khuyến nghị (``create_task(server.start)`` rồi
        chờ ``server.started``) thay vì ``async with server`` / ``enter_async_context``:
        task group của server sống trong 1 task nền RIÊNG, KHÔNG gắn vào task lifespan hay
        task của connection. Nhờ vậy :meth:`serve` có thể tự khởi động lại (self-heal) an
        toàn từ task bất kỳ mà không vi phạm quy tắc "cancel scope phải đóng đúng task" của
        anyio. Lỗi khởi động KHÔNG bị nuốt ở đây — cứ để nổi lên cho caller log/xử lý."""
        if self._running:
            return
        async with self._start_lock:
            if self._running:  # đã có connection khác self-heal xong trong lúc chờ lock
                return
            # Lần start trước chết giữa chừng (task done) -> WebsocketServer có thể còn
            # _task_group cũ khiến start() lại ném "already running". Dùng server sạch để
            # self-heal đáng tin cậy (rooms sẽ được nạp lại từ đĩa trong _ensure_room).
            if self._server_task is not None and self._server_task.done():
                self._server = WebsocketServer(auto_clean_rooms=False)
            self._server_task = asyncio.create_task(self._server.start())
            # Chờ server báo sẵn sàng; nếu task chết TRƯỚC khi set started -> nổi lỗi thật, KHÔNG treo.
            started_wait = asyncio.create_task(self._server.started.wait())
            try:
                done, _ = await asyncio.wait(
                    {started_wait, self._server_task}, return_when=asyncio.FIRST_COMPLETED
                )
                if self._server_task in done:  # task xong sớm = lỗi (hoặc dừng bất thường)
                    self._server_task.result()  # re-raise exception nếu có
                    raise RuntimeError("WebsocketServer dừng ngay khi khởi động")
            finally:
                started_wait.cancel()
                try:
                    await started_wait
                except asyncio.CancelledError:
                    pass
            self._saver_task = asyncio.create_task(self._saver_loop())
            self._running = True
            logger.info("DiagramCollab started (data dir=%s)", _DATA_DIR)

    async def _ensure_started(self) -> None:
        """Đảm bảo WS server đang chạy trước khi phục vụ 1 connection.

        Nếu :meth:`start` lúc boot đã lỗi (vd bị nuốt trong lifespan) thì server chưa chạy;
        connection đầu tiên sẽ tự thử khởi động lại và để lỗi THẬT nổi lên (thay vì client
        cứ nhận ``RuntimeError: WebsocketServer is not running`` mù mờ ở mọi lần nối)."""
        if self._running:
            return
        logger.warning(
            "DiagramCollab chưa chạy (start lúc boot có thể đã lỗi) -> thử khởi động lại theo yêu cầu connection"
        )
        await self.start()

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
        # Dừng WS server: server.stop() set _stopped + cancel task group -> task nền kết thúc.
        try:
            await self._server.stop()
        except Exception:
            logger.exception("Dừng WebsocketServer thất bại")
        if self._server_task is not None:
            self._server_task.cancel()
            try:
                await self._server_task
            except asyncio.CancelledError:
                pass
            self._server_task = None
        logger.info("DiagramCollab stopped")

    # -------------------------------------------------------------------- serving
    async def serve(self, websocket: Any, room: str) -> None:
        """Phục vụ 1 client WebSocket cho ``room`` (đã accept() trước khi gọi)."""
        await self._ensure_started()
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
