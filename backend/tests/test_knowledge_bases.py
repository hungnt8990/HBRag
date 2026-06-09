from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID

from fastapi.testclient import TestClient

from app.api.dependencies.auth import get_current_user
from app.api.routes.knowledge_bases import (
    get_auth_repository,
    get_knowledge_base_repository,
)
from app.main import app

USER_ID = UUID("10000000-0000-0000-0000-000000000001")
OTHER_USER_ID = UUID("10000000-0000-0000-0000-000000000002")
ORG_ID = UUID("20000000-0000-0000-0000-000000000001")
KB_ID = UUID("30000000-0000-0000-0000-000000000001")


class FakeAuthRepository:
    async def get_descendant_organization_ids(self, organization_id: UUID) -> set[UUID]:
        return {organization_id}


class FakeKnowledgeBaseRepository:
    def __init__(self, knowledge_base=None) -> None:
        self.knowledge_base = knowledge_base
        self.committed = False

    async def create(self, **kwargs):
        self.knowledge_base = _knowledge_base(**kwargs)
        return self.knowledge_base

    async def get_by_id(self, knowledge_base_id: UUID):
        if knowledge_base_id == KB_ID:
            return self.knowledge_base
        return None

    async def update(self, knowledge_base, **kwargs):
        for key, value in kwargs.items():
            if value is not None:
                setattr(knowledge_base, key, value)
        return knowledge_base

    async def commit(self) -> None:
        self.committed = True


def test_create_knowledge_base_endpoint() -> None:
    repository = FakeKnowledgeBaseRepository()
    app.dependency_overrides[get_current_user] = lambda: _user()
    app.dependency_overrides[get_knowledge_base_repository] = lambda: repository
    app.dependency_overrides[get_auth_repository] = lambda: FakeAuthRepository()

    try:
        client = TestClient(app)
        response = client.post(
            "/api/knowledge-bases",
            json={"name": "Policies", "visibility": "organization"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 201
    payload = response.json()
    assert payload["id"] == str(KB_ID)
    assert payload["name"] == "Policies"
    assert payload["organization_id"] == str(ORG_ID)
    assert payload["owner_user_id"] == str(USER_ID)
    assert repository.committed is True


def test_private_knowledge_base_requires_owner_or_member() -> None:
    repository = FakeKnowledgeBaseRepository(
        _knowledge_base(
            name="Private KB",
            owner_user_id=OTHER_USER_ID,
            visibility="private",
        )
    )
    app.dependency_overrides[get_current_user] = lambda: _user(role="UNIT_USER")
    app.dependency_overrides[get_knowledge_base_repository] = lambda: repository
    app.dependency_overrides[get_auth_repository] = lambda: FakeAuthRepository()

    try:
        client = TestClient(app)
        get_response = client.get(f"/api/knowledge-bases/{KB_ID}")
        patch_response = client.patch(
            f"/api/knowledge-bases/{KB_ID}",
            json={"name": "Blocked"},
        )
    finally:
        app.dependency_overrides.clear()

    assert get_response.status_code == 403
    assert patch_response.status_code == 403


def _user(*, role: str = "SUPER_ADMIN"):
    return SimpleNamespace(
        id=USER_ID,
        username="tester",
        email="tester@example.com",
        full_name="Test User",
        organization_id=ORG_ID,
        roles=[SimpleNamespace(name=role)],
        is_active=True,
    )


def _knowledge_base(
    *,
    name: str,
    description: str | None = None,
    organization_id: UUID | None = ORG_ID,
    owner_user_id: UUID | None = USER_ID,
    visibility: str = "organization",
):
    now = datetime(2026, 6, 9, tzinfo=UTC)
    return SimpleNamespace(
        id=KB_ID,
        name=name,
        description=description,
        organization_id=organization_id,
        owner_user_id=owner_user_id,
        visibility=visibility,
        is_active=True,
        members=[],
        created_at=now,
        updated_at=now,
    )
