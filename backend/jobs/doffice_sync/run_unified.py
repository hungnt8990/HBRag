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

PIPELINE 3 LUỒNG (producer-consumer): Luồng 1 ghi PostgreSQL -> hàng đợi -> Luồng 2 (ES)
+ Luồng 3 (Qdrant) chạy song song. Số worker mỗi luồng (mặc định suy từ WORKERS; Qdrant
đông nhất vì embed chậm): DOFFICE_JOB_PG_WORKERS / DOFFICE_JOB_ES_WORKERS / DOFFICE_JOB_QDRANT_WORKERS.

Mode "Tất cả"/"Theo đơn vị" có CHECKPOINT incremental: lần chạy sau chỉ quét văn bản MỚI
cập nhật (mốc updated_after lưu sau khi quét xong). Ngắt giữa chừng -> lần sau quét lại từ
mốc cũ (idempotent, an toàn).

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
    """Console CHỈ ERROR+ (để dashboard 3 luồng không bị WARNING chèn ngang, đứng im).

    Mọi chi tiết (INFO/WARNING) vẫn được ghi đầy đủ vào FILE log của job qua
    ``setup_job_logging`` — chỉ giấu khỏi màn hình.
    """
    logging.basicConfig(level=logging.ERROR, format="%(levelname)s %(message)s")
    for noisy in ("httpx", "httpcore", "qdrant_client", "app", "asyncio", "elasticsearch"):
        logging.getLogger(noisy).setLevel(logging.ERROR)


def _status_line(runner: UnifiedJobRunner) -> str:
    """Dashboard 4 luồng (đa dòng, cập nhật tại chỗ): mỗi luồng 1 dòng — xong/đang chờ/lỗi."""
    s = runner.stats
    sep = cs.color("─" * 50, cs.GREY)
    # "đang chờ" = đã qua luồng trước nhưng luồng này chưa xử lý xong.
    clean_wait = max(0, s.scanned - s.cleaned - s.clean_failed)
    es_wait = max(0, s.cleaned - s.es_done - s.es_failed)
    qd_wait = max(0, s.cleaned - s.qdrant_done - s.qdrant_failed)

    def stage(label: str, done: int, code: str, *, wait: int = 0, fail: int = 0, note: str = "") -> str:
        body = f"{cs.color(f'{done:>6}', code)} xong"
        if wait:
            body += f"   {cs.color(f'{wait:>4}', cs.YELLOW)} đang chờ"
        if fail:
            body += f"   {cs.color(f'{fail} lỗi', cs.RED)}"
        if note:
            body += f"   {note}"
        return f"  {cs.BOLD}{label:<24}{cs.RESET}{body}"

    # Luồng 1 (PG) chạy ĐỘC LẬP, vượt trước; báo rõ "đã nạp hết" để khỏi tưởng treo.
    pg_note = cs.color("đã nạp hết ✓", cs.GREEN) if runner.feeding_done else cs.color("đang nạp…", cs.YELLOW)
    # Luồng 3 (Qdrant/embed) chậm nhất -> hiện id_vb đang embed để thấy còn sống.
    qd_note = cs.color(f"· đang embed {s.qdrant_current}", cs.GREY) if (qd_wait and s.qdrant_current) else ""

    skip_qdrant = runner.config.skip_qdrant
    title = "pipeline PG+ES (bỏ Qdrant)" if skip_qdrant else "pipeline 4 luồng"
    lines = [
        f"{cs.BOLD}DOffice Sync · {title}{cs.RESET}  —  {runner.phase}…",
        sep,
        stage("Luồng 1 · PostgreSQL (raw)", s.scanned, cs.CYAN, note=pg_note),
        stage("Luồng 2 · Làm sạch", s.cleaned, cs.YELLOW, wait=clean_wait, fail=s.clean_failed),
        stage("Luồng 3 · Elasticsearch", s.es_done, cs.GREEN, wait=es_wait, fail=s.es_failed),
    ]
    if not skip_qdrant:
        lines.append(stage("Luồng 4 · Qdrant", s.qdrant_done, cs.MAGENTA, wait=qd_wait, fail=s.qdrant_failed, note=qd_note))
    return "\n".join([*lines, sep,
    ])


