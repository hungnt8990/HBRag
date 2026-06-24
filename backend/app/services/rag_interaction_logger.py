from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.core.config import settings

logger = logging.getLogger(__name__)

LOG_DIR = Path(__file__).resolve().parents[3] / "log"
LOG_FILE = LOG_DIR / "rag_chat_logs.jsonl"
MARKDOWN_LOG_FILE = LOG_DIR / "rag_chat_logs.md"
PREVIEW_CHARS = 700


def log_rag_interaction(
    *,
    question: str,
    answer: str,
    session_id: Any = None,
    user_message_id: Any = None,
    assistant_message_id: Any = None,
    user_id: Any = None,
    organization_id: Any = None,
    document_ids: Any = None,
    retrieval_query: str | None = None,
    evidence_query: str | None = None,
    top_k: int | None = None,
    candidate_k: int | None = None,
    effective_top_k: int | None = None,
    effective_candidate_k: int | None = None,
    max_context_chars: int | None = None,
    effective_max_context_chars: int | None = None,
    answer_mode: str | None = None,
    answer_style: str | None = None,
    query_strategy: Any = None,
    query_contract: Any = None,
    document_scope: Any = None,
    semantic_route: Any = None,
    rewrite_result: Any = None,
    rerank_response: Any = None,
    context_chunks: list[Any] | None = None,
    selected_artifacts: list[Any] | None = None,
    artifact_result: Any = None,
    citations: list[Any] | None = None,
    latency_ms: float | None = None,
    answer_status: str = "answered",
    error_message: str | None = None,
) -> None:
    """Best-effort JSONL logging for RAG debugging.

    This logger must never break the chat flow. It writes one JSON object per
    answer so the file can be inspected manually or loaded into notebooks.
    """

    try:
        context_chunks = context_chunks or []
        selected_artifacts = selected_artifacts or []
        citations = citations or []
        context_char_count = sum(len(getattr(item.chunk, "content", "") or "") for item in context_chunks)
        record = {
            "timestamp": datetime.now(UTC).isoformat(),
            "session_id": _string_or_none(session_id),
            "user_message_id": _string_or_none(user_message_id),
            "assistant_message_id": _string_or_none(assistant_message_id),
            "user_id": _string_or_none(user_id),
            "organization_id": _string_or_none(organization_id),
            "document_ids": _string_list(document_ids),
            "question": question,
            "answer": answer,
            "answer_status": answer_status,
            "error_message": error_message,
            "query": {
                "retrieval_query": retrieval_query,
                "evidence_query": evidence_query,
                "query_strategy": _query_strategy_list(query_strategy),
                "query_contract": _query_contract_value(query_contract),
                "document_scope": _document_scope_value(document_scope),
                "semantic_route": _semantic_route_value(semantic_route),
                "rewrite_used": bool(getattr(rewrite_result, "rewritten", False)) if rewrite_result is not None else False,
                "rewrite_reason": getattr(rewrite_result, "reason", None) if rewrite_result is not None else None,
            },
            "settings": {
                "top_k": top_k,
                "candidate_k": candidate_k,
                "effective_top_k": effective_top_k,
                "effective_candidate_k": effective_candidate_k,
                "max_context_chars": max_context_chars,
                "effective_max_context_chars": effective_max_context_chars,
                "answer_mode": answer_mode,
                "answer_style": answer_style,
                "llm_model": getattr(settings, "llm_model", None),
                "embedding_model": getattr(settings, "embedding_model", None),
                "reranker_model": getattr(settings, "reranker_model", None),
            },
            "retrieval": {
                "rerank_query": getattr(rerank_response, "query", None) if rerank_response is not None else None,
                "rerank_top_k": getattr(rerank_response, "top_k", None) if rerank_response is not None else None,
                "rerank_candidate_k": getattr(rerank_response, "candidate_k", None) if rerank_response is not None else None,
                "reranked_results": [_rerank_result_to_dict(result) for result in list(getattr(rerank_response, "results", []) or [])],
                "selected_artifact_count": len(selected_artifacts),
                "used_chunk_fallback": bool(getattr(artifact_result, "used_chunk_fallback", False)) if artifact_result is not None else None,
            },
            "context": {
                "final_context_count": len(context_chunks),
                "context_char_count": context_char_count,
                "context_approx_token_count": _approx_token_count_from_chars(context_char_count),
                "chunks": [_context_chunk_to_dict(item) for item in context_chunks],
            },
            "citations": [_citation_to_dict(citation) for citation in citations],
            "latency_ms": latency_ms,
        }
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        with MARKDOWN_LOG_FILE.open("a", encoding="utf-8") as handle:
            handle.write(_record_to_readable_markdown(record))
    except Exception:
        logger.exception("Failed to write RAG interaction log.")


