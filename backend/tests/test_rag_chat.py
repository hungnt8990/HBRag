import asyncio
from types import SimpleNamespace
from uuid import UUID

from fastapi.testclient import TestClient

from app.api.routes.chat import get_rag_answer_service
from app.main import app
from app.repositories.chat import CitationCreate
from app.schemas.chat import (
    RagChatResponse,
    RagCitationResponse,
    RagRecentMessage,
    RagSessionContext,
)
from app.schemas.documents import RerankSearchResponse, RerankSearchResult
from app.services.llms.llm_fake_llm import FakeLLM
from app.services.rag.rag_answer_service import RagAnswerService

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


class RewriteThenFakeLLM(FakeLLM):
    def __init__(self, rewritten_query: str) -> None:
        self.rewritten_query = rewritten_query
        self.rewrite_prompts: list[str] = []
        self.answer_prompts: list[str] = []

    async def generate(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
    ) -> str:
        if "STANDALONE QUESTION:" in user_prompt:
            self.rewrite_prompts.append(user_prompt)
            return self.rewritten_query
        self.answer_prompts.append(user_prompt)
        return await super().generate(system_prompt=system_prompt, user_prompt=user_prompt)


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
        user_prompt = "Question:\nWhat is RAG?\n\nContext:\n[1] RAG uses context.\n[2] Citations point to chunks."

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
        assert repository.citations[0].quote == ("Python RAG systems use retrieved context for grounded answers.")
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


def test_rag_service_rewrites_followup_query_for_retrieval() -> None:
    async def run_test() -> None:
        repository = FakeChatRepository()
        reranking_service = FakeRerankingService()
        llm = RewriteThenFakeLLM("How should RAG cite chunks?")
        service = RagAnswerService(
            chat_repository=repository,  # type: ignore[arg-type]
            reranking_service=reranking_service,  # type: ignore[arg-type]
            llm_provider=llm,
        )

        response = await service.answer(
            query="CÃ²n cÃ¡i nÃ y thÃ¬ sao?",
            session_id=None,
            top_k=2,
            candidate_k=10,
            session_context=RagSessionContext(
                last_topic="RAG citation behavior",
                recent_messages=[
                    RagRecentMessage(
                        role="user",
                        content="How should RAG cite chunks?",
                    ),
                    RagRecentMessage(
                        role="assistant",
                        content="Citations should point back to source chunks.",
                    ),
                ],
            ),
        )

        assert reranking_service.calls[0]["query"] == "How should RAG cite chunks?"
        assert llm.rewrite_prompts
        assert llm.answer_prompts
        assert "Conversation reference wording" in llm.answer_prompts[0]
        assert "How should RAG cite chunks?" in llm.answer_prompts[0]
        assert "Question:\nCÃ²n cÃ¡i nÃ y thÃ¬ sao?" in llm.answer_prompts[0]
        assert "Generated from provided context" in response.answer

    asyncio.run(run_test())


def test_person_area_query_does_not_answer_from_unrelated_context() -> None:
    async def run_test() -> None:
        repository = FakeChatRepository()
        repository.chunks[CHUNK_ID_1].content = "CHÆ¯Æ NG II\nVIá»†C LÃ€M VÃ€ Äáº¢M Báº¢O VIá»†C LÃ€M\nÄiá»u 7. CÃ´ng tÃ¡c Ä‘Ã o táº¡o.\nEVNCPC coi trá»ng cÃ´ng tÃ¡c Ä‘Ã o táº¡o vÃ  Ä‘Ã o táº¡o láº¡i Ä‘á»ƒ nÃ¢ng cao trÃ¬nh Ä‘á»™ quáº£n lÃ½, chuyÃªn mÃ´n nghiá»‡p vá»¥."
        repository.chunks[CHUNK_ID_1].chunk_metadata = {
            "chunk_type": "docling_hybrid_repaired",
            "start_char": 0,
            "end_char": 180,
        }
        reranking_service = FakeRerankingService()
        service = RagAnswerService(
            chat_repository=repository,  # type: ignore[arg-type]
            reranking_service=reranking_service,  # type: ignore[arg-type]
            llm_provider=FakeLLM(),
        )

        response = await service.answer(
            query="PhÆ°á»›c LÃ¢m tham gia vÃ o máº£ng cÃ´ng nghá»‡ nÃ o",
            session_id=None,
            top_k=1,
            candidate_k=10,
        )

        assert response.answer == "Kh\u00f4ng t\u00ecm th\u1ea5y th\u00f4ng tin ph\u00f9 h\u1ee3p trong c\u00e1c t\u00e0i li\u1ec7u b\u1ea1n c\u00f3 quy\u1ec1n truy c\u1eadp."
        assert "CHÆ¯Æ NG II" not in response.answer
        assert "CÃ´ng tÃ¡c Ä‘Ã o táº¡o" not in response.answer

    asyncio.run(run_test())


