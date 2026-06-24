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
        document_profile = self._document_profile_artifact(document=document, chunks=chunks, docling_metadata=docling_metadata or {})
        artifacts.append(document_profile)
        artifacts.append(self._document_summary_artifact(document_profile))
        artifacts.extend(self._identifier_artifacts(document=document, chunks=chunks))
        artifacts.extend(self._row_artifacts(document=document, chunks=chunks))
        artifacts.extend(self._training_decision_artifacts(document=document, chunks=chunks))
        artifacts.extend(self._qa_packet_artifacts(document=document, chunks=chunks))
        artifacts.extend(self._procedure_artifacts(document=document, chunks=chunks))
        artifacts.extend(self._policy_rule_artifacts(document=document, chunks=chunks))
        artifacts.extend(self._legal_evidence_artifacts(document=document, chunks=chunks))
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

    @staticmethod
    def _document_summary_artifact(profile_artifact: KnowledgeArtifactCreate) -> KnowledgeArtifactCreate:
        data = dict(profile_artifact.structured_data or {})
        return KnowledgeArtifactCreate(
            artifact_type="document_summary_artifact",
            context_type="document",
            title=profile_artifact.title,
            canonical_text=profile_artifact.canonical_text,
            source_chunk_ids=list(profile_artifact.source_chunk_ids),
            structured_data={
                "doc_code": data.get("doc_code") or data.get("document_code") or data.get("ky_hieu"),
                "issued_date": data.get("issued_date") or data.get("ngay_vb"),
                "issuing_org": data.get("issuing_org") or data.get("issuer") or data.get("noi_ban_hanh"),
                "document_title": data.get("title") or data.get("document_title") or data.get("trich_yeu"),
                "source_type": data.get("source_type"),
                "summary": data.get("summary") or data.get("source_summary") or data.get("subject"),
            },
            normalized_identifiers=dict(profile_artifact.normalized_identifiers or {}),
            citation_map=dict(profile_artifact.citation_map or {}),
            confidence_score=max(float(profile_artifact.confidence_score or 0.0), 0.78),
            extraction_method="deterministic",
            status=profile_artifact.status,
        )

    def _training_decision_artifacts(
        self,
        *,
        document: Document,
        chunks: list[Chunk],
    ) -> list[KnowledgeArtifactCreate]:
        text = "\n".join([document.title or "", str(getattr(document, "document_metadata", {}) or {}), *[chunk.content for chunk in chunks[:20]]])
        if not re.search(r"đào\s*tạo|dao\s*tao|training", text, flags=re.IGNORECASE):
            return []
        metadata = dict(getattr(document, "document_metadata", None) or {})
        data = self._training_decision_data(document=document, metadata=metadata, chunks=chunks, text=text)
        if not any(data.get(key) for key in ("course_name", "provider", "start_date", "end_date", "location", "funding_source")):
            return []
        canonical = "Training decision: " + self._render_value(data)
        source_chunks = [chunk for chunk in chunks if str(dict(chunk.chunk_metadata or {}).get("chunk_type") or "") in {"document_summary", "legal_clause", "table_parent", "table_row"}]
        return [
            KnowledgeArtifactCreate(
                artifact_type="training_decision",
                context_type="document",
                title=str(data.get("course_name") or document.title),
                canonical_text=canonical,
                source_chunk_ids=[str(chunk.id) for chunk in source_chunks[:8]],
                structured_data=data,
                normalized_identifiers={
                    "document_id": str(document.id),
                    "document_code": data.get("document_code"),
                    "course_name": data.get("course_name"),
                },
                citation_map={"document_id": str(document.id), "chunks": [self._chunk_citation(chunk) for chunk in source_chunks[:8]]},
                confidence_score=0.84,
                extraction_method="deterministic",
                status="ready",
            )
        ]

    def _qa_packet_artifacts(
        self,
        *,
        document: Document,
        chunks: list[Chunk],
    ) -> list[KnowledgeArtifactCreate]:
        artifacts: list[KnowledgeArtifactCreate] = []
        for chunk in chunks:
            metadata = dict(chunk.chunk_metadata or {})
            if metadata.get("indexable") is False or metadata.get("embedding_enabled") is False:
                continue
            packet = self._qa_packet_for_chunk(document=document, chunk=chunk, metadata=metadata)
            if packet is None:
                continue
            artifacts.append(
                KnowledgeArtifactCreate(
                    artifact_type="qa_packet",
                    context_type="qa",
                    title=str(packet["question"])[:255],
                    canonical_text=f"Q: {packet['question']}\nA: {packet['answer']}",
                    source_chunk_ids=[str(chunk.id)],
                    structured_data=packet,
                    normalized_identifiers={"document_id": str(document.id), "identifiers": self._identifiers_from_text(chunk.content)},
                    citation_map={"document_id": str(document.id), "chunks": [self._chunk_citation(chunk)]},
                    confidence_score=float(metadata.get("confidence") or 0.8),
                    extraction_method="deterministic",
                    status="ready",
                )
            )
        return artifacts

    def _training_decision_data(
        self,
        *,
        document: Document,
        metadata: dict[str, Any],
        chunks: list[Chunk],
        text: str,
    ) -> dict[str, Any]:
        dates = [self._normalize_date_iso(value) for value in self._dates_from_text(text)]
        dates = [value for value in dates if value]
        table_rows = [chunk for chunk in chunks if str(dict(chunk.chunk_metadata or {}).get("chunk_type") or "") == "table_row"]
        data = {
            "artifact_type": "training_decision",
            "document_code": metadata.get("document_code") or metadata.get("doc_code") or metadata.get("ky_hieu"),
            "course_name": self._extract_course_name(text) or metadata.get("trich_yeu") or document.title,
            "provider": self._extract_first_regex(text, r"(?:đơn vị đào tạo|don vi dao tao|provider)\s*[:\-]?\s*([^\n.;]+)"),
            "start_date": dates[0] if dates else None,
            "end_date": dates[-1] if len(dates) > 1 else (dates[0] if dates else None),
            "location": self._extract_first_regex(text, r"(?:địa điểm|dia diem|tại|tai)\s*[:\-]?\s*([^\n.;]{2,80})"),
            "participants_count": len(table_rows) or self._extract_int_regex(text, r"cử\s+(\d{1,3})\s+cán bộ|cu\s+(\d{1,3})\s+can bo"),
            "funding_source": self._extract_first_regex(text, r"kinh\s*phí[^\n.;]{0,80}?do\s+([^\n.;]{2,60}?)\s+(?:chi trả|chi tra|thanh toán|thanh toan|đảm bảo|dam bao)"),
        }
        return {key: value for key, value in data.items() if value not in (None, "", [], {})}

    @staticmethod
    def _qa_packet_for_chunk(*, document: Document, chunk: Chunk, metadata: dict[str, Any]) -> dict[str, Any] | None:
        chunk_type = str(metadata.get("chunk_type") or "").casefold()
        evidence = [{"chunk_id": str(chunk.id), "source_span": metadata.get("source_span")}]
        if chunk_type == "legal_clause" and metadata.get("article_number"):
            question = f"Điều {metadata.get('article_number')} quy định nội dung gì?"
            answer = str(metadata.get("summary") or KnowledgeArtifactCompiler._snippet(chunk.content, 500))
        elif chunk_type == "table_parent":
            table_name = metadata.get("table_name") or metadata.get("table_title") or "bảng này"
            question = f"{table_name} có bao nhiêu dòng?"
            answer = f"{metadata.get('row_count')} dòng." if metadata.get("row_count") is not None else KnowledgeArtifactCompiler._snippet(chunk.content, 500)
        elif chunk_type == "table_row":
            row_key = metadata.get("row_key") or metadata.get("person_name") or metadata.get("email")
            if metadata.get("person_name") and metadata.get("department"):
                question = f"{metadata.get('person_name')} thuộc phòng/đơn vị nào?"
                answer = str(metadata.get("department"))
            elif metadata.get("email") and metadata.get("person_name"):
                question = f"Ai có email {metadata.get('email')}?"
                answer = str(metadata.get("person_name"))
            elif row_key:
                question = f"Thông tin của {row_key} là gì?"
                answer = KnowledgeArtifactCompiler._snippet(chunk.content, 700)
            else:
                question = f"Dòng {metadata.get('row_index') or chunk.chunk_index} của bảng chứa thông tin gì?"
                answer = KnowledgeArtifactCompiler._snippet(chunk.content, 700)
        elif chunk_type == "document_summary":
            question = "Văn bản này có mục đích chính là gì?"
            answer = KnowledgeArtifactCompiler._snippet(chunk.content, 700)
        else:
            question = f"Chunk {chunk.chunk_index} của {document.title} cung cấp thông tin gì?"
            answer = KnowledgeArtifactCompiler._snippet(chunk.content, 700)
        answer = " ".join(str(answer or "").split()).strip()
        if not answer:
            return None
        return {"question": question, "answer": answer, "evidence": evidence}

    @staticmethod
    def _extract_course_name(text: str) -> str | None:
        return KnowledgeArtifactCompiler._extract_first_regex(
            text,
            r"(?:khóa|khoa|lớp|lop)\s+đào\s*tạo\s+([^\n.;]{3,120})",
        )

    @staticmethod
    def _extract_first_regex(text: str, pattern: str) -> str | None:
        match = re.search(pattern, text or "", flags=re.IGNORECASE)
        if not match:
            return None
        for group in match.groups():
            clean = " ".join(str(group or "").split()).strip(" -:;.,")
            if clean:
                return clean
        return None

    @staticmethod
    def _extract_int_regex(text: str, pattern: str) -> int | None:
        match = re.search(pattern, text or "", flags=re.IGNORECASE)
        if not match:
            return None
        for group in match.groups():
            if group and str(group).isdigit():
                return int(group)
        return None

    @staticmethod
    def _normalize_date_iso(value: str) -> str | None:
        clean = " ".join(str(value or "").split()).strip()
        match = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", clean)
        if match:
            return clean
        match = re.fullmatch(r"(\d{1,2})[/-](\d{1,2})[/-](\d{2}|\d{4})", clean)
        if not match:
            return None
        day, month, year = match.groups()
        if len(year) == 2:
            year = "20" + year
        return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"
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
            artifacts.append(
                KnowledgeArtifactCreate(
                    artifact_type="table_evidence_artifact",
                    context_type="table",
                    title=self._row_title(row_data=row_data, metadata=metadata),
                    canonical_text=canonical_text,
                    source_chunk_ids=[str(chunk.id)],
                    structured_data={
                        "table_title": metadata.get("table_title") or metadata.get("table_name"),
                        "logical_table_id": metadata.get("logical_table_id"),
                        "row_index": metadata.get("row_index") or metadata.get("row_start"),
                        "row_key": metadata.get("row_key"),
                        "row": row_data,
                    },
                    normalized_identifiers=self._normalized_identifiers_for_row(row_data=row_data, document_id=document.id),
                    citation_map={"document_id": str(document.id), "chunks": [self._chunk_citation(chunk)]},
                    confidence_score=float(metadata.get("confidence") or 0.82),
                    extraction_method="deterministic",
                    status="ready",
                )
            )
            person_artifact = self._person_assignment_artifact(document=document, chunk=chunk, row_data=row_data)
            if person_artifact is not None:
                artifacts.append(person_artifact)
                artifacts.append(
                    KnowledgeArtifactCreate(
                        artifact_type="assignment_artifact",
                        context_type="assignment",
                        title=person_artifact.title,
                        canonical_text=person_artifact.canonical_text,
                        source_chunk_ids=list(person_artifact.source_chunk_ids),
                        structured_data=dict(person_artifact.structured_data or {}),
                        normalized_identifiers=dict(person_artifact.normalized_identifiers or {}),
                        citation_map=dict(person_artifact.citation_map or {}),
                        confidence_score=float(person_artifact.confidence_score or 0.88),
                        extraction_method="deterministic",
                        status=person_artifact.status,
                    )
                )
            if len(artifacts) >= self._config.max_table_row_artifacts:
                break
        return artifacts

    def _legal_evidence_artifacts(
        self,
        *,
        document: Document,
        chunks: list[Chunk],
    ) -> list[KnowledgeArtifactCreate]:
        artifacts: list[KnowledgeArtifactCreate] = []
        for chunk in chunks:
            metadata = dict(chunk.chunk_metadata or {})
            if str(metadata.get("chunk_type") or "").casefold() != "legal_clause":
                continue
            article_number = metadata.get("article_number")
            if not article_number:
                continue
            legal_data = {
                "article": article_number,
                "article_title": metadata.get("article_title"),
                "chapter": metadata.get("chapter_title") or metadata.get("chapter_number"),
                "section": metadata.get("section_title") or metadata.get("section_number"),
                "clause": metadata.get("clause_number"),
                "point": metadata.get("point_label"),
                "legal_path": metadata.get("legal_path"),
                "rule": self._snippet(chunk.content, 1400),
                "source_span": metadata.get("source_span"),
            }
            canonical = f"Legal evidence from {document.title}: {self._render_value(legal_data)}"
            artifacts.append(
                KnowledgeArtifactCreate(
                    artifact_type="legal_evidence_artifact",
                    context_type="policy",
                    title=str(metadata.get("section_title") or f"Điều {article_number}"),
                    canonical_text=canonical,
                    source_chunk_ids=[str(chunk.id)],
                    structured_data=legal_data,
                    normalized_identifiers={
                        "document_id": str(document.id),
                        "article_number": str(article_number),
                        "identifiers": self._identifiers_from_text(chunk.content),
                    },
                    citation_map={"document_id": str(document.id), "chunks": [self._chunk_citation(chunk)]},
                    confidence_score=0.86,
                    extraction_method="deterministic",
                    status="ready",
                )
            )
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
                if any(fragment in normalized_key for fragment in ("number", "code", "date", "issuer", "agency", "type", "title", "subject")):
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
            "source_span": metadata.get("source_span"),
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

