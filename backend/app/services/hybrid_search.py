from __future__ import annotations

import inspect
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import UUID

from app.repositories.retrieval_logs import RetrievalLogRepository
from app.schemas.documents import (
    HybridSearchResponse,
    HybridSearchResult,
    KeywordSearchResponse,
    KeywordSearchResult,
    VectorSearchResponse,
    VectorSearchResult,
)
from app.services.access_control import AccessFilter
from app.services.keyword_search import KeywordSearchService
from app.services.query_intent_rules import is_field_detail_schema_query
from app.services.table_relationships import (
    analyze_person_area_membership_query,
    score_person_area_membership_match,
)
from app.services.vector_indexing_service import VectorIndexingService

DEFAULT_RRF_K = 60

logger = logging.getLogger(__name__)


def _normalize_metadata_value(value: str) -> str:
    normalized = unicodedata.normalize("NFD", value or "")
    normalized = "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")
    return re.sub(r"\s+", " ", normalized.casefold()).strip()



GENERIC_METADATA_TEXT_FIELDS = (
    "case_name",
    "row_text",
    "title",
    "table_name",
    "section_title",
    "article_title",
    "summary",
    "description",
    "objective",
    "goal",
    "area",
    "unit",
    "field_name",
    "relationship_name",
    "total_leave_benefit",
    "labor_code_benefit",
    "collective_agreement_benefit",
    "total_benefit",
    "base_benefit",
    "additional_benefit",
)

GENERIC_QUERY_STOPWORDS = {
    "anh", "ban", "cac", "cho", "co", "cua", "duoc", "gi", "hoi",
    "khong", "khi", "la", "lam", "nao", "nay", "neu", "nhu", "noi",
    "sao", "theo", "thi", "toi", "trong", "va", "ve", "voi", "xin",
    "bao", "nhieu", "may",
}


def _query_content_tokens(query: str) -> set[str]:
    normalized = _normalize_metadata_value(query)
    tokens = set(re.findall(r"[a-z0-9]+", normalized))
    return {
        token for token in tokens
        if len(token) > 1 and token not in GENERIC_QUERY_STOPWORDS
    }


def _metadata_text(metadata: dict[str, object]) -> str:
    parts: list[str] = []
    for key in GENERIC_METADATA_TEXT_FIELDS:
        value = metadata.get(key)
        if isinstance(value, list):
            parts.extend(str(item) for item in value)
        elif value is not None:
            parts.append(str(value))
    return " ".join(parts)

ENRICHMENT_BOOST_FIELDS = (
    "enrichment_summary",
    "enrichment_keywords",
    "document_code",
    "issued_date",
    "document_type",
    "structure_path",
    "keywords",
    "aliases",
    "legal_refs",
    "article_number",
    "responsible_unit",
    "deadline",
    "answerable_facts",
)

def _append_metadata_value(parts: list[str], value: object) -> None:
    if value is None:
        return
    if isinstance(value, dict):
        for nested in value.values():
            _append_metadata_value(parts, nested)
        return
    if isinstance(value, list | tuple | set):
        for item in value:
            _append_metadata_value(parts, item)
        return
    clean = str(value).strip()
    if clean:
        parts.append(clean)

def _enrichment_metadata_text(metadata: dict[str, object]) -> str:
    parts: list[str] = []
    nested = metadata.get("enrichment")
    if isinstance(nested, dict):
        for key in ENRICHMENT_BOOST_FIELDS:
            _append_metadata_value(parts, nested.get(key))
    for key in ENRICHMENT_BOOST_FIELDS:
        _append_metadata_value(parts, metadata.get(key))
    return " ".join(parts)

def enrichment_metadata_boost(query: str, metadata: dict[str, object]) -> float:
    query_tokens = _query_content_tokens(query)
    if not query_tokens:
        return 0.0
    enrichment_text = _normalize_metadata_value(_enrichment_metadata_text(metadata))
    if not enrichment_text:
        return 0.0
    candidate_tokens = set(re.findall(r"[a-z0-9]+", enrichment_text))
    overlap = query_tokens & candidate_tokens
    if not overlap:
        return 0.0
    coverage = len(overlap) / max(len(query_tokens), 1)
    return 3.0 + coverage * 8.0