def test_policy_question_does_not_answer_from_unrelated_customer_app_context() -> None:
    async def run_test() -> None:
        repository = FakeChatRepository()
        repository.chunks[CHUNK_ID_1].content = (
            "EVNICT thong bao cap nhat phien ban ung dung EVN CSKH. "
            "Noi dung bao gom cac man hinh thanh toan, hoa don va lien ket khach hang."
        )
        repository.chunks[CHUNK_ID_2].content = "Website quan tri noi dung CMS bo sung dashboard va bao cao thong ke."
        reranking_service = FakeRerankingService()
        service = RagAnswerService(
            chat_repository=repository,  # type: ignore[arg-type]
            reranking_service=reranking_service,  # type: ignore[arg-type]
            llm_provider=FakeLLM(),
        )

        query = (
            "Khi k\u1ebft h\u00f4n, NL\u0110 \u0111\u01b0\u1ee3c ngh\u1ec9 vi\u1ec7c ri\u00eang c\u00f3 h\u01b0\u1edfng "
            "l\u01b0\u01a1ng bao nhi\u00eau ng\u00e0y theo Th\u1ecfa \u01b0\u1edbc lao \u0111\u1ed9ng t\u1eadp th\u1ec3 EVNCPC?"
        )
        response = await service.answer(
            query=query,
            session_id=None,
            top_k=2,
            candidate_k=10,
        )

        assert response.answer == "Kh\u00f4ng t\u00ecm th\u1ea5y th\u00f4ng tin ph\u00f9 h\u1ee3p trong c\u00e1c t\u00e0i li\u1ec7u b\u1ea1n c\u00f3 quy\u1ec1n truy c\u1eadp."
        assert response.citations == []
        assert repository.citations == []

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
    from app.services.rag.rag_answer_service import (
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
    from app.services.rag.rag_answer_service import build_system_prompt

    prompt = build_system_prompt(answer_mode="hybrid", answer_style="policy_explainer")
    assert "Provide a concise answer" in prompt or "concise answer first" in prompt
    assert "related cases" in prompt.lower()
    assert "table rows" in prompt.lower()
    assert "exact numbers" in prompt.lower()
    assert "same language as the user's question" in prompt
    assert "Do not create a separate Sources" in prompt

    concise = build_system_prompt(answer_mode="hybrid", answer_style="concise")
    assert "1-2 sentences" in concise

    detailed = build_system_prompt(answer_mode="generative", answer_style="detailed")
    assert "thorough" in detailed.lower()


def test_numeric_identifier_query_defaults_to_vietnamese_prompt() -> None:
    from uuid import uuid4

    from app.services.rag.rag_answer_service import ContextChunk, RagAnswerService

    chunk = SimpleNamespace(
        id=uuid4(),
        document_id=uuid4(),
        chunk_index=0,
        content="574/BC-ÄLÄS lÃ  bÃ¡o cÃ¡o vá» xÃ¢y dá»±ng káº¿ hoáº¡ch Ä‘Ã o táº¡o nÄƒm 2024.",
        chunk_metadata={"identifier_exact_boost": "1"},
    )

    prompt = RagAnswerService._build_user_prompt(
        query="574",
        context_chunks=[ContextChunk(citation_index=1, chunk=chunk)],
    )

    assert "Answer only in Vietnamese" in prompt
    assert "Numeric or code-only lookups" in prompt
    assert "Answer in the same language as the user's question" not in prompt


def test_citation_response_maps_lexical_exact_to_public_keyword_flag() -> None:
    from uuid import uuid4

    from app.services.rag.rag_answer_service import ContextChunk, RagAnswerService

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

    from app.services.rag.rag_answer_service import ContextChunk, RagAnswerService

    document_id = uuid4()
    primary = SimpleNamespace(
        id=uuid4(),
        document_id=document_id,
        chunk_index=0,
        content="Äiá»u 10 ná»™i dung chÃ­nh.",
        chunk_metadata={"article_number": "10"},
    )
    neighbor_a = SimpleNamespace(
        id=uuid4(),
        document_id=document_id,
        chunk_index=1,
        content="Báº£ng quy Ä‘á»‹nh kÃ¨m theo Äiá»u 10.",
        chunk_metadata={"article_number": "10"},
    )
    neighbor_b = SimpleNamespace(
        id=uuid4(),
        document_id=document_id,
        chunk_index=2,
        content="Ghi chÃº bá»• sung cá»§a Äiá»u 10.",
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

    from app.services.rag.rag_answer_service import ContextChunk, RagAnswerService

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

    # Primary alone fits, big neighbor would exceed cap â†’ not added.
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

    from app.services.rag.rag_answer_service import ContextChunk, RagAnswerService

    doc_id = uuid4()
    same_quote = "Káº¿t hÃ´n - Nghá»‰ 03 ngÃ y hÆ°á»Ÿng nguyÃªn lÆ°Æ¡ng"

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
        content="  Káº¿t hÃ´n -   Nghá»‰ 03 ngÃ y hÆ°á»Ÿng nguyÃªn lÆ°Æ¡ng  ",
        chunk_metadata={"article_number": "10"},
    )
    # Truly different content.
    chunk_c = SimpleNamespace(
        id=uuid4(),
        document_id=doc_id,
        chunk_index=2,
        content="Tang lá»… cha máº¹ - Nghá»‰ 03 ngÃ y.",
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

    from app.services.rag.rag_answer_service import ContextChunk, RagAnswerService

    doc_id = uuid4()
    chunk = SimpleNamespace(
        id=uuid4(),
        document_id=doc_id,
        chunk_index=0,
        content="Ná»™i dung Ä‘iá»u 10.",
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

    from app.services.rag.rag_answer_service import ContextChunk, RagAnswerService

    doc_id = uuid4()
    same = "Káº¿t hÃ´n - Nghá»‰ 03 ngÃ y hÆ°á»Ÿng nguyÃªn lÆ°Æ¡ng"
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
    assert "document sources separately" in prompt
    assert "Do not create a Sources" in prompt


def test_prompt_forbids_internal_retrieval_terms_in_final_answer() -> None:
    from uuid import uuid4

    from app.services.queries.query_contract_service import QueryContract
    from app.services.rag.rag_answer_service import ContextChunk, RagAnswerService

    chunk = SimpleNamespace(
        id=uuid4(),
        document_id=uuid4(),
        chunk_index=0,
        content="Sá»‘ 3113/EVN-KDMBD ngÃ y 02/06/2026 lÃ  vÄƒn báº£n cÄƒn cá»© triá»ƒn khai EVN CSKH.",
        chunk_metadata={"identifier_exact_boost": "1"},
    )
    query_contract = QueryContract(
        raw_query="vÄƒn báº£n 3113",
        detected_intent="identifier_lookup",
        target_contexts=["document_header", "references"],
        preferred_artifact_types=["document_profile"],
        output_shape="short_answer",
        citation_requirement="source_document",
        allow_chunk_fallback=True,
    )

    prompt = RagAnswerService._build_user_prompt(
        query="vÄƒn báº£n 3113",
        context_chunks=[ContextChunk(citation_index=1, chunk=chunk)],
        query_contract=query_contract,
    )

    assert "target_contexts" not in prompt
    assert "allow_chunk_fallback" not in prompt
    assert "Never mention how information was searched" in prompt
    assert "BM25" not in prompt
    assert "vector search" not in prompt


def test_clean_llm_answer_drops_internal_leak_notes() -> None:
    from app.services.rag.rag_answer_service import RagAnswerService

    answer = (
        "Sá»‘ 3113/EVN-KDMBD Ä‘Æ°á»£c nÃªu lÃ  vÄƒn báº£n cÄƒn cá»© ngÃ y 02/06/2026 vá» triá»ƒn khai EVN CSKH.\n"
        "LÆ°u Ã½: thÃ´ng tin nÃ y khÃ´ng xuáº¥t hiá»‡n trong target_contexts hoáº·c cÃ¡c Ä‘oáº¡n trÃ­ch há»“i phá»¥c.\n"
        "Nguá»“n: 907/EVNICT-TTPM"
    )

    cleaned = RagAnswerService._clean_llm_answer(answer)

    assert "3113/EVN-KDMBD" in cleaned
    assert "Nguá»“n: 907/EVNICT-TTPM" in cleaned
    assert "target_contexts" not in cleaned
    assert "Ä‘oáº¡n trÃ­ch" not in cleaned


def test_table_neighbor_expansion_prefers_matching_rows_and_headers() -> None:
    from uuid import uuid4

    from app.services.rag.rag_answer_service import ContextChunk, RagAnswerService

    document_id = uuid4()
    primary = SimpleNamespace(
        id=uuid4(),
        document_id=document_id,
        chunk_index=3,
        content=("TABLE_ROW table_id=pdf_p1_1 page=1 row=1 | Name: Nguyen Quang Lam | Area: Infrastructure"),
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
        content=("TABLE_ROW table_id=pdf_p1_1 page=1 row=2 | Name: Nguyen Quang Lam | Area: Data"),
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

    from app.services.rag.rag_answer_service import ContextChunk, RagAnswerService

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
        content=("TABLE_ROW table_id=pdf_p1_1 page=1 row=6 | Name: Nguyen Quang Lam | Area: Platform AI"),
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
        content=("TABLE_ROW table_id=pdf_p1_2 page=1 row=2 | Name: Nguyen Quang Lam | Area: OCR"),
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

    from app.services.rag.rag_answer_service import ContextChunk, RagAnswerService

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
                ("TABLE_ROW table_id=tbl_1 row=3 | Nhom nhiem vu: Xay dung nen tang RAG tren du lieu noi bo | Danh sach: Tong Phuoc Lam; Nguyen Quang Lam"),
                ("TABLE_ROW table_id=tbl_1 row=4 | Nhom nhiem vu: Xay dung dich vu OCR dung chung | Danh sach: Trinh Thanh Tinh; Nguyen Quang Lam"),
                ("TABLE_ROW table_id=tbl_1 row=5 | Nhom nhiem vu: Kho du lieu AI dung chung | Danh sach: Nguyen Quang Lam; Nguyen Trong Hung"),
                ("TABLE_ROW table_id=tbl_1 row=6 | Nhom nhiem vu: Platform AI | Danh sach: Cac nhan su trong ke hoach PoC ThinkLabs; Nguyen Quang Lam; Vo Van Phuc"),
            ],
            start=1,
        )
    ]
    unrelated_row = SimpleNamespace(
        id=uuid4(),
        document_id=document_id,
        chunk_index=99,
        content=("TABLE_ROW table_id=tbl_1 row=7 | Nhom nhiem vu: Xay dung nang luc mo hinh ngon ngu noi bo | Danh sach: Tran Van An"),
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

    from app.services.rag.rag_answer_service import ContextChunk, RagAnswerService

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
    assert "must have N bullet" in RagAnswerService._build_user_prompt.__globals__["TABLE_QA_STYLE"]


def test_user_prompt_splits_inline_table_rows_before_matching_entity() -> None:
    from uuid import uuid4

    from app.services.rag.rag_answer_service import ContextChunk, RagAnswerService

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


def test_structured_rows_are_passed_to_dynamic_prompt() -> None:
    from uuid import uuid4

    from app.services.rag.rag_answer_service import ContextChunk, RagAnswerService

    document_id = uuid4()
    rows = [
        SimpleNamespace(
            id=uuid4(),
            document_id=document_id,
            chunk_index=12,
            content="TrÆ°á»ng há»£p: Káº¿t hÃ´n; Nghá»‰ 03 ngÃ y hÆ°á»Ÿng nguyÃªn lÆ°Æ¡ng",
            chunk_metadata={
                "chunk_type": "legal_table_row",
                "relationship_type": "legal_leave_benefit",
                "case_code": "a",
                "case_name": "Káº¿t hÃ´n",
                "total_leave_days": 3,
                "total_leave_benefit": "Nghá»‰ 03 ngÃ y hÆ°á»Ÿng nguyÃªn lÆ°Æ¡ng",
                "labor_code_benefit": "Nghá»‰ 03 ngÃ y hÆ°á»Ÿng nguyÃªn lÆ°Æ¡ng",
                "table_name": "Äiá»u 10. Nghá»‰ viá»‡c riÃªng cÃ³ hÆ°á»Ÿng lÆ°Æ¡ng",
                "source_label": "Quy cháº¿ nghá»‰ hÆ°á»Ÿng lÆ°Æ¡ng máº«u",
                "source_file": "TULDTT CPC 2024 KY KET 11.10.2024.docx",
            },
        ),
        SimpleNamespace(
            id=uuid4(),
            document_id=document_id,
            chunk_index=13,
            content=("TrÆ°á»ng há»£p: Con Ä‘áº», con nuÃ´i káº¿t hÃ´n; Nghá»‰ 02 ngÃ y hÆ°á»Ÿng nguyÃªn lÆ°Æ¡ng (01 ngÃ y theo BLLÄ + 01 ngÃ y hÆ°á»Ÿng thÃªm theo TÆ¯LÄTT nÃ y)"),
            chunk_metadata={
                "chunk_type": "legal_table_row",
                "relationship_type": "legal_leave_benefit",
                "case_code": "b",
                "case_name": "Con Ä‘áº», con nuÃ´i káº¿t hÃ´n",
                "total_leave_days": 2,
                "total_leave_benefit": "Nghá»‰ 02 ngÃ y hÆ°á»Ÿng nguyÃªn lÆ°Æ¡ng",
                "labor_code_benefit": "Nghá»‰ 01 ngÃ y hÆ°á»Ÿng nguyÃªn lÆ°Æ¡ng",
                "collective_agreement_benefit": "Nghá»‰ 01 ngÃ y hÆ°á»Ÿng nguyÃªn lÆ°Æ¡ng",
                "table_name": "Äiá»u 10. Nghá»‰ viá»‡c riÃªng cÃ³ hÆ°á»Ÿng lÆ°Æ¡ng",
                "source_label": "Quy cháº¿ nghá»‰ hÆ°á»Ÿng lÆ°Æ¡ng máº«u",
                "source_file": "TULDTT CPC 2024 KY KET 11.10.2024.docx",
            },
        ),
        SimpleNamespace(
            id=uuid4(),
            document_id=document_id,
            chunk_index=17,
            content=(
                "TrÆ°á»ng há»£p: Cha hoáº·c máº¹ cá»§a NLÄ hoáº·c cá»§a vá»£ (chá»“ng) NLÄ "
                "káº¿t hÃ´n (ká»ƒ cáº£ bá»‘, máº¹ nuÃ´i Ä‘Æ°á»£c phÃ¡p luáº­t cÃ´ng nháº­n); "
                "Anh, chá»‹, em ruá»™t cá»§a NLÄ hoáº·c cá»§a vá»£ (chá»“ng) NLÄ káº¿t hÃ´n. "
                "Nghá»‰ 01 ngÃ y hÆ°á»Ÿng nguyÃªn lÆ°Æ¡ng vÃ  pháº£i thÃ´ng bÃ¡o vá»›i NSDLÄ"
            ),
            chunk_metadata={
                "chunk_type": "legal_table_row",
                "relationship_type": "legal_leave_benefit",
                "case_code": "f",
                "case_name": ("Cha hoáº·c máº¹ cá»§a NLÄ hoáº·c cá»§a vá»£ (chá»“ng) NLÄ káº¿t hÃ´n (ká»ƒ cáº£ bá»‘, máº¹ nuÃ´i Ä‘Æ°á»£c phÃ¡p luáº­t cÃ´ng nháº­n); Anh, chá»‹, em ruá»™t cá»§a NLÄ hoáº·c cá»§a vá»£ (chá»“ng) NLÄ káº¿t hÃ´n."),
                "total_leave_days": 1,
                "total_leave_benefit": "Nghá»‰ 01 ngÃ y hÆ°á»Ÿng nguyÃªn lÆ°Æ¡ng",
                "collective_agreement_benefit": ("Nghá»‰ 01 ngÃ y hÆ°á»Ÿng nguyÃªn lÆ°Æ¡ng vÃ  pháº£i thÃ´ng bÃ¡o vá»›i NSDLÄ"),
                "table_name": "Äiá»u 10. Nghá»‰ viá»‡c riÃªng cÃ³ hÆ°á»Ÿng lÆ°Æ¡ng",
                "source_label": "Quy cháº¿ nghá»‰ hÆ°á»Ÿng lÆ°Æ¡ng máº«u",
                "source_file": "TULDTT CPC 2024 KY KET 11.10.2024.docx",
            },
        ),
    ]
    context_chunks = [ContextChunk(citation_index=index, chunk=chunk) for index, chunk in enumerate(rows, start=1)]

    prompt = RagAnswerService._build_user_prompt(
        query="Con Ä‘áº» káº¿t hÃ´n Ä‘Æ°á»£c nghá»‰ bao nhiÃªu ngÃ y?",
        context_chunks=context_chunks,
    )

    assert "Document Text:" in prompt
    assert "Con Ä‘áº», con nuÃ´i káº¿t hÃ´n" in prompt
    assert "Nghá»‰ 02 ngÃ y hÆ°á»Ÿng nguyÃªn lÆ°Æ¡ng" in prompt
    assert "Káº¿t hÃ´n" in prompt
    assert "Dynamic answer requirements:" in prompt
    assert "For count questions, state the count first" in prompt


def test_direct_entity_guard_requires_context_that_mentions_entity() -> None:
    from uuid import uuid4

    from app.services.rag.rag_answer_service import ContextChunk, RagAnswerService

    unrelated_chunk = SimpleNamespace(
        id=uuid4(),
        document_id=uuid4(),
        chunk_index=1,
        content="This section is about training plans only.",
        chunk_metadata={"chunk_type": "docling_hybrid_repaired"},
    )
    matching_chunk = SimpleNamespace(
        id=uuid4(),
        document_id=uuid4(),
        chunk_index=2,
        content="Tá»‘ng PhÆ°á»›c LÃ¢m is listed in the retrieved table row.",
        chunk_metadata={"chunk_type": "table_row"},
    )

    query = "PhÆ°á»›c LÃ¢m tham gia vÃ o máº£ng cÃ´ng nghá»‡ nÃ o?"

    assert RagAnswerService._query_requires_direct_entity_evidence(
        query=query,
        context_chunks=[ContextChunk(citation_index=1, chunk=unrelated_chunk)],
    )
    assert not RagAnswerService._query_requires_direct_entity_evidence(
        query=query,
        context_chunks=[ContextChunk(citation_index=1, chunk=matching_chunk)],
    )


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
            search_terms=["Nguyá»…n Quang LÃ¢m", "Nguyen Quang Lam"],
        )
    )

    statement_text = str(fake_session.statement)
    params = fake_session.statement.compile().params
    values = [str(value) for value in params.values()]
    assert "AND" in statement_text
    assert any("%Nguyá»…n%" == value for value in values)
    assert any("%Quang%" == value for value in values)
    assert any("%LÃ¢m%" == value for value in values)


