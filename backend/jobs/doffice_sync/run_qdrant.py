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
import re
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
# Logger RIÊNG cho văn bản nhiều chunk -> file chunks_big.log (xem logger.setup_job_logging).
chunks_logger = logging.getLogger("doffice_sync.chunks")

_QUEUE_MAXSIZE = 200
_QDRANT_RETRIES = 3
# Văn bản có > ngưỡng này chunk -> đưa vào ô "nhiều chunk" + ghi log riêng. Override bằng
# env DOFFICE_QDRANT_BIG_CHUNK.
_BIG_CHUNK_THRESHOLD = 100
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
    chunks: int = 0    # tổng chunk đã embed (cộng dồn)
    current: str = ""  # id_vb đang embed (chỉ báo còn sống)
    errors: dict[str, str] = field(default_factory=dict)
    # Văn bản nhiều chunk (> _BIG_CHUNK_THRESHOLD): (id_vb, số chunk) — hiển thị ô bên cạnh.
    big_chunks: list[tuple[str, int]] = field(default_factory=list)

    def record_chunks(self, id_vb: str, nchunk: int, threshold: int) -> None:
        """Cộng dồn chunk + ghi nhận văn bản nhiều chunk (ô bên + log riêng)."""
        self.chunks += nchunk
        if nchunk > threshold:
            self.big_chunks.append((id_vb, nchunk))
            chunks_logger.warning("id_vb=%s nhiều chunk: %s (> %s)", id_vb, nchunk, threshold)


class QdrantJobRunner:
    def __init__(
        self, *, workers: int, batch_size: int, limit: int = 0,
        big_chunk_threshold: int = _BIG_CHUNK_THRESHOLD,
    ) -> None:
        self._workers = max(1, workers)
        self._batch_size = max(1, batch_size)
        self._limit = max(0, limit)  # >0: chỉ xử lý tối đa N văn bản rồi dừng (debug)
        self._big_threshold = max(1, big_chunk_threshold)
        self.stats = QStats()
        self.phase = "Khởi tạo"
        self.feeding_done = False

    async def _build_ctx(self) -> dict:
        """Dựng tài nguyên dùng chung (catalog, gateway, stores) + đếm tổng cần embed."""
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
        return ctx

    def _make_ingestor(self, session, ctx: dict) -> DofficeUnifiedIngestor:
        return DofficeUnifiedIngestor(
            repository=DocumentRepository(session), chunking_service=None,
            llm_gateway=ctx["gateway"], sparse_provider=ctx["sparse"],
            chunks_store=ctx["chunks_store"], docmeta_store=ctx["docmeta_store"],
            bm25_store=ctx["bm25_store"], catalog=ctx["catalog"],
            unit_tree=ctx["unit_tree"], signature=ctx["signature"],
        )

    async def run_once(self) -> QStats:
        ctx = await self._build_ctx()
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

    async def run_sequential(self) -> QStats:
        """TUẦN TỰ: 1 văn bản/lần (KHÔNG song song) + IN log chi tiết từng bước.

        Mỗi văn bản: làm sạch -> chunk (in số chunk) -> embed + đẩy Qdrant -> in thời gian +
        tiến độ cộng dồn. Dừng ngay ở bước nào treo thì thấy rõ (vd 'đang embed…').
        """
        from app.services.chunkers.chunker_doffice_chunking import build_doffice_chunks
        from app.services.ingestion.ingestion_profiles import get_profile_config

        ctx = await self._build_ctx()
        self.feeding_done = True
        cfg = get_profile_config("doffice_admin")
        kw = dict(
            body_max_chars=int(cfg.get("doffice_body_max_chars") or 2800),
            body_overlap=int(cfg.get("doffice_body_overlap") or 300),
            table_max_chars=int(cfg.get("doffice_table_max_chars") or 3500),
        )
        start = time.monotonic()
        print(cs.color(f"\n== CHẠY TUẦN TỰ == tổng cần embed: {self.stats.total} văn bản\n", cs.BOLD + cs.CYAN), flush=True)
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
                if self._limit and (self.stats.done + self.stats.failed) >= self._limit:
                    return self.stats
                meta = meta or {}
                idx = self.stats.done + self.stats.failed + 1
                id_vb = str(meta.get("id_vb") or "")
                self.stats.current = id_vb
                t0 = time.monotonic()
                print(f"[{idx}/{self.stats.total}] id_vb={id_vb}  — làm sạch…", flush=True)
                try:
                    source = {k: v for k, v in meta.items() if k not in ("access", "has_embedding", "qdrant_indexed")}
                    source["noi_dung"] = parsed or ""
                    acl_lists = (meta.get("access") or {}).get("raw_assignment") or {}
                    item = DofficeJobItem(id_vb=id_vb, document_id=str(doc_id), source=source, acl_lists=acl_lists)
                    async with AsyncSessionLocal() as session:
                        ing = self._make_ingestor(session, ctx)
                        await ing.clean_data(item)
                        nchunk = len(build_doffice_chunks(item.normalized, **kw))
                        print(f"           chunk={nchunk} | lưu PG + embed TỪNG chunk + đẩy Qdrant…", flush=True)

                        def _cb(done: int, total: int) -> None:  # in tiến độ embed từng chunk (cập nhật tại chỗ)
                            print(f"\r             · đã embed {done}/{total} chunk", end="", flush=True)

                        await ing.index_qdrant(item, embed_progress=_cb)
                        if nchunk:
                            print("", flush=True)  # xuống dòng sau dòng tiến độ \r
                    self.stats.done += 1
                    self.stats.record_chunks(id_vb, item.chunk_count, self._big_threshold)
                    print(cs.color(
                        f"           ✓ XONG {item.chunk_count} chunk trong {time.monotonic() - t0:.1f}s "
                        f"| tổng: {self.stats.done}/{self.stats.total} doc · {self.stats.chunks} chunk · "
                        f"{int(time.monotonic() - start)}s", cs.GREEN), flush=True)
                except Exception as exc:  # noqa: BLE001
                    self.stats.failed += 1
                    print(cs.color(
                        f"           ✗ LỖI sau {time.monotonic() - t0:.1f}s: {type(exc).__name__}: {exc}", cs.RED),
                        flush=True)
                    logger.error("id_vb=%s tuần tự lỗi: %s", id_vb, exc, exc_info=True)
                last_id = doc_id
        return self.stats

    async def _feed(self, q: asyncio.Queue) -> None:
        """Quét PG theo lô (keyset trên id) các doc chưa embed -> đẩy vào hàng đợi."""
        last_id = None
        fed = 0
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
                if self._limit and fed >= self._limit:
                    return
                await q.put((doc_id, meta or {}, parsed or ""))
                fed += 1
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
                        self.stats.record_chunks(id_vb, item.chunk_count, self._big_threshold)
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


