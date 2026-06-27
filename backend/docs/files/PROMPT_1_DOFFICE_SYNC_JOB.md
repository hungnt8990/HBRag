# Prompt 1: Job đồng bộ định kỳ văn bản DOffice
# `jobs/doffice_sync/run.py`

---

## TRẠNG THÁI CODEBASE HIỆN TẠI

Đây là codebase FastAPI + SQLAlchemy + Elasticsearch + Qdrant.
Các file/class đã tồn tại cần **tái sử dụng, không tạo lại**:

| File | Class/Function | Mục đích |
|------|----------------|----------|
| `app/db/session.py` | `AsyncSessionLocal`, `engine` | DB session |
| `app/core/config.py` | `settings` | Config toàn cục |
| `app/services/retrieval/retrieval_document_index.py` | `DocumentIndexStore` | ES document index |
| `app/services/security/security_acl_resolver.py` | `resolve_doffice_and_compress()`, `OrgCatalog`, `UnitTree` | Parse ACL từ DOffice |
| `app/services/security/security_acl_recompress.py` | `recompress_document()`, `catalog_signature()` | Cập nhật ACL |
| `app/services/security/security_acl_payload.py` | `acl_keys_from_acl()` | Build acl_subjects |
| `app/services/llm_gateway/__init__.py` | `get_llm_gateway()` | Embed BBQ vector |
| `alembic/versions/` | version cuối là `0014` | Migration tiếp theo là `0015` |

**`DocumentIndexStore` hiện có:**
- `ensure_index()` — tạo ES index nếu chưa có
- `bulk_index(documents)` — index nhiều records
- `search_documents()` — tìm kiếm trả về list doc_id
- `_index_definition()` — mapping hiện tại **chưa có** `noi_dung`, `noi_ban_hanh`, `nguoi_ky`, `embedding`

**`recompress_document()` hiện tại** nhận `vector_store` để update Qdrant.
Trong job này **không truyền `vector_store`** → chỉ update Postgres, không đụng Qdrant.

---

## NGUỒN DỮ LIỆU

### ES source 1: `doffice_vanban`

URL: `https://10.72.121.232:9200` — self-signed SSL → `httpx verify=False`

Scroll văn bản (search_after pagination, KHÔNG dùng from/size vì >10K docs):
```json
POST /doffice_vanban/_search
{
  "query": {
    "bool": {
      "must_not": [{"term": {"da_xoa": true}}],
      "filter": [{"range": {"ngay_capnhat": {"gte": "YYYY-MM-DD HH:MM:SS"}}}]
    }
  },
  "sort": [{"ngay_capnhat": "asc"}, {"_id": "asc"}],
  "size": 500,
  "_source": [
    "id_vb", "ky_hieu", "trich_yeu", "noi_ban_hanh", "nguoi_ky",
    "ten_file", "tom_tat", "noi_dung", "ngay_vb", "ngay_capnhat",
    "nam", "thang", "don_vi_list"
  ]
}
```

Fields quan trọng:
```
id_vb         : int    — mã văn bản (KEY)
ky_hieu       : str    — số ký hiệu (VD: "6515/EVNCPC-VTCNTT+KD+KT")
trich_yeu     : str    — trích yếu (embed BBQ)
noi_ban_hanh  : str    — nơi ban hành
nguoi_ky      : str    — người ký
ten_file      : str    — tên file PDF
tom_tat       : str    — tóm tắt AI (embed BBQ, có thể rỗng)
noi_dung      : str    — nội dung đầy đủ (BM25 only, truncate 50K)
ngay_vb       : str    — ngày văn bản
ngay_capnhat  : str    — ngày cập nhật → dùng cho incremental
da_xoa        : bool   — đã xóa → bỏ qua
```

### ES source 2: `doffice_vanban_quyen`

URL: cùng host (`https://10.72.121.232:9200`)

Batch query ACL theo id_vb:
```json
POST /doffice_vanban_quyen/_search
{
  "query": {"terms": {"id_vb": [1068586, 6093, ...]}},
  "size": 500,
  "_source": ["id_vb", "don_vi_list", "phong_ban_list",
              "ca_nhan_list", "quyen_checksum", "quyen_ngay_capnhat"]
}
```

Fields:
```
don_vi_list        : list[int]
phong_ban_list     : list[int]
ca_nhan_list       : list[int]
quyen_checksum     : str  — hash toàn bộ quyền, dùng để detect thay đổi
quyen_ngay_capnhat : str
```

