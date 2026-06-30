"""WebSocket endpoint cho collaboration real-time sơ đồ (Yjs y-websocket protocol).

Client trình duyệt dùng ``y-websocket`` kết nối tới ``ws://<host>/collab/<room>``. Nhiều
người cùng ``room`` -> kéo thả thấy nhau real-time. Xem :mod:`app.services.collab`.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.services.collab import diagram_collab

logger = logging.getLogger(__name__)

router = APIRouter(tags=["collab"])


@router.websocket("/collab/{room}")
async def collab_ws(websocket: WebSocket, room: str) -> None:
    await websocket.accept()
    try:
        await diagram_collab.serve(websocket, room)
    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001 — không để 1 client lỗi làm sập endpoint.
        logger.exception("Collab WS room=%s lỗi", room)
