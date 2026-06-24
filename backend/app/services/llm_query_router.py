from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any

from app.services.query_strategy import QueryStrategy


@dataclass(frozen=True)
class SemanticConstraint:
    type: str
    value: str

    def model_dump(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class SemanticRoute:
    intent: str
    question_scope: str
    answer_need: str
    requires_retrieval: bool
    document_reference: str
    document_identifiers: list[str]
    id_vb_values: list[str]
    document_codes: list[str]
    document_titles: list[str]
    primary_entities: list[str]
    lookup_entities: list[str]
    lookup_entity_type: str
    constraints: list[SemanticConstraint]
    requested_fields: list[str]
    preferred_chunk_types: list[str]
    requires_table_expansion: bool
    requires_section_expansion: bool
    confidence: float
    reason: str
    route_source: str = "llm"

    def model_dump(self) -> dict[str, Any]:
        data = asdict(self)
        data["constraints"] = [constraint.model_dump() for constraint in self.constraints]
        return data


class SemanticQueryRouterService:
    """Use an LLM router to make retrieval decisions from the user query.

    This service deliberately avoids regex/rule-based query parsing. The LLM is
    responsible for extracting document references, entities, constraints, and
    the preferred retrieval shape. Python code only validates and normalizes the
    returned JSON so the rest of the pipeline can consume it safely.
    """

    def __init__(self, llm_provider: Any) -> None:
        self._llm_provider = llm_provider

    async def route(
        self,
        *,
        query: str,
        retrieval_query: str | None = None,
        session_hint: str | None = None,
    ) -> SemanticRoute:
        query = " ".join((query or "").split())
        retrieval_query = " ".join((retrieval_query or query or "").split())
        if not query:
            return empty_semantic_route(reason="empty_query")

        try:
            response = await self._llm_provider.generate(
                system_prompt=_system_prompt(),
                user_prompt=_user_prompt(
                    query=query,
                    retrieval_query=retrieval_query,
                    session_hint=session_hint,
                ),
            )
            payload = _extract_json_object(response)
        except Exception:
            return empty_semantic_route(reason="llm_router_failed")

        return _route_from_payload(payload)


def query_strategy_from_semantic_route(route: SemanticRoute | None) -> QueryStrategy:
    if route is None:
        return QueryStrategy(
            strategies=("semantic_search",),
            search_terms=(),
        )

    strategies: list[str] = []
    answer_need = route.answer_need
    question_scope = route.question_scope
    preferred_chunk_types = set(route.preferred_chunk_types)

    if answer_need in {"direct_answer", "define", "find_source"}:
        strategies.append("exact_lookup")
    if answer_need in {"summarize", "explain"} or question_scope == "document_level":
        strategies.append("overview_summary")
    if answer_need in {"count", "enumerate"}:
        strategies.append("count_list")
    if (
        question_scope in {"table_level", "table_section_level", "row_level"}
        or preferred_chunk_types & {"table_parent", "table_row", "table_group", "table_column"}
    ):
        strategies.append("table_detail")
    if answer_need == "compare":
        strategies.append("comparison")
    if not strategies:
        strategies.append("semantic_search")

    search_terms = _dedupe(
        [
            *route.primary_entities,
            *route.lookup_entities,
            *route.document_identifiers,
            *route.document_codes,
            *route.document_titles,
            *[constraint.value for constraint in route.constraints],
            *route.requested_fields,
        ],
        limit=32,
    )
    requires_overview_context = bool(
        {"overview_summary", "count_list"} & set(strategies)
        or route.requires_section_expansion
    )
    return QueryStrategy(
        strategies=tuple(strategies),
        search_terms=tuple(search_terms),
        requires_overview_context=requires_overview_context,
        requires_diversity=requires_overview_context or "comparison" in strategies,
        may_need_second_retrieval=requires_overview_context,
    )


def empty_semantic_route(*, reason: str) -> SemanticRoute:
    return SemanticRoute(
        intent="question_answer",
        question_scope="general",
        answer_need="direct_answer",
        requires_retrieval=True,
        document_reference="none",
        document_identifiers=[],
        id_vb_values=[],
        document_codes=[],
        document_titles=[],
        primary_entities=[],
        lookup_entities=[],
        lookup_entity_type="unknown",
        constraints=[],
        requested_fields=["answer"],
        preferred_chunk_types=[],
        requires_table_expansion=False,
        requires_section_expansion=False,
        confidence=0.0,
        reason=reason,
        route_source="fallback_empty",
    )


def _system_prompt() -> str:
    return """
Bạn là lớp LLM router cho hệ thống RAG văn bản hành chính tiếng Việt.

Nhiệm vụ của bạn KHÔNG phải là trả lời người dùng.
Nhiệm vụ của bạn là đọc câu hỏi và trả về một JSON object hợp lệ để pipeline retrieval biết cần tìm tài liệu, chunk và bằng chứng nào.

Chỉ trả về JSON hợp lệ.
Không markdown.
Không bọc trong ```json.
Không giải thích ngoài JSON.

Các giá trị nên dùng:
- intent: greeting, question_answer, source_lookup, statistic, list, compare, summary, definition, explanation
- question_scope: conversation, document_level, section_level, table_level, table_section_level, row_level, general
- answer_need: direct_answer, count, enumerate, compare, summarize, explain, define, find_source
- document_reference: none, explicit_document, current_document, corpus_wide
- preferred_chunk_types: document_header, document_summary, document_body, footer_signature, table_parent, table_row, table_group, table_column

Quy tắc quan trọng:
- Nếu câu hỏi nêu rõ số/ký hiệu/tên văn bản, đặt document_reference là explicit_document.
- Nếu câu hỏi nói "văn bản này", "tài liệu này", "phụ lục đó", đặt document_reference là current_document.
- Nếu câu hỏi hỏi trên nhiều tài liệu hoặc toàn kho, đặt document_reference là corpus_wide.
- Trích mã/tên văn bản vào document_identifiers. Nếu thấy id_vb thì đưa vào id_vb_values. Nếu thấy số/ký hiệu văn bản thì đưa vào document_codes. Nếu thấy tên/trích yếu văn bản thì đưa vào document_titles.
- primary_entities là đối tượng chính cần bám để tìm đúng phạm vi.
- constraints là điều kiện phụ bên trong đối tượng chính, ví dụ phụ lục, mục, điều, bảng, cột, người, đơn vị, thời gian.
- Nếu hỏi về phụ lục/mục/điều trong một văn bản, phụ lục/mục/điều là constraints, không phải document chính.
- Nếu hỏi thông tin hành chính như người ký, ngày ban hành, cơ quan ban hành, ưu tiên preferred_chunk_types gồm document_header và footer_signature.
- Nếu hỏi bảng/dòng/cột/trường, ưu tiên table_parent, table_row, table_group, table_column tùy câu hỏi.
- Nếu hỏi nội dung mục/phụ lục/điều, ưu tiên document_body và document_summary.
- Giữ nguyên tên riêng, mã hiệu, số văn bản, tên phụ lục, tên cột, tên người.
""".strip()


def _user_prompt(
    *,
    query: str,
    retrieval_query: str,
    session_hint: str | None,
) -> str:
    session_section = ""
    if session_hint:
        session_section = f"""
Gợi ý phạm vi hiện tại nếu câu hỏi có tham chiếu nối tiếp:
{session_hint}
""".strip()

    return f"""
Câu hỏi gốc:
{query}

Câu retrieval đã rewrite nếu có:
{retrieval_query}

{session_section}

Trả về JSON theo schema này:
{{
  "intent": "question_answer",
  "question_scope": "general",
  "answer_need": "direct_answer",
  "requires_retrieval": true,
  "document_reference": "none",
  "document_identifiers": [],
  "id_vb_values": [],
  "document_codes": [],
  "document_titles": [],
  "primary_entities": [],
  "lookup_entities": [],
  "lookup_entity_type": "unknown",
  "constraints": [
    {{"type": "unknown", "value": ""}}
  ],
  "requested_fields": ["answer"],
  "preferred_chunk_types": [],
  "requires_table_expansion": false,
  "requires_section_expansion": false,
  "confidence": 0.0,
  "reason": "ngắn gọn vì sao route như vậy"
}}
""".strip()


def _extract_json_object(text: str) -> dict[str, Any]:
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start < 0 or end <= start:
            raise
        return json.loads(cleaned[start : end + 1])


def _route_from_payload(payload: dict[str, Any]) -> SemanticRoute:
    constraints = []
    for item in _list_value(payload.get("constraints"), limit=8):
        if not isinstance(item, dict):
            continue
        constraint_type = _clean_string(item.get("type"), default="unknown", max_chars=40)
        constraint_value = _clean_string(item.get("value"), default="", max_chars=240)
        if constraint_value:
            constraints.append(SemanticConstraint(type=constraint_type, value=constraint_value))

    confidence = payload.get("confidence", 0.0)
    try:
        confidence_value = max(0.0, min(1.0, float(confidence)))
    except (TypeError, ValueError):
        confidence_value = 0.0

    return SemanticRoute(
        intent=_clean_string(payload.get("intent"), default="question_answer", max_chars=60),
        question_scope=_clean_string(payload.get("question_scope"), default="general", max_chars=60),
        answer_need=_clean_string(payload.get("answer_need"), default="direct_answer", max_chars=60),
        requires_retrieval=bool(payload.get("requires_retrieval", True)),
        document_reference=_clean_string(payload.get("document_reference"), default="none", max_chars=60),
        document_identifiers=_string_list(payload.get("document_identifiers"), limit=8),
        id_vb_values=_string_list(payload.get("id_vb_values"), limit=8),
        document_codes=_string_list(payload.get("document_codes"), limit=8),
        document_titles=_string_list(payload.get("document_titles"), limit=8, max_chars=260),
        primary_entities=_string_list(payload.get("primary_entities"), limit=8, max_chars=260),
        lookup_entities=_string_list(payload.get("lookup_entities"), limit=8, max_chars=260),
        lookup_entity_type=_clean_string(payload.get("lookup_entity_type"), default="unknown", max_chars=80),
        constraints=constraints,
        requested_fields=_string_list(payload.get("requested_fields"), limit=8, max_chars=80) or ["answer"],
        preferred_chunk_types=_string_list(payload.get("preferred_chunk_types"), limit=8, max_chars=80),
        requires_table_expansion=bool(payload.get("requires_table_expansion", False)),
        requires_section_expansion=bool(payload.get("requires_section_expansion", False)),
        confidence=confidence_value,
        reason=_clean_string(payload.get("reason"), default="", max_chars=500),
    )


def _string_list(value: Any, *, limit: int, max_chars: int = 220) -> list[str]:
    return _dedupe(
        [
            _clean_string(item, default="", max_chars=max_chars)
            for item in _list_value(value, limit=limit * 2)
        ],
        limit=limit,
    )


def _list_value(value: Any, *, limit: int) -> list[Any]:
    if value is None:
        return []
    values = value if isinstance(value, list) else [value]
    return list(values)[: max(0, limit)]


def _clean_string(value: Any, *, default: str, max_chars: int) -> str:
    if value is None:
        return default
    cleaned = " ".join(str(value).split()).strip()
    return cleaned[:max_chars].strip() if cleaned else default


def _dedupe(values: list[str], *, limit: int) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = " ".join(str(value or "").split()).strip()
        if not cleaned:
            continue
        key = cleaned.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(cleaned)
        if len(result) >= limit:
            break
    return result
