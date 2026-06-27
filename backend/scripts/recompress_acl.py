"""Re-compress ACL của toàn bộ văn bản theo danh mục tổ chức hiện tại.

Chạy sau khi cập nhật danh mục nhân viên (scripts/load_danh_muc.py).

    python -m scripts.recompress_acl
    python -m scripts.recompress_acl --force        # tính lại bất kể chữ ký
    python -m scripts.recompress_acl --no-vector    # chỉ cập nhật PostgreSQL
"""

from __future__ import annotations

import argparse
import asyncio
import logging

from app.db.session import AsyncSessionLocal, engine
from app.services.security.security_acl_recompress import recompress_all


async def main(*, force: bool, use_vector: bool) -> None:
    vector_store = None
    if use_vector:
        from app.services.vector.vector_store import get_vector_store

        vector_store = get_vector_store()

    async with AsyncSessionLocal() as session:
        await recompress_all(session, vector_store=vector_store, force=force)
    await engine.dispose()


def cli() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Re-compress ACL theo danh mục hiện tại.")
    parser.add_argument("--force", action="store_true", help="Tính lại bất kể chữ ký danh mục.")
    parser.add_argument("--no-vector", action="store_true", help="Không cập nhật Qdrant, chỉ PostgreSQL.")
    args = parser.parse_args()
    asyncio.run(main(force=args.force, use_vector=not args.no_vector))


if __name__ == "__main__":
    cli()
