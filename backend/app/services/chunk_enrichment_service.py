from __future__ import annotations

import json
from datetime import UTC, datetime
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
    "Bạn là bộ trích xuất metadata cho hệ thống RAG văn bản hành chính/doanh "
    "nghiệp tiếng Việt. Chỉ trích thông tin có trong chunk. Không suy đoán, "
    "không bịa số văn bản, ngày tháng, đơn vị, trách nhiệm hoặc mối quan hệ. "
    "Nếu không thấy thông tin, trả null, [] hoặc false theo kiểu dữ liệu. "
    "Nếu chunk là phần chân trang, chữ ký hoặc nơi nhận thì đánh dấu "
    "is_footer_or_signature=true. answerable_facts là các fact ngắn có thể "
    "trả lời trực tiếp từ chunk. Luôn trả JSON hợp lệ, không markdown."
)

ENRICHMENT_SCHEMA = {
    "summary": "1-2 câu tóm tắt nội dung chunk",
    "keywords": ["..."],
    "aliases": ["..."],
    "document_type": None,
    "issuing_org": None,
    "document_code": None,
    "issued_date": None,
    "effective_date": None,
    "expiry_date": None,
    "legal_refs": [],
    "structure_path": None,
    "entities": [],
    "obligations": [],
    "permissions": [],
    "prohibitions": [],
    "table_context": None,
    "article_number": None,
    "article_title": None,
    "clause_number": None,
    "point_number": None,
    "appendix": None,
    "section_title": None,
    "parent_structure": None,
    "signer": None,
    "recipients": [],
    "applies_to": [],
    "responsible_unit": [],
    "deadline": None,
    "effective_scope": None,
    "supersedes": [],
    "amends": [],
    "referenced_documents": [],
    "table_name": None,
    "row_keys": [],
    "is_table_row": False,
    "is_footer_or_signature": False,
    "answerable_facts": [],
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
)
BOOL_FIELDS = (
    "is_table_row",
    "is_footer_or_signature",
)
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
        self._model = (
            model
            or settings.chunk_enrichment_model
            or settings.llm_model
            or self._provider
        )
        self._max_chars = max_chars or settings.chunk_enrichment_max_chars
        self._version = version or settings.chunk_enrichment_version

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

        effective_enabled = self._enabled if enabled is None else bool(enabled)
        if not effective_enabled:
            return DocumentChunkEnrichmentResponse(
                document_id=document_id,
                status="skipped",
                enriched_count=0,
                failed_count=0,
                skipped_count=len(chunks),
                preview=[
                    self._preview(chunk, {"status": "skipped"})
                    for chunk in chunks[:PREVIEW_LIMIT]
                ],
            )

        enriched_count = 0
        failed_count = 0
        skipped_count = 0
        preview: list[ChunkEnrichmentPreview] = []
        previous_provider = self._provider
        previous_model = self._model
        previous_max_chars = self._max_chars
        previous_version = self._version
        if provider:
            self._provider = provider
        if model:
            self._model = model
        if max_chars is not None and max_chars > 0:
            self._max_chars = max_chars
        if version:
            self._version = version

        try:
            for chunk in chunks:
                existing = self._existing_enrichment(chunk)
                if existing.get("status") == "success" and not force:
                    skipped_count += 1
                    if len(preview) < PREVIEW_LIMIT:
                        preview.append(self._preview(chunk, existing))
                    continue

                enrichment_metadata, enriched_content = await self._enrich_chunk(
                    document=document,
                    chunk=chunk,
                    existing_enrichment=existing,
                )
                await self._repository.update_chunk_enrichment(
                    chunk.id,
                    enrichment_metadata=enrichment_metadata,
                    enriched_content=enriched_content,
                    update_search_vector=update_keyword_search_vector,
                )

                attempt_status = (
                    enrichment_metadata.get("last_attempt_status")
                    or enrichment_metadata.get("status")
                )
                if attempt_status == "success":
                    enriched_count += 1
                else:
                    failed_count += 1
                if len(preview) < PREVIEW_LIMIT:
                    preview.append(self._preview(chunk, {**existing, **enrichment_metadata}))

            await self._repository.commit()
        except Exception as exc:
            await self._repository.rollback()
            raise ChunkEnrichmentError(f"Failed to enrich document chunks: {exc}") from exc
        finally:
            self._provider = previous_provider
            self._model = previous_model
            self._max_chars = previous_max_chars
            self._version = previous_version

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

    async def _enrich_chunk(
        self,
        *,
        document: Any,
        chunk: Any,
        existing_enrichment: dict[str, Any],
    ) -> tuple[dict[str, Any], str | None]:
        attempted_at = datetime.now(UTC).isoformat()
        try:
            raw = await self._llm_provider.generate(
                system_prompt=SYSTEM_PROMPT,
                user_prompt=self._build_user_prompt(document=document, chunk=chunk),
            )
            payload = self._parse_json(raw)
            normalized = self._normalize_payload(payload)
            metadata = {
                "version": self._version,
                "provider": self._provider,
                "model": self._model,
                "status": "success",
                "error": None,
                "last_attempt_status": "success",
                "last_error": None,
                "last_attempt_at": attempted_at,
                **normalized,
            }
            return metadata, self._build_enriched_content(
                content=str(getattr(chunk, "content", "")),
                enrichment=normalized,
            )
        except Exception as exc:
            error = self._short_error(exc)
            if existing_enrichment.get("status") == "success":
                return {
                    "last_attempt_status": "failed",
                    "last_error": error,
                    "last_attempt_at": attempted_at,
                }, getattr(chunk, "enriched_content", None)
            metadata = {
                "version": self._version,
                "provider": self._provider,
                "model": self._model,
                "status": "failed",
                "last_attempt_status": "failed",
                "last_error": error,
                "last_attempt_at": attempted_at,
                **self._normalize_payload({}),
                "error": error,
            }
            return metadata, None

    def _build_user_prompt(self, *, document: Any, chunk: Any) -> str:
        document_metadata = {
            "document_id": str(getattr(document, "id", "")),
            "title": getattr(document, "title", None),
            "status": getattr(document, "status", None),
            "metadata": dict(getattr(document, "document_metadata", None) or {}),
            "schema": ENRICHMENT_SCHEMA,
        }
        serialized_metadata = json.dumps(
            document_metadata,
            ensure_ascii=False,
            default=str,
        )
        chunk_text = str(getattr(chunk, "content", ""))[: self._max_chars]
        return (
            "Document metadata:\n"
            f"{serialized_metadata}\n\n"
            "Chunk index:\n"
            f"{getattr(chunk, 'chunk_index', 0)}\n\n"
            "Chunk text:\n"
            f"{chunk_text}\n\n"
            "Hãy trích xuất metadata theo schema JSON đã yêu cầu."
        )

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

    @staticmethod
    def _build_enriched_content(*, content: str, enrichment: dict[str, Any]) -> str:
        lines: list[str] = []

        def add(label: str, value: Any) -> None:
            rendered = ChunkEnrichmentService._render_value(value)
            if rendered:
                lines.append(f"{label}: {rendered}")

        add("Tóm tắt", enrichment.get("summary"))
        add("Từ khóa", enrichment.get("keywords"))
        add("Tên gọi khác", enrichment.get("aliases"))
        add("Loại văn bản", enrichment.get("document_type"))
        add("Cơ quan ban hành", enrichment.get("issuing_org"))
        add("Số hiệu văn bản", enrichment.get("document_code"))
        add("Ngày ban hành", enrichment.get("issued_date"))
        add("Ngày hiệu lực", enrichment.get("effective_date"))
        add("Ngày hết hiệu lực", enrichment.get("expiry_date"))
        add("Tham chiếu pháp lý", enrichment.get("legal_refs"))
        add("Đường dẫn cấu trúc", enrichment.get("structure_path"))
        add("Thực thể", enrichment.get("entities"))
        add("Nghĩa vụ", enrichment.get("obligations"))
        add("Quyền hạn", enrichment.get("permissions"))
        add("Điều cấm", enrichment.get("prohibitions"))
        add("Ngữ cảnh bảng", enrichment.get("table_context"))
        add("Điều", enrichment.get("article_number"))
        add("Tiêu đề điều", enrichment.get("article_title"))
        add("Khoản", enrichment.get("clause_number"))
        add("Điểm", enrichment.get("point_number"))
        add("Phụ lục", enrichment.get("appendix"))
        add("Tiêu đề mục", enrichment.get("section_title"))
        add("Cấu trúc cha", enrichment.get("parent_structure"))
        add("Người ký", enrichment.get("signer"))
        add("Nơi nhận", enrichment.get("recipients"))
        add("Áp dụng cho", enrichment.get("applies_to"))
        add("Đơn vị chịu trách nhiệm", enrichment.get("responsible_unit"))
        add("Thời hạn", enrichment.get("deadline"))
        add("Phạm vi hiệu lực", enrichment.get("effective_scope"))
        add("Thay thế", enrichment.get("supersedes"))
        add("Sửa đổi", enrichment.get("amends"))
        add("Văn bản tham chiếu", enrichment.get("referenced_documents"))
        add("Tên bảng", enrichment.get("table_name"))
        add("Khóa dòng", enrichment.get("row_keys"))
        add("Fact trả lời trực tiếp", enrichment.get("answerable_facts"))

        clean_content = content.strip()
        if not lines:
            return clean_content
        enrichment_text = "LLM enrichment:\n" + "\n".join(lines)
        if not clean_content:
            return enrichment_text
        return f"{clean_content}\n\n{enrichment_text}"

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
            enriched_content_preview=(
                str(enriched_content)[:PREVIEW_TEXT_LIMIT] if enriched_content else None
            ),
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
        if value is None or value == "":
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

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
