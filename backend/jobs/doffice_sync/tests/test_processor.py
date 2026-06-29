import asyncio
from types import SimpleNamespace
from uuid import UUID

from jobs.doffice_sync.clients.quyen_client import QuyenRecord
from jobs.doffice_sync.clients.vanban_client import VanbanRecord
from jobs.doffice_sync.sync import processor as proc
from jobs.doffice_sync.sync.checker import PgStatus

_DOC_ID = UUID("22222222-2222-2222-2222-222222222222")


class _FakeAcl:
    allow_unit_ids = [256]
    allow_department_ids: list = []
    allow_user_ids: list = []
    deny_department_ids: list = []
    deny_user_ids: list = []

    def to_dict(self):
        return {"allow_unit_ids": [256]}


class _FakeStore:
    def __init__(self):
        self.calls: list = []

    async def update_acl(self, doc_id, **kw):
        self.calls.append(("update_acl", str(doc_id)))

    async def upsert_document(self, **kw):
        self.calls.append(("upsert_document", kw.get("id_vb")))

    async def update_document_embedding(self, doc_id, emb):
        self.calls.append(("update_embedding", str(doc_id)))


class _FakeGateway:
    def __init__(self, fail=False):
        self._fail = fail

    async def embed_query(self, text):
        if self._fail:
            raise RuntimeError("embed down")
        return [0.1] * 8


class _FakeSession:
    async def get(self, model, doc_id):
        return SimpleNamespace(document_metadata={})

    async def flush(self):
        return None


def _vanban():
    return VanbanRecord(id_vb="1068586", trich_yeu="GIS", tom_tat="x", noi_dung="content")


def _run(store, gateway, vanban, quyen, pg, *, dry_run=False):
    return asyncio.run(
        proc.process_one(
            _FakeSession(), store, gateway, object(), object(),
            vanban, quyen, pg, signature="sig", dry_run=dry_run,
        )
    )


def _patch_resolve(monkeypatch):
    monkeypatch.setattr(
        proc, "resolve_doffice_and_compress",
        lambda **kw: (_FakeAcl(), object(), []),
    )


def test_case1_no_acl(monkeypatch) -> None:
    scheduled = []
    monkeypatch.setattr(proc, "upsert_retry", lambda s, idv, **kw: scheduled.append(idv) or _async_none())
    store = _FakeStore()
    res = _run(store, _FakeGateway(), _vanban(), None, PgStatus("1068586", False, None, None, False))
    assert res.action == "no_acl"
    assert store.calls == []


def test_case2_skipped(monkeypatch) -> None:
    _patch_resolve(monkeypatch)
    store = _FakeStore()
    quyen = QuyenRecord(id_vb="1068586", don_vi_list=[256], quyen_checksum="c1")
    pg = PgStatus("1068586", True, _DOC_ID, "c1", True)
    res = _run(store, _FakeGateway(), _vanban(), quyen, pg)
    assert res.action == "skipped"
    assert store.calls == []


def test_case3_emb_updated(monkeypatch) -> None:
    _patch_resolve(monkeypatch)
    store = _FakeStore()
    quyen = QuyenRecord(id_vb="1068586", don_vi_list=[256], quyen_checksum="c1")
    pg = PgStatus("1068586", True, _DOC_ID, "c1", False)  # thiếu embedding
    res = _run(store, _FakeGateway(), _vanban(), quyen, pg)
    assert res.action == "emb_updated"
    assert ("update_embedding", str(_DOC_ID)) in store.calls


def test_case4_acl_updated(monkeypatch) -> None:
    _patch_resolve(monkeypatch)
    store = _FakeStore()
    quyen = QuyenRecord(id_vb="1068586", don_vi_list=[256, 262], quyen_checksum="c2")
    pg = PgStatus("1068586", True, _DOC_ID, "c1", True)  # checksum khác
    res = _run(store, _FakeGateway(), _vanban(), quyen, pg)
    assert res.action == "acl_updated"
    assert ("update_acl", str(_DOC_ID)) in store.calls


def test_case5_created(monkeypatch) -> None:
    _patch_resolve(monkeypatch)

    created: list = []

    class _FakeRepo:
        def __init__(self, session):
            pass

        async def create_document(self, *, title, source_type, status, visibility):
            doc = SimpleNamespace(id=_DOC_ID, document_metadata={}, document_profile=None)
            created.append(doc)
            return doc

    monkeypatch.setattr("app.repositories.documents.DocumentRepository", _FakeRepo)
    store = _FakeStore()
    quyen = QuyenRecord(id_vb="1068586", don_vi_list=[256], quyen_checksum="c1")
    pg = PgStatus("1068586", False, None, None, False)
    res = _run(store, _FakeGateway(), _vanban(), quyen, pg)
    assert res.action == "created"
    assert res.has_embedding is True
    assert ("upsert_document", "1068586") in store.calls
    # Job sync phải lưu FULL noi_dung vào PG để nhánh click chunk lại không cần DOffice.
    assert created[0].document_metadata["noi_dung_raw"] == "content"


def test_case5_created_embed_fail(monkeypatch) -> None:
    _patch_resolve(monkeypatch)

    class _FakeRepo:
        def __init__(self, session):
            pass

        async def create_document(self, *, title, source_type, status, visibility):
            return SimpleNamespace(id=_DOC_ID, document_metadata={}, document_profile=None)

    monkeypatch.setattr("app.repositories.documents.DocumentRepository", _FakeRepo)
    store = _FakeStore()
    quyen = QuyenRecord(id_vb="1068586", don_vi_list=[256], quyen_checksum="c1")
    pg = PgStatus("1068586", False, None, None, False)
    res = _run(store, _FakeGateway(fail=True), _vanban(), quyen, pg)
    assert res.action == "created"
    assert res.has_embedding is False  # embed thất bại -> vẫn tạo, BM25-only


async def _async_none():
    return None
