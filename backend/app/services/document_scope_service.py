from __future__ import annotations

import unicodedata
from dataclasses import asdict, dataclass
from typing import Any
from uuid import UUID

from app.models.document import Document
from app.repositories.documents import DocumentRepository
from app.services.llm_query_router import SemanticRoute


@dataclass(frozen=True)
class DocumentScopeResult:
    mode: str
    document_ids: list[UUID]
    matched_by: str | None = None
    confidence: float = 0.0
    reason: str = ""
    extracted_id_vb: list[str] | None = None
    extracted_codes: list[str] | None = None
    candidate_count: int = 0

    def model_dump(self) -> dict[str, Any]:
        data = asdict(self)
        data["document_ids"] = [str(document_id) for document_id in self.document_ids]
        return data

    @classmethod
    def none(
        cls,
        *,
        reason: str = "",
        extracted_id_vb: list[str] | None = None,
        extracted_codes: list[str] | None = None,
    ) -> "DocumentScopeResult":
        return cls(
            mode="none",
            document_ids=[],
            reason=reason,
            extracted_id_vb=extracted_id_vb or [],
            extracted_codes=extracted_codes or [],
        )


class DocumentScopeService:
    """Resolve document scope from an LLM semantic route before chunk retrieval."""

    def __init__(self, repository: DocumentRepository) -> None:
        self._repository = repository

    async def resolve_from_semantic_route(
        self,
        route: SemanticRoute,
        *,
        allowed_document_ids: set[UUID] | None = None,
        current_document_id: UUID | None = None,
    ) -> DocumentScopeResult:
        if route.document_reference == "current_document" and current_document_id is not None:
            if allowed_document_ids is None or current_document_id in allowed_document_ids:
                return DocumentScopeResult(
                    mode="hard",
                    document_ids=[current_document_id],
                    matched_by="llm_current_document",
                    confidence=max(0.85, route.confidence),
                    reason="LLM router resolved the query as a current-document reference.",
                    extracted_id_vb=route.id_vb_values,
                    extracted_codes=route.document_codes or route.document_identifiers,
                )

        if route.document_reference not in {"explicit_document", "current_document"}:
            return DocumentScopeResult.none(
                reason="LLM router did not request a single-document scope.",
                extracted_id_vb=route.id_vb_values,
                extracted_codes=route.document_codes or route.document_identifiers,
            )

        id_vb_values = _dedupe(route.id_vb_values)
        document_terms = _dedupe(
            [
                *route.document_identifiers,
                *route.document_codes,
                *route.document_titles,
            ]
        )
        if not id_vb_values and not document_terms:
            return DocumentScopeResult.none(
                reason="LLM router found a document reference but did not return document identifiers.",
                extracted_id_vb=id_vb_values,
                extracted_codes=document_terms,
            )

        candidates = await self._repository.find_document_scope_candidates(
            id_vb_values=id_vb_values,
            code_values=document_terms,
        )
        if allowed_document_ids is not None:
            candidates = [document for document in candidates if document.id in allowed_document_ids]
        if not candidates:
            return DocumentScopeResult.none(
                reason="No indexed document matched the LLM router document identifiers.",
                extracted_id_vb=id_vb_values,
                extracted_codes=document_terms,
            )

        scored = [
            (score, matched_by, document)
            for document in candidates
            for score, matched_by in [self._score_document(document, id_vb_values, document_terms)]
            if score > 0
        ]
        if not scored:
            return DocumentScopeResult.none(
                reason="Candidate documents did not pass normalized matching against LLM router identifiers.",
                extracted_id_vb=id_vb_values,
                extracted_codes=document_terms,
                candidate_count=len(candidates),
            )

        scored.sort(
            key=lambda item: (
                item[0],
                _sort_timestamp(getattr(item[2], "updated_at", None)),
            ),
            reverse=True,
        )
        best_score = scored[0][0]
        best = [
            (score, matched_by, document)
            for score, matched_by, document in scored
            if score == best_score
        ]
        best_document_ids = _unique_document_ids(document for _, _, document in best)
        if len(best_document_ids) == 1 and best_score >= 0.95:
            return DocumentScopeResult(
                mode="hard",
                document_ids=best_document_ids,
                matched_by=f"llm_{best[0][1]}",
                confidence=min(1.0, max(best_score, route.confidence)),
                reason="LLM router identifiers matched exactly one indexed document.",
                extracted_id_vb=id_vb_values,
                extracted_codes=document_terms,
                candidate_count=len(candidates),
            )

        return DocumentScopeResult(
            mode="soft",
            document_ids=best_document_ids,
            matched_by=f"llm_{best[0][1]}",
            confidence=min(1.0, max(best_score, route.confidence)),
            reason="LLM router identifiers matched multiple or lower-confidence documents.",
            extracted_id_vb=id_vb_values,
            extracted_codes=document_terms,
            candidate_count=len(candidates),
        )

    @staticmethod
    def _score_document(
        document: Document,
        id_vb_values: list[str],
        code_values: list[str],
    ) -> tuple[float, str | None]:
        metadata = dict(getattr(document, "document_metadata", None) or {})
        document_id_vb = str(metadata.get("id_vb") or "").strip()
        if document_id_vb and document_id_vb in set(id_vb_values):
            return 1.0, "id_vb"

        normalized_codes = {_normalize_identifier(value) for value in code_values}
        code_fields = (
            metadata.get("document_code"),
            metadata.get("ky_hieu"),
            metadata.get("doc_code"),
            metadata.get("so_ky_hieu"),
            metadata.get("code"),
        )
        for value in code_fields:
            if value is None:
                continue
            normalized_value = _normalize_identifier(str(value))
            if normalized_value in normalized_codes:
                return 0.98, "document_code"
            for code in normalized_codes:
                if code and code.isdigit() and normalized_value.startswith(f"{code}/"):
                    return 0.96, "document_code_prefix"

        title = str(getattr(document, "title", "") or "")
        normalized_title = _normalize_identifier(title)
        for code in normalized_codes:
            if code and code in normalized_title:
                return 0.95, "document_title"
        return 0.0, None


def _normalize_identifier(value: str) -> str:
    text = unicodedata.normalize("NFKC", value or "")
    text = text.replace("–", "-").replace("—", "-").replace("−", "-")
    text = "".join(
        char
        for char in unicodedata.normalize("NFD", text)
        if unicodedata.category(char) != "Mn"
    )
    return "".join(text.split()).casefold().strip(" .,:;\"'")


def _dedupe(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = str(value).strip()
        if not cleaned or cleaned in seen:
            continue
        result.append(cleaned)
        seen.add(cleaned)
    return result


def _unique_document_ids(documents: Any) -> list[UUID]:
    result: list[UUID] = []
    seen: set[UUID] = set()
    for document in documents:
        document_id = document.id
        if document_id in seen:
            continue
        result.append(document_id)
        seen.add(document_id)
    return result


def _sort_timestamp(value: Any) -> float:
    if value is None:
        return 0.0
    timestamp = getattr(value, "timestamp", None)
    if callable(timestamp):
        return float(timestamp())
    return 0.0
