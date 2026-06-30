"""Runner job đồng bộ 3-DB theo PIPELINE 3 LUỒNG (producer-consumer qua hàng đợi).

Luồng 1 (PostgreSQL): fetch văn bản + ACL + normalize -> ghi Document & chunks vào PG,
    đẩy DofficeJobItem vào 2 hàng đợi (q_es, q_qdrant).
Luồng 2 (Elasticsearch): tiêu thụ q_es -> đổ BM25 doc-level (full nội dung + ACL).
Luồng 3 (Qdrant): tiêu thụ q_qdrant -> embed chunk (đọc từ PG) + Qdrant chunks + docmeta + ACL.

3 luồng chạy ĐỘC LẬP (không chờ nhau theo từng văn bản) -> pipeline, throughput cao. Mỗi
giai đoạn có pool worker riêng (Qdrant đông nhất vì embed chậm). Hàng đợi có maxsize -> tự
backpressure (luồng 1 không chạy quá xa luồng 3). Idempotent (ingestor tự xóa dấu vết cũ).

Checkpoint LƯU CUỐI (sau khi 3 luồng rút cạn): lần chạy sau (incremental) chỉ quét văn bản
MỚI cập nhật. Nếu bị ngắt giữa chừng -> chưa lưu checkpoint -> lần sau quét lại từ mốc cũ
(idempotent nên an toàn, không tạo lỗ hổng).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from app.db.session import AsyncSessionLocal
from app.repositories.documents import DocumentRepository
from app.services.chunkers.chunker_chunking_service import ChunkingService
from app.services.documents.document_storage import get_storage_client
from app.services.embeddings.embedding_sparse_factory import get_sparse_embedding_provider
from app.services.ingestion.ingestion_doffice_unified import (
    DofficeJobItem,
    DofficeUnifiedIngestor,
)
from app.services.llm_gateway import get_llm_gateway
from app.services.retrieval.retrieval_doffice_bm25 import (
    DofficeBm25DocumentStore,
    DofficeChunkBm25Store,
)
from app.services.security.security_acl_compressor import OrgCatalog
from app.services.security.security_acl_recompress import catalog_signature
from app.services.security.security_acl_resolver import UnitTree
from app.services.vector.vector_store import (
    get_doffice_chunks_vector_store,
    get_doffice_docmeta_vector_store,
)
from jobs.doffice_sync.clients.quyen_client import QuyenEsClient
from jobs.doffice_sync.clients.vanban_client import VanbanEsClient, VanbanRecord
from jobs.doffice_sync.config import JobConfig
from jobs.doffice_sync.stores.checkpoint import CheckpointStore
from jobs.doffice_sync.stores.progress import ProgressStore

logger = logging.getLogger("doffice_sync.unified")

# Checkpoint riêng cho job 3-DB (KHÔNG dùng chung với job cũ "doffice_sync").
JOB_NAME = "doffice_unified"

# Backpressure: chặn luồng 1 chạy quá xa luồng 3 (embed chậm) -> tránh phình RAM.
_QUEUE_MAXSIZE = 200
# Số lần thử lại Qdrant/embed khi lỗi nhất thời (ReadTimeout do gateway tải cao).
_QDRANT_RETRIES = 3
# Văn bản > ngưỡng chunk -> BỎ QUA (không chunk/embed). Đồng bộ run_qdrant _MAX_CHUNK_THRESHOLD.
_MAX_CHUNK = 500


@dataclass
class UnifiedStats:
    scanned: int = 0       # đã lưu RAW vào PostgreSQL (luồng 1)
    cleaned: int = 0       # đã làm sạch + nén ACL (luồng 2)
    es_done: int = 0       # đã đổ Elasticsearch (luồng 3)
    qdrant_done: int = 0   # đã đẩy Qdrant (luồng 4)
    failed: int = 0        # lỗi luồng 1 (PG)
    clean_failed: int = 0  # lỗi luồng 2 (làm sạch)
    es_failed: int = 0
    qdrant_failed: int = 0
    qdrant_current: str = ""  # id_vb Qdrant ĐANG embed (chỉ báo còn sống)
    skipped: int = 0          # bỏ qua vì đã hoàn tất ở lần chạy trước (resume)
    errors: dict[str, str] = field(default_factory=dict)

    @property
    def ingested(self) -> int:
        """Tương thích summary cũ: số văn bản hoàn tất luồng cuối (Qdrant)."""
        return self.qdrant_done


def _acl_lists(quyen: Any) -> dict[str, Any]:
    if quyen is None:
        return {}
    return {
        "don_vi_list": getattr(quyen, "don_vi_list", None),
        "phong_ban_list": getattr(quyen, "phong_ban_list", None),
        "ca_nhan_list": getattr(quyen, "ca_nhan_list", None),
    }


class UnifiedJobRunner:
    def __init__(self, config: JobConfig) -> None:
        self.config = config
        self._vanban = VanbanEsClient(
            url=config.vanban_es_url, user=config.vanban_es_user,
            password=config.vanban_es_password, verify_ssl=config.vanban_es_verify_ssl,
        )
        self._quyen = QuyenEsClient(
            url=config.quyen_es_url or config.vanban_es_url, user=config.vanban_es_user,
            password=config.vanban_es_password, verify_ssl=config.vanban_es_verify_ssl,
        )
        self._checkpoint = CheckpointStore()
        # Tiến độ theo từng văn bản (resume khi tắt giữa chừng). File CHUNG cho mọi lần
        # chạy job 3-DB; chỉ ghi id_vb đã xong CẢ ES + Qdrant.
        self._progress = ProgressStore("logs/jobs/doffice_unified/.progress.txt")
        self._completed: set[str] = set()  # id_vb đã hoàn tất ở lần trước -> bỏ qua
        self._es_ok: set[str] = set()      # id_vb đã xong luồng ES (lần này)
        self._qdrant_ok: set[str] = set()  # id_vb đã xong luồng Qdrant (lần này)
        self.stats = UnifiedStats()
        self.phase = "Khởi tạo"
        self.feeding_done = False  # True khi luồng 1 đã NẠP HẾT văn bản (PG xong toàn bộ)
        mw = max(1, config.max_workers)
        # Qdrant (embed) là nút cổ chai -> đông nhất; PG/Clean/ES nhẹ hơn.
        self._pg_workers = config.pg_workers or max(2, mw // 4)
        self._clean_workers = config.clean_workers or max(2, mw // 4)
        self._es_workers = config.es_workers or max(2, mw // 4)
        self._qdrant_workers = config.qdrant_workers or mw

    def _on_stage_done(self, id_vb: str, *, es: bool = False, qdrant: bool = False) -> None:
        """Đánh dấu 1 văn bản xong 1 luồng; nếu xong CẢ ES+Qdrant -> ghi file tiến độ."""
        if es:
            self._es_ok.add(id_vb)
        if qdrant:
            self._qdrant_ok.add(id_vb)
        # Job PG+ES (skip_qdrant): "hoàn tất" = chỉ cần ES xong (không chờ Qdrant).
        qdrant_ok = self.config.skip_qdrant or id_vb in self._qdrant_ok
        if id_vb in self._es_ok and qdrant_ok and id_vb not in self._completed:
            self._completed.add(id_vb)
            self._progress.mark_done(id_vb)

    async def run(self) -> UnifiedStats:
        # Resume: nạp danh sách văn bản đã hoàn tất từ lần chạy trước (bỏ qua ở feeder).
        # --full-scan -> bắt đầu sạch (xóa tiến độ cũ).
        if self.config.full_scan:
            self._progress.clear()
            self._completed = set()
        else:
            self._completed = self._progress.load()
            if self._completed:
                logger.info("Resume: bỏ qua %s văn bản đã hoàn tất lần trước.", len(self._completed))

        # --- Tài nguyên dùng chung (1 lần) ---
        async with AsyncSessionLocal() as session:
            catalog = await OrgCatalog.from_session(session)
            unit_tree = await UnitTree.from_session(session)
        signature = catalog_signature(catalog)

        # Nạp profile ingestion từ DB (seed thiếu) -> chunker DOffice đọc được tham số tune.
        async with AsyncSessionLocal() as session:
            from app.repositories.ingestion_profiles import IngestionProfileRepository
            from app.services.ingestion.ingestion_profiles import load_profile_configs

            profile_repo = IngestionProfileRepository(session)
            await load_profile_configs(profile_repo)
            await profile_repo.commit()

        ctx = dict(
            catalog=catalog, unit_tree=unit_tree, signature=signature,
            gateway=get_llm_gateway(), sparse=get_sparse_embedding_provider(),
            chunks_store=get_doffice_chunks_vector_store(),
            docmeta_store=get_doffice_docmeta_vector_store(),
            bm25_store=DofficeBm25DocumentStore(),
            chunk_bm25_store=DofficeChunkBm25Store(),  # nhánh ES chunk (nhánh 2)
        )
        await ctx["chunks_store"].ensure_collection()
        await ctx["docmeta_store"].ensure_collection()
        await ctx["bm25_store"].ensure_index()
        await ctx["chunk_bm25_store"].ensure_index()

        # --- Dựng pipeline: PG (raw) -> Clean -> {ES, [Qdrant]} ---
        # skip_qdrant -> Job PG+ES: KHÔNG dựng luồng Qdrant (chạy được khi model embedding chết).
        skip_qdrant = self.config.skip_qdrant
        q_pg: asyncio.Queue = asyncio.Queue(maxsize=_QUEUE_MAXSIZE)
        q_clean: asyncio.Queue = asyncio.Queue(maxsize=_QUEUE_MAXSIZE)
        q_es: asyncio.Queue = asyncio.Queue(maxsize=_QUEUE_MAXSIZE)
        q_qdrant: asyncio.Queue | None = None if skip_qdrant else asyncio.Queue(maxsize=_QUEUE_MAXSIZE)

        pg_tasks = [
            asyncio.create_task(self._pg_worker(q_pg, q_clean, ctx))
            for _ in range(self._pg_workers)
        ]
        clean_tasks = [
            asyncio.create_task(self._clean_worker(q_clean, q_es, q_qdrant, ctx))
            for _ in range(self._clean_workers)
        ]
        es_tasks = [asyncio.create_task(self._es_worker(q_es, ctx)) for _ in range(self._es_workers)]
        qdrant_tasks = (
            []
            if skip_qdrant
            else [
                asyncio.create_task(self._qdrant_worker(q_qdrant, ctx))
                for _ in range(self._qdrant_workers)
            ]
        )

        try:
            await self._feed_records(q_pg)
            self.feeding_done = True  # đã nạp hết -> PG không còn việc, chỉ chờ các luồng sau
        finally:
            # Đóng theo thứ tự pipeline: PG -> Clean -> ES & Qdrant.
            for _ in pg_tasks:
                await q_pg.put(None)
            await asyncio.gather(*pg_tasks)
            for _ in clean_tasks:
                await q_clean.put(None)
            await asyncio.gather(*clean_tasks)
            for _ in es_tasks:
                await q_es.put(None)
            await asyncio.gather(*es_tasks)
            if qdrant_tasks:
                for _ in qdrant_tasks:
                    await q_qdrant.put(None)
                await asyncio.gather(*qdrant_tasks)
        # Run chạy tới đây (không bị kill) -> xóa file tiến độ: lần sau bắt đầu sạch (dựa
        # vào checkpoint incremental). Bị tắt giữa chừng -> không tới đây -> file còn -> resume.
        self._progress.clear()
        return self.stats

    # -------------------------------------------------------------- workers --
    def _make_ingestor(self, session: Any, ctx: dict) -> DofficeUnifiedIngestor:
        """Tạo ingestor gắn 1 session (None nếu giai đoạn không cần DB, vd ES)."""
        repository = DocumentRepository(session) if session is not None else None
        chunking = (
            ChunkingService(repository=repository, storage=get_storage_client())
            if repository is not None
            else None
        )
        return DofficeUnifiedIngestor(
            repository=repository, chunking_service=chunking,
            llm_gateway=ctx["gateway"], sparse_provider=ctx["sparse"],
            chunks_store=ctx["chunks_store"], docmeta_store=ctx["docmeta_store"],
            bm25_store=ctx["bm25_store"], catalog=ctx["catalog"],
            unit_tree=ctx["unit_tree"], signature=ctx["signature"],
            chunk_bm25_store=ctx.get("chunk_bm25_store"),
        )

    async def _pg_worker(self, q_pg: asyncio.Queue, q_clean: asyncio.Queue, ctx: dict) -> None:
        """Luồng 1: lưu RAW vào PG -> đẩy item sang luồng Làm sạch."""
        while True:
            task = await q_pg.get()
            try:
                if task is None:
                    break
                rec, quyen = task
                self.stats.scanned += 1
                try:
                    async with AsyncSessionLocal() as session:
                        ingestor = self._make_ingestor(session, ctx)
                        item = await ingestor.prepare_postgres(
                            source=rec.raw or {}, acl_lists=_acl_lists(quyen)
                        )
                    await q_clean.put(item)
                except Exception as exc:
                    self.stats.failed += 1
                    self.stats.errors[rec.id_vb] = f"PG {type(exc).__name__}: {exc}"
                    logger.error("id_vb=%s PG lỗi: %s", rec.id_vb, exc, exc_info=True)
            finally:
                q_pg.task_done()

    async def _clean_worker(
        self, q_clean: asyncio.Queue, q_es: asyncio.Queue, q_qdrant: asyncio.Queue, ctx: dict
    ) -> None:
        """Luồng 2: làm sạch + nén ACL + CHUNK -> GHI PostgreSQL, rồi đẩy sang ES & Qdrant.

        Toàn bộ phần nặng (normalize/chunk/nén ACL) làm ở đây và LƯU PG. ES đọc nội dung sạch
        + ACL nén (in-memory item). Qdrant (luồng sau) chỉ ĐỌC PG để embed."""
        while True:
            item = await q_clean.get()
            try:
                if item is None:
                    break
                try:
                    async with AsyncSessionLocal() as session:
                        ingestor = self._make_ingestor(session, ctx)
                        await ingestor.clean_data(item)  # in-memory: normalize + tom_tat + nén ACL
                        await ingestor.persist_to_postgres(item, max_chunks=_MAX_CHUNK)  # ghi PG
                    self.stats.cleaned += 1
                    await q_es.put(item)  # ES dùng item.clean_* + acl_* (in-memory)
                    # Bỏ qua văn bản quá nhiều chunk: không đẩy sang Qdrant (không embed).
                    if q_qdrant is not None and not item.skipped:
                        await q_qdrant.put(item)
                except Exception as exc:  # noqa: BLE001
                    self.stats.clean_failed += 1
                    self.stats.errors[item.id_vb] = f"Clean {type(exc).__name__}: {exc}"
                    logger.error("id_vb=%s Làm sạch/chunk lỗi: %s", item.id_vb, exc, exc_info=True)
            finally:
                q_clean.task_done()

    async def _es_worker(self, q_es: asyncio.Queue, ctx: dict) -> None:
        ingestor = self._make_ingestor(None, ctx)  # ES không cần session DB
        while True:
            item: DofficeJobItem | None = await q_es.get()
            try:
                if item is None:
                    break
                try:
                    await ingestor.index_elasticsearch(item)        # nhánh full (doc-level)
                    await ingestor.index_elasticsearch_chunks(item)  # nhánh chunk (nhánh 2)
                    self.stats.es_done += 1
                    self._on_stage_done(item.id_vb, es=True)
                except Exception as exc:
                    self.stats.es_failed += 1
                    self.stats.errors[item.id_vb] = f"ES {type(exc).__name__}: {exc}"
                    logger.error("id_vb=%s ES lỗi: %s", item.id_vb, exc, exc_info=True)
            finally:
                q_es.task_done()

    async def _qdrant_worker(self, q_qdrant: asyncio.Queue, ctx: dict) -> None:
        while True:
            item: DofficeJobItem | None = await q_qdrant.get()
            try:
                if item is None:
                    break
                self.stats.qdrant_current = item.id_vb
                # Embedding gateway hay ReadTimeout khi tải cao -> RETRY (backoff) vì
                # đây là lỗi NHẤT THỜI; index_qdrant idempotent (Qdrant upsert theo point_id).
                last_exc: Exception | None = None
                for attempt in range(_QDRANT_RETRIES):
                    try:
                        async with AsyncSessionLocal() as session:
                            ingestor = self._make_ingestor(session, ctx)
                            # Chunk + clean + ACL đã được luồng Làm sạch ghi PG -> chỉ ĐỌC PG & embed.
                            await ingestor.embed_to_qdrant(item)
                        self.stats.qdrant_done += 1
                        self._on_stage_done(item.id_vb, qdrant=True)
                        last_exc = None
                        break
                    except Exception as exc:  # noqa: BLE001
                        last_exc = exc
                        if attempt < _QDRANT_RETRIES - 1:
                            await asyncio.sleep(2 * (attempt + 1))  # 2s, 4s
                if last_exc is not None:
                    self.stats.qdrant_failed += 1
                    self.stats.errors[item.id_vb] = f"Qdrant {type(last_exc).__name__}: {last_exc}"
                    logger.error(
                        "id_vb=%s Qdrant lỗi (sau %s lần thử): %s",
                        item.id_vb, _QDRANT_RETRIES, last_exc, exc_info=True,
                    )
            finally:
                q_qdrant.task_done()

    # --------------------------------------------------------------- feeder --
    async def _feed_records(self, q_pg: asyncio.Queue) -> None:
        cfg = self.config
        # Mode id lẻ -> không scroll/checkpoint.
        if cfg.id_vb_filter:
            self.phase = "Ingest id lẻ"
            records = await self._vanban.fetch_by_id_vb(cfg.id_vb_filter)
            await self._enqueue_batch(q_pg, records)
            return

        # Scroll incremental: resume mốc updated_after từ checkpoint (trừ --full-scan).
        self.phase = "Đồng bộ"
        checkpoint = None if cfg.full_scan else await self._checkpoint.load(JOB_NAME)
        updated_after = checkpoint.updated_after if checkpoint else None
        batch_count = 0
        fed = 0
        max_capnhat = updated_after
        async for records, sort_values in self._vanban.scroll_batches(
            batch_size=cfg.batch_size, don_vi_filter=cfg.don_vi_filter,
            updated_after=updated_after, search_after=None,
        ):
            if cfg.scan_limit is not None and fed >= cfg.scan_limit:
                break
            if cfg.scan_limit is not None:
                records = records[: cfg.scan_limit - fed]
            await self._enqueue_batch(q_pg, records)
            fed += len(records)
            batch_count += 1
            for rec in records:
                if rec.ngay_capnhat and (max_capnhat is None or rec.ngay_capnhat > max_capnhat):
                    max_capnhat = rec.ngay_capnhat
            if sort_values is None:
                break

        # Lưu checkpoint CUỐI (sau khi feed hết). Worker còn đang xử lý nhưng checkpoint chỉ
        # đẩy mốc updated_after -> lần sau quét văn bản mới hơn. (Ngắt giữa chừng: không tới
        # đây -> lần sau quét lại từ mốc cũ, idempotent.)
        if not cfg.full_scan:
            await self._checkpoint.save(
                JOB_NAME, [], max_capnhat, batch_count=batch_count, doc_count=fed
            )

    async def _enqueue_batch(self, q_pg: asyncio.Queue, records: list[VanbanRecord]) -> None:
        if not records:
            return
        # Resume: bỏ qua văn bản đã hoàn tất ở lần chạy trước (không nạp lại).
        if self._completed:
            kept = []
            for rec in records:
                if rec.id_vb in self._completed:
                    self.stats.skipped += 1
                else:
                    kept.append(rec)
            records = kept
            if not records:
                return
        ids = [r.id_vb for r in records]
        quyen_map = await self._quyen.get_batch(ids)
        for rec in records:
            await q_pg.put((rec, quyen_map.get(rec.id_vb)))
