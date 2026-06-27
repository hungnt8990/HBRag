"""Orchestrator: scroll doffice_vanban -> ACL từ quyen -> index PG + ES (concurrent)."""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from uuid import UUID

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.services.retrieval.retrieval_document_index import DocumentIndexStore
from app.services.security.security_acl_compressor import OrgCatalog
from app.services.security.security_acl_recompress import catalog_signature
from app.services.security.security_acl_resolver import UnitTree
from jobs.doffice_sync.clients.quyen_client import QuyenEsClient
from jobs.doffice_sync.clients.vanban_client import VanbanEsClient, VanbanRecord
from jobs.doffice_sync.config import JobConfig
from jobs.doffice_sync.stores.checkpoint import CheckpointStore
from jobs.doffice_sync.stores.retry import due_id_vbs, upsert_retry
from jobs.doffice_sync.stores.run_result import RunResultStore
from jobs.doffice_sync.sync.checker import PgStatus, check_batch
from jobs.doffice_sync.sync.processor import SyncResult, process_one

logger = logging.getLogger("doffice_sync.runner")
JOB_NAME = "doffice_sync"

_STAT_KEYS = (
    "total_created",
    "total_acl_updated",
    "total_emb_updated",
    "total_skipped",
    "total_no_acl",
    "total_failed",
)
_ACTION_COUNTER = {
    "created": "total_created",
    "acl_updated": "total_acl_updated",
    "emb_updated": "total_emb_updated",
    "skipped": "total_skipped",
    "no_acl": "total_no_acl",
    "error": "total_failed",
}


def _new_stats() -> dict[str, int]:
    return {"total_scanned": 0, "total_no_embedding": 0, **{k: 0 for k in _STAT_KEYS}}


