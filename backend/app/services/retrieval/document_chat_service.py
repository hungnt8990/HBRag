"""Chat (stream) trên văn bản DOffice — TÁI DÙNG fusion retrieval + LLM đã có của dự án.

Một luồng: (1) route resolve ACL từ jwtToken, (2) ``run_semantic_document_fusion`` truy hồi
(giới hạn theo ``document_ids`` nếu truyền — rỗng/None = TOÀN BỘ văn bản người dùng được ACL cho
phép), (3) LLM SINH câu trả lời grounded trên các passage đánh số ``[i]`` (giống
``RagAnswerService``/``useChunk/retrieve.py``) và STREAM từng đoạn (token) ra ngoài.

Service KHÔNG phụ thuộc FastAPI: trả về luồng ``ChatStreamEvent`` để route bọc thành SSE.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.services.llm_gateway import get_llm_gateway
from app.services.retrieval.document_semantic_search import run_semantic_document_fusion

logger = logging.getLogger("document_chat")

MAX_PASSAGE_CHARS = 2000  # cắt mỗi passage để prompt không phình quá dài


class DocumentChatRequest(BaseModel):
    query: str = Field(min_length=1, max_length=4000, description="Câu hỏi của người dùng")
    jwtToken: str | None = Field(default=None, description="JWT để lấy ID_NV (bắt buộc khi type=DO)")
    type: Literal["EO", "DO"] | None = Field(default="DO", description="DO = DOffice; EO = làm sau")
    document_ids: list[str] | None = Field(
        default=None,
        description=(
            "Danh sách document_id để CHAT TRÊN nhóm văn bản này (lọc + chỉ hỏi trên nội dung các "
            "văn bản đó). Rỗng/null = hỏi trên TOÀN BỘ văn bản người dùng được ACL cho phép."
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
        text = " ".join(str(src.get("trich_yeu") or src.get("tom_tat") or "").split())
        if text:
            parts.append(text)
    return "\n".join(parts)[:MAX_PASSAGE_CHARS]


def _citation(index: int, hit: dict[str, Any]) -> dict[str, Any]:
    """Thông tin nguồn (đánh số [index]) để client hiển thị + đối chiếu."""
    src = hit.get("_source") or {}
    sem = hit.get("_semantic") or {}
    return {
        "index": index,
        "document_id": src.get("document_id"),
        "id_vb": src.get("id_vb"),
        "ky_hieu": src.get("ky_hieu"),
        "trich_yeu": src.get("trich_yeu"),
        "ngay_vb": src.get("ngay_vb"),
        "score": hit.get("_score"),
        "rerank_score": sem.get("rerank_score"),
        "evidence": (sem.get("evidence") or {}).get("status"),
    }


async def stream_document_chat(
    *,
    query: str,
    acl_subject: Any,
    document_ids: set[str] | None,
    top_n: int,
    answer_mode: str | None = None,
    answer_style: str | None = None,
) -> AsyncIterator[ChatStreamEvent]:
    """Truy hồi (giới hạn ACL + ``document_ids``) rồi STREAM câu trả lời LLM. Không raise ra ngoài
    (mọi lỗi -> phát ``error`` event)."""
    from app.services.rag.rag_answer_service import build_system_prompt

    clean = " ".join(str(query or "").split()).strip()
    if not clean:
        yield ChatStreamEvent("error", {"message": "Câu hỏi rỗng."})
        return

    scope = {str(d).strip() for d in (document_ids or set()) if str(d).strip()} or None
    yield ChatStreamEvent(
        "meta",
        {"scope": "documents" if scope else "all", "document_ids": sorted(scope) if scope else None},
    )

    # 1) Truy hồi fusion (dense Qdrant + ES BM25 + RRF + rerank + CRAG), giới hạn theo document_ids.
    try:
        fusion = await run_semantic_document_fusion(
            query=clean, top_n=top_n, acl_subject=acl_subject, document_ids=scope
        )
    except Exception:
        logger.warning("fusion chat lỗi query=%r", clean[:60], exc_info=True)
        yield ChatStreamEvent("error", {"message": "Lỗi truy hồi văn bản."})
        return

    hits = (fusion.hits if fusion else [])[:top_n]
    evidence_summary = getattr(fusion, "evidence_summary", None)
    used_vector = bool(getattr(fusion, "used_vector", False))
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
    system_prompt = build_system_prompt(answer_mode=answer_mode, answer_style=answer_style, query=clean)
    user_prompt = (
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