def _record_to_readable_markdown(record: dict[str, Any]) -> str:
    query = record.get("query") or {}
    route = query.get("semantic_route") or {}
    document_scope = query.get("document_scope") or {}
    settings = record.get("settings") or {}
    retrieval = record.get("retrieval") or {}
    context = record.get("context") or {}
    reranked_results = retrieval.get("reranked_results") or []
    chunks = context.get("chunks") or []
    rerank_by_chunk_id = {
        str(result.get("chunk_id")): result
        for result in reranked_results
        if result.get("chunk_id")
    }

    lines: list[str] = []
    lines.append(f"\n## {record.get('timestamp')}\n")
    lines.append(f"**Session:** `{record.get('session_id')}`\n")

    lines.append("**Câu hỏi:**")
    lines.append(str(record.get("question") or "").strip())
    lines.append("")

    lines.append("**Câu hỏi độc lập:**")
    lines.append(
        str(
            query.get("evidence_query")
            or query.get("retrieval_query")
            or record.get("question")
            or ""
        ).strip()
    )
    lines.append("")

    lines.append("**Timing:**")
    lines.extend(
        [
            f"- Total: {_format_ms(record.get('latency_ms'))}",
            f"- Intent/router: N/A | {_route_source(route)} | confidence={route.get('confidence')}",
            "- Hybrid: N/A",
            "- Rerank: N/A",
            "- Expand: disabled/N/A",
            f"- Document candidates: {_document_candidate_summary(document_scope)}",
            f"- Document reference: {route.get('document_reference')}",
            f"- Document scope: {_document_scope_summary(document_scope)}",
            f"- Primary entities: {route.get('primary_entities') or []}",
            f"- Constraints: {route.get('constraints') or []}",
        ]
    )
    lines.append("")

    lines.append("**Retrieval control:**")
    lines.extend(
        [
            f"- Router: {_route_source(route)}",
            f"- Intent: {route.get('intent')}",
            f"- Question scope: {route.get('question_scope')}",
            f"- Answer need: {route.get('answer_need')}",
            f"- Lookup entity type: {route.get('lookup_entity_type')}",
            f"- Document reference: {route.get('document_reference')}",
            f"- Document identifiers: {route.get('document_identifiers') or []}",
            f"- Document codes: {route.get('document_codes') or []}",
            f"- Lookup entities: {route.get('lookup_entities') or []}",
            f"- Constraints: {route.get('constraints') or []}",
            f"- Preferred chunk types: {route.get('preferred_chunk_types') or []}",
            f"- Requested fields: {route.get('requested_fields') or []}",
            f"- Requires table expansion: {route.get('requires_table_expansion')}",
            f"- Requires section expansion: {route.get('requires_section_expansion')}",
            f"- Document scope mode: {document_scope.get('mode')}",
            f"- Document lock reason: {document_scope.get('reason')}",
            f"- Locked document: {_locked_document_summary(document_scope)}",
            f"- Query strategy: {query.get('query_strategy') or []}",
            f"- Query contract: {query.get('query_contract')}",
            f"- Rewrite used: {query.get('rewrite_used')} | reason={query.get('rewrite_reason')}",
            f"- Rerank query: {retrieval.get('rerank_query')}",
            f"- top_k: requested={settings.get('top_k')} | effective={settings.get('effective_top_k')}",
            f"- candidate_k: requested={settings.get('candidate_k')} | effective={settings.get('effective_candidate_k')}",
            f"- Context: count={context.get('final_context_count')} | chars={context.get('context_char_count')} | approx_tokens={context.get('context_approx_token_count')}",
            f"- Artifacts: selected={retrieval.get('selected_artifact_count')} | used_chunk_fallback={retrieval.get('used_chunk_fallback')}",
            f"- Fallback used: {_fallback_used(route)}",
        ]
    )
    lines.append("")

    lines.append("**Trả lời:**")
    lines.append(str(record.get("answer") or "").strip())
    lines.append("")

    lines.append("**Top sources:**")
    if not chunks:
        lines.append("- Không có source.")
    for index, chunk in enumerate(chunks[:10], start=1):
        rerank_result = rerank_by_chunk_id.get(str(chunk.get("chunk_id")), {})
        document_title = chunk.get("document_title") or chunk.get("document_id")
        lines.append(f"{index}. {document_title}")
        lines.append(f"   - Vị trí: {_source_location(chunk=chunk, rerank_result=rerank_result)}")
        lines.append(
            "   - Search: "
            + _source_search_summary(
                chunk=chunk,
                rerank_result=rerank_result,
                document_scope=document_scope,
            )
        )
        lines.append(f"   - Preview: {chunk.get('content_preview') or ''}")
    lines.append("")
    lines.append("---\n")
    return "\n".join(lines)


