import asyncio
from types import SimpleNamespace
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from app.api.routes import memory as memory_routes
from app.core.config import Settings
from app.main import app
from app.models.memory import SessionSummary, UserMemory
from app.services.memory.memory_base import MemoryResult
from app.services.memory.memory_hybrid_memory_provider import HybridMemoryProvider
from app.services.memory.memory_local_memory_provider import LocalMemoryProvider
from app.services.memory.memory_mem0_provider import Mem0Provider
from app.services.rag.rag_answer_service import ContextChunk, RagAnswerService

USER_A = SimpleNamespace(id=uuid4(), organization_id=uuid4())
USER_B = SimpleNamespace(id=uuid4(), organization_id=uuid4())


class InMemoryMemoryRepository:
    def __init__(self) -> None:
        self.rows: list[SimpleNamespace] = []

    async def create_memory(
        self,
        *,
        user_id,
        organization_id,
        content,
        memory_type,
        source,
        confidence=1.0,
        metadata=None,
    ):
        row = SimpleNamespace(
            id=uuid4(),
            user_id=user_id,
            organization_id=organization_id,
            content=content,
            memory_type=memory_type,
            source=source,
            confidence=confidence,
            is_active=True,
            memory_metadata=metadata,
        )
        self.rows.append(row)
        return row

    async def list_memories(self, *, user_id, limit, offset):
        owned = [r for r in self.rows if r.user_id == user_id and r.is_active]
        return owned[offset : offset + limit]

    async def search_memories(self, *, user_id, query, limit):
        owned = [
            r
            for r in self.rows
            if r.user_id == user_id
            and r.is_active
            and (not query.strip() or query.lower() in r.content.lower())
        ]
        return owned[:limit]

    async def get_memory_for_user(self, *, user_id, memory_id):
        for row in self.rows:
            if row.id == memory_id and row.user_id == user_id:
                return row
        return None

    async def deactivate_memory(self, memory) -> None:
        memory.is_active = False

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None


class FakeMem0Client:
    def __init__(self) -> None:
        self.added: list[tuple] = []

    def add(self, messages, user_id, metadata):
        self.added.append((messages, user_id, metadata))
        return {"results": [{"id": "mem0-1"}]}

    def search(self, query, user_id, limit):
        return {
            "results": [
                {
                    "id": "mem0-1",
                    "memory": "User prefers concise answers.",
                    "metadata": {"memory_type": "preference"},
                    "score": 0.91,
                }
            ]
        }

    def get_all(self, user_id):
        return {"results": [{"id": "mem0-1", "memory": "User prefers concise answers."}]}

    def delete(self, memory_id):
        return None


class FailingMem0Provider:
    async def add_memory(self, *, user, content, memory_type, metadata=None):
        raise RuntimeError("mem0 unavailable")

    async def search_memory(self, *, user, query, limit):
        raise RuntimeError("mem0 unavailable")


def test_user_memories_model_imports() -> None:
    assert UserMemory.__tablename__ == "user_memories"
    assert SessionSummary.__tablename__ == "session_summaries"


def test_local_provider_creates_and_lists_memory() -> None:
    async def run_test() -> None:
        repository = InMemoryMemoryRepository()
        provider = LocalMemoryProvider(repository=repository)

        result = await provider.add_memory(
            user=USER_A,
            content="Likes dark mode",
            memory_type="preference",
        )
        assert result.content == "Likes dark mode"
        assert result.source == "manual"

        listed = await provider.list_memory(user=USER_A, limit=10, offset=0)
        assert len(listed) == 1
        assert listed[0].content == "Likes dark mode"

    asyncio.run(run_test())


def test_local_provider_isolates_users() -> None:
    async def run_test() -> None:
        repository = InMemoryMemoryRepository()
        provider = LocalMemoryProvider(repository=repository)

        await provider.add_memory(
            user=USER_A,
            content="User A secret preference",
            memory_type="preference",
        )

        a_results = await provider.list_memory(user=USER_A, limit=10, offset=0)
        b_results = await provider.list_memory(user=USER_B, limit=10, offset=0)
        b_search = await provider.search_memory(user=USER_B, query="secret", limit=5)

        assert len(a_results) == 1
        assert b_results == []
        assert b_search == []

    asyncio.run(run_test())


