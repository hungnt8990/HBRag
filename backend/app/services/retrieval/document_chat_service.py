"""Chat (stream) trên văn bản DOffice — TÁI DÙNG fusion retrieval + LLM đã có của dự án.

Một luồng: (1) route resolve ACL từ jwtToken, (2) MULTI-TURN: có ``history`` -> LLM condense
câu hỏi nối tiếp thành câu ĐỘC LẬP (timeout + fallback query gốc), (3) truy hồi =
``run_semantic_document_fusion`` (Qdrant dense + ES BM25 chunk) **+ nhánh ES BM25 doc-level
chạy song song** (``run_doc_bm25`` — parity với /search: recency + org boost + identifier),
giới hạn theo ``document_ids`` nếu truyền — rỗng/None = TOÀN BỘ văn bản người dùng được ACL
cho phép, (4) LLM SINH câu trả lời grounded trên các passage đánh số ``[i]`` (giống
``RagAnswerService``/``useChunk/retrieve.py``) và STREAM từng đoạn (token) ra ngoài.

Service KHÔNG phụ thuộc FastAPI: trả về luồng ``ChatStreamEvent`` để route bọc thành SSE.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.core.config import settings
from app.services.llm_gateway import get_llm_gateway
from app.services.retrieval.document_semantic_search import run_semantic_document_fusion
from app.services.retrieval.retrieval_profile import get_retrieval_profile

logger = logging.getLogger("document_chat")

# cắt mỗi passage để prompt không phình quá dài (cấu hình theo retrieval profile)
MAX_PASSAGE_CHARS = get_retrieval_profile().max_passage_chars
# cắt mỗi message lịch sử khi đưa vào prompt condense/answer
MAX_HISTORY_MSG_CHARS = 900


class ChatHistoryMessage(BaseModel):
    role: Literal["user", "assistant"] = Field(description="Vai của lượt thoại")
    content: str = Field(min_length=1, max_length=8000, description="Nội dung lượt thoại")


class DocumentChatRequest(BaseModel):
    query: str = Field(min_length=1, max_length=4000, description="Câu hỏi của người dùng")
    jwtToken: str | None = Field(default=None, description="JWT để lấy ID_NV (bắt buộc khi type=DO)")
    type: Literal["EO", "DO"] | None = Field(default="DO", description="DO = DOffice; EO = làm sau")
    session_id: str | None = Field(
        default=None,
        description=(
            "ID cuộc hội thoại (UUID). LẦN ĐẦU bỏ trống -> backend tạo mới & trả về trong event "
            "`meta.session_id`; các lần SAU gửi lại session_id đó để backend nạp short-term memory "
            "(lịch sử gần đây) làm ngữ cảnh. Lượt gần nhất cách hiện tại quá ngưỡng (mặc định 4h) -> "
            "không nạp lịch sử nhưng vẫn ghi tiếp vào cùng hội thoại."
        ),
    )
    document_ids: list[str] | None = Field(
        default=None,
        description=(
            "Danh sách document_id để CHAT TRÊN nhóm văn bản này (lọc + chỉ hỏi trên nội dung các "
            "văn bản đó). Rỗng/null = hỏi trên TOÀN BỘ văn bản người dùng được ACL cho phép."
        ),
    )
    history: list[ChatHistoryMessage] | None = Field(
        default=None,
        max_length=20,
        description=(
            "Lịch sử hội thoại (client tự giữ, gửi kèm mỗi lượt) để hỏi NỐI TIẾP: câu hỏi tham "
            "chiếu lượt trước ('văn bản này do ai ký?') được tự viết lại thành câu độc lập trước "
            "khi truy hồi. Bỏ trống = câu hỏi độc lập như cũ."
        ),
    )
    top_n: int = Field(default=8, ge=1, le=30, description="Số passage (đoạn) đưa vào LLM làm ngữ cảnh")
    answer_mode: str | None = Field(default=None, description="Chế độ trả lời (RagAnswerService)")
    answer_style: str | None = Field(default=None, description="Văn phong trả lời (RagAnswerService)")
    # id_nv KHÔNG truyền trực tiếp: route tự parse từ jwtToken (type=DO).
    id_nv: int | None = Field(default=None, description="Tự lấy từ jwtToken khi type=DO")


@dataclass
class ChatStreamEvent:
    """Sự kiện stream: ``event`` (meta|sources|delta|done|error) + ``data`` (JSON-serializable)."""

    event: str
    data: Any


def _passage_text(hit: dict[str, Any]) -> str:
    """Ghép văn bản của 1 hit để làm passage cho LLM: ưu tiên các chunk ngữ cảnh, fallback trích yếu."""
    sem = hit.get("_semantic") or {}
    parts: list[str] = []
    for item in sem.get("context") or []:
        text = " ".join(str(item.get("content") or item.get("chunk_text") or "").split())
        if text:
            parts.append(text)
    if not parts:
        src = hit.get("_source") or {}
        text = " ".join(str(src.get("title") or src.get("summary") or "").split())
        if text:
            parts.append(text)
    return "\n".join(parts)[:MAX_PASSAGE_CHARS]


def _citation(index: int, hit: dict[str, Any]) -> dict[str, Any]:
    """Thông tin nguồn (đánh số [index]) để client hiển thị + đối chiếu.

    Source nội bộ theo schema BA; JSON GIỮ key cũ (ky_hieu/trich_yeu/ngay_vb — giá trị map
    từ field BA) + kèm key BA song song (document_no/title/issue_date) để FE chuyển dần."""
    src = hit.get("_source") or {}
    sem = hit.get("_semantic") or {}
    document_no = src.get("document_no")
    title = src.get("title")
    issue_date = str(src.get("issue_date") or "")[:10] or None
    return {
        "index": index,
        "document_id": src.get("document_id"),
        # Key BA chuẩn (mới):
        "document_no": document_no,
        "title": title,
        "issue_date": issue_date,
        "signer": src.get("signer"),
        "issuer_org_name": src.get("issuer_org_name"),
        # Key cũ (compat FE):
        "id_vb": src.get("document_id"),
        "ky_hieu": document_no,
        "trich_yeu": title,
        "ngay_vb": issue_date,
        "score": hit.get("_score"),
        "rerank_score": sem.get("rerank_score"),
        "evidence": (sem.get("evidence") or {}).get("status"),
    }


def _history_lines(history: list[ChatHistoryMessage] | None, *, limit: int) -> list[str]:
    """Rút gọn ``limit`` message cuối thành dòng 'role: content' (cắt ký tự)."""
    lines: list[str] = []
    for message in list(history or [])[-limit:]:
        content = " ".join(str(message.content or "").split())
        if content:
            lines.append(f"{message.role}: {content[:MAX_HISTORY_MSG_CHARS]}")
    return lines


async def _condense_query(query: str, history: list[ChatHistoryMessage] | None) -> str:
    """Viết lại câu hỏi NỐI TIẾP thành câu ĐỘC LẬP dựa trên lịch sử (multi-turn).

    Không history / câu hỏi đã độc lập (``should_rewrite_with_context`` False) -> trả gốc,
    KHÔNG tốn LLM. LLM lỗi/timeout/kết quả bất thường -> fallback query gốc (không chặn chat)."""
    from app.services.queries.query_rewrite_service import (
        QueryRewriteService,
        normalize_rewrite_text,
        should_rewrite_with_context,
    )

    lines = _history_lines(history, limit=max(1, int(settings.document_chat_history_max_messages)))
    if not lines or settings.llm_provider == "fake":
        return query
    if not should_rewrite_with_context(query):
        return query
    context = "\n".join(f"- {line}" for line in lines)
    try:
        raw = await asyncio.wait_for(
            get_llm_gateway().generate(
                system_prompt=QueryRewriteService._system_prompt(),
                user_prompt=QueryRewriteService._user_prompt(query=query, context=context),
                task_name="document_chat_condense",
            ),
            timeout=float(settings.document_chat_condense_timeout_s) or None,
        )
    except Exception:
        logger.warning("Condense câu hỏi nối tiếp lỗi/timeout -> dùng query gốc query=%r", query[:60])
        return query
    cleaned = QueryRewriteService._clean_rewrite(str(raw or ""))
    if not QueryRewriteService._is_usable_rewrite(cleaned, original_query=query):
        return query
    if normalize_rewrite_text(cleaned) == normalize_rewrite_text(query):
        return query
    return cleaned


async def _safe_doc_bm25(
    query: str, *, top_n: int, acl_subject: Any, document_ids: set[str] | None
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """BM25 doc-level cho chat: lỗi ES -> nuốt + log, trả rỗng (KHÔNG fail cả stream)."""
    from app.services.retrieval.document_search_service import run_doc_bm25

    try:
        return await run_doc_bm25(
            query, top_n=top_n, acl_subject=acl_subject, document_ids=document_ids
        )
    except Exception:
        logger.warning("BM25 doc-level chat lỗi -> bỏ nhánh này query=%r", query[:60], exc_info=True)
        return {}, []


async def stream_document_chat(
    *,
    query: str,
    acl_subject: Any,
    document_ids: set[str] | None,
    top_n: int,
    answer_mode: str | None = None,
    answer_style: str | None = None,
    history: list[ChatHistoryMessage] | None = None,
    session_id: Any = None,
) -> AsyncIterator[ChatStreamEvent]:
    """Truy hồi (giới hạn ACL + ``document_ids``) rồi STREAM câu trả lời LLM. Không raise ra ngoài
    (mọi lỗi -> phát ``error`` event)."""
    from app.services.rag.rag_answer_service import build_system_prompt

    clean = " ".join(str(query or "").split()).strip()
    if not clean:
        yield ChatStreamEvent("error", {"message": "Câu hỏi rỗng."})
        return

    # Multi-turn: câu hỏi nối tiếp -> condense thành câu độc lập TRƯỚC retrieval.
    retrieval_query = await _condense_query(clean, history)
    scope = {str(d).strip() for d in (document_ids or set()) if str(d).strip()} or None
    meta: dict[str, Any] = {
        "session_id": str(session_id) if session_id is not None else None,
        "scope": "documents" if scope else "all",
        "document_ids": sorted(scope) if scope else None,
    }
    if retrieval_query != clean:
        meta["rewritten_query"] = retrieval_query
    yield ChatStreamEvent("meta", meta)

    # 1) Truy hồi: fusion (dense Qdrant + ES BM25 chunk + RRF + rerank + CRAG) + nhánh
    #    ES BM25 doc-level SONG SONG (parity /search: recency + org boost + identifier).
    bm25_task = asyncio.ensure_future(
        _safe_doc_bm25(retrieval_query, top_n=top_n, acl_subject=acl_subject, document_ids=scope)
    )
    try:
        fusion = await run_semantic_document_fusion(
            query=retrieval_query,
            top_n=top_n,
            acl_subject=acl_subject,
            document_ids=scope,
            bm25_hits_task=bm25_task,
        )
    except Exception:
        bm25_task.cancel()
        logger.warning("fusion chat lỗi query=%r", retrieval_query[:60], exc_info=True)
        yield ChatStreamEvent("error", {"message": "Lỗi truy hồi văn bản."})
        return

    hits = (fusion.hits if fusion else [])[:top_n]
    evidence_summary = getattr(fusion, "evidence_summary", None)
    used_vector = bool(getattr(fusion, "used_vector", False))
    if not hits:
        # Fusion rỗng (3 nhánh semantic không ra) nhưng BM25 doc-level có thể vẫn có
        # (query thuần lexical/mã) -> fallback dùng hits ES (passage = title/summary).
        try:
            _, bm25_hits = await bm25_task
            hits = (bm25_hits or [])[:top_n]
        except Exception:
            logger.warning("BM25 fallback chat lỗi query=%r", retrieval_query[:60], exc_info=True)
    elif not bm25_task.done():
        bm25_task.cancel()
    # Adaptive: căn cứ đã MẠNH (top-1 rerank cao) -> chỉ giữ N passage đầu, giảm nhiễu cho
    # LLM + ngắn prompt (câu hỏi mơ hồ/evidence yếu vẫn giữ đủ top_n passage để LLM tự cân).
    strong_top_n = int(settings.document_chat_strong_top_n or 0)
    if strong_top_n and evidence_summary == "strong" and len(hits) > strong_top_n:
        top1_rerank = float(((hits[0].get("_semantic") or {}).get("rerank_score")) or 0.0)
        if top1_rerank >= float(settings.document_chat_strong_rerank_min):
            hits = hits[:strong_top_n]
    citations = [_citation(i, hit) for i, hit in enumerate(hits, start=1)]
    yield ChatStreamEvent(
        "sources",
        {"count": len(citations), "citations": citations, "evidence_summary": evidence_summary},
    )

    passages = [_passage_text(hit) for hit in hits]
    numbered = "\n\n".join(f"[{i}] {text}" for i, text in enumerate(passages, start=1) if text)
    if not numbered:
        msg = (
            "Không tìm thấy nội dung phù hợp trong phạm vi văn bản "
            + ("được chọn." if scope else "bạn được phép truy cập.")
        )
        yield ChatStreamEvent("delta", {"text": msg})
        yield ChatStreamEvent(
            "done",
            {"answer": msg, "total_sources": 0, "evidence_summary": evidence_summary, "used_vector": used_vector},
        )
        return

    # 2) LLM sinh câu trả lời grounded trên các passage đánh số [i] (giống RagAnswerService).
    #    Multi-turn: chèn vài lượt thoại cuối TRƯỚC Document Text để trả lời nối mạch;
    #    passage đã truy hồi theo câu condensed, còn Question giữ câu GỐC của người dùng.
    system_prompt = build_system_prompt(answer_mode=answer_mode, answer_style=answer_style, query=clean)
    history_block = ""
    recent_turns = _history_lines(history, limit=4)
    if recent_turns:
        history_block = (
            "Recent Conversation (chỉ để hiểu ngữ cảnh, KHÔNG phải căn cứ trích dẫn):\n"
            + "\n".join(recent_turns)
            + "\n\n"
        )
    user_prompt = (
        f"{history_block}"
        "Document Text:\n"
        f"{numbered}\n\n"
        "Trả lời câu hỏi CHỈ dựa trên các đoạn văn bản đánh số ở trên. Nếu tài liệu không có "
        "thông tin, hãy nói rõ một cách tự nhiên. Không tạo mục Nguồn/Tài liệu ở cuối.\n\n"
        f"Question:\n{clean}"
    )

    collected: list[str] = []
    try:
        async for delta in get_llm_gateway().stream_generate(
            system_prompt=system_prompt, user_prompt=user_prompt, task_name="document_chat_answer"
        ):
            if delta:
                collected.append(delta)
                yield ChatStreamEvent("delta", {"text": delta})
    except Exception:
        logger.warning("LLM stream chat lỗi query=%r", clean[:60], exc_info=True)
        yield ChatStreamEvent("error", {"message": "Lỗi sinh câu trả lời."})
        return

    yield ChatStreamEvent(
        "done",
        {
            "answer": "".join(collected).strip(),
            "total_sources": len(citations),
            "evidence_summary": evidence_summary,
            "used_vector": used_vector,
        },
    )
