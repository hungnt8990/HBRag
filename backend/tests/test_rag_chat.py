import asyncio
from types import SimpleNamespace
from uuid import UUID

from fastapi.testclient import TestClient

from app.api.routes.chat import get_rag_answer_service
from app.main import app
from app.repositories.chat import CitationCreate
from app.schemas.chat import RagChatResponse, RagCitationResponse
from app.schemas.documents import RerankSearchResponse, RerankSearchResult
from app.services.llms.fake_llm import FakeLLM
from app.services.rag_answer_service import RagAnswerService

SESSION_ID = UUID("aaaaaaaa-1111-1111-1111-aaaaaaaaaaaa")
USER_MESSAGE_ID = UUID("bbbbbbbb-2222-2222-2222-bbbbbbbbbbbb")
ASSISTANT_MESSAGE_ID = UUID("cccccccc-3333-3333-3333-cccccccccccc")
DOCUMENT_ID = UUID("dddddddd-4444-4444-4444-dddddddddddd")
CHUNK_ID_1 = UUID("eeeeeeee-5555-5555-5555-eeeeeeeeeeee")
CHUNK_ID_2 = UUID("ffffffff-6666-6666-6666-ffffffffffff")


class FakeChatRepository:
    def __init__(self) -> None:
        self.sessions: list[SimpleNamespace] = []
        self.messages: list[SimpleNamespace] = []
        self.citations: list[CitationCreate] = []
        self.committed = False
        self.rolled_back = False
        self.chunks = {
            CHUNK_ID_1: SimpleNamespace(
                id=CHUNK_ID_1,
                document_id=DOCUMENT_ID,
                chunk_index=3,
                content="Python RAG systems use retrieved context for grounded answers.",
                chunk_metadata={
                    "start_char": 0,
                    "end_char": 61,
                    "article_number": "10",
                    "article_title": "Nguyen tac ap dung",
                    "chapter_title": "Chuong I",
                },
                document=SimpleNamespace(
                    title="Labor Policy Handbook",
                    files=[SimpleNamespace(filename="labor-policy.pdf")],
                ),
            ),
            CHUNK_ID_2: SimpleNamespace(
                id=CHUNK_ID_2,
                document_id=DOCUMENT_ID,
                chunk_index=4,
                content="Citations should point back to source chunks.",
                chunk_metadata={"start_char": 62, "end_char": 107, "page_number": 2},
                document=SimpleNamespace(
                    title="Labor Policy Handbook",
                    files=[SimpleNamespace(filename="labor-policy.pdf")],
                ),
            ),
        }

    async def create_session(self, *, title: str) -> SimpleNamespace:
        session = SimpleNamespace(id=SESSION_ID, title=title)
        self.sessions.append(session)
        return session

    async def get_session(self, session_id: UUID) -> SimpleNamespace | None:
        if session_id == SESSION_ID:
            return SimpleNamespace(id=SESSION_ID, title="Existing session")
        return None

    async def create_message(
        self,
        *,
        session_id: UUID,
        role: str,
        content: str,
    ) -> SimpleNamespace:
        message_id = USER_MESSAGE_ID if role == "user" else ASSISTANT_MESSAGE_ID
        message = SimpleNamespace(
            id=message_id,
            session_id=session_id,
            role=role,
            content=content,
        )
        self.messages.append(message)
        return message

    async def get_chunks_by_ids(self, chunk_ids: list[UUID]) -> list[SimpleNamespace]:
        return [self.chunks[chunk_id] for chunk_id in chunk_ids if chunk_id in self.chunks]

    async def create_citations(
        self,
        *,
        message_id: UUID,
        citations: list[CitationCreate],
    ) -> list[SimpleNamespace]:
        self.citations = list(citations)
        return [
            SimpleNamespace(
                id=UUID("12345678-1234-1234-1234-123456789abc"),
                message_id=message_id,
                chunk_id=citation.chunk_id,
                document_id=citation.document_id,
                quote=citation.quote,
                page_number=citation.page_number,
            )
            for citation in citations
        ]

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        self.rolled_back = True


class FakeRerankingService:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def search(
        self,
        *,
        query: str,
        top_k: int,
        candidate_k: int,
        session_id: UUID | None = None,
        document_ids=None,
        use_graph: bool = False,
        graph_expansion_depth: int = 1,
        graph_expansion_limit: int = 20,
    ) -> RerankSearchResponse:
        self.calls.append(
            {
                "query": query,
                "top_k": top_k,
                "candidate_k": candidate_k,
                "session_id": session_id,
                "use_graph": use_graph,
            }
        )
        return RerankSearchResponse(
            query=query,
            top_k=top_k,
            candidate_k=candidate_k,
            results=[
                RerankSearchResult(
                    chunk_id=CHUNK_ID_1,
                    document_id=DOCUMENT_ID,
                    rerank_score=1.0,
                    fused_score=0.03,
                    vector_score=0.9,
                    keyword_score=0.5,
                    content_preview="Python RAG systems use retrieved context.",
                    metadata={"start_char": 0},
                    source_flags=["vector", "keyword"],
                ),
                RerankSearchResult(
                    chunk_id=CHUNK_ID_2,
                    document_id=DOCUMENT_ID,
                    rerank_score=0.5,
                    fused_score=0.02,
                    vector_score=None,
                    keyword_score=0.4,
                    content_preview="Citations should point back to source chunks.",
                    metadata={"start_char": 62},
                    source_flags=["keyword"],
                ),
            ],
        )


class FakeRagAnswerService:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def answer(
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
        max_context_chars: int = 6000,
        use_graph: bool = False,
        graph_expansion_depth: int = 1,
        graph_expansion_limit: int = 20,
    ) -> RagChatResponse:
        self.calls.append(
            {
                "query": query,
                "session_id": session_id,
                "top_k": top_k,
                "candidate_k": candidate_k,
                "use_graph": use_graph,
            }
        )
        return RagChatResponse(
            session_id=SESSION_ID,
            user_message_id=USER_MESSAGE_ID,
            assistant_message_id=ASSISTANT_MESSAGE_ID,
            answer="Generated from provided context. Relevant citations: [1]",
            citations=[
                RagCitationResponse(
                    citation_index=1,
                    chunk_id=CHUNK_ID_1,
                    document_id=DOCUMENT_ID,
                    document_title="Labor Policy Handbook",
                    file_name="labor-policy.pdf",
                    chunk_index=3,
                    quote="Python RAG systems use retrieved context for grounded answers.",
                    article_number="10",
                    article_title="Nguyen tac ap dung",
                    chapter_title="Chuong I",
                    page_number=None,
                    source_flags=["vector", "keyword"],
                    metadata={
                        "start_char": 0,
                        "end_char": 61,
                        "article_number": "10",
                        "article_title": "Nguyen tac ap dung",
                        "chapter_title": "Chuong I",
                        "source_type": "vector",
                        "source_flags": ["vector", "keyword"],
                    },
                )
            ],
        )


