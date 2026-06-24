from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

ARTICLE_RE = re.compile(r"(?im)^\s*(?:\*\*)?Điều\s+(\d+)\s*[\.\:\-]?\s*(.*?)(?:\*\*)?\s*$")
CHAPTER_RE = re.compile(r"(?im)^\s*(?:\*\*)?CHƯƠNG\s+([IVXLCDM]+|\d+)\s*[\.\:\-]?\s*(.*?)(?:\*\*)?\s*$")
TOKEN_RE = re.compile(r"\w+|[^\w\s]+", flags=re.UNICODE)
LEGAL_KEYWORDS = (
    "thỏa ước lao động tập thể",
    "quy chế",
    "quy định",
    "nghị định",
    "thông tư",
    "quyết định",
    "bộ luật lao động",
)


@dataclass(frozen=True)
class LegalArticle:
    article_number: str
    article_title: str
    chapter_number: str | None
    chapter_title: str | None
    start: int
    end: int
    text: str


def normalize_space(value: str) -> str:
    return " ".join(str(value or "").split()).strip()


def count_tokens(text: str) -> int:
    return len(TOKEN_RE.findall(text or ""))


def text_from_page_texts(page_texts: dict[int, str]) -> str:
    return "\n\n".join(
        str(text or "").strip()
        for _page, text in sorted(page_texts.items())
        if str(text or "").strip()
    ).strip()


def is_legal_article_document(text: str, *, source_file: str | None = None) -> bool:
    """Detect Vietnamese administrative/legal documents with chapter/article layout."""

    normalized = normalize_space(text).casefold()
    if not normalized:
        return False
    article_count = len(list(ARTICLE_RE.finditer(text)))
    chapter_count = len(list(CHAPTER_RE.finditer(text)))
    keyword_hit = any(keyword in normalized for keyword in LEGAL_KEYWORDS)
    filename_hit = any(
        keyword in normalize_space(source_file or "").casefold()
        for keyword in ("tuldtt", "thỏa ước", "quy chế", "quy định")
    )
    return article_count >= 4 and (chapter_count >= 1 or keyword_hit or filename_hit)


def _chapter_catalog(text: str) -> list[dict[str, Any]]:
    lines = text.splitlines(keepends=True)
    positions: list[tuple[int, str]] = []
    cursor = 0
    for line in lines:
        positions.append((cursor, line))
        cursor += len(line)

    chapters: list[dict[str, Any]] = []
    for idx, (start, line) in enumerate(positions):
        match = CHAPTER_RE.match(line.strip())
        if not match:
            continue
        chapter_no = normalize_space(match.group(1))
        inline_title = normalize_space(match.group(2)).strip("* ")
        title = inline_title
        if not title:
            for _next_start, next_line in positions[idx + 1 : idx + 4]:
                clean = normalize_space(next_line).strip("* ")
                if not clean:
                    continue
                if ARTICLE_RE.match(clean) or CHAPTER_RE.match(clean):
                    break
                # Chapter title lines in Vietnamese legal documents are usually uppercase.
                title = clean
                break
        chapters.append(
            {
                "start": start,
                "number": chapter_no,
                "title": title or f"CHƯƠNG {chapter_no}",
                "label": f"CHƯƠNG {chapter_no}" + (f" - {title}" if title else ""),
            }
        )
    return chapters


def _chapter_for_position(chapters: list[dict[str, Any]], position: int) -> dict[str, Any] | None:
    current: dict[str, Any] | None = None
    for chapter in chapters:
        if int(chapter["start"]) <= position:
            current = chapter
        else:
            break
    return current


def _next_chapter_start_after(
    chapters: list[dict[str, Any]],
    *,
    position: int,
    before: int,
) -> int | None:
    for chapter in chapters:
        start = int(chapter["start"])
        if position < start < before:
            return start
    return None


def _strip_markup(value: str) -> str:
    value = re.sub(r"\*+", "", str(value or ""))
    value = normalize_space(value)
    # Docling sometimes emits headings like `Điều 18** . Nội dung...`, so the
    # captured title starts with a stray dot. Normalize this once at the title
    # boundary instead of leaking `Điều 18. . ...` into chunks and metadata.
    value = re.sub(r"^[\s\.\:\-–—]+", "", value)
    return normalize_space(value)


def _clean_legal_text(text: str) -> str:
    # Docling can split ordered-list markers such as `10 **.** ATVSLĐ` in DOCX
    # outputs. Repair only the marker shape, without altering document meaning.
    text = re.sub(r"(?m)^(\s*\d+)\s*\*\*\.\*\*\s*", r"\1. ", text)
    text = re.sub(r"(?m)^(\s*\d+)\s+\.\s+", r"\1. ", text)
    return text.strip()


