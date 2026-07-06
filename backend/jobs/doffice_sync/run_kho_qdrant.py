"""Job KHO AI DÙNG CHUNG (2/2): quét ``kho_ai_dung_chung_chunk`` -> embed -> Qdrant.

Luồng (spec 2026-07-05):
  - Quét chunk CHƯA embed (``qdrant_indexed != true``) theo batch, gom theo ``id_full``.
  - Khởi tạo nếu chưa có 2 collection: ``hbrag_doffice_chunks`` + ``hbrag_doffice_docmeta``
    (tên đọc từ settings ``qdrant_chunks/docmeta_collection_name``).
  - TUẦN TỰ từng document (KHÔNG đa luồng): embed nhánh FULL trước (docmeta: text =
    ``title + signer + summary`` LÀM SẠCH bằng ``clean_for_chunking``), rồi embed TỪNG
    chunk (``chunk_text`` ĐÃ sạch từ job 1 — không làm sạch lại).
  - CHỈ dense vector (sparse TẮT — kiến trúc ES=BM25 lexical / Qdrant=dense).
  - Point id: docmeta = ``id`` (UUIDv7 doc nguồn); chunk = ``id`` chunk (UUIDv7).
    Payload ĐÚNG spec chốt (``KHO_DOCMETA_PAYLOAD_FIELDS`` cho docmeta; docmeta doc-level +
    chunk-level ``KHO_CHUNK_DOC_PAYLOAD_FIELDS``/``KHO_CHUNK_CHUNK_PAYLOAD_FIELDS`` cho chunk).
  - Xong document -> đánh dấu ``qdrant_indexed=true`` cho các chunk của nó trên ES.

CHẠY 1 LƯỢT rồi dừng (không loop liên tục) — quét hết chunk pending theo batch, embed tuần
tự tới đâu in ra tới đó. Chạy lại = gọi lại job (đọc theo cờ ``qdrant_indexed``).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
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
    KHO_CHUNK_CHUNK_PAYLOAD_FIELDS,
    KHO_CHUNK_DOC_PAYLOAD_FIELDS,
    KHO_DOCMETA_PAYLOAD_FIELDS,
    RESET_WIPE,
    KhoAiEsClient,
)
from jobs.doffice_sync.logger import setup_job_logging  # noqa: E402

logger = logging.getLogger("doffice_sync.kho_qdrant")


def _doffice_qdrant_stores() -> tuple[Any, Any]:
    """2 store Qdrant CHỈ DENSE (sparse_embedding_enabled=False) — nơi DUY NHẤT job này
    quản 2 collection ``hbrag_doffice_chunks`` + ``hbrag_doffice_docmeta``."""
    from app.services.vector.vector_store import (
        get_doffice_chunks_vector_store,
        get_doffice_docmeta_vector_store,
    )

    return get_doffice_chunks_vector_store(), get_doffice_docmeta_vector_store()


async def ensure_qdrant_collections() -> tuple[Any, Any]:
    """Kiểm tra tồn tại + TẠO (nếu chưa có) 2 collection Qdrant dense-only. Trả 2 store."""
    chunks_store, docmeta_store = _doffice_qdrant_stores()
    await chunks_store.ensure_collection()
    await docmeta_store.ensure_collection()
    return chunks_store, docmeta_store


async def reset_qdrant_stage(client: KhoAiEsClient) -> int:
    """Reset STAGE EMBED (dùng ở ``run_kho_qdrant --reset 9``).

    Xoá + TẠO LẠI 2 collection Qdrant dense-only + bỏ đánh dấu ``qdrant_indexed`` mọi chunk ES
    (chunk vẫn còn — do run_kho_chunk quản) để embed lại từ đầu. KHÔNG đụng nguồn/ES chunk data.
    Trả số chunk đã bỏ đánh dấu.
    """
    chunks_store, docmeta_store = _doffice_qdrant_stores()
    for store in (chunks_store, docmeta_store):
        await store.recreate_collection()
    return await client.unmark_all_chunks()


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
    for noisy in ("httpx", "httpcore", "qdrant_client", "app", "asyncio", "elasticsearch"):
        logging.getLogger(noisy).setLevel(logging.ERROR)


@dataclass
class QStats:
    docs_total: int = 0     # số id_full pending đầu lượt (ước lượng)
    docs_done: int = 0
    docs_failed: int = 0
    docmeta_points: int = 0
    chunk_points: int = 0
    docs_no_source: int = 0  # id_full không còn doc nguồn (docmeta bỏ qua, chunk vẫn embed)
    errors: dict[str, str] = field(default_factory=dict)


def build_docmeta_embed_text(doc: dict[str, Any]) -> str:
    """Text embed nhánh FULL: title + signer + summary, LÀM SẠCH trước khi embed."""
    from app.services.chunkers.chunker_text_cleaning import clean_for_chunking

    parts = [str(doc.get(k) or "").strip() for k in ("title", "signer", "summary")]
    raw = "\n".join(p for p in parts if p)
    return clean_for_chunking(raw) or str(doc.get("id") or "")


# Field kiểu DANH SÁCH -> khi rỗng ghi ``[]`` (thay vì ``null``) để schema đồng nhất, tiện lọc.
_LIST_PAYLOAD_FIELDS = frozenset({
    "keywords", "acl_subjects", "acl_deny", "related_document_ids", "reference_document_ids",
    "org_list",
})


def _empty_default(key: str) -> Any:
    """Giá trị rỗng CHUẨN HOÁ cho field khi nguồn không có: list -> ``[]``, còn lại -> ``None``."""
    return [] if key in _LIST_PAYLOAD_FIELDS else None


def build_docmeta_payload(doc: dict[str, Any]) -> dict[str, Any]:
    """Payload Qdrant docmeta ĐỦ KHUNG field theo spec (``KHO_DOCMETA_PAYLOAD_FIELDS``).

    GHI ĐỦ mọi field kể cả khi nguồn rỗng (list rỗng -> ``[]``, scalar rỗng -> ``null``) để
    schema đồng nhất giữa mọi doc — các field nghiệp vụ chưa điền (doc_type/keywords/
    priority...) vẫn hiện, tự có giá trị khi API nguồn bổ sung. ``id`` = id doc nguồn.
    """
    payload: dict[str, Any] = {}
    for key in KHO_DOCMETA_PAYLOAD_FIELDS:
        value = doc.get(key)
        if value in (None, "", [], {}):
            value = _empty_default(key)
        payload[key] = value
    payload["id"] = str(doc.get("id") or "")
    payload["document_id"] = str(doc.get("document_id") or doc.get("id") or "")
    return payload


def build_chunk_payload(rec: dict[str, Any], doc: dict[str, Any] | None) -> dict[str, Any]:
    """Payload Qdrant chunk ĐỦ KHUNG field theo spec: doc-level (record ES chunk, bù từ doc
    nguồn cho related/reference/priority) + chunk-level (record ES chunk). GHI ĐỦ field kể cả
    rỗng (list -> ``[]``, scalar -> ``null``) để schema đồng nhất. ``id`` = id chunk.
    """
    src = doc or {}
    payload: dict[str, Any] = {}
    for key in KHO_CHUNK_DOC_PAYLOAD_FIELDS:
        value = rec.get(key)
        if value in (None, "", [], {}):
            value = src.get(key)  # related/reference/priority chỉ có ở doc nguồn
        if value in (None, "", [], {}):
            value = _empty_default(key)
        payload[key] = value
    for key in KHO_CHUNK_CHUNK_PAYLOAD_FIELDS:
        value = rec.get(key)
        if value in (None, "", [], {}):
            value = _empty_default(key)
        payload[key] = value
    payload["document_id"] = str(rec.get("document_id") or src.get("document_id") or "")
    return payload


class KhoQdrantJobRunner:
    def __init__(
        self,
        *,
        batch_size: int,
        embed_batch: int,
        id_full_filter: list[str] | None,
        limit: int,
    ) -> None:
        self._batch_size = max(1, batch_size)
        self._embed_batch = max(1, embed_batch)
        self._id_full_filter = id_full_filter
        self._limit = max(0, limit)
        self._client = KhoAiEsClient()
        self.stats = QStats()
        self.current = ""
        # --- trạng thái cho dashboard TẠI CHỖ (2 luồng vào 2 collection) ---
        self._mode_reset = False           # True = khởi động ở CHẾ ĐỘ 9
        self.phase = "khởi tạo"            # "khởi tạo" -> "quét" -> "embed" -> "xong"
        self.pending_chunks = 0            # ~ số chunk pending đầu lượt
        self.docmeta_collection = ""       # tên collection docmeta (hiện trên dashboard)
        self.chunks_collection = ""        # tên collection chunk
        self.cur_stage = ""                # nhánh đang embed của doc hiện tại: docmeta/chunk
        self.cur_chunk_done = 0            # chunk đã embed của doc hiện tại
        self.cur_chunk_total = 0           # tổng chunk của doc hiện tại

    def scope_label(self) -> str:
        if self._id_full_filter:
            return "id_full " + ", ".join(self._id_full_filter)
        return "TẤT CẢ chunk chưa embed"

    def _status(self) -> str:
        """MỘT dashboard CỐ ĐỊNH cập nhật TẠI CHỖ (không cuộn) — hiện RÕ 2 luồng embed vào
        2 collection Qdrant: docmeta và chunk. Số dòng luôn cố định để không để lại rác."""
        scope = cs.color(self.scope_label(), cs.MAGENTA)
        mode = cs.color(
            "CHẾ ĐỘ 9 · xoá+embed lại" if self._mode_reset else "CHẾ ĐỘ 0 · embed tiếp",
            cs.RED if self._mode_reset else cs.GREEN,
        )
        s = self.stats
        docmeta_col = self.docmeta_collection or "hbrag_doffice_docmeta"
        chunks_col = self.chunks_collection or "hbrag_doffice_chunks"
        if self.phase == "khởi tạo":
            last = f"{cs.CYAN}⏳ khởi tạo / kiểm tra 2 collection Qdrant…{cs.RESET}"
        elif self.phase == "quét":
            last = f"{cs.CYAN}⏳ quét chunk chưa embed trên Elasticsearch…{cs.RESET}"
        elif self.phase == "xong":
            last = f"{cs.GREEN}✓ hoàn tất lượt embed{cs.RESET}"
        elif self.current:
            stage = ""
            if self.cur_stage == "docmeta":
                stage = f" · {cs.MAGENTA}docmeta{cs.RESET}"
            elif self.cur_stage == "chunk":
                stage = f" · {cs.CYAN}chunk {self.cur_chunk_done}/{self.cur_chunk_total}{cs.RESET}"
            last = f"{cs.CYAN}▶ đang embed {self.current}{cs.RESET}{stage}"
        else:
            last = f"{cs.GREEN}✓ sẵn sàng{cs.RESET}"
        return "\n".join([
            f"{cs.BOLD}KHO AI · EMBED QDRANT · {mode}{cs.RESET}   Phạm vi {scope}",
            f"  Chunk pending  : {cs.color(str(self.pending_chunks), cs.BOLD)}   "
            f"{cs.GREY}(ước tính đầu lượt){cs.RESET}",
            f"  {cs.MAGENTA}▌ Luồng 1 docmeta{cs.RESET} → {cs.GREY}{docmeta_col}{cs.RESET} : "
            f"{cs.color(str(s.docmeta_points), cs.BOLD)} point",
            f"  {cs.CYAN}▌ Luồng 2 chunk  {cs.RESET} → {cs.GREY}{chunks_col}{cs.RESET} : "
            f"{cs.color(str(s.chunk_points), cs.BOLD)} point",
            f"  Document xong  : {cs.color(str(s.docs_done), cs.GREEN)}   "
            f"{cs.GREY}(thiếu nguồn {s.docs_no_source}){cs.RESET}"
            + (f"   {cs.color(str(s.docs_failed) + ' lỗi', cs.RED)}" if s.docs_failed else ""),
            f"  {last}",
        ])

    async def _build_ctx(self) -> dict[str, Any]:
        from app.services.embeddings.embedding_factory import get_embedding_provider

        # Kiểm tra tồn tại + tạo (nếu chưa có) 2 collection Qdrant dense-only — chỉ ở job này.
        chunks_store, docmeta_store = await ensure_qdrant_collections()
        self.chunks_collection = getattr(chunks_store, "collection_name", "")
        self.docmeta_collection = getattr(docmeta_store, "collection_name", "")
        return dict(
            chunks_store=chunks_store,
            docmeta_store=docmeta_store,
            dense=get_embedding_provider(),
        )

    async def _embed_texts_sequential(
        self, ctx: dict[str, Any], texts: list[str], *, on_progress: Any = None
    ) -> list[list[float]]:
        """Embed dense TUẦN TỰ theo lô nhỏ (mặc định 1 text/request — không song song).

        ``on_progress(n)`` (nếu có) được gọi SAU mỗi lô -> báo số text đã embed để dashboard
        cập nhật tiến độ TẠI CHỖ (không đợi cả document xong mới nhảy số).
        """
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self._embed_batch):
            batch = texts[start : start + self._embed_batch]
            vectors.extend(await ctx["dense"].embed_texts(batch))
            if on_progress is not None:
                on_progress(len(vectors))
        return vectors

    async def _process_document(self, ctx: dict[str, Any], id_full: str, *, live: bool) -> None:
        """1 document: embed docmeta (nhánh full) TRƯỚC, rồi từng chunk, rồi đánh dấu ES."""
        self.current = id_full
        self.cur_stage = ""
        self.cur_chunk_done = 0
        self.cur_chunk_total = 0
        t0 = time.monotonic()
        chunks = await self._client.fetch_chunks_by_id_full(id_full)
        if not chunks:
            logger.warning("id_full=%s không còn chunk -> bỏ qua.", id_full)
            return
        self.cur_chunk_total = len(chunks)
        # --- Luồng 1 FULL (docmeta) ---
        self.cur_stage = "docmeta"
        source_docs = await self._client.fetch_by_id([id_full])
        doc = source_docs[0] if source_docs else None
        if doc is None:
            self.stats.docs_no_source += 1
            logger.warning(
                "id_full=%s không tìm thấy doc nguồn trong %s -> bỏ docmeta, vẫn embed chunk.",
                id_full, self._client.index,
            )
        else:
            meta_text = build_docmeta_embed_text(doc)
            vector = (await self._embed_texts_sequential(ctx, [meta_text]))[0]
            point = ctx["docmeta_store"].build_point(
                point_id=str(doc.get("id") or id_full),
                vector=vector,
                payload=build_docmeta_payload(doc),
            )
            await ctx["docmeta_store"].upsert_chunks([point])
            self.stats.docmeta_points += 1
        # --- Luồng 2 CHUNK (không làm sạch lại — job 1 đã sạch) ---
        self.cur_stage = "chunk"
        texts = [str(rec.get("chunk_text") or "") for rec in chunks]
        vectors = await self._embed_texts_sequential(
            ctx, texts, on_progress=lambda n: setattr(self, "cur_chunk_done", n)
        )
        points = [
            ctx["chunks_store"].build_point(
                point_id=str(rec.get("id")),
                vector=vec,
                payload=build_chunk_payload(rec, doc),
            )
            for rec, vec in zip(chunks, vectors, strict=True)
            if rec.get("id")
        ]
        # Idempotent: chunk id (point id) sinh MỚI mỗi lần re-chunk -> upsert KHÔNG đè point cũ.
        # Xoá point chunk CŨ của văn bản này (theo id_full) TRƯỚC khi ghi point mới, tránh
        # point mồ côi tích tụ trong Qdrant. (docmeta không cần: point id = id doc nguồn, ổn định.)
        await ctx["chunks_store"].delete_points_by_field("id_full", id_full)
        await ctx["chunks_store"].upsert_chunks(points)
        self.stats.chunk_points += len(points)
        await self._client.mark_chunks_indexed([str(rec.get("id")) for rec in chunks if rec.get("id")])
        self.stats.docs_done += 1
        logger.info(
            "[%s] id_full=%s docmeta=%s chunk=%s %.1fs",
            self.stats.docs_done, id_full, "y" if doc else "-", len(points), time.monotonic() - t0,
        )
        if not live:  # không phải terminal (pipe/log) -> in gọn 1 dòng/doc
            print(
                f"[{self.stats.docs_done}] id_full={id_full} · docmeta {'✓' if doc else '—'} · "
                f"{len(points):>3} chunk · {time.monotonic() - t0:.1f}s",
                flush=True,
            )

    async def run_once(self, *, live: bool) -> QStats:
        self.stats = QStats()
        self.phase = "khởi tạo"
        ctx = await self._build_ctx()
        await self._client.ensure_chunk_index()
        await self._client.refresh_chunk_index()  # thấy ngay chunk job 1 vừa ghi

        if self._id_full_filter:
            self.pending_chunks = 0
            self.phase = "embed"
            for id_full in self._id_full_filter:
                try:
                    await self._process_document(ctx, id_full, live=live)
                except Exception as exc:  # noqa: BLE001
                    self.stats.docs_failed += 1
                    self.stats.errors[id_full] = f"{type(exc).__name__}: {exc}"
                    logger.error("id_full=%s embed lỗi: %s", id_full, exc, exc_info=True)
                finally:
                    self.current = ""
            self.phase = "xong"
            return self.stats

        self.phase = "quét"
        self.pending_chunks = await self._client.count_pending_chunks()
        logger.info(
            "Quét %s · %s · ~%s chunk chưa embed",
            self._client.chunk_index, self.scope_label(), self.pending_chunks,
        )
        self.phase = "embed"
        after_key: dict | None = None
        while True:
            ids, after_key = await self._client.pending_id_full_page(
                page_size=self._batch_size,
                after_key=after_key,
            )
            if not ids:
                break
            for id_full in ids:
                if self._limit and (self.stats.docs_done + self.stats.docs_failed) >= self._limit:
                    self.phase = "xong"
                    return self.stats
                try:
                    await self._process_document(ctx, id_full, live=live)
                except Exception as exc:  # noqa: BLE001
                    self.stats.docs_failed += 1
                    self.stats.errors[id_full] = f"{type(exc).__name__}: {exc}"
                    logger.error("id_full=%s embed lỗi: %s", id_full, exc, exc_info=True)
                finally:
                    self.current = ""
            if after_key is None:
                break
        self.phase = "xong"
        return self.stats


def _print_summary(runner: KhoQdrantJobRunner, elapsed: float, log_dir: Path) -> None:
    s = runner.stats
    minutes, seconds = divmod(int(elapsed), 60)
    line = cs.color("═" * 52, cs.CYAN)
    print("\n".join([
        "",
        line,
        cs.color("  KHO AI dùng chung · embed Qdrant (dense, tuần tự)", cs.BOLD + cs.CYAN),
        line,
        f"  Phạm vi        : {cs.color(runner.scope_label(), cs.MAGENTA)}",
        f"  Document xong  : {cs.color(str(s.docs_done), cs.GREEN)}",
        f"  Docmeta point  : {cs.color(str(s.docmeta_points), cs.BOLD)}",
        f"  Chunk point    : {cs.color(str(s.chunk_points), cs.BOLD)}",
        f"  Thiếu doc nguồn: {cs.color(str(s.docs_no_source), cs.YELLOW if s.docs_no_source else cs.GREEN)}",
        f"  Lỗi            : {cs.color(str(s.docs_failed), cs.RED if s.docs_failed else cs.GREEN)}",
        f"  Thời gian      : {minutes}m {seconds}s",
        f"  Log            : {cs.color(f'{log_dir}/', cs.GREY)}",
        line,
    ]))
    for id_full, err in list(s.errors.items())[:10]:
        print(f"  {cs.color('✗ ' + id_full, cs.RED)}: {err}")


def _refuse_if_running(lock: SingleInstanceLock) -> None:
    """In thông báo khi job embed đã chạy ở tiến trình khác (KHÔNG bật lần 2)."""
    pid, stamp = lock.holder_info()
    who = f" (PID {pid}{', bắt đầu ' + stamp if stamp else ''})" if pid else ""
    bar = cs.color("━" * 60, cs.RED)
    print("\n".join([
        bar,
        cs.color("  ⛔ JOB EMBED QDRANT ĐÃ ĐANG CHẠY — KHÔNG bật thêm tiến trình thứ 2", cs.BOLD + cs.RED),
        cs.color(f"  Tiến trình đang chạy{who}. Đóng cửa sổ đó hoặc chờ nó xong rồi chạy lại.", cs.YELLOW),
        bar,
    ]), flush=True)


async def _main(args: argparse.Namespace) -> None:
    cs.enable_ansi()
    stamp = run_stamp()
    lock = SingleInstanceLock("kho_qdrant")
    if not lock.acquire(start_stamp=stamp):
        _refuse_if_running(lock)
        return
    try:
        await _run_locked(args, stamp)
    finally:
        lock.release()


async def _run_locked(args: argparse.Namespace, stamp: str) -> None:
    loggers = setup_job_logging("logs/jobs/kho_qdrant", stamp)
    _quiet_console()

    batch = args.batch_size if args.batch_size is not None else _int_env("KHO_QDRANT_BATCH_SIZE", 50)
    embed_batch = args.embed_batch if args.embed_batch is not None else _int_env("KHO_QDRANT_EMBED_BATCH", 1)
    limit = args.limit if args.limit is not None else _int_env("KHO_QDRANT_LIMIT", 0)
    id_full = args.id_full if args.id_full else _list_env("KHO_QDRANT_ID_FULL")
    reset = args.reset if args.reset is not None else _int_env("KHO_QDRANT_RESET", 0)

    if reset == RESET_WIPE:
        # reset=9 (stage EMBED): xoá + tạo lại 2 collection Qdrant dense-only + bỏ đánh dấu chunk
        # ES để embed lại. GIỮ nguyên chunk ES (do run_kho_chunk quản) và nguồn kho_ai_dung_chung.
        print(cs.color(
            "RESET=9: xoá + tạo lại 2 collection Qdrant (dense) + bỏ đánh dấu chunk để embed lại "
            "(GIỮ ES chunk, KHÔNG đụng kho_ai_dung_chung)…", cs.BOLD + cs.RED,
        ), flush=True)
        loggers.get("run").warning("RESET=9: recreate Qdrant chunks/docmeta + unmark ES chunks.")
        unmarked = await reset_qdrant_stage(KhoAiEsClient())
        print(cs.color(
            f"RESET xong (đã tạo lại 2 collection, bỏ đánh dấu {unmarked} chunk) — quét embed lại.",
            cs.GREEN,
        ), flush=True)
    elif reset not in (0, None):
        print(cs.color(f"Giá trị --reset {reset} không hỗ trợ (chỉ 0 hoặc 9) — bỏ qua.", cs.YELLOW), flush=True)

    loggers.get("run").info(
        "Job kho_qdrant: batch=%s embed_batch=%s limit=%s id_full=%s reset=%s",
        batch, embed_batch, limit, id_full, reset,
    )
    # CHẠY 1 LƯỢT rồi dừng (không loop liên tục): quét hết chunk pending theo batch, embed
    # tuần tự tới đâu in tới đó.
    runner = KhoQdrantJobRunner(
        batch_size=batch, embed_batch=embed_batch, id_full_filter=id_full, limit=limit,
    )
    runner._mode_reset = reset == RESET_WIPE
    live = sys.stdout.isatty()
    spinner = cs.Spinner(runner._status) if live else None
    if spinner is not None:
        spinner.start()
    start = time.monotonic()
    try:
        await runner.run_once(live=live)
    except Exception:
        loggers.get("run").error("Job kho_qdrant lỗi nghiêm trọng", exc_info=True)
    finally:
        if spinner is not None:
            await spinner.stop()
    _print_summary(runner, time.monotonic() - start, loggers.log_dir)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Job kho AI dung chung: embed chunk + docmeta vao Qdrant (dense, tuan tu)."
    )
    parser.add_argument(
        "--batch-size", type=int, default=None,
        help="So id_full moi trang quet pending (mac dinh 50).",
    )
    parser.add_argument(
        "--embed-batch", type=int, default=None,
        help="So text moi request embed (mac dinh 1 = tung chunk; van TUAN TU, khong song song).",
    )
    parser.add_argument(
        "--id-full", nargs="+", type=str, default=None,
        help="Embed lai cac doc theo id_full chi dinh (ke ca da danh dau).",
    )
    parser.add_argument("--limit", type=int, default=None, help="Toi da N document roi dung (debug).")
    parser.add_argument(
        "--reset", type=int, default=None, choices=(0, 9),
        help="9 = XOA + TAO LAI 2 collection Qdrant (dense) + bo danh dau chunk ES de embed lai "
             "(GIU ES chunk, KHONG dung kho_ai_dung_chung). 0 (mac dinh) = embed theo danh dau "
             "qdrant_indexed. Override KHO_QDRANT_RESET.",
    )
    asyncio.run(_main(parser.parse_args()))
