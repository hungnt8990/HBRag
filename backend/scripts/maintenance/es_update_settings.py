"""Cập nhật setting động cho ES index hiện có (refresh_interval, replicas).

`number_of_shards` KHÔNG đổi được sau khi tạo index — chỉ chỉnh khi tạo mới
(xem ELASTICSEARCH_NUMBER_OF_SHARDS / _index_definition).

    python -m scripts.maintenance.es_update_settings
    python -m scripts.maintenance.es_update_settings --refresh 60s --replicas 1
"""

from __future__ import annotations

import argparse
import asyncio
import logging

import httpx

from app.core.config import settings

logger = logging.getLogger("es_update_settings")


async def update_index_settings(*, index: str, refresh_interval: str, replicas: int) -> None:
    url = f"{settings.elasticsearch_url.rstrip('/')}/{index}/_settings"
    body = {"index": {"refresh_interval": refresh_interval, "number_of_replicas": replicas}}
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.put(url, json=body)
        if resp.status_code >= 400:
            raise RuntimeError(f"ES update settings failed: HTTP {resp.status_code} {resp.text[:300]}")
        logger.info("Updated %s -> %s", index, body["index"])


def cli() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Cập nhật setting động cho ES index.")
    parser.add_argument("--index", default=settings.elasticsearch_index_name)
    parser.add_argument("--refresh", default=settings.elasticsearch_refresh_interval)
    parser.add_argument("--replicas", type=int, default=settings.elasticsearch_number_of_replicas)
    args = parser.parse_args()
    asyncio.run(
        update_index_settings(index=args.index, refresh_interval=args.refresh, replicas=args.replicas)
    )


if __name__ == "__main__":
    cli()
