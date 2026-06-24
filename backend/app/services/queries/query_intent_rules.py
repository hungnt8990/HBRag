from __future__ import annotations

import copy
import re
import unicodedata
from collections.abc import Mapping
from typing import Any

DEFAULT_QUERY_INTENT_RULES: dict[str, dict[str, Any]] = {
    "field_detail_schema": {
        "direct_terms": ["field", "column", "truong", "cot"],
        "required_any_terms": ["thuoc tinh", "attribute"],
        "specific_item_patterns": [r"\bf\d{2}[_a-z0-9]*\b"],
        "phrases": [
            "thuoc tinh cua",
            "cac thuoc tinh cua",
            "attribute of",
            "attributes of",
        ],
    }
}


def normalize_query_intent_rules(
    rules: Mapping[str, Any] | None,
) -> dict[str, dict[str, Any]]:
    normalized = copy.deepcopy(DEFAULT_QUERY_INTENT_RULES)
    if not isinstance(rules, Mapping):
        return normalized

    for intent_name, intent_rules in rules.items():
        if not isinstance(intent_name, str):
            continue
        if not isinstance(intent_rules, Mapping):
            continue
        base_rules = normalized.get(intent_name)
        normalized[intent_name] = {
            **(copy.deepcopy(base_rules) if isinstance(base_rules, Mapping) else {}),
            **copy.deepcopy(dict(intent_rules)),
        }
    return normalized


def is_field_detail_schema_query(
    query: str,
    rules: Mapping[str, Any] | None = None,
) -> bool:
    normalized_query = normalize_query_text(query)
    if not normalized_query:
        return False

    field_rules = normalize_query_intent_rules(rules).get("field_detail_schema", {})
    if _contains_any(normalized_query, field_rules.get("direct_terms")):
        return True

    required_any_terms = _normalized_rule_values(field_rules.get("required_any_terms"))
    if required_any_terms and not any(term in normalized_query for term in required_any_terms):
        return False

    for pattern in _rule_values(field_rules.get("specific_item_patterns")):
        try:
            if re.search(pattern, normalized_query):
                return True
        except re.error:
            continue

    return _contains_any(normalized_query, field_rules.get("phrases"))


def normalize_query_text(value: str) -> str:
    normalized = unicodedata.normalize("NFD", value or "")
    normalized = "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")
    return re.sub(r"\s+", " ", normalized.casefold()).strip()


def _contains_any(normalized_query: str, values: object) -> bool:
    return any(value in normalized_query for value in _normalized_rule_values(values))


def _normalized_rule_values(values: object) -> list[str]:
    return [normalize_query_text(value) for value in _rule_values(values)]


def _rule_values(values: object) -> list[str]:
    if values is None:
        return []
    if isinstance(values, str):
        return [values] if values.strip() else []
    if isinstance(values, list | tuple | set):
        return [str(value) for value in values if str(value).strip()]
    return []
