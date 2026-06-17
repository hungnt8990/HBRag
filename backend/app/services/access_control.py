from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel

from app.core.config import settings
from app.models.chunk import Chunk
from app.models.document import Document
from app.models.user import User


class AccessAction(StrEnum):
    SEARCH = "search"
    READ_ANSWER = "read_answer"
    VIEW_CITATION = "view_citation"
    OPEN_DOCUMENT = "open_document"
    DOWNLOAD = "download"
    INGEST = "ingest"
    MANAGE_ACL = "manage_acl"
    DELETE = "delete"


class AccessDecision(BaseModel):
    allowed: bool
    reason: str
    matched_policy: str | None = None


CLASSIFICATION_RANK = settings.access_classification_rank
SENSITIVE_CLASSIFICATIONS = set(settings.access_sensitive_classifications)
DEFAULT_CLASSIFICATION = settings.access_default_classification
DEFAULT_SCOPE = settings.access_default_scope


@dataclass(frozen=True)
class SubjectContext:
    user_id: str
    organization_id: str | None
    org_path: str | None = None
    org_level: int | None = None
    unit_type: str | None = None
    department_code: str | None = None
    department_type: str | None = None
    position_level: str | None = None
    business_domains: set[str] = field(default_factory=set)
    project_codes: set[str] = field(default_factory=set)
    clearance_level: str = DEFAULT_CLASSIFICATION
    groups: set[str] = field(default_factory=set)
    roles: set[str] = field(default_factory=set)
    employment_status: str = "active"
    descendant_org_ids: set[str] = field(default_factory=set)
    is_active: bool = True


@dataclass(frozen=True)
class ResourceContext:
    document_id: str | None = None
    chunk_id: str | None = None
    owner_org_id: str | None = None
    owner_org_path: str | None = None
    scope: str = DEFAULT_SCOPE
    classification: str = DEFAULT_CLASSIFICATION
    business_domains: set[str] = field(default_factory=set)
    project_codes: set[str] = field(default_factory=set)
    allowed_org_ids: set[str] = field(default_factory=set)
    allowed_org_paths: set[str] = field(default_factory=set)
    allowed_role_names: set[str] = field(default_factory=set)
    allowed_group_codes: set[str] = field(default_factory=set)
    allowed_user_ids: set[str] = field(default_factory=set)
    denied_org_ids: set[str] = field(default_factory=set)
    denied_org_paths: set[str] = field(default_factory=set)
    denied_role_names: set[str] = field(default_factory=set)
    denied_group_codes: set[str] = field(default_factory=set)
    denied_user_ids: set[str] = field(default_factory=set)
    inherit_permission: bool = True
    visibility: str | None = None
    uploaded_by_user_id: str | None = None


@dataclass(frozen=True)
class AccessFilter:
    subject_user_id: str
    organization_id: str | None
    descendant_org_ids: set[str] = field(default_factory=set)
    org_path: str | None = None
    role_names: set[str] = field(default_factory=set)
    group_codes: set[str] = field(default_factory=set)
    business_domains: set[str] = field(default_factory=set)
    project_codes: set[str] = field(default_factory=set)
    clearance_rank: int = 1


def role_names(user: User) -> set[str]:
    return {str(role.name) for role in getattr(user, "roles", []) or []}


def build_subject_context(
    user: User,
    *,
    descendant_organization_ids: set[UUID] | set[str] | None = None,
) -> SubjectContext:
    profile = _metadata(getattr(user, "profile", None))
    if not profile:
        profile = _metadata(getattr(user, "user_metadata", None))
    organization = getattr(user, "organization", None)
    org_path = _first_str(
        profile.get("org_path"),
        getattr(organization, "org_path", None),
        getattr(organization, "path", None),
        getattr(organization, "ma_dviqly", None),
        str(getattr(user, "organization_id", "") or "") or None,
    )
    roles = _string_set(profile.get("roles")) | role_names(user)
    is_active = bool(getattr(user, "is_active", False))
    return SubjectContext(
        user_id=str(user.id),
        organization_id=str(user.organization_id) if getattr(user, "organization_id", None) else None,
        org_path=org_path,
        org_level=_optional_int(profile.get("org_level") or getattr(organization, "dvi_level", None)),
        unit_type=_optional_str(profile.get("unit_type")),
        department_code=_optional_str(profile.get("department_code")),
        department_type=_optional_str(profile.get("department_type")),
        position_level=_optional_str(profile.get("position_level")),
        business_domains=_string_set(profile.get("business_domains")),
        project_codes=_string_set(profile.get("project_codes")),
        clearance_level=_normalize_classification(profile.get("clearance_level")),
        groups=_string_set(profile.get("groups")),
        roles=roles,
        employment_status=_optional_str(profile.get("employment_status")) or ("active" if is_active else "inactive"),
        descendant_org_ids={str(value) for value in descendant_organization_ids or set()},
        is_active=is_active,
    )


