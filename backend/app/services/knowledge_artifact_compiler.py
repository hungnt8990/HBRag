from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from app.models.chunk import Chunk
from app.models.document import Document
from app.repositories.knowledge_artifacts import KnowledgeArtifactCreate

DOCUMENT_IDENTIFIER_PATTERN = re.compile(
    r"\b(?:[0-9]{3,8}(?:/[\w._/-]{2,})?|[^\W\d_]{1,12}[0-9]{1,8}[\w._/-]*)\b",
    flags=re.IGNORECASE,
)
DATE_PATTERN = re.compile(r"\b(?:[0-3]?\d[/-][01]?\d[/-](?:\d{2}|\d{4})|\d{4}-\d{2}-\d{2})\b")
SHORT_DOC_NUMBER_PATTERN = re.compile(r"\b([0-9]{3,8})(?=/[\w._/-]+)?\b", flags=re.IGNORECASE)

ARTIFACT_TYPE_BY_IDEA_BLOCK_TYPE = {
    "document_identity": "identifier_lookup",
    "directive_task": "procedure_artifact",
    "assignment_table_row": "person_assignment_artifact",
    "legal_clause": "policy_rule_artifact",
    "implementation_plan": "procedure_artifact",
    "system_or_project_reference": "procedure_artifact",
    "recipient_scope": "document_profile",
    "summary_block": "document_profile",
    "deadline_requirement": "procedure_artifact",
}


@dataclass(frozen=True)
class KnowledgeArtifactCompilerConfig:
    enable_llm_extraction: bool = False
    max_identifier_artifacts: int = 20
    max_table_row_artifacts: int = 200


