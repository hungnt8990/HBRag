"""Tests cho document search: service (detect/build/execute) + route + auth hook."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.api.dependencies import document_search_auth as auth_mod
from app.main import app
from app.services.retrieval import document_search_service as dss
from app.services.retrieval.retrieval_document_index import DocumentIndexStore
from app.services.security.security_acl_payload import AclSubject


# --- detect / build (service) ---------------------------------------------

def test_detect_search_type() -> None:
    assert dss.detect_search_type("6515/EVNCPC-VTCNTT+KD+KT") == "exact"
    assert dss.detect_search_type("GIS lưới điện") == "bm25"
    assert dss.detect_search_type("quy định về phụ cấp điện lực là gì năm 2023") == "hybrid"
    assert dss.detect_search_type("một hai ba bốn năm sáu bảy") == "hybrid"


def test_detect_mode() -> None:
    assert dss.detect_mode("x", "list") == "list"
    assert dss.detect_mode("GIS", "auto") == "list"
    assert dss.detect_mode("điều kiện nghỉ phép là gì", "auto") == "excerpt"


def test_build_acl_filters() -> None:
    filters = dss.build_acl_filters(AclSubject(id_nv=117058, id_pb=43038, id_dv=1833))
    acl = filters[0]["bool"]
    assert acl["filter"][0]["terms"]["acl_subjects"]
    assert {"terms": {"acl_deny_nv": [117058]}} in acl["must_not"]
    assert {"terms": {"acl_deny_pb": [43038]}} in acl["must_not"]
    assert dss.build_acl_filters(AclSubject(id_nv=1, is_super_admin=True)) == []


# --- fake ES ---------------------------------------------------------------

class _FakeResp:
    def __init__(self, payload, status_code=200):
        self.status_code = status_code
        self._p = payload
        self.text = ""

    def json(self):
        return self._p


class _FakeClient:
    captured: dict = {}

    def __init__(self, resp):
        self._resp = resp

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, **kw):
        _FakeClient.captured = kw.get("json") or {}
        return self._resp


def _patch(monkeypatch, resp, *, api_key=None):
    monkeypatch.setattr(dss.settings, "elasticsearch_enabled", True)
    monkeypatch.setattr(auth_mod.settings, "document_search_api_key", api_key)
    monkeypatch.setattr(dss.httpx, "AsyncClient", lambda *a, **k: _FakeClient(resp))

    async def _noop(self):
        return None

    monkeypatch.setattr(DocumentIndexStore, "ensure_index", _noop)


def _hits():
    return {
        "hits": {
            "hits": [
                {
                    "_source": {"document_id": "d1", "id_vb": "1068586", "ky_hieu": "6515/EVNCPC"},
                    "_score": 12.5,
                    "highlight": {"noi_dung": ["... <mark>GIS</mark> ..."]},
                }
            ]
        }
    }


# --- endpoint --------------------------------------------------------------

def test_endpoint_exact(monkeypatch) -> None:
    _patch(monkeypatch, _FakeResp(_hits()))
    resp = TestClient(app).post(
        "/api/document-search/search", json={"query": "6515/EVNCPC-VTCNTT", "id_nv": 117058}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["search_type"] == "exact"
    assert data["results"][0]["highlights"]


def test_endpoint_hybrid_uses_knn(monkeypatch) -> None:
    _patch(monkeypatch, _FakeResp(_hits()))

    class _G:
        async def embed_query(self, q):
            return [0.1] * 8

    monkeypatch.setattr("app.services.llm_gateway.get_llm_gateway", lambda: _G())
    resp = TestClient(app).post(
        "/api/document-search/search",
        json={"query": "quy trình đồng bộ dữ liệu địa lý lưới điện là gì", "id_nv": 1},
    )
    assert resp.status_code == 200
    assert resp.json()["used_vector"] is True
    assert "knn" in _FakeClient.captured


def test_endpoint_embed_fail_fallback(monkeypatch) -> None:
    _patch(monkeypatch, _FakeResp(_hits()))

    class _G:
        async def embed_query(self, q):
            raise RuntimeError("down")

    monkeypatch.setattr("app.services.llm_gateway.get_llm_gateway", lambda: _G())
    resp = TestClient(app).post(
        "/api/document-search/search",
        json={"query": "quy trình đồng bộ dữ liệu địa lý lưới điện là gì", "id_nv": 1},
    )
    assert resp.status_code == 200
    assert resp.json()["search_type"] == "bm25"
    assert "knn" not in _FakeClient.captured


def test_endpoint_es_error_502(monkeypatch) -> None:
    _patch(monkeypatch, _FakeResp({"error": "boom"}, status_code=400))
    resp = TestClient(app).post("/api/document-search/search", json={"query": "GIS", "id_nv": 1})
    assert resp.status_code == 502


def test_endpoint_503_when_es_disabled(monkeypatch) -> None:
    monkeypatch.setattr(auth_mod.settings, "document_search_api_key", None)
    monkeypatch.setattr(dss.settings, "elasticsearch_enabled", False)
    resp = TestClient(app).post("/api/document-search/search", json={"query": "GIS", "id_nv": 1})
    assert resp.status_code == 503


# --- auth hook -------------------------------------------------------------

def test_auth_blocks_without_key(monkeypatch) -> None:
    monkeypatch.setattr(auth_mod.settings, "document_search_api_key", "secret")
    resp = TestClient(app).post("/api/document-search/search", json={"query": "GIS", "id_nv": 1})
    assert resp.status_code == 401


def test_auth_allows_with_correct_key(monkeypatch) -> None:
    _patch(monkeypatch, _FakeResp(_hits()), api_key="secret")
    resp = TestClient(app).post(
        "/api/document-search/search",
        json={"query": "GIS lưới điện", "id_nv": 1},
        headers={"X-API-Key": "secret"},
    )
    assert resp.status_code == 200


# --- inspect ACL endpoint --------------------------------------------------

def test_inspect_acl_es_allowed(monkeypatch) -> None:
    from app.services.retrieval import document_acl_inspect_service as ais

    monkeypatch.setattr(auth_mod.settings, "document_search_api_key", None)
    payload = {"hits": {"hits": [{"_source": {
        "document_id": "d1", "id_vb": "1300411", "ky_hieu": "03/ĐK",
        "acl_subjects": ["pb_40036", "nv_90257"], "acl_deny_pb": [], "acl_deny_nv": [90255]}}]}}
    monkeypatch.setattr(ais.httpx, "AsyncClient", lambda *a, **k: _FakeClient(_FakeResp(payload)))
    r = TestClient(app).post(
        "/api/document-search/acl",
        json={"id_vb": "1300411", "source": "es", "id_nv": 90257, "id_pb": 40036},
    )
    assert r.status_code == 200
    d = r.json()
    assert d["found"] is True and d["source"] == "es"
    assert "pb_40036" in d["acl_subjects"]
    assert d["subject_check"]["allowed"] is True


def test_inspect_acl_es_denied(monkeypatch) -> None:
    from app.services.retrieval import document_acl_inspect_service as ais

    monkeypatch.setattr(auth_mod.settings, "document_search_api_key", None)
    payload = {"hits": {"hits": [{"_source": {
        "document_id": "d1", "id_vb": "1300411", "ky_hieu": "03/ĐK",
        "acl_subjects": ["pb_40036"], "acl_deny_pb": [], "acl_deny_nv": [90257]}}]}}
    monkeypatch.setattr(ais.httpx, "AsyncClient", lambda *a, **k: _FakeClient(_FakeResp(payload)))
    r = TestClient(app).post(
        "/api/document-search/acl",
        json={"id_vb": "1300411", "source": "es", "id_nv": 90257, "id_pb": 40036},
    )
    d = r.json()
    assert d["subject_check"]["allowed"] is False  # bị deny bởi acl_deny_nv
    assert d["subject_check"]["denied_by_nv"] is True


def test_inspect_acl_not_found(monkeypatch) -> None:
    from app.services.retrieval import document_acl_inspect_service as ais

    monkeypatch.setattr(auth_mod.settings, "document_search_api_key", None)
    monkeypatch.setattr(ais.httpx, "AsyncClient", lambda *a, **k: _FakeClient(_FakeResp({"hits": {"hits": []}})))
    r = TestClient(app).post("/api/document-search/acl", json={"id_vb": "9999999", "source": "es"})
    assert r.status_code == 200
    assert r.json()["found"] is False


def test_inspect_acl_requires_auth(monkeypatch) -> None:
    monkeypatch.setattr(auth_mod.settings, "document_search_api_key", "secret")
    r = TestClient(app).post("/api/document-search/acl", json={"id_vb": "1300411", "source": "es"})
    assert r.status_code == 401