def structured_row_metadata_boost(
    query: str,
    content: str,
    metadata: dict[str, object],
) -> float:
    """Boost structured row/section chunks using generic lexical overlap.

    This replaces domain-specific boosts such as a fixed list of leave-benefit
    phrases. Any chunk with row-like metadata can be boosted when its row text,
    title, table name, or extracted fact fields overlap with the user's content
    words. The retrieval behavior is therefore data-driven rather than tied to
    one document family.
    """

    query_tokens = _query_content_tokens(query)
    if not query_tokens:
        return 0.0

    chunk_type = str(metadata.get("chunk_type") or "")
    relationship_type = str(metadata.get("relationship_type") or "")
    try:
        confidence = float(metadata.get("confidence") or 1.0)
    except (TypeError, ValueError):
        confidence = 1.0
    if metadata.get("table_parse_warning") and confidence < 0.5:
        return 0.0

    row_like = (
        "row" in chunk_type
        or "table" in chunk_type
        or relationship_type.endswith("_benefit")
        or relationship_type.endswith("_row")
        or bool(metadata.get("case_name") or metadata.get("row_text"))
    )
    section_like = bool(metadata.get("unit") or metadata.get("section_path"))
    if not row_like and not section_like:
        return 0.0

    combined = _normalize_metadata_value(f"{content} {_metadata_text(metadata)}")
    candidate_tokens = set(re.findall(r"[a-z0-9]+", combined))
    if not candidate_tokens:
        return 0.0

    overlap = query_tokens & candidate_tokens
    if not overlap:
        return 0.0

    coverage = len(overlap) / max(len(query_tokens), 1)
    boost = 8.0 + coverage * 30.0
    if row_like:
        boost += 4.0
    if any(field in metadata for field in ("case_name", "row_text", "area", "unit")):
        boost += 4.0
    return boost


# Backward-compatible alias for older tests/call sites. The implementation is
# intentionally generic and does not encode any legal case or organization.
def legal_leave_metadata_boost(query: str, content: str, metadata: dict[str, object]) -> float:
    return structured_row_metadata_boost(query, content, metadata)

def schema_or_procedure_metadata_boost(
    query: str,
    metadata: dict[str, object],
    query_intent_rules: dict[str, Any] | None = None,
) -> float:
    """Boost structured schema/procedure metadata without document-specific rules."""

    normalized_query = _normalize_metadata_value(query)
    chunk_type = str(metadata.get("chunk_type") or "")
    table_name = str(metadata.get("table_name") or "")
    boost = 0.0
    for metadata_field, amount in (
        ("object_code", 8.0),
        ("field_name", 10.0),
        ("relationship_name", 9.0),
        ("table_name", 5.0),
        ("source_data", 2.0),
        ("data_type", 1.0),
    ):
        value = str(metadata.get(metadata_field) or "")
        if value and _normalize_metadata_value(value) in normalized_query:
            boost += amount

    schema_count_query = any(
        term in normalized_query
        for term in (
            "schema",
            "database",
            "table",
            "column",
            "field",
            "attribute",
            "layer",
            "object",
            "csdl",
            "co so du lieu",
            "bang",
            "cot",
            "truong",
            "thuoc tinh",
            "lop",
            "doi tuong",
        )
    ) and any(
        term in normalized_query
        for term in ("how many", "number of", "count", "may", "bao nhieu", "so luong")
    )
    field_detail_schema_query = _is_field_detail_schema_query(
        normalized_query,
        query_intent_rules,
    )
    if schema_count_query:
        if chunk_type == "attribute_table_schema":
            boost += 24.0
        elif chunk_type == "gis_relationship_schema":
            boost += 20.0
        elif chunk_type == "schema_object_summary":
            boost += 16.0
        elif chunk_type in {"table_parent", "table_complete", "table_rows"} and table_name:
            boost += 18.0 if field_detail_schema_query else 8.0
        if metadata.get("field_names") and field_detail_schema_query:
            boost += 4.0
        if metadata.get("relationship_name") or metadata.get("target_table"):
            boost += 6.0 if field_detail_schema_query else 16.0
    appendix_id = str(metadata.get("appendix_id") or "")
    if appendix_id:
        appendix_number = re.escape(appendix_id.lstrip("0") or appendix_id)
        if re.search(rf"phu luc\s*0?{appendix_number}\b", normalized_query):
            boost += 4.0
    if chunk_type == "schema_field_row" and field_detail_schema_query:
        boost += 4.0
    elif chunk_type == "schema_object_summary":
        boost += 3.0
    elif chunk_type == "procedure_table_row":
        boost += 4.0
    elif chunk_type == "deadline_index" and any(
        term in normalized_query for term in ("deadline", "thoi han", "khi nao", "hoan thanh")
    ):
        boost += 8.0
    elif chunk_type == "assignment_section" and any(
        term in normalized_query for term in ("deadline", "thoi han", "khi nao", "hoan thanh")
    ):
        boost += 3.0
    elif chunk_type == "gis_relationship_schema":
        boost += 5.0
    elif chunk_type == "attribute_table_schema":
        boost += 6.0 if schema_count_query else 2.0
    return boost