Không có record trong `doffice_vanban_quyen` = chưa có ACL → lưu retry.

---

## VIỆC CẦN LÀM

### VIỆC 1 — Cập nhật `DocumentIndexStore`

**File: `app/services/retrieval/retrieval_document_index.py`**

**1A. Sửa `_index_definition()` — thêm fields mới vào mapping**

Thêm vào `"properties"`:
```python
"noi_dung":     {
    "type": "text",
    "analyzer": "vi_bm25",
    "index_options": "offsets",  # hỗ trợ highlight
},
"noi_ban_hanh": {"type": "keyword"},
"nguoi_ky":     {"type": "keyword"},
"ten_file":     {"type": "keyword"},
"embedding": {
    "type": "dense_vector",
    "dims": settings.embedding_dimension,
    "index": True,
    "similarity": "dot_product",
    "index_options": {
        "type": "bbq_hnsw",
        "m": 16,
        "ef_construction": 100,
    },
},
```

`ensure_index()` cần xử lý graceful: nếu ES <8.12 không hỗ trợ `bbq_hnsw`
thì tạo index không có field `embedding` và log warning. Index vẫn chạy BM25-only.

**1B. Thêm method `upsert_document()`**

```python
async def upsert_document(
    self,
    *,
    document_id: str,
    id_vb: str | None = None,
    ky_hieu: str | None = None,
    trich_yeu: str | None = None,
    tom_tat: str | None = None,
    noi_dung: str | None = None,        # truncate 50_000 chars nội bộ
    noi_ban_hanh: str | None = None,
    nguoi_ky: str | None = None,
    ten_file: str | None = None,
    keywords: str | None = None,
    nam: int | None = None,
    ngay_vb: str | None = None,
    acl_subjects: list[str],
    acl_deny_pb: list[int],
    acl_deny_nv: list[int],
    embedding: list[float] | None = None,   # None = không ghi field embedding
) -> None:
    """PUT _doc/{document_id} — tạo mới hoặc ghi đè toàn bộ."""
```

Truncate noi_dung trước khi ghi: `(noi_dung or "")[:50_000] or None`
Bỏ qua các field None/""/[] để record gọn.

**1C. Thêm method `update_acl()`**

```python
async def update_acl(
    self,
    document_id: str,
    *,
    acl_subjects: list[str],
    acl_deny_pb: list[int],
    acl_deny_nv: list[int],
) -> None:
    """POST _update/{document_id} — partial update chỉ 3 field ACL.
    Không đụng embedding hay BM25 fields.
    """
```

---

### VIỆC 2 — Tạo job trong thư mục `jobs/doffice_sync/`

Tạo mới hoàn toàn thư mục này. Không sửa bất kỳ file nào trong `app/`.

```
backend/
└── jobs/
    └── doffice_sync/
        ├── __init__.py
        ├── run.py                  ← entry point
        ├── config.py               ← dataclass config + đọc env
        ├── logger.py               ← setup 4 file log
        ├── clients/
        │   ├── __init__.py
        │   ├── vanban_client.py    ← scroll doffice_vanban
        │   └── quyen_client.py     ← batch query doffice_vanban_quyen
        ├── stores/
        │   ├── __init__.py
        │   ├── checkpoint.py       ← search_after cursor
        │   ├── retry.py            ← VB chưa có ACL
        │   └── run_result.py       ← kết quả mỗi lần chạy
        ├── sync/
        │   ├── __init__.py
        │   ├── checker.py          ← batch check Postgres
        │   └── processor.py        ← xử lý từng VB (5 case)
        ├── runner.py               ← orchestrator
        └── tests/
            ├── __init__.py
            ├── test_vanban_client.py
            ├── test_quyen_client.py
            ├── test_checker.py
            └── test_processor.py
```

---

### VIỆC 3 — Migration `0015_add_job_sync_tables.py`

Tạo 3 bảng mới:

