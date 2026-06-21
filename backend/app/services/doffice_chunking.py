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
    "source_type",
    "ten_file",
    "duong_dan",
    "chunk_type",
    "section_title",
    "table_name",
    "table_index",
    "row_index",
    "row_number",
    "row_count",
    "column_count",
    "columns",
    "group_name",
    "features",
    "platform",
    "feature_name",
    "screen_name",
    "change_content",
    "phase",
    "is_table_row",
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
        content = _element_content(element)
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
                "source_summary": chunk_type == "document_summary",
                "is_table_row": chunk_type == "table_row",
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


def _element_content(element: NormalizedElement) -> str:
    chunk_type = str(element.metadata.get("chunk_type") or element.element_type)
    if chunk_type == "footer_signature":
        return element.text.strip()
    lines = []
    if chunk_type not in {"document_header", "document_summary"}:
        document_code = element.metadata.get("document_code") or element.metadata.get("ky_hieu")
        title = element.metadata.get("trich_yeu")
        if document_code or title:
            lines.append(f"Văn bản: {document_code or ''} - {title or ''}".strip(" -"))
    lines.append(element.text.strip())
    return "\n".join(line for line in lines if line.strip())


def _structure_path(element: NormalizedElement) -> list[str]:
    path = [str(element.metadata.get("chunk_type") or element.element_type)]
    for key in ("platform", "phase", "feature_name"):
        value = element.metadata.get(key)
        if value:
            path.append(str(value))
    return path
