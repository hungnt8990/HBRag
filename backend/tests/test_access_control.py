from types import SimpleNamespace
from uuid import UUID

import pytest

from app.core.config import settings
from app.services.security.security_access_control import (
    AccessAction,
    access_payload_for_chunk,
    build_resource_context,
    build_subject_context,
    can_access_resource,
)

ORG_ID = UUID("10000000-0000-0000-0000-000000000001")
OTHER_ORG_ID = UUID("10000000-0000-0000-0000-000000000002")
USER_ID = UUID("20000000-0000-0000-0000-000000000001")
OTHER_USER_ID = UUID("20000000-0000-0000-0000-000000000002")
DOCUMENT_ID = UUID("30000000-0000-0000-0000-000000000001")

@pytest.fixture(autouse=True)
def strict_access_policy(monkeypatch):
    monkeypatch.setattr(settings, "access_read_all_documents", False)


def _user(
    *,
    user_id=USER_ID,
    organization_id=ORG_ID,
    org_path="EVNCPC/PC_DANANG/PHONG_KINH_DOANH",
    role="UNIT_USER",
    business_domains=None,
    project_codes=None,
    clearance_level="internal",
    groups=None,
    is_active=True,
):
    return SimpleNamespace(
        id=user_id,
        organization_id=organization_id,
        organization=SimpleNamespace(ma_dviqly=org_path, dvi_level=3),
        roles=[SimpleNamespace(name=role)],
        is_active=is_active,
        profile={
            "org_path": org_path,
            "business_domains": business_domains or [],
            "project_codes": project_codes or [],
            "clearance_level": clearance_level,
            "groups": groups or [],
            "employment_status": "active" if is_active else "inactive",
        },
    )


def _document(*, organization_id=ORG_ID, visibility="organization", access=None):
    return SimpleNamespace(
        id=DOCUMENT_ID,
        organization_id=organization_id,
        uploaded_by_user_id=OTHER_USER_ID,
        visibility=visibility,
        document_metadata={"access": access} if access is not None else {},
    )


def _allowed(user, document, *, descendants=None) -> bool:
    subject = build_subject_context(
        user,
        descendant_organization_ids=descendants or {user.organization_id},
    )
    decision = can_access_resource(
        subject,
        build_resource_context(document),
        AccessAction.READ_ANSWER,
    )
    return decision.allowed


def test_corp_wide_internal_allows_active_internal_user() -> None:
    document = _document(access={"scope": "corp_wide", "classification": "internal"})

    assert _allowed(_user(), document) is True

def test_configured_open_read_access_allows_other_unit(monkeypatch) -> None:
    monkeypatch.setattr(settings, "access_read_all_documents", True)
    document = _document(
        organization_id=ORG_ID,
        access={"scope": "unit_only", "classification": "internal", "owner_org_id": str(ORG_ID)},
    )

    assert _allowed(_user(organization_id=OTHER_ORG_ID, org_path="EVNCPC/PC_QUANGNAM"), document) is True


def test_unit_only_allows_same_unit_and_denies_other_unit() -> None:
    document = _document(
        organization_id=ORG_ID,
        access={"scope": "unit_only", "classification": "internal", "owner_org_id": str(ORG_ID)},
    )

    assert _allowed(_user(organization_id=ORG_ID), document) is True
    assert _allowed(_user(organization_id=OTHER_ORG_ID, org_path="EVNCPC/PC_QUANGNAM"), document) is False


def test_functional_vertical_matches_business_domain() -> None:
    document = _document(
        access={
            "scope": "functional_vertical",
            "classification": "internal",
            "business_domains": ["kinh_doanh"],
        }
    )

    assert _allowed(_user(business_domains=["kinh_doanh"]), document) is True
    assert _allowed(_user(business_domains=["ky_thuat"]), document) is False


def test_explicit_acl_allows_selected_organization_id() -> None:
    document = _document(
        access={
            "scope": "explicit_acl",
            "classification": "internal",
            "allowed_org_ids": [str(ORG_ID)],
        }
    )

    assert _allowed(_user(organization_id=ORG_ID), document) is True
    assert _allowed(_user(organization_id=OTHER_ORG_ID, org_path="EVNCPC/PC_QUANGNAM"), document) is False

def test_project_only_matches_project_membership() -> None:
    document = _document(
        access={
            "scope": "project_only",
            "classification": "restricted",
            "project_codes": ["chuyen_doi_so_2026"],
        }
    )

    assert _allowed(
        _user(project_codes=["chuyen_doi_so_2026"], clearance_level="restricted"),
        document,
    ) is True
    assert _allowed(_user(project_codes=["du_an_khac"], clearance_level="restricted"), document) is False


def test_sensitive_acl_requires_clearance_and_explicit_allow() -> None:
    document = _document(
        access={
            "scope": "explicit_acl",
            "classification": "confidential",
            "inherit_permission": False,
            "allowed_user_ids": [str(USER_ID)],
        }
    )

    assert _allowed(_user(clearance_level="confidential"), document) is True
    assert _allowed(_user(clearance_level="internal"), document) is False


def test_deny_user_overrides_allow() -> None:
    document = _document(
        access={
            "scope": "corp_wide",
            "classification": "internal",
            "allowed_user_ids": [str(USER_ID)],
            "denied_user_ids": [str(USER_ID)],
        }
    )

    assert _allowed(_user(), document) is False


def test_chunk_access_inherits_document_access_and_allows_override() -> None:
    document = _document(
        access={
            "scope": "corp_wide",
            "classification": "internal",
            "business_domains": ["kinh_doanh"],
        }
    )

    inherited = access_payload_for_chunk(document=document, chunk_metadata={})
    overridden = access_payload_for_chunk(
        document=document,
        chunk_metadata={"access": {"classification": "personal_data", "scope": "explicit_acl"}},
    )

    assert inherited["scope"] == "corp_wide"
    assert inherited["business_domains"] == ["kinh_doanh"]
    assert overridden["classification"] == "personal_data"
    assert overridden["scope"] == "explicit_acl"