def _is_field_detail_schema_query(
    normalized_query: str,
    query_intent_rules: dict[str, Any] | None = None,
) -> bool:
    return is_field_detail_schema_query(normalized_query, query_intent_rules)


IDENTIFIER_EXACT_BOOST = 50.0
IDENTIFIER_METADATA_BOOST = 12.0
IDENTIFIER_MISS_PENALTY = 0.02


def _identifier_terms(query: str) -> list[str]:
    """Return exact lookup terms for short code/number queries.

    Queries like ``3113`` are not semantic questions; they are identifier lookups.
    Dense retrieval/reranking can otherwise pull chunks that are only topically related.
    """

    normalized = " ".join((query or "").split()).strip(" ?!.,;:")
    if not normalized:
        return []

    terms: list[str] = []
    # Bare numbers and common official-document codes, e.g. 3113 or 3113/EVN-KDMBD.
    if re.fullmatch(r"[0-9]{2,8}", normalized):
        terms.append(normalized)
    if re.fullmatch(r"[A-Z0-9][A-Z0-9._/-]{1,40}", normalized, flags=re.IGNORECASE):
        terms.append(normalized)

    # Also support a natural-language query that contains one strong identifier.
    for match in re.findall(r"\b[0-9]{3,8}(?:/[A-Z0-9._/-]+)?\b", normalized, flags=re.IGNORECASE):
        terms.append(match)

    ordered: list[str] = []
    seen: set[str] = set()
    for term in terms:
        key = term.casefold()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(term)
    return ordered


def _contains_identifier_term(text: str, terms: list[str]) -> bool:
    if not text or not terms:
        return False
    normalized = text.casefold()
    return any(term.casefold() in normalized for term in terms)


def identifier_exact_match_boost(query: str, content: str, metadata: dict[str, object]) -> float:
    """Boost chunks that literally contain a requested code/number."""

    terms = _identifier_terms(query)
    if not terms:
        return 0.0

    boost = 0.0
    if _contains_identifier_term(content, terms):
        boost += IDENTIFIER_EXACT_BOOST

    identifier_values: list[str] = []
    for key in ("identifiers", "doc_codes"):
        value = metadata.get(key)
        if isinstance(value, list):
            identifier_values.extend(str(item) for item in value)
        elif value is not None:
            identifier_values.append(str(value))
    if _contains_identifier_term(" ".join(identifier_values), terms):
        boost += IDENTIFIER_EXACT_BOOST

    # Metadata may hold title/context/source fields that include the identifier.
    metadata_text_parts: list[str] = []
    for key in (
        "document_context",
        "document_title",
        "section_path",
        "title",
        "source_file",
        "document_number",
        "reference_number",
        "so_van_ban",
        "citation",
    ):
        value = metadata.get(key)
        if isinstance(value, list):
            metadata_text_parts.extend(str(item) for item in value)
        elif value is not None:
            metadata_text_parts.append(str(value))
    if _contains_identifier_term(" ".join(metadata_text_parts), terms):
        boost += IDENTIFIER_METADATA_BOOST

    return boost


def is_identifier_lookup_query(query: str) -> bool:
    return bool(_identifier_terms(query))

HYBRID_DEPTH_MULTIPLIER = 3
SourceFlag = Literal["vector", "keyword", "lexical_exact"]


class HybridSearchError(RuntimeError):
    pass


@dataclass
class _FusedResult:
    chunk_id: object
    document_id: object
    fused_score: float = 0.0
    vector_score: float | None = None
    keyword_score: float | None = None
    content_preview: str = ""
    metadata: dict[str, object] = field(default_factory=dict)
    source_flags: list[SourceFlag] = field(default_factory=list)


@dataclass(frozen=True)
class HybridSearchRun:
    vector_response: VectorSearchResponse
    keyword_response: KeywordSearchResponse
    hybrid_response: HybridSearchResponse


