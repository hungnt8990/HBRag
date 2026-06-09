from types import SimpleNamespace
from uuid import UUID

import pytest

from app.api.dependencies.auth import get_current_user
from app.api.routes import chat, documents, knowledge_bases, search
from app.main import app

TEST_USER_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
TEST_ORGANIZATION_ID = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
TEST_KNOWLEDGE_BASE_ID = UUID("cccccccc-cccc-cccc-cccc-cccccccccccc")
TEST_DOCUMENT_IDS = {
    UUID("11111111-1111-1111-1111-111111111111"),
    UUID("22222222-2222-2222-2222-222222222222"),
    UUID("33333333-3333-3333-3333-333333333333"),
    UUID("44444444-4444-4444-4444-444444444444"),
    UUID("66666666-6666-6666-6666-666666666666"),
    UUID("88888888-8888-8888-8888-888888888888"),
    UUID("99999999-9999-9999-9999-999999999999"),
    UUID("dddddddd-4444-4444-4444-dddddddddddd"),
}


class FakeAuthRepository:
    async def get_descendant_organization_ids(self, organization_id: UUID) -> set[UUID]:
        return {organization_id, TEST_ORGANIZATION_ID}


class FakeDocumentLogRepository:
    def __init__(self) -> None:
        self.pipeline_logs = []
        self.access_logs = []
        self.committed = False
        self.rolled_back = False

    async def create_pipeline_log(self, **kwargs):
        self.pipeline_logs.append(kwargs)
        return SimpleNamespace(**kwargs)

    async def create_access_log(self, **kwargs):
        self.access_logs.append(kwargs)
        return SimpleNamespace(**kwargs)

    async def latest_pipeline_logs(self, **kwargs):
        return []

    async def access_log_summary(self, **kwargs):
        return {}

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        self.rolled_back = True


class FakeMemoryRepository:
    async def search_memories(self, **kwargs):
        return []

    async def list_memories(self, **kwargs):
        return []

    async def get_session_summary(self, **kwargs):
        return None

    async def get_memory_for_user(self, **kwargs):
        return None

    async def create_memory(self, **kwargs):
        return SimpleNamespace(**kwargs)

    async def deactivate_memory(self, memory) -> None:
        return None

    async def upsert_session_summary(self, **kwargs):
        return SimpleNamespace(**kwargs)

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None


class FakeSearchRepository:
    async def list_documents_for_permission_check(self, *, knowledge_base_ids=None):
        if knowledge_base_ids is not None and TEST_KNOWLEDGE_BASE_ID not in knowledge_base_ids:
            return []
        return [
            SimpleNamespace(
                id=document_id,
                organization_id=TEST_ORGANIZATION_ID,
                knowledge_base_id=TEST_KNOWLEDGE_BASE_ID,
                uploaded_by_user_id=None,
                visibility="global",
            )
            for document_id in TEST_DOCUMENT_IDS
        ]

class FakeKnowledgeBaseRepository:
    def __init__(self) -> None:
        self.default = SimpleNamespace(
            id=TEST_KNOWLEDGE_BASE_ID,
            name="Default Knowledge Base",
            description=None,
            organization_id=TEST_ORGANIZATION_ID,
            owner_user_id=TEST_USER_ID,
            visibility="organization",
            is_active=True,
            members=[],
        )

    async def get_by_id(self, knowledge_base_id: UUID):
        if knowledge_base_id == TEST_KNOWLEDGE_BASE_ID:
            return self.default
        return None

    async def get_by_ids(self, knowledge_base_ids):
        return [self.default for item in knowledge_base_ids if item == TEST_KNOWLEDGE_BASE_ID]

    async def get_or_create_default(self, **kwargs):
        return self.default


@pytest.fixture(autouse=True)
def default_authenticated_user():
    user = SimpleNamespace(
        id=TEST_USER_ID,
        username="test-admin",
        email="test-admin@example.com",
        full_name="Test Admin",
        organization_id=TEST_ORGANIZATION_ID,
        organization=SimpleNamespace(
            id=TEST_ORGANIZATION_ID,
            ma_dviqly="CPC",
            ma_dviqly_cha=None,
            ten_dviqly="CPC",
            dvi_level=1,
            parent_id=None,
        ),
        roles=[SimpleNamespace(name="SUPER_ADMIN")],
        is_active=True,
    )

    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[documents.get_auth_repository] = lambda: FakeAuthRepository()
    app.dependency_overrides[search.get_search_repository] = lambda: FakeSearchRepository()
    app.dependency_overrides[chat.get_document_repository] = lambda: FakeSearchRepository()
    app.dependency_overrides[documents.get_knowledge_base_repository] = (
        lambda: FakeKnowledgeBaseRepository()
    )
    app.dependency_overrides[search.get_knowledge_base_repository] = (
        lambda: FakeKnowledgeBaseRepository()
    )
    app.dependency_overrides[chat.get_knowledge_base_repository] = (
        lambda: FakeKnowledgeBaseRepository()
    )
    app.dependency_overrides[knowledge_bases.get_knowledge_base_repository] = (
        lambda: FakeKnowledgeBaseRepository()
    )
    app.dependency_overrides[chat.get_memory_repository] = lambda: FakeMemoryRepository()
    app.dependency_overrides[search.get_auth_repository] = lambda: FakeAuthRepository()
    app.dependency_overrides[chat.get_auth_repository] = lambda: FakeAuthRepository()
    app.dependency_overrides[documents.get_document_log_repository] = (
        lambda: FakeDocumentLogRepository()
    )
    app.dependency_overrides[chat.get_document_log_repository] = lambda: FakeDocumentLogRepository()
    yield
    app.dependency_overrides.clear()
