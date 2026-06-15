from __future__ import annotations

from typing import Any


def build_staff_area_records_from_page_texts(
    *,
    page_texts: dict[int, str],
    max_tokens: int,
) -> list[dict[str, Any]]:
    """Create semantic staff-area rows from raw page text.

    This is schema-aware, not person-name-specific. It only emits records created by
    table_aware_chunk_text for tables headed by STT / Mảng công nghệ / Phòng chủ trì /
    Nhân sự đề xuất.
    """

    if not page_texts:
        return []

    from app.services.table_aware_chunking import table_aware_chunk_text

    combined_parts = [f"[Trang {page}]\n{text}" for page, text in sorted(page_texts.items())]
    combined_text = "\n\n".join(combined_parts)
    chunks, _entity_index = table_aware_chunk_text(
        combined_text,
        chunk_size=max_tokens,
        chunk_overlap=0,
    )

    records: list[dict[str, Any]] = []
    seen_keys: set[tuple[str, str, str]] = set()
    for chunk in chunks:
        metadata = dict(chunk.get("metadata") or {})
        if metadata.get("relationship_type") != "technology_area_staff":
            continue
        chunk_type = str(metadata.get("chunk_type") or "")
        if chunk_type not in {"table_row", "entity_profile"}:
            continue

        key = (
            chunk_type,
            str(metadata.get("stt") or metadata.get("person_name") or ""),
            str(metadata.get("area") or metadata.get("person_name") or ""),
        )
        if key in seen_keys:
            continue
        seen_keys.add(key)

        content = str(chunk.get("content") or "").strip()
        if not content:
            continue

        pages = metadata.get("page_numbers") or metadata.get("page_number") or []
        if isinstance(pages, int):
            pages = [pages]

        record: dict[str, Any] = {
            **metadata,
            "chunk_id": f"staff_area_semantic_{len(records):04d}",
            "chunk_type": chunk_type,
            "content_format": "text",
            "document_type": "staff_area_table_document",
            "chunk_strategy": "table_relationship_v1",
            "segment_chunk_strategy": "table_relationship_v1",
            "text": content,
            "content": content,
            "contextualized_text": content,
            "raw_text": str(metadata.get("raw_text") or content),
            "source_raw_text": str(metadata.get("raw_text_original") or content),
            "headings": [str(metadata.get("source_table") or "Danh sách nhân sự")],
            "section_path": [str(metadata.get("source_table") or "Danh sách nhân sự")],
            "pages": [int(page) for page in pages if str(page).isdigit()],
            "quality_status": "pass",
            "indexable": True,
            "embedding_enabled": True,
        }
        records.append(record)

    return records
