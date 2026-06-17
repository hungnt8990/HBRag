from __future__ import annotations

import re
import unicodedata
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Float,
    Select,
    bindparam,
    case,
    cast,
    func,
    literal_column,
    not_,
    or_,
    select,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.chunk import Chunk
from app.schemas.documents import KeywordSearchResponse, KeywordSearchResult
from app.services.access_control import AccessFilter
from app.services.table_aware_chunking import extract_entities_from_text
from app.services.table_relationships import analyze_person_area_membership_query

CONTENT_PREVIEW_LIMIT = 300
KEYWORD_QUERY_PARAM = "keyword_query"
TS_CONFIG = literal_column("'simple'")
EXACT_MATCH_BOOST = 10.0
MAX_EXACT_TERMS = 6


QUERY_CONTENT_STOPWORDS = {
    "anh", "bạn", "các", "cho", "có", "của", "đang", "đây", "đó",
    "được", "gì", "hãy", "hỏi", "không", "khi", "là", "làm", "nào",
    "này", "nêu", "nếu", "nhé", "như", "nói", "sao", "theo", "thì",
    "tôi", "trong", "và", "về", "với", "xin", "bao", "nhiêu", "mấy",
}


def _strip_vietnamese_accents(value: str) -> str:
    normalized = unicodedata.normalize("NFD", value or "")
    stripped = "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")
    return stripped.replace("Đ", "D").replace("đ", "d")


def _content_exact_terms(query: str, *, max_terms: int = 6) -> list[str]:
    """Extract reusable exact-match phrases from any natural-language query.

    The previous implementation had domain phrases for one leave-benefit table.
    This version is schema-neutral: it derives compact n-grams only from the
    user's own content words, so it can help retrieve short table/section rows in
    any corpus without adding code for each new document type.
    """

    tokens = re.findall(r"[\wÀ-ỹĐđ]+", query or "", flags=re.UNICODE)
    content_tokens: list[str] = []
    for token in tokens:
        normalized = _strip_vietnamese_accents(token).casefold()
        if len(normalized) <= 1 or normalized in QUERY_CONTENT_STOPWORDS:
            continue
        content_tokens.append(token)

    terms: list[str] = []
    seen: set[str] = set()
    for size in range(min(5, len(content_tokens)), 1, -1):
        for index in range(0, len(content_tokens) - size + 1):
            phrase = " ".join(content_tokens[index : index + size]).strip()
            key = _strip_vietnamese_accents(phrase).casefold()
            if key in seen:
                continue
            seen.add(key)
            terms.append(phrase)
            if len(terms) >= max_terms:
                return terms
    for token in content_tokens:
        key = _strip_vietnamese_accents(token).casefold()
        if key in seen:
            continue
        seen.add(key)
        terms.append(token)
        if len(terms) >= max_terms:
            break
    return terms


class KeywordSearchError(RuntimeError):
    pass


