from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from app.schemas.chat import RagSessionContext
from app.services.llm_gateway import LLMGateway
from app.services.memory.memory_base import MemoryResult

MAX_REWRITE_CHARS = 900
MAX_CONTEXT_CHARS = 6000


@dataclass(frozen=True)
class QueryRewriteResult:
    original_query: str
    retrieval_query: str
    rewritten: bool
    reason: str
    context_char_count: int = 0


def normalize_rewrite_text(text: str) -> str:
    if not text:
        return ""
    text = text.lower()
    text = unicodedata.normalize("NFD", text)
    text = "".join(char for char in text if unicodedata.category(char) != "Mn")
    text = text.replace("Ä‘", "d").replace("Ä", "d")
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def should_rewrite_with_context(question: str) -> bool:
    """Detect follow-up questions that need conversational anchors restored."""

    normalized = normalize_rewrite_text(question)
    if not normalized:
        return False

    followup_phrases = (
        "van ban nay",
        "tai lieu nay",
        "quyet dinh nay",
        "thong bao nay",
        "bao cao nay",
        "bang nay",
        "bang do",
        "dong nay",
        "dong do",
        "muc nay",
        "muc do",
        "nguoi nay",
        "nguoi do",
        "nguoi kia",
        "tac gia nay",
        "tac gia do",
        "van ban do",
        "tai lieu do",
        "o tren",
        "ben tren",
        "truoc do",
        "vua roi",
        "cau truoc",
        "ket qua tren",
        "noi dung tren",
        "y tren",
        "cai nay",
        "cai do",
        "no",
        "ho",
        "nguoi dau tien",
        "nguoi thu hai",
        "nguoi cuoi",
        "cai dau tien",
        "cai thu hai",
        "cai cuoi",
        "this document",
        "that document",
        "this table",
        "that table",
        "this item",
        "that item",
        "the above",
        "previous answer",
    )
    if any(phrase in normalized for phrase in followup_phrases):
        return True

    if normalized.startswith("con ") and not normalized.startswith("con nguoi"):
        return True

    if re.search(r"\bthi sao\b|\bnua khong\b|\btiep tuc\b|\bke tiep\b", normalized):
        return True

    words = normalized.split()
    return len(words) <= 6 and any(token in normalized for token in ("sao", "nua", "tiep", "do", "nay"))