def build_resource_context(
    document: Document | None,
    chunk: Chunk | None = None,
) -> ResourceContext:
    document_access = normalize_document_access_metadata(document)
    chunk_metadata = _metadata(getattr(chunk, "chunk_metadata", None)) if chunk else {}
    chunk_access = _metadata(chunk_metadata.get("access"))
    merged = {**document_access, **{k: v for k, v in chunk_access.items() if v not in (None, "", [])}}
    if chunk_access.get("inherit_permission") is False:
        merged["inherit_permission"] = False
    return ResourceContext(
        document_id=str(getattr(document, "id", "") or "") or None,
        chunk_id=str(getattr(chunk, "id", "") or "") or None,
        owner_org_id=_first_str(merged.get("owner_org_id"), getattr(document, "organization_id", None)),
        owner_org_path=_optional_str(merged.get("owner_org_path")),
        scope=_normalize_scope(merged.get("scope")),
        classification=_normalize_classification(merged.get("classification")),
        business_domains=_string_set(merged.get("business_domains")),
        project_codes=_string_set(merged.get("project_codes")),
        allowed_org_ids=_string_set(merged.get("allowed_org_ids")),
        allowed_org_paths=_string_set(merged.get("allowed_org_paths")),
        allowed_role_names=_string_set(merged.get("allowed_role_names")),
        allowed_group_codes=_string_set(merged.get("allowed_group_codes")),
        allowed_user_ids=_string_set(merged.get("allowed_user_ids")),
        denied_org_ids=_string_set(merged.get("denied_org_ids")),
        denied_org_paths=_string_set(merged.get("denied_org_paths")),
        denied_role_names=_string_set(merged.get("denied_role_names")),
        denied_group_codes=_string_set(merged.get("denied_group_codes")),
        denied_user_ids=_string_set(merged.get("denied_user_ids")),
        inherit_permission=bool(merged.get("inherit_permission", True)),
        visibility=_optional_str(getattr(document, "visibility", None)),
        uploaded_by_user_id=_first_str(merged.get("uploaded_by_user_id"), getattr(document, "uploaded_by_user_id", None)),
    )


def build_access_filter(subject: SubjectContext) -> AccessFilter:
    return AccessFilter(
        subject_user_id=subject.user_id,
        organization_id=subject.organization_id,
        descendant_org_ids=set(subject.descendant_org_ids),
        org_path=subject.org_path,
        role_names=set(subject.roles),
        group_codes=set(subject.groups),
        business_domains=set(subject.business_domains),
        project_codes=set(subject.project_codes),
        clearance_rank=CLASSIFICATION_RANK.get(subject.clearance_level, 1),
    )


def normalize_document_access_metadata(document: Document | None) -> dict[str, Any]:
    if document is None:
        return {}
    metadata = _metadata(getattr(document, "document_metadata", None))
    access = _metadata(metadata.get("access"))
    visibility = str(getattr(document, "visibility", "organization") or "organization")
    defaults = _defaults_for_visibility(visibility)
    owner_org_id = str(getattr(document, "organization_id", "") or "") or None
    uploaded_by_user_id = str(getattr(document, "uploaded_by_user_id", "") or "") or None
    normalized = {
        **defaults,
        **access,
        "owner_org_id": access.get("owner_org_id") or owner_org_id,
        "uploaded_by_user_id": access.get("uploaded_by_user_id") or uploaded_by_user_id,
    }
    if uploaded_by_user_id and visibility == "private":
        normalized["allowed_user_ids"] = _merge_unique(
            normalized.get("allowed_user_ids"),
            [uploaded_by_user_id],
        )
    return normalize_access_payload(normalized)


