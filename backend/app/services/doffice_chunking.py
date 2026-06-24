from __future__ import annotations

from app.repositories.documents import ChunkCreate
from app.services.doffice_content_normalizer import NormalizedDofficeDocument, NormalizedElement

CHUNK_METADATA_ALLOWLIST = {
    "id_vb",
    "document_code",
    "ky_hieu",
    "trich_yeu",
    "issued_date",
    "ngay_vb",
    "issuer",
    "noi_ban_hanh",
    "nguoi_ky",
    "signer",
    "source_type",
    "ten_file",
    "duong_dan",
    "chunk_type",
    "section_title",
    "table_context",
    "table_name",
    "table_index",
    "source_format",
    "heading_path",
    "section_index",
    "section_part",
    "column_name",
    "column_index",
    "column_value_count",
    "column_context_headers",
    "row_index",
    "row_start",
    "row_end",
    "row_number",
    "row_data",
    "row_count",
    "column_count",
    "columns",
    "group_name",
    "features",
    "platform",
    "feature_name",
    "field_name",
    "screen_name",
    "change_content",
    "phase",
    "is_table_row",
    "is_table_column",
    "is_footer_or_signature",
    "indexable",
    "embedding_enabled",
    "retrieval_priority",
    "source_summary",
    "content_hash",
    "structure_path",
    "doc_codes",
    "identifiers",
}


def build_doffice_chunks(normalized: NormalizedDofficeDocument) -> list[ChunkCreate]:
    chunks: list[ChunkCreate] = []
    for element in normalized.elements:
        content = _element_content(element, normalized)
        if not content.strip():
            continue
        chunk_type = str(element.metadata.get("chunk_type") or element.element_type)
        metadata = _compact_chunk_metadata(
            {
                **_document_chunk_metadata(normalized),
                **element.metadata,
                "source_type": "doffice_elasticsearch",
                "chunk_type": chunk_type,
                "document_code": normalized.document_code,
                "doc_code": normalized.document_code,
                "ky_hieu": normalized.document_code,
                "issued_date": normalized.issued_date,
                "issuer": normalized.issuer,
                "noi_ban_hanh": normalized.issuer,
                "nguoi_ky": normalized.signer,
                "signer": normalized.signer,
                "source_summary": chunk_type == "document_summary",
                "is_table_row": chunk_type == "table_row",
                "is_table_column": chunk_type == "table_column",
                "is_footer_or_signature": chunk_type == "footer_signature",
                "indexable": bool(element.metadata.get("indexable", chunk_type != "footer_signature")),
                "embedding_enabled": bool(element.metadata.get("embedding_enabled", element.metadata.get("indexable", chunk_type != "footer_signature"))),
                "content_hash": normalized.content_hash,
                "structure_path": _structure_path(element),
            }
        )
        chunks.append(
            ChunkCreate(
                chunk_index=len(chunks),
                content=content,
                metadata=metadata,
            )
        )
    return chunks

def _document_chunk_metadata(normalized: NormalizedDofficeDocument) -> dict[str, object]:
    return {
        key: normalized.metadata.get(key)
        for key in (
            "id_vb",
            "document_code",
            "ky_hieu",
            "trich_yeu",
            "issued_date",
            "ngay_vb",
            "issuer",
            "noi_ban_hanh",
            "nguoi_ky",
            "signer",
            "source_type",
            "ten_file",
            "duong_dan",
            "doc_codes",
            "identifiers",
        )
    }

def _compact_chunk_metadata(metadata: dict[str, object]) -> dict[str, object]:
    return {
        key: value
        for key, value in metadata.items()
        if key in CHUNK_METADATA_ALLOWLIST and value not in (None, "", [])
    }


def _element_content(element: NormalizedElement, normalized: NormalizedDofficeDocument) -> str:
    chunk_type = str(element.metadata.get("chunk_type") or element.element_type)
    if chunk_type == "footer_signature":
        return element.text.strip()
    lines = []
    if chunk_type in {"document_body", "document_summary"}:
        lines.extend(_document_text_preamble(element, normalized))
    elif chunk_type in {"table_parent", "table_row", "table_group", "table_column"}:
        lines.extend(_table_text_preamble(element, normalized))
    lines.append(element.text.strip())
    return "\n".join(line for line in lines if line.strip())


def _document_text_preamble(element: NormalizedElement, normalized: NormalizedDofficeDocument) -> list[str]:
    document_code = element.metadata.get("document_code") or element.metadata.get("ky_hieu") or normalized.document_code
    title = element.metadata.get("trich_yeu") or normalized.title
    issued_date = element.metadata.get("issued_date") or element.metadata.get("ngay_vb") or normalized.issued_date
    issuer = element.metadata.get("issuer") or element.metadata.get("noi_ban_hanh") or normalized.issuer
    lines: list[str] = []
    document_label = " - ".join(str(value) for value in (document_code, title) if value)
    if document_label:
        lines.append(f"Văn bản: {document_label}")
    if issued_date:
        lines.append(f"Ngày ban hành: {issued_date}")
    if issuer:
        lines.append(f"Cơ quan ban hành: {issuer}")
    return lines


def _table_text_preamble(element: NormalizedElement, normalized: NormalizedDofficeDocument) -> list[str]:
    document_code = element.metadata.get("document_code") or element.metadata.get("ky_hieu") or normalized.document_code
    title = element.metadata.get("trich_yeu") or normalized.title
    section_title = element.metadata.get("section_title")
    table_name = element.metadata.get("table_name")
    row_number = element.metadata.get("row_number") or element.metadata.get("row_index")
    lines: list[str] = []
    document_label = " - ".join(str(value) for value in (document_code, title) if value)
    if document_label:
        lines.append(f"Văn bản: {document_label}")
    appendix_label = section_title or table_name
    if appendix_label:
        lines.append(f"Phụ lục/Bảng: {appendix_label}")
    if table_name and table_name != appendix_label:
        lines.append(f"Bảng: {table_name}")
    if row_number and str(element.metadata.get("chunk_type") or element.element_type) == "table_row":
        lines.append(f"STT: {row_number}")
    return lines


def _structure_path(element: NormalizedElement) -> list[str]:
    path = [str(element.metadata.get("chunk_type") or element.element_type)]
    heading_path = element.metadata.get("heading_path")
    if isinstance(heading_path, list):
        path.extend(str(value) for value in heading_path if value)
    for key in ("section_title", "platform", "phase", "feature_name", "column_name"):
        value = element.metadata.get(key)
        if value:
            path.append(str(value))
    return path
