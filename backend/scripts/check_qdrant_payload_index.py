"""Kiểm tra payload index của 2 collection Qdrant KHO (chunk + docmeta).

In danh sách payload index đang có + cho biết field chỉ định (mặc định ``org_list``) đã được
index chưa. Payload index do ``run_kho_qdrant`` tạo (ensure_collection); ``run_kho_chunk`` KHÔNG
tạo (chỉ ghi ES).

Dùng:
    .venv\\Scripts\\python.exe scripts/check_qdrant_payload_index.py
    .venv\\Scripts\\python.exe scripts/check_qdrant_payload_index.py org_list acl_subjects
    .venv\\Scripts\\python.exe scripts/check_qdrant_payload_index.py --all   # in TẤT CẢ index
"""

from __future__ import annotations

import asyncio
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore", message="Api key is used with an insecure connection.")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except Exception:  # noqa: BLE001
        pass

from app.services.vector.vector_store import (  # noqa: E402
    get_doffice_chunks_vector_store,
    get_doffice_docmeta_vector_store,
)


async def _report(label: str, factory, want: list[str], show_all: bool) -> None:
    store = factory()
    try:
        if not await store._client.collection_exists(collection_name=store.collection_name):
            print(f"  {label} {store.collection_name}: ⚠ COLLECTION CHƯA TỒN TẠI (chưa chạy run_kho_qdrant)")
            return
        info = await store._client.get_collection(collection_name=store.collection_name)
        schema = getattr(info, "payload_schema", {}) or {}
        print(f"  {label} {store.collection_name}  ·  {len(schema)} payload index · {getattr(info, 'points_count', '?')} point")
        for field in want:
            entry = schema.get(field)
            if entry is None:
                print(f"     ✗ {field}: CHƯA index")
            else:
                dtype = getattr(entry, "data_type", entry)
                print(f"     ✓ {field}: đã index (kiểu {dtype})")
        if show_all:
            print("     — tất cả field đã index:")
            for name in sorted(schema):
                print(f"        · {name}")
    finally:
        try:
            await store._client.close()
        except Exception:  # noqa: BLE001
            pass


async def _main() -> None:
    args = [a for a in sys.argv[1:] if a != "--all"]
    show_all = "--all" in sys.argv[1:]
    want = args or ["org_list"]
    print("Kiểm tra payload index Qdrant (KHO):")
    await _report("CHUNK  ", get_doffice_chunks_vector_store, want, show_all)
    await _report("DOCMETA", get_doffice_docmeta_vector_store, want, show_all)


if __name__ == "__main__":
    asyncio.run(_main())