_ANSI_RE = re.compile(r"\033\[[0-9;]*m")


def _vis_len(text: str) -> int:
    """Độ dài HIỂN THỊ (bỏ mã màu ANSI)."""
    return len(_ANSI_RE.sub("", text))


def _pad(text: str, width: int) -> str:
    """Đệm/cắt 1 dòng về đúng ``width`` cột hiển thị (giữ màu nếu không phải cắt)."""
    visible = _vis_len(text)
    if visible > width:
        plain = _ANSI_RE.sub("", text)
        return plain[: max(0, width - 1)] + "…"
    return text + " " * (width - visible)


def _box(title: str, rows: list[str], *, width: int, color: str) -> list[str]:
    """Vẽ 1 ô có viền: tiêu đề đậm + các dòng nội dung, rộng cố định ``width`` cột."""
    inner = width - 4  # trừ "│ " ... " │"
    top = cs.color("┌" + "─" * (width - 2) + "┐", color)
    bottom = cs.color("└" + "─" * (width - 2) + "┘", color)
    bar = cs.color("│", color)
    lines = [top, f"{bar} {cs.color(_pad(cs.BOLD + title + cs.RESET, inner), color)} {bar}"]
    for row in rows:
        lines.append(f"{bar} {_pad(row, inner)} {bar}")
    lines.append(bottom)
    return lines


def _join_cols(left: list[str], right: list[str], *, gap: str = "  ") -> list[str]:
    """Ghép 2 khối ô CẠNH NHAU theo dòng (đệm khối thấp hơn cho bằng chiều cao)."""
    height = max(len(left), len(right))
    lwidth = max((_vis_len(line) for line in left), default=0)
    out: list[str] = []
    for i in range(height):
        lft = left[i] if i < len(left) else " " * lwidth
        rgt = right[i] if i < len(right) else ""
        if _vis_len(lft) < lwidth:
            lft = lft + " " * (lwidth - _vis_len(lft))
        out.append(f"{lft}{gap}{rgt}")
    return out