```python
# job_sync_runs — kết quả mỗi lần chạy
class JobSyncRun(Base):
    __tablename__ = "job_sync_runs"
    id               : UUID PK
    job_name         : str = "doffice_sync"
    started_at       : datetime
    finished_at      : datetime | None
    status           : str   # "running"|"success"|"partial"|"failed"
    is_full_scan     : bool
    updated_after    : str | None       # mốc ngay_capnhat dùng lần này
    total_scanned    : int = 0
    total_created    : int = 0          # VB mới
    total_acl_updated: int = 0          # ACL thay đổi
    total_emb_updated: int = 0          # bổ sung embedding thiếu
    total_skipped    : int = 0          # đã có, không đổi
    total_no_acl     : int = 0          # chưa có quyền → retry
    total_failed     : int = 0
    total_no_embedding: int = 0         # created nhưng embed thất bại
    error_summary    : JSONB = {}       # {id_vb: "lỗi"}
    config_snapshot  : JSONB = {}
    log_file_path    : str | None


# job_sync_checkpoints — lưu search_after cursor để resume
class JobSyncCheckpoint(Base):
    __tablename__ = "job_sync_checkpoints"
    id            : UUID PK
    job_name      : str UNIQUE
    search_after  : JSONB               # sort values của hit cuối
    updated_after : str | None
    last_batch_at : datetime
    batch_count   : int = 0
    doc_count     : int = 0


# job_sync_retries — VB chưa có ACL, cần retry sau
class JobSyncRetry(Base):
    __tablename__ = "job_sync_retries"
    id            : UUID PK
    id_vb         : str UNIQUE
    reason        : str   # "no_acl"|"fetch_error"|"index_error"
    retry_count   : int = 0
    next_retry_at : datetime
    last_error    : str | None
    created_at    : datetime
    updated_at    : datetime
```

Migration tự động khi job start (`checkfirst=True`):
```python
async with engine.begin() as conn:
    await conn.run_sync(Base.metadata.create_all, checkfirst=True)
```

---

### VIỆC 4 — Chi tiết các module

#### `clients/vanban_client.py`

```python
@dataclass
class VanbanRecord:
    id_vb: str
    ky_hieu: str | None
    trich_yeu: str | None
    noi_ban_hanh: str | None
    nguoi_ky: str | None
    ten_file: str | None
    tom_tat: str | None
    noi_dung: str | None
    ngay_vb: str | None
    ngay_capnhat: str | None
    nam: int | None

    @property
    def embed_text(self) -> str:
        """Text để embed BBQ: trich_yeu + tom_tat."""
        return " ".join(
            p for p in [self.trich_yeu or "", self.tom_tat or ""]
            if p.strip()
        ).strip()

    @property
    def noi_dung_truncated(self) -> str | None:
        """noi_dung giới hạn 50K chars cho ES BM25."""
        return (self.noi_dung or "")[:50_000] or None


class VanbanEsClient:
    """httpx.AsyncClient(verify=False) — self-signed SSL."""

    async def scroll_batches(
        self,
        *,
        batch_size: int = 500,
        don_vi_filter: list[int] | None = None,
        updated_after: str | None = None,
        search_after: list | None = None,
    ) -> AsyncIterator[tuple[list[VanbanRecord], list | None]]:
        """Yield (records, sort_values). sort_values=None khi hết docs."""
```

#### `clients/quyen_client.py`

```python
@dataclass
class QuyenRecord:
    id_vb: str
    don_vi_list: list[int]
    phong_ban_list: list[int]
    ca_nhan_list: list[int]
    quyen_checksum: str
    quyen_ngay_capnhat: str | None

    @property
    def has_acl(self) -> bool:
        return bool(self.don_vi_list or self.phong_ban_list or self.ca_nhan_list)


class QuyenEsClient:
    async def get_batch(
        self, id_vb_list: list[str]
    ) -> dict[str, QuyenRecord]:
        """Trả dict key=id_vb. VB không có record → không có trong dict."""
```

#### `sync/checker.py`

```python
@dataclass
class PgStatus:
    id_vb: str
    exists: bool
    document_id: UUID | None
    pg_quyen_checksum: str | None
    has_embedding: bool          # False nếu chưa có hoặc embed thất bại trước

async def check_batch(
    session: AsyncSession,
    id_vb_list: list[str],
) -> dict[str, PgStatus]:
    """1 query Postgres cho cả batch.

    SELECT id,
           document_metadata->>'id_vb' as id_vb,
           document_metadata->'access'->>'quyen_checksum',
           (document_metadata->>'has_embedding')::boolean
    FROM documents
    WHERE source_type = 'doffice_elasticsearch'
      AND document_metadata->>'id_vb' = ANY(:ids)
    """
```

