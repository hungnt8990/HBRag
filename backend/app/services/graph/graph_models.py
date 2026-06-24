from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ExtractedEntity:
    name: str
    normalized_name: str
    type: str
    confidence: float
    evidence: str


@dataclass(frozen=True)
class ExtractedRelation:
    source: str
    target: str
    type: str
    description: str
    confidence: float
    evidence: str


@dataclass(frozen=True)
class GraphChunkCandidate:
    chunk_id: str
    document_id: str
    score: float
    content_preview: str
    metadata: dict[str, object]
    matched_entities: list[str] = field(default_factory=list)
    relations: list[str] = field(default_factory=list)
    source_flags: list[str] = field(default_factory=lambda: ["graph"])


@dataclass(frozen=True)
class GraphExpandResult:
    candidates: list[GraphChunkCandidate]
    matched_entities: list[str]
