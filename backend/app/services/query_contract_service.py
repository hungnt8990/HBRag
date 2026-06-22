from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Literal

DetectedIntent = Literal[
    "identifier_lookup",
    "person_assignment",
    "procedure_lookup",
    "policy_rule_lookup",
    "table_lookup",
    "general_summary",
]

OutputShape = Literal["short_fact", "table", "report"]
CitationRequirement = Literal["required", "preferred", "none"]


@dataclass(frozen=True)
class QueryContract:
    raw_query: str
    detected_intent: DetectedIntent
    filters: dict[str, Any] = field(default_factory=dict)
    target_contexts: list[str] = field(default_factory=list)
    preferred_artifact_types: list[str] = field(default_factory=list)
    output_shape: OutputShape = "short_fact"
    citation_requirement: CitationRequirement = "required"
    confidence_threshold: float = 0.45
    retrieval_budget: int = 6000
    allow_chunk_fallback: bool = True
    allow_neighbor_expansion: bool = True
    allow_graph_expansion: bool = False
    exact_terms: list[str] = field(default_factory=list)


DEFAULT_QUERY_CONTRACT_RULES: dict[str, Any] = {
    "identifier_patterns": [
        r"\b[0-9]{3,8}(?:/[\w._/-]+)?\b",
        r"\b[^\W\d_]{1,12}[0-9]{1,8}[\w._/-]*\b",
    ],
    "person_assignment_patterns": [
        r"\b(who|person|people|staff|assignee|owner|participant|phong|phong ban|don vi|mang|nhiem vu|tham gia)\b",
    ],
    "procedure_patterns": [
        r"\b(procedure|process|workflow|step|steps|application|dossier|fee|deadline|result|agency|thu tuc|ho so|le phi|thoi han|ket qua|co quan|bao cao|nhiem vu|don vi thuc hien|truoc ngay|han bao cao)\b",
    ],
    "policy_rule_patterns": [
        r"\b(policy|rule|condition|benefit|entitlement|obligation|exception|amount|days|salary|allowance|quyen loi|nghia vu|dieu kien|muc huong|so ngay|nghi|luong)\b",
    ],
    "table_patterns": [
        r"\b(table|row|column|list|total|matrix|bang|dong|cot|danh sach|liet ke|tong)\b",
    ],
    "table_output_patterns": [
        r"\b(table|matrix|bang|danh sach|liet ke|list|all rows)\b",
    ],
    "report_output_patterns": [
        r"\b(summary|summarize|report|overview|tong hop|tom tat|bao cao)\b",
    ],
}


INTENT_ARTIFACT_TYPES: dict[DetectedIntent, list[str]] = {
    "identifier_lookup": ["document_identity", "identifier_lookup", "document_profile"],
    "person_assignment": ["assignment_table_row", "person_assignment_artifact", "table_row_artifact"],
    "procedure_lookup": [
        "directive_task",
        "deadline_requirement",
        "implementation_plan",
        "procedure_artifact",
        "table_row_artifact",
        "document_profile",
    ],
    "policy_rule_lookup": ["legal_clause", "policy_rule_artifact", "table_row_artifact"],
    "table_lookup": [
        "assignment_table_row",
        "legal_clause",
        "directive_task",
        "table_row_artifact",
        "person_assignment_artifact",
        "policy_rule_artifact",
        "procedure_artifact",
    ],
    "general_summary": [
        "summary_block",
        "document_identity",
        "recipient_scope",
        "legal_clause",
        "directive_task",
        "document_profile",
        "policy_rule_artifact",
        "procedure_artifact",
        "table_row_artifact",
    ],
}


