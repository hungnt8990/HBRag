"""Kiem tra trang thai chunk DOffice: PG flag + phan bo so point/van ban tren Qdrant.

Chi DOC (read-only). In: tong doc, da/chua qdrant_indexed, tong point Qdrant Col1,
va top van ban nhieu point nhat (nghi ngo no chunk).
"""

from __future__ import annotations

import asyncio
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.db.session import AsyncSessionLocal  # noqa: E402
from app.services.document_sources import DOFFICE_SOURCE_TYPE  # noqa: E402
from app.services.vector.vector_store import get_doffice_chunks_vector_store  # noqa: E402


async def main() -> None:
    async with AsyncSessionLocal() as s:
        total = (await s.execute(
            text("SELECT count(*) FROM documents WHERE source_type=:t"),
            {"t": DOFFICE_SOURCE_TYPE})).scalar() or 0
        indexed = (await s.execute(
            text("SELECT count(*) FROM documents WHERE source_type=:t "
                 "AND coalesce(document_metadata->>'qdrant_indexed','false')='true'"),
            {"t": DOFFICE_SOURCE_TYPE})).scalar() or 0
    print("== POSTGRES ==")
    print(f"  doffice docs total      : {total}")
    print(f"  qdrant_indexed = true   : {indexed}")
    print(f"  qdrant_indexed != true  : {total - indexed}")
    print()

    store = get_doffice_chunks_vector_store()
    client = store._client
    coll = store.collection_name
    print("== QDRANT Col1 (chunks) ==")
    print(f"  collection: {coll}")
    if not await client.collection_exists(collection_name=coll):
        print("  (collection chua ton tai)")
        return
    info = await client.get_collection(collection_name=coll)
    print(f"  points_count: {info.points_count}")

    # Scroll toan bo, dem point theo document_id.
    per_doc: Counter = Counter()
    offset = None
    scanned = 0
    while True:
        points, offset = await client.scroll(
            collection_name=coll, limit=1000, offset=offset,
            with_payload=["document_id", "id_vb"], with_vectors=False,
        )
        if not points:
            break
        for p in points:
            pl = p.payload or {}
            key = str(pl.get("id_vb") or pl.get("document_id") or "?")
            per_doc[key] += 1
        scanned += len(points)
        if offset is None:
            break
    print(f"  scanned points: {scanned}")
    print(f"  distinct docs : {len(per_doc)}")
    if per_doc:
        counts = sorted(per_doc.values())
        n = len(counts)
        print(f"  chunks/doc: min={counts[0]} median={counts[n//2]} max={counts[-1]}")
        big = [(k, v) for k, v in per_doc.items() if v >= 60]
        big.sort(key=lambda kv: -kv[1])
        print(f"  docs >= 60 chunk: {len(big)}")
        for k, v in big[:40]:
            print(f"    {v:6d}  id_vb={k}")


if __name__ == "__main__":
    asyncio.run(main())
