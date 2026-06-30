"""CLI XOA van ban DOffice khoi CA 3 DB (PostgreSQL + Elasticsearch + Qdrant).

2 CHE DO (flag hoac bien moi truong; flag uu tien):
| Che do        | Flag                    | Bien moi truong                     |
|---------------|-------------------------|-------------------------------------|
| Theo van ban  | --id-vb 1068586 1479029 | DOFFICE_DEL_ID_VB="1068586,1479029" |
| Theo don vi   | --don-vi 251 252        | DOFFICE_DEL_DON_VI="251,252"         |

Xoa moi van ban khop khoi:
  - PostgreSQL : Document + chunks
  - Elasticsearch : hbrag_doffice_documents_v1 (theo id_vb)
  - Qdrant : hbrag_doffice_chunks_v1 + hbrag_doffice_docmeta_v1 (theo document_id)

"Theo don vi" = van ban co document_metadata.id_dv_ban_hanh khop (don vi ban hanh).
YEU CAU XAC NHAN (go 'yes') truoc khi xoa, tru khi --yes / DOFFICE_DEL_YES=1.

  python -m jobs.doffice_sync.run_delete --id-vb 1068586
  python -m jobs.doffice_sync.run_delete --don-vi 251 --yes
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sqlalchemy import text  # noqa: E402

from app.db.session import AsyncSessionLocal  # noqa: E402
from app.repositories.documents import DocumentRepository  # noqa: E402
from app.services.document_sources import DOFFICE_SOURCE_TYPE  # noqa: E402
from app.services.ingestion.ingestion_doffice_unified import DofficeUnifiedIngestor  # noqa: E402
from app.services.retrieval.retrieval_doffice_bm25 import DofficeBm25DocumentStore  # noqa: E402
from app.services.vector.vector_store import (  # noqa: E402
    get_doffice_chunks_vector_store,
    get_doffice_docmeta_vector_store,
)
from jobs.common import console as cs  # noqa: E402
from jobs.common.bootstrap import run_stamp  # noqa: E402
from jobs.doffice_sync.logger import setup_job_logging  # noqa: E402


def _split_env(name: str) -> list[str] | None:
    raw = os.getenv(name)
    if not raw:
        return None
    parts = [p.strip() for p in raw.replace(";", ",").replace(" ", ",").split(",") if p.strip()]
    return parts or None


def _quiet_console() -> None:
    logging.basicConfig(level=logging.ERROR, format="%(levelname)s %(message)s")
    for noisy in ("httpx", "httpcore", "qdrant_client", "app", "asyncio", "elasticsearch"):
        logging.getLogger(noisy).setLevel(logging.ERROR)


def _make_ingestor(session) -> DofficeUnifiedIngestor:
    # Xoa khong can ACL/chunking/embed -> de None nhung kep cac store + repository.
    return DofficeUnifiedIngestor(
        repository=DocumentRepository(session),
        chunking_service=None,
        llm_gateway=None,
        sparse_provider=None,
        chunks_store=get_doffice_chunks_vector_store(),
        docmeta_store=get_doffice_docmeta_vector_store(),
        bm25_store=DofficeBm25DocumentStore(),
        catalog=None,
        unit_tree=None,
    )


async def _id_vbs_for_don_vi(session, don_vi: list[str]) -> list[str]:
    """Tat ca id_vb thuoc don vi -> KHOP voi luc sync.

    Ingest loc nguon bang ``don_vi_list`` (don vi QUAN LY/NHAN van ban), KHONG phai
    ``id_dv_ban_hanh`` (don vi ban hanh). Vd VB do 251 ban hanh nhung gui toi 256 ->
    don_vi_list=[251,256] -> thuoc ca don vi 256. Nen xoa cung phai khop don_vi_list.
    """
    ids = [int(d) for d in don_vi if str(d).strip().isdigit()]
    if not ids:
        return []
    rows = (
        await session.execute(
            text(
                "SELECT document_metadata->>'id_vb' FROM documents "
                "WHERE source_type = :st AND EXISTS ("
                "  SELECT 1 FROM jsonb_array_elements_text("
                "    document_metadata->'access'->'raw_assignment'->'don_vi_list') AS e "
                "  WHERE e ~ '^[0-9]+$' AND e::int = ANY(:dv))"
            ),
            {"st": DOFFICE_SOURCE_TYPE, "dv": ids},
        )
    ).scalars().all()
    return [r for r in rows if r]


async def _main(args: argparse.Namespace) -> None:
    cs.enable_ansi()
    loggers = setup_job_logging("logs/jobs/doffice_delete", run_stamp())
    _quiet_console()

    id_vb = args.id_vb or _split_env("DOFFICE_DEL_ID_VB")
    don_vi = args.don_vi or _split_env("DOFFICE_DEL_DON_VI")
    yes = bool(args.yes or (os.getenv("DOFFICE_DEL_YES", "").strip().lower() in {"1", "true", "yes", "on"}))

    if not id_vb and not don_vi:
        print(cs.color(
            "Chua cau hinh xoa. Dat DOFFICE_DEL_ID_VB (theo van ban) hoac DOFFICE_DEL_DON_VI "
            "(theo don vi), hoac dung --id-vb / --don-vi.", cs.RED,
        ))
        return

    # --- Resolve danh sach id_vb can xoa ---
    if don_vi:
        async with AsyncSessionLocal() as session:
            resolved = await _id_vbs_for_don_vi(session, don_vi)
        targets = sorted(set((id_vb or []) + resolved))
        mode = f"Theo don vi {don_vi}" + (f" + id_vb {id_vb}" if id_vb else "")
    else:
        targets = sorted(set(id_vb or []))
        mode = f"Theo van ban ({len(targets)})"

    if not targets:
        print(cs.color("Khong tim thay van ban nao khop -> khong xoa gi.", cs.YELLOW))
        return

    # --- Xac nhan ---
    print(cs.color(f"\nSE XOA {len(targets)} van ban khoi PG + ES + Qdrant ({mode}):", cs.BOLD + cs.RED))
    print("  " + ", ".join(targets[:30]) + (" ..." if len(targets) > 30 else ""))
    if not yes:
        if not sys.stdin.isatty():
            print(cs.color("Thieu xac nhan (stdin khong phai terminal). Them --yes hoac DOFFICE_DEL_YES=1.", cs.RED))
            return
        ans = input(cs.color("Go 'yes' de xac nhan xoa: ", cs.YELLOW)).strip().lower()
        if ans != "yes":
            print("Da huy.")
            return

    deleted = not_found = failed = 0
    start = time.monotonic()

    def _status() -> str:
        return (
            f"Dang xoa… {cs.color(str(deleted + not_found + failed), cs.BOLD)}/{len(targets)}  "
            f"{cs.GREEN}xoa {deleted}{cs.RESET}  {cs.YELLOW}khong thay {not_found}{cs.RESET}  "
            f"{cs.RED}loi {failed}{cs.RESET}"
        )

    spinner = cs.Spinner(_status)
    spinner.start()
    try:
        for idv in targets:
            try:
                async with AsyncSessionLocal() as session:
                    ok = await _make_ingestor(session).delete_by_id_vb(idv)
                if ok:
                    deleted += 1
                else:
                    not_found += 1
            except Exception as exc:  # noqa: BLE001
                failed += 1
                loggers.get("delete").error("Xoa id_vb=%s loi: %s", idv, exc, exc_info=True)
    finally:
        await spinner.stop()

    line = cs.color("=" * 46, cs.CYAN)
    print("\n".join([
        "",
        line,
        cs.color("  XOA van ban DOffice (PG + ES + Qdrant)", cs.BOLD + cs.CYAN),
        line,
        f"  Che do      : {cs.color(mode, cs.BOLD)}",
        f"  Da xoa      : {cs.color(str(deleted), cs.GREEN)}",
        f"  Khong thay  : {cs.color(str(not_found), cs.YELLOW)} (PG khong co; da don ES theo id_vb neu con)",
        f"  Loi         : {cs.color(str(failed), cs.RED if failed else cs.GREEN)}",
        f"  Thoi gian   : {int(time.monotonic() - start)}s",
        f"  Log         : {cs.color(f'{loggers.log_dir}/', cs.GREY)}",
        line,
    ]))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Xoa van ban DOffice khoi PG + ES + Qdrant.")
    parser.add_argument("--id-vb", nargs="+", help="id_vb le (override DOFFICE_DEL_ID_VB).")
    parser.add_argument("--don-vi", nargs="+", help="id don vi ban hanh (override DOFFICE_DEL_DON_VI).")
    parser.add_argument("--yes", action="store_true", help="Bo qua xac nhan.")
    asyncio.run(_main(parser.parse_args()))
