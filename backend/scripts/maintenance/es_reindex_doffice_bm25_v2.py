"""Build the DOffice BM25 v2 index and optionally switch the read alias.

Default mode is dry-run. Use ``--apply`` to write the destination index and
``--switch-alias`` only after validating golden queries against the v2 index.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from typing import Any

import httpx

from app.core.config import settings
from app.services.retrieval.retrieval_doffice_bm25 import (
    DofficeBm25DocumentStore,
    extract_doffice_body_text,
)


def _bulk_lines(index_name: str, hits: list[dict[str, Any]]) -> bytes:
    lines: list[str] = []
    for hit in hits:
        source = dict(hit.get("_source") or {})
        doc_id = str(hit.get("_id") or source.get("id_vb") or source.get("document_id"))
        if not doc_id:
            continue
        source["noi_dung_body"] = extract_doffice_body_text(source.get("noi_dung"))
        lines.append(json.dumps({"index": {"_index": index_name, "_id": doc_id}}, ensure_ascii=False))
        lines.append(json.dumps(source, ensure_ascii=False))
    return ("\n".join(lines) + "\n").encode("utf-8") if lines else b""


async def _count(client: httpx.AsyncClient, url: str, index_name: str) -> int:
    resp = await client.get(f"{url}/{index_name}/_count")
    if resp.status_code == 404:
        return 0
    resp.raise_for_status()
    return int(resp.json().get("count") or 0)


async def reindex(args: argparse.Namespace) -> None:
    url = settings.elasticsearch_url.rstrip("/")
    source_index = args.source
    dest_index = args.dest
    alias = args.alias

    async with httpx.AsyncClient(timeout=args.timeout) as client:
        source_count = await _count(client, url, source_index)
        print(f"source={source_index} count={source_count}")
        if not args.apply:
            print("dry-run: add --apply to create/write destination index")
            return

        if args.recreate:
            resp = await client.delete(f"{url}/{dest_index}")
            if resp.status_code not in (200, 404):
                raise RuntimeError(f"delete {dest_index} failed: HTTP {resp.status_code} {resp.text[:300]}")

    store = DofficeBm25DocumentStore(index_name=dest_index, timeout_seconds=args.timeout)
    await store.ensure_index()

    async with httpx.AsyncClient(timeout=args.timeout) as client:
        search_body = {
            "size": args.batch_size,
            "sort": ["_doc"],
            "_source": True,
            "query": {"match_all": {}},
        }
        resp = await client.post(f"{url}/{source_index}/_search", params={"scroll": args.scroll}, json=search_body)
        resp.raise_for_status()
        payload = resp.json()
        scroll_id = payload.get("_scroll_id")
        indexed = 0
        while True:
            hits = payload.get("hits", {}).get("hits", [])
            if not hits:
                break
            body = _bulk_lines(dest_index, hits)
            if body:
                bulk = await client.post(
                    f"{url}/_bulk",
                    content=body,
                    headers={"Content-Type": "application/x-ndjson"},
                )
                bulk.raise_for_status()
                result = bulk.json()
                if result.get("errors"):
                    first = next(
                        (it["index"].get("error") for it in result.get("items", []) if it.get("index", {}).get("error")),
                        None,
                    )
                    raise RuntimeError(f"bulk item failed: {str(first)[:300]}")
                indexed += len(hits)
                print(f"indexed={indexed}")
            if not scroll_id:
                break
            resp = await client.post(f"{url}/_search/scroll", json={"scroll": args.scroll, "scroll_id": scroll_id})
            resp.raise_for_status()
            payload = resp.json()
            scroll_id = payload.get("_scroll_id")

        await client.post(f"{url}/{dest_index}/_refresh")
        dest_count = await _count(client, url, dest_index)
        print(f"dest={dest_index} count={dest_count}")

        if args.switch_alias:
            actions: list[dict[str, Any]] = []
            alias_resp = await client.get(f"{url}/_alias/{alias}")
            if alias_resp.status_code != 404:
                alias_resp.raise_for_status()
                actions.extend(
                    {"remove": {"index": index_name, "alias": alias}}
                    for index_name in alias_resp.json()
                )
            actions.append({"add": {"index": dest_index, "alias": alias}})
            resp = await client.post(f"{url}/_aliases", json={"actions": actions})
            resp.raise_for_status()
            print(f"alias {alias} -> {dest_index}")
        else:
            print("alias unchanged; run with --switch-alias after validation")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default=settings.doffice_documents_index_name)
    parser.add_argument("--dest", default=settings.doffice_documents_index_v2_name)
    parser.add_argument("--alias", default=settings.doffice_documents_index_alias)
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--scroll", default="2m")
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--recreate", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--switch-alias", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    asyncio.run(reindex(parse_args()))
