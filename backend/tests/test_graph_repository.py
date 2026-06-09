from __future__ import annotations

import asyncio
from uuid import uuid4

from sqlalchemy.exc import ProgrammingError

from app.models.graph import GraphDocumentStatus
from app.repositories.graph import GraphRepository


class FakeScalarResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class FakeSession:
    def __init__(self, *, scalar_value=None, execute_error=None) -> None:
        self.scalar_value = scalar_value
        self.execute_error = execute_error
        self.added = []
        self.flushed = False
        self.rolled_back = False

    async def execute(self, _statement):
        if self.execute_error is not None:
            raise self.execute_error
        return FakeScalarResult(self.scalar_value)

    def add(self, value) -> None:
        self.added.append(value)

    async def flush(self) -> None:
        self.flushed = True

    async def rollback(self) -> None:
        self.rolled_back = True


class FakeUndefinedTableError(Exception):
    pass


def _undefined_graph_table_error() -> ProgrammingError:
    return ProgrammingError(
        "SELECT * FROM graph_document_status",
        {},
        FakeUndefinedTableError('relation "graph_document_status" does not exist'),
    )


def test_graph_repository_get_document_status_returns_none_when_missing() -> None:
    repository = GraphRepository(FakeSession())  # type: ignore[arg-type]

    result = asyncio.run(repository.get_document_status(uuid4()))

    assert result is None


def test_graph_repository_upsert_document_status_creates_record() -> None:
    session = FakeSession()
    repository = GraphRepository(session)  # type: ignore[arg-type]
    document_id = uuid4()

    status = asyncio.run(
        repository.upsert_document_status(
            document_id=document_id,
            graph_indexed=True,
            chunks_processed=3,
            entity_count=4,
            relation_count=5,
        )
    )

    assert isinstance(status, GraphDocumentStatus)
    assert status.document_id == document_id
    assert status.graph_indexed is True
    assert status.chunks_processed == 3
    assert status.entity_count == 4
    assert status.relation_count == 5
    assert session.added == [status]
    assert session.flushed is True


def test_graph_repository_missing_graph_table_fallback_logs_warning(caplog) -> None:
    session = FakeSession(execute_error=_undefined_graph_table_error())
    repository = GraphRepository(session)  # type: ignore[arg-type]

    result = asyncio.run(repository.get_document_status(uuid4()))

    assert result is None
    assert session.rolled_back is True
    assert "run `alembic upgrade head`" in caplog.text
