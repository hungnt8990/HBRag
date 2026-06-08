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
