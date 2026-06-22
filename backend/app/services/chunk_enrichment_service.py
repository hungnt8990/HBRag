from __future__ import annotations

import asyncio
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from uuid import UUID

from app.core.config import settings
from app.repositories.documents import DocumentRepository
from app.schemas.documents import (
    ChunkEnrichmentPreview,
    DocumentChunkEnrichmentResponse,
)
from app.services.llms import LLMProvider

SYSTEM_PROMPT = (
    "Bạn trích xuất enrichment ngắn cho chunk RAG tiếng Việt. Chỉ dùng thông tin "
    "có trong chunk và metadata được cung cấp. Không thêm thông tin ngoài nguồn, "
    "không suy đoán số hiệu, ngày tháng, đơn vị hoặc trách nhiệm. Nếu không có "
    "thông tin thì trả null hoặc []. Output JSON hợp lệ, không markdown. Giữ nguyên "
    "tên riêng, số hiệu và ngày tháng."
)

COMPACT_SCHEMA = {
    "summary": None,
    "keywords": [],
    "entities": [],
    "aliases": [],
    "answerable_facts": [],
    "possible_queries": [],
    "table_context": None,
    "legal_context": None,
    "confidence": 0.0,
}

SCALAR_FIELDS = (
    "summary",
    "document_type",
    "issuing_org",
    "document_code",
    "issued_date",
    "effective_date",
    "expiry_date",
    "structure_path",
    "table_context",
    "legal_context",
    "article_number",
    "article_title",
    "clause_number",
    "point_number",
    "appendix",
    "section_title",
    "parent_structure",
    "signer",
    "deadline",
    "effective_scope",
    "table_name",
)
LIST_FIELDS = (
    "keywords",
    "aliases",
    "legal_refs",
    "entities",
    "obligations",
    "permissions",
    "prohibitions",
    "recipients",
    "applies_to",
    "responsible_unit",
    "supersedes",
    "amends",
    "referenced_documents",
    "row_keys",
    "answerable_facts",
    "possible_queries",
)
BOOL_FIELDS = (
    "is_table_row",
    "is_footer_or_signature",
)
LLM_CHUNK_TYPES = {
    "table_row",
    "table_rows",
    "table_context",
    "assignment_section",
    "person_technology_assignment",
    "staff_matrix_row",
    "catalog_row_chunk",
}
NON_LLM_CHUNK_TYPES = {
    "administrative_footer",
    "header_footer",
    "footer",
    "signature",
    "recipients",
    "empty",
    "parse_error",
}
FAILED_QUALITY_STATUSES = {"fail", "failed", "rejected"}
PROFILE_FORCE_KEYS = {
    "chunk_enrichment_required",
    "enrichment_required",
    "always_enrich",
    "llm_enrich",
}
LEGAL_KEYS = ("article_number", "clause_number", "point_number", "appendix")
ENTITY_KEYS = (
    "staff_names",
    "staff",
    "person_name",
    "entities",
    "entity",
    "area",
    "lead_department",
    "department",
    "position",
    "title",
)
DOC_CODE_PATTERN = re.compile(
    r"\b\d{1,6}\s*/\s*[A-ZĐƠƯÂÊÔĂÁÀẢÃẠÉÈẺẼẸÍÌỈĨỊÓÒỎÕỌÚÙỦŨỤÝỲỶỸỴ0-9][A-ZĐƠƯÂÊÔĂÁÀẢÃẠÉÈẺẼẸÍÌỈĨỊÓÒỎÕỌÚÙỦŨỤÝỲỶỸỴ0-9\-_/]{1,}\b",
    flags=re.IGNORECASE,
)
DATE_PATTERN = re.compile(r"\b\d{1,2}/\d{1,2}/\d{4}\b")
IDENTIFIER_PATTERN = re.compile(r"\b[A-ZĐ]{2,}[A-ZĐ0-9_\-/]{1,}\b|\b\d{3,8}\b")
PREVIEW_LIMIT = 3
PREVIEW_TEXT_LIMIT = 240
ERROR_LIMIT = 300


