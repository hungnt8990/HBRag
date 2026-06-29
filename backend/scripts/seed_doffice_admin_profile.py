"""Seed/cập nhật profile ingestion ``doffice_admin`` vào PostgreSQL.

Ghi (upsert) cấu hình chunk DOffice tune-được vào bảng ``ingestion_profile_configs``
để hiện trong RAG Config và để job/chunker đọc tham số:
  doffice_body_max_chars / doffice_body_overlap / doffice_table_max_chars.

Chạy:  python -m scripts.seed_doffice_admin_profile
"""

from __future__ import annotations

import asyncio
import logging

from app.db.session import AsyncSessionLocal
from app.repositories.ingestion_profiles import IngestionProfileRepository
from app.services.ingestion.ingestion_profiles import (
    BOOTSTRAP_PROFILE_CONFIGS,
    save_profile_config_to_database,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("seed_doffice_admin_profile")


async def main() -> None:
    config = BOOTSTRAP_PROFILE_CONFIGS["doffice_admin"]
    async with AsyncSessionLocal() as session:
        repo = IngestionProfileRepository(session)
        saved = await save_profile_config_to_database(repo, "doffice_admin", config)
        await repo.commit()
    logger.info(
        "Đã upsert profile doffice_admin: body_max=%s body_overlap=%s table_max=%s",
        saved.get("doffice_body_max_chars"),
        saved.get("doffice_body_overlap"),
        saved.get("doffice_table_max_chars"),
    )


if __name__ == "__main__":
    asyncio.run(main())
