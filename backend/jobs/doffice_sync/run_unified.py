"""CLI job đồng bộ 3-DB (PG + ES BM25 + Qdrant 2 collection).

Output gọn (log chi tiết -> file, console chỉ spinner + summary box) như job cũ.

3 MODE (flag hoặc biến môi trường — flag ưu tiên):
| Mode        | Flag                       | Biến môi trường                     |
|-------------|----------------------------|-------------------------------------|
| Văn bản lẻ  | --id-vb 1068586 1068587    | DOFFICE_JOB_ID_VB="1068586,1068587" |
| Theo đơn vị | --don-vi 251 252           | DOFFICE_JOB_DON_VI="251,252"        |
| Tất cả      | (không flag)               | (không đặt ID_VB/DON_VI)            |

Phụ: DOFFICE_JOB_BATCH_SIZE(200) DOFFICE_JOB_WORKERS(8) DOFFICE_JOB_LIMIT
     DOFFICE_JOB_FULL_SCAN(false) — true = quét lại từ đầu (bỏ qua checkpoint).

Mode "Tất cả"/"Theo đơn vị" có CHECKPOINT: lần chạy sau tiếp tục batch kế tiếp
(resume search_after), hết thì lần sau chỉ quét văn bản MỚI cập nhật.

  python -m jobs.doffice_sync.run_unified --id-vb 1068586
  python -m jobs.doffice_sync.run_unified --don-vi 251 --batch-size 200 --limit 50
  python -m jobs.doffice_sync.run_unified                 # tất cả (resume)
  python -m jobs.doffice_sync.run_unified --full-scan      # quét lại từ đầu

Reset 3 DB trước khi chạy lần đầu:  python -m scripts.reset_all_stores --yes
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

from jobs.common import console as cs  # noqa: E402
from jobs.common.bootstrap import run_stamp  # noqa: E402
from jobs.doffice_sync.config import JobConfig  # noqa: E402
from jobs.doffice_sync.logger import setup_job_logging  # noqa: E402
from jobs.doffice_sync.models import ensure_job_tables  # noqa: E402
from jobs.doffice_sync.sync.unified_runner import UnifiedJobRunner  # noqa: E402


def _split_env(name: str) -> list[str] | None:
    raw = os.getenv(name)
    if not raw:
        return None
    parts = [p.strip() for p in raw.replace(";", ",").replace(" ", ",").split(",") if p.strip()]
    return parts or None


def _int_env(name: str, default: int | None) -> int | None:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _bool_env(name: str) -> bool:
    return (os.getenv(name) or "").strip().lower() in {"1", "true", "yes", "on"}


def _quiet_console() -> None:
    """Console chỉ WARNING+ (giấu httpx/app INFO). Chi tiết -> file log của job."""
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    for noisy in ("httpx", "httpcore", "qdrant_client", "app", "asyncio", "elasticsearch"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def _status_line(runner: UnifiedJobRunner) -> str:
    s = runner.stats
    return (
        f"{cs.BOLD}{runner.phase}…{cs.RESET} "
        f"quét={cs.BOLD}{s.scanned}{cs.RESET}  "
        f"{cs.GREEN}ingest {s.ingested}{cs.RESET}  "
        f"{cs.RED}lỗi {s.failed}{cs.RESET}"
    )


def _print_summary(runner: UnifiedJobRunner, *, mode: str, elapsed: float, log_dir: Path) -> None:
    s = runner.stats
    minutes, seconds = divmod(int(elapsed), 60)
    line = cs.color("═" * 46, cs.CYAN)

    def row(label: str, value, code: str, suffix: str = "") -> str:
        v = f"{value:,}" if isinstance(value, int) else str(value)
        return f"  {label:<13}: {cs.color(v, code)}{suffix}"

    print(
        "\n".join(
            [
                "",
                line,
                cs.color("  DOffice Sync 3-DB (PG + ES + Qdrant)", cs.BOLD + cs.CYAN),
                line,
                row("Chế độ", mode, cs.BOLD),
                row("Thời gian", f"{minutes}m {seconds}s", cs.RESET),
                row("Quét", s.scanned, cs.BOLD),
                row("Ingest", s.ingested, cs.GREEN),
                row("Lỗi", s.failed, cs.RED if s.failed else cs.GREEN),
                row("Log", f"{log_dir}/", cs.GREY),
                line,
            ]
        )
    )
    for id_vb, err in list(s.errors.items())[:10]:
        print(f"  {cs.color('✗ ' + id_vb, cs.RED)}: {err}")


async def _main(args: argparse.Namespace) -> None:
    cs.enable_ansi()
    loggers = setup_job_logging("logs/doffice_unified", run_stamp())
    _quiet_console()

    id_vb = args.id_vb or _split_env("DOFFICE_JOB_ID_VB")
    don_vi_raw = args.don_vi or _split_env("DOFFICE_JOB_DON_VI")
    don_vi = [int(v) for v in don_vi_raw] if don_vi_raw else None
    batch_size = args.batch_size if args.batch_size is not None else _int_env("DOFFICE_JOB_BATCH_SIZE", 200)
    workers = args.workers if args.workers is not None else _int_env("DOFFICE_JOB_WORKERS", 8)
    limit = args.limit if args.limit is not None else _int_env("DOFFICE_JOB_LIMIT", None)
    full_scan = bool(args.full_scan or _bool_env("DOFFICE_JOB_FULL_SCAN"))

    cfg = JobConfig.from_settings(
        id_vb_filter=id_vb, don_vi_filter=don_vi,
        batch_size=batch_size, max_workers=workers, scan_limit=limit, full_scan=full_scan,
    )
    mode = (
        "Văn bản lẻ" if id_vb
        else ("Theo đơn vị (resume)" if don_vi else ("Full scan" if full_scan else "Incremental (resume)"))
    )
    loggers.get("run").info(
        "Mode=%s id_vb=%s don_vi=%s batch=%s workers=%s limit=%s full_scan=%s",
        mode, id_vb, don_vi, batch_size, workers, limit, full_scan,
    )

    await ensure_job_tables()
    runner = UnifiedJobRunner(cfg)
    start = time.monotonic()
    spinner = cs.Spinner(lambda: _status_line(runner))
    spinner.start()
    try:
        await runner.run()
    except Exception:
        loggers.get("run").error("Job lỗi nghiêm trọng", exc_info=True)
    finally:
        await spinner.stop()
        _print_summary(runner, mode=mode, elapsed=time.monotonic() - start, log_dir=loggers.log_dir)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Job dong bo DOffice 3-DB (PG + ES + Qdrant).")
    parser.add_argument("--id-vb", nargs="+", help="id_vb le (override DOFFICE_JOB_ID_VB).")
    parser.add_argument("--don-vi", nargs="+", type=int, help="Loc theo don vi (override DOFFICE_JOB_DON_VI).")
    parser.add_argument("--batch-size", type=int, default=None)
    parser.add_argument("--workers", type=int, default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--full-scan", action="store_true", help="Quet lai tu dau (bo qua checkpoint).")
    asyncio.run(_main(parser.parse_args()))
