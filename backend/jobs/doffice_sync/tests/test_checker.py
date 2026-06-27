import asyncio
from uuid import UUID

from jobs.doffice_sync.sync.checker import check_batch


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeSession:
    def __init__(self, rows):
        self._rows = rows

    async def execute(self, stmt):
        return _Result(self._rows)


def test_check_batch_maps_status() -> None:
    doc_id = UUID("11111111-1111-1111-1111-111111111111")
    rows = [(doc_id, "1068586", "checksum-1", "true"), (doc_id, "1234567", None, "false")]
    out = asyncio.run(check_batch(_FakeSession(rows), ["1068586", "1234567", "999"]))
    assert out["1068586"].exists is True
    assert out["1068586"].pg_quyen_checksum == "checksum-1"
    assert out["1068586"].has_embedding is True
    assert out["1234567"].has_embedding is False
    assert "999" not in out  # không có trong Postgres


def test_check_batch_empty() -> None:
    assert asyncio.run(check_batch(_FakeSession([]), [])) == {}
