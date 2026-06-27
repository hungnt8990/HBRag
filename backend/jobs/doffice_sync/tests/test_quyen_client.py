import asyncio

from jobs.doffice_sync.clients.quyen_client import QuyenEsClient, QuyenRecord


def test_has_acl() -> None:
    assert QuyenRecord(id_vb="1", don_vi_list=[256]).has_acl is True
    assert QuyenRecord(id_vb="1", phong_ban_list=[35778]).has_acl is True
    assert QuyenRecord(id_vb="1").has_acl is False


def test_from_source_normalizes_ints() -> None:
    rec = QuyenRecord.from_source(
        {"id_vb": 693358, "don_vi_list": [256, "262"], "quyen_checksum": "abc"}
    )
    assert rec.id_vb == "693358"
    assert rec.don_vi_list == [256, 262]
    assert rec.quyen_checksum == "abc"


class _FakeResp:
    def __init__(self, payload):
        self.status_code = 200
        self._p = payload
        self.text = ""

    def json(self):
        return self._p


class _FakeClient:
    def __init__(self, payload):
        self._p = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, **kw):
        return _FakeResp(self._p)


def test_get_batch_returns_dict_by_id_vb(monkeypatch) -> None:
    from jobs.doffice_sync.clients import quyen_client as mod

    payload = {
        "hits": {
            "hits": [
                {"_source": {"id_vb": 1068586, "don_vi_list": [256], "quyen_checksum": "c1"}},
            ]
        }
    }
    monkeypatch.setattr(mod.httpx, "AsyncClient", lambda **k: _FakeClient(payload))
    client = QuyenEsClient(url="https://x")
    out = asyncio.run(client.get_batch(["1068586", "999"]))
    assert "1068586" in out
    assert out["1068586"].don_vi_list == [256]
    assert "999" not in out  # không có record quyền