class JobRunner:
    def __init__(self, config: JobConfig) -> None:
        self.config = config
        self._vanban = VanbanEsClient(
            url=config.vanban_es_url,
            user=config.vanban_es_user,
            password=config.vanban_es_password,
            verify_ssl=config.vanban_es_verify_ssl,
        )
        self._quyen = QuyenEsClient(
            url=config.quyen_es_url or config.vanban_es_url,
            user=config.vanban_es_user,
            password=config.vanban_es_password,
            verify_ssl=config.vanban_es_verify_ssl,
        )
        self._checkpoint = CheckpointStore()
        self.stats: dict[str, int] = _new_stats()
        self.phase: str = "Khởi tạo"

    async def run(self, *, run_id: UUID | None = None) -> dict[str, Any]:
        cfg = self.config
        async with AsyncSessionLocal() as session:
            catalog = await OrgCatalog.from_session(session)
            unit_tree = await UnitTree.from_session(session)
        signature = catalog_signature(catalog)

        from app.services.llm_gateway import get_llm_gateway

        gateway = get_llm_gateway()
        store = DocumentIndexStore(
            url=settings.two_stage_document_index_url or settings.elasticsearch_url
        )
        await store.ensure_index()

        self.stats = _new_stats()
        stats = self.stats  # cùng tham chiếu -> spinner đọc live qua runner.stats
        error_summary: dict[str, str] = {}
        self.phase = "Đang đồng bộ"
        ctx = _Ctx(store=store, gateway=gateway, catalog=catalog, unit_tree=unit_tree, signature=signature)

        # Chế độ "danh sách id_vb cụ thể" (--id-vb hoặc --retry-only) -> không scroll.
        explicit_ids = cfg.id_vb_filter
        if cfg.retry_only and not explicit_ids:
            explicit_ids = await due_id_vbs(max_retry_count=cfg.max_retry_count, limit=cfg.scan_limit)

        if explicit_ids:
            records = await self._vanban.fetch_by_id_vb(explicit_ids)
            await self._run_batch(records, ctx, stats, error_summary)
            return {"stats": stats, "error_summary": error_summary}

        # Scroll incremental (resume từ checkpoint nếu không full_scan).
        checkpoint = None if cfg.full_scan else await self._checkpoint.load(JOB_NAME)
        search_after = checkpoint.search_after if checkpoint else None
        updated_after = checkpoint.updated_after if checkpoint else None
        batch_count = 0
        max_capnhat = updated_after

        async for records, sort_values in self._vanban.scroll_batches(
            batch_size=cfg.batch_size,
            don_vi_filter=cfg.don_vi_filter,
            updated_after=updated_after,
            search_after=search_after,
        ):
            if cfg.scan_limit is not None and stats["total_scanned"] >= cfg.scan_limit:
                break
            if cfg.scan_limit is not None:
                remaining = cfg.scan_limit - stats["total_scanned"]
                records = records[:remaining]
            await self._run_batch(records, ctx, stats, error_summary)
            batch_count += 1
            for rec in records:
                if rec.ngay_capnhat and (max_capnhat is None or rec.ngay_capnhat > max_capnhat):
                    max_capnhat = rec.ngay_capnhat
            if not cfg.dry_run and sort_values:
                await self._checkpoint.save(
                    JOB_NAME, sort_values, updated_after,
                    batch_count=batch_count, doc_count=stats["total_scanned"],
                )
            if sort_values is None:
                break

        # Hết docs -> reset search_after, đẩy mốc updated_after lên max đã thấy.
        if not cfg.dry_run and not cfg.full_scan:
            await self._checkpoint.save(
                JOB_NAME, [], max_capnhat, batch_count=batch_count, doc_count=stats["total_scanned"]
            )
        return {"stats": stats, "error_summary": error_summary}

    async def _run_batch(
        self, records: list[VanbanRecord], ctx: "_Ctx", stats: dict, error_summary: dict
    ) -> None:
        if not records:
            return
        id_vb_list = [r.id_vb for r in records]
        async with AsyncSessionLocal() as session:
            pg_map, quyen_map, es_existing = await asyncio.gather(
                check_batch(session, id_vb_list),
                self._quyen.get_batch(id_vb_list),
                ctx.store.existing_id_vb(id_vb_list),
            )
        sem = asyncio.Semaphore(self.config.max_workers)
        results = await asyncio.gather(
            *(
                self._process_with_sem(
                    sem, ctx, rec,
                    quyen_map.get(rec.id_vb),
                    pg_map.get(rec.id_vb, PgStatus(rec.id_vb, False, None, None, False)),
                    in_es=rec.id_vb in es_existing,
                )
                for rec in records
            )
        )
        for result in results:
            stats["total_scanned"] += 1
            stats[_ACTION_COUNTER.get(result.action, "total_failed")] += 1
            if result.action == "created" and not result.has_embedding:
                stats["total_no_embedding"] += 1
            if result.error:
                error_summary[result.id_vb] = result.error

    async def _process_with_sem(
        self, sem: asyncio.Semaphore, ctx: "_Ctx", vanban: VanbanRecord, quyen, pg: PgStatus,
        *, in_es: bool,
    ) -> SyncResult:
        cfg = self.config
        async with sem:
            try:
                async with AsyncSessionLocal() as session:
                    result = await process_one(
                        session, ctx.store, ctx.gateway, ctx.catalog, ctx.unit_tree,
                        vanban, quyen, pg, signature=ctx.signature, in_es=in_es, dry_run=cfg.dry_run,
                    )
                    if not cfg.dry_run:
                        await session.commit()
                if not cfg.dry_run and result.action != "no_acl":
                    await self._clear_retry(vanban.id_vb)
                return result
            except Exception as exc:
                message = f"{type(exc).__name__}: {exc}"
                logger.error("id_vb=%s lỗi: %s", vanban.id_vb, message, exc_info=True)
                if not cfg.dry_run:
                    await self._safe_retry(vanban.id_vb, message)
                return SyncResult(id_vb=vanban.id_vb, action="error", error=message)

    async def _clear_retry(self, id_vb: str) -> None:
        from jobs.doffice_sync.stores.retry import clear_retry

        try:
            async with AsyncSessionLocal() as session:
                await clear_retry(session, id_vb)
                await session.commit()
        except Exception:
            pass

    async def _safe_retry(self, id_vb: str, message: str) -> None:
        try:
            async with AsyncSessionLocal() as session:
                await upsert_retry(
                    session, id_vb, reason="index_error",
                    delay_minutes=self.config.retry_delay_minutes, last_error=message,
                )
                await session.commit()
        except Exception:
            logger.error("Không lưu được retry id_vb=%s", id_vb, exc_info=True)


class _Ctx:
    def __init__(self, *, store, gateway, catalog, unit_tree, signature) -> None:
        self.store = store
        self.gateway = gateway
        self.catalog = catalog
        self.unit_tree = unit_tree
        self.signature = signature