class KnowledgeArtifactCompiler:
    """Compile typed retrieval IdeaBlocks from evidence chunks.

    The compiler is deterministic and metadata-driven. It extracts typed blocks
    from document/chunk structure without binding to any specific sample entity.
    """

    def __init__(self, *, config: KnowledgeArtifactCompilerConfig | None = None) -> None:
        self._config = config or KnowledgeArtifactCompilerConfig()

    def compile_document(
        self,
        *,
        document: Document,
        chunks: list[Chunk],
        docling_metadata: dict[str, Any] | None = None,
    ) -> list[KnowledgeArtifactCreate]:
        document_metadata = dict(getattr(document, "document_metadata", None) or {})
        artifacts: list[KnowledgeArtifactCreate] = []
        artifacts.append(
            self._document_identity_block(
                document=document,
                chunks=chunks,
                document_metadata=document_metadata,
                docling_metadata=docling_metadata or {},
            )
        )
        artifacts.extend(self._identifier_blocks(document=document, chunks=chunks, document_metadata=document_metadata))
        artifacts.extend(self._recipient_blocks(document=document, chunks=chunks, document_metadata=document_metadata))
        artifacts.extend(self._row_blocks(document=document, chunks=chunks, document_metadata=document_metadata))
        artifacts.extend(self._directive_blocks(document=document, chunks=chunks, document_metadata=document_metadata))
        artifacts.extend(self._legal_clause_blocks(document=document, chunks=chunks, document_metadata=document_metadata))
        artifacts.extend(self._summary_blocks(document=document, chunks=chunks, document_metadata=document_metadata))
        return self._dedupe_artifacts(artifacts)

    def failed_artifact(
        self,
        *,
        document_id: UUID,
        error: str,
    ) -> KnowledgeArtifactCreate:
        return KnowledgeArtifactCreate(
            artifact_type="document_profile",
            idea_block_type="summary_block",
            context_type="ingestion",
            title="Knowledge artifact compilation failed",
            canonical_text="Loai: Trang thai bien dich IdeaBlock\nTrang thai: That bai",
            summary_text="Knowledge artifact compilation failed.",
            metadata={"error": str(error)[:1000]},
            structured_data={"error": str(error)[:1000]},
            normalized_identifiers={},
            citation_map={"document_id": str(document_id), "chunks": []},
            confidence_score=0.0,
            extraction_method="deterministic",
            status="failed",
        )

    def _document_identity_block(
        self,
        *,
        document: Document,
        chunks: list[Chunk],
        document_metadata: dict[str, Any],
        docling_metadata: dict[str, Any],
    ) -> KnowledgeArtifactCreate:
        identity = self._base_document_metadata(document=document, document_metadata=document_metadata)
        for source in (document_metadata, dict(document_metadata.get("parsed_metadata") or {}), docling_metadata):
            for key, value in source.items():
                if value in (None, "", [], {}):
                    continue
                normalized_key = str(key).casefold()
                if any(
                    fragment in normalized_key
                    for fragment in (
                        "code",
                        "number",
                        "date",
                        "issuer",
                        "agency",
                        "subject",
                        "title",
                        "recipient",
                        "signer",
                        "document_type",
                    )
                ):
                    identity.setdefault(key, value)
        text_for_ids = "\n".join(
            [document.title or "", str(document_metadata), *[chunk.content[:2000] for chunk in chunks[:20]]]
        )
        identifiers = self._identifier_variants(self._identifiers_from_text(text_for_ids))
        if identifiers:
            identity["identifiers"] = identifiers[:50]
        dates = self._dates_from_text(text_for_ids)
        if dates:
            identity.setdefault("issued_date", dates[0])
            identity["dates"] = dates[:20]
        return self._block(
            document=document,
            idea_block_type="document_identity",
            context_type="document",
            title=document.title,
            metadata=identity,
            evidence_chunks=chunks[:3],
            confidence_score=0.82 if identifiers else 0.62,
        )

    def _identifier_blocks(
        self,
        *,
        document: Document,
        chunks: list[Chunk],
        document_metadata: dict[str, Any],
    ) -> list[KnowledgeArtifactCreate]:
        by_identifier: dict[str, list[Chunk]] = {}
        for chunk in chunks:
            identifiers = self._identifier_variants(
                self._identifiers_from_text(f"{chunk.content}\n{chunk.chunk_metadata}")
            )
            for identifier in identifiers:
                by_identifier.setdefault(identifier, []).append(chunk)

        artifacts: list[KnowledgeArtifactCreate] = []
        base_metadata = self._base_document_metadata(document=document, document_metadata=document_metadata)
        for identifier, source_chunks in sorted(by_identifier.items())[: self._config.max_identifier_artifacts]:
            metadata = {
                **base_metadata,
                "identifier": identifier,
                "identifiers": self._identifier_variants([identifier]),
                "source_count": len(source_chunks),
            }
            artifacts.append(
                self._block(
                    document=document,
                    idea_block_type="document_identity",
                    context_type="identifier",
                    title=identifier,
                    metadata=metadata,
                    evidence_chunks=source_chunks[:5],
                    confidence_score=0.88,
                )
            )
        return artifacts

    def _recipient_blocks(
        self,
        *,
        document: Document,
        chunks: list[Chunk],
        document_metadata: dict[str, Any],
    ) -> list[KnowledgeArtifactCreate]:
        base_metadata = self._base_document_metadata(document=document, document_metadata=document_metadata)
        recipient_values = self._first_list(
            document_metadata,
            keys=("recipient_units", "recipients", "kinh_gui", "to_units", "noi_nhan"),
        )
        recipient_chunks = [
            chunk
            for chunk in chunks
            if str((chunk.chunk_metadata or {}).get("chunk_type") or "").casefold()
            in {"recipient_block", "document_preamble"}
        ]
        if not recipient_values and not recipient_chunks:
            return []
        metadata = {**base_metadata, "recipient_units": recipient_values}
        return [
            self._block(
                document=document,
                idea_block_type="recipient_scope",
                context_type="recipient",
                title="Recipient scope",
                metadata=metadata,
                evidence_chunks=recipient_chunks[:5] or chunks[:1],
                confidence_score=0.76 if recipient_values else 0.58,
            )
        ]

    def _row_blocks(
        self,
        *,
        document: Document,
        chunks: list[Chunk],
        document_metadata: dict[str, Any],
    ) -> list[KnowledgeArtifactCreate]:
        artifacts: list[KnowledgeArtifactCreate] = []
        for chunk in chunks:
            metadata = dict(chunk.chunk_metadata or {})
            if not self._is_table_row(metadata):
                continue
            row_data = self._row_data(chunk=chunk, metadata=metadata)
            idea_metadata = {
                **self._base_document_metadata(document=document, document_metadata=document_metadata),
                **self._metadata_tags_from_row(row_data),
                "row": row_data,
                "table_id": metadata.get("table_id") or metadata.get("table_name"),
                "row_index": metadata.get("row_index") or metadata.get("row_start") or metadata.get("stt"),
                "stt": metadata.get("stt"),
            }
            artifacts.append(
                self._block(
                    document=document,
                    idea_block_type="assignment_table_row",
                    context_type="assignment",
                    title=self._row_title(row_data=row_data, metadata=metadata),
                    metadata=idea_metadata,
                    evidence_chunks=[chunk],
                    confidence_score=float(metadata.get("confidence") or 0.82),
                )
            )
            if len(artifacts) >= self._config.max_table_row_artifacts:
                break
        return artifacts

    def _directive_blocks(
        self,
        *,
        document: Document,
        chunks: list[Chunk],
        document_metadata: dict[str, Any],
    ) -> list[KnowledgeArtifactCreate]:
        artifacts: list[KnowledgeArtifactCreate] = []
        for chunk in chunks:
            metadata = dict(chunk.chunk_metadata or {})
            chunk_type = str(metadata.get("chunk_type") or "").casefold()
            selected = self._fields_with_name_fragments(
                metadata,
                {
                    "task",
                    "assignment",
                    "owner",
                    "assigned",
                    "deadline",
                    "due",
                    "implementation",
                    "scope",
                    "unit",
                    "cooperating",
                    "system",
                    "project",
                },
            )
            if chunk_type not in {"directive_task", "section", "clause", "document_body", "table_row"} and not selected:
                continue
            if not selected and not self._looks_like_directive(chunk.content):
                continue
            metadata_tags = {
                **self._base_document_metadata(document=document, document_metadata=document_metadata),
                **selected,
                **self._metadata_tags_from_row(selected),
                "directive_text": self._snippet(chunk.content, 1200),
            }
            idea_type = "deadline_requirement" if metadata_tags.get("deadline") else "directive_task"
            if self._field_name_contains_any(selected, {"system", "project", "software"}):
                artifacts.append(
                    self._block(
                        document=document,
                        idea_block_type="system_or_project_reference",
                        context_type="system_project",
                        title=str(selected.get("system_name") or selected.get("project_name") or metadata.get("section_title") or document.title),
                        metadata=metadata_tags,
                        evidence_chunks=[chunk],
                        confidence_score=0.72,
                    )
                )
            artifacts.append(
                self._block(
                    document=document,
                    idea_block_type=idea_type,
                    context_type="directive",
                    title=str(selected.get("task") or selected.get("title") or metadata.get("section_title") or document.title),
                    metadata=metadata_tags,
                    evidence_chunks=[chunk],
                    confidence_score=0.78 if selected else 0.62,
                )
            )
        return artifacts

    def _legal_clause_blocks(
        self,
        *,
        document: Document,
        chunks: list[Chunk],
        document_metadata: dict[str, Any],
    ) -> list[KnowledgeArtifactCreate]:
        artifacts: list[KnowledgeArtifactCreate] = []
        for chunk in chunks:
            metadata = dict(chunk.chunk_metadata or {})
            rule_data = self._fields_with_name_fragments(
                metadata,
                {
                    "policy",
                    "rule",
                    "condition",
                    "benefit",
                    "obligation",
                    "exception",
                    "amount",
                    "days",
                    "salary",
                    "allowance",
                    "entitlement",
                    "case",
                    "article",
                    "clause",
                },
            )
            chunk_type = str(metadata.get("chunk_type") or "").casefold()
            relationship_type = str(metadata.get("relationship_type") or "").casefold()
            if not rule_data and chunk_type not in {"legal_basis", "clause", "article"} and "benefit" not in relationship_type:
                continue
            numeric_facts = self._numeric_facts(chunk.content)
            if numeric_facts:
                rule_data["numeric_facts"] = numeric_facts
            rule_data.setdefault("clause_text", self._snippet(chunk.content, 1200))
            metadata_tags = {
                **self._base_document_metadata(document=document, document_metadata=document_metadata),
                **rule_data,
                **self._metadata_tags_from_row(rule_data),
            }
            artifacts.append(
                self._block(
                    document=document,
                    idea_block_type="legal_clause",
                    context_type="legal_clause",
                    title=str(rule_data.get("case_name") or rule_data.get("title") or metadata.get("section_title") or document.title),
                    metadata=metadata_tags,
                    evidence_chunks=[chunk],
                    confidence_score=0.8 if numeric_facts else 0.68,
                )
            )
        return artifacts

    def _summary_blocks(
        self,
        *,
        document: Document,
        chunks: list[Chunk],
        document_metadata: dict[str, Any],
    ) -> list[KnowledgeArtifactCreate]:
        summary = (
            document_metadata.get("source_summary")
            or document_metadata.get("tom_tat")
            or document_metadata.get("summary")
        )
        if not summary:
            return []
        metadata = {
            **self._base_document_metadata(document=document, document_metadata=document_metadata),
            "summary_text": summary,
        }
        return [
            self._block(
                document=document,
                idea_block_type="summary_block",
                context_type="summary",
                title=f"Summary: {document.title}",
                metadata=metadata,
                evidence_chunks=chunks[:3],
                confidence_score=0.7,
            )
        ]

    def _block(
        self,
        *,
        document: Document,
        idea_block_type: str,
        context_type: str,
        title: str | None,
        metadata: dict[str, Any],
        evidence_chunks: list[Chunk],
        confidence_score: float,
    ) -> KnowledgeArtifactCreate:
        clean_metadata = self._clean_metadata(metadata)
        evidence_chunk_ids = [str(chunk.id) for chunk in evidence_chunks if getattr(chunk, "id", None)]
        clean_metadata["evidence_chunk_ids"] = evidence_chunk_ids
        clean_metadata["confidence"] = confidence_score
        identifiers = self._identifier_variants(
            list(clean_metadata.get("identifiers") or [])
            + self._identifiers_from_text(f"{title or ''}\n{self._render_value(clean_metadata)}")
        )
        normalized_identifiers: dict[str, Any] = {"document_id": str(document.id)}
        if identifiers:
            normalized_identifiers["identifiers"] = identifiers[:50]
        for key in ("person_names", "recipient_units", "assigned_units", "department_names", "project_names", "system_names"):
            values = self._as_list(clean_metadata.get(key))
            if values:
                normalized_identifiers[key] = values

        scope_key = self._scope_key(
            document_id=document.id,
            idea_block_type=idea_block_type,
            metadata=clean_metadata,
        )
        canonical_text = self._canonical_text(
            idea_block_type=idea_block_type,
            title=title,
            metadata=clean_metadata,
            evidence_chunks=evidence_chunks,
        )
        content_hash = self._hash_text(canonical_text)
        dedup_hash = self._hash_text("|".join([scope_key, content_hash]))
        return KnowledgeArtifactCreate(
            artifact_type=ARTIFACT_TYPE_BY_IDEA_BLOCK_TYPE.get(idea_block_type, "document_profile"),
            idea_block_type=idea_block_type,
            context_type=context_type,
            title=title,
            canonical_text=canonical_text,
            summary_text=self._snippet(str(clean_metadata.get("summary_text") or clean_metadata.get("directive_text") or ""), 500) or None,
            metadata=clean_metadata,
            evidence_chunk_ids=evidence_chunk_ids,
            source_chunk_ids=evidence_chunk_ids,
            scope_key=scope_key,
            content_hash=content_hash,
            dedup_hash=dedup_hash,
            embedding_status="pending",
            structured_data=clean_metadata,
            normalized_identifiers=normalized_identifiers,
            citation_map={"document_id": str(document.id), "chunks": [self._chunk_citation(chunk) for chunk in evidence_chunks]},
            confidence_score=confidence_score,
            extraction_method="deterministic",
            status="ready",
        )

    @staticmethod
    def _base_document_metadata(*, document: Document, document_metadata: dict[str, Any]) -> dict[str, Any]:
        doc_code = (
            document_metadata.get("doc_code")
            or document_metadata.get("document_code")
            or document_metadata.get("ky_hieu")
            or document_metadata.get("document_number")
        )
        identifiers = KnowledgeArtifactCompiler._identifier_variants(
            list(document_metadata.get("identifiers") or [])
            + list(document_metadata.get("doc_codes") or [])
            + ([doc_code] if doc_code else [])
        )
        doc_number = document_metadata.get("doc_number") or KnowledgeArtifactCompiler._document_number(doc_code)
        return {
            "document_id": str(document.id),
            "doc_id": str(document.id),
            "title": document.title,
            "document_title": document.title,
            "source_type": document.source_type,
            "doc_code": doc_code,
            "official_dispatch_code": doc_code,
            "doc_number": doc_number,
            "issued_date": document_metadata.get("issued_date") or document_metadata.get("ngay_vb"),
            "issuing_org": document_metadata.get("issuing_org") or document_metadata.get("issuer") or document_metadata.get("noi_ban_hanh"),
            "subject": document_metadata.get("subject") or document_metadata.get("trich_yeu"),
            "document_type": document_metadata.get("document_type"),
            "business_domain": document_metadata.get("business_domain"),
            "source_summary": document_metadata.get("source_summary"),
            "signer": document_metadata.get("signer") or document_metadata.get("nguoi_ky"),
            "identifiers": identifiers,
        }

    @staticmethod
    def _metadata_tags_from_row(row_data: dict[str, Any]) -> dict[str, Any]:
        return {
            "recipient_units": KnowledgeArtifactCompiler._first_list(row_data, keys=("recipient_units", "recipients", "kinh_gui")),
            "assigned_units": KnowledgeArtifactCompiler._first_list(row_data, keys=("assigned_units", "assigned_unit", "unit", "owner_unit")),
            "person_names": KnowledgeArtifactCompiler._person_names(row_data),
            "department_names": KnowledgeArtifactCompiler._first_list(row_data, keys=("department_names", "department", "department_name", "phong_ban")),
            "project_names": KnowledgeArtifactCompiler._first_list(row_data, keys=("project_names", "project_name", "project")),
            "system_names": KnowledgeArtifactCompiler._first_list(row_data, keys=("system_names", "software_system", "system_name", "system")),
            "article_no": row_data.get("article_no") or row_data.get("article"),
            "clause_no": row_data.get("clause_no") or row_data.get("clause"),
            "deadline": row_data.get("deadline") or row_data.get("due_date") or row_data.get("thoi_han"),
            "evn_unit": row_data.get("evn_unit"),
            "power_company": row_data.get("power_company"),
            "software_system": row_data.get("software_system") or row_data.get("system_name"),
            "implementation_scope": row_data.get("implementation_scope") or row_data.get("scope") or row_data.get("task_area"),
            "task_owner": row_data.get("task_owner") or row_data.get("owner") or row_data.get("person_name"),
            "cooperating_unit": row_data.get("cooperating_unit"),
        }

    @staticmethod
    def _scope_key(*, document_id: UUID, idea_block_type: str, metadata: dict[str, Any]) -> str:
        if idea_block_type == "assignment_table_row":
            parts = (
                document_id,
                metadata.get("table_id"),
                metadata.get("stt") or metadata.get("row_index"),
                KnowledgeArtifactCompiler._render_value(metadata.get("person_names")),
                KnowledgeArtifactCompiler._render_value(metadata.get("department_names")),
                metadata.get("implementation_scope"),
            )
        elif idea_block_type == "legal_clause":
            parts = (
                document_id,
                metadata.get("article_no"),
                metadata.get("clause_no"),
                metadata.get("condition") or metadata.get("case_name"),
            )
        else:
            parts = (
                metadata.get("doc_code") or document_id,
                metadata.get("issued_date"),
                metadata.get("issuing_org"),
                idea_block_type,
                metadata.get("assigned_units"),
                metadata.get("person_names"),
            )
        return "|".join(" ".join(str(part or "").split()).casefold() for part in parts)

    @staticmethod
    def _canonical_text(
        *,
        idea_block_type: str,
        title: str | None,
        metadata: dict[str, Any],
        evidence_chunks: list[Chunk],
    ) -> str:
        label = {
            "document_identity": "Dinh danh van ban",
            "directive_task": "Nhiem vu giao viec",
            "assignment_table_row": "Dong bang phan cong",
            "legal_clause": "Dieu khoan/quy dinh",
            "implementation_plan": "Ke hoach thuc hien",
            "system_or_project_reference": "He thong/du an lien quan",
            "recipient_scope": "Pham vi kinh gui",
            "summary_block": "Tom tat van ban",
            "deadline_requirement": "Yeu cau thoi han",
        }.get(idea_block_type, idea_block_type)
        ordered_keys = [
            "doc_code",
            "doc_number",
            "issued_date",
            "issuing_org",
            "subject",
            "recipient_units",
            "assigned_units",
            "person_names",
            "department_names",
            "project_names",
            "system_names",
            "article_no",
            "clause_no",
            "deadline",
            "document_type",
            "business_domain",
            "evn_unit",
            "power_company",
            "software_system",
            "implementation_scope",
            "task_owner",
            "cooperating_unit",
            "identifier",
            "directive_text",
            "clause_text",
            "summary_text",
        ]
        lines = [f"Loai: {label}"]
        if title:
            lines.append(f"Tieu de: {title}")
        for key in ordered_keys:
            value = metadata.get(key)
            if value not in (None, "", [], {}):
                lines.append(f"{key}: {KnowledgeArtifactCompiler._render_value(value)}")
        evidence_text = " ".join(chunk.content for chunk in evidence_chunks[:2])
        if evidence_text:
            lines.append(f"Bang chung: {KnowledgeArtifactCompiler._snippet(evidence_text, 900)}")
        return "\n".join(lines)

    @staticmethod
    def _is_table_row(metadata: dict[str, Any]) -> bool:
        chunk_type = str(metadata.get("chunk_type") or "").casefold()
        if "row" in chunk_type:
            return True
        if metadata.get("row_start") is not None or metadata.get("row_end") is not None:
            return True
        headers = metadata.get("headers")
        return isinstance(headers, list) and bool(headers)

    @staticmethod
    def _row_data(*, chunk: Chunk, metadata: dict[str, Any]) -> dict[str, Any]:
        row_data: dict[str, Any] = {}
        for key, value in metadata.items():
            if key in {"access", "validation_issues", "enrichment"}:
                continue
            if value in (None, "", [], {}):
                continue
            if isinstance(value, (str, int, float, bool, list, dict)):
                row_data[key] = value
        row_data.setdefault("chunk_index", chunk.chunk_index)
        row_data.setdefault("row_text", chunk.content)
        return row_data

    @staticmethod
    def _row_title(*, row_data: dict[str, Any], metadata: dict[str, Any]) -> str | None:
        for key in (
            "title",
            "section_title",
            "table_name",
            "case_name",
            "person_name",
            "entity_name",
            "area",
            "task_area",
            "feature_name",
        ):
            value = row_data.get(key) or metadata.get(key)
            if value not in (None, "", [], {}):
                return str(value)[:255]
        return None

    @staticmethod
    def _looks_like_directive(text: str) -> bool:
        normalized = (text or "").casefold()
        return any(fragment in normalized for fragment in ("bao cao", "thuc hien", "trien khai", "hoan thanh", "nhiem vu", "deadline"))

    @staticmethod
    def _fields_with_name_fragments(metadata: dict[str, Any], fragments: set[str]) -> dict[str, Any]:
        selected: dict[str, Any] = {}
        for key, value in metadata.items():
            if value in (None, "", [], {}):
                continue
            if KnowledgeArtifactCompiler._field_name_contains(key, fragments):
                selected[key] = value
        return selected

    @staticmethod
    def _field_name_contains(key: Any, fragments: set[str]) -> bool:
        normalized = re.sub(r"[^a-z0-9]+", "_", str(key or "").casefold())
        return any(fragment in normalized for fragment in fragments)

    @staticmethod
    def _field_name_contains_any(metadata: dict[str, Any], fragments: set[str]) -> bool:
        return any(KnowledgeArtifactCompiler._field_name_contains(key, fragments) for key in metadata)

    @staticmethod
    def _person_names(row_data: dict[str, Any]) -> list[str]:
        values: list[Any] = []
        for key in ("person_name", "entity_name", "staff_names", "staff", "people", "assignees", "task_owner"):
            if key in row_data:
                values.append(row_data[key])
        names: list[str] = []
        for value in values:
            if isinstance(value, str):
                names.append(value)
            elif isinstance(value, dict):
                for nested_key in ("name", "full_name", "person_name", "entity_name"):
                    nested = value.get(nested_key)
                    if nested:
                        names.append(str(nested))
            elif isinstance(value, list | tuple | set):
                for item in value:
                    if isinstance(item, dict):
                        names.extend(KnowledgeArtifactCompiler._person_names(item))
                    elif item not in (None, ""):
                        names.append(str(item))
        return KnowledgeArtifactCompiler._dedupe_text(names, limit=20)

    @staticmethod
    def _numeric_facts(text: str) -> list[str]:
        facts = []
        for match in re.finditer(r".{0,60}\b\d+(?:[.,]\d+)?\s*(?:%|[A-Za-z]+)?\b.{0,60}", text or ""):
            facts.append(" ".join(match.group(0).split()))
        return KnowledgeArtifactCompiler._dedupe_text(facts, limit=12)

    @staticmethod
    def _identifiers_from_text(text: str) -> list[str]:
        return KnowledgeArtifactCompiler._dedupe_text(DOCUMENT_IDENTIFIER_PATTERN.findall(text or ""), limit=50)

    @staticmethod
    def _identifier_variants(values: list[Any]) -> list[str]:
        expanded: list[str] = []
        for value in values:
            clean = " ".join(str(value or "").split()).strip()
            if not clean:
                continue
            expanded.append(clean)
            for match in SHORT_DOC_NUMBER_PATTERN.finditer(clean):
                expanded.append(match.group(1))
        return KnowledgeArtifactCompiler._dedupe_text(expanded, limit=80)

    @staticmethod
    def _document_number(value: Any) -> str | None:
        if value in (None, ""):
            return None
        match = SHORT_DOC_NUMBER_PATTERN.search(str(value))
        return match.group(1) if match else None

    @staticmethod
    def _dates_from_text(text: str) -> list[str]:
        return KnowledgeArtifactCompiler._dedupe_text(DATE_PATTERN.findall(text or ""), limit=20)

    @staticmethod
    def _chunk_citation(chunk: Chunk) -> dict[str, Any]:
        metadata = dict(chunk.chunk_metadata or {})
        return {
            "chunk_id": str(chunk.id),
            "page": metadata.get("page_number") or metadata.get("page_start"),
            "section": metadata.get("section_title") or metadata.get("heading_path"),
            "table": metadata.get("table_id") or metadata.get("table_name"),
            "row": metadata.get("row_start") or metadata.get("row_index") or metadata.get("stt"),
        }

    @staticmethod
    def _render_value(value: Any) -> str:
        if isinstance(value, dict):
            return ", ".join(
                f"{key}={KnowledgeArtifactCompiler._render_value(item)}"
                for key, item in value.items()
                if item not in (None, "", [], {})
            )
        if isinstance(value, list | tuple | set):
            return ", ".join(
                KnowledgeArtifactCompiler._render_value(item)
                for item in value
                if item not in (None, "", [], {})
            )
        return str(value)

    @staticmethod
    def _snippet(text: str, limit: int) -> str:
        clean = " ".join((text or "").split())
        return clean[:limit]

    @staticmethod
    def _clean_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
        return {
            key: value
            for key, value in metadata.items()
            if value not in (None, "", [], {}) and isinstance(value, (str, int, float, bool, list, dict))
        }

    @staticmethod
    def _as_list(value: Any) -> list[str]:
        if value in (None, "", [], {}):
            return []
        if isinstance(value, list | tuple | set):
            return KnowledgeArtifactCompiler._dedupe_text([str(item) for item in value], limit=50)
        return KnowledgeArtifactCompiler._dedupe_text([str(value)], limit=50)

    @staticmethod
    def _first_list(metadata: dict[str, Any], *, keys: tuple[str, ...]) -> list[str]:
        for key in keys:
            values = KnowledgeArtifactCompiler._as_list(metadata.get(key))
            if values:
                return values
        return []

    @staticmethod
    def _dedupe_text(values: list[str], *, limit: int) -> list[str]:
        ordered: list[str] = []
        seen: set[str] = set()
        for value in values:
            clean = " ".join(str(value or "").split()).strip(" ?!.,;:")
            if not clean:
                continue
            key = clean.casefold()
            if key in seen:
                continue
            seen.add(key)
            ordered.append(clean)
            if len(ordered) >= limit:
                break
        return ordered

    @staticmethod
    def _dedupe_artifacts(artifacts: list[KnowledgeArtifactCreate]) -> list[KnowledgeArtifactCreate]:
        by_hash: dict[str, KnowledgeArtifactCreate] = {}
        for artifact in artifacts:
            key = artifact.dedup_hash or hashlib.sha1(
                "|".join(
                    [
                        artifact.idea_block_type or artifact.artifact_type,
                        artifact.scope_key or "",
                        artifact.canonical_text,
                    ]
                ).encode("utf-8")
            ).hexdigest()
            existing = by_hash.get(key)
            if existing is None:
                by_hash[key] = artifact
                continue
            by_hash[key] = KnowledgeArtifactCompiler._merge_artifacts(existing, artifact)
        return list(by_hash.values())

    @staticmethod
    def _merge_artifacts(left: KnowledgeArtifactCreate, right: KnowledgeArtifactCreate) -> KnowledgeArtifactCreate:
        evidence = KnowledgeArtifactCompiler._dedupe_text(
            [*left.evidence_chunk_ids, *right.evidence_chunk_ids, *left.source_chunk_ids, *right.source_chunk_ids],
            limit=100,
        )
        metadata = {**left.metadata, **right.metadata}
        metadata["evidence_chunk_ids"] = evidence
        structured_data = {**left.structured_data, **right.structured_data}
        structured_data["evidence_chunk_ids"] = evidence
        citations = [
            *list((left.citation_map or {}).get("chunks") or []),
            *list((right.citation_map or {}).get("chunks") or []),
        ]
        return KnowledgeArtifactCreate(
            artifact_type=left.artifact_type,
            idea_block_type=left.idea_block_type,
            context_type=left.context_type,
            title=left.title or right.title,
            canonical_text=left.canonical_text if len(left.canonical_text) >= len(right.canonical_text) else right.canonical_text,
            summary_text=left.summary_text or right.summary_text,
            metadata=metadata,
            evidence_chunk_ids=evidence,
            source_chunk_ids=evidence,
            scope_key=left.scope_key,
            content_hash=left.content_hash,
            dedup_hash=left.dedup_hash,
            embedding_status=left.embedding_status,
            structured_data=structured_data,
            normalized_identifiers={**left.normalized_identifiers, **right.normalized_identifiers},
            citation_map={"document_id": (left.citation_map or {}).get("document_id"), "chunks": citations},
            confidence_score=max(left.confidence_score, right.confidence_score),
            extraction_method=left.extraction_method,
            status=left.status,
        )

    @staticmethod
    def _hash_text(text: str) -> str:
        return hashlib.sha256((text or "").encode("utf-8")).hexdigest()
