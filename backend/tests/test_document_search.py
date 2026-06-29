"""Tests cho document search: service (detect/build/execute) + route + auth hook."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.api.dependencies import document_search_auth as auth_mod  # noqa: F401 (giữ cho _patch)
from app.api.dependencies.auth import get_current_user
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
    # tra cứu số/ký hiệu rời -> ref
    assert dss.detect_search_type("qd 258") == "ref"
    assert dss.detect_search_type("258") == "ref"
    assert dss.detect_search_type("kh 80") == "ref"
    # có số nhưng kèm từ thường -> KHÔNG phải ref
    assert dss.detect_search_type("phụ cấp 2023") == "bm25"


def test_build_query_body_ref_phrase() -> None:
    body = dss.build_query_body("qd 258", 10, "ref", [], None)
    should = body["query"]["bool"]["should"]
    phrases = [c for c in should if "match_phrase" in c]
    # phrase đảo về thứ tự "<số> <loại>" để khớp "258/QĐ"
    assert any(c["match_phrase"]["ky_hieu"]["query"] == "258 qd" for c in phrases)
    assert "noi_dung" not in str(body["query"])  # ref không đụng noi_dung (tránh nhiễu)


def test_detect_mode() -> None:
    assert dss.detect_mode("x", "list") == "list"
    assert dss.detect_mode("GIS", "auto") == "list"
    assert dss.detect_mode("điều kiện nghỉ phép là gì", "auto") == "excerpt"


def test_build_query_body_fuzzy_bm25() -> None:
    body = dss.build_query_body("khen thuong", 10, "bm25", [], None)
    should = body["query"]["bool"]["should"]
    mm = [c for c in should if "multi_match" in c]
    # có nhánh fuzzy + nhánh phrase_prefix
    assert any(c["multi_match"].get("fuzziness") == "AUTO" for c in mm)
    assert any(c["multi_match"].get("type") == "phrase_prefix" for c in mm)
    # ký hiệu không fuzzy
    kh = [c for c in should if "match" in c and "ky_hieu" in c["match"]]
    assert kh and "fuzziness" not in kh[0]["match"]["ky_hieu"]


def test_build_query_body_exact_no_fuzzy() -> None:
    body = dss.build_query_body("6515/EVNCPC-VTCNTT", 10, "exact", [], None)
    dump = str(body)
    assert "fuzziness" not in dump and "phrase_prefix" not in dump


def test_extract_year_org() -> None:
    assert dss._extract_years("quỹ phúc lợi năm 2025 của evncpc") == [2025]
    assert dss._extract_years("qd 2632") == []  # số ký hiệu, không phải năm
    assert set(dss._extract_orgs("của evncpc và cpcit")) == {"evncpc", "cpcit"}


def test_build_query_body_year_org_boost() -> None:
    body = dss.build_query_body(
        "chi quy phuc loi nam 2025 cua evncpc", 10, "bm25", [], None, prefer_recent=True
    )
    # có năm tường minh -> KHÔNG bọc recency (không ép "mới nhất")
    assert "function_score" not in body["query"]
    bq = body["query"]["bool"]
    # năm là FILTER cứng (áp được cả lên knn ở hybrid)
    assert {"terms": {"nam": [2025]}} in bq["filter"]
    # org là boost mềm trong should (ưu tiên, không loại văn bản khác)
    assert any(c.get("match", {}).get("ky_hieu", {}).get("query") == "evncpc" for c in bq["should"])


def test_detect_org_query_uses_bm25() -> None:
    # nêu đích danh đơn vị -> bm25 (chính xác lexical) thay vì hybrid
    assert dss.detect_search_type("tình hình chi quỹ phúc lợi của evncpc thế nào") == "bm25"


def test_build_query_body_recency() -> None:
    on = dss.build_query_body("quy trinh", 10, "bm25", [], None, prefer_recent=True)
    fs = on["query"]["function_score"]
    assert fs["functions"][0]["gauss"]["ngay_vb.date"]  # decay theo ngày
    assert fs["boost_mode"] == "multiply" and "bool" in fs["query"]
    off = dss.build_query_body("quy trinh", 10, "bm25", [], None, prefer_recent=False)
    assert "function_score" not in off["query"]
    # ref (tra cứu số) KHÔNG áp recency
    ref = dss.build_query_body("qd 258", 10, "ref", [], None, prefer_recent=True)
    assert "function_score" not in str(ref["query"])


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


def _patch(monkeypatch, resp, *, api_key=None, org=(None, None)):
    monkeypatch.setattr(dss.settings, "elasticsearch_enabled", True)
    # Test logic hybrid/kNN gốc -> tắt BM25-only (mặc định production=True khi gateway embed chết).
    monkeypatch.setattr(dss.settings, "document_search_bm25_only", False)
    monkeypatch.setattr(auth_mod.settings, "document_search_api_key", api_key)
    monkeypatch.setattr(dss.httpx, "AsyncClient", lambda *a, **k: _FakeClient(resp))

    async def _noop(self):
        return None

    monkeypatch.setattr(DocumentIndexStore, "ensure_index", _noop)

    # Chặn DB: resolve id_pb/id_dv từ dm_nhan_vien -> trả org=(id_pb, id_dv) giả lập.
    async def _fake_resolve(id_nv):
        if org == (None, None):
            return None
        return AclSubject(id_nv=id_nv, id_pb=org[0], id_dv=org[1], is_super_admin=False)

    monkeypatch.setattr(dss, "_resolve_subject_from_db", _fake_resolve)


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


def test_search_resolves_pb_dv_from_nhan_vien(monkeypatch) -> None:
    # id_nv -> (id_pb, id_dv) thật từ dm_nhan_vien; ACL filter phải có cả 3 key.
    _patch(monkeypatch, _FakeResp(_hits()), org=(43038, 1833))
    resp = TestClient(app).post(
        "/api/document-search/search",
        json={"query": "GIS lưới điện", "id_nv": 117058, "prefer_recent": False},
    )
    assert resp.status_code == 200
    keys = _FakeClient.captured["query"]["bool"]["filter"][0]["bool"]["filter"][0]["terms"][
        "acl_subjects"
    ]
    assert {"nv_117058", "pb_43038", "dv_1833"} <= set(keys)
    body = resp.json()
    assert body["id_pb"] == 43038 and body["id_dv"] == 1833


def test_search_unknown_nhan_vien_nv_only(monkeypatch) -> None:
    # id_nv không có trong dm_nhan_vien -> chỉ key nv_, không có pb_/dv_.
    _patch(monkeypatch, _FakeResp(_hits()), org=(None, None))
    resp = TestClient(app).post(
        "/api/document-search/search",
        json={"query": "GIS lưới điện", "id_nv": 87, "prefer_recent": False},
    )
    assert resp.status_code == 200
    keys = _FakeClient.captured["query"]["bool"]["filter"][0]["bool"]["filter"][0]["terms"][
        "acl_subjects"
    ]
    assert keys == ["nv_87"]
    assert resp.json()["id_pb"] is None


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

def test_auth_blocks_without_token() -> None:
    # Bỏ override get_current_user -> kiểm auth THẬT: không có Bearer token -> 401.
    app.dependency_overrides.pop(get_current_user, None)
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


def test_inspect_acl_requires_auth() -> None:
    # Bỏ override get_current_user -> không có Bearer token -> 401.
    app.dependency_overrides.pop(get_current_user, None)
    r = TestClient(app).post("/api/document-search/acl", json={"id_vb": "1300411", "source": "es"})
    assert r.status_code == 401
