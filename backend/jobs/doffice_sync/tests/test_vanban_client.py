import asyncio

from jobs.doffice_sync.clients.vanban_client import VanbanEsClient, VanbanRecord


def test_embed_text_combines_trich_yeu_tom_tat() -> None:
    rec = VanbanRecord(id_vb="1", trich_yeu="Kế hoạch GIS", tom_tat="Tóm tắt AI")
    assert rec.embed_text == "Kế hoạch GIS Tóm tắt AI"
    assert VanbanRecord(id_vb="1").embed_text == ""


def test_noi_dung_truncated_50k() -> None:
    rec = VanbanRecord(id_vb="1", noi_dung="x" * 60_000)
    assert len(rec.noi_dung_truncated) == 50_000
    assert VanbanRecord(id_vb="1", noi_dung="").noi_dung_truncated is None


def test_from_source_maps_fields() -> None:
    rec = VanbanRecord.from_source({"id_vb": 1068586, "ky_hieu": "6515", "nam": 2025})
    assert rec.id_vb == "1068586"
    assert rec.ky_hieu == "6515"
    assert rec.nam == 2025


class _FakeResp:
    def __init__(self, payload):
        self.status_code = 200
        self._p = payload
        self.text = ""

    def json(self):
        return self._p


class _FakeClient:
    def __init__(self, pages):
        self._pages = pages
        self._i = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, **kw):
        page = self._pages[self._i]
        self._i += 1
        return _FakeResp(page)


def test_scroll_batches_paginates(monkeypatch) -> None:
    from jobs.doffice_sync.clients import vanban_client as mod

    pages = [
        {"hits": {"hits": [{"_source": {"id_vb": 1}, "sort": [1, 1]}] * 2}},  # full batch (size 2)
        {"hits": {"hits": [{"_source": {"id_vb": 3}, "sort": [3, 3]}]}},  # last (partial)
    ]
    monkeypatch.setattr(mod.httpx, "AsyncClient", lambda **k: _FakeClient(pages))
    client = VanbanEsClient(url="https://x")

    async def collect():
        out = []
        async for recs, sort in client.scroll_batches(batch_size=2):
            out.append((len(recs), sort))
        return out

    out = asyncio.run(collect())
    assert out[0][1] == [1, 1]  # tiếp tục search_after
    assert out[-1][1] is None  # batch cuối