def _extract_leave_days(value: str) -> int | None:
    """Extract day count from Vietnamese leave-benefit text."""

    text = normalize_space(value).casefold()
    if not text:
        return None
    match = re.search(r"nghỉ\s+(\d+)\s+ngày", text)
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def _combined_leave_benefit_text(
    *,
    labor_code_benefit: str,
    collective_benefit: str,
) -> tuple[str | None, int | None, str | None]:
    """Build one row-level entitlement from base and additional columns.

    This function is intentionally column-driven. If a table row has one base
    entitlement column and one additional entitlement column, the row-level fact
    is the readable total plus the original column values. It does not depend on
    a specific article number, organization, event, or file name.
    """

    base_text = normalize_space(labor_code_benefit)
    additional_text = normalize_space(collective_benefit)
    base_days = _extract_leave_days(base_text)
    additional_days = _extract_leave_days(additional_text)

    if base_days is not None and additional_days is not None:
        total_days = base_days + additional_days
        total_text = (
            f"Tổng quyền lợi theo dòng bảng: Nghỉ {total_days:02d} ngày "
            f"hưởng nguyên lương ({base_days:02d} ngày theo quy định nền + "
            f"{additional_days:02d} ngày theo quy định bổ sung)"
        )
        return total_text, total_days, "base_plus_additional"

    if base_days is not None:
        total_text = (
            f"Tổng quyền lợi theo dòng bảng: Nghỉ {base_days:02d} ngày "
            "hưởng nguyên lương (theo quy định nền được nêu trong bảng)"
        )
        return total_text, base_days, "base_column"

    if additional_days is not None:
        total_text = (
            f"Tổng quyền lợi theo dòng bảng: Nghỉ {additional_days:02d} ngày "
            "hưởng nguyên lương (theo quy định bổ sung được nêu trong bảng)"
        )
        return total_text, additional_days, "additional_column"

    if additional_text:
        return f"Quyền lợi theo dòng bảng: {additional_text}", None, "additional_column"
    if base_text:
        return f"Quyền lợi theo dòng bảng: {base_text}", None, "base_column"
    return None, None, None


def _remove_duplicate_article_heading(body: str, article: LegalArticle) -> str:
    lines = body.splitlines()
    if not lines:
        return body.strip()

    # Keep the first heading from Docling, but drop repeated copies that often appear
    # immediately after a plain-text bookmark/TOC line. This prevents chunks like:
    # `Điều 7...` followed by `**Điều 7...**` in the same article.
    article_heading_re = re.compile(
        rf"^\s*(?:\*\*)?Điều\s+{re.escape(article.article_number)}\s*[\.\:\-]?",
        flags=re.I,
    )
    seen_heading = False
    output: list[str] = []
    for line in lines:
        clean = line.strip()
        is_article_heading = article_heading_re.match(clean) is not None
        if is_article_heading:
            if seen_heading:
                continue
            seen_heading = True
        output.append(line)
    return "\n".join(output).strip()


def _filtered_article_matches(text: str) -> list[re.Match[str]]:
    raw_matches = list(ARTICLE_RE.finditer(text))
    filtered: list[re.Match[str]] = []
    index = 0
    while index < len(raw_matches):
        current = raw_matches[index]
        next_match = raw_matches[index + 1] if index + 1 < len(raw_matches) else None
        if next_match and normalize_space(current.group(1)) == normalize_space(next_match.group(1)):
            between = normalize_space(text[current.end() : next_match.start()])
            if not between:
                # DOCX extraction can emit both a plain and a bold copy of the same
                # article heading. Prefer the later heading because the body follows it.
                index += 1
                continue
        filtered.append(current)
        index += 1
    return filtered


def extract_legal_articles(text: str) -> list[LegalArticle]:
    article_matches = _filtered_article_matches(text)
    if not article_matches:
        return []
    raw_article_matches = list(ARTICLE_RE.finditer(text))
    chapters = _chapter_catalog(text)
    articles: list[LegalArticle] = []
    for match in article_matches:
        start = match.start()
        next_raw_starts = [raw.start() for raw in raw_article_matches if raw.start() > start]
        next_article_start = next_raw_starts[0] if next_raw_starts else len(text)
        next_chapter_start = _next_chapter_start_after(
            chapters,
            position=start,
            before=next_article_start,
        )
        end = next_chapter_start or next_article_start

        chapter = _chapter_for_position(chapters, start)
        title = _strip_markup(match.group(2))
        draft = _clean_legal_text(text[start:end])
        article_number = normalize_space(match.group(1))
        draft_article = LegalArticle(
            article_number=article_number,
            article_title=title,
            chapter_number=str(chapter.get("number")) if chapter else None,
            chapter_title=str(chapter.get("title")) if chapter else None,
            start=start,
            end=end,
            text=draft,
        )
        article_text = _remove_duplicate_article_heading(draft, draft_article)
        if not article_text:
            continue
        articles.append(
            LegalArticle(
                article_number=article_number,
                article_title=title,
                chapter_number=str(chapter.get("number")) if chapter else None,
                chapter_title=str(chapter.get("title")) if chapter else None,
                start=start,
                end=end,
                text=article_text,
            )
        )
    return articles


