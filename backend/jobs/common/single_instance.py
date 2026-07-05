"""Khoá chống chạy trùng (single-instance) cho các job dài.

Mỗi job giữ MỘT khoá theo tên. Nếu đã có tiến trình khác đang chạy cùng job ->
``acquire`` trả ``False`` -> job từ chối bật lần 2 (tránh 2 tiến trình cùng quét/ghi
chồng lên nhau). Khi tiến trình kết thúc (kể cả bị kill / crash / tắt cửa sổ), OS TỰ nhả
khoá -> KHÔNG để lại "khoá chết" chặn lần chạy sau.

Cơ chế:
  - Windows: named mutex (``CreateMutexW``). Handle mutex đóng khi tiến trình thoát -> tự nhả.
  - POSIX: ``fcntl.flock`` độc quyền non-blocking trên 1 file. Lock nhả khi fd đóng.
Kèm 1 file ``.info`` (best-effort) ghi PID + thời điểm chiếm khoá để hiện thông báo thân thiện.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

_LOCK_DIR = Path(tempfile.gettempdir())
_ERROR_ALREADY_EXISTS = 183


def _safe_name(name: str) -> str:
    return "".join(c if (c.isalnum() or c in "-_.") else "_" for c in name)


class SingleInstanceLock:
    """Khoá độc quyền theo ``name``. Dùng: ``if not lock.acquire(): ... ; lock.release()``."""

    def __init__(self, name: str) -> None:
        self._name = _safe_name(name)
        self.info_path = _LOCK_DIR / f"hbrag_job_{self._name}.info"
        self._handle = None  # Windows: HANDLE mutex
        self._fh = None      # POSIX: file object đang giữ flock

    def acquire(self, *, start_stamp: str = "") -> bool:
        ok = self._acquire_win() if sys.platform == "win32" else self._acquire_posix()
        if ok:
            self._write_info(start_stamp)
        return ok

    def _acquire_win(self) -> bool:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.windll.kernel32
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        kernel32.CreateMutexW.argtypes = [wintypes.LPCVOID, wintypes.BOOL, wintypes.LPCWSTR]
        handle = kernel32.CreateMutexW(None, True, f"Local\\hbrag_job_{self._name}")
        last = kernel32.GetLastError()
        if not handle:
            return True  # không tạo được mutex -> không chặn (thà chạy còn hơn treo job)
        if last == _ERROR_ALREADY_EXISTS:
            kernel32.CloseHandle(handle)  # đóng handle thừa, tiến trình khác đang giữ mutex
            return False
        self._handle = handle
        return True

    def _acquire_posix(self) -> bool:
        import fcntl

        fh = open(_LOCK_DIR / f"hbrag_job_{self._name}.lock", "a+")
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            fh.close()
            return False
        self._fh = fh
        return True

    def _write_info(self, start_stamp: str) -> None:
        try:
            self.info_path.write_text(f"{os.getpid()}\n{start_stamp}\n", encoding="utf-8")
        except OSError:
            pass

    def holder_info(self) -> tuple[str, str]:
        """(pid, start_stamp) của tiến trình đang giữ khoá — best-effort, có thể rỗng."""
        try:
            lines = self.info_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return "", ""
        pid = lines[0].strip() if lines else ""
        stamp = lines[1].strip() if len(lines) > 1 else ""
        return pid, stamp

    def release(self) -> None:
        if sys.platform == "win32":
            if self._handle:
                import ctypes

                kernel32 = ctypes.windll.kernel32
                kernel32.ReleaseMutex(self._handle)
                kernel32.CloseHandle(self._handle)
                self._handle = None
        elif self._fh is not None:
            import fcntl

            try:
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
            self._fh.close()
            self._fh = None
        try:
            self.info_path.unlink()
        except OSError:
            pass
