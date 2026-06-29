"""Runner job đồng bộ 3-DB (thiết kế mới): mỗi văn bản -> PG + ES + Qdrant(2 col).

Modes:
- id lẻ:        ids=[...]                     (kiểm thử vài văn bản)
- theo đơn vị:  don_vi_filter=[251, ...]      (đổ theo từng đơn vị/đợt — đã chốt)
- tất cả:       không filter

Tái dùng VanbanEsClient/QuyenEsClient + OrgCatalog/UnitTree + DofficeUnifiedIngestor.
Enrichment TẮT. Idempotent (ingestor tự xóa dấu vết cũ trước khi ghi lại).
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
from app.services.ingestion.ingestion_doffice_unified import DofficeUnifiedIngestor
from app.services.llm_gateway import get_llm_gateway
from app.services.retrieval.retrieval_doffice_bm25 import DofficeBm25DocumentStore
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

logger = logging.getLogger("doffice_sync.unified")

# Checkpoint riêng cho job 3-DB (KHÔNG dùng chung với job cũ "doffice_sync").
JOB_NAME = "doffice_unified"


@dataclass
class UnifiedStats:
    scanned: int = 0
    ingested: int = 0
    failed: int = 0
    errors: dict[str, str] = field(default_factory=dict)


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
        self.stats = UnifiedStats()
        self.phase = "Khởi tạo"

    async def run(self) -> UnifiedStats:
        cfg = self.config
        async with AsyncSessionLocal() as session:
            catalog = await OrgCatalog.from_session(session)
            unit_tree = await UnitTree.from_session(session)
        signature = catalog_signature(catalog)

        # Nạp profile ingestion từ DB (đồng thời seed thiếu) -> chunker DOffice đọc
        # được tham số tune (doffice_body_max_chars...) từ profile doffice_admin.
        async with AsyncSessionLocal() as session:
            from app.repositories.ingestion_profiles import IngestionProfileRepository
            from app.services.ingestion.ingestion_profiles import load_profile_configs

            profile_repo = IngestionProfileRepository(session)
            await load_profile_configs(profile_repo)
            await profile_repo.commit()

        gateway = get_llm_gateway()
        sparse = get_sparse_embedding_provider()
        chunks_store = get_doffice_chunks_vector_store()
        docmeta_store = get_doffice_docmeta_vector_store()
        bm25_store = DofficeBm25DocumentStore()
        await chunks_store.ensure_collection()
        await docmeta_store.ensure_collection()
        await bm25_store.ensure_index()

        ctx = dict(
            catalog=catalog, unit_tree=unit_tree, signature=signature,
            gateway=gateway, sparse=sparse, chunks_store=chunks_store,
            docmeta_store=docmeta_store, bm25_store=bm25_store,
        )

        # Mode id lẻ -> không scroll/checkpoint.
        if cfg.id_vb_filter:
            self.phase = "Ingest id lẻ"
            records = await self._vanban.fetch_by_id_vb(cfg.id_vb_filter)
            await self._run_batch(records, ctx)
            return self.stats

        # Scroll incremental: resume từ checkpoint (search_after + updated_after) trừ
        # khi --full-scan. Lần chạy sau tiếp tục batch kế tiếp như job cũ.
        self.phase = "Đồng bộ"
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
            if cfg.scan_limit is not None and self.stats.scanned >= cfg.scan_limit:
                break
            if cfg.scan_limit is not None:
                records = records[: cfg.scan_limit - self.stats.scanned]
            await self._run_batch(records, ctx)
            batch_count += 1
            for rec in records:
                if rec.ngay_capnhat and (max_capnhat is None or rec.ngay_capnhat > max_capnhat):
                    max_capnhat = rec.ngay_capnhat
            # Lưu vị trí scroll sau mỗi batch -> lần sau resume từ đây.
            if sort_values:
                await self._checkpoint.save(
                    JOB_NAME, sort_values, updated_after,
                    batch_count=batch_count, doc_count=self.stats.scanned,
                )
            if sort_values is None:
                break

        # Hết docs -> reset search_after, đẩy mốc updated_after lên max đã thấy
        # (lần sau chỉ quét văn bản MỚI cập nhật sau mốc này).
        if not cfg.full_scan:
            await self._checkpoint.save(
                JOB_NAME, [], max_capnhat,
                batch_count=batch_count, doc_count=self.stats.scanned,
            )
        return self.stats

    async def _run_batch(self, records: list[VanbanRecord], ctx: dict) -> None:
        if not records:
            return
        ids = [r.id_vb for r in records]
        quyen_map = await self._quyen.get_batch(ids)
        sem = asyncio.Semaphore(self.config.max_workers)

        async def _one(rec: VanbanRecord) -> None:
            async with sem:
                self.stats.scanned += 1
                try:
                    async with AsyncSessionLocal() as session:
                        repository = DocumentRepository(session)
                        ingestor = DofficeUnifiedIngestor(
                            repository=repository,
                            chunking_service=ChunkingService(repository=repository, storage=get_storage_client()),
                            llm_gateway=ctx["gateway"], sparse_provider=ctx["sparse"],
                            chunks_store=ctx["chunks_store"], docmeta_store=ctx["docmeta_store"],
                            bm25_store=ctx["bm25_store"], catalog=ctx["catalog"],
                            unit_tree=ctx["unit_tree"], signature=ctx["signature"],
                        )
                        await ingestor.ingest(source=rec.raw or {}, acl_lists=_acl_lists(quyen_map.get(rec.id_vb)))
                    self.stats.ingested += 1
                except Exception as exc:
                    self.stats.failed += 1
                    self.stats.errors[rec.id_vb] = f"{type(exc).__name__}: {exc}"
                    logger.error("id_vb=%s ingest lỗi: %s", rec.id_vb, exc, exc_info=True)

        await asyncio.gather(*(_one(rec) for rec in records))