def normalize_access_payload(payload: dict[str, Any] | None) -> dict[str, Any]:
    raw = dict(payload or {})
    normalized = {
        "scope": _normalize_scope(raw.get("scope")),
        "classification": _normalize_classification(raw.get("classification")),
        "owner_org_id": _optional_str(raw.get("owner_org_id")),
        "owner_org_path": _optional_str(raw.get("owner_org_path")),
        "business_domains": sorted(_string_set(raw.get("business_domains"))),
        "project_codes": sorted(_string_set(raw.get("project_codes"))),
        "allowed_org_ids": sorted(_string_set(raw.get("allowed_org_ids"))),
        "allowed_org_paths": sorted(_string_set(raw.get("allowed_org_paths"))),
        "allowed_role_names": sorted(_string_set(raw.get("allowed_role_names"))),
        "allowed_group_codes": sorted(_string_set(raw.get("allowed_group_codes"))),
        "allowed_user_ids": sorted(_string_set(raw.get("allowed_user_ids"))),
        "denied_org_ids": sorted(_string_set(raw.get("denied_org_ids"))),
        "denied_org_paths": sorted(_string_set(raw.get("denied_org_paths"))),
        "denied_role_names": sorted(_string_set(raw.get("denied_role_names"))),
        "denied_group_codes": sorted(_string_set(raw.get("denied_group_codes"))),
        "denied_user_ids": sorted(_string_set(raw.get("denied_user_ids"))),
        "inherit_permission": bool(raw.get("inherit_permission", True)),
    }
    if raw.get("access_policy_id"):
        normalized["access_policy_id"] = str(raw["access_policy_id"])
    return normalized


def access_payload_for_chunk(
    *,
    document: Document,
    chunk_metadata: dict[str, Any] | None,
) -> dict[str, Any]:
    document_access = normalize_document_access_metadata(document)
    chunk_access = _metadata((chunk_metadata or {}).get("access"))
    if not chunk_access:
        return document_access
    merged = {**document_access, **{key: value for key, value in chunk_access.items() if value not in (None, "")}}
    return normalize_access_payload(merged)


def flatten_access_payload(payload: dict[str, Any]) -> dict[str, Any]:
    access = normalize_access_payload(payload)
    return {key: value for key, value in access.items() if key != "access_policy_id"}


def can_access_resource(
    subject: SubjectContext,
    resource: ResourceContext,
    action: AccessAction,
) -> AccessDecision:
    if not subject.is_active or subject.employment_status != "active":
        return AccessDecision(allowed=False, reason="inactive user", matched_policy="subject")
    if settings.access_read_all_documents and action in {
        AccessAction.SEARCH,
        AccessAction.READ_ANSWER,
        AccessAction.VIEW_CITATION,
        AccessAction.OPEN_DOCUMENT,
        AccessAction.DOWNLOAD,
    }:
        return AccessDecision(allowed=True, reason="read access is open to all users", matched_policy="config")
    if _matches_any(subject.user_id, resource.denied_user_ids):
        return AccessDecision(allowed=False, reason="user explicitly denied", matched_policy="deny_user")
    if subject.roles & resource.denied_role_names:
        return AccessDecision(allowed=False, reason="role explicitly denied", matched_policy="deny_role")
    if subject.groups & resource.denied_group_codes:
        return AccessDecision(allowed=False, reason="group explicitly denied", matched_policy="deny_group")
    if _org_id_matches_any(subject, resource.denied_org_ids):
        return AccessDecision(allowed=False, reason="organization explicitly denied", matched_policy="deny_org")
    if _path_matches_any(subject.org_path, resource.denied_org_paths):
        return AccessDecision(allowed=False, reason="organization explicitly denied", matched_policy="deny_org")

    if settings.permission_super_admin_role in subject.roles:
        return AccessDecision(allowed=True, reason="super admin", matched_policy="role")

    clearance_rank = CLASSIFICATION_RANK.get(subject.clearance_level, 1)
    required_rank = CLASSIFICATION_RANK.get(resource.classification, 1)
    if clearance_rank < required_rank:
        return AccessDecision(allowed=False, reason="classification exceeds clearance", matched_policy="classification")

    explicit = _explicit_allow(subject, resource)
    if explicit is not None:
        return explicit

    if resource.classification in SENSITIVE_CLASSIFICATIONS and not resource.inherit_permission:
        return AccessDecision(allowed=False, reason="sensitive resource requires explicit ACL", matched_policy="sensitive")

    if resource.uploaded_by_user_id and resource.uploaded_by_user_id == subject.user_id:
        return AccessDecision(allowed=True, reason="uploaded by user", matched_policy="owner")

    if action in {AccessAction.MANAGE_ACL, AccessAction.DELETE, AccessAction.INGEST}:
        if subject.roles & set(settings.access_manage_roles) and _same_org_tree(subject, resource):
            return AccessDecision(allowed=True, reason="admin in organization tree", matched_policy="role_org")
        return AccessDecision(allowed=False, reason="manage action requires owner/admin", matched_policy="action")

    scope = resource.scope
    if scope in set(settings.access_corp_wide_scopes):
        return AccessDecision(allowed=True, reason="corp-wide internal scope", matched_policy=scope)
    if scope in set(settings.access_org_tree_scopes):
        return _allow_if(_same_org_tree(subject, resource), "organization scope", scope)
    if scope == "department_only":
        return _allow_if(_same_org_path(subject.org_path, resource.owner_org_path), "department scope", scope)
    if scope == "functional_vertical":
        return _allow_if(
            bool(subject.business_domains & resource.business_domains),
            "business domain scope",
            scope,
        )
    if scope == "project_only":
        return _allow_if(bool(subject.project_codes & resource.project_codes), "project scope", scope)
    if scope == "leadership_only":
        return _allow_if(
            bool(subject.roles & set(settings.access_leadership_roles))
            or subject.position_level in set(settings.access_leadership_positions),
            "leadership scope",
            scope,
        )
    if scope == settings.access_explicit_acl_scope:
        return AccessDecision(allowed=False, reason="explicit ACL required", matched_policy=scope)
    return AccessDecision(allowed=False, reason="no matching access policy", matched_policy=scope)