def _record_to_markdown(record: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("\n---\n")
    lines.append(f"## {record.get('timestamp')}\n")
    lines.append("### Câu hỏi\n")
    lines.append(_md_block(record.get("question") or ""))
    lines.append("### Câu trả lời\n")
    lines.append(_md_block(record.get("answer") or ""))

    lines.append("### Thông tin chung\n")
    lines.extend(
        [
            f"- `session_id`: `{record.get('session_id')}`",
            f"- `user_message_id`: `{record.get('user_message_id')}`",
            f"- `assistant_message_id`: `{record.get('assistant_message_id')}`",
            f"- `user_id`: `{record.get('user_id')}`",
            f"- `document_ids`: `{', '.join(record.get('document_ids') or [])}`",
            f"- `answer_status`: `{record.get('answer_status')}`",
            f"- `latency_ms`: `{record.get('latency_ms')}`",
        ]
    )
    lines.append("")

    query = record.get("query") or {}
    lines.append("### Query/Retrieval Query\n")
    lines.extend(
        [
            f"- `retrieval_query`: {_inline_code(query.get('retrieval_query'))}",
            f"- `evidence_query`: {_inline_code(query.get('evidence_query'))}",
            f"- `query_strategy`: `{', '.join(query.get('query_strategy') or [])}`",
            f"- `query_contract`: `{query.get('query_contract')}`",
            f"- `document_scope`: `{query.get('document_scope')}`",
            f"- `semantic_route`: `{query.get('semantic_route')}`",
            f"- `rewrite_used`: `{query.get('rewrite_used')}`",
            f"- `rewrite_reason`: `{query.get('rewrite_reason')}`",
        ]
    )
    lines.append("")

    settings = record.get("settings") or {}
    lines.append("### Cấu hình\n")
    for key in (
        "top_k",
        "candidate_k",
        "effective_top_k",
        "effective_candidate_k",
        "max_context_chars",
        "effective_max_context_chars",
        "answer_mode",
        "answer_style",
        "llm_model",
        "embedding_model",
        "reranker_model",
    ):
        lines.append(f"- `{key}`: `{settings.get(key)}`")
    lines.append("")

    retrieval = record.get("retrieval") or {}
    lines.append("### Kết quả retrieval/rerank\n")
    lines.extend(
        [
            f"- `rerank_query`: {_inline_code(retrieval.get('rerank_query'))}",
            f"- `rerank_top_k`: `{retrieval.get('rerank_top_k')}`",
            f"- `rerank_candidate_k`: `{retrieval.get('rerank_candidate_k')}`",
            f"- `selected_artifact_count`: `{retrieval.get('selected_artifact_count')}`",
            f"- `used_chunk_fallback`: `{retrieval.get('used_chunk_fallback')}`",
        ]
    )
    reranked_results = retrieval.get("reranked_results") or []
    for index, result in enumerate(reranked_results[:15], start=1):
        lines.append(
            f"- #{index} chunk `{result.get('chunk_id')}` | type `{result.get('chunk_type')}` | "
            f"rerank `{result.get('rerank_score')}` | fused `{result.get('fused_score')}` | "
            f"flags `{', '.join(result.get('source_flags') or [])}`"
        )
        detail = _result_location(result)
        if detail:
            lines.append(f"  - Vị trí: {detail}")
        if result.get("content_preview"):
            lines.append(f"  - Preview: {result.get('content_preview')}")
    lines.append("")

    context = record.get("context") or {}
    lines.append("### Context đưa vào LLM\n")
    lines.extend(
        [
            f"- `final_context_count`: `{context.get('final_context_count')}`",
            f"- `context_char_count`: `{context.get('context_char_count')}`",
            f"- `context_approx_token_count`: `{context.get('context_approx_token_count')}`",
        ]
    )
    for chunk in context.get("chunks") or []:
        lines.append(
            f"#### [{chunk.get('citation_index')}] chunk `{chunk.get('chunk_id')}` "
            f"- type `{chunk.get('chunk_type')}`"
        )
        lines.append(f"- Document: `{chunk.get('document_title')}`")
        lines.append(f"- Chunk index: `{chunk.get('chunk_index')}`")
        lines.append(f"- Source flags: `{', '.join(chunk.get('source_flags') or [])}`")
        detail = _result_location(chunk)
        if detail:
            lines.append(f"- Vị trí: {detail}")
        lines.append(_md_block(chunk.get("content_preview") or ""))

    citations = record.get("citations") or []
    lines.append("### Citations\n")
    if not citations:
        lines.append("- Không có citation.")
    for citation in citations:
        lines.append(
            f"- [{citation.get('citation_index')}] chunk `{citation.get('chunk_id')}` "
            f"| document `{citation.get('document_title')}` | type `{citation.get('chunk_type')}`"
        )
        detail = _result_location(citation)
        if detail:
            lines.append(f"  - Vị trí: {detail}")
        if citation.get("quote"):
            lines.append(f"  - Quote: {citation.get('quote')}")
    lines.append("")
    return "\n".join(lines)


def _context_chunk_to_dict(context_chunk: Any) -> dict[str, Any]:
    chunk = context_chunk.chunk
    metadata = dict(getattr(chunk, "chunk_metadata", None) or {})
    document = getattr(chunk, "document", None)
    return {
        "citation_index": getattr(context_chunk, "citation_index", None),
        "chunk_id": _string_or_none(getattr(chunk, "id", None)),
        "document_id": _string_or_none(getattr(chunk, "document_id", None)),
        "document_title": getattr(document, "title", None),
        "chunk_index": getattr(chunk, "chunk_index", None),
        "chunk_type": metadata.get("chunk_type"),
        "source_type": getattr(context_chunk, "source_type", None),
        "source_flags": list(getattr(context_chunk, "source_flags", None) or []),
        "section_title": metadata.get("section_title"),
        "heading_path": metadata.get("heading_path"),
        "table_name": metadata.get("table_name"),
        "row_number": metadata.get("row_number"),
        "column_name": metadata.get("column_name"),
        "field_name": metadata.get("field_name"),
        "metadata": _selected_metadata(metadata),
        "content_length": len(getattr(chunk, "content", "") or ""),
        "content_preview": _preview(getattr(chunk, "content", "") or ""),
    }


def _rerank_result_to_dict(result: Any) -> dict[str, Any]:
    metadata = dict(getattr(result, "metadata", None) or {})
    return {
        "chunk_id": _string_or_none(getattr(result, "chunk_id", None)),
        "document_id": _string_or_none(getattr(result, "document_id", None)),
        "rerank_score": getattr(result, "rerank_score", None),
        "fused_score": getattr(result, "fused_score", None),
        "vector_score": getattr(result, "vector_score", None),
        "keyword_score": getattr(result, "keyword_score", None),
        "source_flags": list(getattr(result, "source_flags", None) or []),
        "chunk_type": metadata.get("chunk_type"),
        "section_title": metadata.get("section_title"),
        "heading_path": metadata.get("heading_path"),
        "table_name": metadata.get("table_name"),
        "row_number": metadata.get("row_number"),
        "column_name": metadata.get("column_name"),
        "content_preview": _preview(getattr(result, "content_preview", "") or ""),
    }


def _citation_to_dict(citation: Any) -> dict[str, Any]:
    metadata = dict(getattr(citation, "metadata", None) or {})
    return {
        "citation_index": getattr(citation, "citation_index", None),
        "chunk_id": _string_or_none(getattr(citation, "chunk_id", None)),
        "document_id": _string_or_none(getattr(citation, "document_id", None)),
        "document_title": getattr(citation, "document_title", None),
        "chunk_index": getattr(citation, "chunk_index", None),
        "source_flags": list(getattr(citation, "source_flags", None) or []),
        "chunk_type": metadata.get("chunk_type"),
        "section_title": metadata.get("section_title"),
        "heading_path": metadata.get("heading_path"),
        "table_name": metadata.get("table_name"),
        "row_number": metadata.get("row_number"),
        "column_name": metadata.get("column_name"),
        "quote": _preview(getattr(citation, "quote", "") or ""),
    }


def _selected_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "id_vb",
        "document_code",
        "ky_hieu",
        "trich_yeu",
        "issued_date",
        "issuer",
        "source_type",
        "chunk_type",
        "section_title",
        "heading_path",
        "table_name",
        "table_index",
        "row_index",
        "row_number",
        "column_name",
        "field_name",
        "source_format",
        "source_flags",
        "match_type",
    )
    return {key: metadata[key] for key in keys if key in metadata}


