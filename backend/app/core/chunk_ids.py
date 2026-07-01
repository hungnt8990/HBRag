"""Sinh chunk_id TẤT ĐỊNH để PostgreSQL, Elasticsearch và Qdrant dùng CHUNG một định danh.

Nguyên tắc (theo yêu cầu): chunk_id do bảng PG ``chunks`` sinh ra (khóa chính ``chunks.id``)
được suy ra **tất định** từ ``(document_id, chunk_index)`` — KHÔNG random. Nhờ vậy ES và Qdrant
(đọc/tính lại ở thời điểm khác, thậm chí ở job khác) luôn ra CÙNG một giá trị, không còn lệch:

- PostgreSQL: ``chunks.id`` = ``deterministic_chunk_id(document_id, chunk_index)``.
- Elasticsearch (nhánh chunk): ``_id`` và field ``chunk_id`` = str của cùng UUID đó.
- Qdrant (Col1 chunks): payload ``chunk_id`` = ``str(chunks.id)`` (đã dùng ``database_chunk_id``)
  nên tự khớp.

``document_id`` vốn đã đồng bộ ở cả 3 store (đều là ``documents.id``) nên không cần đổi.
"""

from __future__ import annotations

from uuid import NAMESPACE_URL, UUID, uuid5


def deterministic_chunk_id(document_id: UUID | str, chunk_index: int) -> UUID:
    """UUID5 tất định cho 1 chunk theo ``(document_id, chunk_index)``.

    Cùng document + cùng thứ tự chunk -> cùng UUID (idempotent giữa các lần ingest, giúp
    re-index/backfill là upsert sạch); khác document hoặc khác vị trí -> khác UUID. Ràng buộc
    ``uq_chunks_document_chunk_index`` đảm bảo cặp là duy nhất nên UUID cũng duy nhất.
    """
    return uuid5(NAMESPACE_URL, f"doffice-chunk:{document_id}:{int(chunk_index)}")
