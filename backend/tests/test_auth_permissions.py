from datetime import timedelta
from types import SimpleNamespace
from uuid import UUID

import pytest

from app.core.config import settings
from app.core.security import (
    TokenError,
    create_access_token,
    decode_jwt,
    hash_password,
    verify_password,
)
from app.services.security.security_permissions import (
    can_manage_document,
    can_upload_document,
    can_view_document,
)

USER_ID = UUID("10000000-0000-0000-0000-000000000001")
ORG_ID = UUID("20000000-0000-0000-0000-000000000001")
CHILD_ORG_ID = UUID("20000000-0000-0000-0000-000000000002")
OTHER_ORG_ID = UUID("20000000-0000-0000-0000-000000000003")


def test_password_hash_verification() -> None:
    hashed = hash_password("secret-password")

    assert hashed != "secret-password"
    assert verify_password("secret-password", hashed) is True
    assert verify_password("wrong-password", hashed) is False


def test_jwt_round_trip_and_expiry() -> None:
    token = create_access_token(subject=str(USER_ID), expires_delta=timedelta(minutes=5))

    assert decode_jwt(token)["sub"] == str(USER_ID)

    expired = create_access_token(subject=str(USER_ID), expires_delta=timedelta(minutes=-1))
    with pytest.raises(TokenError):
        decode_jwt(expired)


def test_role_upload_permissions() -> None:
    assert can_upload_document(_user("UNIT_USER")) is True
    assert can_upload_document(_user("VIEWER")) is False


def test_document_visibility_rules(monkeypatch) -> None:
    monkeypatch.setattr(settings, "access_read_all_documents", False)

    unit_user = _user("UNIT_USER", organization_id=ORG_ID)
    company_admin = _user("COMPANY_ADMIN", organization_id=ORG_ID)
    visible_orgs = {ORG_ID, CHILD_ORG_ID}

    own_org_document = _document(organization_id=ORG_ID, visibility="organization")
    child_subtree_document = _document(organization_id=CHILD_ORG_ID, visibility="subtree")
    private_other_document = _document(organization_id=OTHER_ORG_ID, visibility="private")
    global_document = _document(organization_id=OTHER_ORG_ID, visibility="global")

    assert can_view_document(
        unit_user,
        own_org_document,
        descendant_organization_ids={ORG_ID},
    )
    assert can_view_document(
        company_admin,
        child_subtree_document,
        descendant_organization_ids=visible_orgs,
    )
    assert can_view_document(
        unit_user,
        private_other_document,
        descendant_organization_ids={ORG_ID},
    ) is False
    assert can_view_document(
        unit_user,
        global_document,
        descendant_organization_ids={ORG_ID},
    )


def test_manage_document_rules() -> None:
    user = _user("UNIT_USER", organization_id=ORG_ID)
    viewer = _user("VIEWER", organization_id=ORG_ID)
    document = _document(organization_id=ORG_ID, visibility="organization")

    assert can_manage_document(user, document, descendant_organization_ids={ORG_ID})
    assert can_manage_document(viewer, document, descendant_organization_ids={ORG_ID}) is False


def _user(role_name: str, organization_id: UUID = ORG_ID):
    return SimpleNamespace(
        id=USER_ID,
        organization_id=organization_id,
        roles=[SimpleNamespace(name=role_name)],
        is_active=True,
    )


def _document(
    *,
    organization_id: UUID,
    visibility: str,
    uploaded_by_user_id: UUID | None = None,
):
    return SimpleNamespace(
        organization_id=organization_id,
        visibility=visibility,
        uploaded_by_user_id=uploaded_by_user_id,
    )
