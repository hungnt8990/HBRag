from __future__ import annotations

import json
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
    "không bịa. Nếu không thấy thông tin, trả null hoặc []. Luôn trả JSON "
    "hợp lệ, không markdown."
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
)
LIST_FIELDS = (
    "keywords",
    "aliases",
    "legal_refs",
    "entities",
    "obligations",
    "permissions",
    "prohibitions",
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
        model: str | None = None,
        max_chars: int | None = None,
        version: str | None = None,
    ) -> None:
        self._repository = repository
        self._llm_provider = llm_provider
        self._enabled = settings.chunk_enrichment_enabled if enabled is None else enabled
        self._model = (
            model
            or settings.chunk_enrichment_model
            or settings.llm_model
            or settings.llm_provider
        )
        self._max_chars = max_chars or settings.chunk_enrichment_max_chars
        self._version = version or settings.chunk_enrichment_version

    async def enrich_document(
        self,
        document_id: UUID,
        *,
        force: bool = False,
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

        if not self._enabled and not force:
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
                )
                await self._repository.update_chunk_enrichment(
                    chunk.id,
                    enrichment_metadata=enrichment_metadata,
                    enriched_content=enriched_content,
                )

                if enrichment_metadata["status"] == "success":
                    enriched_count += 1
                else:
                    failed_count += 1
                if len(preview) < PREVIEW_LIMIT:
                    preview.append(self._preview(chunk, enrichment_metadata))

            await self._repository.commit()
        except Exception as exc:
            await self._repository.rollback()
            raise ChunkEnrichmentError(f"Failed to enrich document chunks: {exc}") from exc

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

    async def _enrich_chunk(self, *, document: Any, chunk: Any) -> tuple[dict[str, Any], str | None]:
        try:
            raw = await self._llm_provider.generate(
                system_prompt=SYSTEM_PROMPT,
                user_prompt=self._build_user_prompt(document=document, chunk=chunk),
            )
            payload = self._parse_json(raw)
            normalized = self._normalize_payload(payload)
            metadata = {
                "version": self._version,
                "model": self._model,
                "status": "success",
                **normalized,
            }
            return metadata, self._build_enriched_content(
                content=str(getattr(chunk, "content", "")),
                enrichment=normalized,
            )
        except Exception as exc:
            metadata = {
                "version": self._version,
                "model": self._model,
                "status": "failed",
                **self._normalize_payload({}),
                "error": self._short_error(exc),
            }
            return metadata, getattr(chunk, "enriched_content", None)

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
    def _short_error(exc: Exception) -> str:
        return " ".join(str(exc).split()).strip()[:ERROR_LIMIT]