class HybridSearchService:
    def __init__(
        self,
        *,
        vector_search_service: VectorIndexingService,
        keyword_search_service: KeywordSearchService,
        retrieval_log_repository: RetrievalLogRepository,
        rrf_k: int = DEFAULT_RRF_K,
    ) -> None:
        if rrf_k <= 0:
            raise ValueError("rrf_k must be greater than 0.")

        self._vector_search_service = vector_search_service
        self._keyword_search_service = keyword_search_service
        self._retrieval_log_repository = retrieval_log_repository
        self._rrf_k = rrf_k

    async def search(
        self,
        *,
        query: str,
        top_k: int,
        vector_weight: float,
        keyword_weight: float,
        save_log: bool = True,
        document_ids: set[UUID] | None = None,
        access_filter: AccessFilter | None = None,
        retrieval_enrichment_enabled: bool = False,
        query_intent_rules: dict[str, Any] | None = None,
    ) -> HybridSearchResponse:
        run = await self.run_search(
            query=query,
            top_k=top_k,
            vector_weight=vector_weight,
            keyword_weight=keyword_weight,
            save_log=save_log,
            document_ids=document_ids,
            access_filter=access_filter,
            retrieval_enrichment_enabled=retrieval_enrichment_enabled,
            query_intent_rules=query_intent_rules,
        )
        return run.hybrid_response

    async def run_search(
        self,
        *,
        query: str,
        top_k: int,
        vector_weight: float,
        keyword_weight: float,
        save_log: bool = True,
        document_ids: set[UUID] | None = None,
        access_filter: AccessFilter | None = None,
        retrieval_enrichment_enabled: bool = False,
        query_intent_rules: dict[str, Any] | None = None,
    ) -> HybridSearchRun:
        depth = top_k * HYBRID_DEPTH_MULTIPLIER

        try:
            try:
                if document_ids is None:
                    vector_response = await self._call_search_service(
                        self._vector_search_service,
                        query=query,
                        top_k=depth,
                        access_filter=access_filter,
                    )
                else:
                    vector_response = await self._call_search_service(
                        self._vector_search_service,
                        query=query,
                        top_k=depth,
                        document_ids={str(document_id) for document_id in document_ids},
                        access_filter=access_filter,
                    )
            except Exception:
                logger.exception(
                    "Vector search failed; continuing with keyword-only retrieval."
                )
                vector_response = VectorSearchResponse(
                    query=query,
                    top_k=depth,
                    results=[],
                )

            if document_ids is None:
                keyword_response = await self._call_search_service(
                    self._keyword_search_service,
                    query=query,
                    top_k=depth,
                    access_filter=access_filter,
                    retrieval_enrichment_enabled=retrieval_enrichment_enabled,
                )
            else:
                keyword_response = await self._call_search_service(
                    self._keyword_search_service,
                    query=query,
                    top_k=depth,
                    document_ids=document_ids,
                    access_filter=access_filter,
                    retrieval_enrichment_enabled=retrieval_enrichment_enabled,
                )
            hybrid_results = self.fuse_results(
                query=query,
                vector_results=vector_response.results,
                keyword_results=keyword_response.results,
                top_k=top_k,
                vector_weight=vector_weight,
                keyword_weight=keyword_weight,
                rrf_k=self._rrf_k,
                retrieval_enrichment_enabled=retrieval_enrichment_enabled,
                query_intent_rules=query_intent_rules,
            )
            response = HybridSearchResponse(
                query=query,
                top_k=top_k,
                vector_weight=vector_weight,
                keyword_weight=keyword_weight,
                results=hybrid_results,
            )

            run = HybridSearchRun(
                vector_response=vector_response,
                keyword_response=keyword_response,
                hybrid_response=response,
            )
            if save_log:
                await self._retrieval_log_repository.save_log(
                    query=query,
                    vector_results=vector_response.model_dump(mode="json"),
                    keyword_results=keyword_response.model_dump(mode="json"),
                    hybrid_results=response.model_dump(mode="json"),
                )
                await self._retrieval_log_repository.commit()
        except Exception as exc:
            await self._retrieval_log_repository.rollback()
            raise HybridSearchError("Failed to run hybrid search.") from exc

        return run

    @staticmethod
    async def _call_search_service(service, **kwargs):
        if kwargs.get("access_filter") is None:
            kwargs.pop("access_filter", None)
        parameters = inspect.signature(service.search).parameters
        accepts_var_kwargs = any(
            parameter.kind is inspect.Parameter.VAR_KEYWORD
            for parameter in parameters.values()
        )
        supported = (
            kwargs
            if accepts_var_kwargs
            else {key: value for key, value in kwargs.items() if key in parameters}
        )
        return await service.search(**supported)

    @staticmethod
    def fuse_results(
        *,
        query: str | None = None,
        vector_results: list[VectorSearchResult],
        keyword_results: list[KeywordSearchResult],
        top_k: int,
        vector_weight: float = 1.0,
        keyword_weight: float = 1.0,
        rrf_k: int = DEFAULT_RRF_K,
        retrieval_enrichment_enabled: bool = False,
        query_intent_rules: dict[str, Any] | None = None,
    ) -> list[HybridSearchResult]:
        fused: dict[str, _FusedResult] = {}

        for rank, result in enumerate(vector_results, start=1):
            key = str(result.chunk_id)
            item = fused.get(key)
            if item is None:
                item = _FusedResult(
                    chunk_id=result.chunk_id,
                    document_id=result.document_id,
                    content_preview=result.content_preview,
                    metadata=dict(result.metadata),
                )
                fused[key] = item

            item.vector_score = result.score
            item.fused_score += vector_weight * HybridSearchService._rrf_score(
                rank=rank,
                rrf_k=rrf_k,
            )
            HybridSearchService._append_source_flag(item, "vector")

        for rank, result in enumerate(keyword_results, start=1):
            key = str(result.chunk_id)
            item = fused.get(key)
            if item is None:
                item = _FusedResult(
                    chunk_id=result.chunk_id,
                    document_id=result.document_id,
                    content_preview=result.content_preview,
                    metadata=dict(result.metadata),
                )
                fused[key] = item

            item.keyword_score = result.score
            item.fused_score += keyword_weight * HybridSearchService._rrf_score(
                rank=rank,
                rrf_k=rrf_k,
            )
            HybridSearchService._append_source_flag(item, "keyword")
            if result.metadata.get("exact_match_terms"):
                HybridSearchService._append_source_flag(item, "lexical_exact")

        membership_query = analyze_person_area_membership_query(query or "")
        for item in fused.values():
            normalized_query = query or ""
            metadata_boost = schema_or_procedure_metadata_boost(
                normalized_query,
                item.metadata,
                query_intent_rules=query_intent_rules,
            )
            if metadata_boost > 0:
                item.fused_score += metadata_boost
                item.metadata = {**item.metadata, "metadata_exact_boost": metadata_boost}

            identifier_boost = identifier_exact_match_boost(
                normalized_query,
                item.content_preview,
                item.metadata,
            )
            if identifier_boost > 0:
                item.fused_score += identifier_boost
                item.metadata = {**item.metadata, "identifier_exact_boost": identifier_boost}
                HybridSearchService._append_source_flag(item, "lexical_exact")
            elif is_identifier_lookup_query(normalized_query):
                # For code-only lookups, keep vector-only topical matches below exact matches.
                item.fused_score -= IDENTIFIER_MISS_PENALTY

            structured_row_boost = structured_row_metadata_boost(
                normalized_query,
                item.content_preview,
                item.metadata,
            )
            if structured_row_boost > 0:
                item.fused_score += structured_row_boost
                item.metadata = {
                    **item.metadata,
                    "structured_row_boost": structured_row_boost,
                }
                HybridSearchService._append_source_flag(item, "lexical_exact")

            if retrieval_enrichment_enabled:
                enrichment_boost = enrichment_metadata_boost(normalized_query, item.metadata)
                if enrichment_boost > 0:
                    item.fused_score += enrichment_boost
                    item.metadata = {
                        **item.metadata,
                        "enrichment_boost": enrichment_boost,
                    }
                    HybridSearchService._append_source_flag(item, "lexical_exact")

            if membership_query is not None:
                boost = score_person_area_membership_match(
                    membership_query,
                    content=item.content_preview,
                    metadata=item.metadata,
                )
                if boost <= 0:
                    continue
                item.fused_score += boost
                item.metadata = {**item.metadata, "membership_boost": boost}

        ranked = sorted(
            fused.values(),
            key=lambda item: (-item.fused_score, str(item.chunk_id)),
        )

        return [
            HybridSearchResult(
                chunk_id=item.chunk_id,
                document_id=item.document_id,
                fused_score=item.fused_score,
                vector_score=item.vector_score,
                keyword_score=item.keyword_score,
                content_preview=item.content_preview,
                metadata=item.metadata,
                source_flags=item.source_flags,
            )
            for item in ranked[:top_k]
        ]

    @staticmethod
    def _rrf_score(*, rank: int, rrf_k: int) -> float:
        return 1.0 / (rrf_k + rank)

    @staticmethod
    def _append_source_flag(item: _FusedResult, source: SourceFlag) -> None:
        if source not in item.source_flags:
            item.source_flags.append(source)