def test_user_prompt_keeps_narrative_context_when_table_rows_match() -> None:
    from uuid import uuid4

    from app.services.rag.rag_answer_service import ContextChunk, RagAnswerService

    document_id = uuid4()
    narrative_chunk = SimpleNamespace(
        id=uuid4(),
        document_id=document_id,
        chunk_index=5,
        content=(
            "3. XÃ¢y dá»±ng ná»n táº£ng RAG trÃªn dá»¯ liá»‡u ná»™i bá»™\n"
            "Má»¥c tiÃªu: Khai thÃ¡c tri thá»©c ná»™i bá»™ qua há»i Ä‘Ã¡p cÃ³ dáº«n nguá»“n, "
            "chÃ­nh xÃ¡c vÃ  Ä‘Æ°á»£c phÃ¢n quyá»n.\n"
            "- Kháº£o sÃ¡t vÃ  lá»±a chá»n ká»¹ thuáº­t RAG phÃ¹ há»£p.\n"
            "- Chuáº©n hÃ³a quy trÃ¬nh phÃ¢n Ä‘oáº¡n tÃ i liá»‡u, embedding vÃ  index.\n"
            "- Káº¿t há»£p tÃ¬m kiáº¿m tá»« khÃ³a vÃ  tÃ¬m kiáº¿m ngá»¯ nghÄ©a.\n"
            "- PhÃ¢n quyá»n truy há»“i theo tÃ i liá»‡u hoáº·c ngÆ°á»i dÃ¹ng."
        ),
        chunk_metadata={"chunk_type": "docling_hybrid_repaired"},
    )
    table_row = SimpleNamespace(
        id=uuid4(),
        document_id=document_id,
        chunk_index=19,
        content=("STT: 3\nMáº£ng cÃ´ng nghá»‡: XÃ¢y dá»±ng ná»n táº£ng RAG trÃªn dá»¯ liá»‡u ná»™i bá»™\nPhÃ²ng chá»§ trÃ¬: PTUD\nNhÃ¢n sá»± Ä‘á» xuáº¥t: Tá»‘ng PhÆ°á»›c LÃ¢m; Nguyá»…n Quang LÃ¢m"),
        chunk_metadata={"chunk_type": "table_row"},
    )

    prompt = RagAnswerService._build_user_prompt(
        query="Máº£ng cÃ´ng nghá»‡ RAG trÃªn dá»¯ liá»‡u ná»™i bá»™ cÃ³ má»¥c tiÃªu gÃ¬?",
        context_chunks=[
            ContextChunk(citation_index=1, chunk=table_row),
            ContextChunk(citation_index=2, chunk=narrative_chunk),
        ],
    )

    assert "ENTITY_MATCHED_ROWS:" in prompt
    assert "Document Text:" in prompt
    assert "Má»¥c tiÃªu: Khai thÃ¡c tri thá»©c ná»™i bá»™" in prompt
    assert "Káº¿t há»£p tÃ¬m kiáº¿m tá»« khÃ³a" in prompt
    assert "do not ignore narrative text" in prompt
    assert "summary followed by list items" in prompt


