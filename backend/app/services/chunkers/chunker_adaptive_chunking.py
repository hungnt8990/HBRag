from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

TABLE_PLACEHOLDER_RE = re.compile(r"(\[\[TABLE_\d+]]|<TABLE>|{{TABLE_\d+}})", re.IGNORECASE)
ARTICLE_RE = re.compile(r"(?im)^\s*Điều\s+(?P<number>\d+[a-zA-Z]?)\s*[\.:]?\s*(?P<title>.*)$")
CHAPTER_RE = re.compile(r"(?im)^\s*Chương\s+(?P<number>[IVXLCDM]+|\d+)\s*[\.:]?\s*(?P<title>.*)$")
SECTION_RE = re.compile(r"(?im)^\s*Mục\s+(?P<number>\d+[a-zA-Z]?)\s*[\.:]?\s*(?P<title>.*)$")
CLAUSE_RE = re.compile(r"(?m)^\s*(?P<number>\d+)[\.)]\s+")
POINT_RE = re.compile(r"(?m)^\s*(?P<label>[a-zđ])[\.)]\s+", re.IGNORECASE)
HEADING_RE = re.compile(
    r"(?m)^\s*(?P<label>(?:[IVXLCDM]+|\d+(?:\.\d+)*|[A-Z])[\.)])\s+(?P<title>\S.{0,180})$",
    re.IGNORECASE,
)
FOOTER_RE = re.compile(
    r"(?im)^\s*(Nơi nhận|Noi nhan|KT\.|TL\.|TUQ\.|PHÓ\s+|PHO\s+|TỔNG\s+GIÁM\s+ĐỐC|TONG\s+GIAM\s+DOC)\b"
)
APPENDIX_RE = re.compile(r"(?im)^\s*(PHỤ\s+LỤC|PHU\s+LUC|Appendix)\b")
SENTENCE_END_RE = re.compile(r"[.!?。！？…]\s*$")


@dataclass(frozen=True)
class EvidenceChunk:
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ChunkQualityResult:
    metadata: dict[str, Any]
    reasons: list[str]

    @property
    def passed(self) -> bool:
        return not self.reasons


def normalize_text_for_chunking(text: str) -> str:
    """Normalize for deterministic matching while preserving Vietnamese content."""

    normalized = unicodedata.normalize("NFC", str(text or ""))
    normalized = normalized.replace("\r\n", "\n").replace("\r", "\n").replace("\u00a0", " ")
    normalized = normalized.replace("–", "-").replace("—", "-")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in normalized.split("\n")]
    compact: list[str] = []
    previous_blank = False
    for line in lines:
        if not line:
            if compact and not previous_blank:
                compact.append("")
            previous_blank = True
            continue
        compact.append(line)
        previous_blank = False
    return "\n".join(compact).strip()


def build_body_evidence_chunks(
    *,
    text: str,
    base_metadata: dict[str, Any],
    max_chars: int = 2800,
    overlap_chars: int = 300,
) -> list[EvidenceChunk]:
    clean = normalize_text_for_chunking(text)
    if not clean:
        return []
    if ARTICLE_RE.search(clean):
        return _legal_chunks(clean, base_metadata=base_metadata, max_chars=max_chars, overlap_chars=overlap_chars)
    if HEADING_RE.search(clean):
        return _section_chunks(clean, base_metadata=base_metadata, max_chars=max_chars, overlap_chars=overlap_chars)
    return _recursive_chunks(
        clean,
        base_metadata={**base_metadata, "chunk_strategy": "recursive_fallback"},
        max_chars=max_chars,
        overlap_chars=overlap_chars,
    )


def apply_chunk_quality_gate(content: str, metadata: dict[str, Any]) -> ChunkQualityResult:
    updated = dict(metadata)
    reasons: list[str] = []
    chunk_type = str(updated.get("chunk_type") or "")
    source_type = str(updated.get("source_type") or "")

    if TABLE_PLACEHOLDER_RE.search(content or ""):
        reasons.append("table_placeholder")
        updated["indexable"] = False
        updated["embedding_enabled"] = False
    if chunk_type == "footer_signature":
        updated["indexable"] = bool(updated.get("indexable", False))
        updated["embedding_enabled"] = bool(updated.get("embedding_enabled", False))
        if APPENDIX_RE.search(content or ""):
            reasons.append("footer_contains_appendix")
    if chunk_type == "table_row":
        if not updated.get("table_title"):
            reasons.append("missing_table_title")
        if not updated.get("table_headers"):
            reasons.append("missing_table_headers")
        if updated.get("row_index") is None:
            reasons.append("missing_row_index")
    if chunk_type == "legal_clause" and not updated.get("article_number"):
        reasons.append("missing_article_context")
    if source_type == "doffice_elasticsearch":
        if not (updated.get("document_code") or updated.get("doc_code") or updated.get("ky_hieu")):
            reasons.append("missing_document_code")
        if not (updated.get("issued_date") or updated.get("ngay_vb")):
            updated.setdefault("quality_warnings", []).append("missing_issued_date")
        if not (updated.get("issuing_org") or updated.get("issuer") or updated.get("noi_ban_hanh")):
            updated.setdefault("quality_warnings", []).append("missing_issuing_org")
    if not updated.get("chunk_type"):
        reasons.append("missing_chunk_type")
    if not updated.get("source_span"):
        updated.setdefault("quality_warnings", []).append("missing_source_span")

    if reasons:
        updated["quality_status"] = "failed" if "table_placeholder" in reasons else "warning"
        updated["quality_gate_reasons"] = reasons
        if any(reason.startswith("missing_table") or reason == "missing_article_context" for reason in reasons):
            updated["indexable"] = False
            updated["embedding_enabled"] = False
    else:
        updated.setdefault("quality_status", "pass")
    return ChunkQualityResult(metadata=updated, reasons=reasons)


