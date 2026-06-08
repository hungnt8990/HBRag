from __future__ import annotations

import argparse
import asyncio
import csv
import logging
from pathlib import Path

from sqlalchemy import select

from app.db.session import AsyncSessionLocal
from app.models.organization import Organization

logger = logging.getLogger("seed_organizations")


async def seed_organizations(csv_path: Path) -> None:
    rows = _read_rows(csv_path)
    async with AsyncSessionLocal() as session:
        existing = {
            organization.ma_dviqly: organization
            for organization in (await session.execute(select(Organization))).scalars().all()
        }

        for row in rows:
            organization = existing.get(row["ma_dviqly"])
            if organization is None:
                organization = Organization(
                    ma_dviqly=row["ma_dviqly"],
                    ma_dviqly_cha=row["ma_dviqly_cha"],
                    ten_dviqly=row["ten_dviqly"],
                    dvi_level=row["dvi_level"],
                )
                session.add(organization)
                existing[organization.ma_dviqly] = organization
            else:
                organization.ma_dviqly_cha = row["ma_dviqly_cha"]
                organization.ten_dviqly = row["ten_dviqly"]
                organization.dvi_level = row["dvi_level"]

        await session.flush()

        for organization in existing.values():
            if organization.ma_dviqly_cha:
                parent = existing.get(organization.ma_dviqly_cha)
                if parent is None:
                    logger.warning(
                        "Parent organization not found: ma_dviqly=%s ma_dviqly_cha=%s",
                        organization.ma_dviqly,
                        organization.ma_dviqly_cha,
                    )
                organization.parent_id = parent.id if parent else None
            else:
                organization.parent_id = None

        await session.commit()
        logger.info("Seeded %s organizations from %s", len(rows), csv_path)


def _read_rows(csv_path: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    with csv_path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        required = {"MaDviqly", "MaDviqlyCha", "TenDviqly", "DviLevel"}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"CSV is missing required columns: {', '.join(sorted(missing))}")

        for line_number, raw in enumerate(reader, start=2):
            ma_dviqly = (raw.get("MaDviqly") or "").strip()
            ten_dviqly = (raw.get("TenDviqly") or "").strip()
            raw_level = (raw.get("DviLevel") or "").strip()
            if not ma_dviqly or not ten_dviqly or not raw_level:
                logger.warning("Skipping row %s: required values are missing.", line_number)
                continue
            try:
                dvi_level = int(raw_level)
            except ValueError:
                logger.warning("Skipping row %s: invalid DviLevel=%s.", line_number, raw_level)
                continue
            if dvi_level not in {1, 2, 3}:
                logger.warning("Skipping row %s: unsupported DviLevel=%s.", line_number, dvi_level)
                continue
            rows.append(
                {
                    "ma_dviqly": ma_dviqly,
                    "ma_dviqly_cha": (raw.get("MaDviqlyCha") or "").strip() or None,
                    "ten_dviqly": ten_dviqly,
                    "dvi_level": dvi_level,
                }
            )
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed organizations from CSV.")
    parser.add_argument("csv_path", type=Path)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    asyncio.run(seed_organizations(args.csv_path))


if __name__ == "__main__":
    main()
