"""Job KHO AI DÙNG CHUNG (1/2): quét ``kho_ai_dung_chung`` -> làm sạch -> chunk -> ghi
``kho_ai_dung_chung_chunk`` trên Elasticsearch.

Luồng:
  1. Quét ES nguồn ``kho_ai_dung_chung`` theo batch.
  2. Xác định văn bản CHƯA chunk = kiểm tra trực tiếp trên ES (``id_full`` chưa có trong
     ``kho_ai_dung_chung_chunk``) — KHÔNG lưu trạng thái/checkpoint trên PostgreSQL.
     Chỉ chunk văn bản CHƯA có chunk.
  3. Làm sạch ``ocr_content`` (tái dùng normalizer DOffice) TRƯỚC khi chunk.
  4. Chunk bằng ``build_doffice_chunks`` (profile ``doffice_admin``).
  5. Ghi chunk vào ``kho_ai_dung_chung_chunk`` (``id`` UUIDv7 mới/chunk, ``id_full`` = id doc
     nguồn). Chunk KHÔNG lưu PostgreSQL.

Hiển thị: MỘT bảng thông tin cập nhật TẠI CHỖ (không cuộn nhiều dòng) — tổng nguồn, đã chunk,
chưa chunk, đang xử lý. Dừng an toàn: nhấn Ctrl-C sẽ dừng SAU khi chunk xong văn bản hiện tại
(không cắt ngang giữa chừng). Loop: mặc định 300s (5 phút) quét lại 1 lần — đổi bằng
``--interval``/env ``KHO_JOB_INTERVAL`` (0 = chạy 1 lần rồi thoát).

Phạm vi: ``--issuer-org 256 258`` (hoặc env ``KHO_JOB_ISSUER_ORG``) lọc theo đơn vị ban hành;
để trống = TẤT CẢ. Embed vào Qdrant do job 2 (``run_kho_qdrant.py``) đảm nhiệm.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import logging
import os
import signal
import sys
import time
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Cảnh báo vô hại của qdrant-client khi QDRANT_URL=http:// kèm api-key (mạng nội bộ). Ẩn để
# không bị nhầm là lỗi (chỉ ảnh hưởng job này, không đổi hành vi kết nối).
warnings.filterwarnings("ignore", message="Api key is used with an insecure connection.")

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

for _stream in (sys.stdout, sys.stderr):  # console Windows mặc định cp1252 -> ép utf-8
    try:
        _stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except Exception:  # noqa: BLE001
        pass

from jobs.common import console as cs  # noqa: E402
from jobs.common.bootstrap import run_stamp  # noqa: E402
from jobs.common.single_instance import SingleInstanceLock  # noqa: E402
from jobs.doffice_sync.clients.kho_client import (  # noqa: E402
    KHO_ES_CHUNK_DOC_FIELDS,
    RESET_WIPE,
    KhoAiEsClient,
    reset_es_chunk_stage,
    uuid7,
)
from jobs.doffice_sync.logger import setup_job_logging  # noqa: E402

logger = logging.getLogger("doffice_sync.kho_chunk")
oversize_logger = logging.getLogger("doffice_sync.oversize")

_MAX_CHUNK_DEFAULT = 500

# Field record ES chunk LUÔN có mặt (để null nếu nguồn/chunk rỗng) — cho schema đủ, đều.
_CHUNK_ALWAYS_FIELDS = ("doc_type", "doc_category", "owner_department_id", "section_path")

# Cờ dừng an toàn (đặt bởi handler Ctrl-C): job kiểm tra GIỮA các văn bản, chỉ dừng SAU khi
# chunk xong văn bản đang xử lý. Ctrl-C lần 2 -> buộc thoát ngay.
_STOP = False


def _install_stop_handler() -> None:
    def _handler(signum, frame):  # noqa: ANN001
        global _STOP
        if _STOP:
            raise KeyboardInterrupt  # lần 2 -> thoát ngay
        _STOP = True
    try:
        signal.signal(signal.SIGINT, _handler)
    except Exception:  # noqa: BLE001 — môi trường không cho set handler thì bỏ qua
        pass


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    try:
        return int(raw) if raw and raw.strip() else default
    except ValueError:
        return default


def _list_env(name: str) -> list[str] | None:
    raw = os.getenv(name)
    if not raw:
        return None
    parts = [p.strip() for p in raw.replace(";", ",").replace(" ", ",").split(",") if p.strip()]
    return parts or None


def _quiet_console() -> None:
    logging.basicConfig(level=logging.ERROR, format="%(levelname)s %(message)s")
    for noisy in ("httpx", "httpcore", "app", "asyncio", "elasticsearch"):
        logging.getLogger(noisy).setLevel(logging.ERROR)


@dataclass
class KStats:
    scanned: int = 0           # số văn bản đã QUÉT (gồm cả đã có chunk từ trước)
    already: int = 0           # đã có chunk từ trước -> bỏ qua
    done: int = 0              # chunk MỚI trong lượt này
    failed: int = 0
    skipped_empty: int = 0     # không có ocr_content
    skipped_oversize: int = 0  # > max_chunks
    chunks: int = 0            # tổng chunk đã ghi
    batches: int = 0
    current: str = ""          # văn bản đang chunk (chỉ báo còn sống)
    errors: dict[str, str] = field(default_factory=dict)


def build_doffice_style_source(doc: dict[str, Any]) -> dict[str, Any]:
    """Map doc ``kho_ai_dung_chung`` -> dict tên trường DOffice để tái dùng normalizer.

    ``normalize_doffice_source`` đọc ``noi_dung``/``trich_yeu``/``nguoi_ky``... -> đưa
    ``ocr_content``/``title``/``signer``... vào đúng chỗ; preamble + context "Văn bản:/Mục:"
    của chunk nhờ đó vẫn đầy đủ thông tin văn bản.
    """
    issue_date = str(doc.get("issue_date") or "")
    return {
        "id_vb": doc.get("document_id") or doc.get("id"),
        "ky_hieu": doc.get("document_no"),
        "trich_yeu": doc.get("title"),
        "tom_tat": doc.get("summary"),
        "noi_ban_hanh": doc.get("issuer_org_name"),
        "nguoi_ky": doc.get("signer"),
        "ten_file": doc.get("file_name"),
        "duong_dan": doc.get("file_path"),
        "ngay_vb": issue_date[:10] or None,
        "nam": doc.get("issue_year"),
        "thang": doc.get("issue_month"),
        "id_dv_ban_hanh": doc.get("issuer_org_id"),
        "type_ocr": doc.get("type_ocr"),
        "noi_dung": doc.get("ocr_content") or "",
    }


def build_chunk_records(doc: dict[str, Any], chunk_creates: list[Any]) -> list[dict[str, Any]]:
    """Dựng record ES nhánh chunk THEO ĐÚNG spec chốt (2026-07-05 rev2).

    Field doc-level (``KHO_ES_CHUNK_DOC_FIELDS``) + field chunk-level. Ngoài spec chỉ có 2
    field CƠ CHẾ: ``table_context`` (để job 2 dựng payload Qdrant chunk) và ``qdrant_indexed``
    (đánh dấu đã embed). 4 field ``_CHUNK_ALWAYS_FIELDS`` luôn có mặt (null nếu rỗng).
    """
    base: dict[str, Any] = {}
    for key in KHO_ES_CHUNK_DOC_FIELDS:
        value = doc.get(key)
        if value not in (None, "", [], {}):
            base[key] = value
    id_full = str(doc.get("id") or "")
    records: list[dict[str, Any]] = []
    for cc in chunk_creates:
        meta = dict(cc.metadata or {})
        section = meta.get("section_path")
        if isinstance(section, (list, tuple)):
            section = " > ".join(str(s) for s in section if str(s).strip())
        section = section or meta.get("section_title")
        content = str(cc.content or "")
        chunk_id = uuid7()
        rec: dict[str, Any] = {
            **base,
            "id": chunk_id,
            "id_full": id_full,
            "chunk_id": chunk_id,
            "chunk_order": int(cc.chunk_index),
            "chunk_text": content,
            "chunk_type": meta.get("chunk_type"),
            "section_path": section,
            "content_hash": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            "table_context": meta.get("table_title") or meta.get("table_name"),
            "qdrant_indexed": False,
        }
        out = {k: v for k, v in rec.items() if v not in (None, "", [], {})}
        for key in _CHUNK_ALWAYS_FIELDS:
            out[key] = rec.get(key)
        records.append(out)
    return records


class KhoChunkJobRunner:
    def __init__(
        self,
        *,
        batch_size: int,
        issuer_orgs: list[str] | None,
        id_filter: list[str] | None,
        full_scan: bool,
        limit: int,
        max_chunks: int,
    ) -> None:
        self._batch_size = max(1, batch_size)
        self._issuer_orgs = issuer_orgs
        self._id_filter = id_filter
        self._full_scan = full_scan  # True = chunk lại TẤT CẢ (bỏ qua kiểm tra đã chunk)
        self._limit = max(0, limit)
        self._max_chunks = max(0, max_chunks)
        self._client = KhoAiEsClient()
        self._chunk_kwargs: dict[str, int] | None = None
        self.total_source = 0
        self.to_chunk_total = 0     # số văn bản CHƯA chunk (chốt sau bước kiểm tra)
        self.phase = "kiểm tra"      # "kiểm tra" -> "chunk" -> "chờ"
        self._checked_src = 0        # tiến độ bước kiểm tra: id nguồn đã quét
        self._checked_chunked = 0    # tiến độ bước kiểm tra: id_full đã có chunk
        self.wait_deadline = 0.0     # mốc hết chờ (pha "chờ" giữa 2 lượt loop)
        self._mode_reset = False     # True = khởi động ở CHẾ ĐỘ 9 (hiển thị trên dashboard)
        self.stats = KStats()

    def scope_label(self) -> str:
        if self._id_filter:
            return "id " + ", ".join(self._id_filter)
        if self._issuer_orgs:
            return "đơn vị " + ", ".join(self._issuer_orgs)
        return "TẤT CẢ đơn vị"

    def _status(self) -> str:
        """MỘT dashboard CỐ ĐỊNH 6 dòng — cập nhật TẠI CHỖ suốt vòng lặp (kiểm tra -> chunk ->
        chờ), KHÔNG tạo dòng mới mỗi lượt. Số dòng luôn bằng nhau để không để lại rác."""
        scope = cs.color(self.scope_label(), cs.MAGENTA)
        mode = cs.color(
            "CHẾ ĐỘ 9 · xoá+chunk lại" if self._mode_reset else "CHẾ ĐỘ 0 · chạy tiếp",
            cs.RED if self._mode_reset else cs.GREEN,
        )
        s = self.stats
        da_chunk = s.already + s.done
        processed = s.done + s.failed + s.skipped_empty + s.skipped_oversize
        con_lai = max(0, self.to_chunk_total - processed)
        chua = max(0, self.total_source - da_chunk)
        if _STOP and self.phase == "chunk":
            last = f"{cs.YELLOW}⏸ đang dừng — chờ chunk xong văn bản hiện tại…{cs.RESET}"
        elif self.phase == "kiểm tra":
            last = (
                f"{cs.CYAN}⏳ đang kiểm tra: {self._checked_src} id nguồn · "
                f"{self._checked_chunked} đã có chunk…{cs.RESET}"
            )
        elif self.phase == "chờ":
            remain = max(0, int(self.wait_deadline - time.monotonic()))
            last = f"{cs.GREY}✓ xong lượt · chờ {remain}s rồi quét lại (Ctrl-C để dừng)…{cs.RESET}"
        elif s.current:
            last = f"{cs.CYAN}▶ đang chunk {s.current}{cs.RESET}"
        else:
            last = f"{cs.GREEN}✓ hoàn tất lượt này{cs.RESET}"
        return "\n".join([
            f"{cs.BOLD}KHO AI · {mode}{cs.RESET}   Phạm vi {scope}",
            f"  Tổng nguồn : {cs.color(str(self.total_source), cs.BOLD)}",
            f"  Đã chunk   : {cs.color(str(da_chunk), cs.GREEN)}   "
            f"{cs.GREY}(mới {s.done}, sẵn có {s.already}){cs.RESET}",
            f"  Chưa chunk : {cs.color(str(chua), cs.YELLOW)}",
            f"  Cần xử lý  : {cs.color(str(self.to_chunk_total), cs.BOLD)} · còn "
            f"{cs.color(str(con_lai), cs.YELLOW)}   {cs.GREY}({s.chunks} chunk · rỗng "
            f"{s.skipped_empty} · bỏ>max {s.skipped_oversize}){cs.RESET}"
            + (f"   {cs.color(str(s.failed) + ' lỗi', cs.RED)}" if s.failed else ""),
            f"  {last}",
        ])

    async def _load_chunk_kwargs(self) -> dict[str, int]:
        """Tham số kích thước chunk từ profile DB ``doffice_admin`` (fallback mặc định)."""
        if self._chunk_kwargs is not None:
            return self._chunk_kwargs
        cfg: dict[str, Any] = {}
        try:
            from app.db.session import AsyncSessionLocal
            from app.repositories.ingestion_profiles import IngestionProfileRepository
            from app.services.ingestion.ingestion_profiles import (
                get_profile_config,
                load_profile_configs,
            )

            async with AsyncSessionLocal() as session:
                repo = IngestionProfileRepository(session)
                await load_profile_configs(repo)
                await repo.commit()
            cfg = get_profile_config("doffice_admin") or {}
        except Exception as exc:  # noqa: BLE001 — profile lỗi thì dùng mặc định, không chặn job
            logger.warning("Không tải được profile doffice_admin (%s) — dùng mặc định.", exc)
        self._chunk_kwargs = dict(
            body_max_chars=int(cfg.get("doffice_body_max_chars") or 2800),
            body_overlap=int(cfg.get("doffice_body_overlap") or 300),
            table_max_chars=int(cfg.get("doffice_table_max_chars") or 3500),
        )
        return self._chunk_kwargs

    async def _process_doc(self, doc: dict[str, Any]) -> None:
        """Chunk 1 văn bản + ghi ES (idempotent: xoá chunk cũ theo id_full rồi ghi lại)."""
        from app.services.chunkers.chunker_doffice_chunking import build_doffice_chunks
        from app.services.ingestion.ingestion_doffice_content_normalizer import (
            normalize_doffice_source,
        )

        doc_id = str(doc.get("id") or "")
        label = str(doc.get("document_id") or doc_id)
        self.stats.current = label
        try:
            if not str(doc.get("ocr_content") or "").strip():
                self.stats.skipped_empty += 1
                logger.info("id=%s (%s): ocr_content rỗng -> bỏ qua.", doc_id, label)
                return
            kwargs = await self._load_chunk_kwargs()
            source = build_doffice_style_source(doc)

            def _chunk_sync() -> list[Any]:
                normalized = normalize_doffice_source(source)
                return build_doffice_chunks(normalized, **kwargs)

            # Làm sạch + chunk là CPU thuần -> đẩy sang thread để không nghẽn event loop.
            chunk_creates = await asyncio.to_thread(_chunk_sync)
            if self._max_chunks and len(chunk_creates) > self._max_chunks:
                self.stats.skipped_oversize += 1
                oversize_logger.warning(
                    "id=%s (%s) BỎ QUA: %s chunk > ngưỡng %s",
                    doc_id, label, len(chunk_creates), self._max_chunks,
                )
                return
            records = build_chunk_records(doc, chunk_creates)
            await self._client.delete_chunks_by_id_full(doc_id)  # re-chunk idempotent
            await self._client.bulk_upsert_chunks(records)
            self.stats.done += 1
            self.stats.chunks += len(records)
        except Exception as exc:  # noqa: BLE001
            self.stats.failed += 1
            self.stats.errors[doc_id] = f"{type(exc).__name__}: {exc}"
            logger.error("id=%s (%s) chunk lỗi: %s", doc_id, label, exc, exc_info=True)
        finally:
            self.stats.current = ""

    async def _resolve_pending_ids(self, live: bool) -> list[str]:
        """BƯỚC KIỂM TRA: đối chiếu toàn bộ id nguồn (phạm vi) với tập id_full ĐÃ có chunk
        -> trả DANH SÁCH id văn bản CHƯA chunk. Cách kiểm tra:
          (1) ``all_source_ids`` — quét toàn bộ id văn bản nguồn (chỉ field id, nhẹ).
          (2) ``all_chunked_id_full`` — toàn bộ id_full đã có chunk (composite agg).
          (3) hiệu = chưa chunk. ``--full-scan`` -> coi như chưa chunk hết (chunk lại tất cả).
        """
        src_ids = await self._client.all_source_ids(
            issuer_org_filter=self._issuer_orgs,
            on_progress=lambda n: setattr(self, "_checked_src", n),
        )
        self.total_source = len(src_ids)
        chunked: set[str] = set()
        if not self._full_scan:
            chunked = await self._client.all_chunked_id_full(
                on_progress=lambda n: setattr(self, "_checked_chunked", n),
            )
        pending = [i for i in src_ids if i not in chunked]
        self.stats.already = self.total_source - len(pending)
        self.to_chunk_total = len(pending)
        if not live:
            print(
                cs.color(
                    f"KIỂM TRA xong · {self.scope_label()}: tổng {self.total_source} · đã chunk "
                    f"{self.stats.already} · CHƯA chunk {self.to_chunk_total}",
                    cs.BOLD + cs.CYAN,
                ),
                flush=True,
            )
        return pending

    async def run_pass(self, *, live: bool) -> KStats:
        """1 LƯỢT = KIỂM TRA (chốt danh sách chưa chunk) -> CHUNK. Dừng an toàn khi ``_STOP``.

        KHÔNG tự quản spinner: dashboard do ``_main`` giữ chung 1 lần cho cả vòng lặp (cập
        nhật tại chỗ, không tạo dòng mới mỗi lượt). ``live`` = stdout là terminal.
        """
        self.stats = KStats()
        self.phase = "kiểm tra"
        self.to_chunk_total = 0
        self._checked_src = self._checked_chunked = 0
        await self._client.ensure_chunk_index()
        await self._client.refresh_chunk_index()  # thấy chunk vừa ghi -> kiểm tra "đã chunk" đúng

        # --- BƯỚC 1: KIỂM TRA -> danh sách id CHƯA chunk ---
        if self._id_filter:
            pending_ids = list(self._id_filter)  # chỉ định id -> chunk lại đúng các id đó
            self.total_source = len(pending_ids)
            self.to_chunk_total = len(pending_ids)
            self.stats.already = 0
        else:
            pending_ids = await self._resolve_pending_ids(live)

        # --- BƯỚC 2: CHUNK đúng danh sách chưa chunk (theo lô, fetch full doc) ---
        self.phase = "chunk"
        for start in range(0, len(pending_ids), self._batch_size):
            if _STOP:
                break
            if self._limit and self.stats.scanned >= self._limit:
                break
            batch_ids = pending_ids[start : start + self._batch_size]
            docs = await self._client.fetch_by_id(batch_ids)
            self.stats.batches += 1
            for doc in docs:
                if _STOP:
                    break
                if self._limit and self.stats.scanned >= self._limit:
                    break
                self.stats.scanned += 1
                await self._process_doc(doc)
        return self.stats


def _print_summary(runner: KhoChunkJobRunner, elapsed: float, log_dir: Path) -> None:
    s = runner.stats
    da_chunk = s.already + s.done
    chua = max(0, runner.total_source - da_chunk)
    minutes, seconds = divmod(int(elapsed), 60)
    line = cs.color("═" * 52, cs.CYAN)
    print("\n".join([
        "",
        line,
        cs.color("  KHO AI dùng chung · kiểm tra + chunk -> ES", cs.BOLD + cs.CYAN),
        line,
        f"  Phạm vi        : {cs.color(runner.scope_label(), cs.MAGENTA)}",
        f"  Tổng nguồn     : {cs.color(str(runner.total_source), cs.BOLD)}",
        f"  Cần chunk lượt : {cs.color(str(runner.to_chunk_total), cs.BOLD)}  "
        f"{cs.GREY}(văn bản chưa chunk phát hiện khi kiểm tra){cs.RESET}",
        f"  Đã chunk       : {cs.color(str(da_chunk), cs.GREEN)}  "
        f"{cs.GREY}(mới {s.done}, sẵn có {s.already}){cs.RESET}",
        f"  Chưa chunk còn : {cs.color(str(chua), cs.YELLOW if chua else cs.GREEN)}",
        f"  Rỗng/Bỏ>max    : {cs.color(str(s.skipped_empty), cs.YELLOW)} / "
        f"{cs.color(str(s.skipped_oversize), cs.RED if s.skipped_oversize else cs.GREEN)}"
        f"   {cs.GREY}({s.chunks} chunk){cs.RESET}",
        f"  Lỗi            : {cs.color(str(s.failed), cs.RED if s.failed else cs.GREEN)}",
        f"  Thời gian      : {minutes}m {seconds}s",
        f"  Log            : {cs.color(f'{log_dir}/', cs.GREY)}",
        line,
    ]))
    for doc_id, err in list(s.errors.items())[:10]:
        print(f"  {cs.color('✗ ' + doc_id, cs.RED)}: {err}")


def _refuse_if_running(lock: SingleInstanceLock) -> bool:
    """Trả True nếu job đã chạy ở tiến trình khác (KHÔNG bật lần 2). In thông báo rõ ràng."""
    pid, stamp = lock.holder_info()
    who = f" (PID {pid}{', bắt đầu ' + stamp if stamp else ''})" if pid else ""
    bar = cs.color("━" * 60, cs.RED)
    print("\n".join([
        bar,
        cs.color("  ⛔ JOB CHUNK ĐÃ ĐANG CHẠY — KHÔNG bật thêm tiến trình thứ 2", cs.BOLD + cs.RED),
        cs.color(f"  Tiến trình đang chạy{who}. Đóng cửa sổ đó hoặc chờ nó xong rồi chạy lại.", cs.YELLOW),
        bar,
    ]), flush=True)
    return True


async def _main(args: argparse.Namespace) -> None:
    cs.enable_ansi()
    stamp = run_stamp()
    lock = SingleInstanceLock("kho_chunk")
    if not lock.acquire(start_stamp=stamp):
        _refuse_if_running(lock)
        return
    try:
        await _run_locked(args, stamp)
    finally:
        lock.release()


async def _run_locked(args: argparse.Namespace, stamp: str) -> None:
    loggers = setup_job_logging("logs/jobs/kho_chunk", stamp)
    _quiet_console()
    _install_stop_handler()

    batch = args.batch_size if args.batch_size is not None else _int_env("KHO_JOB_BATCH_SIZE", 200)
    interval = args.interval if args.interval is not None else _int_env("KHO_JOB_INTERVAL", 300)
    limit = args.limit if args.limit is not None else _int_env("KHO_JOB_LIMIT", 0)
    max_chunks = args.max_chunk if args.max_chunk is not None else _int_env("KHO_JOB_MAX_CHUNK", _MAX_CHUNK_DEFAULT)
    issuer_orgs = args.issuer_org if args.issuer_org else _list_env("KHO_JOB_ISSUER_ORG")
    id_filter = args.id if args.id else _list_env("KHO_JOB_ID")
    reset = args.reset if args.reset is not None else _int_env("KHO_JOB_RESET", 0)

    # BANNER MODE — hiện NGAY khi bật job để biết đang chạy chế độ nào.
    scope_txt = (
        ("đơn vị " + ", ".join(issuer_orgs)) if issuer_orgs
        else ("id " + ", ".join(id_filter)) if id_filter
        else "TẤT CẢ đơn vị"
    )
    loop_txt = f"loop {interval}s" if interval and interval > 0 else "chạy 1 lượt rồi thoát"
    bar = cs.color("━" * 60, cs.CYAN)
    if reset == RESET_WIPE:
        mode_line = cs.color("  CHẾ ĐỘ 9 — XOÁ TOÀN BỘ CHUNK rồi CHUNK LẠI TỪ ĐẦU", cs.BOLD + cs.RED)
    else:
        mode_line = cs.color("  CHẾ ĐỘ 0 — CHẠY TIẾP (chỉ chunk văn bản CHƯA chunk)", cs.BOLD + cs.GREEN)
    print("\n".join([
        bar, mode_line,
        f"  Phạm vi: {cs.color(scope_txt, cs.MAGENTA)}   ·   {cs.GREY}{loop_txt} · batch {batch}{cs.RESET}",
        bar,
    ]), flush=True)

    if reset == RESET_WIPE:
        # reset=9 (stage CHUNK): xoá + tạo lại RỖNG ES chunk -> mọi văn bản coi như chưa chunk.
        # KHÔNG đụng Qdrant (do run_kho_qdrant quản), PostgreSQL, hay nguồn kho_ai_dung_chung.
        print(cs.color(
            "RESET=9: xoá + tạo lại rỗng kho_ai_dung_chung_chunk (KHÔNG đụng Qdrant, "
            "Postgres, kho_ai_dung_chung)…", cs.BOLD + cs.RED,
        ), flush=True)
        loggers.get("run").warning("RESET=9: wipe ES chunk (Qdrant do run_kho_qdrant, không dùng PG).")
        await reset_es_chunk_stage(KhoAiEsClient())
        print(cs.color("RESET xong — mọi văn bản sẽ được chunk lại từ đầu.", cs.GREEN), flush=True)
    elif reset not in (0, None):
        print(cs.color(f"Giá trị --reset {reset} không hỗ trợ (chỉ 0 hoặc 9) — bỏ qua.", cs.YELLOW), flush=True)

    loggers.get("run").info(
        "Job kho_chunk: batch=%s interval=%ss limit=%s max_chunk=%s issuer_org=%s id=%s full_scan=%s reset=%s",
        batch, interval, limit, max_chunks, issuer_orgs, id_filter, args.full_scan, reset,
    )
    # MỘT runner + MỘT spinner cho CẢ vòng lặp -> dashboard cập nhật TẠI CHỖ, KHÔNG in dòng
    # mới mỗi lượt. Tổng kết chỉ in 1 lần khi thoát.
    runner = KhoChunkJobRunner(
        batch_size=batch, issuer_orgs=issuer_orgs, id_filter=id_filter,
        full_scan=args.full_scan, limit=limit, max_chunks=max_chunks,
    )
    runner._mode_reset = reset == RESET_WIPE
    live = sys.stdout.isatty()
    spinner = cs.Spinner(runner._status) if live else None
    if spinner is not None:
        spinner.start()
    forced_exit = False
    last_elapsed = 0.0
    try:
        while True:
            start = time.monotonic()
            try:
                await runner.run_pass(live=live)
            except KeyboardInterrupt:
                forced_exit = True
                break
            except Exception:
                loggers.get("run").error("Job kho_chunk lỗi nghiêm trọng", exc_info=True)
            last_elapsed = time.monotonic() - start
            if not live:  # không phải terminal -> in gọn 1 dòng/lượt (log)
                s = runner.stats
                print(
                    f"Lượt xong: đã chunk mới {s.done}/{runner.to_chunk_total} · lỗi {s.failed} "
                    f"· {last_elapsed:.0f}s",
                    flush=True,
                )
            if _STOP or not interval or interval <= 0:
                break
            # PHA CHỜ — cùng spinner, đếm ngược cập nhật tại chỗ (không tạo dòng mới).
            runner.phase = "chờ"
            runner.wait_deadline = time.monotonic() + interval
            while time.monotonic() < runner.wait_deadline and not _STOP:
                await asyncio.sleep(0.2)
    finally:
        if spinner is not None:
            await spinner.stop()
    _print_summary(runner, last_elapsed, loggers.log_dir)
    if forced_exit:
        print(cs.color("Đã buộc thoát (Ctrl-C lần 2).", cs.RED), flush=True)
    elif _STOP:
        print(cs.color("Đã dừng theo yêu cầu (chunk xong văn bản hiện tại).", cs.YELLOW), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Job kho AI dung chung: quet kho_ai_dung_chung -> lam sach -> chunk -> ES."
    )
    parser.add_argument("--batch-size", type=int, default=None, help="Lo quet ES nguon (mac dinh 200).")
    parser.add_argument(
        "--interval", type=int, default=None,
        help="Giay giua 2 lan quet (mac dinh 300 = 5 phut; 0 = chay 1 lan roi thoat). "
             "Override KHO_JOB_INTERVAL.",
    )
    parser.add_argument(
        "--issuer-org", nargs="+", type=str, default=None,
        help="Chi quet doc cua don vi ban hanh (issuer_org_id). Trong = tat ca. "
             "Override KHO_JOB_ISSUER_ORG.",
    )
    parser.add_argument(
        "--id", nargs="+", type=str, default=None,
        help="Chi chunk cac doc theo field id (UUIDv7) chi dinh (chunk lai ke ca da co chunk).",
    )
    parser.add_argument(
        "--full-scan", action="store_true",
        help="Chunk LAI tat ca (bo qua kiem tra 'da chunk chua').",
    )
    parser.add_argument("--limit", type=int, default=None, help="Toi da N doc roi dung (debug).")
    parser.add_argument(
        "--max-chunk", type=int, default=None,
        help="Doc > nguong chunk nay se BO QUA (mac dinh 500; 0 = khong gioi han).",
    )
    parser.add_argument(
        "--reset", type=int, default=None, choices=(0, 9),
        help="9 = XOA + tao lai rong ES chunk (kho_ai_dung_chung_chunk) -> chunk lai tu dau. "
             "KHONG dung Qdrant/Postgres/kho_ai_dung_chung. 0 (mac dinh) = chi chunk van ban chua chunk. "
             "Override KHO_JOB_RESET.",
    )
    asyncio.run(_main(parser.parse_args()))