def test_fake_llm_returns_deterministic_answer() -> None:
    async def run_test() -> None:
        llm = FakeLLM()
        system_prompt = "system"
        user_prompt = (
            "Question:\nWhat is RAG?\n\n"
            "Context:\n"
            "[1] RAG uses context.\n"
            "[2] Citations point to chunks."
        )

        first = await llm.generate(system_prompt=system_prompt, user_prompt=user_prompt)
        second = await llm.generate(system_prompt=system_prompt, user_prompt=user_prompt)

        assert first == second
        assert "Generated from provided context" in first
        assert "[1] [2]" in first

    asyncio.run(run_test())


def test_rag_service_creates_session_messages_and_citations() -> None:
    async def run_test() -> None:
        repository = FakeChatRepository()
        reranking_service = FakeRerankingService()
        service = RagAnswerService(
            chat_repository=repository,  # type: ignore[arg-type]
            reranking_service=reranking_service,  # type: ignore[arg-type]
            llm_provider=FakeLLM(),
        )

        response = await service.answer(
            query="How should RAG cite chunks?",
            session_id=None,
            top_k=2,
            candidate_k=10,
        )

        assert response.session_id == SESSION_ID
        assert response.user_message_id == USER_MESSAGE_ID
        assert response.assistant_message_id == ASSISTANT_MESSAGE_ID
        assert "Generated from provided context" in response.answer
        assert "[1] [2]" in response.answer
        assert repository.sessions[0].title == "How should RAG cite chunks?"
        assert [(message.role, message.content) for message in repository.messages] == [
            ("user", "How should RAG cite chunks?"),
            ("assistant", response.answer),
        ]
        assert len(repository.citations) == 2
        assert repository.citations[0].chunk_id == CHUNK_ID_1
        assert repository.citations[0].document_id == DOCUMENT_ID
        assert repository.citations[0].quote == (
            "Python RAG systems use retrieved context for grounded answers."
        )
        assert repository.citations[1].page_number == 2
        assert response.citations[0].citation_index == 1
        assert response.citations[0].document_title == "Labor Policy Handbook"
        assert response.citations[0].file_name == "labor-policy.pdf"
        assert response.citations[0].chunk_index == 3
        assert response.citations[0].article_number == "10"
        assert response.citations[0].article_title == "Nguyen tac ap dung"
        assert response.citations[0].chapter_title == "Chuong I"
        assert response.citations[1].citation_index == 2
        assert response.citations[1].chunk_index == 4
        assert response.citations[1].page_number == 2
        assert reranking_service.calls == [
            {
                "query": "How should RAG cite chunks?",
                "top_k": 2,
                "candidate_k": 10,
                "session_id": SESSION_ID,
                "use_graph": False,
            }
        ]
        assert repository.committed is True
        assert repository.rolled_back is False

    asyncio.run(run_test())