def test_dynamic_prompt_preserves_narrative_section_bullets() -> None:
    from uuid import uuid4

    from app.services.rag.rag_answer_service import ContextChunk, RagAnswerService

    document_id = uuid4()
    narrative_chunk = SimpleNamespace(
        id=uuid4(),
        document_id=document_id,
        chunk_index=5,
        content=(
            "3. XÃ¢y dá»±ng ná»n táº£ng RAG trÃªn dá»¯ liá»‡u ná»™i bá»™\n"
            "Má»¥c tiÃªu: Khai thÃ¡c tri thá»©c ná»™i bá»™ qua há»i Ä‘Ã¡p cÃ³ dáº«n nguá»“n, "
            "chÃ­nh xÃ¡c vÃ  Ä‘Æ°á»£c phÃ¢n quyá»n.\n"
            "- Kháº£o sÃ¡t, Ä‘Ã¡nh giÃ¡ vÃ  lá»±a chá»n ká»¹ thuáº­t RAG phÃ¹ há»£p.\n"
            "- Chuáº©n hÃ³a quy trÃ¬nh phÃ¢n Ä‘oáº¡n tÃ i liá»‡u, vector hÃ³a vÃ  láº­p chá»‰ má»¥c.\n"
            "- Káº¿t há»£p tÃ¬m kiáº¿m tá»« khÃ³a vÃ  tÃ¬m kiáº¿m ngá»¯ nghÄ©a.\n"
            "- XÃ¢y dá»±ng pipeline RAG dÃ¹ng chung cho toÃ n EVNCPC."
        ),
        chunk_metadata={"chunk_type": "docling_hybrid_repaired"},
    )
    table_row = SimpleNamespace(
        id=uuid4(),
        document_id=document_id,
        chunk_index=19,
        content=("STT: 3\nMáº£ng cÃ´ng nghá»‡: XÃ¢y dá»±ng ná»n táº£ng RAG trÃªn dá»¯ liá»‡u ná»™i bá»™\nPhÃ²ng chá»§ trÃ¬: PTUD"),
        chunk_metadata={"chunk_type": "table_row"},
    )

    prompt = RagAnswerService._build_user_prompt(
        query="Máº£ng cÃ´ng nghá»‡ RAG trÃªn dá»¯ liá»‡u ná»™i bá»™ cÃ³ má»¥c tiÃªu gÃ¬?",
        context_chunks=[
            ContextChunk(citation_index=1, chunk=table_row),
            ContextChunk(citation_index=2, chunk=narrative_chunk),
        ],
    )

    assert "Document Text:" in prompt
    assert "Khai thÃ¡c tri thá»©c ná»™i bá»™" in prompt
    assert "Kháº£o sÃ¡t, Ä‘Ã¡nh giÃ¡" in prompt
    assert "Chuáº©n hÃ³a quy trÃ¬nh" in prompt
    assert "Káº¿t há»£p tÃ¬m kiáº¿m tá»« khÃ³a" in prompt
    assert "pipeline RAG dÃ¹ng chung" in prompt
    assert "For narrative evidence, preserve the relevant section heading" in prompt


