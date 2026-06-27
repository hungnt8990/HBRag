"""So sanh chunk markdown 1068586: bo HIEN TAI vs chonkie vs markdown-chunker."""

from __future__ import annotations

import asyncio

from app.services.document_sources import DofficeElasticsearchSource
from app.services.ingestion.ingestion_doffice_content_normalizer import normalize_doffice_source


def stat(name, chunks_text):
    sizes = [len(t) for t in chunks_text if t and t.strip()]
    if not sizes:
        print(f"{name:28} | 0 chunk"); return
    tot = sum(sizes)
    print(f"{name:28} | {len(sizes):4} chunk | tong {tot:7}c | min {min(sizes):4} max {max(sizes):4} avg {tot//len(sizes):4}")


async def main():
    src = DofficeElasticsearchSource()
    doc = await src.fetch_document_by_id_vb("1068586")
    nm = normalize_doffice_source(dict(doc.raw_source or {}))
    md = nm.markdown_text or ""
    clean = nm.clean_text or ""
    print(f"INPUT: markdown={len(md)}c  clean={len(clean)}c  tables={len(nm.tables)}\n")

    # 0) Bo hien tai
    from app.services.chunkers.chunker_doffice_chunking import build_doffice_chunks
    cur = build_doffice_chunks(nm)
    stat("HIEN TAI (doffice)", [c.content for c in cur])

    # 1) chonkie RecursiveChunker (markdown recipe)
    try:
        from chonkie import RecursiveChunker
        try:
            ch = RecursiveChunker.from_recipe("markdown", lang="en", chunk_size=512)
        except Exception:
            ch = RecursiveChunker(chunk_size=512)
        stat("chonkie Recursive(md,512tok)", [c.text for c in ch(md)])
    except Exception as e:
        print(f"chonkie loi: {e}")

    # 2) markdown-chunker
    try:
        import markdown_chunker as mc
        done = False
        for ctor in ("MarkdownChunker", "Chunker"):
            cls = getattr(mc, ctor, None)
            if cls is None:
                continue
            try:
                inst = cls(min_chunk_len=200, soft_max_len=1200, hard_max_len=2000)
            except TypeError:
                inst = cls()
            for meth in ("chunk", "split", "__call__"):
                fn = getattr(inst, meth, None)
                if fn is None:
                    continue
                res = fn(md)
                texts = [r if isinstance(r, str) else getattr(r, "text", getattr(r, "content", str(r))) for r in res]
                stat(f"markdown-chunker.{ctor}", texts); done = True; break
            if done:
                break
        if not done:
            print("markdown-chunker: API:", [a for a in dir(mc) if not a.startswith("_")])
    except Exception as e:
        print(f"markdown-chunker loi: {e}")


if __name__ == "__main__":
    asyncio.run(main())