def _record_base(
    *,
    chunk_id: str,
    text: str,
    article: LegalArticle,
    chunk_type: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    chapter_label = None
    if article.chapter_number:
        chapter_label = f"CHƯƠNG {article.chapter_number}"
        if article.chapter_title:
            chapter_label += f" - {article.chapter_title}"
    article_label = f"Điều {article.article_number}"
    if article.article_title:
        article_label += f". {article.article_title}"
    section_path = [value for value in (chapter_label, article_label) if value]
    clean_text = _clean_legal_text(text)
    record: dict[str, Any] = {
        "chunk_id": chunk_id,
        "chunk_type": chunk_type,
        "content_format": "legal_article_text",
        "document_type": "legal_article_document",
        "chunk_strategy": "legal_article_v1",
        "segment_chunk_strategy": "legal_article_v1",
        "text": clean_text,
        "content": clean_text,
        "contextualized_text": clean_text,
        "raw_text": clean_text,
        "source_raw_text": clean_text,
        "headings": section_path,
        "section_path": section_path,
        "chapter_number": article.chapter_number,
        "chapter_title": article.chapter_title,
        "article_number": article.article_number,
        "article_title": article.article_title,
        "quality_status": "pass",
        "indexable": True,
        "embedding_enabled": True,
    }
    if extra:
        record.update(extra)
    return record


def _split_article_text(article: LegalArticle, *, max_tokens: int) -> list[str]:
    """Split very long articles on clause boundaries while preserving article metadata."""

    prefix: list[str] = []
    if article.chapter_number:
        chapter = f"CHƯƠNG {article.chapter_number}"
        if article.chapter_title:
            chapter += f"\n{article.chapter_title}"
        prefix.append(chapter)
    article_heading = f"Điều {article.article_number}. {_strip_markup(article.article_title)}".strip()

    body = article.text.strip()
    # The article text already starts with the heading; avoid duplicating it in small articles.
    if count_tokens("\n".join(prefix + [body])) <= max_tokens:
        return ["\n".join(prefix + [body]).strip()]

    lines = [line.rstrip() for line in body.splitlines()]
    while lines and ARTICLE_RE.match(lines[0].strip()):
        lines.pop(0)
    while lines and not lines[0].strip():
        lines.pop(0)
    blocks: list[str] = []
    current: list[str] = []
    clause_re = re.compile(r"^\s*(?:\d+\.|[a-zđ]\))\s+")
    for line in lines:
        clean = line.strip()
        if not clean:
            if current:
                current.append("")
            continue
        is_heading = ARTICLE_RE.match(clean) is not None
        if current and clause_re.match(clean) and not is_heading:
            blocks.append("\n".join(current).strip())
            current = [clean]
        else:
            current.append(clean)
    if current:
        blocks.append("\n".join(current).strip())

    parts: list[str] = []
    current_blocks: list[str] = []
    header = "\n".join(prefix + [article_heading]).strip()
    for block in blocks:
        candidate = "\n\n".join([header, *current_blocks, block]).strip()
        if current_blocks and count_tokens(candidate) > max_tokens:
            parts.append("\n\n".join([header, *current_blocks]).strip())
            current_blocks = [block]
        else:
            current_blocks.append(block)
    if current_blocks:
        parts.append("\n\n".join([header, *current_blocks]).strip())
    return [part for part in parts if part.strip()] or ["\n".join(prefix + [body]).strip()]


def build_legal_article_records(
    text: str,
    *,
    source_file: str | None = None,
    max_tokens: int = 350,
) -> list[dict[str, Any]]:
    if not is_legal_article_document(text, source_file=source_file):
        return []
    records: list[dict[str, Any]] = []
    for article in extract_legal_articles(text):
        parts = _split_article_text(article, max_tokens=max_tokens)
        for part_index, part in enumerate(parts, start=1):
            suffix = f"_{part_index:02d}" if len(parts) > 1 else ""
            records.append(
                _record_base(
                    chunk_id=f"legal_article_{int(article.article_number):03d}{suffix}",
                    text=part,
                    article=article,
                    chunk_type="legal_article",
                    extra={
                        "subchunk_index": part_index if len(parts) > 1 else None,
                        "subchunk_total": len(parts) if len(parts) > 1 else None,
                        # Explicit article part metadata makes split legal articles easy
                        # to debug and lets retrieval expand all sibling parts of an
                        # article when one part is retrieved. Keep these fields only for
                        # split articles to avoid noisy metadata on short articles.
                        "article_part": part_index if len(parts) > 1 else None,
                        "article_part_total": len(parts) if len(parts) > 1 else None,
                    },
                )
            )
        records.extend(_build_legal_table_row_records(article))
    return [record for record in records if normalize_space(str(record.get("contextualized_text") or ""))]


def _build_legal_table_row_records(article: LegalArticle) -> list[dict[str, Any]]:
    """Create row-level chunks for simple legal tables embedded in an article.

    This targets simple article-level fact tables that Docling/PDF text
    extraction often renders as one cell per line. Detection is based on the
    table structure and row content, not on a fixed article number, event, or
    source file.
    """

    if not re.search(r"nghỉ\s+việc\s+riêng|hưởng\s+lương", article.text, flags=re.I):
        return []
    lines = [normalize_space(line).strip("* ") for line in article.text.splitlines()]
    rows: list[tuple[str, dict[str, str]]] = []

    # Preferred representation: one table row per line, with pipe-separated cells.
    for line in lines:
        cells = [normalize_space(cell) for cell in line.strip("|").split("|")]
        if len(cells) >= 3 and re.fullmatch(r"[a-zđ]", cells[0], flags=re.I):
            rows.append(
                (
                    cells[0].lower(),
                    {
                        "description": cells[1] if len(cells) > 1 else "",
                        "labor_code_benefit": cells[2] if len(cells) > 2 else "",
                        "collective_agreement_benefit": cells[3] if len(cells) > 3 else "",
                    },
                )
            )

    # Fallback representation: table cells rendered one per line.
    if not rows:
        current_code: str | None = None
        current_lines: list[str] = []
        row_code_re = re.compile(r"^[a-zđ]$", flags=re.I)
        for line in lines:
            if not line:
                continue
            if row_code_re.match(line):
                if current_code and current_lines:
                    rows.append((current_code, {"description": " ".join(current_lines)}))
                current_code = line.lower()
                current_lines = []
                continue
            if current_code:
                # Stop at article prose after the table.
                if re.match(r"^\d+\.\s+", line) and rows:
                    break
                current_lines.append(line)
        if current_code and current_lines:
            rows.append((current_code, {"description": " ".join(current_lines)}))

    records: list[dict[str, Any]] = []
    for code, row in rows:
        description = normalize_space(row.get("description", ""))
        labor_code_benefit = normalize_space(row.get("labor_code_benefit", ""))
        collective_benefit = normalize_space(row.get("collective_agreement_benefit", ""))
        total_benefit, total_days, benefit_source = _combined_leave_benefit_text(
            labor_code_benefit=labor_code_benefit,
            collective_benefit=collective_benefit,
        )
        row_text_parts = [f"Trường hợp: {description}" if description else ""]
        if total_benefit:
            row_text_parts.append(total_benefit)
        if labor_code_benefit:
            row_text_parts.append(f"Quy định nền: {labor_code_benefit}")
        if collective_benefit:
            row_text_parts.append(f"Quy định bổ sung: {collective_benefit}")
        row_text = "; ".join(part for part in row_text_parts if part).strip()
        if not row_text:
            continue
        content = (
            f"Điều {article.article_number}. {article.article_title}\n"
            f"Bảng quyền lợi nghỉ việc riêng - dòng {code}: {row_text}"
        )
        records.append(
            _record_base(
                chunk_id=f"legal_table_row_{int(article.article_number):03d}_{code}",
                text=content,
                article=article,
                chunk_type="legal_table_row",
                extra={
                    "content_format": "legal_table_row_text",
                    "case_code": code,
                    "case_name": description or None,
                    "labor_code_benefit": labor_code_benefit or None,
                    "collective_agreement_benefit": collective_benefit or None,
                    "base_benefit": labor_code_benefit or None,
                    "additional_benefit": collective_benefit or None,
                    "total_leave_benefit": total_benefit,
                    "total_benefit": total_benefit,
                    "total_leave_days": total_days,
                    "total_days": total_days,
                    "benefit_source": benefit_source,
                    "row_text": row_text,
                    "table_name": f"Điều {article.article_number}. {article.article_title}",
                    "relationship_type": "structured_fact_row",
                    "legacy_relationship_type": "legal_leave_benefit",
                    "chunk_strategy": "legal_table_row_v1",
                    "segment_chunk_strategy": "legal_table_row_v1",
                },
            )
        )
    return records
