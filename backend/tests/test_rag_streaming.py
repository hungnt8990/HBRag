import asyncio
import json
from uuid import UUID

from fastapi.testclient import TestClient

from app.api.routes.chat import get_rag_answer_service
from app.main import app
from app.schemas.chat import RagCitationResponse
from app.services.llms.llm_fake_llm import FakeLLM
from app.services.rag.rag_answer_service import RagStreamEvent

SESSION_ID = UUID("aaaaaaaa-1111-1111-1111-aaaaaaaaaaaa")
USER_MESSAGE_ID = UUID("bbbbbbbb-2222-2222-2222-bbbbbbbbbbbb")
ASSISTANT_MESSAGE_ID = UUID("cccccccc-3333-3333-3333-cccccccccccc")
DOCUMENT_ID = UUID("dddddddd-4444-4444-4444-dddddddddddd")
CHUNK_ID = UUID("eeeeeeee-5555-5555-5555-eeeeeeeeeeee")


class FakeStreamingRagService:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def answer_stream(
        self,
        *,
        query: str,
        session_id: UUID | None,
        top_k: int,
        candidate_k: int,
        current_user=None,
        document_ids=None,
        memory_context=None,
        session_context=None,
        session_summary=None,
        answer_mode=None,
        answer_style=None,
        max_context_chars=6000,
        use_graph=False,
        graph_expansion_depth=1,
        graph_expansion_limit=20,
    ):
        self.calls.append(
            {
                "query": query,
                "session_id": session_id,
                "top_k": top_k,
                "candidate_k": candidate_k,
            }
        )
        yield RagStreamEvent(
            event="metadata",
            data={
                "session_id": str(SESSION_ID),
                "user_message_id": str(USER_MESSAGE_ID),
            },
        )
        yield RagStreamEvent(event="token", data={"delta": "Hello"})
        yield RagStreamEvent(event="token", data={"delta": " world"})
        yield RagStreamEvent(
            event="citations",
            data=[
                {
                    "citation_index": 1,
                    "chunk_id": str(CHUNK_ID),
                    "document_id": str(DOCUMENT_ID),
                    "document_title": "Labor Policy Handbook",
                    "file_name": "labor-policy.pdf",
                    "chunk_index": 3,
                    "quote": "Grounded answer source.",
                    "article_number": "10",
                    "article_title": "Nguyen tac ap dung",
                    "chapter_title": "Chuong I",
                    "page_number": 2,
                    "source_flags": ["vector", "graph"],
                    "metadata": {
                        "article_number": "10",
                        "article_title": "Nguyen tac ap dung",
                        "chapter_title": "Chuong I",
                        "page_number": 2,
                        "source_flags": ["vector", "graph"],
                    },
                }
            ],
        )
        yield RagStreamEvent(
            event="done",
            data={"assistant_message_id": str(ASSISTANT_MESSAGE_ID)},
        )


def _parse_sse(body: str) -> list[tuple[str, object]]:
    events: list[tuple[str, object]] = []
    for block in body.strip().split("\n\n"):
        if not block.strip():
            continue
        event_name = ""
        data_line = ""
        for line in block.splitlines():
            if line.startswith("event:"):
                event_name = line[len("event:"):].strip()
            elif line.startswith("data:"):
                data_line = line[len("data:"):].strip()
        events.append((event_name, json.loads(data_line) if data_line else None))
    return events


def test_fake_llm_stream_generate_yields_multiple_deltas() -> None:
    async def run_test() -> None:
        llm = FakeLLM()
        user_prompt = (
            "Question:\nWhat is RAG?\n\n"
            "Context:\n[1] RAG uses context.\n[2] Citations point to chunks."
        )

        deltas = [
            delta
            async for delta in llm.stream_generate(
                system_prompt="system",
                user_prompt=user_prompt,
            )
        ]

        assert len(deltas) > 1
        assert "".join(deltas) == await llm.generate(
            system_prompt="system",
            user_prompt=user_prompt,
        )

    asyncio.run(run_test())


def test_stream_endpoint_emits_metadata_token_citations_done() -> None:
    service = FakeStreamingRagService()
    app.dependency_overrides[get_rag_answer_service] = lambda: service

    try:
        client = TestClient(app)
        response = client.post(
            "/api/chat/rag/stream",
            json={"query": "How should RAG cite chunks?", "top_k": 1, "candidate_k": 5},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = _parse_sse(response.text)
    event_names = [name for name, _ in events]
    assert event_names == ["metadata", "token", "token", "citations", "done"]

    metadata = dict(events[0][1])
    assert metadata["session_id"] == str(SESSION_ID)
    assert metadata["user_message_id"] == str(USER_MESSAGE_ID)

    tokens = [data["delta"] for name, data in events if name == "token"]
    assert tokens == ["Hello", " world"]

    citations = events[3][1]
    assert isinstance(citations, list)
    assert citations[0]["chunk_id"] == str(CHUNK_ID)
    assert citations[0]["document_title"] == "Labor Policy Handbook"
    assert citations[0]["file_name"] == "labor-policy.pdf"
    assert citations[0]["article_number"] == "10"
    assert citations[0]["article_title"] == "Nguyen tac ap dung"
    assert citations[0]["chapter_title"] == "Chuong I"
    assert citations[0]["page_number"] == 2
    assert citations[0]["source_flags"] == ["vector", "graph"]
    parsed_citation = RagCitationResponse.model_validate(citations[0])
    assert parsed_citation.document_title == "Labor Policy Handbook"
    assert parsed_citation.file_name == "labor-policy.pdf"
    assert parsed_citation.article_number == "10"
    assert parsed_citation.source_flags == ["vector", "graph"]

    done = dict(events[4][1])
    assert done["assistant_message_id"] == str(ASSISTANT_MESSAGE_ID)


def test_stream_endpoint_rejects_empty_query() -> None:
    service = FakeStreamingRagService()
    app.dependency_overrides[get_rag_answer_service] = lambda: service

    try:
        client = TestClient(app)
        response = client.post(
            "/api/chat/rag/stream",
            json={"query": "   ", "top_k": 5, "candidate_k": 20},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422
    assert service.calls == []
