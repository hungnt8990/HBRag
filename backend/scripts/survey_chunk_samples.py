"""Khao sat 8 van ban mau: dinh dang noi_dung + so chunk hien tai vs chonkie."""

from __future__ import annotations

import json
import re
from pathlib import Path

from app.services.chunkers.chunker_doffice_chunking import build_doffice_chunks
from app.services.ingestion.ingestion_doffice_content_normalizer import normalize_doffice_source

VB = Path(r"D:\CPC\PM\Tiep-Nhan-AI\Project\HBRag\backend\data\vb")
IDS = ["1068586", "1479034", "1479029", "1479790", "1474990", "1476649", "1475606", "1468950"]


def load_source(id_vb):
    data = json.loads((VB / f"{id_vb}.json").read_text(encoding="utf-8"))
    hits = data.get("hits", {}).get("hits", [])
    return (hits[0].get("_source") or {}) if hits else {}


def main():
    from chonkie import RecursiveChunker
    ch = RecursiveChunker.from_recipe("markdown", lang="en", chunk_size=2048)

    print(f"{'id_vb':9} {'noi_dung':>8} {'html?':5} {'#tbl':>4} {'md':>6} {'cur#':>5} {'cur_chars':>9} {'chonkie#':>8}")
    for id_vb in IDS:
        src = load_source(id_vb)
        nd = str(src.get("noi_dung") or "")
        has_html = bool(re.search(r"<(table|tr|td|div|p|br)\b", nd, re.I))
        try:
            nm = normalize_doffice_source(src)
            md = nm.markdown_text or ""
            cur = build_doffice_chunks(nm)
            cur_chars = sum(len(c.content) for c in cur)
            ck = [c for c in ch(md) if c.text.strip()] if md else []
            print(f"{id_vb:9} {len(nd):8} {str(has_html):5} {len(nm.tables):4} {len(md):6} {len(cur):5} {cur_chars:9} {len(ck):8}")
        except Exception as e:
            print(f"{id_vb:9} {len(nd):8} {str(has_html):5}  LOI: {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