def standard_document_context(metadata: dict[str, Any]) -> list[str]:
    code = metadata.get("document_code") or metadata.get("doc_code") or metadata.get("ky_hieu")
    title = metadata.get("document_title") or metadata.get("trich_yeu") or metadata.get("subject")
    issued_date = metadata.get("issued_date") or metadata.get("ngay_vb")
    issuing_org = metadata.get("issuing_org") or metadata.get("issuer") or metadata.get("noi_ban_hanh")
    lines: list[str] = []
    if code or title:
        lines.append(f"Văn bản: {code or ''} - {title or ''}".strip(" -"))
    if issued_date:
        lines.append(f"Ngày ban hành: {issued_date}")
    if issuing_org:
        lines.append(f"Cơ quan ban hành: {issuing_org}")
    return lines


def _legal_chunks(
    text: str,
    *,
    base_metadata: dict[str, Any],
    max_chars: int,
    overlap_chars: int,
) -> list[EvidenceChunk]:
    headings = list(ARTICLE_RE.finditer(text))
    if not headings:
        return []
    chunks: list[EvidenceChunk] = []
    for index, match in enumerate(headings):
        start = match.start()
        end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
        article_text = text[start:end].strip()
        chapter = _last_match_before(CHAPTER_RE, text, start)
        section = _last_match_before(SECTION_RE, text, start)
        article_number = match.group("number")
        article_title = normalize_text_for_chunking(match.group("title") or "")
        metadata = {
            **base_metadata,
            "chunk_type": "legal_clause",
            "chunk_strategy": "legal_clause_aware",
            "article_number": article_number,
            "article_title": article_title,
            "chapter_number": chapter.group("number") if chapter else None,
            "chapter_title": normalize_text_for_chunking(chapter.group("title")) if chapter else None,
            "section_number": section.group("number") if section else None,
            "section_title": _legal_section_title(chapter, section, match),
            "legal_path": _legal_path(chapter, section, match),
            "summary": _legal_clause_summary(
                article_text=article_text,
                article_number=article_number,
                article_title=article_title,
            ),
            "source_span": {"start": start, "end": end},
        }
        clause = CLAUSE_RE.search(article_text)
        point = POINT_RE.search(article_text)
        if clause:
            metadata["clause_number"] = clause.group("number")
        if point:
            metadata["point_label"] = point.group("label")
        chunks.extend(
            _bounded_chunks(
                article_text,
                base_metadata=metadata,
                max_chars=max_chars,
                overlap_chars=overlap_chars,
            )
        )
    return chunks


def _legal_clause_summary(*, article_text: str, article_number: str, article_title: str | None) -> str:
    topics: list[str] = []
    if article_title:
        topics.append(article_title)
    for line in article_text.splitlines():
        clean = normalize_text_for_chunking(line).strip(" -;.")
        if not clean or ARTICLE_RE.match(clean):
            continue
        if CLAUSE_RE.match(clean) or POINT_RE.match(clean):
            topic = re.sub(r"^\s*(?:\d+[.)]|[a-zđ][.)])\s+", "", clean, flags=re.IGNORECASE)
            topic = topic.split(":", 1)[0].strip(" -;.")
            if 4 <= len(topic) <= 120:
                topics.append(topic)
        if len(topics) >= 6:
            break
    topics = _unique_ordered(topics)[:5]
    if not topics:
        return f"Điều {article_number} quy định nội dung của điều khoản này."
    bullet_lines = "\n".join(f"- {topic}" for topic in topics)
    return f"Điều {article_number} quy định:\n{bullet_lines}"


def _unique_ordered(values: list[str]) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for value in values:
        clean = normalize_text_for_chunking(value).strip(" -;.")
        if not clean:
            continue
        key = clean.casefold()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(clean)
    return ordered


