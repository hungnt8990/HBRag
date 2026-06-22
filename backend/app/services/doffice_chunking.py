from __future__ import annotations

import re
import unicodedata

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
    "source_layer",
    "doc_code",
    "doc_number",
    "document_type",
    "business_domain",
    "person_name",
    "person_names",
    "department",
    "department_names",
    "assigned_unit",
    "assigned_units",
    "task",
    "task_owner",
    "cooperating_unit",
    "deadline",
    "deadline_text",
    "article_no",
    "clause_no",
    "point_no",
    "case_name",
    "condition",
    "days",
    "relationship_type",
    "table_id",
    "stt",
    "assignment_area",
    "implementation_scope",
    "project_name",
    "project_names",
    "system_name",
    "system_names",
    "software_system",
    "evn_unit",
    "power_company",
    "recipient_units",
    "evidence_chunk_ids",
    "parent_chunk_type",
}

DIRECTIVE_PATTERN = re.compile(
    r"\b(?:đề nghị|de nghi|yêu cầu|yeu cau|giao|báo cáo|bao cao|thực hiện|thuc hien|"
    r"triển khai|trien khai|hoàn thành|hoan thanh|gửi về|gui ve|trước ngày|truoc ngay)\b",
    flags=re.IGNORECASE,
)
DATE_DEADLINE_PATTERN = re.compile(
    r"(?:trước ngày|truoc ngay|hoàn thành trước ngày|hoan thanh truoc ngay|"
    r"gửi[^.\n]{0,80}trước ngày|gui[^.\n]{0,80}truoc ngay)\s*([0-3]?\d[/-][01]?\d[/-](?:\d{2}|\d{4}))",
    flags=re.IGNORECASE,
)


def build_doffice_chunks(normalized: NormalizedDofficeDocument) -> list[ChunkCreate]:
    chunks: list[ChunkCreate] = []
    for element in normalized.elements:
        content = _element_content(element)
        if not content.strip():
            continue
        base_chunk_type = str(element.metadata.get("chunk_type") or element.element_type)
        for piece_content, chunk_type, extra_metadata in _structured_subchunks(
            content=content,
            metadata=element.metadata,
            base_chunk_type=base_chunk_type,
        ):
            metadata = _compact_chunk_metadata(
                {
                    **_document_chunk_metadata(normalized),
                    **element.metadata,
                    **extra_metadata,
                    "source_type": "doffice_elasticsearch",
                    "source_layer": "chunk",
                    "chunk_type": chunk_type,
                    "document_code": normalized.document_code,
                    "doc_code": normalized.document_code,
                    "ky_hieu": normalized.document_code,
                    "issued_date": normalized.issued_date,
                    "issuer": normalized.issuer,
                    "issuing_org": normalized.issuer,
                    "noi_ban_hanh": normalized.issuer,
                    "source_summary": chunk_type == "summary_block",
                    "is_table_row": chunk_type == "table_row",
                    "is_footer_or_signature": chunk_type == "signature_block",
                    "indexable": bool(element.metadata.get("indexable", chunk_type != "signature_block")),
                    "embedding_enabled": bool(element.metadata.get("embedding_enabled", element.metadata.get("indexable", chunk_type != "signature_block"))),
                    "content_hash": normalized.content_hash,
                    "structure_path": _structure_path(element),
                }
            )
            chunks.append(
                ChunkCreate(
                    chunk_index=len(chunks),
                    content=piece_content,
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
            "issuing_org",
            "noi_ban_hanh",
            "source_type",
            "ten_file",
            "duong_dan",
            "doc_codes",
            "identifiers",
            "doc_number",
            "document_type",
            "business_domain",
            "recipient_units",
        )
    }

def _compact_chunk_metadata(metadata: dict[str, object]) -> dict[str, object]:
    return {
        key: value
        for key, value in metadata.items()
        if key in CHUNK_METADATA_ALLOWLIST and value not in (None, "", [])
    }