def test_rag_endpoint_rejects_empty_query() -> None:
    service = FakeRagAnswerService()
    app.dependency_overrides[get_rag_answer_service] = lambda: service

    try:
        client = TestClient(app)
        response = client.post(
            "/api/chat/rag",
            json={"query": "   ", "top_k": 5, "candidate_k": 20},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422
    assert service.calls == []


def test_rag_endpoint_response_schema_is_correct() -> None:
    service = FakeRagAnswerService()
    app.dependency_overrides[get_rag_answer_service] = lambda: service

    try:
        client = TestClient(app)
        response = client.post(
            "/api/chat/rag",
            json={"query": "How should RAG cite chunks?", "top_k": 1, "candidate_k": 5},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload == {
        "session_id": str(SESSION_ID),
        "user_message_id": str(USER_MESSAGE_ID),
        "assistant_message_id": str(ASSISTANT_MESSAGE_ID),
        "answer": "Generated from provided context. Relevant citations: [1]",
        "citations": [
            {
                "citation_index": 1,
                "chunk_id": str(CHUNK_ID_1),
                "document_id": str(DOCUMENT_ID),
                "document_title": "Labor Policy Handbook",
                "file_name": "labor-policy.pdf",
                "chunk_index": 3,
                "quote": "Python RAG systems use retrieved context for grounded answers.",
                "article_number": "10",
                "article_title": "Nguyen tac ap dung",
                "chapter_title": "Chuong I",
                "page_number": None,
                "source_flags": ["vector", "keyword"],
                "metadata": {
                    "start_char": 0,
                    "end_char": 61,
                    "article_number": "10",
                    "article_title": "Nguyen tac ap dung",
                    "chapter_title": "Chuong I",
                    "source_type": "vector",
                    "source_flags": ["vector", "keyword"],
                },
            }
        ],
    }
    assert service.calls == [
        {
            "query": "How should RAG cite chunks?",
            "session_id": None,
            "top_k": 1,
            "candidate_k": 5,
            "use_graph": False,
        }
    ]


def test_rag_citation_includes_document_and_article_metadata() -> None:
    async def run_test() -> None:
        repository = FakeChatRepository()
        reranking_service = FakeRerankingService()
        service = RagAnswerService(
            chat_repository=repository,  # type: ignore[arg-type]
            reranking_service=reranking_service,  # type: ignore[arg-type]
            llm_provider=FakeLLM(),
        )

        response = await service.answer(
            query="How should RAG cite chunks?",
            session_id=None,
            top_k=2,
            candidate_k=10,
        )

        first = response.citations[0]
        assert first.document_title == "Labor Policy Handbook"
        assert first.file_name == "labor-policy.pdf"
        assert first.article_number == "10"
        assert first.article_title == "Nguyen tac ap dung"
        assert first.chapter_title == "Chuong I"
        assert first.source_flags == ["vector", "keyword"]

    asyncio.run(run_test())


def test_rag_citation_quote_uses_exact_chunk_text_with_500_char_limit() -> None:
    async def run_test() -> None:
        repository = FakeChatRepository()
        repository.chunks[CHUNK_ID_1].content = "A" * 520
        reranking_service = FakeRerankingService()
        service = RagAnswerService(
            chat_repository=repository,  # type: ignore[arg-type]
            reranking_service=reranking_service,  # type: ignore[arg-type]
            llm_provider=FakeLLM(),
        )

        response = await service.answer(
            query="How should RAG cite chunks?",
            session_id=None,
            top_k=1,
            candidate_k=10,
        )

        assert response.citations[0].quote == "A" * 500
        assert repository.citations[0].quote == "A" * 500

    asyncio.run(run_test())


def test_system_prompt_for_mode_selects_expected_prompt() -> None:
    from app.services.rag_answer_service import (
        EXTRACTIVE_PROMPT,
        GENERATIVE_PROMPT,
        HYBRID_PROMPT,
        system_prompt_for_mode,
    )

    assert system_prompt_for_mode("generative") == GENERATIVE_PROMPT
    assert system_prompt_for_mode("extractive") == EXTRACTIVE_PROMPT
    assert system_prompt_for_mode("hybrid") == HYBRID_PROMPT
    # Default and unknown values fall back to hybrid.
    assert system_prompt_for_mode(None) == HYBRID_PROMPT
    assert system_prompt_for_mode("unknown") == HYBRID_PROMPT
    # Extraction mode must not summarize or rewrite.
    assert "Do not summarize" in EXTRACTIVE_PROMPT
    assert "Do not rewrite legal wording" in EXTRACTIVE_PROMPT


def test_build_system_prompt_includes_policy_explainer_instructions() -> None:
    from app.services.rag_answer_service import build_system_prompt

    prompt = build_system_prompt(answer_mode="hybrid", answer_style="policy_explainer")
    assert "Provide a concise answer" in prompt or "concise answer first" in prompt
    assert "related cases" in prompt.lower()
    assert "table rows" in prompt.lower()
    assert "exact numbers" in prompt.lower()
    assert "Vietnamese administrative" in prompt

    concise = build_system_prompt(answer_mode="hybrid", answer_style="concise")
    assert "1-2 sentences" in concise

    detailed = build_system_prompt(answer_mode="generative", answer_style="detailed")
    assert "thorough" in detailed.lower()


def test_citation_response_maps_lexical_exact_to_public_keyword_flag() -> None:
    from uuid import uuid4

    from app.services.rag_answer_service import ContextChunk, RagAnswerService

    chunk = SimpleNamespace(
        id=uuid4(),
        document_id=uuid4(),
        chunk_index=1,
        content="exact lexical match",
        chunk_metadata={},
    )
    service = RagAnswerService(
        chat_repository=SimpleNamespace(),  # type: ignore[arg-type]
        reranking_service=SimpleNamespace(),  # type: ignore[arg-type]
        llm_provider=SimpleNamespace(),  # type: ignore[arg-type]
    )

    citation = service._build_citation_response(
        context_chunk=ContextChunk(
            citation_index=1,
            chunk=chunk,
            source_type="primary",
            source_flags=["keyword", "lexical_exact"],
        ),
        quote="exact lexical match",
    )

    assert citation.source_flags == ["keyword"]
    assert "lexical_exact" not in citation.source_flags
    assert citation.metadata["raw_source_flags"] == ["keyword", "lexical_exact"]
    assert citation.metadata["match_type"] == "lexical_exact"


def test_neighbor_expansion_fetches_same_article_chunks() -> None:
    import asyncio
    from uuid import uuid4

    from app.services.rag_answer_service import ContextChunk, RagAnswerService

    document_id = uuid4()
    primary = SimpleNamespace(
        id=uuid4(),
        document_id=document_id,
        chunk_index=0,
        content="Điều 10 nội dung chính.",
        chunk_metadata={"article_number": "10"},
    )
    neighbor_a = SimpleNamespace(
        id=uuid4(),
        document_id=document_id,
        chunk_index=1,
        content="Bảng quy định kèm theo Điều 10.",
        chunk_metadata={"article_number": "10"},
    )
    neighbor_b = SimpleNamespace(
        id=uuid4(),
        document_id=document_id,
        chunk_index=2,
        content="Ghi chú bổ sung của Điều 10.",
        chunk_metadata={"article_number": "10"},
    )

    class FakeRepoWithNeighbors:
        async def get_neighbor_chunks(self, *, document_id, article_number, exclude_ids):
            return [neighbor_a, neighbor_b]

    service = RagAnswerService(
        chat_repository=FakeRepoWithNeighbors(),  # type: ignore[arg-type]
        reranking_service=SimpleNamespace(),  # type: ignore[arg-type]
        llm_provider=SimpleNamespace(),  # type: ignore[arg-type]
    )

    primary_chunks = [ContextChunk(citation_index=1, chunk=primary)]

    expanded = asyncio.run(
        service._expand_with_neighbors(
            query="Dieu 10",
            context_chunks=primary_chunks,
            max_context_chars=1000,
        )
    )

    assert len(expanded) == 3
    assert expanded[0].source_type == "primary"
    assert expanded[1].source_type == "neighbor"
    assert expanded[2].source_type == "neighbor"
    assert expanded[1].citation_index == 2
    assert expanded[2].citation_index == 3


def test_neighbor_expansion_respects_max_context_chars() -> None:
    import asyncio
    from uuid import uuid4

    from app.services.rag_answer_service import ContextChunk, RagAnswerService

    document_id = uuid4()
    primary = SimpleNamespace(
        id=uuid4(),
        document_id=document_id,
        chunk_index=0,
        content="A" * 100,
        chunk_metadata={"article_number": "10"},
    )
    big_neighbor = SimpleNamespace(
        id=uuid4(),
        document_id=document_id,
        chunk_index=1,
        content="B" * 500,
        chunk_metadata={"article_number": "10"},
    )

    class FakeRepoWithNeighbors:
        async def get_neighbor_chunks(self, *, document_id, article_number, exclude_ids):
            return [big_neighbor]

    service = RagAnswerService(
        chat_repository=FakeRepoWithNeighbors(),  # type: ignore[arg-type]
        reranking_service=SimpleNamespace(),  # type: ignore[arg-type]
        llm_provider=SimpleNamespace(),  # type: ignore[arg-type]
    )

    expanded = asyncio.run(
        service._expand_with_neighbors(
            query="Dieu 10",
            context_chunks=[ContextChunk(citation_index=1, chunk=primary)],
            max_context_chars=200,
        )
    )

    # Primary alone fits, big neighbor would exceed cap → not added.
    assert len(expanded) == 1
    assert expanded[0].source_type == "primary"


def test_rag_endpoint_accepts_answer_mode() -> None:
    service = FakeRagAnswerService()
    app.dependency_overrides[get_rag_answer_service] = lambda: service

    try:
        client = TestClient(app)
        response = client.post(
            "/api/chat/rag",
            json={
                "query": "How should RAG cite chunks?",
                "top_k": 1,
                "candidate_k": 5,
                "answer_mode": "extractive",
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200


def test_rag_endpoint_rejects_invalid_answer_mode() -> None:
    client = TestClient(app)
    response = client.post(
        "/api/chat/rag",
        json={"query": "hello", "answer_mode": "invalid-mode"},
    )
    assert response.status_code == 422


def test_deduplicate_context_chunks_removes_duplicates() -> None:
    from uuid import uuid4

    from app.services.rag_answer_service import ContextChunk, RagAnswerService

    doc_id = uuid4()
    same_quote = "Kết hôn - Nghỉ 03 ngày hưởng nguyên lương"

    chunk_a = SimpleNamespace(
        id=uuid4(),
        document_id=doc_id,
        chunk_index=0,
        content=same_quote,
        chunk_metadata={"article_number": "10"},
    )
    # Identical content, different id (e.g. neighbor duplicate).
    chunk_b = SimpleNamespace(
        id=uuid4(),
        document_id=doc_id,
        chunk_index=1,
        content="  Kết hôn -   Nghỉ 03 ngày hưởng nguyên lương  ",
        chunk_metadata={"article_number": "10"},
    )
    # Truly different content.
    chunk_c = SimpleNamespace(
        id=uuid4(),
        document_id=doc_id,
        chunk_index=2,
        content="Tang lễ cha mẹ - Nghỉ 03 ngày.",
        chunk_metadata={"article_number": "10"},
    )

    service = RagAnswerService(
        chat_repository=SimpleNamespace(),  # type: ignore[arg-type]
        reranking_service=SimpleNamespace(),  # type: ignore[arg-type]
        llm_provider=SimpleNamespace(),  # type: ignore[arg-type]
    )

    chunks = [
        ContextChunk(citation_index=1, chunk=chunk_a, source_type="primary"),
        ContextChunk(citation_index=2, chunk=chunk_b, source_type="neighbor"),
        ContextChunk(citation_index=3, chunk=chunk_c, source_type="neighbor"),
    ]

    deduped = service._deduplicate_context_chunks(chunks)

    # chunk_b is a duplicate of chunk_a by normalized content.
    assert len(deduped) == 2
    assert deduped[0].chunk.id == chunk_a.id
    assert deduped[1].chunk.id == chunk_c.id
    # Citation indexes reassigned sequentially.
    assert [item.citation_index for item in deduped] == [1, 2]


def test_deduplicate_context_chunks_removes_repeated_chunk_id() -> None:
    from uuid import uuid4

    from app.services.rag_answer_service import ContextChunk, RagAnswerService

    doc_id = uuid4()
    chunk = SimpleNamespace(
        id=uuid4(),
        document_id=doc_id,
        chunk_index=0,
        content="Nội dung điều 10.",
        chunk_metadata={"article_number": "10"},
    )

    service = RagAnswerService(
        chat_repository=SimpleNamespace(),  # type: ignore[arg-type]
        reranking_service=SimpleNamespace(),  # type: ignore[arg-type]
        llm_provider=SimpleNamespace(),  # type: ignore[arg-type]
    )

    chunks = [
        ContextChunk(citation_index=1, chunk=chunk, source_type="primary"),
        ContextChunk(citation_index=2, chunk=chunk, source_type="neighbor"),
    ]

    deduped = service._deduplicate_context_chunks(chunks)
    assert len(deduped) == 1
    assert deduped[0].citation_index == 1


def test_deduplicated_prompt_has_no_repeated_lines() -> None:
    from uuid import uuid4

    from app.services.rag_answer_service import ContextChunk, RagAnswerService

    doc_id = uuid4()
    same = "Kết hôn - Nghỉ 03 ngày hưởng nguyên lương"
    chunk_a = SimpleNamespace(
        id=uuid4(),
        document_id=doc_id,
        chunk_index=0,
        content=same,
        chunk_metadata={"article_number": "10"},
    )
    chunk_b = SimpleNamespace(
        id=uuid4(),
        document_id=doc_id,
        chunk_index=1,
        content=same,
        chunk_metadata={"article_number": "10"},
    )

    service = RagAnswerService(
        chat_repository=SimpleNamespace(),  # type: ignore[arg-type]
        reranking_service=SimpleNamespace(),  # type: ignore[arg-type]
        llm_provider=SimpleNamespace(),  # type: ignore[arg-type]
    )

    deduped = service._deduplicate_context_chunks(
        [
            ContextChunk(citation_index=1, chunk=chunk_a),
            ContextChunk(citation_index=2, chunk=chunk_b),
        ]
    )
    prompt = service._build_user_prompt(query="q", context_chunks=deduped)
    assert prompt.count(same) == 1


def test_table_neighbor_expansion_prefers_matching_rows_and_headers() -> None:
    from uuid import uuid4

    from app.services.rag_answer_service import ContextChunk, RagAnswerService

    document_id = uuid4()
    primary = SimpleNamespace(
        id=uuid4(),
        document_id=document_id,
        chunk_index=3,
        content=(
            "TABLE_ROW table_id=pdf_p1_1 page=1 row=1 | Name: Nguyen Quang Lam | "
            "Area: Infrastructure"
        ),
        chunk_metadata={"table_id": "pdf_p1_1", "chunk_type": "table_row"},
    )
    header = SimpleNamespace(
        id=uuid4(),
        document_id=document_id,
        chunk_index=1,
        content="TABLE_HEADER table_id=pdf_p1_1 page=1 | Name | Area",
        chunk_metadata={"table_id": "pdf_p1_1", "chunk_type": "table_header"},
    )
    related = SimpleNamespace(
        id=uuid4(),
        document_id=document_id,
        chunk_index=4,
        content=(
            "TABLE_ROW table_id=pdf_p1_1 page=1 row=2 | Name: Nguyen Quang Lam | "
            "Area: Data"
        ),
        chunk_metadata={"table_id": "pdf_p1_1", "chunk_type": "table_row"},
    )
    unrelated = SimpleNamespace(
        id=uuid4(),
        document_id=document_id,
        chunk_index=5,
        content="TABLE_ROW table_id=pdf_p1_1 page=1 row=3 | Name: Tran Van An | Area: QA",
        chunk_metadata={"table_id": "pdf_p1_1", "chunk_type": "table_row"},
    )

    class FakeRepoWithTableNeighbors:
        async def get_table_chunks(self, **kwargs):
            return [header, related, unrelated]

    service = RagAnswerService(
        chat_repository=FakeRepoWithTableNeighbors(),  # type: ignore[arg-type]
        reranking_service=SimpleNamespace(),  # type: ignore[arg-type]
        llm_provider=SimpleNamespace(),  # type: ignore[arg-type]
    )

    expanded = asyncio.run(
        service._expand_with_neighbors(
            query="Nguyen Quang Lam tham gia mang nao?",
            context_chunks=[ContextChunk(citation_index=1, chunk=primary)],
            max_context_chars=1000,
        )
    )

    contents = [item.chunk.content for item in expanded]
    assert any("TABLE_HEADER" in content for content in contents)
    assert any("Area: Data" in content for content in contents)
    assert all("Tran Van An" not in content for content in contents)


def test_entity_summary_expansion_uses_table_ids_metadata() -> None:
    from uuid import uuid4

    from app.services.rag_answer_service import ContextChunk, RagAnswerService

    document_id = uuid4()
    summary_chunk = SimpleNamespace(
        id=uuid4(),
        document_id=document_id,
        chunk_index=10,
        content="ENTITY_SUMMARY entity=Nguyen Quang Lam\nRows:\n- ...",
        chunk_metadata={
            "chunk_type": "entity_summary",
            "table_ids": ["pdf_p1_1", "pdf_p1_2"],
        },
    )
    table_a_row = SimpleNamespace(
        id=uuid4(),
        document_id=document_id,
        chunk_index=11,
        content=(
            "TABLE_ROW table_id=pdf_p1_1 page=1 row=6 | Name: Nguyen Quang Lam | "
            "Area: Platform AI"
        ),
        chunk_metadata={"table_id": "pdf_p1_1", "chunk_type": "table_row"},
    )
    table_b_header = SimpleNamespace(
        id=uuid4(),
        document_id=document_id,
        chunk_index=12,
        content="TABLE_HEADER table_id=pdf_p1_2 page=1 | Name | Area",
        chunk_metadata={"table_id": "pdf_p1_2", "chunk_type": "table_header"},
    )
    table_b_row = SimpleNamespace(
        id=uuid4(),
        document_id=document_id,
        chunk_index=13,
        content=(
            "TABLE_ROW table_id=pdf_p1_2 page=1 row=2 | Name: Nguyen Quang Lam | "
            "Area: OCR"
        ),
        chunk_metadata={"table_id": "pdf_p1_2", "chunk_type": "table_row"},
    )

    class FakeRepoWithMultipleTables:
        async def get_table_chunks(self, *, document_id, table_id, exclude_ids):
            if table_id == "pdf_p1_1":
                return [table_a_row]
            if table_id == "pdf_p1_2":
                return [table_b_header, table_b_row]
            return []

    service = RagAnswerService(
        chat_repository=FakeRepoWithMultipleTables(),  # type: ignore[arg-type]
        reranking_service=SimpleNamespace(),  # type: ignore[arg-type]
        llm_provider=SimpleNamespace(),  # type: ignore[arg-type]
    )

    expanded = asyncio.run(
        service._expand_with_neighbors(
            query="Nguyen Quang Lam tham gia nhung mang cong nghe nao?",
            context_chunks=[ContextChunk(citation_index=1, chunk=summary_chunk)],
            max_context_chars=1000,
        )
    )

    contents = [item.chunk.content for item in expanded]
    assert any("Platform AI" in content for content in contents)
    assert any("TABLE_HEADER table_id=pdf_p1_2" in content for content in contents)
    assert any("Area: OCR" in content for content in contents)


def test_entity_coverage_lookup_adds_all_matching_table_rows_only() -> None:
    from uuid import uuid4

    from app.services.rag_answer_service import ContextChunk, RagAnswerService

    document_id = uuid4()
    primary = SimpleNamespace(
        id=uuid4(),
        document_id=document_id,
        chunk_index=0,
        content="ENTITY_SUMMARY entity=Nguyen Quang Lam\nRows:\n- partial summary",
        chunk_metadata={"chunk_type": "entity_summary", "table_ids": ["tbl_1"]},
    )
    matching_rows = [
        SimpleNamespace(
            id=uuid4(),
            document_id=document_id,
            chunk_index=index,
            content=row_content,
            chunk_metadata={"chunk_type": "table_row", "table_id": "tbl_1"},
        )
        for index, row_content in enumerate(
            [
                (
                    "TABLE_ROW table_id=tbl_1 row=3 | Nhom nhiem vu: Xay dung nen tang "
                    "RAG tren du lieu noi bo | Danh sach: Tong Phuoc Lam; Nguyen Quang Lam"
                ),
                (
                    "TABLE_ROW table_id=tbl_1 row=4 | Nhom nhiem vu: Xay dung dich vu OCR "
                    "dung chung | Danh sach: Trinh Thanh Tinh; Nguyen Quang Lam"
                ),
                (
                    "TABLE_ROW table_id=tbl_1 row=5 | Nhom nhiem vu: Kho du lieu AI dung "
                    "chung | Danh sach: Nguyen Quang Lam; Nguyen Trong Hung"
                ),
                (
                    "TABLE_ROW table_id=tbl_1 row=6 | Nhom nhiem vu: Platform AI | Danh "
                    "sach: Cac nhan su trong ke hoach PoC ThinkLabs; Nguyen Quang Lam; "
                    "Vo Van Phuc"
                ),
            ],
            start=1,
        )
    ]
    unrelated_row = SimpleNamespace(
        id=uuid4(),
        document_id=document_id,
        chunk_index=99,
        content=(
            "TABLE_ROW table_id=tbl_1 row=7 | Nhom nhiem vu: Xay dung nang luc mo hinh "
            "ngon ngu noi bo | Danh sach: Tran Van An"
        ),
        chunk_metadata={"chunk_type": "table_row", "table_id": "tbl_1"},
    )

    class FakeCoverageRepo:
        async def get_entity_coverage_chunks(self, *, document_id, search_terms, exclude_ids):
            assert any("Nguyen Quang Lam" in term for term in search_terms)
            return [*matching_rows, unrelated_row]

        async def get_table_chunks(self, *, document_id, table_id, exclude_ids):
            return []

    service = RagAnswerService(
        chat_repository=FakeCoverageRepo(),  # type: ignore[arg-type]
        reranking_service=SimpleNamespace(),  # type: ignore[arg-type]
        llm_provider=SimpleNamespace(),  # type: ignore[arg-type]
    )

    expanded = asyncio.run(
        service._expand_with_neighbors(
            query="Nguyen Quang Lam tham gia nhung mang cong nghe nao?",
            context_chunks=[ContextChunk(citation_index=1, chunk=primary)],
            max_context_chars=4000,
        )
    )

    contents = [item.chunk.content for item in expanded]
    assert sum("Nguyen Quang Lam" in content for content in contents) >= 5
    assert any("RAG tren du lieu noi bo" in content for content in contents)
    assert any("OCR dung chung" in content for content in contents)
    assert any("Kho du lieu AI dung chung" in content for content in contents)
    assert any("Platform AI" in content for content in contents)
    assert all("Xay dung nang luc mo hinh ngon ngu noi bo" not in content for content in contents)


def test_entity_coverage_repository_queries_full_content_with_priority_order() -> None:
    from uuid import uuid4

    from app.repositories.chat import ChatRepository

    document_id = uuid4()
    chunks = [
        SimpleNamespace(chunk_index=3, content="TABLE_ROW row=3 Nguyen Quang Lam"),
        SimpleNamespace(chunk_index=4, content="TABLE_ROW row=4 Nguyen Quang Lam"),
        SimpleNamespace(chunk_index=5, content="TABLE_ROW row=5 Nguyen Quang Lam"),
        SimpleNamespace(chunk_index=6, content="TABLE_ROW row=6 Nguyen Quang Lam"),
    ]

    class FakeScalars:
        def all(self):
            return chunks

    class FakeResult:
        def scalars(self):
            return FakeScalars()

    class FakeSession:
        def __init__(self) -> None:
            self.statement = None

        async def execute(self, statement):
            self.statement = statement
            return FakeResult()

    fake_session = FakeSession()
    repository = ChatRepository(fake_session)  # type: ignore[arg-type]

    result = asyncio.run(
        repository.get_entity_coverage_chunks(
            document_id=document_id,
            search_terms=["Nguyen Quang Lam"],
            max_matches=50,
        )
    )

    statement_text = str(fake_session.statement)
    statement_params = fake_session.statement.compile().params
    param_values = []
    for value in statement_params.values():
        if isinstance(value, list):
            param_values.extend(value)
        else:
            param_values.append(value)
    assert result == chunks
    assert "chunks.content" in statement_text
    assert "content_preview" not in statement_text
    assert "table_row" in param_values
    assert "entity_summary" in param_values
    assert "table_block" in param_values
    assert "text" in param_values
    assert fake_session.statement._limit_clause is not None


def test_user_prompt_separates_entity_matched_rows_from_table_support() -> None:
    from uuid import uuid4

    from app.services.rag_answer_service import ContextChunk, RagAnswerService

    table_chunk = SimpleNamespace(
        id=uuid4(),
        document_id=uuid4(),
        chunk_index=1,
        content=(
            "TABLE_TITLE table_id=tbl_1 | Ke hoach cong nghe\n"
            "TABLE_HEADER table_id=tbl_1 | STT | Nhom nhiem vu | Danh sach\n"
            "TABLE_ROW table_id=tbl_1 row=2 | STT: 2 | Nhom nhiem vu: Xay dung nang "
            "luc mo hinh ngon ngu noi bo | Danh sach: Tran Van An\n"
            "TABLE_ROW table_id=tbl_1 row=3 | STT: 3 | Nhom nhiem vu: Xay dung nen "
            "tang RAG tren du lieu noi bo | Danh sach: Tong Phuoc Lam; Nguyen Quang Lam\n"
            "TABLE_ROW table_id=tbl_1 row=4 | STT: 4 | Nhom nhiem vu: Xay dung dich "
            "vu OCR dung chung | Danh sach: Trinh Thanh Tinh; Nguyen Quang Lam\n"
            "TABLE_ROW table_id=tbl_1 row=5 | STT: 5 | Nhom nhiem vu: Kho du lieu AI "
            "dung chung | Danh sach: Nguyen Quang Lam; Nguyen Trong Hung\n"
            "TABLE_ROW table_id=tbl_1 row=6 | STT: 6 | Nhom nhiem vu: Platform AI | "
            "Danh sach: Cac nhan su trong ke hoach PoC ThinkLabs; Nguyen Quang Lam"
        ),
        chunk_metadata={"chunk_type": "table_block", "table_id": "tbl_1"},
    )

    prompt = RagAnswerService._build_user_prompt(
        query="Nguyen Quang Lam tham gia nhung mang cong nghe nao?",
        context_chunks=[ContextChunk(citation_index=1, chunk=table_chunk)],
    )

    entity_section = prompt.split("ENTITY_MATCHED_ROWS:\n", 1)[1].split(
        "\n\nTABLE_SUPPORT:",
        1,
    )[0]
    assert "row=3" in entity_section
    assert "row=4" in entity_section
    assert "row=5" in entity_section
    assert "row=6" in entity_section
    assert "row=2" not in entity_section
    assert "Xay dung nang luc mo hinh ngon ngu noi bo" not in entity_section
    assert "TABLE_SUPPORT:" in prompt
    assert "TABLE_HEADER table_id=tbl_1" in prompt
    assert "must have N bullet" in RagAnswerService._build_user_prompt.__globals__[
        "TABLE_QA_STYLE"
    ]

def test_user_prompt_splits_inline_table_rows_before_matching_entity() -> None:
    from uuid import uuid4

    from app.services.rag_answer_service import ContextChunk, RagAnswerService

    table_chunk = SimpleNamespace(
        id=uuid4(),
        document_id=uuid4(),
        chunk_index=1,
        content=(
            "TABLE_HEADER table_id=pdf_p5_1 page=5 | cell_1 | cell_2 | cell_6 "
            "TABLE_ROW table_id=pdf_p5_1 page=5 row=6 | cell_1: 3 | "
            "cell_2: Xay dung nen tang RAG tren du lieu noi bo | "
            "cell_6: Nguyen Quang Lam "
            "TABLE_ROW table_id=pdf_p5_1 page=5 row=7 | cell_1: 4 | "
            "cell_2: Xay dung dich vu OCR dung chung | "
            "cell_6: Trinh Thanh Tinh; Duong Sinh Sinh; Nguyen Quang Lam"
        ),
        chunk_metadata={"chunk_type": "table_block", "table_id": "pdf_p5_1"},
    )

    prompt = RagAnswerService._build_user_prompt(
        query="Trinh Thanh Tinh tham gia vao cac mang cong nghe nao?",
        context_chunks=[ContextChunk(citation_index=1, chunk=table_chunk)],
    )

    entity_section = prompt.split("ENTITY_MATCHED_ROWS:\n", 1)[1].split(
        "\n\nTABLE_SUPPORT:",
        1,
    )[0]
    assert "row=7" in entity_section
    assert "Xay dung dich vu OCR dung chung" in entity_section
    assert "row=6" not in entity_section
    assert "Xay dung nen tang RAG tren du lieu noi bo" not in entity_section


def test_deterministic_legal_leave_prefers_specific_child_marriage_row() -> None:
    from uuid import uuid4

    from app.services.rag_answer_service import ContextChunk, RagAnswerService

    document_id = uuid4()
    rows = [
        SimpleNamespace(
            id=uuid4(),
            document_id=document_id,
            chunk_index=12,
            content="Trường hợp: Kết hôn; Nghỉ 03 ngày hưởng nguyên lương",
            chunk_metadata={
                "chunk_type": "legal_table_row",
                "relationship_type": "legal_leave_benefit",
                "case_code": "a",
                "case_name": "Kết hôn",
                "total_leave_days": 3,
                "total_leave_benefit": "Nghỉ 03 ngày hưởng nguyên lương",
                "labor_code_benefit": "Nghỉ 03 ngày hưởng nguyên lương",
                "table_name": "Điều 10. Nghỉ việc riêng có hưởng lương",
                "source_label": "Quy chế nghỉ hưởng lương mẫu",
                "source_file": "TULDTT CPC 2024 KY KET 11.10.2024.docx",
            },
        ),
        SimpleNamespace(
            id=uuid4(),
            document_id=document_id,
            chunk_index=13,
            content=(
                "Trường hợp: Con đẻ, con nuôi kết hôn; Nghỉ 02 ngày hưởng "
                "nguyên lương (01 ngày theo BLLĐ + 01 ngày hưởng thêm theo TƯLĐTT này)"
            ),
            chunk_metadata={
                "chunk_type": "legal_table_row",
                "relationship_type": "legal_leave_benefit",
                "case_code": "b",
                "case_name": "Con đẻ, con nuôi kết hôn",
                "total_leave_days": 2,
                "total_leave_benefit": "Nghỉ 02 ngày hưởng nguyên lương",
                "labor_code_benefit": "Nghỉ 01 ngày hưởng nguyên lương",
                "collective_agreement_benefit": "Nghỉ 01 ngày hưởng nguyên lương",
                "table_name": "Điều 10. Nghỉ việc riêng có hưởng lương",
                "source_label": "Quy chế nghỉ hưởng lương mẫu",
                "source_file": "TULDTT CPC 2024 KY KET 11.10.2024.docx",
            },
        ),
        SimpleNamespace(
            id=uuid4(),
            document_id=document_id,
            chunk_index=17,
            content=(
                "Trường hợp: Cha hoặc mẹ của NLĐ hoặc của vợ (chồng) NLĐ "
                "kết hôn (kể cả bố, mẹ nuôi được pháp luật công nhận); "
                "Anh, chị, em ruột của NLĐ hoặc của vợ (chồng) NLĐ kết hôn. "
                "Nghỉ 01 ngày hưởng nguyên lương và phải thông báo với NSDLĐ"
            ),
            chunk_metadata={
                "chunk_type": "legal_table_row",
                "relationship_type": "legal_leave_benefit",
                "case_code": "f",
                "case_name": (
                    "Cha hoặc mẹ của NLĐ hoặc của vợ (chồng) NLĐ kết hôn "
                    "(kể cả bố, mẹ nuôi được pháp luật công nhận); Anh, chị, "
                    "em ruột của NLĐ hoặc của vợ (chồng) NLĐ kết hôn."
                ),
                "total_leave_days": 1,
                "total_leave_benefit": "Nghỉ 01 ngày hưởng nguyên lương",
                "collective_agreement_benefit": (
                    "Nghỉ 01 ngày hưởng nguyên lương và phải thông báo với NSDLĐ"
                ),
                "table_name": "Điều 10. Nghỉ việc riêng có hưởng lương",
                "source_label": "Quy chế nghỉ hưởng lương mẫu",
                "source_file": "TULDTT CPC 2024 KY KET 11.10.2024.docx",
            },
        ),
    ]
    context_chunks = [
        ContextChunk(citation_index=index, chunk=chunk)
        for index, chunk in enumerate(rows, start=1)
    ]

    child_answer = RagAnswerService._deterministic_legal_leave_answer(
        query="Con đẻ kết hôn được nghỉ bao nhiêu ngày?",
        context_chunks=context_chunks,
    )
    adopted_child_answer = RagAnswerService._deterministic_legal_leave_answer(
        query="Trường hợp con nuôi kết hôn có được hưởng quyền lợi này không?",
        context_chunks=context_chunks,
    )
    self_answer = RagAnswerService._deterministic_legal_leave_answer(
        query="Khi kết hôn, NLĐ được nghỉ việc riêng có hưởng lương bao nhiêu ngày?",
        context_chunks=context_chunks,
    )

    assert child_answer is not None
    assert "02 ngày" in child_answer
    assert "con đẻ" in child_answer
    assert adopted_child_answer is not None
    assert "02 ngày" in adopted_child_answer
    assert "con nuôi" in adopted_child_answer
    assert "Ngoài trường hợp" not in adopted_child_answer
    assert "Cha hoặc mẹ" not in adopted_child_answer
    assert "Người lao động có vợ sinh con" not in adopted_child_answer
    assert self_answer is not None
    assert self_answer.startswith("Theo Quy chế nghỉ hưởng lương mẫu")
    assert "03 ngày" in self_answer
    assert "Ngoài trường hợp bản thân" not in self_answer
    assert "Thỏa ước còn quy định" not in self_answer
    assert "Ngoài trường hợp" in self_answer
    assert "trường hợp liên quan" in self_answer
    assert "có chứa" not in self_answer
    assert "Con đẻ, con nuôi kết hôn" in self_answer
    assert "02 ngày" in self_answer
    assert "Cha hoặc mẹ" in self_answer
    assert "Anh, chị, em ruột" in self_answer
    assert "01 ngày" in self_answer
    assert "phải thông báo với người sử dụng lao động" in self_answer


def test_deterministic_person_area_answer_reads_canonical_table_block_without_metadata() -> None:
    from uuid import uuid4

    from app.services.rag_answer_service import ContextChunk, RagAnswerService

    document_id = uuid4()
    table_block = SimpleNamespace(
        id=uuid4(),
        document_id=document_id,
        chunk_index=10,
        content=(
            "STT: 3\n"
            "Mảng công nghệ: Xây dựng nền tảng RAG trên dữ liệu nội bộ\n"
            "Phòng chủ trì: PTUD\n"
            "Nhân sự đề xuất: 1. Tống Phước Lâm 2. Nguyễn\nQuang Lâm\n\n"
            "STT: 4\n"
            "Mảng công nghệ: Xây dựng dịch vụ OCR dùng chung\n"
            "Phòng chủ trì: PM\n"
            "Nhân sự đề xuất: 1. Trịnh Thanh Tịnh 2. Nguyễn Quang Lâm\n"
        ),
        chunk_metadata={"chunk_type": "table_block"},
    )

    answer = RagAnswerService._deterministic_person_area_answer(
        query="Nguyễn Quang Lâm tham gia vào mảng công nghệ nào?",
        context_chunks=[ContextChunk(citation_index=1, chunk=table_block)],
    )

    assert answer is not None
    assert "được đề xuất tham gia 02 mảng công nghệ" in answer
    assert "Xây dựng nền tảng RAG trên dữ liệu nội bộ" in answer
    assert "Phòng chủ trì: PTUD" in answer
    assert "Xây dựng dịch vụ OCR dùng chung" in answer
    assert "Phòng chủ trì: PM" in answer


def test_deterministic_person_area_answer_handles_membership_question() -> None:
    from uuid import uuid4

    from app.services.rag_answer_service import ContextChunk, RagAnswerService

    document_id = uuid4()
    table_row = SimpleNamespace(
        id=uuid4(),
        document_id=document_id,
        chunk_index=19,
        content=(
            "STT: 3\n"
            "Mảng công nghệ: Xây dựng nền tảng RAG trên dữ liệu nội bộ\n"
            "Phòng chủ trì: PTUD\n"
            "Nhân sự đề xuất: Tống Phước Lâm; Nguyễn Quang Lâm; "
            "Nguyễn Trọng Hùng; Võ Văn Hòa; Đoàn Gia Hy (kiểm thử)"
        ),
        chunk_metadata={"chunk_type": "table_row"},
    )
    profile = SimpleNamespace(
        id=uuid4(),
        document_id=document_id,
        chunk_index=32,
        content=(
            "Nhân sự: Nguyễn Trọng Hùng.\n"
            "Nguyễn Trọng Hùng được đề xuất tham gia các mảng công nghệ:\n"
            "- Xây dựng nền tảng RAG trên dữ liệu nội bộ; phòng chủ trì: PTUD.\n"
            "- Kho dữ liệu AI dùng chung; phòng chủ trì: VH.\n"
            "- Platform AI; phòng chủ trì: PTUD."
        ),
        chunk_metadata={"chunk_type": "entity_profile"},
    )

    answer = RagAnswerService._deterministic_person_area_answer(
        query="Nguyễn Trọng Hùng tham gia Xây dựng nền tảng RAG trên dữ liệu nội bộ đúng không?",
        context_chunks=[
            ContextChunk(citation_index=1, chunk=table_row),
            ContextChunk(citation_index=2, chunk=profile),
        ],
    )

    assert answer is not None
    assert answer.startswith("Đúng, Nguyễn Trọng Hùng được đề xuất tham gia")
    assert "Xây dựng nền tảng RAG trên dữ liệu nội bộ" in answer
    assert "Kho dữ liệu AI dùng chung" in answer
    assert "Platform AI" in answer


def test_deterministic_person_area_answer_matches_short_person_name_alias() -> None:
    from uuid import uuid4

    from app.services.rag_answer_service import ContextChunk, RagAnswerService

    profile = SimpleNamespace(
        id=uuid4(),
        document_id=uuid4(),
        chunk_index=38,
        content=(
            "Nhân sự: Tống Phước Lâm.\n"
            "Tống Phước Lâm được đề xuất tham gia các mảng công nghệ:\n"
            "- Xây dựng nền tảng RAG trên dữ liệu nội bộ; phòng chủ trì: PTUD.\n"
            "- Platform AI; phòng chủ trì: PTUD."
        ),
        chunk_metadata={"chunk_type": "entity_profile"},
    )

    answer = RagAnswerService._deterministic_person_area_answer(
        query="Phước Lâm tham gia vào mảng công nghệ nào?",
        context_chunks=[ContextChunk(citation_index=1, chunk=profile)],
    )

    assert answer is not None
    assert "Phước Lâm được đề xuất tham gia 02 mảng công nghệ" in answer
    assert "Xây dựng nền tảng RAG trên dữ liệu nội bộ" in answer
    assert "Platform AI" in answer


def test_entity_coverage_repository_adds_token_fallback_for_split_person_names() -> None:
    from uuid import uuid4

    from app.repositories.chat import ChatRepository

    class FakeScalars:
        def all(self):
            return []

    class FakeResult:
        def scalars(self):
            return FakeScalars()

    class FakeSession:
        def __init__(self) -> None:
            self.statement = None

        async def execute(self, statement):
            self.statement = statement
            return FakeResult()

    fake_session = FakeSession()
    repository = ChatRepository(fake_session)  # type: ignore[arg-type]

    asyncio.run(
        repository.get_entity_coverage_chunks(
            document_id=uuid4(),
            search_terms=["Nguyễn Quang Lâm", "Nguyen Quang Lam"],
        )
    )

    statement_text = str(fake_session.statement)
    params = fake_session.statement.compile().params
    values = [str(value) for value in params.values()]
    assert "AND" in statement_text
    assert any("%Nguyễn%" == value for value in values)
    assert any("%Quang%" == value for value in values)
    assert any("%Lâm%" == value for value in values)


def test_user_prompt_keeps_narrative_context_when_table_rows_match() -> None:
    from uuid import uuid4

    from app.services.rag_answer_service import ContextChunk, RagAnswerService

    document_id = uuid4()
    narrative_chunk = SimpleNamespace(
        id=uuid4(),
        document_id=document_id,
        chunk_index=5,
        content=(
            "3. Xây dựng nền tảng RAG trên dữ liệu nội bộ\n"
            "Mục tiêu: Khai thác tri thức nội bộ qua hỏi đáp có dẫn nguồn, "
            "chính xác và được phân quyền.\n"
            "- Khảo sát và lựa chọn kỹ thuật RAG phù hợp.\n"
            "- Chuẩn hóa quy trình phân đoạn tài liệu, embedding và index.\n"
            "- Kết hợp tìm kiếm từ khóa và tìm kiếm ngữ nghĩa.\n"
            "- Phân quyền truy hồi theo tài liệu hoặc người dùng."
        ),
        chunk_metadata={"chunk_type": "docling_hybrid_repaired"},
    )
    table_row = SimpleNamespace(
        id=uuid4(),
        document_id=document_id,
        chunk_index=19,
        content=(
            "STT: 3\n"
            "Mảng công nghệ: Xây dựng nền tảng RAG trên dữ liệu nội bộ\n"
            "Phòng chủ trì: PTUD\n"
            "Nhân sự đề xuất: Tống Phước Lâm; Nguyễn Quang Lâm"
        ),
        chunk_metadata={"chunk_type": "table_row"},
    )

    prompt = RagAnswerService._build_user_prompt(
        query="Mảng công nghệ RAG trên dữ liệu nội bộ có mục tiêu gì?",
        context_chunks=[
            ContextChunk(citation_index=1, chunk=table_row),
            ContextChunk(citation_index=2, chunk=narrative_chunk),
        ],
    )

    assert "ENTITY_MATCHED_ROWS:" in prompt
    assert "Retrieved Document Context:" in prompt
    assert "Mục tiêu: Khai thác tri thức nội bộ" in prompt
    assert "Kết hợp tìm kiếm từ khóa" in prompt
    assert "do not ignore narrative context" in prompt
    assert "summary followed by list items" in prompt


def test_deterministic_narrative_section_answer_preserves_section_bullets() -> None:
    from uuid import uuid4

    from app.services.rag_answer_service import ContextChunk, RagAnswerService

    document_id = uuid4()
    narrative_chunk = SimpleNamespace(
        id=uuid4(),
        document_id=document_id,
        chunk_index=5,
        content=(
            "3. Xây dựng nền tảng RAG trên dữ liệu nội bộ\n"
            "Mục tiêu: Khai thác tri thức nội bộ qua hỏi đáp có dẫn nguồn, "
            "chính xác và được phân quyền.\n"
            "- Khảo sát, đánh giá và lựa chọn kỹ thuật RAG phù hợp.\n"
            "- Chuẩn hóa quy trình phân đoạn tài liệu, vector hóa và lập chỉ mục.\n"
            "- Kết hợp tìm kiếm từ khóa và tìm kiếm ngữ nghĩa.\n"
            "- Xây dựng pipeline RAG dùng chung cho toàn EVNCPC."
        ),
        chunk_metadata={"chunk_type": "docling_hybrid_repaired"},
    )
    table_row = SimpleNamespace(
        id=uuid4(),
        document_id=document_id,
        chunk_index=19,
        content=(
            "STT: 3\n"
            "Mảng công nghệ: Xây dựng nền tảng RAG trên dữ liệu nội bộ\n"
            "Phòng chủ trì: PTUD"
        ),
        chunk_metadata={"chunk_type": "table_row"},
    )

    answer = RagAnswerService._deterministic_narrative_section_answer(
        query="Mảng công nghệ RAG trên dữ liệu nội bộ có mục tiêu gì?",
        context_chunks=[
            ContextChunk(citation_index=1, chunk=table_row),
            ContextChunk(citation_index=2, chunk=narrative_chunk),
        ],
    )

    assert answer is not None
    assert "Khai thác tri thức nội bộ" in answer
    assert "Khảo sát, đánh giá" in answer
    assert "Chuẩn hóa quy trình" in answer
    assert "Kết hợp tìm kiếm từ khóa" in answer
    assert "pipeline RAG dùng chung" in answer
    assert "Phòng chủ trì" not in answer


def test_query_terms_extract_generic_keyphrases_for_section_recovery() -> None:
    from app.services.rag_answer_service import RagAnswerService

    terms = RagAnswerService._query_terms(
        "Mảng công nghệ RAG trên dữ liệu nội bộ có mục tiêu gì?"
    )

    normalized_terms = [
        RagAnswerService._strip_vietnamese_accents(term).casefold()
        for term in terms
    ]
    assert "rag" in normalized_terms
    assert any("rag tren du lieu noi bo" in term for term in normalized_terms)
    assert any("muc tieu" in term for term in normalized_terms)


def test_high_signal_coverage_chunk_can_override_context_budget() -> None:
    import asyncio
    from uuid import uuid4

    from app.services.rag_answer_service import ContextChunk, RagAnswerService

    document_id = uuid4()
    primary = SimpleNamespace(
        id=uuid4(),
        document_id=document_id,
        chunk_index=19,
        content="X" * 600,
        chunk_metadata={"chunk_type": "table_row"},
    )
    narrative = SimpleNamespace(
        id=uuid4(),
        document_id=document_id,
        chunk_index=5,
        content=(
            "3. Xây dựng nền tảng RAG trên dữ liệu nội bộ. "
            "Mục tiêu: Khai thác tri thức nội bộ qua hỏi đáp có dẫn nguồn."
        ),
        chunk_metadata={"chunk_type": "docling_hybrid_repaired"},
    )

    class FakeCoverageRepo:
        async def get_entity_coverage_chunks(self, *, document_id, search_terms, exclude_ids):
            assert any("RAG" in term for term in search_terms)
            return [narrative]

    service = RagAnswerService(
        chat_repository=FakeCoverageRepo(),  # type: ignore[arg-type]
        reranking_service=SimpleNamespace(),  # type: ignore[arg-type]
        llm_provider=SimpleNamespace(),  # type: ignore[arg-type]
    )

    expanded = asyncio.run(
        service._expand_with_neighbors(
            query="Mảng công nghệ RAG trên dữ liệu nội bộ có mục tiêu gì?",
            context_chunks=[ContextChunk(citation_index=1, chunk=primary)],
            max_context_chars=200,
        )
    )

    assert any("Mục tiêu: Khai thác tri thức nội bộ" in item.chunk.content for item in expanded)