def _section_chunks(
    text: str,
    *,
    base_metadata: dict[str, Any],
    max_chars: int,
    overlap_chars: int,
) -> list[EvidenceChunk]:
    headings = list(HEADING_RE.finditer(text))
    if not headings:
        return []
    chunks: list[EvidenceChunk] = []
    if headings[0].start() > 0:
        chunks.extend(
            _bounded_chunks(
                text[: headings[0].start()].strip(),
                base_metadata={
                    **base_metadata,
                    "chunk_type": "document_preamble",
                    "chunk_strategy": "structure_aware",
                    "section_path": ["preamble"],
                    "source_span": {"start": 0, "end": headings[0].start()},
                },
                max_chars=max_chars,
                overlap_chars=overlap_chars,
            )
        )
    for index, match in enumerate(headings):
        start = match.start()
        end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
        section_title = normalize_text_for_chunking(f"{match.group('label')} {match.group('title')}")
        chunks.extend(
            _bounded_chunks(
                text[start:end].strip(),
                base_metadata={
                    **base_metadata,
                    "chunk_type": "document_section",
                    "chunk_strategy": "structure_aware",
                    "section_title": section_title,
                    "section_path": [section_title],
                    "source_span": {"start": start, "end": end},
                },
                max_chars=max_chars,
                overlap_chars=overlap_chars,
            )
        )
    return chunks


def _recursive_chunks(
    text: str,
    *,
    base_metadata: dict[str, Any],
    max_chars: int,
    overlap_chars: int,
) -> list[EvidenceChunk]:
    return _bounded_chunks(text, base_metadata=base_metadata, max_chars=max_chars, overlap_chars=overlap_chars)


def _bounded_chunks(
    text: str,
    *,
    base_metadata: dict[str, Any],
    max_chars: int,
    overlap_chars: int,
) -> list[EvidenceChunk]:
    clean = normalize_text_for_chunking(text)
    if not clean:
        return []
    context = standard_document_context(base_metadata)
    section_title = base_metadata.get("section_title")
    prefix = [*context]
    if section_title and f"Mục: {section_title}" not in prefix:
        prefix.append(f"Mục: {section_title}")
    body_budget = max(500, max_chars - len("\n".join(prefix)) - 2)
    raw_parts = _split_by_boundaries(clean, max_chars=body_budget, overlap_chars=overlap_chars)
    chunks: list[EvidenceChunk] = []
    for sub_index, part in enumerate(raw_parts):
        content = "\n".join([*prefix, part]).strip()
        metadata = dict(base_metadata)
        metadata["subchunk_index"] = sub_index
        metadata["subchunk_total"] = len(raw_parts)
        if len(raw_parts) > 1:
            metadata["chunk_strategy"] = f"{metadata.get('chunk_strategy', 'adaptive')}_split"
        chunks.append(EvidenceChunk(content=content, metadata=metadata))
    return chunks


def _split_by_boundaries(text: str, *, max_chars: int, overlap_chars: int) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    parts: list[str] = []
    start = 0
    while start < len(text):
        target = min(start + max_chars, len(text))
        end = _best_boundary(text, start, target)
        if end <= start:
            end = target
        part = text[start:end].strip()
        if part:
            parts.append(part)
        if end >= len(text):
            break
        start = max(end - overlap_chars, start + 1)
    return parts


def _best_boundary(text: str, start: int, target: int) -> int:
    window = text[start:target]
    minimum = max(1, int(len(window) * 0.55))
    for separator in ("\n\n", "\n", ". ", "; ", ", ", " "):
        index = window.rfind(separator)
        if index >= minimum:
            end = start + index + len(separator)
            if separator in {". ", "; "} or SENTENCE_END_RE.search(text[start:end]):
                return end
            return end
    return target


def _last_match_before(pattern: re.Pattern[str], text: str, position: int) -> re.Match[str] | None:
    latest: re.Match[str] | None = None
    for match in pattern.finditer(text[:position]):
        latest = match
    return latest


def _legal_section_title(
    chapter: re.Match[str] | None,
    section: re.Match[str] | None,
    article: re.Match[str],
) -> str:
    parts: list[str] = []
    if chapter:
        parts.append(f"Chương {chapter.group('number')} {chapter.group('title')}".strip())
    if section:
        parts.append(f"Mục {section.group('number')} {section.group('title')}".strip())
    parts.append(f"Điều {article.group('number')} {article.group('title')}".strip())
    return " > ".join(part for part in parts if part)


def _legal_path(
    chapter: re.Match[str] | None,
    section: re.Match[str] | None,
    article: re.Match[str],
) -> list[str]:
    path: list[str] = []
    if chapter:
        path.append(f"Chương {chapter.group('number')}")
    if section:
        path.append(f"Mục {section.group('number')}")
    path.append(f"Điều {article.group('number')}")
    return path
