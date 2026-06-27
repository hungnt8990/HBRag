"""Lấy MỘT văn bản từ API D-Office (theo id_vb) rồi xem trước LỚP PHÂN QUYỀN.

KHÔNG ingest vào DB/Qdrant/ES. Chỉ:
  1. Gọi API D-Office ES qua DofficeElasticsearchSource.fetch_document_by_id_vb
  2. Dựng danh mục tổ chức (OrgCatalog + UnitTree) từ PostgreSQL
  3. Rút 3 list ACL (đơn vị/phòng ban/cá nhân) từ nguồn; nếu nguồn chưa có ->
     dùng ACL giả định (synthetic) đúng như luồng ingest thật
  4. Resolve + nén ACL, dựng payload chunk (acl_*) y như khi index
  5. Ghi toàn bộ ra file MỚI ``data/vb/acl_preview_<id_vb>.json``

    python -m scripts.preview_doffice_acl 1068586
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.db.session import AsyncSessionLocal, engine
from app.services.document_sources import DofficeElasticsearchSource
from app.services.security.security_acl_compressor import OrgCatalog
from app.services.security.security_acl_payload import to_chunk_payload
from app.services.security.security_acl_resolver import (
    UnitTree,
    resolve_doffice_and_compress,
)

logger = logging.getLogger("preview_doffice_acl")

VB_DIR = Path(__file__).resolve().parent.parent / "data" / "vb"


def _int_list(values: Any) -> list[int]:
    out: list[int] = []
    for value in values or []:
        try:
            out.append(int(value))
        except (TypeError, ValueError):
            continue
    return out


async def run(id_vb: str) -> None:
    source = DofficeElasticsearchSource()
    logger.info("Gọi API D-Office %s lấy id_vb=%s ...", settings.doffice_es_url, id_vb)
    doc = await source.fetch_document_by_id_vb(id_vb)
    raw = dict(doc.raw_source or {})

    # 1) Rút ACL từ nguồn; fallback synthetic giống _attach_acl_from_source.
    don_vi = _int_list(raw.get("don_vi_list"))
    phong_ban = _int_list(raw.get("phong_ban_list"))
    ca_nhan = _int_list(raw.get("ca_nhan_list"))
    acl_source = "doffice_api"
    if not (don_vi or phong_ban or ca_nhan) and settings.doffice_synthetic_acl_enabled:
        don_vi = _int_list(settings.doffice_synthetic_don_vi_list)
        phong_ban = _int_list(settings.doffice_synthetic_phong_ban_list)
        ca_nhan = _int_list(settings.doffice_synthetic_ca_nhan_list)
        acl_source = "synthetic_config"
        logger.warning("API chưa trả ACL -> dùng ACL giả định (synthetic) từ config.")

    # 2) Danh mục tổ chức từ PostgreSQL + resolve/nén ACL.
    async with AsyncSessionLocal() as session:
        catalog = await OrgCatalog.from_session(session)
        unit_tree = await UnitTree.from_session(session)
        acl, assignment, warnings = resolve_doffice_and_compress(
            don_vi_list=don_vi,
            phong_ban_list=phong_ban,
            ca_nhan_list=ca_nhan,
            catalog=catalog,
            unit_tree=unit_tree,
        )

    # Chỉ payload THỰC SỰ lưu vào chunk: 3 list allow + 2 list deny (dạng số, gọn nhất).
    # KHÔNG ghi acl_subjects (trùng allow, nặng) và KHÔNG ghi warnings (chỉ để debug).
    chunk_payload = to_chunk_payload(acl)
    audience_size = len(acl.decompress(catalog))

    clean_text = doc.clean_text or ""
    result = {
        "id_vb": doc.id_vb,
        "ky_hieu": doc.ky_hieu,
        "trich_yeu": doc.trich_yeu,
        "noi_ban_hanh": doc.noi_ban_hanh,
        "nguoi_ky": doc.nguoi_ky,
        "ten_file": doc.ten_file,
        "ngay_vb": doc.ngay_vb,
        "clean_text_chars": len(clean_text),
        "clean_text_preview": clean_text[:800],
        "phan_quyen": {
            "acl_source": acl_source,
            "chunk_payload": chunk_payload,
            "audience_size": audience_size,
        },
    }
    # 'assignment'/'warnings' chỉ dùng cho log, không ghi ra file kết quả.
    _ = (assignment, warnings)

    output = VB_DIR / f"acl_preview_{doc.id_vb}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("ACL nguồn=%s -> %s", acl_source, acl.to_dict())
    logger.info("Số người được xem (giải nén): %s", audience_size)
    logger.info("Đã ghi -> %s", output)
    await engine.dispose()


def cli() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Xem trước phân quyền 1 văn bản DOffice theo id_vb.")
    parser.add_argument("id_vb", help="id_vb của văn bản cần lấy từ API D-Office.")
    args = parser.parse_args()
    asyncio.run(run(args.id_vb))


if __name__ == "__main__":
    cli()
