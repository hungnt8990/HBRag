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
    r"\b(?:[0-9]{3,8}(?:/[A-Z0-9._/-]{2,})?|[A-Z]{1,12}[0-9]{1,8}[A-Z0-9._/-]*)\b",
    flags=re.IGNORECASE,
)
DATE_PATTERN = re.compile(r"\b(?:[0-3]?\d[/-][01]?\d[/-](?:\d{2}|\d{4})|\d{4}-\d{2}-\d{2})\b")

DOCUMENT_PROFILE_KEY_FRAGMENTS = {
    "agency",
    "code",
    "date",
    "issuer",
    "number",
    "signed",
    "signer",
    "subject",
    "title",
    "type",
    "ky_hieu",
    "ngay_vb",
    "ngay_tao",
    "nguoi_ky",
    "noi_ban_hanh",
    "trich_yeu",
}


@dataclass(frozen=True)
class KnowledgeArtifactCompilerConfig:
    enable_llm_extraction: bool = False
    max_identifier_artifacts: int = 20
    max_table_row_artifacts: int = 200


class KnowledgeArtifactCompiler:
    """Compile structured retrieval artifacts from already-created chunks.

    The compiler is intentionally deterministic-first and schema-driven. It does
    not know any specific person, document code, answer, or organization name.
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
        artifacts: list[KnowledgeArtifactCreate] = []
        artifacts.append(self._document_profile_artifact(document=document, chunks=chunks, docling_metadata=docling_metadata or {}))
        artifacts.extend(self._identifier_artifacts(document=document, chunks=chunks))
        artifacts.extend(self._row_artifacts(document=document, chunks=chunks))
        artifacts.extend(self._procedure_artifacts(document=document, chunks=chunks))
        artifacts.extend(self._policy_rule_artifacts(document=document, chunks=chunks))
        return self._dedupe_artifacts(artifacts)

    def failed_artifact(
        self,
        *,
        document_id: UUID,
        error: str,
    ) -> KnowledgeArtifactCreate:
        return KnowledgeArtifactCreate(
            artifact_type="document_profile",
            context_type="ingestion",
            title="Knowledge artifact compilation failed",
            canonical_text="Knowledge artifact compilation failed.",
            structured_data={"error": str(error)[:1000]},
            normalized_identifiers={},
            citation_map={"document_id": str(document_id), "chunks": []},
            confidence_score=0.0,
            extraction_method="deterministic",
            status="failed",
        )

    def _document_profile_artifact(
        self,
        *,
        document: Document,
        chunks: list[Chunk],
        docling_metadata: dict[str, Any],
    ) -> KnowledgeArtifactCreate:
        metadata = dict(getattr(document, "document_metadata", None) or {})
        profile = self._document_profile_data(document=document, metadata=metadata, docling_metadata=docling_metadata)
        identifiers = self._identifiers_from_text("\n".join([document.title or "", str(metadata), *[chunk.content[:2000] for chunk in chunks[:20]]]))
        if identifiers:
            profile["identifiers"] = identifiers[:20]
        dates = self._dates_from_text("\n".join([document.title or "", str(metadata), *[chunk.content[:2000] for chunk in chunks[:10]]]))
        if dates:
            profile["dates"] = dates[:10]
        canonical_parts = [f"Document title: {document.title}"]
        for key, value in profile.items():
            if value in (None, "", [], {}):
                continue
            canonical_parts.append(f"{key}: {self._render_value(value)}")
        return KnowledgeArtifactCreate(
            artifact_type="document_profile",
            context_type="document",
            title=document.title,
            canonical_text="; ".join(canonical_parts),
            structured_data=profile,
            normalized_identifiers={"identifiers": identifiers[:20], "document_id": str(document.id)},
            citation_map={"document_id": str(document.id), "chunks": [self._chunk_citation(chunk) for chunk in chunks[:3]]},
            confidence_score=0.72 if identifiers or dates else 0.55,
            extraction_method="deterministic",
            status="ready",
        )

    def _identifier_artifacts(
        self,
        *,
        document: Document,
        chunks: list[Chunk],
    ) -> list[KnowledgeArtifactCreate]:
        by_identifier: dict[str, list[Chunk]] = {}
        for chunk in chunks:
            for identifier in self._identifiers_from_text(f"{chunk.content}\n{chunk.chunk_metadata}"):
                by_identifier.setdefault(identifier, []).append(chunk)

        artifacts: list[KnowledgeArtifactCreate] = []
        for identifier, source_chunks in sorted(by_identifier.items())[: self._config.max_identifier_artifacts]:
            source_chunk_ids = [str(chunk.id) for chunk in source_chunks[:5]]
            source_text = " ".join(chunk.content for chunk in source_chunks[:2])
            canonical = f"Identifier {identifier} appears in document {document.title}. {self._snippet(source_text, 600)}"
            artifacts.append(
                KnowledgeArtifactCreate(
                    artifact_type="identifier_lookup",
                    context_type="identifier",
                    title=identifier,
                    canonical_text=canonical,
                    source_chunk_ids=source_chunk_ids,
                    structured_data={
                        "identifier": identifier,
                        "document_title": document.title,
                        "source_count": len(source_chunks),
                    },
                    normalized_identifiers={"identifiers": [identifier], "document_id": str(document.id)},
                    citation_map={"document_id": str(document.id), "chunks": [self._chunk_citation(chunk) for chunk in source_chunks[:5]]},
                    confidence_score=0.86,
                    extraction_method="deterministic",
                    status="ready",
                )
            )
        return artifacts

    def _row_artifacts(
        self,
        *,
        document: Document,
        chunks: list[Chunk],
    ) -> list[KnowledgeArtifactCreate]:
        artifacts: list[KnowledgeArtifactCreate] = []
        for chunk in chunks:
            metadata = dict(chunk.chunk_metadata or {})
            if not self._is_table_row(metadata):
                continue
            row_data = self._row_data(chunk=chunk, metadata=metadata)
            canonical_text = self._canonical_row_text(row_data=row_data, fallback=chunk.content)
            artifacts.append(
                KnowledgeArtifactCreate(
                    artifact_type="table_row_artifact",
                    context_type="table",
                    title=self._row_title(row_data=row_data, metadata=metadata),
                    canonical_text=canonical_text,
                    source_chunk_ids=[str(chunk.id)],
                    structured_data={"row": row_data, "content": chunk.content},
                    normalized_identifiers=self._normalized_identifiers_for_row(row_data=row_data, document_id=document.id),
                    citation_map={"document_id": str(document.id), "chunks": [self._chunk_citation(chunk)]},
                    confidence_score=float(metadata.get("confidence") or 0.78),
                    extraction_method="deterministic",
                    status="ready",
                )
            )
            person_artifact = self._person_assignment_artifact(document=document, chunk=chunk, row_data=row_data)
            if person_artifact is not None:
                artifacts.append(person_artifact)
            if len(artifacts) >= self._config.max_table_row_artifacts:
                break
        return artifacts

    def _person_assignment_artifact(
        self,
        *,
        document: Document,
        chunk: Chunk,
        row_data: dict[str, Any],
    ) -> KnowledgeArtifactCreate | None:
        names = self._person_names(row_data)
        if not names:
            return None
        assignment = {
            key: value
            for key, value in row_data.items()
            if self._field_name_contains(key, {"stt", "task", "area", "department", "unit", "role", "note", "assignment", "work", "lead"})
            and value not in (None, "", [], {})
        }
        if not assignment:
            assignment = {"row": row_data}
        canonical = "; ".join(
            f"{name}: {self._render_value(assignment)}"
            for name in names[:5]
        )
        return KnowledgeArtifactCreate(
            artifact_type="person_assignment_artifact",
            context_type="people",
            title=", ".join(names[:3]),
            canonical_text=canonical,
            source_chunk_ids=[str(chunk.id)],
            structured_data={"people": names, "assignments": [assignment], "row": row_data},
            normalized_identifiers={"people": names, "document_id": str(document.id)},
            citation_map={"document_id": str(document.id), "chunks": [self._chunk_citation(chunk)]},
            confidence_score=0.88,
            extraction_method="deterministic",
            status="ready",
        )

    def _procedure_artifacts(
        self,
        *,
        document: Document,
        chunks: list[Chunk],
    ) -> list[KnowledgeArtifactCreate]:
        artifacts: list[KnowledgeArtifactCreate] = []
        for chunk in chunks:
            metadata = dict(chunk.chunk_metadata or {})
            procedure_data = self._fields_with_name_fragments(
                metadata,
                {"procedure", "dossier", "deadline", "fee", "result", "agency", "step", "process"},
            )
            chunk_type = str(metadata.get("chunk_type") or "").casefold()
            if not procedure_data and "procedure" not in chunk_type:
                continue
            if not procedure_data:
                procedure_data = {"content": self._snippet(chunk.content, 1200)}
            canonical = f"Procedure artifact from {document.title}: {self._render_value(procedure_data)}"
            artifacts.append(
                KnowledgeArtifactCreate(
                    artifact_type="procedure_artifact",
                    context_type="procedure",
                    title=str(procedure_data.get("procedure") or procedure_data.get("title") or metadata.get("section_title") or document.title),
                    canonical_text=canonical,
                    source_chunk_ids=[str(chunk.id)],
                    structured_data=procedure_data,
                    normalized_identifiers={"document_id": str(document.id), "identifiers": self._identifiers_from_text(chunk.content)},
                    citation_map={"document_id": str(document.id), "chunks": [self._chunk_citation(chunk)]},
                    confidence_score=0.74,
                    extraction_method="deterministic",
                    status="ready",
                )
            )
        return artifacts

    def _policy_rule_artifacts(
        self,
        *,
        document: Document,
        chunks: list[Chunk],
    ) -> list[KnowledgeArtifactCreate]:
        artifacts: list[KnowledgeArtifactCreate] = []
        for chunk in chunks:
            metadata = dict(chunk.chunk_metadata or {})
            rule_data = self._fields_with_name_fragments(
                metadata,
                {"policy", "rule", "condition", "benefit", "obligation", "exception", "amount", "days", "salary", "allowance", "entitlement", "case"},
            )
            relationship_type = str(metadata.get("relationship_type") or "").casefold()
            if not rule_data and not any(fragment in relationship_type for fragment in ("policy", "rule", "benefit")):
                continue
            numeric_facts = self._numeric_facts(chunk.content)
            if numeric_facts:
                rule_data["numeric_facts"] = numeric_facts
            if not rule_data:
                rule_data = {"content": self._snippet(chunk.content, 1200)}
            canonical = f"Policy/rule artifact from {document.title}: {self._render_value(rule_data)}"
            artifacts.append(
                KnowledgeArtifactCreate(
                    artifact_type="policy_rule_artifact",
                    context_type="policy",
                    title=str(rule_data.get("case_name") or rule_data.get("title") or metadata.get("section_title") or document.title),
                    canonical_text=canonical,
                    source_chunk_ids=[str(chunk.id)],
                    structured_data=rule_data,
                    normalized_identifiers={"document_id": str(document.id), "identifiers": self._identifiers_from_text(chunk.content)},
                    citation_map={"document_id": str(document.id), "chunks": [self._chunk_citation(chunk)]},
                    confidence_score=0.76 if numeric_facts else 0.66,
                    extraction_method="deterministic",
                    status="ready",
                )
            )
        return artifacts

    @staticmethod
    def _document_profile_data(
        *,
        document: Document,
        metadata: dict[str, Any],
        docling_metadata: dict[str, Any],
    ) -> dict[str, Any]:
        profile: dict[str, Any] = {
            "document_id": str(document.id),
            "title": document.title,
            "source_type": document.source_type,
            "document_profile": getattr(document, "document_profile", None),
        }
        for source in (metadata, dict(metadata.get("parsed_metadata") or {}), docling_metadata):
            for key, value in source.items():
                normalized_key = str(key).casefold()
                if any(fragment in normalized_key for fragment in DOCUMENT_PROFILE_KEY_FRAGMENTS):
                    profile[key] = value
        return profile

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
    def _canonical_row_text(*, row_data: dict[str, Any], fallback: str) -> str:
        parts = [f"{key}: {KnowledgeArtifactCompiler._render_value(value)}" for key, value in row_data.items() if value not in (None, "", [], {})]
        return "; ".join(parts) or fallback

    @staticmethod
    def _row_title(*, row_data: dict[str, Any], metadata: dict[str, Any]) -> str | None:
        for key in ("title", "section_title", "table_name", "case_name", "person_name", "entity_name", "area", "task_area"):
            value = row_data.get(key) or metadata.get(key)
            if value not in (None, "", [], {}):
                return str(value)[:255]
        return None

    @classmethod
    def _normalized_identifiers_for_row(cls, *, row_data: dict[str, Any], document_id: UUID) -> dict[str, Any]:
        text = cls._render_value(row_data)
        payload: dict[str, Any] = {"document_id": str(document_id)}
        identifiers = cls._identifiers_from_text(text)
        if identifiers:
            payload["identifiers"] = identifiers
        people = cls._person_names(row_data)
        if people:
            payload["people"] = people
        return payload

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
    def _person_names(row_data: dict[str, Any]) -> list[str]:
        values: list[Any] = []
        for key in ("person_name", "entity_name", "staff_names", "staff", "people", "assignees"):
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
            "row": metadata.get("row_start") or metadata.get("stt"),
        }

    @staticmethod
    def _render_value(value: Any) -> str:
        if isinstance(value, dict):
            return ", ".join(f"{key}={KnowledgeArtifactCompiler._render_value(item)}" for key, item in value.items() if item not in (None, "", [], {}))
        if isinstance(value, list | tuple | set):
            return ", ".join(KnowledgeArtifactCompiler._render_value(item) for item in value if item not in (None, "", [], {}))
        return str(value)

    @staticmethod
    def _snippet(text: str, limit: int) -> str:
        clean = " ".join((text or "").split())
        return clean[:limit]

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
        ordered: list[KnowledgeArtifactCreate] = []
        seen: set[str] = set()
        for artifact in artifacts:
            digest_input = "|".join([artifact.artifact_type, artifact.context_type, artifact.canonical_text, ",".join(artifact.source_chunk_ids)])
            digest = hashlib.sha1(digest_input.encode("utf-8")).hexdigest()
            if digest in seen:
                continue
            seen.add(digest)
            ordered.append(artifact)
        return ordered