class KeywordSearchService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def search(
        self,
        *,
        query: str,
        top_k: int,
        document_ids: set[UUID] | None = None,
        access_filter: AccessFilter | None = None,
    ) -> KeywordSearchResponse:
        exact_terms = self._extract_exact_terms(query)
        try:
            if document_ids is not None and not document_ids:
                return KeywordSearchResponse(query=query, top_k=top_k, results=[])
            statement = self.build_statement(
                query=query,
                top_k=top_k,
                document_ids=document_ids,
                access_filter=access_filter,
            )
            result = await self._session.execute(statement)
            rows = result.mappings().all()
        except Exception as exc:
            raise KeywordSearchError("Failed to run keyword search.") from exc

        return KeywordSearchResponse(
            query=query,
            top_k=top_k,
            results=[
                KeywordSearchResult(
                    chunk_id=row["chunk_id"],
                    document_id=row["document_id"],
                    score=float(row["score"] or 0.0),
                    content_preview=self._preview(row["content"]),
                    metadata=self._metadata(
                        row["metadata"],
                        content=row["content"],
                        exact_terms=exact_terms,
                    ),
                )
                for row in rows
            ],
        )

    @staticmethod
    def build_statement(
        *,
        query: str,
        top_k: int,
        document_ids: set[UUID] | None = None,
        access_filter: AccessFilter | None = None,
    ) -> Select[tuple[Any, ...]]:
        query_param = bindparam(KEYWORD_QUERY_PARAM, value=query)
        ts_query = func.plainto_tsquery(TS_CONFIG, query_param)
        rank = func.ts_rank_cd(Chunk.search_vector, ts_query).label("score")
        exact_terms = KeywordSearchService._extract_exact_terms(query)

        exact_clauses = []
        exact_score = cast(0.0, Float)
        for index, term in enumerate(exact_terms):
            param_name = f"keyword_exact_{index}"
            exact_param = bindparam(param_name, value=f"%{term}%")
            clause = Chunk.content.ilike(exact_param)
            exact_clauses.append(clause)
            exact_score = exact_score + case((clause, EXACT_MATCH_BOOST), else_=0.0)
        exact_score = exact_score.label("exact_score")
        combined_score = (rank + exact_score).label("combined_score")

        statement = (
            select(
                Chunk.id.label("chunk_id"),
                Chunk.document_id.label("document_id"),
                combined_score.label("score"),
                Chunk.content.label("content"),
                Chunk.chunk_metadata.label("metadata"),
            )
            .where(
                or_(
                    Chunk.search_vector.is_not(None) & Chunk.search_vector.op("@@")(ts_query),
                    *exact_clauses,
                ),
                or_(
                    Chunk.chunk_metadata["indexable"].as_boolean().is_(None),
                    Chunk.chunk_metadata["indexable"].as_boolean().is_(True),
                ),
                or_(
                    Chunk.chunk_metadata["embedding_enabled"].as_boolean().is_(None),
                    Chunk.chunk_metadata["embedding_enabled"].as_boolean().is_(True),
                ),
                or_(
                    Chunk.chunk_metadata["chunk_type"].as_string().is_(None),
                    Chunk.chunk_metadata["chunk_type"].as_string().not_in(
                        ["administrative_footer", "header_footer", "footer", "parse_error"]
                    ),
                ),
            )
            .order_by(
                exact_score.desc(),
                combined_score.desc(),
                Chunk.document_id.asc(),
                Chunk.chunk_index.asc(),
            )
            .limit(top_k)
        )
        if document_ids is not None:
            statement = statement.where(Chunk.document_id.in_(document_ids))
        if access_filter is not None:
            statement = statement.where(*KeywordSearchService._access_clauses(access_filter))
        return statement

    @staticmethod
    def _access_clauses(access_filter: AccessFilter) -> list[Any]:
        if settings.access_read_all_documents:
            return []
        access = Chunk.chunk_metadata["access"]
        allowed_classifications = [
            name
            for name, rank in settings.access_classification_rank.items()
            if rank <= access_filter.clearance_rank
        ]
        org_ids = set(access_filter.descendant_org_ids)
        if access_filter.organization_id:
            org_ids.add(access_filter.organization_id)
        clauses: list[Any] = [
            or_(
                access["classification"].as_string().is_(None),
                access["classification"].as_string().in_(allowed_classifications),
            ),
            or_(
                access["denied_user_ids"].is_(None),
                not_(access["denied_user_ids"].contains([access_filter.subject_user_id])),
            ),
        ]
        for key, values in (
            ("denied_org_ids", org_ids),
            ("denied_role_names", access_filter.role_names),
            ("denied_group_codes", access_filter.group_codes),
        ):
            for value in values:
                clauses.append(or_(access[key].is_(None), not_(access[key].contains([value]))))

        should = [
            access["scope"].as_string().is_(None),
            access["scope"].as_string().in_(settings.access_corp_wide_scopes),
        ]
        if org_ids:
            should.append(access["owner_org_id"].as_string().in_(sorted(org_ids)))
            for org_id in org_ids:
                should.append(access["allowed_org_ids"].contains([org_id]))
        should.append(access["allowed_user_ids"].contains([access_filter.subject_user_id]))
        for key, values in (
            ("allowed_role_names", access_filter.role_names),
            ("allowed_group_codes", access_filter.group_codes),
            ("business_domains", access_filter.business_domains),
            ("project_codes", access_filter.project_codes),
        ):
            for value in values:
                should.append(access[key].contains([value]))
        if access_filter.org_path:
            should.append(access["allowed_org_paths"].contains([access_filter.org_path]))
        clauses.append(or_(*should))
        return clauses

    @staticmethod
    def _preview(content: str) -> str:
        return content[:CONTENT_PREVIEW_LIMIT]

    @staticmethod
    def _metadata(
        metadata: dict[str, Any] | None,
        *,
        content: str,
        exact_terms: list[str],
    ) -> dict[str, object]:
        payload = dict(metadata or {})
        matched_terms = [
            term for term in exact_terms if term.casefold() in (content or "").casefold()
        ]
        if matched_terms:
            payload["exact_match_terms"] = matched_terms
        return payload

    @staticmethod
    def _extract_exact_terms(query: str) -> list[str]:
        quoted_terms = re.findall(r'"([^"]+)"|\'([^\']+)\'', query)
        flattened_quotes = [
            term.strip()
            for pair in quoted_terms
            for term in pair
            if term.strip()
        ]

        entity_terms = extract_entities_from_text(query)
        code_terms = re.findall(r"\b[A-Z0-9][A-Z0-9._/-]{1,}\b", query)
        membership_query = analyze_person_area_membership_query(query)
        membership_terms = []
        if membership_query is not None:
            membership_terms = [
                term
                for term in (
                    membership_query.person_candidate,
                    membership_query.area_candidate,
                )
                if term
            ]

        ordered_terms: list[str] = []
        content_terms = _content_exact_terms(query)

        for term in [
            *membership_terms,
            *content_terms,
            query.strip(),
            *flattened_quotes,
            *entity_terms,
            *code_terms,
        ]:
            normalized = " ".join(term.split()).strip(" ?!.,;:")
            if len(normalized) < 2:
                continue
            if normalized.lower() in {item.lower() for item in ordered_terms}:
                continue
            ordered_terms.append(normalized)
            if len(ordered_terms) >= MAX_EXACT_TERMS:
                break
        return ordered_terms
