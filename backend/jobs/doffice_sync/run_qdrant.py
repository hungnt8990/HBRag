"""Job RIÊNG: chunk + embed + đẩy Qdrant cho văn bản DOffice ĐÃ có trong PostgreSQL.

Tách khỏi job PG+ES (``run_unified --skip-qdrant``). Đọc doc THÔ từ PG (những doc có cờ
``qdrant_indexed`` != true) -> làm sạch (normalize + nén ACL) -> chunk -> embed -> Qdrant
Col1 (chunks) + Col2 (docmeta) -> đánh dấu ``qdrant_indexed=true``.

Idempotent + resume qua CỜ PG: re-sync (job PG+ES) tạo lại doc -> cờ về false -> embed lại.
Chạy LẶP định kỳ: quét xong đứng im chờ ``--interval`` giây rồi quét lần sau (mặc định 300s;
0 = chạy 1 lần rồi thoát). Dùng khi model embedding chập chờn — cứ để chạy, có gì mới thì embed.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sqlalchemy import text  # noqa: E402

from app.db.session import AsyncSessionLocal  # noqa: E402
from app.repositories.documents import DocumentRepository  # noqa: E402
from app.services.document_sources import DOFFICE_SOURCE_TYPE  # noqa: E402
from app.services.embeddings.embedding_sparse_factory import get_sparse_embedding_provider  # noqa: E402
from app.services.ingestion.ingestion_doffice_unified import DofficeJobItem, DofficeUnifiedIngestor  # noqa: E402
from app.services.llm_gateway import get_llm_gateway  # noqa: E402
from app.services.retrieval.retrieval_doffice_bm25 import DofficeBm25DocumentStore  # noqa: E402
from app.services.security.security_acl_compressor import OrgCatalog  # noqa: E402
from app.services.security.security_acl_recompress import catalog_signature  # noqa: E402
from app.services.security.security_acl_resolver import UnitTree  # noqa: E402
from app.services.vector.vector_store import (  # noqa: E402
    get_doffice_chunks_vector_store,
    get_doffice_docmeta_vector_store,
)
from jobs.common import console as cs  # noqa: E402
from jobs.common.bootstrap import run_stamp  # noqa: E402
from jobs.doffice_sync.logger import setup_job_logging  # noqa: E402

logger = logging.getLogger("doffice_sync.qdrant")

_QUEUE_MAXSIZE = 200
_QDRANT_RETRIES = 3
# Truy vấn doc chưa embed (cờ qdrant_indexed != 'true').
_WHERE_PENDING = (
    "source_type = :t AND coalesce(document_metadata->>'qdrant_indexed','false') <> 'true'"
)


def _int_env(name: str, default):
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _quiet_console() -> None:
    logging.basicConfig(level=logging.ERROR, format="%(levelname)s %(message)s")
    for noisy in ("httpx", "httpcore", "qdrant_client", "app", "asyncio", "elasticsearch"):
        logging.getLogger(noisy).setLevel(logging.ERROR)


@dataclass
class QStats:
    total: int = 0     # số doc CẦN embed (chưa qdrant_indexed) đầu lượt
    done: int = 0
    failed: int = 0
    current: str = ""  # id_vb đang embed (chỉ báo còn sống)
    errors: dict[str, str] = field(default_factory=dict)


class QdrantJobRunner:
    def __init__(self, *, workers: int, batch_size: int) -> None:
        self._workers = max(1, workers)
        self._batch_size = max(1, batch_size)
        self.stats = QStats()
        self.phase = "Khởi tạo"
        self.feeding_done = False

    async def run_once(self) -> QStats:
        # Tài nguyên dùng chung
        async with AsyncSessionLocal() as s:
            catalog = await OrgCatalog.from_session(s)
            unit_tree = await UnitTree.from_session(s)
        signature = catalog_signature(catalog)
        async with AsyncSessionLocal() as s:
            from app.repositories.ingestion_profiles import IngestionProfileRepository
            from app.services.ingestion.ingestion_profiles import load_profile_configs

            pr = IngestionProfileRepository(s)
            await load_profile_configs(pr)
            await pr.commit()
        ctx = dict(
            catalog=catalog, unit_tree=unit_tree, signature=signature,
            gateway=get_llm_gateway(), sparse=get_sparse_embedding_provider(),
            chunks_store=get_doffice_chunks_vector_store(),
            docmeta_store=get_doffice_docmeta_vector_store(),
            bm25_store=DofficeBm25DocumentStore(),
        )
        await ctx["chunks_store"].ensure_collection()
        await ctx["docmeta_store"].ensure_collection()

        async with AsyncSessionLocal() as s:
            self.stats.total = (
                await s.execute(text(f"SELECT count(*) FROM documents WHERE {_WHERE_PENDING}"),
                                {"t": DOFFICE_SOURCE_TYPE})
            ).scalar() or 0
        self.phase = "Embed"

        q: asyncio.Queue = asyncio.Queue(maxsize=_QUEUE_MAXSIZE)
        workers = [asyncio.create_task(self._worker(q, ctx)) for _ in range(self._workers)]
        try:
            await self._feed(q)
            self.feeding_done = True
        finally:
            for _ in workers:
                await q.put(None)
            await asyncio.gather(*workers)
        return self.stats

    async def _feed(self, q: asyncio.Queue) -> None:
        """Quét PG theo lô (keyset trên id) các doc chưa embed -> đẩy vào hàng đợi."""
        last_id = None
        while True:
            params = {"t": DOFFICE_SOURCE_TYPE, "lim": self._batch_size}
            sql = f"SELECT id, document_metadata, parsed_text FROM documents WHERE {_WHERE_PENDING} "
            if last_id is not None:
                sql += "AND id > :last "
                params["last"] = last_id
            sql += "ORDER BY id LIMIT :lim"
            async with AsyncSessionLocal() as s:
                rows = (await s.execute(text(sql), params)).all()
            if not rows:
                break
            for doc_id, meta, parsed in rows:
                await q.put((doc_id, meta or {}, parsed or ""))
                last_id = doc_id

    async def _worker(self, q: asyncio.Queue, ctx: dict) -> None:
        while True:
            task = await q.get()
            try:
                if task is None:
                    break
                doc_id, meta, parsed = task
                id_vb = str(meta.get("id_vb") or "")
                self.stats.current = id_vb
                # Dựng lại source THÔ từ PG: trường metadata (bỏ field nội bộ) + noi_dung=parsed_text.
                source = {
                    k: v for k, v in meta.items()
                    if k not in ("access", "has_embedding", "qdrant_indexed")
                }
                source["noi_dung"] = parsed
                acl_lists = (meta.get("access") or {}).get("raw_assignment") or {}
                item = DofficeJobItem(
                    id_vb=id_vb, document_id=str(doc_id), source=source, acl_lists=acl_lists
                )
                last_exc: Exception | None = None
                for attempt in range(_QDRANT_RETRIES):
                    try:
                        async with AsyncSessionLocal() as session:
                            ing = DofficeUnifiedIngestor(
                                repository=DocumentRepository(session), chunking_service=None,
                                llm_gateway=ctx["gateway"], sparse_provider=ctx["sparse"],
                                chunks_store=ctx["chunks_store"], docmeta_store=ctx["docmeta_store"],
                                bm25_store=ctx["bm25_store"], catalog=ctx["catalog"],
                                unit_tree=ctx["unit_tree"], signature=ctx["signature"],
                            )
                            await ing.clean_data(item)
                            await ing.index_qdrant(item)
                        self.stats.done += 1
                        last_exc = None
                        break
                    except Exception as exc:  # noqa: BLE001
                        last_exc = exc
                        if attempt < _QDRANT_RETRIES - 1:
                            await asyncio.sleep(2 * (attempt + 1))
                if last_exc is not None:
                    self.stats.failed += 1
                    self.stats.errors[id_vb] = f"{type(last_exc).__name__}: {last_exc}"
                    logger.error(
                        "id_vb=%s Qdrant lỗi (sau %s lần thử): %s",
                        id_vb, _QDRANT_RETRIES, last_exc, exc_info=True,
                    )
            finally:
                q.task_done()


def _status(runner: QdrantJobRunner) -> str:
    s = runner.stats
    sep = cs.color("─" * 50, cs.GREY)
    pending = max(0, s.total - s.done - s.failed)
    cur = cs.color(f"· đang embed {s.current}", cs.GREY) if (pending and s.current) else ""
    body = (
        f"{cs.color(f'{s.done:>6}', cs.GREEN)} xong"
        f"   {cs.color(f'{pending:>5}', cs.YELLOW)} chờ"
    )
    if s.failed:
        body += f"   {cs.color(f'{s.failed} lỗi', cs.RED)}"
    if cur:
        body += f"   {cur}"
    return "\n".join([
        f"{cs.BOLD}DOffice Qdrant · chunk + embed{cs.RESET}  —  {runner.phase}…",
        sep,
        f"  {cs.BOLD}{'Embed vào Qdrant':<22}{cs.RESET}{body}",
        f"  {cs.GREY}(tổng cần embed lượt này: {s.total}){cs.RESET}",
        sep,
    ])


def _print_summary(runner: QdrantJobRunner, elapsed: float, log_dir: Path) -> None:
    s = runner.stats
    minutes, seconds = divmod(int(elapsed), 60)
    line = cs.color("═" * 46, cs.CYAN)
    print("\n".join([
        "",
        line,
        cs.color("  DOffice Qdrant (chunk + embed)", cs.BOLD + cs.CYAN),
        line,
        f"  Cần embed   : {cs.color(str(s.total), cs.BOLD)}",
        f"  Đã embed    : {cs.color(str(s.done), cs.GREEN)}",
        f"  Lỗi         : {cs.color(str(s.failed), cs.RED if s.failed else cs.GREEN)}",
        f"  Thời gian   : {minutes}m {seconds}s",
        f"  Log         : {cs.color(f'{log_dir}/', cs.GREY)}",
        line,
    ]))
    for id_vb, err in list(s.errors.items())[:10]:
        print(f"  {cs.color('✗ ' + id_vb, cs.RED)}: {err}")


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


async def _main(args: argparse.Namespace) -> None:
    cs.enable_ansi()
    loggers = setup_job_logging("logs/doffice_qdrant", run_stamp())
    _quiet_console()

    workers = args.workers if args.workers is not None else _int_env("DOFFICE_QDRANT_WORKERS", 4)
    batch = args.batch_size if args.batch_size is not None else _int_env("DOFFICE_QDRANT_BATCH_SIZE", 200)
    interval = args.interval if args.interval is not None else _int_env("DOFFICE_QDRANT_INTERVAL", 300)

    loggers.get("run").info("Job Qdrant: workers=%s batch=%s interval=%ss", workers, batch, interval)
    while True:
        runner = QdrantJobRunner(workers=workers, batch_size=batch)
        start = time.monotonic()
        spinner = cs.Spinner(lambda: _status(runner))
        spinner.start()
        try:
            await runner.run_once()
        except Exception:
            loggers.get("run").error("Job Qdrant lỗi nghiêm trọng", exc_info=True)
        finally:
            await spinner.stop()
        _print_summary(runner, time.monotonic() - start, loggers.log_dir)
        if not interval or interval <= 0:
            break
        await _idle_countdown(interval)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Job Qdrant DOffice (chunk + embed tu PG).")
    parser.add_argument("--workers", type=int, default=None, help="So worker embed (mac dinh 4).")
    parser.add_argument("--batch-size", type=int, default=None, help="Lo quet PG (mac dinh 200).")
    parser.add_argument(
        "--interval", type=int, default=None,
        help="Giay cho giua 2 lan quet (mac dinh 300; 0 = chay 1 lan roi thoat).",
    )
    asyncio.run(_main(parser.parse_args()))
