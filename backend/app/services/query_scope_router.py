from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

QueryScope = Literal[
    "identifier_lookup",
    "document_question",
    "smalltalk",
    "out_of_scope",
]


@dataclass(frozen=True)
class QueryScopeResult:
    scope: QueryScope
    reason: str


_SIMPLE_MATH_RE = re.compile(r"^[\d\s\+\-\*/xX÷\(\)\.,=？?]+$")
_IDENTIFIER_RE = re.compile(
    r"^\s*(?:văn\s*bản\s*)?[A-Za-zÀ-Ỹ0-9Đđ][A-Za-zÀ-Ỹ0-9Đđ/\-_. ]{1,80}\s*$",
    re.IGNORECASE,
)

_SMALLTALK = {
    "xin chào",
    "chào",
    "hello",
    "hi",
    "cảm ơn",
    "cam on",
    "thanks",
    "thank you",
}

_OUT_OF_SCOPE_KEYWORDS = (
    "thời tiết",
    "weather",
    "dịch sang",
    "translate",
    "viết caption",
    "caption facebook",
    "viết email",
    "soạn email",
    "làm thơ",
    "kể chuyện",
    "bạn là ai",
    "who are you",
    "giá vàng",
    "tỷ giá",
    "bóng đá",
    "lịch thi đấu",
)

_DOCUMENT_HINT_KEYWORDS = (
    "văn bản",
    "công văn",
    "quyết định",
    "thông báo",
    "tờ trình",
    "phụ lục",
    "điều",
    "khoản",
    "mục",
    "nội dung",
    "quy định",
    "căn cứ",
    "số hiệu",
    "ngày",
    "giai đoạn",
    "cms",
    "ứng dụng",
    "chức năng",
    "dashboard",
)


def classify_query_scope(query: str) -> QueryScopeResult:
    """Classify whether a query should enter the internal-document RAG pipeline.

    The chatbot is scoped to indexed internal documents. This router avoids wasting
    retrieval/LLM calls on obvious off-topic requests while preserving document and
    identifier lookups such as ``3113`` or ``văn bản 3113``.
    """

    text = (query or "").strip()
    lowered = text.lower()

    if not text:
        return QueryScopeResult("out_of_scope", "empty query")

    if lowered in _SMALLTALK:
        return QueryScopeResult("smalltalk", "smalltalk")

    if _SIMPLE_MATH_RE.fullmatch(text) and any(
        op in text for op in ["+", "-", "*", "/", "x", "X", "÷"]
    ):
        return QueryScopeResult(
            "out_of_scope",
            "simple arithmetic is outside internal-document QA",
        )

    if any(keyword in lowered for keyword in _OUT_OF_SCOPE_KEYWORDS):
        return QueryScopeResult("out_of_scope", "general task outside internal-document QA")

    if any(keyword in lowered for keyword in _DOCUMENT_HINT_KEYWORDS):
        # Queries such as "văn bản 3113 nói gì" should still go to RAG.
        if any(ch.isdigit() for ch in text) and len(text.split()) <= 8:
            return QueryScopeResult("identifier_lookup", "document identifier lookup")
        return QueryScopeResult("document_question", "document-related query")

    has_digit = any(ch.isdigit() for ch in text)
    looks_like_code = bool(_IDENTIFIER_RE.fullmatch(text)) and len(text.split()) <= 6
    if has_digit and looks_like_code:
        return QueryScopeResult("identifier_lookup", "identifier/code lookup")

    return QueryScopeResult("document_question", "potential internal-document question")


def scoped_direct_answer(scope_result: QueryScopeResult) -> str | None:
    """Return a direct non-RAG answer for scopes that should bypass retrieval."""

    if scope_result.scope == "smalltalk":
        return (
            "Xin chào! Tôi hỗ trợ hỏi đáp dựa trên tài liệu nội bộ đã được lập chỉ mục. "
            "Bạn có thể nhập số hiệu văn bản hoặc đặt câu hỏi liên quan đến nội dung "
            "công văn, phụ lục, quy định trong tài liệu."
        )

    if scope_result.scope == "out_of_scope":
        return (
            "Chức năng này chỉ hỗ trợ hỏi đáp dựa trên tài liệu nội bộ đã được lập chỉ mục. "
            "Vui lòng đặt câu hỏi liên quan đến số hiệu văn bản, nội dung công văn, "
            "phụ lục hoặc quy định trong tài liệu."
        )

    return None
