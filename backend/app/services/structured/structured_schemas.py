from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class StructuredField:
    name: str
    value: Any
    aliases: list[str] = field(default_factory=list)


@dataclass
class StructuredRow:
    row_id: str
    chunk_id: str
    document_id: str
    table_id: str | None
    source_title: str | None
    fields: dict[str, Any]
    aliases: dict[str, list[str]] = field(default_factory=dict)
    raw_text: str = ""
    citation: int | None = None


@dataclass
class StructuredEvidence:
    row: StructuredRow
    score: float
    matched_fields: list[str] = field(default_factory=list)