def test_schema_count_query_uses_dynamic_count_prompt() -> None:
    from uuid import uuid4

    from app.services.rag.rag_answer_service import ContextChunk, RagAnswerService

    narrative_chunk = SimpleNamespace(
        id=uuid4(),
        document_id=uuid4(),
        chunk_index=7,
        content=("CÃC Lá»šP Dá»® LIá»†U\nLuá»“ng dá»¯ liá»‡u: CMIS/TTHT â†’ LÆ°u trá»¯ & Tá»•ng há»£p â†’ GIS Háº¡ tháº¿.\n- ThÃ´ng tin Tráº¡m, Sá»•.\n- ThÃ´ng tin khÃ¡ch hÃ ng.\n- Lá»›p Ä‘iá»ƒm Ä‘o."),
        chunk_metadata={"chunk_type": "docling_hybrid_repaired"},
    )

    prompt = RagAnswerService._build_user_prompt(
        query="Khung CSDL gis háº¡ tháº¿ cÃ³ máº¥y lá»›p thuá»™c tÃ­nh",
        context_chunks=[ContextChunk(citation_index=2, chunk=narrative_chunk)],
    )

    assert "Document Text:" in prompt
    assert "CÃC Lá»šP Dá»® LIá»†U" in prompt
    assert "Reading notes" in prompt
    assert "overview_summary" in prompt
    assert "count_list" in prompt
    assert "multi_hop" in prompt
    assert "For count questions, state the count first" in prompt
    assert "If the document text is insufficient or conflicting" in prompt