class QueryRewriteService:
    def __init__(self, llm_provider: LLMGateway) -> None:
        self._llm_provider = llm_provider

    async def rewrite(
        self,
        *,
        query: str,
        session_context: RagSessionContext | None = None,
        memory_context: list[MemoryResult] | None = None,
        session_summary: str | None = None,
    ) -> QueryRewriteResult:
        query = " ".join((query or "").split())
        if not query:
            return QueryRewriteResult(
                original_query=query,
                retrieval_query=query,
                rewritten=False,
                reason="empty_query",
            )

        context = self._build_context(
            session_context=session_context,
            memory_context=memory_context,
            session_summary=session_summary,
        )
        if not context:
            return QueryRewriteResult(
                original_query=query,
                retrieval_query=query,
                rewritten=False,
                reason="no_context",
            )

        if not should_rewrite_with_context(query):
            return QueryRewriteResult(
                original_query=query,
                retrieval_query=query,
                rewritten=False,
                reason="not_followup",
                context_char_count=len(context),
            )

        try:
            rewritten = await self._llm_provider.generate(
                system_prompt=self._system_prompt(),
                user_prompt=self._user_prompt(query=query, context=context),
            )
        except Exception:
            return QueryRewriteResult(
                original_query=query,
                retrieval_query=self._fallback_hint_query(query=query, context=context),
                rewritten=True,
                reason="llm_error_context_hints",
                context_char_count=len(context),
            )

        cleaned = self._clean_rewrite(rewritten)
        if not self._is_usable_rewrite(cleaned, original_query=query):
            return QueryRewriteResult(
                original_query=query,
                retrieval_query=self._fallback_hint_query(query=query, context=context),
                rewritten=True,
                reason="invalid_rewrite_context_hints",
                context_char_count=len(context),
            )

        if normalize_rewrite_text(cleaned) == normalize_rewrite_text(query):
            return QueryRewriteResult(
                original_query=query,
                retrieval_query=query,
                rewritten=False,
                reason="unchanged",
                context_char_count=len(context),
            )

        return QueryRewriteResult(
            original_query=query,
            retrieval_query=cleaned,
            rewritten=True,
            reason="llm_followup_rewrite",
            context_char_count=len(context),
        )

    @staticmethod
    def _system_prompt() -> str:
        return (
            "You rewrite follow-up questions for an internal-document RAG system. "
            "Return only one standalone search question. Do not answer the question. "
            "Do not add facts that are not present in the supplied context. Preserve "
            "proper names, document titles, identifiers, numbers, and the user's language."
        )

    @staticmethod
    def _user_prompt(*, query: str, context: str) -> str:
        return (
            "Rewrite the CURRENT QUESTION into a standalone question suitable for "
            "retrieval. Use RECENT CONTEXT only to resolve references such as this "
            "document, that table, the previous result, he/she/they, it, this item, "
            "or similar abbreviated follow-ups. If the question is already clear, "
            "return it unchanged.\n\n"
            f"RECENT CONTEXT:\n{context}\n\n"
            f"CURRENT QUESTION:\n{query}\n\n"
            "STANDALONE QUESTION:"
        )

    @staticmethod
    def _clean_rewrite(value: str) -> str:
        text = " ".join(str(value or "").strip().split())
        text = re.sub(r"^```(?:json|text)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
        text = re.sub(
            r"^(?:standalone question|rewritten question|cau hoi doc lap)\s*:\s*",
            "",
            text,
            flags=re.IGNORECASE,
        )
        return text.strip(" \"'`")

    @staticmethod
    def _is_usable_rewrite(value: str, *, original_query: str) -> bool:
        if not value:
            return False
        if len(value) > MAX_REWRITE_CHARS:
            return False
        normalized = normalize_rewrite_text(value)
        if not normalized:
            return False
        answer_like_markers = (
            "generated from provided context",
            "relevant citations",
            "source list",
            "tai lieu tham khao",
        )
        if any(marker in normalized for marker in answer_like_markers):
            return False
        if len(value) > max(len(original_query) * 6, 500):
            return False
        return True

    @staticmethod
    def _build_context(
        *,
        session_context: RagSessionContext | None,
        memory_context: list[MemoryResult] | None,
        session_summary: str | None,
    ) -> str:
        lines: list[str] = []

        if session_context is not None:
            for label, value in (
                ("Last topic", session_context.last_topic),
                ("Current scope", session_context.current_scope),
                ("User scope", session_context.user_scope),
                ("Current document id", session_context.current_document_id),
            ):
                text = " ".join(str(value or "").split())
                if text:
                    lines.append(f"{label}: {text[:700]}")

            for message in list(session_context.recent_messages or [])[-8:]:
                content = " ".join(str(getattr(message, "content", "") or "").split())
                if not content:
                    continue
                role = str(getattr(message, "role", "message") or "message")
                lines.append(f"{role}: {content[:900]}")

        if session_summary:
            lines.append(f"Session summary: {' '.join(session_summary.split())[:1200]}")

        for memory in list(memory_context or [])[:6]:
            content = " ".join(str(getattr(memory, "content", "") or "").split())
            if not content:
                continue
            memory_type = str(getattr(memory, "memory_type", "memory") or "memory")
            lines.append(f"Memory ({memory_type}): {content[:700]}")

        context = "\n".join(f"- {line}" for line in lines if line)
        return context[:MAX_CONTEXT_CHARS]

    @staticmethod
    def _fallback_hint_query(*, query: str, context: str) -> str:
        return f"{query}\n\nShort-term context for retrieval only; do not cite it as evidence:\n{context[:2000]}"