def _print_summary(runner: UnifiedJobRunner, *, mode: str, elapsed: float, log_dir: Path) -> None:
    s = runner.stats
    _err = s.failed + s.clean_failed + s.es_failed + s.qdrant_failed
    minutes, seconds = divmod(int(elapsed), 60)
    line = cs.color("═" * 46, cs.CYAN)

    def row(label: str, value, code: str, suffix: str = "") -> str:
        v = f"{value:,}" if isinstance(value, int) else str(value)
        return f"  {label:<13}: {cs.color(v, code)}{suffix}"

    print(
        "\n".join(
            row_line for row_line in [
                "",
                line,
                cs.color("  DOffice Sync 3-DB (PG + ES + Qdrant)", cs.BOLD + cs.CYAN),
                line,
                row("Chế độ", mode, cs.BOLD),
                row("Thời gian", f"{minutes}m {seconds}s", cs.RESET),
                row("Luồng 1 (PG raw)", s.scanned, cs.BOLD),
                row("Luồng 2 (Làm sạch)", s.cleaned, cs.YELLOW),
                row("Luồng 3 (ES)", s.es_done, cs.CYAN),
                row("Luồng 4 (Qdrant)", s.qdrant_done, cs.GREEN),
                row("Bỏ qua", s.skipped, cs.YELLOW, suffix="  (đã xong từ lần trước - resume)") if s.skipped else "",
                row("Lỗi", _err, cs.RED if _err else cs.GREEN),
                row("Log", f"{log_dir}/", cs.GREY),
                line,
            ] if row_line
        )
    )
    for id_vb, err in list(s.errors.items())[:10]:
        print(f"  {cs.color('✗ ' + id_vb, cs.RED)}: {err}")


async def _main(args: argparse.Namespace) -> None:
    cs.enable_ansi()
    loggers = setup_job_logging("logs/jobs/doffice_unified", run_stamp())
    _quiet_console()

    id_vb = args.id_vb or _split_env("DOFFICE_JOB_ID_VB")
    don_vi_raw = args.don_vi or _split_env("DOFFICE_JOB_DON_VI")
    don_vi = [int(v) for v in don_vi_raw] if don_vi_raw else None
    batch_size = args.batch_size if args.batch_size is not None else _int_env("DOFFICE_JOB_BATCH_SIZE", 200)
    workers = args.workers if args.workers is not None else _int_env("DOFFICE_JOB_WORKERS", 8)
    limit = args.limit if args.limit is not None else _int_env("DOFFICE_JOB_LIMIT", None)
    full_scan = bool(args.full_scan or _bool_env("DOFFICE_JOB_FULL_SCAN"))
    skip_qdrant = bool(getattr(args, "skip_qdrant", False) or _bool_env("DOFFICE_JOB_SKIP_QDRANT"))

    cfg = JobConfig.from_settings(
        id_vb_filter=id_vb, don_vi_filter=don_vi,
        batch_size=batch_size, max_workers=workers, scan_limit=limit, full_scan=full_scan,
        skip_qdrant=skip_qdrant,
        pg_workers=_int_env("DOFFICE_JOB_PG_WORKERS", None),
        clean_workers=_int_env("DOFFICE_JOB_CLEAN_WORKERS", None),
        es_workers=_int_env("DOFFICE_JOB_ES_WORKERS", None),
        qdrant_workers=_int_env("DOFFICE_JOB_QDRANT_WORKERS", None),
    )
    mode = (
        "Văn bản lẻ" if id_vb
        else ("Theo đơn vị (resume)" if don_vi else ("Full scan" if full_scan else "Incremental (resume)"))
    )
    loggers.get("run").info(
        "Mode=%s id_vb=%s don_vi=%s batch=%s workers=%s limit=%s full_scan=%s",
        mode, id_vb, don_vi, batch_size, workers, limit, full_scan,
    )

    interval = args.interval if args.interval is not None else _int_env("DOFFICE_JOB_INTERVAL", 0)
    await ensure_job_tables()
    # LẶP định kỳ: quét xong đứng im chờ ``interval`` giây rồi quét lần sau (incremental).
    # id-lẻ -> chạy 1 lần (không lặp). interval<=0 -> chạy 1 lần.
    loop = interval > 0 and not id_vb
    while True:
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
        if not loop:
            break
        await _idle_countdown(interval)


async def _idle_countdown(seconds: int) -> None:
    """Đứng im hiển thị đếm ngược rồi quét lần sau (không spam log)."""
    deadline = time.monotonic() + seconds
    sp = cs.Spinner(
        lambda: cs.color(
            f"Đã quét xong — chờ {max(0, int(deadline - time.monotonic()))}s rồi quét lần sau "
            f"(Ctrl-C để dừng)…", cs.GREY,
        )
    )
    sp.start()
    try:
        await asyncio.sleep(seconds)
    finally:
        await sp.stop()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Job dong bo DOffice 3-DB (PG + ES + Qdrant).")
    parser.add_argument("--id-vb", nargs="+", help="id_vb le (override DOFFICE_JOB_ID_VB).")
    parser.add_argument("--don-vi", nargs="+", type=int, help="Loc theo don vi (override DOFFICE_JOB_DON_VI).")
    parser.add_argument("--batch-size", type=int, default=None)
    parser.add_argument("--workers", type=int, default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--full-scan", action="store_true", help="Quet lai tu dau (bo qua checkpoint).")
    parser.add_argument("--skip-qdrant", action="store_true", help="Job PG+ES: bo luong Qdrant (embed).")
    parser.add_argument("--interval", type=int, default=None, help="Giay cho giua 2 lan quet (0=chay 1 lan).")
    asyncio.run(_main(parser.parse_args()))