def test_count_prompt_separates_evidence_categories_without_domain_template() -> None:
    from uuid import uuid4

    from app.services.rag.rag_answer_service import ContextChunk, RagAnswerService

    summary_chunk = SimpleNamespace(
        id=uuid4(),
        document_id=uuid4(),
        chunk_index=8,
        content=(
            "3. Khá»Ÿi táº¡o bá»• sung 03 báº£ng dá»¯ liá»‡u thuá»™c tÃ­nh\n"
            "HinhAnhCotDien; HinhAnhKhachHang; HinhAnhHoSoKhachHang.\n"
            "Khung CSDL GIS háº¡ tháº¿ tá»•ng thá»ƒ cÃ³ 11 lá»›p dá»¯ liá»‡u GIS; "
            "07 lá»›p Ä‘á»‘i tÆ°á»£ng chÃ­nh Ä‘Æ°á»£c Æ°u tiÃªn, 04 lá»›p cÃ²n láº¡i thá»±c hiá»‡n giai Ä‘oáº¡n sau."
        ),
        chunk_metadata={"chunk_type": "docling_hybrid_repaired"},
    )

    prompt = RagAnswerService._build_user_prompt(
        query="Khung CSDL gis háº¡ tháº¿ cÃ³ máº¥y lá»›p thuá»™c tÃ­nh",
        context_chunks=[ContextChunk(citation_index=1, chunk=summary_chunk)],
    )

    assert "03 báº£ng dá»¯ liá»‡u thuá»™c tÃ­nh" in prompt
    assert "11 lá»›p dá»¯ liá»‡u GIS" in prompt
    assert "different groups, tables, layers, sections" in prompt
    assert "Do not merge partial, priority, phase, or subtype counts" in prompt
    assert "GIS/spatial" not in prompt


def test_overview_prompt_omits_field_level_schema_when_structural_context_exists() -> None:
    from uuid import uuid4

    from app.services.rag.rag_answer_service import ContextChunk, RagAnswerService

    document_id = uuid4()
    structural_chunk = SimpleNamespace(
        id=uuid4(),
        document_id=document_id,
        chunk_index=1,
        content=(
            "Khung CSDL tá»•ng thá»ƒ cÃ³ 11 lá»›p dá»¯ liá»‡u vÃ  03 báº£ng dá»¯ liá»‡u thuá»™c tÃ­nh: "
            "HinhAnhCotDien, HinhAnhKhachHang, HinhAnhHoSoKhachHang."
        ),
        chunk_metadata={"chunk_type": "attribute_table_schema"},
    )
    structural_table_chunk = SimpleNamespace(
        id=uuid4(),
        document_id=document_id,
        chunk_index=2,
        content=(
            "3. Khá»Ÿi táº¡o bá»• sung 03 báº£ng dá»¯ liá»‡u thuá»™c tÃ­nh. "
            "TÃªn báº£ng dá»¯ liá»‡u: HinhAnhCotDien, HinhAnhKhachHang, HinhAnhHoSoKhachHang."
        ),
        chunk_metadata={
            "chunk_type": "table_complete",
            "field_names": ["IDHinhAnh", "DuongDan"],
            "retrieval_roles": ["structural_schema_overview"],
            "schema_overview": True,
            "schema_coverage_priority": 0,
        },
    )
    field_chunk = SimpleNamespace(
        id=uuid4(),
        document_id=document_id,
        chunk_index=3,
        content="F08_CotDien_HT cÃ³ trÆ°á»ng Trá»‹ sá»‘ tiáº¿p Ä‘á»‹a vÃ  Chiá»u cao cá»™t.",
        chunk_metadata={"chunk_type": "schema_field_row", "field_name": "TriSoTiepDia"},
    )

    prompt = RagAnswerService._build_user_prompt(
        query="Khung CSDL gis háº¡ tháº¿ cÃ³ máº¥y lá»›p thuá»™c tÃ­nh",
        context_chunks=[
            ContextChunk(citation_index=1, chunk=structural_chunk),
            ContextChunk(citation_index=2, chunk=structural_table_chunk),
            ContextChunk(citation_index=3, chunk=field_chunk),
        ],
    )

    assert "HinhAnhCotDien" in prompt
    assert "03 báº£ng dá»¯ liá»‡u thuá»™c tÃ­nh" in prompt
    assert "SCHEMA_OVERVIEW_EVIDENCE" in prompt
    assert "Trá»‹ sá»‘ tiáº¿p Ä‘á»‹a" not in prompt
    assert "Do not switch to field-level schemas" in prompt