def _structured_subchunks(
    *,
    content: str,
    metadata: dict[str, object],
    base_chunk_type: str,
) -> list[tuple[str, str, dict[str, object]]]:
    canonical = _canonical_chunk_type(content=content, metadata=metadata, base_chunk_type=base_chunk_type)
    pieces: list[tuple[str, str, dict[str, object]]] = [(content, canonical, {})]
    if canonical in {"section", "document_preamble", "document_body"}:
        recipient_text = _recipient_text(content)
        if recipient_text:
            pieces.append(
                (
                    recipient_text,
                    "recipient_block",
                    {
                        "parent_chunk_type": canonical,
                        "recipient_units": _recipient_units(recipient_text),
                    },
                )
            )
        legal_text = _legal_basis_text(content)
        if legal_text:
            pieces.append((legal_text, "legal_basis", {"parent_chunk_type": canonical}))
        if DIRECTIVE_PATTERN.search(_normalized_search_text(content)):
            deadline = _deadline(content)
            pieces.append(
                (
                    content,
                    "directive_task",
                    {
                        "parent_chunk_type": canonical,
                        "deadline": deadline,
                        "deadline_text": deadline,
                    },
                )
            )
    return pieces


def _canonical_chunk_type(
    *,
    content: str,
    metadata: dict[str, object],
    base_chunk_type: str,
) -> str:
    raw_type = str(metadata.get("chunk_type") or base_chunk_type or "").casefold()
    normalized_text = _normalized_search_text(content)
    if raw_type in {"document_header", "header"}:
        return "document_preamble"
    if raw_type in {"footer_signature", "signature", "signature_block"}:
        return "signature_block"
    if raw_type in {"table_parent", "table"}:
        return "table"
    if raw_type == "table_row":
        return "table_row"
    if raw_type in {"document_summary", "summary"}:
        return "summary_block"
    if "phu luc" in normalized_text or "phụ lục" in content.casefold():
        return "appendix"
    if re.search(r"\b(?:dieu|điều)\s+\d+", normalized_text, flags=re.IGNORECASE):
        return "article"
    if re.search(r"\b(?:khoan|khoản)\s+\d+", normalized_text, flags=re.IGNORECASE):
        return "clause"
    if "kinh gui" in normalized_text:
        return "recipient_block"
    if "can cu" in normalized_text:
        return "legal_basis"
    if DIRECTIVE_PATTERN.search(normalized_text):
        return "directive_task"
    if raw_type in {"section", "clause", "article", "appendix", "recipient_block", "legal_basis", "directive_task"}:
        return raw_type
    return "section"


def _normalized_search_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text or "")
    return "".join(ch for ch in normalized if not unicodedata.combining(ch)).casefold()


def _recipient_text(content: str) -> str:
    match = re.search(
        r"(Kính gửi\s*:?\s*(?:\n|.){0,800}?)(?=\n\s*(?:Căn cứ|EVN|Tổng công ty|Công ty|Để|Theo|1\.|I\.|Nơi nhận|Trân trọng|$))",
        content or "",
        flags=re.IGNORECASE,
    )
    return (match.group(1).strip() if match else "")[:1200]


def _recipient_units(text: str) -> list[str]:
    units: list[str] = []
    for line in (text or "").splitlines():
        clean = line.strip(" -–;:\t")
        if not clean or clean.casefold().startswith("kính gửi"):
            continue
        if len(clean) > 3:
            units.append(clean)
    return list(dict.fromkeys(units))[:20]


def _legal_basis_text(content: str) -> str:
    matches = re.findall(r"(Căn cứ[^;\n]*(?:;|\n)?)", content or "", flags=re.IGNORECASE)
    return "\n".join(match.strip() for match in matches)[:2000]


def _deadline(content: str) -> str | None:
    match = DATE_DEADLINE_PATTERN.search(_normalized_search_text(content))
    return match.group(1) if match else None


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
