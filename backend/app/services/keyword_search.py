from __future__ import annotations

import re
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
    or_,
    select,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chunk import Chunk
from app.schemas.documents import KeywordSearchResponse, KeywordSearchResult
from app.services.table_aware_chunking import extract_entities_from_text
from app.services.table_relationships import analyze_person_area_membership_query

CONTENT_PREVIEW_LIMIT = 300
KEYWORD_QUERY_PARAM = "keyword_query"
TS_CONFIG = literal_column("'simple'")
EXACT_MATCH_BOOST = 10.0
MAX_EXACT_TERMS = 6


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
    ) -> KeywordSearchResponse:
        exact_terms = self._extract_exact_terms(query)
        try:
            if document_ids is not None and not document_ids:
                return KeywordSearchResponse(query=query, top_k=top_k, results=[])
            statement = self.build_statement(query=query, top_k=top_k, document_ids=document_ids)
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
        return statement

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
        for term in [
            *membership_terms,
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
