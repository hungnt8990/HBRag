"""Dat lai trang thai de CHUNK LAI toan bo DOffice sau khi fix bug no chunk.

Thao tac (chi chay khi co --yes):
  1. PG: reset co qdrant_indexed=false cho cac doc dang 'true' -> quay lai hang cho.
  2. Qdrant Col1 (chunks): wipe sach (recreate_collection) -> xoa moi point cu (ke ca
     point no chunk + point mo coi). Col2 (docmeta) GIU NGUYEN (point ID deterministic,
     job se ghi de).

Sau khi chay: chay lai job `python jobs/doffice_sync/run_qdrant.py` de chunk + embed lai.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402

from app.db.session import AsyncSessionLocal  # noqa: E402
from app.services.document_sources import DOFFICE_SOURCE_TYPE  # noqa: E402
from app.services.vector.vector_store import get_doffice_chunks_vector_store  # noqa: E402

_PENDING_TRUE = (
    "source_type=:t AND coalesce(document_metadata->>'qdrant_indexed','false')='true'"
)


async def main(do_it: bool) -> None:
    # --- Truoc khi dong ---
    async with AsyncSessionLocal() as s:
        total = (await s.execute(
            text("SELECT count(*) FROM documents WHERE source_type=:t"),
            {"t": DOFFICE_SOURCE_TYPE})).scalar() or 0
        flagged = (await s.execute(
            text(f"SELECT count(*) FROM documents WHERE {_PENDING_TRUE}"),
            {"t": DOFFICE_SOURCE_TYPE})).scalar() or 0
    store = get_doffice_chunks_vector_store()
    coll = store.collection_name
    exists = await store._client.collection_exists(collection_name=coll)
    points = 0
    if exists:
        points = (await store._client.get_collection(collection_name=coll)).points_count

    print("== TRUOC ==")
    print(f"  doffice docs            : {total}")
    print(f"  qdrant_indexed=true     : {flagged}  (se reset ve false)")
    print(f"  Qdrant Col1 '{coll}'")
    print(f"    points_count          : {points}  (se XOA SACH)")
    print()

    if not do_it:
        print("DRY-RUN. Them co --yes de thuc thi.")
        return

    # --- 1. Reset co PG ---
    async with AsyncSessionLocal() as s:
        res = await s.execute(
            text(
                "UPDATE documents "
                "SET document_metadata = jsonb_set(document_metadata,'{qdrant_indexed}','false'::jsonb) "
                f"WHERE {_PENDING_TRUE}"
            ),
            {"t": DOFFICE_SOURCE_TYPE},
        )
        await s.commit()
        print(f"[1] PG: da reset {res.rowcount} doc ve qdrant_indexed=false")

    # --- 2. Wipe Qdrant Col1 ---
    info = await store.recreate_collection()
    print(f"[2] Qdrant: da recreate collection '{coll}' (vector_size={info.vector_size}, sparse={info.sparse_configured})")

    # --- Sau khi dong ---
    async with AsyncSessionLocal() as s:
        flagged_after = (await s.execute(
            text(f"SELECT count(*) FROM documents WHERE {_PENDING_TRUE}"),
            {"t": DOFFICE_SOURCE_TYPE})).scalar() or 0
    points_after = (await store._client.get_collection(collection_name=coll)).points_count
    print()
    print("== SAU ==")
    print(f"  qdrant_indexed=true     : {flagged_after}")
    print(f"  Qdrant Col1 points      : {points_after}")
    print(f"  Doc cho re-chunk        : {total} (toan bo)")
    print()
    print("Tiep theo: python jobs/doffice_sync/run_qdrant.py   (chunk + embed lai)")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Reset DOffice de chunk lai sau fix bug no chunk.")
    p.add_argument("--yes", action="store_true", help="Thuc thi that (mac dinh dry-run).")
    asyncio.run(main(p.parse_args().yes))