def test_field_detail_prompt_uses_profile_query_intent_rules() -> None:
    from uuid import uuid4

    from app.services.rag.rag_answer_service import ContextChunk, RagAnswerService

    document_id = uuid4()
    structural_chunk = SimpleNamespace(
        id=uuid4(),
        document_id=document_id,
        chunk_index=1,
        content="Khung CSDL tá»•ng thá»ƒ cÃ³ 11 lá»›p dá»¯ liá»‡u.",
        chunk_metadata={"chunk_type": "attribute_table_schema"},
    )
    field_chunk = SimpleNamespace(
        id=uuid4(),
        document_id=document_id,
        chunk_index=2,
        content="F08_CotDien_HT cÃ³ trÆ°á»ng Trá»‹ sá»‘ tiáº¿p Ä‘á»‹a vÃ  Chiá»u cao cá»™t.",
        chunk_metadata={"chunk_type": "schema_field_row", "field_name": "TriSoTiepDia"},
    )

    prompt = RagAnswerService._build_user_prompt(
        query="schema-field khung CSDL gis háº¡ tháº¿ cÃ³ máº¥y lá»›p thuá»™c tÃ­nh",
        context_chunks=[
            ContextChunk(citation_index=1, chunk=structural_chunk),
            ContextChunk(citation_index=2, chunk=field_chunk),
        ],
        query_intent_rules={
            "field_detail_schema": {
                "direct_terms": ["schema-field"],
                "required_any_terms": [],
                "specific_item_patterns": [],
                "phrases": [],
            }
        },
    )

    assert "Khung CSDL tá»•ng thá»ƒ" in prompt
    assert "Trá»‹ sá»‘ tiáº¿p Ä‘á»‹a" in prompt


def test_prompt_lists_structured_relationship_evidence_when_available() -> None:
    from uuid import uuid4

    from app.services.rag.rag_answer_service import ContextChunk, RagAnswerService

    document_id = uuid4()
    summary_chunk = SimpleNamespace(
        id=uuid4(),
        document_id=document_id,
        chunk_index=8,
        content="Khá»Ÿi táº¡o bá»• sung 03 má»‘i quan há»‡ giá»¯a lá»›p dá»¯ liá»‡u vÃ  báº£ng thuá»™c tÃ­nh.",
        chunk_metadata={"chunk_type": "docling_hybrid_repaired"},
    )
    relationship_chunk = SimpleNamespace(
        id=uuid4(),
        document_id=document_id,
        chunk_index=9,
        content="TÃªn má»‘i quan há»‡: PXXXXX_CotDien_HT_HinhAnhCotDien",
        chunk_metadata={
            "chunk_type": "relationship_definition",
            "relationship_name": "PXXXXX_CotDien_HT_HinhAnhCotDien",
            "source_layer": "F08_CotDien_HT",
            "source_key": "ID",
            "target_table": "HinhAnhCotDien",
            "target_key": "IDCotDien",
            "cardinality": "1-M",
        },
    )

    prompt = RagAnswerService._build_user_prompt(
        query="Khung CSDL gis háº¡ tháº¿ cÃ³ máº¥y lá»›p thuá»™c tÃ­nh",
        context_chunks=[
            ContextChunk(citation_index=1, chunk=summary_chunk),
            ContextChunk(citation_index=2, chunk=relationship_chunk),
        ],
    )

    assert "STRUCTURED_RELATIONSHIP_EVIDENCE" in prompt
    assert "PXXXXX_CotDien_HT_HinhAnhCotDien" in prompt
    assert "F08_CotDien_HT -> HinhAnhCotDien" in prompt
    assert "ID -> IDCotDien" in prompt
    assert "1-M" in prompt
    assert "include those item names or relationship endpoints" in prompt


def test_schema_count_search_terms_are_query_derived() -> None:
    from app.services.rag.rag_answer_service import RagAnswerService

    query_terms = ["Khung CSDL gis háº¡ tháº¿ cÃ³ máº¥y lá»›p thuá»™c tÃ­nh"]

    terms = RagAnswerService._schema_count_search_terms(query_terms)
    normalized = [RagAnswerService._strip_vietnamese_accents(term).casefold() for term in terms]

    assert RagAnswerService._is_schema_count_query(query_terms[0])
    assert any("csdl" in term for term in normalized)
    assert any("bang du lieu thuoc tinh" in term for term in normalized)
    assert any("gis" in term for term in normalized)
    assert all(term != "lop du lieu gis" for term in normalized)
    assert all("hinhanh" not in term for term in normalized)
    assert all("cotdien_ht" not in term for term in normalized)

