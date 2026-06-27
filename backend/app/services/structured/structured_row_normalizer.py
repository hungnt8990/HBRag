from __future__ import annotations

from typing import Any

from app.services.structured.structured_schemas import StructuredRow


def normalize_structured_row(
    *,
    chunk: Any,
    citation: int | None = None,
) -> StructuredRow | None:
    metadata = chunk.chunk_metadata or {}
    content = chunk.content or ""

    chunk_type = str(metadata.get("chunk_type") or "")
    if chunk_type not in {
        "table_row",
        "table_rows",
        "legal_table_row",
        "structured_fact_row",
        "entity_profile",
    }:
        return None

    fields: dict[str, Any] = {}

    # Generic copy: không hiểu tiếng Việt, chỉ gom field có sẵn.
    for key, value in metadata.items():
        if value in (None, "", [], {}):
            continue
        if isinstance(value, (str, int, float, bool, list, dict)):
            fields[key] = value

    # Backward-compatible normalization.
    if "case_name" in metadata:
        fields.setdefault("subject", metadata.get("case_name"))

    if "total_days" in metadata or "total_leave_days" in metadata:
        fields.setdefault(
            "measure_value",
            metadata.get("total_days") or metadata.get("total_leave_days"),
        )

    if "total_benefit" in metadata or "total_leave_benefit" in metadata:
        fields.setdefault(
            "measure_text",
            metadata.get("total_benefit") or metadata.get("total_leave_benefit"),
        )

    if "area" in metadata:
        fields.setdefault("topic", metadata.get("area"))

    if "department" in metadata or "lead_department" in metadata:
        fields.setdefault(
            "department",
            metadata.get("department") or metadata.get("lead_department"),
        )

    if "staff" in metadata or "proposed_staff" in metadata:
        fields.setdefault(
            "person",
            metadata.get("staff") or metadata.get("proposed_staff"),
        )

    if not fields:
        return None

    return StructuredRow(
        row_id=str(
            metadata.get("row_id")
            or metadata.get("case_code")
            or metadata.get("chunk_id")
            or ""
        ),
        chunk_id=str(getattr(chunk, "id", "") or ""),
        document_id=str(getattr(chunk, "document_id", "") or ""),
        table_id=str(metadata.get("table_id") or metadata.get("table_name") or ""),
        source_title=str(
            metadata.get("document_title")
            or metadata.get("source_label")
            or metadata.get("source_file")
            or ""
        ),
        fields=fields,
        raw_text=content,
        citation=citation,
    )