"""Job đồng bộ định kỳ văn bản DOffice -> PostgreSQL + ES document index.

Kết hợp ACL + BBQ embedding. KHÔNG đẩy Qdrant.

    python jobs/doffice_sync/run.py
    python jobs/doffice_sync/run.py --full-scan
    python jobs/doffice_sync/run.py --dry-run --id-vb 1068586
    python jobs/doffice_sync/run.py --limit 5 --workers 2
    python jobs/doffice_sync/run.py --retry-only
    python jobs/doffice_sync/run.py --don-vi 251 256
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

# Bootstrap path tối thiểu (entry point) rồi dùng helper chung ở jobs/common.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from jobs.common import console as cs  # noqa: E402
from jobs.common.bootstrap import run_stamp  # noqa: E402
from jobs.doffice_sync.config import JobConfig  # noqa: E402
from jobs.doffice_sync.logger import setup_job_logging  # noqa: E402
from jobs.doffice_sync.models import ensure_job_tables  # noqa: E402
from jobs.doffice_sync.runner import JobRunner  # noqa: E402
from jobs.doffice_sync.stores.run_result import RunResultStore  # noqa: E402


def _parse_args() -> JobConfig:
    parser = argparse.ArgumentParser(description="Đồng bộ DOffice -> Postgres + ES document index.")
    parser.add_argument("--full-scan", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--retry-only", action="store_true")
    parser.add_argument("--id-vb", nargs="+", default=None)
    parser.add_argument("--don-vi", nargs="+", type=int, default=None)
    parser.add_argument("--workers", type=int, default=None)
    parser.add_argument("--batch", type=int, default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--log-dir", default=None)
    args = parser.parse_args()
    return JobConfig.from_settings(
        full_scan=args.full_scan,
        dry_run=args.dry_run,
        retry_only=args.retry_only,
        id_vb_filter=args.id_vb,
        don_vi_filter=args.don_vi,
        max_workers=args.workers,
        batch_size=args.batch,
        scan_limit=args.limit,
        log_dir=args.log_dir,
    )


def _status_line(config: JobConfig, runner) -> str:
    """Dòng trạng thái live (spinner đọc): màu theo nhóm."""
    s = runner.stats
    return (
        f"{cs.BOLD}{runner.phase}…{cs.RESET} "
        f"quét={cs.BOLD}{s['total_scanned']}{cs.RESET}  "
        f"{cs.GREEN}tạo {s['total_created']}{cs.RESET}  "
        f"{cs.CYAN}ACL {s['total_acl_updated']}{cs.RESET}  "
        f"{cs.MAGENTA}BBQ {s['total_emb_updated']}{cs.RESET}  "
        f"{cs.GREY}bỏ qua {s['total_skipped']}{cs.RESET}  "
        f"{cs.YELLOW}chờ {s['total_no_acl']}{cs.RESET}  "
        f"{cs.RED}lỗi {s['total_failed']}{cs.RESET}"
    )


def _print_summary(*, config: JobConfig, elapsed: float, stats: dict, log_dir: Path) -> None:
    minutes, seconds = divmod(int(elapsed), 60)
    mode = "Full scan" if config.full_scan else ("Retry-only" if config.retry_only else "Incremental")
    failed = stats.get("total_failed", 0)
    line = cs.color("═" * 46, cs.CYAN)

    def row(label: str, value, code: str, suffix: str = "") -> str:
        v = f"{value:,}" if isinstance(value, int) else str(value)
        return f"  {label:<13}: {cs.color(v, code)}{suffix}"

    print(
        "\n".join(
            [
                "",
                line,
                cs.color("  DOffice Sync", cs.BOLD + cs.CYAN),
                line,
                row("Chế độ", mode + (" (dry-run)" if config.dry_run else ""), cs.BOLD),
                row("Thời gian", f"{minutes}m {seconds}s", cs.RESET),
                row("Quét", stats.get("total_scanned", 0), cs.BOLD),
                row("Tạo mới", stats.get("total_created", 0), cs.GREEN,
                    cs.color(f"  (thiếu BBQ: {stats.get('total_no_embedding', 0)})", cs.GREY)),
                row("Cập nhật ACL", stats.get("total_acl_updated", 0), cs.CYAN),
                row("Bổ sung BBQ", stats.get("total_emb_updated", 0), cs.MAGENTA),
                row("Bỏ qua", stats.get("total_skipped", 0), cs.GREY),
                row("Chờ quyền", stats.get("total_no_acl", 0), cs.YELLOW, "  ⏳"),
                row("Lỗi", failed, cs.RED if failed else cs.GREEN),
                row("Log", f"{log_dir}/", cs.GREY),
                line,
            ]
        )
    )


async def _main(config: JobConfig) -> None:
    cs.enable_ansi()
    loggers = setup_job_logging(config.log_dir, run_stamp())
    log = loggers.get("run")
    start = time.monotonic()
    log.info(
        "Bắt đầu job: workers=%d batch=%d full_scan=%s dry_run=%s retry_only=%s",
        config.max_workers, config.batch_size, config.full_scan, config.dry_run, config.retry_only,
    )

    await ensure_job_tables()
    result_store = RunResultStore()
    run_id = await result_store.create(
        config_snapshot=config.snapshot(),
        is_full_scan=config.full_scan,
        updated_after=None,
        log_file_path=str(loggers.log_dir),
    )

    runner = JobRunner(config)
    spinner = cs.Spinner(lambda: _status_line(config, runner))
    spinner.start()

    status = "success"
    outcome: dict = {"stats": {}, "error_summary": {}}
    try:
        outcome = await runner.run(run_id=run_id)
        stats = outcome["stats"]
        if stats.get("total_failed", 0) > 0:
            status = "partial" if stats["total_failed"] < stats.get("total_scanned", 0) else "failed"
    except Exception:
        status = "failed"
        log.error("Job lỗi nghiêm trọng", exc_info=True)
    finally:
        await spinner.stop()
        await result_store.finish(
            run_id, status=status,
            stats=outcome.get("stats", {}), error_summary=outcome.get("error_summary", {}),
        )
        _print_summary(
            config=config, elapsed=time.monotonic() - start,
            stats=outcome.get("stats", {}), log_dir=loggers.log_dir,
        )
    return status


def main() -> None:
    status = asyncio.run(_main(_parse_args()))
    sys.exit(1 if status == "failed" else 0)


if __name__ == "__main__":
    main()