def _status(runner: QdrantJobRunner) -> str:
    s = runner.stats
    pending = max(0, s.total - s.done - s.failed)
    # --- Ô TRÁI: tiến độ (gọn, 1 khối; "đang embed" chỉ 1 dòng) ---
    left_rows = [
        f"{cs.color(f'{s.done:>6}', cs.GREEN)} xong   "
        f"{cs.color(f'{pending:>6}', cs.YELLOW)} chờ"
        + (f"   {cs.color(f'{s.failed} lỗi', cs.RED)}" if s.failed else ""),
        f"{cs.GREY}chunk đã embed: {s.chunks}   (tổng cần: {s.total}){cs.RESET}",
        f"{cs.GREY}đang embed: {s.current or '—'}{cs.RESET}" if pending else f"{cs.GREEN}hoàn tất lượt này{cs.RESET}",
    ]
    left = _box(f"Embed Qdrant · {runner.phase}", left_rows, width=46, color=cs.CYAN)
    # --- Ô PHẢI: văn bản nhiều chunk (> ngưỡng) ---
    big = sorted(s.big_chunks, key=lambda kv: -kv[1])
    if big:
        right_rows = [f"{cs.color(f'{n:>5}', cs.YELLOW)}  id_vb={vb}" for vb, n in big[:10]]
        if len(big) > 10:
            right_rows.append(f"{cs.GREY}… và {len(big) - 10} văn bản khác{cs.RESET}")
    else:
        right_rows = [f"{cs.GREY}(chưa có văn bản > {runner._big_threshold} chunk){cs.RESET}"]
    right = _box(f"Nhiều chunk (> {runner._big_threshold})", right_rows, width=40, color=cs.MAGENTA)
    head = f"{cs.BOLD}DOffice Qdrant · chunk + embed{cs.RESET}"
    return "\n".join([head, *_join_cols(left, right)])


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
        f"  Tổng chunk  : {cs.color(str(s.chunks), cs.BOLD)}",
        f"  Nhiều chunk : {cs.color(str(len(s.big_chunks)), cs.YELLOW if s.big_chunks else cs.GREEN)}"
        f"  {cs.GREY}(> ngưỡng -> chunks_big.log){cs.RESET}",
        f"  Thời gian   : {minutes}m {seconds}s",
        f"  Log         : {cs.color(f'{log_dir}/', cs.GREY)}",
        line,
    ]))
    for id_vb, n in sorted(s.big_chunks, key=lambda kv: -kv[1])[:15]:
        print(f"  {cs.color('▶ ' + id_vb, cs.YELLOW)}: {n} chunk")
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
    loggers = setup_job_logging("logs/jobs/doffice_qdrant", run_stamp())
    _quiet_console()

    workers = args.workers if args.workers is not None else _int_env("DOFFICE_QDRANT_WORKERS", 4)
    batch = args.batch_size if args.batch_size is not None else _int_env("DOFFICE_QDRANT_BATCH_SIZE", 200)
    interval = args.interval if args.interval is not None else _int_env("DOFFICE_QDRANT_INTERVAL", 300)
    sequential = bool(args.sequential or _int_env("DOFFICE_QDRANT_SEQUENTIAL", 0))
    limit = args.limit if args.limit is not None else _int_env("DOFFICE_QDRANT_LIMIT", 0)
    big_threshold = args.big_chunk if args.big_chunk is not None else _int_env("DOFFICE_QDRANT_BIG_CHUNK", _BIG_CHUNK_THRESHOLD)

    loggers.get("run").info(
        "Job Qdrant: workers=%s batch=%s interval=%ss sequential=%s limit=%s big_chunk>%s",
        workers, batch, interval, sequential, limit, big_threshold,
    )
    while True:
        # Tuần tự -> 1 worker (không song song).
        runner = QdrantJobRunner(
            workers=1 if sequential else workers, batch_size=batch, limit=limit,
            big_chunk_threshold=big_threshold,
        )
        start = time.monotonic()
        try:
            if sequential:
                # In log trực tiếp từng văn bản (không dùng spinner để khỏi che log).
                await runner.run_sequential()
            else:
                spinner = cs.Spinner(lambda: _status(runner))
                spinner.start()
                try:
                    await runner.run_once()
                finally:
                    await spinner.stop()
        except Exception:
            loggers.get("run").error("Job Qdrant lỗi nghiêm trọng", exc_info=True)
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
    parser.add_argument(
        "--sequential", action="store_true",
        help="Chay TUAN TU 1 van ban/lan + in log chi tiet (khong song song).",
    )
    parser.add_argument(
        "--limit", type=int, default=None, help="Chi xu ly toi da N van ban roi dung (debug).",
    )
    parser.add_argument(
        "--big-chunk", type=int, default=None,
        help="Nguong chunk de coi la 'nhieu chunk' (mac dinh 100) -> o ben canh + chunks_big.log.",
    )
    asyncio.run(_main(parser.parse_args()))
