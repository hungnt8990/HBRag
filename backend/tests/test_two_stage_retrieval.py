"""Tests cho two-stage retrieval: Stage1 (document index) -> Stage2 (chunk).

Bao phủ:
- Stage 1 đủ kết quả -> Stage 2 giới hạn theo document_ids của Stage 1
- Stage 1 quá ít (< stage1_min_results) -> fallback full search
- enabled=False -> bỏ qua Stage 1, delegate thẳng
- giao với document_ids của caller
- wiring get_hybrid_search_service theo cờ config
- recompress_document(document_index_store=...) -> KHÔNG đụng Qdrant
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import UUID, uuid4

from app.services.retrieval.retrieval_document_index import (
    DocumentIndexStore,
    TwoStageHybridSearchService,
)
from app.services.retrieval.retrieval_hybrid_search import HybridSearchService


class _FakeDocIndex:
    def __init__(self, doc_ids: list[str]) -> None:
        self._doc_ids = doc_ids
        self.calls: list[tuple] = []

    async def search_documents(self, query, *, top_n=50, acl_subject=None, query_vector=None):
        self.calls.append((query, top_n, acl_subject, query_vector))
        return list(self._doc_ids)


class _FakeHybrid:
    def __init__(self) -> None:
        self.run_search_calls: list[dict] = []
        self.search_calls: list[dict] = []

    async def run_search(self, *, query, top_k, document_ids=None, acl_subject=None, **kwargs):
        self.run_search_calls.append({"document_ids": document_ids, "kwargs": kwargs})
        return SimpleNamespace(hybrid_response="RUN", document_ids=document_ids)

    async def search(self, *, query, top_k, document_ids=None, acl_subject=None, **kwargs):
        self.search_calls.append({"document_ids": document_ids, "kwargs": kwargs})
        return SimpleNamespace(results=[], document_ids=document_ids)


def _service(doc_ids, *, min_results=3, enabled=True):
    idx = _FakeDocIndex(doc_ids)
    hybrid = _FakeHybrid()
    svc = TwoStageHybridSearchService(
        hybrid_search=hybrid,
        document_index=idx,
        stage1_top_n=50,
        stage1_min_results=min_results,
        enabled=enabled,
    )
    return svc, idx, hybrid


def test_two_stage_run_search_limits_to_stage1_when_enough() -> None:
    doc_ids = [str(uuid4()) for _ in range(5)]
    svc, idx, hybrid = _service(doc_ids, min_results=3)
    asyncio.run(svc.run_search(query="q", top_k=5, vector_weight=1.0, keyword_weight=1.0))
    assert len(idx.calls) == 1
    assert hybrid.run_search_calls[0]["document_ids"] == {UUID(d) for d in doc_ids}


def test_two_stage_run_search_fallback_when_too_few() -> None:
    svc, idx, hybrid = _service([str(uuid4())], min_results=3)  # 1 < 3
    asyncio.run(svc.run_search(query="q", top_k=5, vector_weight=1.0, keyword_weight=1.0))
    assert len(idx.calls) == 1
    # fallback -> không giới hạn scope
    assert hybrid.run_search_calls[0]["document_ids"] is None


def test_two_stage_disabled_delegates_without_stage1() -> None:
    doc_ids = [str(uuid4()) for _ in range(5)]
    svc, idx, hybrid = _service(doc_ids, enabled=False)
    asyncio.run(svc.run_search(query="q", top_k=5, vector_weight=1.0, keyword_weight=1.0))
    assert idx.calls == []  # Stage 1 không được gọi
    assert hybrid.run_search_calls[0]["document_ids"] is None


def test_two_stage_intersects_caller_document_ids() -> None:
    ids = [str(uuid4()) for _ in range(5)]
    svc, idx, hybrid = _service(ids, min_results=1)
    caller = {UUID(ids[0]), UUID(ids[1]), uuid4()}  # 1 id ngoài Stage 1
    asyncio.run(
        svc.run_search(
            query="q", top_k=5, document_ids=caller, vector_weight=1.0, keyword_weight=1.0
        )
    )
    assert hybrid.run_search_calls[0]["document_ids"] == {UUID(ids[0]), UUID(ids[1])}


def test_two_stage_search_method_also_limits() -> None:
    doc_ids = [str(uuid4()) for _ in range(4)]
    svc, idx, hybrid = _service(doc_ids, min_results=2)
    asyncio.run(svc.search(query="q", top_k=5, vector_weight=1.0, keyword_weight=1.0))
    assert hybrid.search_calls[0]["document_ids"] == {UUID(d) for d in doc_ids}


def test_get_hybrid_search_service_plain_when_disabled(monkeypatch) -> None:
    from app.api.routes import search as search_module

    monkeypatch.setattr(search_module.settings, "doffice_retrieval_enabled", False)
    monkeypatch.setattr(search_module.settings, "two_stage_retrieval_enabled", False)
    svc = search_module.get_hybrid_search_service(
        vector_search_service=object(),
        keyword_search_service=object(),
        retrieval_log_repository=object(),
        llm_gateway=object(),
    )
    assert isinstance(svc, HybridSearchService)
    assert not isinstance(svc, TwoStageHybridSearchService)


def test_get_hybrid_search_service_wraps_when_enabled(monkeypatch) -> None:
    from app.api.routes import search as search_module

    monkeypatch.setattr(search_module.settings, "doffice_retrieval_enabled", False)
    monkeypatch.setattr(search_module.settings, "two_stage_retrieval_enabled", True)
    svc = search_module.get_hybrid_search_service(
        vector_search_service=object(),
        keyword_search_service=object(),
        retrieval_log_repository=object(),
        llm_gateway=object(),
    )
    assert isinstance(svc, TwoStageHybridSearchService)


def test_recompress_document_uses_document_index_not_vector_store(monkeypatch) -> None:
    from app.services.security import security_acl_recompress as mod

    fake_compressed = SimpleNamespace(
        allow_unit_ids=[1],
        allow_department_ids=[2],
        allow_user_ids=[3],
        deny_department_ids=[4],
        deny_user_ids=[5],
        to_dict=lambda: {"acl": "v"},
    )
    monkeypatch.setattr(
        mod, "resolve_and_compress", lambda raw, catalog, unit_tree=None: fake_compressed
    )
    monkeypatch.setattr(mod.RawAssignment, "from_dict", lambda data: object())

    class _FakeVS:
        def __init__(self) -> None:
            self.called = False

        async def set_acl_payload_for_document(self, *args, **kwargs):
            self.called = True

    class _FakeDI:
        def __init__(self) -> None:
            self.call = None

        async def update_acl(self, document_id, *, acl_subjects, acl_deny_pb, acl_deny_nv):
            self.call = {
                "document_id": document_id,
                "acl_subjects": acl_subjects,
                "acl_deny_pb": acl_deny_pb,
                "acl_deny_nv": acl_deny_nv,
            }

    class _FakeSession:
        async def flush(self):
            return None

    doc = SimpleNamespace(
        id=UUID("11111111-1111-1111-1111-111111111111"),
        document_metadata={
            "access": {"raw_assignment": {"x": 1}, "acl_ver": "old", "acl": {}}
        },
    )
    vs, di = _FakeVS(), _FakeDI()
    changed = asyncio.run(
        mod.recompress_document(
            _FakeSession(),
            doc,
            catalog=object(),
            unit_tree=None,
            signature="sig-new",
            vector_store=vs,
            document_index_store=di,
            force=False,
        )
    )
    assert changed is True
    assert vs.called is False  # Qdrant KHÔNG bị đụng
    assert di.call is not None
    assert di.call["document_id"] == "11111111-1111-1111-1111-111111111111"
    assert di.call["acl_subjects"] == ["dv_1", "nv_3", "pb_2"]
    assert di.call["acl_deny_pb"] == [4]
    assert di.call["acl_deny_nv"] == [5]


def test_recompress_document_legacy_uses_vector_store(monkeypatch) -> None:
    from app.services.security import security_acl_recompress as mod

    fake_compressed = SimpleNamespace(
        allow_unit_ids=[1],
        allow_department_ids=[],
        allow_user_ids=[],
        deny_department_ids=[],
        deny_user_ids=[],
        to_dict=lambda: {"acl": "v"},
    )
    monkeypatch.setattr(
        mod, "resolve_and_compress", lambda raw, catalog, unit_tree=None: fake_compressed
    )
    monkeypatch.setattr(mod.RawAssignment, "from_dict", lambda data: object())

    class _FakeVS:
        def __init__(self) -> None:
            self.called = False

        async def set_acl_payload_for_document(self, document_id, payload):
            self.called = True

    class _FakeSession:
        async def flush(self):
            return None

    doc = SimpleNamespace(
        id=UUID("22222222-2222-2222-2222-222222222222"),
        document_metadata={
            "access": {"raw_assignment": {"x": 1}, "acl_ver": "old", "acl": {}}
        },
    )
    vs = _FakeVS()
    changed = asyncio.run(
        mod.recompress_document(
            _FakeSession(),
            doc,
            catalog=object(),
            unit_tree=None,
            signature="sig-new",
            vector_store=vs,
            document_index_store=None,
            force=False,
        )
    )
    assert changed is True
    assert vs.called is True


# ---------------------------------------------------------------------------
# DocumentIndexStore.update_acl / search_documents (mock httpx)
# ---------------------------------------------------------------------------

class _FakeResp:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = text

    def json(self):
        return self._payload


class _FakeHttpClient:
    def __init__(self, capture, resp):
        self._capture = capture
        self._resp = resp

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def _record(self, method, url, **kw):
        self._capture.append({"method": method, "url": url, **kw})
        return self._resp

    async def head(self, url, **kw):
        return await self._record("HEAD", url, **kw)

    async def post(self, url, **kw):
        return await self._record("POST", url, **kw)

    async def put(self, url, **kw):
        return await self._record("PUT", url, **kw)

    async def delete(self, url, **kw):
        return await self._record("DELETE", url, **kw)


def _patch_httpx(monkeypatch, capture, resp):
    from app.services.retrieval import retrieval_document_index as mod

    monkeypatch.setattr(mod.httpx, "AsyncClient", lambda *a, **k: _FakeHttpClient(capture, resp))


def test_update_acl_partial_body(monkeypatch) -> None:
    import json

    capture: list[dict] = []
    _patch_httpx(monkeypatch, capture, _FakeResp(200))
    store = DocumentIndexStore()
    asyncio.run(
        store.update_acl("doc-1", acl_subjects=["nv_1", "pb_2"], acl_deny_pb=[3], acl_deny_nv=[4])
    )
    post = [c for c in capture if c["method"] == "POST"][-1]
    assert post["url"].endswith("/_update/doc-1")
    body = json.loads(post["content"].decode("utf-8"))
    assert set(body["doc"].keys()) == {"acl_subjects", "acl_deny_pb", "acl_deny_nv"}
    assert body["doc"]["acl_subjects"] == ["nv_1", "pb_2"]
    assert body["doc"]["acl_deny_pb"] == [3]


def test_search_documents_bm25_only_when_no_vector(monkeypatch) -> None:
    capture: list[dict] = []
    resp = _FakeResp(200, {"hits": {"hits": [{"_source": {"document_id": "d1"}}]}})
    _patch_httpx(monkeypatch, capture, resp)
    store = DocumentIndexStore()
    ids = asyncio.run(store.search_documents("quy trình", top_n=10))
    assert ids == ["d1"]
    search = [c for c in capture if c["method"] == "POST" and "_search" in c["url"]][-1]
    assert "knn" not in search["json"]


def test_search_documents_hybrid_when_vector(monkeypatch) -> None:
    capture: list[dict] = []
    resp = _FakeResp(200, {"hits": {"hits": [{"_source": {"document_id": "d1"}}]}})
    _patch_httpx(monkeypatch, capture, resp)
    store = DocumentIndexStore()
    ids = asyncio.run(
        store.search_documents("quy trình", top_n=10, query_vector=[0.1] * 8)
    )
    assert ids == ["d1"]
    search = [c for c in capture if c["method"] == "POST" and "_search" in c["url"]][-1]
    assert "knn" in search["json"]
    assert search["json"]["knn"]["field"] == "embedding"
