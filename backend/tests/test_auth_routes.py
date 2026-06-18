from types import SimpleNamespace
from uuid import UUID

from fastapi.testclient import TestClient

from app.api.routes import auth
from app.core.security import hash_password
from app.main import app
from tests.conftest import TEST_ORGANIZATION_ID, TEST_USER_ID


class FakeAuthRepository:
    async def get_user_by_username(self, username: str):
        if username != "admin":
            return None
        return SimpleNamespace(
            id=TEST_USER_ID,
            username="admin",
            hashed_password=hash_password("secret"),
            is_active=True,
            roles=[SimpleNamespace(name="SUPER_ADMIN")],
            organization=SimpleNamespace(
                id=TEST_ORGANIZATION_ID,
                ma_dviqly="CPC",
                ma_dviqly_cha=None,
                ten_dviqly="CPC",
                dvi_level=1,
                parent_id=None,
            ),
        )

    async def get_descendant_organization_ids(self, organization_id: UUID) -> set[UUID]:
        return {organization_id, UUID("dddddddd-dddd-dddd-dddd-dddddddddddd")}

    async def list_organizations(self):
        return [
            SimpleNamespace(
                id=TEST_ORGANIZATION_ID,
                ma_dviqly="CPC",
                ma_dviqly_cha=None,
                ten_dviqly="EVNCPC",
                dvi_level=1,
                parent_id=None,
            ),
            SimpleNamespace(
                id=UUID("dddddddd-dddd-dddd-dddd-dddddddddddd"),
                ma_dviqly="PCDN",
                ma_dviqly_cha="CPC",
                ten_dviqly="PC Da Nang",
                dvi_level=2,
                parent_id=TEST_ORGANIZATION_ID,
            ),
        ]

    async def list_roles(self):
        return [
            SimpleNamespace(
                id=UUID("11111111-aaaa-aaaa-aaaa-111111111111"),
                name="COMPANY_ADMIN",
                description="Company administrator",
            ),
            SimpleNamespace(
                id=UUID("22222222-aaaa-aaaa-aaaa-222222222222"),
                name="UNIT_USER",
                description=None,
            ),
        ]

class FakeAccessCatalogDocumentRepository:
    async def list_documents_for_permission_check(self, *, knowledge_base_ids=None):
        return [
            SimpleNamespace(
                id=UUID("33333333-aaaa-aaaa-aaaa-333333333333"),
                organization_id=TEST_ORGANIZATION_ID,
                uploaded_by_user_id=None,
                visibility="global",
                document_metadata={
                    "access": {
                        "allowed_group_codes": ["ai-team", "legal-team"],
                        "denied_group_codes": "external|contractor",
                    },
                },
            )
        ]


def test_login_returns_jwt_access_token() -> None:
    app.dependency_overrides[auth.get_auth_repository] = lambda: FakeAuthRepository()

    try:
        client = TestClient(app)
        response = client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "secret"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload["token_type"] == "bearer"
    assert payload["access_token"]


def test_login_rejects_wrong_password() -> None:
    app.dependency_overrides[auth.get_auth_repository] = lambda: FakeAuthRepository()

    try:
        client = TestClient(app)
        response = client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "wrong"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 401


def test_me_does_not_expose_password_hash() -> None:
    client = TestClient(app)
    response = client.get("/api/auth/me")

    assert response.status_code == 200
    payload = response.json()
    assert payload["username"] == "test-admin"
    assert "hashed_password" not in payload

def test_access_catalog_lists_organizations_roles_and_known_groups() -> None:
    app.dependency_overrides[auth.get_auth_repository] = lambda: FakeAuthRepository()
    app.dependency_overrides[auth.get_document_repository] = (
        lambda: FakeAccessCatalogDocumentRepository()
    )

    try:
        client = TestClient(app)
        response = client.get("/api/auth/access-catalog")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert [organization["ma_dviqly"] for organization in payload["organizations"]] == [
        "CPC",
        "PCDN",
    ]
    assert [role["name"] for role in payload["roles"]] == [
        "COMPANY_ADMIN",
        "UNIT_USER",
    ]
    assert payload["groups"] == ["ai-team", "contractor", "external", "legal-team"]
