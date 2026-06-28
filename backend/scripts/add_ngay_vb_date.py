"""Thêm sub-field ``ngay_vb.date`` (kiểu date) vào index document v1 + populate.

Cần cho recency decay (function_score gauss theo ngày). ``_update_by_query`` reindex TẠI CHỖ
từ _source (đã có ngay_vb dạng 'yyyy-MM-dd' + embedding) -> KHÔNG re-embed, KHÔNG mất dữ liệu.

Chạy:  .venv/Scripts/python.exe scripts/add_ngay_vb_date.py            # dry-run
       .venv/Scripts/python.exe scripts/add_ngay_vb_date.py --apply    # thực thi
"""

from __future__ import annotations

import argparse
import asyncio
import sys

import httpx

sys.path.insert(0, ".")

from app.services.retrieval.retrieval_document_index import DocumentIndexStore  # noqa: E402

SUBFIELD = {
    "properties": {
        "ngay_vb": {
            "type": "keyword",
            "fields": {"date": {"type": "date", "format": "yyyy-MM-dd", "ignore_malformed": True}},
        }
    }
}


async def run(url: str, index: str, *, do_apply: bool) -> None:
    async with httpx.AsyncClient(timeout=600.0) as c:
        total = (await c.get(f"{url}/{index}/_count")).json()["count"]
        m = (await c.get(f"{url}/{index}/_mapping")).json()
        ng = next(iter(m.values()))["mappings"]["properties"].get("ngay_vb", {})
        has_sub = "date" in (ng.get("fields") or {})
        print(f"Index: {url}/{index} | {total} doc")
        print(f"Sub-field ngay_vb.date đã có? {has_sub}")
        if not do_apply:
            print("\n[DRY-RUN] Thêm --apply để thêm sub-field + reindex tại chỗ.")
            return

        print("\n[1] PUT mapping (thêm ngay_vb.date)…")
        (await c.put(f"{url}/{index}/_mapping", json=SUBFIELD)).raise_for_status()

        print("[2] _update_by_query (reindex tại chỗ để populate)… (có thể mất ~chục giây)")
        r = await c.post(
            f"{url}/{index}/_update_by_query",
            params={"conflicts": "proceed", "wait_for_completion": "true", "refresh": "true"},
        )
        r.raise_for_status()
        j = r.json()
        print(f"    updated={j.get('updated')} took={j.get('took')}ms conflicts={len(j.get('failures', []))}")

        # Verify: bao nhiêu doc có ngay_vb.date + top theo decay
        cnt = (await c.post(f"{url}/{index}/_count",
                            json={"query": {"exists": {"field": "ngay_vb.date"}}})).json()["count"]
        print(f"[VERIFY] doc có ngay_vb.date: {cnt}/{total}")
        body = {"size": 3, "_source": ["ngay_vb", "ky_hieu"],
                "sort": [{"ngay_vb.date": "desc"}]}
        rs = (await c.post(f"{url}/{index}/_search", json=body)).json()
        print("    3 văn bản mới nhất:",
              [(h["_source"].get("ngay_vb"), h["_source"].get("ky_hieu")) for h in rs["hits"]["hits"]])
        print("\n✅ Xong. Recency decay (prefer_recent) đã dùng được.")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    store = DocumentIndexStore()
    asyncio.run(run(store.url, store.index_name, do_apply=args.apply))


if __name__ == "__main__":
    main()
