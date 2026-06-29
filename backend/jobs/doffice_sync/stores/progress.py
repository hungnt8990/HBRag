"""Ghi nhận tiến độ theo TỪNG văn bản ra FILE -> resume khi job tắt giữa chừng.

Mỗi văn bản chỉ ghi "xong" khi đã hoàn tất CẢ 3 luồng (PG đã có sẵn, + ES + Qdrant).
Khi job tắt: văn bản đang embed (Qdrant chưa xong) KHÔNG nằm trong file -> lần chạy sau
bỏ qua văn bản đã xong, làm LẠI TỪ ĐẦU văn bản dở (ingest idempotent: xóa cũ + ghi lại).

File text đơn giản, mỗi dòng 1 id_vb. Hoàn tất cả run -> xóa file (lần sau bắt đầu sạch,
dựa vào checkpoint incremental). Tắt giữa chừng -> file còn -> resume.
"""

from __future__ import annotations

from pathlib import Path


class ProgressStore:
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    def load(self) -> set[str]:
        """Tập id_vb đã hoàn tất từ lần chạy trước (rỗng nếu chưa có file)."""
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

    def mark_done(self, id_vb: str) -> None:
        """Ghi 1 id_vb đã hoàn tất (append, flush ngay để an toàn khi tắt đột ngột)."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(f"{id_vb}\n")
            handle.flush()

    def clear(self) -> None:
        """Xóa file tiến độ (gọi khi cả run hoàn tất, hoặc khi --full-scan)."""
        try:
            self._path.unlink(missing_ok=True)
        except OSError:
            pass
