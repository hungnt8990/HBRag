"""Hạ tầng dùng chung cho "kho vector local" của bộ test chunk DOffice.

Ý tưởng: tái sử dụng NGUYÊN thuật toán embedding + lưu trữ + truy hồi của backend
(``app/services``), ghi vào **Qdrant SERVER thật** (URL/API key lấy từ .env qua settings)
nhưng đặt trên **collection TEST riêng** để KHÔNG đụng collection production. Nhờ đó:

- Lớp ``QdrantVectorStore`` (dense + sparse, payload index, hybrid RRF) giữ NGUYÊN, chạy
  hệt production (server HNSW + payload index thật) — chỉ khác tên collection.
- Chunk -> RagChunk -> embedding_text -> payload đi qua ĐÚNG các hàm pipeline
  (``rag_chunk_from_database`` / ``build_embedding_text`` / ``qdrant_payload``), nên
  vector + metadata sinh ra giống hệt luồng ``embed_to_qdrant`` thật.

Hai collection TEST (giống thiết kế 3-DB thật, khác tên prod ``hbrag_doffice_*``):
  - ``chunk_test_chunks``  : 1 point / chunk nội dung (thứ để TEST chất lượng chunk).
  - ``chunk_test_docmeta`` : 1 point / văn bản (vector metadata trich_yeu+tom_tat+noi_ban_hanh).
Đổi tên qua env ``CHUNK_TEST_CHUNKS_COLLECTION`` / ``CHUNK_TEST_DOCMETA_COLLECTION``.

Dùng CHUNG 1 client (server hỗ trợ nhiều collection). ``data/manifest.json`` chỉ lưu tóm
tắt lần build (tên collection + cờ sparse) để retrieve khớp cấu hình — KHÔNG còn db local.
"""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

# useChunk -> Chunk -> tests -> backend (thư mục gốc backend để import package ``app``).
BACKEND_ROOT = Path(__file__).resolve().parents[3]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

CHUNK_DIR = Path(__file__).resolve().parents[1]          # tests/Chunk
DATA_DIR = CHUNK_DIR / "data"                            # chỉ chứa manifest.json (tóm tắt build)
MANIFEST_PATH = DATA_DIR / "manifest.json"              # tóm tắt lần build gần nhất

# Collection TEST trên Qdrant server thật (khác prod ``hbrag_doffice_*``). Override qua env.
CHUNKS_COLLECTION = os.getenv("CHUNK_TEST_CHUNKS_COLLECTION", "chunk_test_chunks")
DOCMETA_COLLECTION = os.getenv("CHUNK_TEST_DOCMETA_COLLECTION", "chunk_test_docmeta")

# Namespace cố định -> document_id/chunk_id TẤT ĐỊNH theo id_vb (build lại cho cùng id
# sẽ ra cùng point_id -> upsert đè, không nhân bản).
_DOC_NAMESPACE = uuid.UUID("0d0ff1ce-0000-4000-8000-0000000000aa")
_SOURCE_TYPE = "doffice_elasticsearch"


def doc_uuid(id_vb: Any) -> uuid.UUID:
    """UUID tất định của 1 văn bản theo id_vb (đóng vai document_id nội bộ)."""
    return uuid.uuid5(_DOC_NAMESPACE, f"doffice:{id_vb}")


def _chunk_uuid(document_id: uuid.UUID, chunk_index: int) -> uuid.UUID:
    return uuid.uuid5(_DOC_NAMESPACE, f"{document_id}:{chunk_index}")


# ---------------------------------------------------------------- store server --
def open_client():
    """Mở 1 AsyncQdrantClient tới Qdrant SERVER thật (URL/API key từ .env qua settings)."""
    from qdrant_client import AsyncQdrantClient

    from app.core.config import settings

    return AsyncQdrantClient(
        url=settings.qdrant_url,
        api_key=settings.qdrant_api_key,
        timeout=60.0,
    )


def make_store(client, collection_name: str, *, sparse_enabled: bool | None = None):
    """Dựng ``QdrantVectorStore`` (dense+sparse) trỏ vào client local, cùng tham số
    embedding/vector như settings thật (chỉ đổi collection + client).

    ``sparse_enabled=None`` -> lấy theo settings. Truyền tường minh (từ manifest) để
    build và retrieve LUÔN khớp cấu hình sparse (tránh search sparse trên collection
    chỉ có dense -> lỗi)."""
    from app.core.config import settings
    from app.services.vector.vector_store import QdrantVectorStore

    return QdrantVectorStore(
        client=client,
        collection_name=collection_name,
        vector_size=settings.embedding_dimension,
        upsert_batch_size=settings.qdrant_upsert_batch_size,
        dense_vector_name=settings.dense_vector_name,
        sparse_vector_name=settings.sparse_vector_name,
        sparse_enabled=(
            settings.sparse_embedding_enabled if sparse_enabled is None else sparse_enabled
        ),
        auto_recreate_collection=True,  # test: tự tạo/tương thích collection local
    )


def read_manifest() -> dict:
    """Đọc manifest.json của lần build gần nhất (rỗng nếu chưa build)."""
    import json

    if not MANIFEST_PATH.is_file():
        return {}
    try:
        return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


# ------------------------------------------------------- fake ORM cho rag_chunk --
def fake_document(id_vb: Any, source: dict[str, Any], document_id: uuid.UUID) -> SimpleNamespace:
    """Đối tượng "document" tối thiểu mà ``rag_chunk_from_database`` cần (không đụng PG)."""
    return SimpleNamespace(
        id=document_id,
        title=source.get("trich_yeu") or source.get("ten_file") or None,
        organization_id=None,
        knowledge_base_id=None,
        uploaded_by_user_id=None,
        visibility=None,
        document_metadata={
            "id_vb": str(id_vb),
            "document_version": "v1",
            "source_type": _SOURCE_TYPE,
            "issuer": source.get("noi_ban_hanh") or None,
        },
    )


def fake_chunk(document_id: uuid.UUID, chunk_create: Any, id_vb: Any) -> SimpleNamespace:
    """Đối tượng "chunk" tối thiểu (từ ``ChunkCreate``) cho ``rag_chunk_from_database``."""
    metadata = dict(getattr(chunk_create, "metadata", None) or {})
    metadata.setdefault("source_type", _SOURCE_TYPE)
    metadata.setdefault("id_vb", str(id_vb))
    return SimpleNamespace(
        id=_chunk_uuid(document_id, int(chunk_create.chunk_index)),
        document_id=document_id,
        chunk_index=int(chunk_create.chunk_index),
        content=chunk_create.content,
        token_count=getattr(chunk_create, "token_count", None),
        chunk_metadata=metadata,
        enriched_content="",
    )
