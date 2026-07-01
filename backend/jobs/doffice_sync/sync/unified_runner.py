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
from jobs.doffice_sync.logger import LOG_ROOT
from jobs.doffice_sync.stores.checkpoint import CheckpointStore
from jobs.doffice_sync.stores.pending_acl import PendingAclStore
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
# Văn bản CHƯA có ACL (đơn vị/phòng ban/nhân viên list đều rỗng) -> KHÔNG chờ, BỎ QUA và ghi
# file pending; đầu lần chạy sau fetch lại theo id_vb và thử tiếp (xem PendingAclStore).


@dataclass
class UnifiedStats:
    scanned: int = 0       # đã lưu RAW vào PostgreSQL (luồng 1)
    cleaned: int = 0       # đã LÀM SẠCH noi_dung/tom_tat (ghi PG)
    acl_done: int = 0      # đã NÉN ACL (ghi PG metadata.access)
    chunked: int = 0       # đã CHUNK + ghi bảng chunks (PG)
    es_done: int = 0       # đã đổ Elasticsearch 2 nhánh (luồng cuối)
    qdrant_done: int = 0   # đã đẩy Qdrant (luồng 4)
    failed: int = 0        # lỗi luồng 1 (PG)
    clean_failed: int = 0  # lỗi luồng 2 (làm sạch)
    es_failed: int = 0
    qdrant_failed: int = 0
    qdrant_current: str = ""  # id_vb Qdrant ĐANG embed (chỉ báo còn sống)
    skipped: int = 0          # bỏ qua vì đã hoàn tất ở lần chạy trước (resume)
    # --- Theo dõi BATCH (mỗi batch = 1 lần scroll ES, kích thước batch_size) ---
    batch_size: int = 0       # kích thước batch cấu hình (DOFFICE_JOB_BATCH_SIZE)
    batches_fed: int = 0      # số batch đã NẠP vào pipeline (đã/đang chạy)
    batch_docs: int = 0       # số văn bản trong batch đang nạp
    # --- BỎ QUA vì CHƯA ACL (không chờ; thử lại lần quét sau qua file pending) ---
    acl_skipped: int = 0      # số VB bỏ qua vì chưa ACL TRONG lần chạy này
    acl_pending: int = 0      # tổng VB đang chờ ACL (file pending) -> thử lại lần sau
    acl_last_skip: str = ""   # id_vb chưa ACL gần nhất (chỉ báo)
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
        # Checkpoint/progress/pending TÁCH theo PHẠM VI quét (đơn vị) — KHÔNG dùng chung. Nếu dùng
        # chung, đổi đơn vị sẽ tái dùng mốc updated_after của đơn vị trước -> lọc gte loại sạch văn
        # bản đơn vị mới (cũ hơn mốc) -> "quét không ra". Mỗi đơn vị 1 mốc incremental riêng.
        scope = self._scope_suffix()
        self._job_key = JOB_NAME + scope
        # Tiến độ theo từng văn bản (resume khi tắt giữa chừng); chỉ ghi id_vb đã xong CẢ ES + Qdrant.
        self._progress = ProgressStore(str(LOG_ROOT / "doffice_unified" / f".progress{scope}.txt"))
        # VB chưa ACL -> bỏ qua + nhớ ở đây; đầu lần chạy sau fetch lại theo id_vb để thử tiếp.
        self._pending_acl = PendingAclStore(str(LOG_ROOT / "doffice_unified" / f".pending_acl{scope}.txt"))
        self._pending_acl_ids: set[str] = set()
        self._completed: set[str] = set()  # id_vb đã hoàn tất ở lần trước -> bỏ qua
        self._es_ok: set[str] = set()      # id_vb đã xong luồng ES (lần này)
        self._qdrant_ok: set[str] = set()  # id_vb đã xong luồng Qdrant (lần này)
        # Mốc incremental: lọc ES dùng gte (>=) để KHÔNG bỏ sót doc cùng mốc thời gian, nên
        # văn bản ở ĐÚNG mốc ngay_capnhat cũ sẽ lặp lại. Nhớ id_vb đã xử lý ở mốc đó -> bỏ qua.
        self._boundary_after: str | None = None        # ngay_capnhat mốc của lần chạy trước
        self._boundary_done: set[str] = set()          # id_vb đã xong ở đúng mốc đó
        self.stats = UnifiedStats()
        self.stats.batch_size = config.batch_size
        self.phase = "Khởi tạo"
        self.feeding_done = False  # True khi luồng 1 đã NẠP HẾT văn bản (PG xong toàn bộ)
        mw = max(1, config.max_workers)
        # Qdrant (embed) là nút cổ chai -> đông nhất; PG/Clean/ES nhẹ hơn.
        self._pg_workers = config.pg_workers or max(2, mw // 4)
        self._clean_workers = config.clean_workers or max(2, mw // 4)
        self._acl_workers = config.acl_workers or max(2, mw // 4)
        self._chunk_workers = config.chunk_workers or max(2, mw // 4)
        self._es_workers = config.es_workers or max(2, mw // 4)
        self._qdrant_workers = config.qdrant_workers or mw

    def _scope_suffix(self) -> str:
        """Hậu tố phân biệt PHẠM VI quét -> checkpoint/progress/pending riêng cho mỗi đơn vị.

        '' = mode Tất cả; '_dv258' / '_dv258-259' = theo đơn vị; '_idvb' = id lẻ (không checkpoint)."""
        if self.config.id_vb_filter:
            return "_idvb"
        if self.config.don_vi_filter:
            return "_dv" + "-".join(str(v) for v in sorted(self.config.don_vi_filter))
        return ""

    @staticmethod
    def _has_acl(quyen: Any) -> bool:
        """True nếu quyền có ÍT NHẤT 1 trong: đơn vị / phòng ban / nhân viên list (khác rỗng)."""
        if quyen is None:
            return False
        return bool(
            getattr(quyen, "don_vi_list", None)
            or getattr(quyen, "phong_ban_list", None)
            or getattr(quyen, "ca_nhan_list", None)
        )

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

        # --- Dựng pipeline 6 LUỒNG VẬT LÝ: PG(raw) -> Làm sạch -> Nén ACL -> Chunking -> {ES, [Qdrant]} ---
        # Mỗi giai đoạn 1 pool worker + 1 hàng đợi riêng (clean/acl/chunk TÁCH RỜI).
        # skip_qdrant -> Job PG+ES: KHÔNG dựng luồng Qdrant (chạy được khi model embedding chết).
        skip_qdrant = self.config.skip_qdrant
        q_pg: asyncio.Queue = asyncio.Queue(maxsize=_QUEUE_MAXSIZE)
        q_clean: asyncio.Queue = asyncio.Queue(maxsize=_QUEUE_MAXSIZE)
        q_acl: asyncio.Queue = asyncio.Queue(maxsize=_QUEUE_MAXSIZE)
        q_chunk: asyncio.Queue = asyncio.Queue(maxsize=_QUEUE_MAXSIZE)
        q_es: asyncio.Queue = asyncio.Queue(maxsize=_QUEUE_MAXSIZE)
        q_qdrant: asyncio.Queue | None = None if skip_qdrant else asyncio.Queue(maxsize=_QUEUE_MAXSIZE)

        pg_tasks = [asyncio.create_task(self._pg_worker(q_pg, q_clean, ctx)) for _ in range(self._pg_workers)]
        clean_tasks = [asyncio.create_task(self._clean_worker(q_clean, q_acl, ctx)) for _ in range(self._clean_workers)]
        acl_tasks = [asyncio.create_task(self._acl_worker(q_acl, q_chunk, ctx)) for _ in range(self._acl_workers)]
        chunk_tasks = [asyncio.create_task(self._chunk_worker(q_chunk, q_es, q_qdrant, ctx)) for _ in range(self._chunk_workers)]
        es_tasks = [asyncio.create_task(self._es_worker(q_es, ctx)) for _ in range(self._es_workers)]
        qdrant_tasks = (
            []
            if skip_qdrant
            else [asyncio.create_task(self._qdrant_worker(q_qdrant, ctx)) for _ in range(self._qdrant_workers)]
        )

        async def _drain(tasks: list, q: asyncio.Queue) -> None:
            for _ in tasks:
                await q.put(None)
            await asyncio.gather(*tasks)

        try:
            await self._feed_records(q_pg)
            self.feeding_done = True  # đã nạp hết -> PG không còn việc, chỉ chờ các luồng sau
        finally:
            # Đóng THEO THỨ TỰ pipeline: PG -> Clean -> ACL -> Chunk -> ES -> Qdrant.
            await _drain(pg_tasks, q_pg)
            await _drain(clean_tasks, q_clean)
            await _drain(acl_tasks, q_acl)
            await _drain(chunk_tasks, q_chunk)
            await _drain(es_tasks, q_es)
            if qdrant_tasks:
                await _drain(qdrant_tasks, q_qdrant)
        # Run chạy tới đây (không bị kill) -> xóa file tiến độ: lần sau bắt đầu sạch (dựa
        # vào checkpoint incremental). Bị tắt giữa chừng -> không tới đây -> file còn -> resume.
        self._progress.clear()
        return self.stats

    # -------------------------------------------------------------- workers --
    def _make_ingestor(self, session: Any, ctx: dict) -> DofficeUnifiedIngestor:
        """Tạo ingestor gắn 1 session (None nếu giai đoạn không cần DB, vd ES).

        chunking_service=None: doffice KHÔNG dùng ChunkingService (build_doffice_chunks trực
        tiếp). Bỏ luôn để KHÔNG tạo MinioStorageClient mỗi văn bản (trước đây gây chậm/treo)."""
        repository = DocumentRepository(session) if session is not None else None
        return DofficeUnifiedIngestor(
            repository=repository, chunking_service=None,
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

    async def _clean_worker(self, q_clean: asyncio.Queue, q_acl: asyncio.Queue, ctx: dict) -> None:
        """LUỒNG LÀM SẠCH (pool riêng): normalize + làm sạch noi_dung/tom_tat -> GHI PG -> q_acl."""
        no_db = self._make_ingestor(None, ctx)  # clean_only in-memory, KHÔNG giữ session lúc normalize
        while True:
            item = await q_clean.get()
            try:
                if item is None:
                    break
                try:
                    await no_db.clean_only(item)
                    async with AsyncSessionLocal() as session:
                        await self._make_ingestor(session, ctx).persist_clean(item)
                    self.stats.cleaned += 1
                    await q_acl.put(item)
                except Exception as exc:  # noqa: BLE001
                    self.stats.clean_failed += 1
                    self.stats.errors[item.id_vb] = f"Clean {type(exc).__name__}: {exc}"
                    logger.error("id_vb=%s Làm sạch lỗi: %s", item.id_vb, exc, exc_info=True)
            finally:
                q_clean.task_done()

    async def _acl_worker(self, q_acl: asyncio.Queue, q_chunk: asyncio.Queue, ctx: dict) -> None:
        """LUỒNG NÉN ACL (pool riêng): nén ACL (in-memory) -> GHI PG -> q_chunk."""
        no_db = self._make_ingestor(None, ctx)
        while True:
            item = await q_acl.get()
            try:
                if item is None:
                    break
                try:
                    no_db.compress_acl(item)
                    async with AsyncSessionLocal() as session:
                        await self._make_ingestor(session, ctx).persist_acl(item)
                    self.stats.acl_done += 1
                    await q_chunk.put(item)
                except Exception as exc:  # noqa: BLE001
                    self.stats.clean_failed += 1
                    self.stats.errors[item.id_vb] = f"ACL {type(exc).__name__}: {exc}"
                    logger.error("id_vb=%s Nén ACL lỗi: %s", item.id_vb, exc, exc_info=True)
            finally:
                q_acl.task_done()

    async def _chunk_worker(
        self, q_chunk: asyncio.Queue, q_es: asyncio.Queue, q_qdrant: asyncio.Queue | None, ctx: dict
    ) -> None:
        """LUỒNG CHUNKING (pool riêng): chunk -> GHI bảng chunks (PG) -> {ES, [Qdrant]}.

        > max_chunks -> item.skipped: vẫn đẩy ES (full) nhưng KHÔNG đẩy Qdrant (không embed)."""
        while True:
            item = await q_chunk.get()
            try:
                if item is None:
                    break
                try:
                    async with AsyncSessionLocal() as session:
                        await self._make_ingestor(session, ctx).persist_chunks(item, max_chunks=_MAX_CHUNK)
                    self.stats.chunked += 1
                    await q_es.put(item)  # ES dùng item.clean_* + acl_* + chunk_records (in-memory)
                    if q_qdrant is not None and not item.skipped:
                        await q_qdrant.put(item)
                except Exception as exc:  # noqa: BLE001
                    self.stats.clean_failed += 1
                    self.stats.errors[item.id_vb] = f"Chunk {type(exc).__name__}: {exc}"
                    logger.error("id_vb=%s Chunking lỗi: %s", item.id_vb, exc, exc_info=True)
            finally:
                q_chunk.task_done()

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
            self._pending_acl.save(self._pending_acl_ids)
            return

        # Thử lại trước các VB lần trước BỎ QUA vì chưa ACL (nay có thể đã được gán quyền).
        await self._retry_pending_acl(q_pg)

        # Scroll incremental: resume mốc updated_after từ checkpoint (trừ --full-scan).
        self.phase = "Đồng bộ"
        checkpoint = None if cfg.full_scan else await self._checkpoint.load(self._job_key)
        updated_after = checkpoint.updated_after if checkpoint else None
        # id_vb đã xử lý ở ĐÚNG mốc updated_after (lần trước) -> bỏ qua để khỏi lặp văn bản mốc.
        self._boundary_after = updated_after
        self._boundary_done = set(checkpoint.search_after or []) if checkpoint else set()
        batch_count = 0
        fed = 0
        max_capnhat = updated_after
        boundary_ids: list[str] = []  # id_vb ở mốc max_capnhat MỚI -> lưu checkpoint
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
            # records sort tăng dần theo ngay_capnhat -> mốc max là nhóm cuối. Gom id_vb ở mốc.
            for rec in records:
                nc = rec.ngay_capnhat
                if not nc:
                    continue
                if max_capnhat is None or nc > max_capnhat:
                    max_capnhat = nc
                    boundary_ids = [rec.id_vb]
                elif nc == max_capnhat:
                    boundary_ids.append(rec.id_vb)
            if sort_values is None:
                break

        # Lưu checkpoint CUỐI: updated_after = mốc max + DANH SÁCH id_vb ở mốc đó (search_after)
        # -> lần sau lọc gte(max) nhưng bỏ qua đúng các id đã xong ở mốc -> không lặp văn bản.
        if not cfg.full_scan:
            await self._checkpoint.save(
                self._job_key, boundary_ids, max_capnhat, batch_count=batch_count, doc_count=fed
            )
        # Lưu danh sách VB còn chờ ACL -> lần chạy sau thử lại (qua _retry_pending_acl).
        self._pending_acl.save(self._pending_acl_ids)

    async def _enqueue_batch(self, q_pg: asyncio.Queue, records: list[VanbanRecord]) -> None:
        if not records:
            return
        # 1 batch = 1 lần scroll ES (kích thước batch_size). Đếm để dashboard hiển thị
        # "đã/đang chạy batch thứ mấy".
        self.stats.batches_fed += 1
        self.stats.batch_docs = len(records)
        # Bỏ qua văn bản ở ĐÚNG mốc updated_after đã xử lý lần trước (gte tái lấy nhưng đã xong).
        # Nếu doc được cập nhật MỚI (ngay_capnhat > mốc) -> KHÔNG bỏ (xử lý lại đúng).
        if self._boundary_done:
            records = [
                rec for rec in records
                if not (rec.ngay_capnhat == self._boundary_after and rec.id_vb in self._boundary_done)
            ]
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
        await self._enqueue_acl_filtered(q_pg, records)

    async def _enqueue_acl_filtered(self, q_pg: asyncio.Queue, records: list[VanbanRecord]) -> None:
        """Đẩy các VB CÓ ACL; VB CHƯA ACL -> KHÔNG chờ, BỎ QUA + ghi pending (thử lại lần sau).

        Văn bản chưa có quyền (đơn vị/phòng ban/nhân viên list đều rỗng) thường do DOffice gán
        quyền TRỄ sau khi tạo VB. Thay vì dừng cả pipeline chờ, ta bỏ qua và nhớ id_vb vào
        ``_pending_acl_ids``; đầu lần chạy sau fetch lại theo id_vb để thử tiếp (xem
        ``_retry_pending_acl``)."""
        if not records:
            return
        quyen_map = await self._quyen.get_batch([r.id_vb for r in records])
        for rec in records:
            quyen = quyen_map.get(rec.id_vb)
            if self._has_acl(quyen):
                self._pending_acl_ids.discard(rec.id_vb)  # nếu trước đó pending, giờ đã có ACL
                await q_pg.put((rec, quyen))
            else:
                if rec.id_vb not in self._pending_acl_ids:
                    self._pending_acl_ids.add(rec.id_vb)
                    self.stats.acl_skipped += 1
                self.stats.acl_pending = len(self._pending_acl_ids)
                self.stats.acl_last_skip = rec.id_vb
                logger.warning(
                    "id_vb=%s CHƯA ACL (đơn vị/phòng ban/nhân viên rỗng) -> BỎ QUA, thử lại lần quét sau.",
                    rec.id_vb,
                )

    async def _retry_pending_acl(self, q_pg: asyncio.Queue) -> None:
        """Đầu lần chạy: nạp lại VB chờ ACL (lần trước bỏ qua), fetch theo id_vb rồi thử tiếp.

        Fetch trực tiếp theo id_vb (KHÔNG qua scroll incremental) vì checkpoint lọc
        ``gte(updated_after)`` sẽ không lấy lại VB cũ đã bỏ qua."""
        pending = self._pending_acl.load()
        if not pending:
            return
        self._pending_acl_ids = set(pending)
        self.stats.acl_pending = len(self._pending_acl_ids)
        records = await self._vanban.fetch_by_id_vb(sorted(pending))
        logger.info("Thử lại %s/%s văn bản chờ ACL từ lần trước.", len(records), len(pending))
        if records:
            self.phase = "Thử lại VB chờ ACL"
            await self._enqueue_acl_filtered(q_pg, records)
