"""Xuất bản xem trước LỚP PHÂN QUYỀN cho các văn bản mẫu trong data/vb.

KHÔNG ingest vào DB/Qdrant/ES. Chỉ đọc danh mục tổ chức từ PostgreSQL rồi áp đúng
resolver + compressor đã có (không sửa thuật toán), và ghi kết quả ra file MỚI
``data/vb/acl_preview.json`` (không đụng các file JSON nguồn).

    python -m scripts.export_acl_preview            # xoá Qdrant/ES rồi xuất file
    python -m scripts.export_acl_preview --no-clean # chỉ xuất file, không xoá store
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from pathlib import Path
from typing import Any

import httpx

from app.core.config import settings
from app.db.session import AsyncSessionLocal, engine
from app.services.security.security_acl_compressor import OrgCatalog
from app.services.security.security_acl_resolver import (
    UnitTree,
    resolve_doffice_and_compress,
)
from app.services.vector.vector_store import get_vector_store

logger = logging.getLogger("export_acl_preview")

VB_DIR = Path(__file__).resolve().parent.parent / "data" / "vb"
OUTPUT = VB_DIR / "acl_preview.json"


async def _clean_stores() -> None:
    vs = get_vector_store()
    logger.info("Xoá Qdrant collection %s ...", settings.qdrant_collection_name)
    await vs.recreate_collection()
    url = f"{settings.elasticsearch_url.rstrip('/')}/{settings.elasticsearch_index_name}"
    logger.info("Xoá Elasticsearch index %s ...", settings.elasticsearch_index_name)
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.delete(url)
            logger.info("  ES DELETE -> %s", resp.status_code)
    except httpx.HTTPError as exc:
        logger.warning("  ES delete bỏ qua: %s", exc)


def _dept_to_unit(catalog: OrgCatalog) -> dict[int, int]:
    """Suy ra phòng ban -> công ty từ catalog.unit_departments (đảo chiều)."""
    out: dict[int, int] = {}
    for unit_id, depts in catalog.unit_departments.items():
        for pb in depts:
            out[pb] = unit_id
    return out


async def _process_file(session, path: Path, catalog: OrgCatalog, unit_tree: UnitTree) -> dict[str, Any] | None:
    data = json.loads(path.read_text(encoding="utf-8"))
    hits = data.get("hits", {}).get("hits", [])
    if not hits:
        return None
    src = hits[0].get("_source") or {}

    dv = [int(x) for x in (src.get("don_vi_list") or [])]
    pb = [int(x) for x in (src.get("phong_ban_list") or [])]
    nv = [int(x) for x in (src.get("ca_nhan_list") or [])]

    acl, _assignment, warnings = resolve_doffice_and_compress(
        don_vi_list=dv, phong_ban_list=pb, ca_nhan_list=nv, catalog=catalog, unit_tree=unit_tree
    )

    # Công ty (bối cảnh): công ty của các phòng/cá nhân được cấp quyền + đơn vị khai báo.
    dept_unit = _dept_to_unit(catalog)
    cong_ty: set[int] = set(acl.allow_unit_ids) | set(dv)
    for pb_id in acl.allow_department_ids:
        if pb_id in dept_unit:
            cong_ty.add(dept_unit[pb_id])
    for nv_id in acl.allow_user_ids:
        loc = catalog.user_location.get(nv_id)
        if loc and loc[0] is not None:
            cong_ty.add(loc[0])

    return {
        "source_file": path.name,
        "id_vb": str(src.get("id_vb")),
        "ky_hieu": src.get("ky_hieu"),
        "doffice_raw": {"don_vi_list": dv, "phong_ban_list": pb, "ca_nhan_list": nv},
        "acl": {
            "cong_ty": sorted(cong_ty),                          # bối cảnh: phải có công ty mới có phòng ban
            "allow_toan_bo_cong_ty": sorted(acl.allow_unit_ids),  # cho CẢ công ty xem (roll-up)
            "allow_phong_ban": sorted(acl.allow_department_ids),
            "allow_ca_nhan": sorted(acl.allow_user_ids),
            "deny_phong_ban": sorted(acl.deny_department_ids),
            "deny_ca_nhan": sorted(acl.deny_user_ids),
        },
        "warnings": warnings,
    }


async def run(*, clean: bool) -> None:
    if clean:
        await _clean_stores()

    async with AsyncSessionLocal() as session:
        catalog = await OrgCatalog.from_session(session)
        unit_tree = await UnitTree.from_session(session)

        results = []
        for path in sorted(VB_DIR.glob("*.json")):
            if path.name == OUTPUT.name:
                continue
            entry = await _process_file(session, path, catalog, unit_tree)
            if entry is None:
                logger.warning("Bỏ qua %s (không phải response DOffice)", path.name)
                continue
            results.append(entry)
            logger.info("%s (id_vb=%s) -> %s", path.name, entry["id_vb"], entry["acl"])

    OUTPUT.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("Đã ghi %d văn bản -> %s", len(results), OUTPUT)
    await engine.dispose()


def cli() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Xuất xem trước lớp phân quyền cho data/vb.")
    parser.add_argument("--no-clean", action="store_true", help="Không xoá Qdrant/ES.")
    args = parser.parse_args()
    asyncio.run(run(clean=not args.no_clean))


if __name__ == "__main__":
    cli()
