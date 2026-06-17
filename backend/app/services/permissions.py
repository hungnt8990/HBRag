from __future__ import annotations

from uuid import UUID

from app.core.config import settings
from app.models.document import Document
from app.models.knowledge_base import KnowledgeBase
from app.models.user import User
from app.services.access_control import (
    AccessAction,
    build_resource_context,
    build_subject_context,
    can_access_resource,
)

SUPER_ADMIN = settings.permission_super_admin_role
CORP_ADMIN = settings.permission_corp_admin_role
COMPANY_ADMIN = settings.permission_company_admin_role
UNIT_USER = settings.permission_unit_user_role
VIEWER = settings.permission_viewer_role
ADMIN_ROLES = set(settings.permission_admin_roles)
KB_VIEW_PERMISSIONS = set(settings.knowledge_base_view_permissions)
KB_MANAGE_PERMISSIONS = set(settings.knowledge_base_manage_permissions)
KB_UPLOAD_PERMISSIONS = set(settings.knowledge_base_upload_permissions)


def role_names(user: User) -> set[str]:
    return {role.name for role in user.roles}


def can_upload_document(user: User) -> bool:
    roles = role_names(user)
    return bool(roles & set(settings.permission_upload_roles))


def can_manage_document(
    user: User,
    document: Document,
    *,
    descendant_organization_ids: set[UUID],
) -> bool:
    decision = can_access_resource(
        build_subject_context(user, descendant_organization_ids=descendant_organization_ids),
        build_resource_context(document),
        AccessAction.MANAGE_ACL,
    )
    if decision.allowed:
        return True
    if _has_explicit_access_metadata(document):
        return False

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
    subject = build_subject_context(
        user,
        descendant_organization_ids=descendant_organization_ids,
    )
    decision = can_access_resource(
        subject,
        build_resource_context(document),
        AccessAction.OPEN_DOCUMENT,
    )
    if decision.allowed:
        return True
    if _has_explicit_access_metadata(document):
        return False
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
    if roles & set(settings.permission_cross_org_upload_roles):
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


def _has_explicit_access_metadata(document: Document) -> bool:
    metadata = getattr(document, "document_metadata", None)
    if not isinstance(metadata, dict):
        return False
    return isinstance(metadata.get("access"), dict)