def _explicit_allow(subject: SubjectContext, resource: ResourceContext) -> AccessDecision | None:
    if _matches_any(subject.user_id, resource.allowed_user_ids):
        return AccessDecision(allowed=True, reason="user explicitly allowed", matched_policy="allow_user")
    if subject.roles & resource.allowed_role_names:
        return AccessDecision(allowed=True, reason="role explicitly allowed", matched_policy="allow_role")
    if subject.groups & resource.allowed_group_codes:
        return AccessDecision(allowed=True, reason="group explicitly allowed", matched_policy="allow_group")
    if _org_id_matches_any(subject, resource.allowed_org_ids):
        return AccessDecision(allowed=True, reason="organization explicitly allowed", matched_policy="allow_org")
    if _path_matches_any(subject.org_path, resource.allowed_org_paths):
        return AccessDecision(allowed=True, reason="organization explicitly allowed", matched_policy="allow_org")
    return None


def _defaults_for_visibility(visibility: str) -> dict[str, Any]:
    defaults = settings.access_visibility_defaults
    return dict(defaults.get(visibility) or defaults.get("organization") or {})


def _same_org_tree(subject: SubjectContext, resource: ResourceContext) -> bool:
    if resource.owner_org_id and resource.owner_org_id == subject.organization_id:
        return True
    if resource.owner_org_id and resource.owner_org_id in subject.descendant_org_ids:
        return True
    if _same_org_path(subject.org_path, resource.owner_org_path):
        return True
    return _path_is_descendant(subject.org_path, resource.owner_org_path)


def _same_org_path(left: str | None, right: str | None) -> bool:
    return bool(left and right and left == right)


def _path_is_descendant(subject_path: str | None, owner_path: str | None) -> bool:
    return bool(subject_path and owner_path and subject_path.startswith(f"{owner_path}/"))


def _path_matches_any(subject_path: str | None, patterns: set[str]) -> bool:
    if not subject_path:
        return False
    return any(subject_path == pattern or subject_path.startswith(f"{pattern}/") for pattern in patterns)


def _matches_any(value: str | None, values: set[str]) -> bool:
    return bool(value and value in values)

def _org_id_matches_any(subject: SubjectContext, values: set[str]) -> bool:
    if not values:
        return False
    subject_org_ids = set(subject.descendant_org_ids)
    if subject.organization_id:
        subject_org_ids.add(subject.organization_id)
    return bool(subject_org_ids & values)


def _allow_if(condition: bool, reason: str, policy: str) -> AccessDecision:
    return AccessDecision(allowed=condition, reason=reason if condition else f"not allowed by {reason}", matched_policy=policy)


def _metadata(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _string_set(value: Any) -> set[str]:
    if value is None or value == "":
        return set()
    if isinstance(value, str):
        parts = value.replace(";", ",").replace("|", ",").split(",")
        return {part.strip() for part in parts if part.strip()}
    if isinstance(value, list | tuple | set):
        return {str(item).strip() for item in value if str(item).strip()}
    return {str(value).strip()} if str(value).strip() else set()


def _merge_unique(value: Any, extra: list[str]) -> list[str]:
    merged = list(_string_set(value))
    for item in extra:
        if item not in merged:
            merged.append(item)
    return merged


def _normalize_scope(value: Any) -> str:
    scope = str(value or DEFAULT_SCOPE).strip() or DEFAULT_SCOPE
    return settings.access_scope_aliases.get(scope, scope)


def _normalize_classification(value: Any) -> str:
    classification = str(value or DEFAULT_CLASSIFICATION).strip() or DEFAULT_CLASSIFICATION
    return classification if classification in CLASSIFICATION_RANK else DEFAULT_CLASSIFICATION


def _optional_str(value: Any) -> str | None:
    if value is None or value == "":
        return None
    return str(value)


def _first_str(*values: Any) -> str | None:
    for value in values:
        candidate = _optional_str(value)
        if candidate:
            return candidate
    return None


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
