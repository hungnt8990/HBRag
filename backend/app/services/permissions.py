from __future__ import annotations

from uuid import UUID

from app.models.document import Document
from app.models.user import User

SUPER_ADMIN = "SUPER_ADMIN"
CORP_ADMIN = "CORP_ADMIN"
COMPANY_ADMIN = "COMPANY_ADMIN"
UNIT_USER = "UNIT_USER"
VIEWER = "VIEWER"
ADMIN_ROLES = {SUPER_ADMIN, CORP_ADMIN, COMPANY_ADMIN}


def role_names(user: User) -> set[str]:
    return {role.name for role in user.roles}


def can_upload_document(user: User) -> bool:
    roles = role_names(user)
    return bool(roles & {SUPER_ADMIN, CORP_ADMIN, COMPANY_ADMIN, UNIT_USER})


def can_manage_document(
    user: User,
    document: Document,
    *,
    descendant_organization_ids: set[UUID],
) -> bool:
    roles = role_names(user)
    if SUPER_ADMIN in roles:
        return True
    if getattr(document, "uploaded_by_user_id", None) == user.id:
        return True
    document_organization_id = getattr(document, "organization_id", None)
    if document_organization_id is None:
        return bool(role_names(user) & ADMIN_ROLES)
    if CORP_ADMIN in roles:
        return document_organization_id in descendant_organization_ids
    if COMPANY_ADMIN in roles:
        return document_organization_id in descendant_organization_ids
    return UNIT_USER in roles and document_organization_id == user.organization_id


def can_view_document(
    user: User,
    document: Document,
    *,
    descendant_organization_ids: set[UUID],
) -> bool:
    if not user.is_active:
        return False

    roles = role_names(user)
    if SUPER_ADMIN in roles:
        return True
    visibility = getattr(document, "visibility", "global")
    if visibility == "global":
        return True
    if getattr(document, "uploaded_by_user_id", None) == user.id:
        return True
    document_organization_id = getattr(document, "organization_id", None)
    if document_organization_id is None:
        return bool(roles & ADMIN_ROLES)

    if roles & ADMIN_ROLES and document_organization_id in descendant_organization_ids:
        return True

    if visibility == "private":
        return False
    if visibility == "organization":
        return document_organization_id == user.organization_id
    if visibility == "subtree":
        return user.organization_id in descendant_organization_ids

    return False


def can_assign_upload_organization(
    user: User,
    organization_id: UUID,
    *,
    descendant_organization_ids: set[UUID],
) -> bool:
    roles = role_names(user)
    if SUPER_ADMIN in roles:
        return True
    if CORP_ADMIN in roles or COMPANY_ADMIN in roles:
        return organization_id in descendant_organization_ids
    return organization_id == user.organization_id