class ChunkEnrichmentDocumentNotFoundError(LookupError):
    pass


class ChunkEnrichmentStatusError(ValueError):
    pass


class ChunkEnrichmentChunksNotFoundError(ValueError):
    pass


class ChunkEnrichmentError(RuntimeError):
    pass


@dataclass(frozen=True)
class _ChunkPlan:
    chunk: Any
    existing: dict[str, Any]
    rule_enrichment: dict[str, Any]
    input_hash: str
    should_call_llm: bool
    reason: dict[str, Any]


def should_llm_enrich(
    chunk: Any,
    document: Any,
    config: Any,
) -> tuple[bool, dict[str, Any]]:
    metadata = dict(getattr(chunk, "chunk_metadata", None) or {})
    content = str(getattr(chunk, "content", "") or "")
    chunk_type = str(metadata.get("chunk_type") or "text").casefold()
    mode = str(_config_value(config, "chunk_enrichment_mode", "selective") or "selective")
    mode = mode.casefold()
    min_chars = int(_config_value(config, "chunk_enrichment_min_chars", 300) or 300)
    reason: dict[str, Any] = {
        "mode": mode,
        "chunk_type": chunk_type,
        "content_chars": len(content.strip()),
    }

    if mode in {"off", "metadata_only"}:
        reason["skip"] = f"mode_{mode}"
        return False, reason
    if not content.strip():
        reason["skip"] = "empty_content"
        return False, reason
    if chunk_type in NON_LLM_CHUNK_TYPES or _looks_like_footer(content):
        reason["skip"] = "footer_or_non_indexable"
        return False, reason
    if metadata.get("indexable") is False or metadata.get("embedding_enabled") is False:
        reason["skip"] = "non_indexable"
        return False, reason
    if str(metadata.get("quality_status") or "").casefold() in FAILED_QUALITY_STATUSES:
        reason["skip"] = "failed_quality"
        return False, reason
    if any(_truthy(metadata.get(key)) for key in PROFILE_FORCE_KEYS):
        reason["trigger"] = "profile_required"
        return True, reason
    if mode == "all":
        reason["trigger"] = "mode_all"
        return True, reason
    if chunk_type in LLM_CHUNK_TYPES:
        reason["trigger"] = "structured_chunk_type"
        return True, reason

    rule_enrichment = build_rule_enrichment(document=document, chunk=chunk)
    identifier_count = len(_string_list(rule_enrichment.get("identifiers")))
    entity_count = _important_entity_count(metadata, rule_enrichment)
    has_structural_code = bool(
        _string_list(rule_enrichment.get("document_code"))
        or _string_list(rule_enrichment.get("dates"))
        or identifier_count
    )
    if len(content.strip()) < min_chars and has_structural_code:
        reason.update(trigger="short_with_codes", identifier_count=identifier_count)
        return True, reason
    if identifier_count >= 3:
        reason.update(trigger="many_identifiers", identifier_count=identifier_count)
        return True, reason
    if any(metadata.get(key) for key in LEGAL_KEYS):
        reason["trigger"] = "legal_structure"
        return True, reason
    if entity_count >= 2:
        reason.update(trigger="important_entities", entity_count=entity_count)
        return True, reason
    score = _optional_float(
        metadata.get("self_contained_score")
        or metadata.get("self_containedness")
        or metadata.get("context_score")
    )
    if score is not None and score < 0.55:
        reason.update(trigger="low_self_contained_score", self_contained_score=score)
        return True, reason
    if len(content.strip()) >= min_chars and _has_section_path(metadata):
        reason["skip"] = "clear_prose_with_section_path"
        return False, reason
    reason["skip"] = "selective_no_trigger"
    return False, reason