#### `sync/processor.py` — 5 case xử lý

```python
@dataclass
class SyncResult:
    id_vb: str
    action: str      # "created"|"acl_updated"|"emb_updated"|"skipped"|"no_acl"|"error"
    has_embedding: bool = False
    error: str | None = None
    duration_ms: int = 0


async def process_one(
    session: AsyncSession,
    store: DocumentIndexStore,
    gateway,                    # LLMGateway
    catalog: OrgCatalog,
    unit_tree: UnitTree,
    vanban: VanbanRecord,
    quyen: QuyenRecord | None,
    pg: PgStatus,
    *,
    dry_run: bool = False,
) -> SyncResult:
```

**5 case theo thứ tự kiểm tra:**

```
CASE 1 — no_acl
  quyen is None OR not quyen.has_acl
  → lưu job_sync_retries(reason="no_acl")
  → return SyncResult(action="no_acl")

CASE 2 — skipped
  pg.exists AND pg.pg_quyen_checksum == quyen.quyen_checksum AND pg.has_embedding
  → không làm gì
  → return SyncResult(action="skipped")

CASE 3 — emb_updated
  pg.exists AND pg.pg_quyen_checksum == quyen.quyen_checksum AND NOT pg.has_embedding
  → chỉ embed + store.update_document_embedding(doc_id, vector)
  → cập nhật has_embedding=True trong document_metadata
  → return SyncResult(action="emb_updated")

CASE 4 — acl_updated
  pg.exists AND pg.pg_quyen_checksum != quyen.quyen_checksum
  → embed BBQ
  → recompress_document(session, doc, catalog=catalog, unit_tree=unit_tree,
                         signature=catalog_signature(catalog), vector_store=None)
  → store.update_acl(doc_id, acl_subjects=..., acl_deny_pb=..., acl_deny_nv=...)
  → cập nhật quyen_checksum + has_embedding trong document_metadata
  → return SyncResult(action="acl_updated")

CASE 5 — created
  NOT pg.exists
  → acl, assignment, warnings = resolve_doffice_and_compress(
        don_vi_list=quyen.don_vi_list,
        phong_ban_list=quyen.phong_ban_list,
        ca_nhan_list=quyen.ca_nhan_list,
        catalog=catalog, unit_tree=unit_tree,
    )
  → embedding, ok = await _try_embed(gateway, vanban.embed_text)
  → Tạo Document trong Postgres:
      source_type = "doffice_elasticsearch"
      status = "indexed"
      document_metadata = {
          "id_vb": vanban.id_vb,
          "ky_hieu": vanban.ky_hieu,
          "trich_yeu": vanban.trich_yeu,
          "noi_ban_hanh": vanban.noi_ban_hanh,
          "nguoi_ky": vanban.nguoi_ky,
          "ten_file": vanban.ten_file,
          "tom_tat": vanban.tom_tat,
          "ngay_vb": vanban.ngay_vb,
          "nam": vanban.nam,
          "has_embedding": ok,
          "access": {
              "quyen_checksum": quyen.quyen_checksum,
              "quyen_ngay_capnhat": quyen.quyen_ngay_capnhat,
              "raw_assignment": {
                  "don_vi_list": quyen.don_vi_list,
                  "phong_ban_list": quyen.phong_ban_list,
                  "ca_nhan_list": quyen.ca_nhan_list,
              },
              "acl": acl.to_dict(),
              "acl_ver": catalog_signature(catalog),
          },
      }
  → store.upsert_document(
        document_id=str(doc.id),
        id_vb=vanban.id_vb,
        ky_hieu=vanban.ky_hieu,
        trich_yeu=vanban.trich_yeu,
        tom_tat=vanban.tom_tat,
        noi_dung=vanban.noi_dung_truncated,
        noi_ban_hanh=vanban.noi_ban_hanh,
        nguoi_ky=vanban.nguoi_ky,
        ten_file=vanban.ten_file,
        ngay_vb=vanban.ngay_vb,
        nam=vanban.nam,
        acl_subjects=acl_keys_from_acl(acl),
        acl_deny_pb=sorted(acl.deny_department_ids),
        acl_deny_nv=sorted(acl.deny_user_ids),
        embedding=embedding,
    )
  → return SyncResult(action="created", has_embedding=ok)
```

