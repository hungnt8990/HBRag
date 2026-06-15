from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.services.chunkers.catalog_table_chunker import (
    build_catalog_table_records_from_page_texts,
)
from app.services.chunkers.legal_article_chunker import (
    build_legal_article_records,
    is_legal_article_document,
    text_from_page_texts,
)
from app.services.chunkers.table_relationship_chunker import (
    build_staff_area_records_from_page_texts,
)


@dataclass(frozen=True)
class DoclingChunkRoute:
    records: list[dict[str, Any]]
    primary_strategy: str
    document_profile: str
    used_generic_docling: bool
    supplemental_strategies: list[str]


def _non_empty_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for record in records:
        text = str(record.get("contextualized_text") or record.get("text") or "").strip()
        if not text:
            continue
        copied = dict(record)
        copied.setdefault("chunk_strategy", "generic_docling_v1")
        copied.setdefault("segment_chunk_strategy", "generic_docling_v1")
        copied.setdefault("document_type", "generic_document")
        output.append(copied)
    return output


def route_docling_chunks(
    *,
    generic_records: list[dict[str, Any]],
    page_texts: dict[int, str],
    source_file: str,
    max_tokens: int,
    parsed_text: str | None = None,
) -> DoclingChunkRoute:
    """Route parsed Docling output to a specialized chunking strategy.

    Docling is treated as parser/extractor. This router decides whether the parsed
    content should be chunked by legal articles, table relationships, or generic
    Docling records.
    """

    full_text = text_from_page_texts(page_texts)
    if not full_text:
        full_text = str(parsed_text or "").strip()
    if not full_text:
        full_text = "\n\n".join(
            str(record.get("contextualized_text") or record.get("text") or "")
            for record in generic_records
        )

    if is_legal_article_document(full_text, source_file=source_file):
        legal_records = build_legal_article_records(
            full_text,
            source_file=source_file,
            max_tokens=max_tokens,
        )
        if legal_records:
            supplemental = sorted(
                {
                    str(record.get("chunk_strategy"))
                    for record in legal_records
                    if str(record.get("chunk_strategy")) != "legal_article_v1"
                }
            )
            return DoclingChunkRoute(
                records=legal_records,
                primary_strategy="legal_article_v1",
                document_profile="legal_article_document",
                used_generic_docling=False,
                supplemental_strategies=supplemental,
            )

    records = _non_empty_records(generic_records)
    supplemental: list[str] = []

    catalog_records = build_catalog_table_records_from_page_texts(
        page_texts=page_texts,
        source_file=source_file,
        max_tokens=max_tokens,
    )
    if catalog_records:
        records.extend(catalog_records)
        supplemental.append("catalog_table_v1")

    staff_area_records = build_staff_area_records_from_page_texts(
        page_texts=page_texts,
        max_tokens=max_tokens,
    )
    if staff_area_records:
        records.extend(staff_area_records)
        supplemental.append("table_relationship_v1")

    primary = "generic_docling_v1"
    profile = "mixed_administrative_technical"
    if supplemental:
        primary = "generic_docling_with_supplemental_v1"
        profile = "mixed_administrative_technical_with_relationships"
        if "catalog_table_v1" in supplemental and len(supplemental) == 1:
            primary = "generic_docling_with_catalog_v1"
            profile = "catalog_table"

    return DoclingChunkRoute(
        records=records,
        primary_strategy=primary,
        document_profile=profile,
        used_generic_docling=True,
        supplemental_strategies=supplemental,
    )
