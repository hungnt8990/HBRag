"""Unit test cho helper thuần của luồng session/short-term ``/chat`` (không đụng DB)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import UUID, uuid4

from app.services.retrieval.document_chat_session_service import (
    _history_from_rows,
    _is_fresh,
    _parse_uuid,
)


def test_parse_uuid_valid_and_invalid() -> None:
    good = uuid4()
    assert _parse_uuid(str(good)) == good
    assert _parse_uuid("  " + str(good) + "  ") == good
    assert _parse_uuid(None) is None
    assert _parse_uuid("") is None
    assert _parse_uuid("not-a-uuid") is None


def test_is_fresh_threshold() -> None:
    now = datetime.now(timezone.utc)
    # Trong ngưỡng 4h -> tươi
    assert _is_fresh(now - timedelta(hours=1), 4.0) is True
    # Quá ngưỡng -> không tươi
    assert _is_fresh(now - timedelta(hours=5), 4.0) is False
    # Chưa có lượt nào / ttl<=0 -> không tươi
    assert _is_fresh(None, 4.0) is False
    assert _is_fresh(now, 0) is False


def test_is_fresh_naive_datetime_treated_as_utc() -> None:
    # DB có thể trả naive datetime -> coi như UTC, không được ném lỗi
    naive_recent = datetime.now(timezone.utc).replace(tzinfo=None)
    assert _is_fresh(naive_recent, 4.0) is True


def test_history_from_rows_filters_and_truncates() -> None:
    rows = [
        SimpleNamespace(role="user", content="  câu   hỏi  "),  # chuẩn hoá khoảng trắng
        SimpleNamespace(role="assistant", content="x" * 9000),  # cắt <= 8000
        SimpleNamespace(role="system", content="bỏ"),  # role ngoài user/assistant -> loại
        SimpleNamespace(role="user", content="   "),  # rỗng -> loại
    ]
    out = _history_from_rows(rows)
    assert [m.role for m in out] == ["user", "assistant"]
    assert out[0].content == "câu hỏi"
    assert len(out[1].content) == 8000
