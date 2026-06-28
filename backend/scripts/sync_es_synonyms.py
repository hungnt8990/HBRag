"""Đồng bộ từ viết tắt: file config/vi_synonyms.txt -> ES synonyms_set 'vi_abbreviations'.

Nguồn sự thật là FILE (version-control). Script đẩy file vào ES Synonyms API.

- Lần ĐẦU (index còn dùng synonym inline / chưa cấu hình): tự cấu hình analyzer dùng
  synonyms_set qua close/open (gián đoạn vài giây).
- Các lần SAU (đã set-based): chỉ cập nhật set + reload analyzer -> KHÔNG downtime, KHÔNG reindex.

Chạy:  .venv/Scripts/python.exe scripts/sync_es_synonyms.py            # dry-run (xem luật)
       .venv/Scripts/python.exe scripts/sync_es_synonyms.py --apply    # đồng bộ thật
"""

from __future__ import annotations

import argparse
import asyncio
import sys

import httpx

sys.path.insert(0, ".")

from app.services.retrieval.retrieval_document_index import (  # noqa: E402
    SYNONYMS_SET_NAME,
    DocumentIndexStore,
    _vi_analysis,
    load_vi_synonyms,
)

CONTENT_FIELDS = ["trich_yeu", "tom_tat", "keywords", "noi_dung"]


async def _filter_kind(c, url, index) -> str:
    """'set' nếu vi_synonyms đã dùng synonyms_set; 'inline' nếu còn synonyms tĩnh; 'none' nếu chưa có."""
    s = (await c.get(f"{url}/{index}/_settings")).json()
    filt = next(iter(s.values()))["settings"]["index"].get("analysis", {}).get("filter", {})
    vs = filt.get("vi_synonyms")
    if not vs:
        return "none"
    return "set" if "synonyms_set" in vs else "inline"


async def sync(url: str, index: str, *, do_apply: bool) -> None:
    rules = load_vi_synonyms()
    analysis = _vi_analysis()
    print(f"Index: {url}/{index}")
    print(f"Synonyms set: {SYNONYMS_SET_NAME} | luật đọc từ file: {len(rules)}")
    for r in rules[:4]:
        print(f"  {r}")
    print(f"  … (+{max(0, len(rules) - 4)} luật)")

    async with httpx.AsyncClient(timeout=60.0) as c:
        kind = await _filter_kind(c, url, index)
        print(f"Trạng thái filter vi_synonyms hiện tại: {kind}")
        if not do_apply:
            print("\n[DRY-RUN] Thêm --apply để đồng bộ. "
                  + ("Sẽ close/open 1 lần (chuyển sang set-based)." if kind != "set"
                     else "Chỉ cập nhật set + reload (không downtime)."))
            return

        # 1) Upsert synonyms_set từ file (luôn làm)
        body = {"synonyms_set": [{"id": f"r{i}", "synonyms": rule} for i, rule in enumerate(rules)]}
        r = await c.put(f"{url}/_synonyms/{SYNONYMS_SET_NAME}", json=body)
        r.raise_for_status()
        print(f"\n[1] PUT _synonyms/{SYNONYMS_SET_NAME}: {len(rules)} luật -> {r.json().get('result')}")

        # 2) Nếu chưa set-based -> cấu hình analyzer dùng synonyms_set (close/open 1 lần)
        if kind != "set":
            add_settings = {"analysis": {
                "filter": {"vi_synonyms": analysis["filter"]["vi_synonyms"]},
                "analyzer": {"vi_bm25_search": analysis["analyzer"]["vi_bm25_search"]},
            }}
            print("[2] Chuyển analyzer sang synonyms_set (close -> settings -> open)…")
            (await c.post(f"{url}/{index}/_close")).raise_for_status()
            try:
                (await c.put(f"{url}/{index}/_settings", json=add_settings)).raise_for_status()
            finally:
                (await c.post(f"{url}/{index}/_open")).raise_for_status()

            add_mapping = {"properties": {
                f: {"type": "text", "analyzer": "vi_bm25", "search_analyzer": "vi_bm25_search"}
                for f in CONTENT_FIELDS}}
            add_mapping["properties"]["noi_dung"]["index_options"] = "offsets"
            (await c.put(f"{url}/{index}/_mapping", json=add_mapping)).raise_for_status()
        else:
            print("[2] Bỏ qua (đã set-based).")

        # 3) Reload analyzer để áp luật mới (không downtime)
        rr = await c.post(f"{url}/{index}/_reload_search_analyzers")
        rr.raise_for_status()
        print(f"[3] Reload search analyzers: {rr.json().get('_shards', {}).get('successful')} shard OK")

        # 4) Verify
        ra = await c.post(f"{url}/{index}/_analyze",
                          json={"analyzer": "vi_bm25_search", "text": "qd tb khen thuong"})
        print(f"[VERIFY] 'qd tb khen thuong' -> {[t['token'] for t in ra.json()['tokens']]}")
        print("\n✅ Đồng bộ xong. Sửa file rồi chạy lại --apply để cập nhật (không downtime).")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="Đồng bộ thật (mặc định dry-run)")
    args = ap.parse_args()
    store = DocumentIndexStore()
    asyncio.run(sync(store.url, store.index_name, do_apply=args.apply))


if __name__ == "__main__":
    main()
