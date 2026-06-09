from __future__ import annotations

from uuid import UUID

from app.models.document import Document
from app.models.knowledge_base import KnowledgeBase
from app.models.user import User

SUPER_ADMIN = "SUPER_ADMIN"
CORP_ADMIN = "CORP_ADMIN"
COMPANY_ADMIN = "COMPANY_ADMIN"
UNIT_USER = "UNIT_USER"
VIEWER = "VIEWER"
ADMIN_ROLES = {SUPER_ADMIN, CORP_ADMIN, COMPANY_ADMIN}
KB_VIEW_PERMISSIONS = {"owner", "admin", "editor", "viewer"}
KB_MANAGE_PERMISSIONS = {"owner", "admin"}
KB_UPLOAD_PERMISSIONS = {"owner", "admin", "editor"}


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

def can_view_knowledge_base(
    user: User,
    knowledge_base: KnowledgeBase,
    *,
    descendant_organization_ids: set[UUID],
) -> bool:
    if not user.is_active:
        return False
    if not getattr(knowledge_base, "is_active", True):
        return False

    roles = role_names(user)
    if SUPER_ADMIN in roles:
        return True
    if _is_knowledge_base_owner(user, knowledge_base):
        return True
    if _has_knowledge_base_permission(user, knowledge_base, KB_VIEW_PERMISSIONS):
        return True

    knowledge_base_organization_id = getattr(knowledge_base, "organization_id", None)
    if roles & ADMIN_ROLES and knowledge_base_organization_id in descendant_organization_ids:
        return True

    visibility = getattr(knowledge_base, "visibility", "private")
    if visibility == "global":
        return True
    if knowledge_base_organization_id is None:
        return False
    if visibility == "organization":
        return knowledge_base_organization_id == user.organization_id
    if visibility == "subtree":
        return knowledge_base_organization_id in descendant_organization_ids
    return False

def can_manage_knowledge_base(
    user: User,
    knowledge_base: KnowledgeBase,
    *,
    descendant_organization_ids: set[UUID],
) -> bool:
    roles = role_names(user)
    if SUPER_ADMIN in roles:
        return True
    if _is_knowledge_base_owner(user, knowledge_base):
        return True
    if _has_knowledge_base_permission(user, knowledge_base, KB_MANAGE_PERMISSIONS):
        return True

    knowledge_base_organization_id = getattr(knowledge_base, "organization_id", None)
    return bool(
        roles & ADMIN_ROLES
        and knowledge_base_organization_id in descendant_organization_ids
    )

def can_upload_to_knowledge_base(
    user: User,
    knowledge_base: KnowledgeBase,
    *,
    descendant_organization_ids: set[UUID],
) -> bool:
    if not can_upload_document(user):
        return False
    if can_manage_knowledge_base(
        user,
        knowledge_base,
        descendant_organization_ids=descendant_organization_ids,
    ):
        return True
    return _has_knowledge_base_permission(user, knowledge_base, KB_UPLOAD_PERMISSIONS)

def _is_knowledge_base_owner(user: User, knowledge_base: KnowledgeBase) -> bool:
    return getattr(knowledge_base, "owner_user_id", None) == user.id

def _has_knowledge_base_permission(
    user: User,
    knowledge_base: KnowledgeBase,
    allowed_permissions: set[str],
) -> bool:
    role_ids = {role.id for role in getattr(user, "roles", []) if getattr(role, "id", None)}
    for member in getattr(knowledge_base, "members", []) or []:
        permission = getattr(member, "permission", None)
        if permission not in allowed_permissions:
            continue
        if getattr(member, "user_id", None) == user.id:
            return True
        if getattr(member, "organization_id", None) == user.organization_id:
            return True
        if getattr(member, "role_id", None) in role_ids:
            return True
    return False
