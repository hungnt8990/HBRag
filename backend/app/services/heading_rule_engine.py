from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class HeadingRule:
    name: str
    pattern: str
    level: int = 1
    enabled: bool = True
    boundary: bool = False
    metadata_key: str | None = None
    number_metadata_key: str | None = None
    metadata_value: str = "title"


@dataclass(frozen=True)
class DetectedHeading:
    start: int
    end: int
    level: int
    name: str
    label: str
    number: str
    title: str
    display_text: str
    boundary: bool
    metadata_key: str | None = None
    number_metadata_key: str | None = None
    metadata_value: str = "title"


def heading_rule_from_dict(raw: dict[str, Any]) -> HeadingRule | None:
    pattern = str(raw.get("pattern") or "").strip()
    name = str(raw.get("name") or raw.get("label") or "heading").strip() or "heading"
    if not pattern:
        return None
    try:
        level = int(raw.get("level") or 1)
    except (TypeError, ValueError):
        level = 1
    return HeadingRule(
        name=name,
        pattern=pattern,
        level=max(level, 1),
        enabled=bool(raw.get("enabled", True)),
        boundary=bool(raw.get("boundary", False)),
        metadata_key=str(raw.get("metadata_key") or "").strip() or None,
        number_metadata_key=str(raw.get("number_metadata_key") or "").strip() or None,
        metadata_value=str(raw.get("metadata_value") or "title").strip() or "title",
    )


def heading_rules_from_config(config: dict[str, Any] | None) -> list[HeadingRule]:
    if not isinstance(config, dict):
        return []
    raw_rules = config.get("heading_rules") or []
    if not isinstance(raw_rules, list):
        return []
    rules: list[HeadingRule] = []
    for raw in raw_rules:
        if not isinstance(raw, dict):
            continue
        rule = heading_rule_from_dict(raw)
        if rule is not None:
            rules.append(rule)
    return rules


def _group_value(match: re.Match[str], name: str, fallback_index: int | None = None) -> str:
    try:
        value = match.group(name)
    except IndexError:
        value = None
    if value is None and fallback_index is not None:
        try:
            value = match.group(fallback_index)
        except IndexError:
            value = None
    return str(value or "").strip()


def detect_headings(text: str, rules: list[HeadingRule]) -> list[DetectedHeading]:
    headings: list[DetectedHeading] = []
    for rule in rules:
        if not rule.enabled:
            continue
        try:
            pattern = re.compile(rule.pattern, flags=re.MULTILINE | re.IGNORECASE)
        except re.error:
            continue
        for match in pattern.finditer(text):
            label = _group_value(match, "label", 1)
            number = _group_value(match, "number", 2)
            title = _group_value(match, "title", 3)
            display_text = " ".join(part for part in (label, number, title) if part).strip()
            if not display_text:
                display_text = match.group(0).strip()
            headings.append(
                DetectedHeading(
                    start=match.start(),
                    end=match.end(),
                    level=rule.level,
                    name=rule.name,
                    label=label,
                    number=number,
                    title=title,
                    display_text=display_text,
                    boundary=rule.boundary,
                    metadata_key=rule.metadata_key,
                    number_metadata_key=rule.number_metadata_key,
                    metadata_value=rule.metadata_value,
                )
            )
    headings.sort(key=lambda item: (item.start, item.level, item.name))
    return headings


def score_heading_rules(text: str, rules: list[HeadingRule]) -> int:
    if not text or not rules:
        return 0
    return len(detect_headings(text, rules))
