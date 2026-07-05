"""Sinh chunk_id để PostgreSQL, Elasticsearch và Qdrant dùng CHUNG một định danh.

⚠️ 2026-07-03: chunk_id đổi sang **UUID v7** (``new_chunk_id()``, time-ordered) theo yêu cầu —
KHÔNG còn tất định. Vì uuid7 random nên sinh MỘT LẦN ở ``create_chunks`` (PG ``chunks.id``) rồi
truyền cho ES qua ``chunk_metadata['chunk_uuid']`` và cho Qdrant qua ``chunks.id`` (point_id +
payload chunk_id) -> vẫn KHỚP 3 store. ``deterministic_chunk_id`` (uuid5) GIỮ làm fallback cho
chunk cũ chưa có ``chunk_uuid``.

Nguyên tắc CŨ (deterministic_chunk_id, còn dùng fallback): chunk_id suy **tất định** từ
``(document_id, chunk_index)`` để ES/Qdrant tính lại ra CÙNG giá trị:

- PostgreSQL: ``chunks.id`` = ``deterministic_chunk_id(document_id, chunk_index)``.
- Elasticsearch (nhánh chunk): ``_id`` và field ``chunk_id`` = str của cùng UUID đó.
- Qdrant (Col1 chunks): payload ``chunk_id`` = ``str(chunks.id)`` (đã dùng ``database_chunk_id``)
  nên tự khớp.

``document_id`` vốn đã đồng bộ ở cả 3 store (đều là ``documents.id``) nên không cần đổi.
"""

from __future__ import annotations

import os
import time
from uuid import NAMESPACE_URL, UUID, uuid5


def new_chunk_id() -> UUID:
    """UUID v7 (time-ordered, RFC 9562) cho 1 chunk — Python 3.11 chưa có ``uuid.uuid7``.

    KHÔNG tất định (48-bit mốc ms + random) nên phải sinh MỘT LẦN lúc tạo chunk ở PG
    (``chunks.id``) rồi TRUYỀN cho ES/Qdrant qua ``chunks.id`` + ``metadata['chunk_uuid']`` để
    giữ ``chunk_id`` KHỚP giữa 3 store (join retrieval). Ưu điểm so với uuid5 cũ: id sắp theo
    thời gian tạo (tiện phân trang/soi), vẫn duy nhất."""
    unix_ms = int(time.time() * 1000)
    data = bytearray(unix_ms.to_bytes(6, "big") + os.urandom(10))
    data[6] = (data[6] & 0x0F) | 0x70  # version 7
    data[8] = (data[8] & 0x3F) | 0x80  # variant RFC 4122
    return UUID(bytes=bytes(data))


def deterministic_chunk_id(document_id: UUID | str, chunk_index: int) -> UUID:
    """UUID5 tất định cho 1 chunk theo ``(document_id, chunk_index)``.

    Cùng document + cùng thứ tự chunk -> cùng UUID (idempotent giữa các lần ingest, giúp
    re-index/backfill là upsert sạch); khác document hoặc khác vị trí -> khác UUID. Ràng buộc
    ``uq_chunks_document_chunk_index`` đảm bảo cặp là duy nhất nên UUID cũng duy nhất.
    """
    return uuid5(NAMESPACE_URL, f"doffice-chunk:{document_id}:{int(chunk_index)}")