def test_strategy_enriched_query_adds_search_terms_not_answer_facts() -> None:
    from app.services.queries.query_strategy import classify_query_strategy
    from app.services.rag.rag_answer_service import RagAnswerService

    query = "Khung CSDL gis háº¡ tháº¿ cÃ³ máº¥y lá»›p thuá»™c tÃ­nh"
    enriched = RagAnswerService._strategy_enriched_query(
        query,
        query_strategy=classify_query_strategy(query),
    )

    assert "Retrieval expansion terms derived from query strategy" in enriched
    assert "document summary" in enriched
    assert "heading outline" in enriched
    assert "not as answer facts" in enriched


def test_query_terms_extract_generic_keyphrases_for_section_recovery() -> None:
    from app.services.rag.rag_answer_service import RagAnswerService

    terms = RagAnswerService._query_terms("Máº£ng cÃ´ng nghá»‡ RAG trÃªn dá»¯ liá»‡u ná»™i bá»™ cÃ³ má»¥c tiÃªu gÃ¬?")

    normalized_terms = [RagAnswerService._strip_vietnamese_accents(term).casefold() for term in terms]
    assert "rag" in normalized_terms
    assert any("rag tren du lieu noi bo" in term for term in normalized_terms)
    assert any("muc tieu" in term for term in normalized_terms)


def test_high_signal_coverage_chunk_can_override_context_budget() -> None:
    import asyncio
    from uuid import uuid4

    from app.services.rag.rag_answer_service import ContextChunk, RagAnswerService

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
        content=("3. XÃ¢y dá»±ng ná»n táº£ng RAG trÃªn dá»¯ liá»‡u ná»™i bá»™. Má»¥c tiÃªu: Khai thÃ¡c tri thá»©c ná»™i bá»™ qua há»i Ä‘Ã¡p cÃ³ dáº«n nguá»“n."),
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
            query="Máº£ng cÃ´ng nghá»‡ RAG trÃªn dá»¯ liá»‡u ná»™i bá»™ cÃ³ má»¥c tiÃªu gÃ¬?",
            context_chunks=[ContextChunk(citation_index=1, chunk=primary)],
            max_context_chars=200,
        )
    )

    assert any("Má»¥c tiÃªu: Khai thÃ¡c tri thá»©c ná»™i bá»™" in item.chunk.content for item in expanded)

def test_schema_count_expansion_prioritizes_structural_schema_chunks() -> None:
    import asyncio
    from uuid import uuid4

    from app.services.rag.rag_answer_service import ContextChunk, RagAnswerService

    document_id = uuid4()
    primary = SimpleNamespace(
        id=uuid4(),
        document_id=document_id,
        chunk_index=11,
        content="1. Má»¥c tiÃªu - Khá»Ÿi táº¡o khung CSDL GIS háº¡ tháº¿ bao gá»“m 10 Ä‘á»‘i tÆ°á»£ng." + (" X" * 10000),
        chunk_metadata={"chunk_type": "docling_hybrid_repaired"},
    )
    field_row = SimpleNamespace(
        id=uuid4(),
        document_id=document_id,
        chunk_index=14,
        content="F08_CotDien_HT cÃ³ cÃ¡c trÆ°á»ng thuá»™c tÃ­nh: ID, MaTramBienAp, ViTriCotHaThe.",
        chunk_metadata={"chunk_type": "schema_field_row", "field_name": "MaTramBienAp"},
    )
    structural_table = SimpleNamespace(
        id=uuid4(),
        document_id=document_id,
        chunk_index=30,
        content=(
            "3. Khá»Ÿi táº¡o bá»• sung 03 báº£ng dá»¯ liá»‡u thuá»™c tÃ­nh. "
            "TÃªn báº£ng dá»¯ liá»‡u: HinhAnhCotDien; HinhAnhKhachHang; HinhAnhHoSoKhachHang."
        ),
        chunk_metadata={
            "chunk_type": "table_complete",
            "field_names": ["IDHinhAnh"],
            "retrieval_roles": ["structural_schema_overview"],
            "schema_overview": True,
            "schema_coverage_priority": 0,
        },
    )
    relationship = SimpleNamespace(
        id=uuid4(),
        document_id=document_id,
        chunk_index=31,
        content="4. Khá»Ÿi táº¡o bá»• sung 03 má»‘i quan há»‡ giá»¯a lá»›p dá»¯ liá»‡u GIS vá»›i báº£ng dá»¯ liá»‡u 1-M.",
        chunk_metadata={"chunk_type": "relationship_definition", "relationship_name": "R1"},
    )

    class FakeCoverageRepo:
        async def get_entity_coverage_chunks(self, *, document_id, search_terms, exclude_ids):
            assert any("báº£ng dá»¯ liá»‡u thuá»™c tÃ­nh" in term for term in search_terms)
            return [field_row, structural_table, relationship]

    service = RagAnswerService(
        chat_repository=FakeCoverageRepo(),  # type: ignore[arg-type]
        reranking_service=SimpleNamespace(),  # type: ignore[arg-type]
        llm_provider=SimpleNamespace(),  # type: ignore[arg-type]
    )

    expanded = asyncio.run(
        service._expand_with_neighbors(
            query="Khung CSDL gis háº¡ tháº¿ cÃ³ máº¥y lá»›p thuá»™c tÃ­nh",
            context_chunks=[ContextChunk(citation_index=1, chunk=primary)],
            max_context_chars=200,
        )
    )

    expanded_contents = [item.chunk.content for item in expanded]
    assert any("03 báº£ng dá»¯ liá»‡u thuá»™c tÃ­nh" in content for content in expanded_contents)
    assert any("03 má»‘i quan há»‡" in content for content in expanded_contents)
    structural_index = next(
        index for index, content in enumerate(expanded_contents) if "03 báº£ng dá»¯ liá»‡u thuá»™c tÃ­nh" in content
    )
    field_index = next(index for index, content in enumerate(expanded_contents) if "MaTramBienAp" in content)
    assert structural_index < field_index