def _preview(value: str, *, limit: int = PREVIEW_CHARS) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _string_list(values: Any) -> list[str]:
    if values is None:
        return []
    return sorted(str(value) for value in values)


def _query_strategy_list(query_strategy: Any) -> list[str]:
    if query_strategy is None:
        return []
    return list(getattr(query_strategy, "strategies", []) or [])


def _query_contract_value(query_contract: Any) -> str | None:
    if query_contract is None:
        return None
    return getattr(query_contract, "detected_intent", None)


def _document_scope_value(document_scope: Any) -> dict[str, Any] | None:
    if document_scope is None:
        return None
    if hasattr(document_scope, "model_dump"):
        return document_scope.model_dump()
    if isinstance(document_scope, dict):
        return document_scope
    return {
        "mode": getattr(document_scope, "mode", None),
        "document_ids": _string_list(getattr(document_scope, "document_ids", None)),
        "matched_by": getattr(document_scope, "matched_by", None),
        "confidence": getattr(document_scope, "confidence", None),
        "reason": getattr(document_scope, "reason", None),
    }


def _semantic_route_value(semantic_route: Any) -> dict[str, Any] | None:
    if semantic_route is None:
        return None
    if hasattr(semantic_route, "model_dump"):
        return semantic_route.model_dump()
    if isinstance(semantic_route, dict):
        return semantic_route
    return {
        "intent": getattr(semantic_route, "intent", None),
        "question_scope": getattr(semantic_route, "question_scope", None),
        "answer_need": getattr(semantic_route, "answer_need", None),
        "document_reference": getattr(semantic_route, "document_reference", None),
        "document_identifiers": _string_list(getattr(semantic_route, "document_identifiers", None)),
        "document_codes": _string_list(getattr(semantic_route, "document_codes", None)),
        "constraints": getattr(semantic_route, "constraints", None),
        "preferred_chunk_types": _string_list(getattr(semantic_route, "preferred_chunk_types", None)),
        "confidence": getattr(semantic_route, "confidence", None),
        "reason": getattr(semantic_route, "reason", None),
    }


