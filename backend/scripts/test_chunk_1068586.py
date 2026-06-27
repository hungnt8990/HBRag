"""Test bo chunker DOffice HIEN TAI tren van ban 1068586.

Fetch -> normalize -> build_doffice_chunks. In thong ke + dump ra scratchpad de soi.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from app.services.chunkers.chunker_doffice_chunking import build_doffice_chunks
from app.services.document_sources import DofficeElasticsearchSource
from app.services.ingestion.ingestion_doffice_content_normalizer import normalize_doffice_source

OUT = Path(r"C:\Users\hungnt\AppData\Local\Temp\claude\D--CPC-PM-Tiep-Nhan-AI-Project-HBRag-backend\fb0a9553-fc15-45ff-b71b-e62cd8434759\scratchpad")


async def main(id_vb: str) -> None:
    src = DofficeElasticsearchSource()
    doc = await src.fetch_document_by_id_vb(id_vb)
    normalized = normalize_doffice_source(dict(doc.raw_source or {}))

    print("== NORMALIZE ==")
    print(f"  clean_text   : {len(normalized.clean_text)} chars")
    print(f"  markdown_text: {len(normalized.markdown_text or '')} chars")
    print(f"  elements     : {len(normalized.elements)}")
    print(f"  tables       : {len(normalized.tables)}")
    from collections import Counter
    ec = Counter(e.element_type for e in normalized.elements)
    print(f"  element_types: {dict(ec)}")
    print()

    chunks = build_doffice_chunks(normalized)
    print("== CHUNKS (bo hien tai) ==")
    print(f"  tong so chunk: {len(chunks)}")
    sizes = [len(c.content) for c in chunks]
    if sizes:
        print(f"  size (chars) : min={min(sizes)} max={max(sizes)} avg={sum(sizes)//len(sizes)}")
    tc = Counter((c.metadata or {}).get("chunk_type") for c in chunks)
    print(f"  chunk_type   : {dict(tc)}")
    idx = sum(1 for c in chunks if (c.metadata or {}).get("indexable") is not False)
    print(f"  indexable    : {idx}/{len(chunks)}")
    print()

    dump = []
    for c in chunks:
        m = c.metadata or {}
        dump.append({
            "i": c.chunk_index,
            "chunk_type": m.get("chunk_type"),
            "indexable": m.get("indexable"),
            "chars": len(c.content),
            "content": c.content,
        })
    OUT.mkdir(parents=True, exist_ok=True)
    f = OUT / f"chunks_{id_vb}_current.json"
    f.write_text(json.dumps(dump, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Dump -> {f}")

    # In nhanh 6 chunk dau de soi do sach
    print("\n== 6 CHUNK DAU (preview) ==")
    for c in chunks[:6]:
        m = c.metadata or {}
        head = c.content[:160].replace("\n", " ")
        print(f"  [{c.chunk_index}] {m.get('chunk_type')} ({len(c.content)}c idx={m.get('indexable')}): {head}")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "1068586"))