def build_rule_enrichment(*, document: Any, chunk: Any) -> dict[str, Any]:
    metadata = dict(getattr(chunk, "chunk_metadata", None) or {})
    document_metadata = dict(getattr(document, "document_metadata", None) or {})
    content = str(getattr(chunk, "content", "") or "")
    section_path = _section_path(metadata)
    doc_codes = _unique_strings(
        [
            *_string_list(metadata.get("document_code")),
            *_string_list(metadata.get("doc_codes")),
            *_string_list(metadata.get("document_codes")),
            *DOC_CODE_PATTERN.findall(content),
        ]
    )
    dates = _unique_strings(
        [
            *_string_list(metadata.get("issued_date")),
            *_string_list(metadata.get("dates")),
            *_string_list(metadata.get("document_dates")),
            *DATE_PATTERN.findall(content),
        ]
    )
    identifiers = _unique_strings(
        [
            *_string_list(metadata.get("identifiers")),
            *doc_codes,
            *dates,
            *IDENTIFIER_PATTERN.findall(content[:3000]),
        ]
    )
    row_start = metadata.get("row_start")
    row_end = metadata.get("row_end")
    row_range = None
    if row_start is not None or row_end is not None:
        row_range = f"{row_start if row_start is not None else '?'}-{row_end or row_start or '?'}"

    payload = {
        "document_title": getattr(document, "title", None),
        "issuer": _first_value(metadata.get("issuer"), document_metadata.get("issuer")),
        "issuing_org": _first_value(
            metadata.get("issuing_org"),
            document_metadata.get("issuing_org"),
            document_metadata.get("issuer"),
        ),
        "document_code": doc_codes[0] if doc_codes else None,
        "doc_codes": doc_codes,
        "issued_date": dates[0] if dates else None,
        "dates": dates,
        "section_path": " > ".join(section_path) if section_path else None,
        "article_number": metadata.get("article_number"),
        "clause_number": metadata.get("clause_number"),
        "point_number": metadata.get("point_number"),
        "appendix": metadata.get("appendix"),
        "table_name": metadata.get("table_name") or metadata.get("source_table"),
        "table_columns": _string_list(metadata.get("table_columns")),
        "row_start": row_start,
        "row_end": row_end,
        "row_range": row_range,
        "staff_names": _string_list(metadata.get("staff_names")),
        "area": metadata.get("area"),
        "lead_department": metadata.get("lead_department"),
        "identifiers": identifiers,
        "resolved_reference_text": metadata.get("resolved_reference_text"),
    }
    return {key: value for key, value in payload.items() if value not in (None, "", [])}


def _snapshot_document_for_enrichment(document: Any) -> SimpleNamespace:
    return SimpleNamespace(
        id=getattr(document, "id", None),
        title=getattr(document, "title", None),
        status=getattr(document, "status", None),
        document_metadata=dict(getattr(document, "document_metadata", None) or {}),
    )


def _snapshot_chunk_for_enrichment(chunk: Any) -> SimpleNamespace:
    return SimpleNamespace(
        id=getattr(chunk, "id", None),
        document_id=getattr(chunk, "document_id", None),
        chunk_index=getattr(chunk, "chunk_index", 0),
        content=str(getattr(chunk, "content", "") or ""),
        enriched_content=getattr(chunk, "enriched_content", None),
        token_count=getattr(chunk, "token_count", None),
        chunk_metadata=dict(getattr(chunk, "chunk_metadata", None) or {}),
    )


