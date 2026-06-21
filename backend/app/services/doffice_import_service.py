from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import httpx

from app.core.config import settings
from app.repositories.documents import DocumentRepository
from app.services.parsers import MarkdownParser, parsed_element_to_dict


class DOfficeDocumentNotFoundError(LookupError):
    pass


class DOfficeDuplicateDocumentError(ValueError):
    pass


class DOfficeImportError(RuntimeError):
    pass


@dataclass(frozen=True)
class DOfficeImportResult:
    document_id: UUID
    id_vb: str
    title: str
    character_count: int
    reused_existing: bool = False


class DOfficeImportService:
    def __init__(
        self,
        *,
        repository: DocumentRepository,
        elastic_url: str | None = None,
    ) -> None:
        self._repository = repository
        self._elastic_url = elastic_url or settings.doffice_elastic_url

    async def import_document(
        self,
        *,
        id_vb: str,
        uploaded_by_user_id: UUID | None,
        organization_id: UUID | None,
        knowledge_base_id: UUID | None,
        visibility: str,
        access: dict[str, Any] | None,
        force_reimport: bool = False,
    ) -> DOfficeImportResult:
        normalized_id = str(id_vb).strip()
        if not normalized_id:
            raise ValueError("id_vb is required.")

        existing = await self._repository.get_document_by_external_id(
            external_source="doffice",
            external_id=normalized_id,
        )
        if existing is not None and not force_reimport:
            if existing.status == "indexed":
                return DOfficeImportResult(
                    document_id=existing.id,
                    id_vb=normalized_id,
                    title=existing.title,
                    character_count=len(existing.parsed_text or ""),
                    reused_existing=True,
                )
            raise DOfficeDuplicateDocumentError(
                f"DOffice document id_vb={normalized_id} already exists as document {existing.id}."
            )

        try:
            source = await self._fetch_source(normalized_id)
            parsed_text = self._document_text(source)
            title = self._document_title(source, normalized_id)
            parsed = MarkdownParser().parse(parsed_text.encode("utf-8"))

            document = await self._repository.create_document(
                title=title,
                source_type="doffice",
                status="parsed",
                uploaded_by_user_id=uploaded_by_user_id,
                organization_id=organization_id,
                knowledge_base_id=knowledge_base_id,
                visibility=visibility,
                access=access,
            )
            await self._repository.update_document_parsed_content(
                document,
                parsed_text=parsed_text,
                parsed_at=datetime.now(UTC),
                status="parsed",
            )
            await self._repository.update_document_metadata(
                document,
                {
                    "parser": "doffice_elasticsearch",
                    "external_source": "doffice",
                    "external_id": normalized_id,
                    "parsed_metadata": {
                        "parser": "doffice_elasticsearch",
                        "id_vb": normalized_id,
                        "ky_hieu": self._repair_text(source.get("ky_hieu")),
                        "trich_yeu": self._repair_text(source.get("trich_yeu")),
                        "ten_file": self._repair_text(source.get("ten_file")),
                        "duong_dan": self._repair_text(source.get("duong_dan")),
                        "ngay_vb": self._repair_text(source.get("ngay_vb")),
                        "noi_ban_hanh": self._repair_text(source.get("noi_ban_hanh")),
                        "nguoi_ky": self._repair_text(source.get("nguoi_ky")),
                        "elastic_url": self._elastic_url,
                    },
                    "parsed_elements": [
                        parsed_element_to_dict(element) for element in parsed.elements
                    ],
                    "doffice_source": self._compact_source_metadata(source),
                },
            )
            await self._repository.commit()
            return DOfficeImportResult(
                document_id=document.id,
                id_vb=normalized_id,
                title=title,
                character_count=len(parsed_text),
            )
        except (DOfficeDocumentNotFoundError, DOfficeDuplicateDocumentError, ValueError):
            await self._repository.rollback()
            raise
        except Exception as exc:
            await self._repository.rollback()
            raise DOfficeImportError(f"Failed to import DOffice document: {exc}") from exc

    async def _fetch_source(self, id_vb: str) -> dict[str, Any]:
        payload = {
            "query": {
                "bool": {
                    "must": [{"term": {"id_vb": id_vb}}],
                    "must_not": [],
                    "should": [],
                }
            },
            "from": 0,
            "size": 1,
            "sort": [],
            "aggs": {},
        }
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(self._elastic_url, json=payload)
            response.raise_for_status()
        hits = response.json().get("hits", {}).get("hits", [])
        if not hits:
            raise DOfficeDocumentNotFoundError(f"DOffice document id_vb={id_vb} was not found.")
        source = hits[0].get("_source")
        if not isinstance(source, dict):
            raise DOfficeImportError("Elasticsearch hit does not contain a valid _source.")
        return source

    def _document_text(self, source: dict[str, Any]) -> str:
        sections = [
            ("Ký hiệu", source.get("ky_hieu")),
            ("Trích yếu", source.get("trich_yeu")),
            ("Nơi ban hành", source.get("noi_ban_hanh")),
            ("Người ký", source.get("nguoi_ky")),
            ("Ngày văn bản", source.get("ngay_vb")),
            ("Tóm tắt", source.get("tom_tat")),
            ("Nội dung", source.get("noi_dung")),
        ]
        parts = [
            f"# Văn bản DOffice {self._repair_text(source.get('id_vb'))}".strip(),
        ]
        for label, value in sections:
            text = self._repair_text(value).strip()
            if not text:
                continue
            parts.append(f"\n## {label}\n\n{text}")
        parsed_text = "\n".join(parts).strip()
        if not parsed_text:
            raise ValueError("DOffice document has no text content.")
        return parsed_text

    def _document_title(self, source: dict[str, Any], id_vb: str) -> str:
        candidates = (
            source.get("trich_yeu"),
            source.get("ten_file"),
            source.get("ky_hieu"),
            f"DOffice {id_vb}",
        )
        for candidate in candidates:
            title = self._repair_text(candidate).strip()
            if title:
                return title[:255]
        return f"DOffice {id_vb}"

    def _compact_source_metadata(self, source: dict[str, Any]) -> dict[str, Any]:
        keys = [
            "id_vb",
            "ky_hieu",
            "trich_yeu",
            "id_dv_ban_hanh",
            "noi_ban_hanh",
            "nguoi_ky",
            "ten_file",
            "duong_dan",
            "ngay_vb",
            "ngay_tao",
            "ngay_capnhat",
            "type_ocr",
            "id_dt",
            "nam",
            "thang",
        ]
        return {
            key: self._repair_text(value) if isinstance(value, str) else value
            for key in keys
            if (value := source.get(key)) is not None
        }

    @classmethod
    def _repair_text(cls, value: Any) -> str:
        text = "" if value is None else str(value)
        if not cls._looks_mojibake(text):
            return text
        for encoding in ("cp1252", "latin-1"):
            try:
                return text.encode(encoding).decode("utf-8")
            except UnicodeError:
                continue
        return text

    @staticmethod
    def _looks_mojibake(text: str) -> bool:
        markers = ("Ã", "Ä", "áº", "á»", "â€", "Æ")
        return any(marker in text for marker in markers)