Hàm embed nội bộ (không raise):
```python
async def _try_embed(gateway, text: str) -> tuple[list[float] | None, bool]:
    if not text.strip():
        return None, False
    try:
        return await gateway.embed_query(text), True
    except Exception:
        logger.warning("Embed thất bại", exc_info=True)
        return None, False
```

#### `runner.py` — Orchestrator

```python
async def run(config: JobConfig) -> JobRunResult:
    # Catalog load 1 lần dùng chung toàn job
    async with AsyncSessionLocal() as session:
        catalog = await OrgCatalog.from_session(session)
        unit_tree = await UnitTree.from_session(session)

    gateway = get_llm_gateway()
    store = DocumentIndexStore(url=settings.two_stage_document_index_url
                               or settings.elasticsearch_url)

    # Đọc checkpoint để resume
    checkpoint = await checkpoint_store.load("doffice_sync")
    search_after = checkpoint.search_after if checkpoint and not config.full_scan else None
    updated_after = checkpoint.updated_after if checkpoint and not config.full_scan else None

    # Tạo job_sync_runs record
    run_id = await run_result_store.create(config)

    # Vòng lặp batch
    async with AsyncSessionLocal() as session:
        async for batch, sort_values in vanban_client.scroll_batches(
            batch_size=config.batch_size,
            don_vi_filter=config.don_vi_filter,
            updated_after=updated_after,
            search_after=search_after,
        ):
            id_vb_list = [v.id_vb for v in batch]

            # I/O song song
            pg_map, quyen_map = await asyncio.gather(
                checker.check_batch(session, id_vb_list),
                quyen_client.get_batch(id_vb_list),
            )

            # Xử lý song song với semaphore
            sem = asyncio.Semaphore(config.max_workers)
            results = await asyncio.gather(*[
                _process_with_sem(sem, session, store, gateway,
                                  catalog, unit_tree, v,
                                  quyen_map.get(v.id_vb),
                                  pg_map.get(v.id_vb, PgStatus(v.id_vb, False, None, None, False)),
                                  dry_run=config.dry_run)
                for v in batch
            ], return_exceptions=True)

            await session.commit()

            # Lưu checkpoint ngay sau commit
            if sort_values:
                await checkpoint_store.save("doffice_sync", sort_values, updated_after)

            await run_result_store.update_batch(run_id, results)

    await run_result_store.finish(run_id)
```

#### `logger.py` — 4 file log

Mỗi lần chạy tạo thư mục `logs/doffice_sync/YYYYMMDD_HHMMSS/` với:
```
full.log      ← DEBUG+ (tất cả)
info.log      ← INFO+
warning.log   ← WARNING+ (embed thất bại, ACL thiếu)
error.log     ← ERROR+ (kèm traceback)
```

Format: `2026-06-27 14:32:01 [INFO ] [processor] id_vb=1068586 → created ✓ embed=True`

#### `config.py`

```python
@dataclass
class JobConfig:
    # ES DOffice (đọc từ settings đã có hoặc env riêng)
    vanban_es_url: str       # "https://10.72.121.232:9200"
    vanban_es_user: str | None = None
    vanban_es_password: str | None = None
    quyen_es_url: str = ""   # mặc định = vanban_es_url

    # Filter
    don_vi_filter: list[int] | None = None   # None = tất cả đơn vị

    # Hiệu năng
    batch_size: int = 500
    max_workers: int = 20
    pg_commit_every: int = 200

    # Chế độ
    full_scan: bool = False          # bỏ checkpoint, quét từ đầu
    dry_run: bool = False
    id_vb_filter: list[str] | None = None
    scan_limit: int | None = None

    # Retry
    retry_delay_minutes: int = 60
    max_retry_count: int = 5

    # Log
    log_dir: str = "logs/doffice_sync"
```

#### `run.py` — Entry point

```python
"""
Job đồng bộ định kỳ văn bản DOffice → PostgreSQL + ES document index.
Kết hợp ACL + BBQ embedding. Không đẩy Qdrant.

Chạy định kỳ (ví dụ cron 2h/lần):
    python jobs/doffice_sync/run.py

Các option:
    --full-scan          Bỏ checkpoint, quét lại từ đầu
    --dry-run            Không ghi, chỉ log
    --id-vb 1068586 6093 Chỉ xử lý các VB này
    --retry-only         Chỉ xử lý retry queue
    --don-vi 251 256     Filter theo đơn vị
    --workers N          Số concurrent (default: 20)
    --batch N            Batch size scroll (default: 500)
    --limit N            Giới hạn số VB
"""
```

