"""Tests cho API DOffice cập nhật ACL: route wiring + auth hook + map lỗi + checksum."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.api.dependencies import acl_update_auth as auth_mod
from app.api.routes import doffice_acl as route_mod
from app.main import app
from app.services.retrieval.document_acl_update_service import (
    AclUpdateError,
    AclUpdateResponse,
    DocumentNotFoundError,
    _quyen_checksum,
)


def _ok_response(action: str = "acl_updated", acl_source: str = "params") -> AclUpdateResponse:
    return AclUpdateResponse(
        id_vb="1084300", document_id="d1", action=action, acl_source=acl_source,
        updated=True, es_updated=True, has_embedding=True,
        acl_subjects=["pb_40036", "nv_90263"], acl_deny_pb=[], acl_deny_nv=[90251],
        quyen_checksum="abc", warnings=[],
    )


def _patch_service(monkeypatch, *, result=None, exc=None, api_key=None):
    monkeypatch.setattr(auth_mod.settings, "doffice_acl_api_key", api_key)

    async def _fake(id_vb, **kw):
        if exc is not None:
            raise exc
        return result

    monkeypatch.setattr(route_mod, "update_document_acl", _fake)


def test_update_acl_success(monkeypatch) -> None:
    _patch_service(monkeypatch, result=_ok_response())
    resp = TestClient(app).post(
        "/api/doffice/acl/update",
        json={"id_vb": "1084300", "phong_ban_list": [40036], "ca_nhan_list": [90263]},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["action"] == "acl_updated"
    assert data["updated"] is True and data["es_updated"] is True
    assert "pb_40036" in data["acl_subjects"]
    assert data["acl_deny_nv"] == [90251]


def test_update_acl_auto_create(monkeypatch) -> None:
    # văn bản chưa có -> service tự fetch nội dung + fetch quyền nguồn + tạo mới
    _patch_service(monkeypatch, result=_ok_response(action="created", acl_source="doffice_vanban_quyen"))
    resp = TestClient(app).post("/api/doffice/acl/update", json={"id_vb": "1306546"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["action"] == "created"
    assert data["acl_source"] == "doffice_vanban_quyen"


def test_update_acl_not_found(monkeypatch) -> None:
    # không có ở PG LẪN nguồn DOffice -> 404
    _patch_service(monkeypatch, exc=DocumentNotFoundError("không có ở DOffice"))
    resp = TestClient(app).post("/api/doffice/acl/update", json={"id_vb": "9999999"})
    assert resp.status_code == 404


def test_update_acl_es_error(monkeypatch) -> None:
    _patch_service(monkeypatch, exc=AclUpdateError("ES down"))
    resp = TestClient(app).post("/api/doffice/acl/update", json={"id_vb": "1084300"})
    assert resp.status_code == 502


def test_update_acl_auth_blocks(monkeypatch) -> None:
    _patch_service(monkeypatch, result=_ok_response(), api_key="secret")
    resp = TestClient(app).post("/api/doffice/acl/update", json={"id_vb": "1084300"})
    assert resp.status_code == 401


def test_update_acl_auth_allows_with_key(monkeypatch) -> None:
    _patch_service(monkeypatch, result=_ok_response(), api_key="secret")
    resp = TestClient(app).post(
        "/api/doffice/acl/update", json={"id_vb": "1084300"}, headers={"X-API-Key": "secret"}
    )
    assert resp.status_code == 200


def test_quyen_checksum_deterministic() -> None:
    # cùng tập (khác thứ tự/trùng) -> cùng checksum
    a = _quyen_checksum([256], [40036, 42208], [90263, 90288])
    b = _quyen_checksum([256], [42208, 40036], [90288, 90263, 90263])
    assert a == b
    # đổi nội dung -> khác checksum
    assert a != _quyen_checksum([256], [40036], [90263])
