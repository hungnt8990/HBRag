"""Tiện ích console dùng chung: bật màu ANSI, spinner loading, hộp summary có màu."""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Callable

RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
CYAN = "\033[36m"
MAGENTA = "\033[35m"
GREY = "\033[90m"


def enable_ansi() -> None:
    """Bật Virtual Terminal Processing trên Windows để render màu ANSI trong cmd."""
    if sys.platform == "win32":
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32
            kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
        except Exception:
            pass


def color(text: str, code: str) -> str:
    return f"{code}{text}{RESET}"


class Spinner:
    """Spinner quay (asyncio) hiển thị trạng thái live, có màu.

    ``status_fn()`` có thể trả NHIỀU dòng (ngăn bằng ``\\n``) -> render thành khối cố
    định, cập nhật TẠI CHỖ (không cuộn) nhờ di chuyển con trỏ. Frame quay đặt ở đầu dòng
    đầu tiên. Tương thích ngược với status 1 dòng.
    """

    FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

    def __init__(self, status_fn: Callable[[], str], *, interval: float = 0.1) -> None:
        self._status_fn = status_fn
        self._interval = interval
        self._task: asyncio.Task | None = None
        self._running = False
        self._lines = 0  # số dòng khối đang hiển thị (để xóa/ghi đè lần sau)

    def start(self) -> None:
        if not sys.stdout.isatty():
            return  # không phải terminal (vd pipe/log) -> bỏ spinner
        self._running = True
        self._task = asyncio.create_task(self._loop())

    def _render(self, frame: str) -> None:
        lines = self._status_fn().split("\n")
        if lines:
            lines[0] = f"{CYAN}{frame}{RESET} {lines[0]}"
        buf: list[str] = []
        if self._lines:
            buf.append(f"\033[{self._lines}A")  # lên đầu khối cũ
        for ln in lines:
            buf.append(f"\r{ln}\033[K\n")  # ghi đè + xóa phần thừa cuối dòng
        sys.stdout.write("".join(buf))
        sys.stdout.flush()
        self._lines = len(lines)

    async def _loop(self) -> None:
        i = 0
        while self._running:
            self._render(self.FRAMES[i % len(self.FRAMES)])
            i += 1
            try:
                await asyncio.sleep(self._interval)
            except asyncio.CancelledError:
                break

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        if sys.stdout.isatty() and self._lines:
            # Xóa toàn bộ khối live rồi đưa con trỏ về đầu khối -> summary ghi đè sạch.
            sys.stdout.write(f"\033[{self._lines}A")
            sys.stdout.write(("\r\033[K\n") * self._lines)
            sys.stdout.write(f"\033[{self._lines}A")
            sys.stdout.flush()
            self._lines = 0
