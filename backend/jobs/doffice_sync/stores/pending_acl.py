"""Nhớ các văn bản BỊ BỎ QUA vì CHƯA có ACL -> thử lại ở lần quét sau.

Feeder đẩy văn bản theo thứ tự; gặp VB chưa có quyền (đơn vị/phòng ban/nhân viên list
đều rỗng) thì KHÔNG chờ mà BỎ QUA và ghi id_vb vào file này. Đầu mỗi lần chạy, feeder
nạp lại danh sách này, fetch trực tiếp theo id_vb (bỏ qua checkpoint incremental) rồi thử
lại: VB nào ĐÃ có ACL -> đẩy vào pipeline + xoá khỏi pending; VB nào vẫn chưa -> giữ lại.

Cần file riêng (KHÔNG dựa vào checkpoint) vì checkpoint incremental lọc ``gte(updated_after)``
nên VB cũ đã bị bỏ qua sẽ KHÔNG được scroll lấy lại.
"""

from __future__ import annotations

from pathlib import Path


class PendingAclStore:
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    def load(self) -> set[str]:
        if not self._path.exists():
            return set()
        try:
            return {
                line.strip()
                for line in self._path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            }
        except OSError:
            return set()

    def save(self, ids: set[str]) -> None:
        """Ghi đè toàn bộ tập pending. Rỗng -> xoá file."""
        if not ids:
            try:
                self._path.unlink(missing_ok=True)
            except OSError:
                pass
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._path.write_text("\n".join(sorted(ids)) + "\n", encoding="utf-8")
        except OSError:
            pass