class ChunkEnrichmentService:
    def __init__(
        self,
        *,
        repository: DocumentRepository,
        llm_provider: LLMProvider,
        enabled: bool | None = None,
        provider: str | None = None,
        model: str | None = None,
        max_chars: int | None = None,
        version: str | None = None,
    ) -> None:
        self._repository = repository
        self._llm_provider = llm_provider
        self._enabled = bool(enabled) if enabled is not None else False
        self._provider = provider or settings.chunk_enrichment_provider or settings.llm_provider
        self._model = model or settings.chunk_enrichment_model or settings.llm_model or self._provider
        self._max_chars = max_chars or settings.chunk_enrichment_max_chars
        self._version = version or settings.chunk_enrichment_version
        self._prompt_version = settings.chunk_enrichment_prompt_version
        self._mode = settings.chunk_enrichment_mode
        self._min_chars = settings.chunk_enrichment_min_chars
        self._max_llm_chunks_per_document = settings.chunk_enrichment_max_llm_chunks_per_document
        self._concurrency = max(1, int(settings.chunk_enrichment_concurrency or 1))

    async def enrich_document(
        self,
        document_id: UUID,
        *,
        force: bool = False,
        enabled: bool | None = None,
        update_keyword_search_vector: bool = True,
        provider: str | None = None,
        model: str | None = None,
        max_chars: int | None = None,
        version: str | None = None,
    ) -> DocumentChunkEnrichmentResponse:
        document = await self._repository.get_document(document_id)
        if document is None:
            raise ChunkEnrichmentDocumentNotFoundError("Document not found.")

        document_status = str(getattr(document, "status", ""))
        if document_status not in {"chunked", "indexed"}:
            raise ChunkEnrichmentStatusError(
                "Only chunked or indexed documents can be enriched."
            )

        chunks = await self._repository.list_chunks_for_document(document_id)
        if not chunks:
            raise ChunkEnrichmentChunksNotFoundError("Document has no chunks to enrich.")

        # Keep enrichment planning and LLM calls independent from live ORM objects.
        # The DOffice queue runs chunking/enrichment/indexing in a long async flow,
        # and previous pipeline stages commit their own transactions. Snapshotting
        # the scalar fields we need prevents SQLAlchemy async lazy loads from being
        # attempted inside plain asyncio tasks, which otherwise raises
        # MissingGreenlet/greenlet_spawn errors.
        document_snapshot = _snapshot_document_for_enrichment(document)
        chunk_snapshots = [_snapshot_chunk_for_enrichment(chunk) for chunk in chunks]

        effective_enabled = self._enabled if enabled is None else bool(enabled)
        if not effective_enabled:
            return DocumentChunkEnrichmentResponse(
                document_id=document_id,
                status="skipped",
                enriched_count=0,
                failed_count=0,
                skipped_count=len(chunk_snapshots),
                preview=[self._preview(chunk, {"status": "skipped"}) for chunk in chunk_snapshots[:PREVIEW_LIMIT]],
            )

        previous = (
            self._provider,
            self._model,
            self._max_chars,
            self._version,
        )
        if provider:
            self._provider = provider
        if model:
            self._model = model
        if max_chars is not None and max_chars > 0:
            self._max_chars = max_chars
        if version:
            self._version = version

        try:
            plans = [
                self._plan_chunk(document=document_snapshot, chunk=chunk, force=force)
                for chunk in chunk_snapshots
            ]
            plans = self._apply_llm_limit(plans)
            results = await self._run_llm_plans(document=document_snapshot, plans=plans)

            enriched_count = 0
            failed_count = 0
            skipped_count = 0
            preview: list[ChunkEnrichmentPreview] = []
            for plan, (enrichment_metadata, enriched_content) in zip(plans, results, strict=True):
                await self._repository.update_chunk_enrichment(
                    plan.chunk.id,
                    enrichment_metadata=enrichment_metadata,
                    enriched_content=enriched_content,
                    rule_enrichment=plan.rule_enrichment,
                    update_search_vector=update_keyword_search_vector,
                )
                if enriched_content is not None:
                    plan.chunk.enriched_content = enriched_content
                attempt_status = enrichment_metadata.get("last_attempt_status") or enrichment_metadata.get("status")
                if attempt_status == "success":
                    enriched_count += 1
                elif attempt_status == "failed":
                    failed_count += 1
                else:
                    skipped_count += 1
                if len(preview) < PREVIEW_LIMIT:
                    preview.append(self._preview(plan.chunk, {**plan.existing, **enrichment_metadata}))

            await self._repository.commit()
        except Exception as exc:
            await self._repository.rollback()
            raise ChunkEnrichmentError(f"Failed to enrich document chunks: {exc}") from exc
        finally:
            self._provider, self._model, self._max_chars, self._version = previous

        if failed_count and enriched_count:
            response_status = "partial"
        elif failed_count:
            response_status = "failed"
        elif skipped_count and not enriched_count:
            response_status = "skipped"
        else:
            response_status = "enriched"

        return DocumentChunkEnrichmentResponse(
            document_id=document_id,
            status=response_status,
            enriched_count=enriched_count,
            failed_count=failed_count,
            skipped_count=skipped_count,
            preview=preview,
        )

    def _plan_chunk(self, *, document: Any, chunk: Any, force: bool) -> _ChunkPlan:
        existing = self._existing_enrichment(chunk)
        rule_enrichment = build_rule_enrichment(document=document, chunk=chunk)
        input_hash = self._input_hash(chunk=chunk, rule_enrichment=rule_enrichment)
        if existing.get("status") == "success" and existing.get("input_hash") == input_hash and not force:
            return _ChunkPlan(
                chunk=chunk,
                existing=existing,
                rule_enrichment=rule_enrichment,
                input_hash=input_hash,
                should_call_llm=False,
                reason={"skip": "cache_hit", "input_hash": input_hash},
            )
        should_call, reason = should_llm_enrich(
            chunk,
            document,
            {
                "chunk_enrichment_mode": self._mode,
                "chunk_enrichment_min_chars": self._min_chars,
            },
        )
        return _ChunkPlan(
            chunk=chunk,
            existing=existing,
            rule_enrichment=rule_enrichment,
            input_hash=input_hash,
            should_call_llm=should_call,
            reason=reason,
        )

    def _apply_llm_limit(self, plans: list[_ChunkPlan]) -> list[_ChunkPlan]:
        limit = self._max_llm_chunks_per_document
        if limit is None or limit < 0:
            return plans
        seen = 0
        limited: list[_ChunkPlan] = []
        for plan in plans:
            if not plan.should_call_llm:
                limited.append(plan)
                continue
            seen += 1
            if seen <= limit:
                limited.append(plan)
                continue
            limited.append(
                _ChunkPlan(
                    chunk=plan.chunk,
                    existing=plan.existing,
                    rule_enrichment=plan.rule_enrichment,
                    input_hash=plan.input_hash,
                    should_call_llm=False,
                    reason={**plan.reason, "skip": "llm_limit_reached"},
                )
            )
        return limited

    async def _run_llm_plans(
        self,
        *,
        document: Any,
        plans: list[_ChunkPlan],
    ) -> list[tuple[dict[str, Any], str | None]]:
        semaphore = asyncio.Semaphore(self._concurrency)

        async def run(plan: _ChunkPlan) -> tuple[dict[str, Any], str | None]:
            if not plan.should_call_llm:
                return self._skipped_metadata(plan), self._skipped_enriched_content(plan)
            async with semaphore:
                try:
                    return await self._enrich_chunk(document=document, plan=plan)
                except Exception as exc:
                    return self._failed_metadata(plan, exc), None

        return list(await asyncio.gather(*(run(plan) for plan in plans)))

    def _skipped_metadata(self, plan: _ChunkPlan) -> dict[str, Any]:
        attempted_at = datetime.now(UTC).isoformat()
        if plan.reason.get("skip") == "cache_hit" and plan.existing.get("status") == "success":
            return {
                "last_attempt_status": "skipped",
                "last_skip_reason": plan.reason,
                "last_attempt_at": attempted_at,
            }
        return {
            "version": self._version,
            "provider": self._provider,
            "model": self._model,
            "prompt_version": self._prompt_version,
            "status": "skipped",
            "error": None,
            "last_attempt_status": "skipped",
            "last_skip_reason": plan.reason,
            "last_error": None,
            "last_attempt_at": attempted_at,
            "input_hash": plan.input_hash,
            **self._normalize_payload({}),
        }

    @staticmethod
    def _skipped_enriched_content(plan: _ChunkPlan) -> str | None:
        if plan.reason.get("skip") == "cache_hit" and plan.existing.get("status") == "success":
            return getattr(plan.chunk, "enriched_content", None)
        return None

    async def _enrich_chunk(
        self,
        *,
        document: Any,
        plan: _ChunkPlan,
    ) -> tuple[dict[str, Any], str | None]:
        attempted_at = datetime.now(UTC).isoformat()
        try:
            raw = await self._llm_provider.generate(
                system_prompt=SYSTEM_PROMPT,
                user_prompt=self._build_user_prompt(document=document, plan=plan),
            )
            payload = self._parse_json(raw)
            normalized = self._normalize_payload(payload)
            normalized = self._merge_rule_defaults(normalized, plan.rule_enrichment)
            metadata = {
                "version": self._version,
                "provider": self._provider,
                "model": self._model,
                "prompt_version": self._prompt_version,
                "status": "success",
                "error": None,
                "last_attempt_status": "success",
                "last_error": None,
                "last_attempt_at": attempted_at,
                "input_hash": plan.input_hash,
                "llm_reason": plan.reason,
                **normalized,
            }
            return metadata, self._build_enriched_content(enrichment=normalized)
        except Exception as exc:
            if plan.existing.get("status") == "success":
                return {
                    "last_attempt_status": "failed",
                    "last_error": self._short_error(exc),
                    "last_attempt_at": attempted_at,
                    "llm_reason": plan.reason,
                }, getattr(plan.chunk, "enriched_content", None)
            return self._failed_metadata(plan, exc), None

    def _failed_metadata(self, plan: _ChunkPlan, exc: Exception) -> dict[str, Any]:
        error = self._short_error(exc)
        return {
            "version": self._version,
            "provider": self._provider,
            "model": self._model,
            "prompt_version": self._prompt_version,
            "status": "failed",
            "last_attempt_status": "failed",
            "last_error": error,
            "last_attempt_at": datetime.now(UTC).isoformat(),
            "input_hash": plan.input_hash,
            "llm_reason": plan.reason,
            **self._normalize_payload({}),
            "error": error,
        }

    def _build_user_prompt(self, *, document: Any, plan: _ChunkPlan) -> str:
        metadata = dict(getattr(plan.chunk, "chunk_metadata", None) or {})
        safe_metadata = {
            key: metadata.get(key)
            for key in (
                "chunk_type",
                "section_path",
                "article_number",
                "clause_number",
                "point_number",
                "appendix",
                "table_name",
                "table_columns",
                "row_start",
                "row_end",
                "staff_names",
                "area",
                "lead_department",
                "resolved_reference_text",
                "self_contained_score",
            )
            if metadata.get(key) not in (None, "", [])
        }
        payload = {
            "document": {
                "id": str(getattr(document, "id", "")),
                "title": getattr(document, "title", None),
            },
            "chunk_index": getattr(plan.chunk, "chunk_index", 0),
            "rule_enrichment": plan.rule_enrichment,
            "metadata": safe_metadata,
            "schema": COMPACT_SCHEMA,
        }
        chunk_text = str(getattr(plan.chunk, "content", ""))[: self._max_chars]
        return (
            f"Context JSON:\n{json.dumps(payload, ensure_ascii=False, default=str)}\n\n"
            f"Chunk text:\n{chunk_text}\n\n"
            "Trả JSON compact theo schema."
        )

    def _input_hash(self, *, chunk: Any, rule_enrichment: dict[str, Any]) -> str:
        payload = {
            "content": str(getattr(chunk, "content", "") or ""),
            "context": rule_enrichment,
            "version": self._version,
            "model": self._model,
            "prompt_version": self._prompt_version,
        }
        serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    @staticmethod
    def _parse_json(raw: str) -> dict[str, Any]:
        text = (raw or "").strip()
        if text.startswith("```"):
            lines = text.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines).strip()
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            start = text.find("{")
            end = text.rfind("}")
            if start < 0 or end <= start:
                raise ValueError("LLM response is not valid JSON.") from None
            try:
                payload = json.loads(text[start : end + 1])
            except json.JSONDecodeError as exc:
                raise ValueError("LLM response is not valid JSON.") from exc
        if not isinstance(payload, dict):
            raise ValueError("LLM response JSON must be an object.")
        return payload

    @classmethod
    def _normalize_payload(cls, payload: dict[str, Any]) -> dict[str, Any]:
        normalized: dict[str, Any] = {
            field: cls._optional_string(payload.get(field)) for field in SCALAR_FIELDS
        }
        normalized.update({field: cls._json_list(payload.get(field)) for field in LIST_FIELDS})
        normalized.update({field: cls._optional_bool(payload.get(field)) for field in BOOL_FIELDS})
        normalized["confidence"] = cls._optional_float(payload.get("confidence"))
        return normalized

    @classmethod
    def _merge_rule_defaults(
        cls,
        normalized: dict[str, Any],
        rule_enrichment: dict[str, Any],
    ) -> dict[str, Any]:
        merged = dict(normalized)
        for target, source in (
            ("document_code", "document_code"),
            ("issued_date", "issued_date"),
            ("issuing_org", "issuing_org"),
            ("structure_path", "section_path"),
            ("table_name", "table_name"),
            ("article_number", "article_number"),
            ("clause_number", "clause_number"),
            ("point_number", "point_number"),
            ("appendix", "appendix"),
        ):
            if not merged.get(target) and rule_enrichment.get(source):
                merged[target] = cls._optional_string(rule_enrichment.get(source))
        if not merged.get("entities") and rule_enrichment.get("staff_names"):
            merged["entities"] = cls._json_list(rule_enrichment.get("staff_names"))
        return merged

    @staticmethod
    def _build_enriched_content(*, enrichment: dict[str, Any]) -> str | None:
        lines: list[str] = []

        def add(label: str, value: Any) -> None:
            rendered = ChunkEnrichmentService._render_value(value)
            if rendered:
                lines.append(f"{label}: {rendered}")

        add("Tóm tắt", enrichment.get("summary"))
        add("Từ khóa", enrichment.get("keywords"))
        add("Thực thể", enrichment.get("entities"))
        add("Tên gọi khác", enrichment.get("aliases"))
        add("Fact trả lời trực tiếp", enrichment.get("answerable_facts"))
        add("Câu hỏi có thể trả lời", enrichment.get("possible_queries"))
        add("Ngữ cảnh bảng", enrichment.get("table_context"))
        add("Ngữ cảnh pháp lý", enrichment.get("legal_context"))
        add("Số hiệu văn bản", enrichment.get("document_code"))
        add("Ngày ban hành", enrichment.get("issued_date"))
        add("Đường dẫn cấu trúc", enrichment.get("structure_path"))
        if not lines:
            return None
        return "LLM enrichment:\n" + "\n".join(lines)

    @staticmethod
    def _render_value(value: Any) -> str | None:
        if value is None or value == "":
            return None
        if isinstance(value, list):
            clean_items = [ChunkEnrichmentService._render_value(item) for item in value]
            return "; ".join(item for item in clean_items if item) or None
        if isinstance(value, dict):
            return json.dumps(value, ensure_ascii=False, default=str)
        return " ".join(str(value).split()).strip() or None

    @staticmethod
    def _existing_enrichment(chunk: Any) -> dict[str, Any]:
        metadata = dict(getattr(chunk, "chunk_metadata", None) or {})
        enrichment = metadata.get("enrichment")
        return dict(enrichment) if isinstance(enrichment, dict) else {}

    @classmethod
    def _preview(cls, chunk: Any, enrichment: dict[str, Any]) -> ChunkEnrichmentPreview:
        enriched_content = getattr(chunk, "enriched_content", None)
        return ChunkEnrichmentPreview(
            chunk_index=int(getattr(chunk, "chunk_index", 0)),
            status=str(enrichment.get("status") or "unknown"),
            summary=cls._optional_string(enrichment.get("summary")),
            keywords=cls._string_list(enrichment.get("keywords")),
            enriched_content_preview=str(enriched_content)[:PREVIEW_TEXT_LIMIT] if enriched_content else None,
            error=cls._optional_string(enrichment.get("error")),
        )

    @staticmethod
    def _optional_string(value: Any) -> str | None:
        if value is None:
            return None
        if isinstance(value, list | tuple | set):
            value = ", ".join(str(item) for item in value if str(item).strip())
        if isinstance(value, dict):
            value = json.dumps(value, ensure_ascii=False, default=str)
        clean = " ".join(str(value).split()).strip()
        return clean or None

    @classmethod
    def _string_list(cls, value: Any) -> list[str]:
        return [item for item in (cls._optional_string(item) for item in cls._json_list(value)) if item]

    @staticmethod
    def _json_list(value: Any) -> list[Any]:
        if value is None or value == "":
            return []
        if isinstance(value, list | tuple | set):
            return [item for item in value if item not in (None, "")]
        return [value]

    @staticmethod
    def _optional_float(value: Any) -> float | None:
        return _optional_float(value)

    @staticmethod
    def _optional_bool(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.strip().casefold()
            if normalized in {"1", "true", "yes", "y", "on"}:
                return True
            if normalized in {"0", "false", "no", "n", "off"}:
                return False
        return bool(value) if value is not None else False

    @staticmethod
    def _short_error(exc: Exception) -> str:
        return " ".join(str(exc).split()).strip()[:ERROR_LIMIT]


def _config_value(config: Any, key: str, default: Any) -> Any:
    if isinstance(config, dict):
        return config.get(key, default)
    return getattr(config, key, default)


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().casefold() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def _looks_like_footer(content: str) -> bool:
    clean = " ".join(content.split()).casefold()
    if len(clean) > 600:
        return False
    return any(marker in clean for marker in ("nơi nhận", "người ký", "ký tên", "lưu:", "kt.", "tl."))


def _has_section_path(metadata: dict[str, Any]) -> bool:
    return bool(_section_path(metadata))


def _section_path(metadata: dict[str, Any]) -> list[str]:
    value = metadata.get("section_path") or metadata.get("headings") or metadata.get("structure_path")
    if isinstance(value, str):
        return [part.strip() for part in re.split(r">|/", value) if part.strip()]
    if isinstance(value, list | tuple):
        return [str(part).strip() for part in value if str(part).strip()]
    return []


def _important_entity_count(metadata: dict[str, Any], rule_enrichment: dict[str, Any]) -> int:
    values: list[str] = []
    for key in ENTITY_KEYS:
        values.extend(_string_list(metadata.get(key)))
    values.extend(_string_list(rule_enrichment.get("staff_names")))
    values.extend(_string_list(rule_enrichment.get("area")))
    values.extend(_string_list(rule_enrichment.get("lead_department")))
    return len(_unique_strings(values))


def _first_value(*values: Any) -> Any:
    for value in values:
        if value not in (None, "", []):
            return value
    return None


def _string_list(value: Any) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, str):
        return _unique_strings([part for part in re.split(r"[,;|]", value) if part.strip()])
    if isinstance(value, list | tuple | set):
        return _unique_strings([str(item) for item in value if str(item).strip()])
    return _unique_strings([str(value)])


def _unique_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for value in values:
        clean = " ".join(str(value or "").split()).strip().strip(".,;:()[]{}")
        if not clean:
            continue
        key = clean.casefold()
        if key in seen:
            continue
        seen.add(key)
        unique.append(clean)
    return unique


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
