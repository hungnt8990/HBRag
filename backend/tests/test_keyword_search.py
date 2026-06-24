from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy.dialects import postgresql

from app.api.routes.search import get_keyword_search_service
from app.main import app
from app.schemas.documents import KeywordSearchResponse, KeywordSearchResult
from app.services.retrieval.retrieval_keyword_search import KEYWORD_QUERY_PARAM, KeywordSearchService

DOCUMENT_ID = UUID("66666666-6666-6666-6666-666666666666")
CHUNK_ID = UUID("77777777-7777-7777-7777-777777777777")


class FakeKeywordSearchService:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def search(self, *, query: str, top_k: int, document_ids=None) -> KeywordSearchResponse:
        self.calls.append({"query": query, "top_k": top_k})
        return KeywordSearchResponse(
            query=query,
            top_k=top_k,
            results=[
                KeywordSearchResult(
                    chunk_id=CHUNK_ID,
                    document_id=DOCUMENT_ID,
                    score=0.42,
                    content_preview="Keyword result preview",
                    metadata={"chunk_size": 1000},
                )
            ],
        )


def test_keyword_search_endpoint_returns_expected_schema() -> None:
    service = FakeKeywordSearchService()
    app.dependency_overrides[get_keyword_search_service] = lambda: service

    try:
        client = TestClient(app)
        response = client.post(
            "/api/search/keyword",
            json={"query": "postgres search", "top_k": 3},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload == {
        "query": "postgres search",
        "top_k": 3,
        "results": [
            {
                "chunk_id": str(CHUNK_ID),
                "document_id": str(DOCUMENT_ID),
                "score": 0.42,
                "content_preview": "Keyword result preview",
                "metadata": {"chunk_size": 1000},
            }
        ],
    }
    assert service.calls == [{"query": "postgres search", "top_k": 3}]


def test_keyword_search_rejects_empty_query() -> None:
    service = FakeKeywordSearchService()
    app.dependency_overrides[get_keyword_search_service] = lambda: service

    try:
        client = TestClient(app)
        response = client.post(
            "/api/search/keyword",
            json={"query": "   ", "top_k": 5},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422
    assert service.calls == []


def test_keyword_search_service_builds_query_safely() -> None:
    malicious_query = "rag'); DROP TABLE chunks; --"

    statement = KeywordSearchService.build_statement(query=malicious_query, top_k=5)
    compiled = statement.compile(dialect=postgresql.dialect())
    sql = str(compiled)

    assert "DROP TABLE" not in sql
    assert "plainto_tsquery('simple'," in sql
    assert f"%({KEYWORD_QUERY_PARAM})s" in sql
    assert compiled.params[KEYWORD_QUERY_PARAM] == malicious_query


def test_keyword_search_extracts_unicode_entity_terms_for_exact_matching() -> None:
    terms = KeywordSearchService._extract_exact_terms("Nguyá»…n Quang LÃ¢m tham gia máº£ng nÃ o?")

    assert any(term == "Nguyá»…n Quang LÃ¢m" for term in terms)


def test_keyword_search_statement_includes_exact_match_clause() -> None:
    statement = KeywordSearchService.build_statement(
        query="Nguyá»…n Quang LÃ¢m tham gia máº£ng nÃ o?",
        top_k=5,
    )
    compiled = statement.compile(dialect=postgresql.dialect())
    sql = str(compiled)

    assert "ILIKE" in sql

def test_keyword_search_disables_enrichment_matching_by_default() -> None:
    statement = KeywordSearchService.build_statement(query="123/QÄ-CPCIT", top_k=5)
    compiled = statement.compile(dialect=postgresql.dialect())
    sql = str(compiled)

    assert "to_tsvector('simple', chunks.content)" in sql
    assert "chunks.enriched_content ILIKE" not in sql

def test_keyword_search_can_match_enrichment_metadata_when_enabled() -> None:
    statement = KeywordSearchService.build_statement(
        query="123/QÄ-CPCIT",
        top_k=5,
        retrieval_enrichment_enabled=True,
    )
    compiled = statement.compile(dialect=postgresql.dialect())
    sql = str(compiled)

    assert "chunks.search_vector @@" in sql
    assert "chunks.enriched_content ILIKE" in sql
    assert compiled.params["metadata_1"] == "enrichment"
    assert "document_code" in compiled.params.values()