Summary cuối:
```
══════════════════════════════════════════
  DOffice Sync  —  2026-06-27 14:35:00
══════════════════════════════════════════
  Chế độ       : Incremental (resume batch 47)
  Thời gian    : 5m 23s  |  Batch: 23 × 500
  Quét         : 11,347 văn bản
  Tạo mới      : 10,890  ✓  (10,850 có BBQ / 40 thiếu BBQ)
  Cập nhật ACL : 245     ✓
  Bổ sung BBQ  : 38      ✓  (VB thiếu embedding từ trước)
  Bỏ qua       : 180     →
  Chờ quyền   : 32      ⏳ retry sau 60 phút
  Lỗi          : 5       ✗
  Checkpoint   : đã lưu  |  Log: logs/doffice_sync/20260627_143200/
══════════════════════════════════════════
```

---

## YÊU CẦU CHUNG

1. **Chỉ tạo/sửa** các file trong `jobs/doffice_sync/` và
   `app/services/retrieval/retrieval_document_index.py` và migration `0015`.
   Không sửa bất kỳ file nào khác trong `app/`.

2. **Không import Qdrant** trong job này. `recompress_document()` gọi với
   `vector_store=None`.

3. **Catalog load 1 lần** khi job start, dùng chung toàn bộ batch.

4. **search_after pagination** (không `from/size`). Sort ổn định:
   `[ngay_capnhat asc, _id asc]`. Lưu checkpoint sau mỗi batch commit.

5. **Embed graceful** — lỗi gateway không dừng job. Ghi `has_embedding=False`,
   log WARNING. CASE 3 sẽ bổ sung lần sau.

6. **noi_dung truncate 50K** — làm ở cả `VanbanRecord.noi_dung_truncated`
   (client) và `upsert_document()` (server guard).

7. **Unit tests** — mock ES clients, mock LLMGateway, mock AsyncSession.
   Test đủ 5 case của `processor.py`.

8. **Chạy pytest sau khi xong:**
   ```bash
   pytest jobs/doffice_sync/tests/ -q
   ```

---

## THỨ TỰ THỰC HIỆN

```
Bước 1: Sửa retrieval_document_index.py (mapping + upsert_document + update_acl)
Bước 2: Tạo alembic/versions/0015_add_job_sync_tables.py
Bước 3: Tạo jobs/doffice_sync/__init__.py và cấu trúc thư mục
Bước 4: jobs/doffice_sync/logger.py
Bước 5: jobs/doffice_sync/config.py
Bước 6: jobs/doffice_sync/clients/vanban_client.py
Bước 7: jobs/doffice_sync/clients/quyen_client.py
Bước 8: jobs/doffice_sync/stores/checkpoint.py
Bước 9: jobs/doffice_sync/stores/retry.py
Bước 10: jobs/doffice_sync/stores/run_result.py
Bước 11: jobs/doffice_sync/sync/checker.py
Bước 12: jobs/doffice_sync/sync/processor.py  ← 5 case
Bước 13: jobs/doffice_sync/runner.py
Bước 14: jobs/doffice_sync/run.py  ← CLI entry point
Bước 15: jobs/doffice_sync/tests/
Bước 16: pytest -q
```

Sau mỗi bước ghi rõ file đã tạo/sửa và hàm chính.

---

## VALIDATION

```bash
# Dry-run với 1 VB thực
python jobs/doffice_sync/run.py --dry-run --id-vb 1068586
# Kỳ vọng log: id_vb=1068586 → created (dry_run) embed_text=...

# Chạy thật 5 VB
python jobs/doffice_sync/run.py --limit 5 --workers 2

# Kiểm tra Postgres
SELECT document_metadata->>'id_vb',
       document_metadata->>'has_embedding',
       document_metadata->'access'->>'quyen_checksum'
FROM documents WHERE source_type='doffice_elasticsearch' LIMIT 5;

# Kiểm tra ES doc index
POST http://localhost:9200/hbrag_documents_v1/_search
{"query":{"term":{"id_vb":"1068586"}},"_source":["ky_hieu","trich_yeu","noi_ban_hanh","acl_subjects"]}
# Kỳ vọng: có record với embedding (nếu bbq), acl_subjects, noi_dung KHÔNG xuất hiện
```