class QueryContractService:
    def __init__(self, *, rules: dict[str, Any] | None = None) -> None:
        self._rules = self._merge_rules(rules)

    def build_contract(
        self,
        query: str,
        *,
        confidence_threshold: float = 0.45,
        retrieval_budget: int = 6000,
        allow_chunk_fallback: bool = True,
        allow_neighbor_expansion: bool = True,
        allow_graph_expansion: bool = False,
    ) -> QueryContract:
        clean_query = " ".join((query or "").split()).strip()
        normalized_query = self._normalize(clean_query)
        exact_terms = self._exact_terms(clean_query)
        detected_intent = self._detect_intent(normalized_query, exact_terms=exact_terms)
        output_shape = self._output_shape(normalized_query, detected_intent=detected_intent)
        filters = self._filters(clean_query, exact_terms=exact_terms)
        target_contexts = self._target_contexts(detected_intent)
        return QueryContract(
            raw_query=clean_query,
            detected_intent=detected_intent,
            filters=filters,
            target_contexts=target_contexts,
            preferred_artifact_types=list(INTENT_ARTIFACT_TYPES[detected_intent]),
            output_shape=output_shape,
            citation_requirement="required",
            confidence_threshold=confidence_threshold,
            retrieval_budget=retrieval_budget,
            allow_chunk_fallback=allow_chunk_fallback,
            allow_neighbor_expansion=allow_neighbor_expansion,
            allow_graph_expansion=allow_graph_expansion,
            exact_terms=exact_terms,
        )

    def _detect_intent(
        self,
        normalized_query: str,
        *,
        exact_terms: list[str],
    ) -> DetectedIntent:
        if exact_terms and len(normalized_query.split()) <= 8:
            return "identifier_lookup"
        if self._matches("procedure_patterns", normalized_query):
            return "procedure_lookup"
        if self._matches("person_assignment_patterns", normalized_query) and self._looks_like_named_entity_question(normalized_query):
            return "person_assignment"
        if self._matches("policy_rule_patterns", normalized_query):
            return "policy_rule_lookup"
        if self._matches("table_patterns", normalized_query):
            return "table_lookup"
        return "general_summary"

    def _output_shape(self, normalized_query: str, *, detected_intent: DetectedIntent) -> OutputShape:
        if self._matches("table_output_patterns", normalized_query) or detected_intent in {"person_assignment", "table_lookup"}:
            return "table"
        if self._matches("report_output_patterns", normalized_query):
            return "report"
        return "short_fact"

    @staticmethod
    def _filters(query: str, *, exact_terms: list[str]) -> dict[str, Any]:
        filters: dict[str, Any] = {}
        if exact_terms:
            filters["exact_terms"] = exact_terms
        quoted_matches = re.findall(r'"([^"]+)"|\'([^\']+)\'', query)
        quoted = [value.strip() for pair in quoted_matches for value in pair if value.strip()]
        if quoted:
            filters["quoted_terms"] = quoted
        return filters

    @staticmethod
    def _target_contexts(intent: DetectedIntent) -> list[str]:
        return {
            "identifier_lookup": ["document", "identifier"],
            "person_assignment": ["people", "assignment", "table"],
            "procedure_lookup": ["procedure", "administrative"],
            "policy_rule_lookup": ["policy", "rule"],
            "table_lookup": ["table"],
            "general_summary": ["document"],
        }[intent]

    def _exact_terms(self, query: str) -> list[str]:
        terms: list[str] = []
        for pattern in self._rules["identifier_patterns"]:
            terms.extend(match.group(0) for match in re.finditer(pattern, query or "", flags=re.IGNORECASE))
        return self._dedupe(terms, limit=8)

    def _matches(self, rule_key: str, normalized_query: str) -> bool:
        return any(re.search(pattern, normalized_query, flags=re.IGNORECASE) for pattern in self._rules.get(rule_key, []))

    @staticmethod
    def _looks_like_named_entity_question(normalized_query: str) -> bool:
        words = normalized_query.split()
        return len(words) >= 3

    @staticmethod
    def _normalize(value: str) -> str:
        normalized = unicodedata.normalize("NFD", value or "")
        stripped = "".join(char for char in normalized if unicodedata.category(char) != "Mn")
        return re.sub(r"\s+", " ", stripped.casefold()).strip()

    @staticmethod
    def _dedupe(values: list[str], *, limit: int) -> list[str]:
        ordered: list[str] = []
        seen: set[str] = set()
        for value in values:
            clean = " ".join(str(value or "").split()).strip(" ?!.,;:")
            if len(clean) < 2:
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
    def _merge_rules(rules: dict[str, Any] | None) -> dict[str, list[str]]:
        merged: dict[str, list[str]] = {
            key: list(value)
            for key, value in DEFAULT_QUERY_CONTRACT_RULES.items()
        }
        for key, value in dict(rules or {}).items():
            if key not in merged:
                continue
            if isinstance(value, str):
                merged[key] = [value]
            elif isinstance(value, list):
                merged[key] = [str(item) for item in value if str(item).strip()]
        return merged