def _format_ms(value: Any) -> str:
    try:
        milliseconds = float(value)
    except (TypeError, ValueError):
        return "N/A"
    return f"{milliseconds:.0f} ms ({milliseconds / 1000:.2f}s)"


def _route_source(route: dict[str, Any]) -> str:
    return str(route.get("route_source") or "unknown")


def _fallback_used(route: dict[str, Any]) -> bool:
    return _route_source(route) not in {"llm", "unknown"}


def _document_candidate_summary(document_scope: dict[str, Any]) -> list[dict[str, Any]]:
    document_ids = document_scope.get("document_ids") or []
    return [
        {
            "document_id": document_id,
            "rank": index,
        }
        for index, document_id in enumerate(document_ids, start=1)
    ]


def _document_scope_summary(document_scope: dict[str, Any]) -> str:
    if not document_scope:
        return "mode=None | reason=None | locked=None"
    return (
        f"mode={document_scope.get('mode')} | "
        f"reason={document_scope.get('reason')} | "
        f"locked={_locked_document_summary(document_scope)}"
    )


def _locked_document_summary(document_scope: dict[str, Any]) -> str:
    document_ids = document_scope.get("document_ids") or []
    if document_scope.get("mode") != "hard" or not document_ids:
        return "None"
    if len(document_ids) == 1:
        return str(document_ids[0])
    return ", ".join(str(value) for value in document_ids)