def test_memory_injected_into_prompt_when_present() -> None:
    context_chunk = ContextChunk(
        citation_index=1,
        chunk=SimpleNamespace(content="Document chunk content."),
    )
    prompt = RagAnswerService._build_user_prompt(
        query="What now?",
        context_chunks=[context_chunk],
        memory_context=[
            MemoryResult(
                content="User prefers Vietnamese.",
                memory_type="preference",
                source="manual",
            )
        ],
        session_summary="Earlier the user asked about onboarding.",
    )

    assert "User Memory:" in prompt
    assert "User prefers Vietnamese." in prompt
    assert "Session Summary:" in prompt
    assert "Document Text:" in prompt
    assert "[1] Document chunk content." in prompt


def test_memory_not_injected_when_absent() -> None:
    context_chunk = ContextChunk(
        citation_index=1,
        chunk=SimpleNamespace(content="Document chunk content."),
    )
    prompt = RagAnswerService._build_user_prompt(
        query="What now?",
        context_chunks=[context_chunk],
    )

    assert "User Memory:" not in prompt
    assert "Session Summary:" not in prompt
    # Document citation context is unchanged.
    assert "[1] Document chunk content." in prompt


def test_mem0_provider_can_be_mocked() -> None:
    async def run_test() -> None:
        client = FakeMem0Client()
        provider = Mem0Provider(client=client, user_prefix="hbrag")

        result = await provider.add_memory(
            user=USER_A,
            content="Prefers concise answers",
            memory_type="preference",
        )
        assert result.id == "mem0-1"
        assert client.added[0][1] == f"hbrag:{USER_A.id}"
        assert client.added[0][2]["hbrag_user_id"] == str(USER_A.id)

        searched = await provider.search_memory(user=USER_A, query="answers", limit=5)
        assert searched[0].source == "mem0"
        assert searched[0].memory_type == "preference"

    asyncio.run(run_test())


def test_hybrid_local_save_succeeds_when_mem0_fails() -> None:
    async def run_test() -> None:
        repository = InMemoryMemoryRepository()
        local_provider = LocalMemoryProvider(repository=repository)
        hybrid = HybridMemoryProvider(
            local_provider=local_provider,
            mem0_provider=FailingMem0Provider(),
            top_k=5,
        )

        result = await hybrid.add_memory(
            user=USER_A,
            content="Always greet formally",
            memory_type="instruction",
        )
        assert result.content == "Always greet formally"

        listed = await local_provider.list_memory(user=USER_A, limit=10, offset=0)
        assert len(listed) == 1

        # Search still works even though Mem0 fails.
        searched = await hybrid.search_memory(user=USER_A, query="greet", limit=5)
        assert len(searched) == 1

    asyncio.run(run_test())


def test_memory_settings_endpoint_hides_api_key(monkeypatch) -> None:
    configured = Settings(
        _env_file=None,
        memory_enabled=True,
        memory_provider="hybrid",
        mem0_enabled=True,
        mem0_api_key="super-secret-mem0-key",
        memory_top_k=7,
    )
    monkeypatch.setattr(memory_routes, "settings", configured)

    client = TestClient(app)
    response = client.get("/api/memory/settings")

    assert response.status_code == 200
    payload = response.json()
    assert payload == {
        "memory_enabled": True,
        "memory_provider": "hybrid",
        "mem0_enabled": True,
        "memory_top_k": 7,
        "memory_auto_save": True,
        "memory_inject_into_prompt": True,
    }
    assert "mem0_api_key" not in payload
    assert "super-secret-mem0-key" not in response.text


def test_patch_memory_settings_returns_not_implemented() -> None:
    client = TestClient(app)
    response = client.patch("/api/memory/settings")
    assert response.status_code == 501


def test_memory_id_validation_rejects_invalid_uuid() -> None:
    async def run_test() -> None:
        repository = InMemoryMemoryRepository()
        provider = LocalMemoryProvider(repository=repository)
        deleted = await provider.delete_memory(user=USER_A, memory_id="not-a-uuid")
        assert deleted is False

    asyncio.run(run_test())


def test_uuid_constant_usage() -> None:
    # Ensure UUID import is used and helper objects are valid.
    assert isinstance(UUID(str(USER_A.id)), UUID)
