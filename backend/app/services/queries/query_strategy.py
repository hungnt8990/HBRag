from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


@dataclass(frozen=True)
class QueryStrategy:
    strategies: tuple[str, ...]
    search_terms: tuple[str, ...]
    requires_overview_context: bool = False
    requires_diversity: bool = False
    may_need_second_retrieval: bool = False


def classify_query_strategy(query: str) -> QueryStrategy:
    normalized = _normalize(query)
    strategies: list[str] = []

    if _has_any(normalized, EXACT_LOOKUP_TERMS) or DOC_CODE_RE.search(query or ""):
        strategies.append("exact_lookup")
    if _has_any(normalized, OVERVIEW_TERMS):
        strategies.append("overview_summary")
    if _has_any(normalized, COUNT_LIST_TERMS):
        strategies.append("count_list")
    if _has_any(normalized, TABLE_DETAIL_TERMS):
        strategies.append("table_detail")
    if _has_any(normalized, COMPARISON_TERMS):
        strategies.append("comparison")
    if _has_any(normalized, PROCEDURE_TERMS):
        strategies.append("procedure")
    if _has_any(normalized, CALCULATION_TERMS):
        strategies.append("calculation")

    if _has_any(normalized, MULTI_HOP_TERMS) or (
        "overview_summary" in strategies and "count_list" in strategies
    ):
        strategies.append("multi_hop")
    if not strategies:
        strategies.append("semantic_search")

    search_terms = _dedupe_terms(
        [
            *GENERIC_STRATEGY_TERMS,
            *[term for name in strategies for term in STRATEGY_TERMS.get(name, ())],
        ]
    )
    requires_overview = bool({"overview_summary", "count_list", "multi_hop"} & set(strategies))
    return QueryStrategy(
        strategies=tuple(strategies),
        search_terms=tuple(search_terms),
        requires_overview_context=requires_overview,
        requires_diversity=requires_overview or "comparison" in strategies,
        may_need_second_retrieval=requires_overview or "multi_hop" in strategies,
    )


DOC_CODE_RE = re.compile(r"\b\d{1,6}\s*/\s*[A-ZĐ0-9][A-ZĐ0-9_\-/]{1,}\b", re.IGNORECASE)
EXACT_LOOKUP_TERMS = ("ma", "so hieu", "dinh nghia", "la gi", "what is", "definition")
OVERVIEW_TERMS = (
    "tong quan",
    "khung",
    "cau truc",
    "cau thanh",
    "overview",
    "structure",
    "framework",
)
COUNT_LIST_TERMS = (
    "co may",
    "bao nhieu",
    "so luong",
    "gom",
    "bao gom",
    "danh sach",
    "liet ke",
    "nhung phan nao",
    "cac loai",
    "how many",
    "list",
    "include",
)
TABLE_DETAIL_TERMS = (
    "bang",
    "dong",
    "cot",
    "truong",
    "thuoc tinh",
    "table",
    "row",
    "column",
    "field",
    "attribute",
)
COMPARISON_TERMS = ("so sanh", "khac nhau", "giong nhau", "compare", "difference")
PROCEDURE_TERMS = ("quy trinh", "cac buoc", "thu tuc", "procedure", "steps", "workflow")
CALCULATION_TERMS = ("tinh", "tong", "ti le", "phan tram", "calculate", "sum", "average")
MULTI_HOP_TERMS = (
    "moi quan he",
    "lien quan",
    "giua",
    "nhieu phan",
    "nhieu muc",
    "multi hop",
    "relationship",
)
GENERIC_STRATEGY_TERMS = ("heading", "outline", "summary", "section", "table")
STRATEGY_TERMS = {
    "overview_summary": ("document summary", "heading outline", "section summary", "tong quan", "cau truc"),
    "count_list": ("count", "number", "so luong", "danh sach", "bao gom", "gom"),
    "table_detail": (
        "table summary",
        "table header",
        "attribute table",
        "row",
        "column",
        "field",
        "bang du lieu thuoc tinh",
        "bang",
        "cot",
        "dong",
    ),
    "multi_hop": (
        "relationship",
        "related section",
        "moi quan he",
        "1-M",
        "phan lien quan",
        "nhom thong tin",
    ),
    "comparison": ("compare", "difference", "so sanh"),
    "procedure": ("procedure", "step", "quy trinh", "cac buoc"),
    "calculation": ("calculation", "total", "tong", "so lieu"),
}


def _has_any(text: str, terms: tuple[str, ...]) -> bool:
    padded = f" {text} "
    for term in terms:
        normalized_term = _normalize(term)
        if not normalized_term:
            continue
        if len(normalized_term) <= 3:
            if f" {normalized_term} " in padded:
                return True
            continue
        if normalized_term in text:
            return True
    return False


def _dedupe_terms(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        clean = " ".join(str(value or "").split()).strip()
        if not clean:
            continue
        key = clean.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(clean)
    return result


def _normalize(value: str) -> str:
    normalized = unicodedata.normalize("NFD", value or "")
    stripped = "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")
    stripped = stripped.replace("Đ", "D").replace("đ", "d")
    return re.sub(r"\s+", " ", stripped.casefold()).strip()
