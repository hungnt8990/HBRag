from types import SimpleNamespace

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