def _source_location(*, chunk: dict[str, Any], rerank_result: dict[str, Any]) -> str:
    chunk_index = chunk.get("chunk_index")
    chunk_type = chunk.get("chunk_type") or rerank_result.get("chunk_type")
    parts = [f"chunk={chunk_index}", f"type={chunk_type}"]
    heading = _heading_value(chunk) or _heading_value(rerank_result)
    if heading:
        parts.append(f"heading={heading}")
    table_name = chunk.get("table_name") or rerank_result.get("table_name")
    if table_name:
        parts.append(f"table={table_name}")
    row_number = chunk.get("row_number") or rerank_result.get("row_number")
    if row_number:
        parts.append(f"row={row_number}")
    column_name = chunk.get("column_name") or rerank_result.get("column_name")
    if column_name:
        parts.append(f"column={column_name}")
    return ", ".join(str(part) for part in parts if part)


def _source_search_summary(
    *,
    chunk: dict[str, Any],
    rerank_result: dict[str, Any],
    document_scope: dict[str, Any],
) -> str:
    flags = rerank_result.get("source_flags") or chunk.get("source_flags") or []
    search = "+".join(str(flag) for flag in flags) if flags else "unknown"
    scope_mode = document_scope.get("mode")
    rerank_score = rerank_result.get("rerank_score")
    fused_score = rerank_result.get("fused_score")
    return (
        f"{search} | "
        f"scope_mode={scope_mode} | "
        f"rerank={rerank_score} | "
        f"fused={fused_score}"
    )


def _heading_value(item: dict[str, Any]) -> str | None:
    heading_path = item.get("heading_path")
    if isinstance(heading_path, list) and heading_path:
        return " > ".join(str(value) for value in heading_path)
    if heading_path:
        return str(heading_path)
    section_title = item.get("section_title")
    if section_title:
        return str(section_title)
    return None


def _approx_token_count_from_chars(chars: int) -> int:
    return max(0, int(chars / 4))


def _md_block(value: Any) -> str:
    return "```text\n" + str(value or "").strip() + "\n```\n"


def _inline_code(value: Any) -> str:
    return "`" + str(value or "").replace("`", "'") + "`"


def _result_location(item: dict[str, Any]) -> str:
    parts: list[str] = []
    if item.get("section_title"):
        parts.append(f"section `{item.get('section_title')}`")
    if item.get("heading_path"):
        path = item.get("heading_path")
        if isinstance(path, list):
            parts.append("heading `" + " > ".join(str(value) for value in path) + "`")
        else:
            parts.append(f"heading `{path}`")
    if item.get("table_name"):
        parts.append(f"table `{item.get('table_name')}`")
    if item.get("row_number"):
        parts.append(f"row `{item.get('row_number')}`")
    if item.get("column_name"):
        parts.append(f"column `{item.get('column_name')}`")
    return "; ".join(parts)
