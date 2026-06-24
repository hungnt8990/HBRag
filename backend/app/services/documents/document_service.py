from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import anyio
from fastapi import UploadFile

from app.repositories.documents import DocumentRepository
from app.schemas.documents import DocumentUploadResponse
from app.services.documents.document_storage import StorageClient

ALLOWED_DOCUMENT_TYPES = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".txt": "text/plain",
    ".md": "text/markdown",
}


class UnsupportedDocumentTypeError(ValueError):
    pass


class EmptyDocumentUploadError(ValueError):
    pass


class DocumentUploadError(RuntimeError):
    pass

class DuplicateDocumentUploadError(ValueError):
    pass


class DocumentService:
    def __init__(
        self,
        *,
        repository: DocumentRepository,
        storage: StorageClient,
    ) -> None:
        self._repository = repository
        self._storage = storage

    async def upload_document(
        self,
        upload_file: UploadFile,
        *,
        uploaded_by_user_id: UUID | None = None,
        organization_id: UUID | None = None,
        knowledge_base_id: UUID | None = None,
        visibility: str = "organization",
        access: dict[str, Any] | None = None,
    ) -> DocumentUploadResponse:
        filename = self._clean_filename(upload_file.filename)
        extension = Path(filename).suffix.lower()
        if extension not in ALLOWED_DOCUMENT_TYPES:
            raise UnsupportedDocumentTypeError(
                "Unsupported file type. Supported types: PDF, DOCX, TXT, MD."
            )

        file_size = await self._get_file_size(upload_file)
        if file_size <= 0:
            raise EmptyDocumentUploadError("Uploaded file is empty.")

        find_duplicate = getattr(self._repository, "find_document_file_by_signature", None)
        if find_duplicate is not None:
            duplicate_file = await find_duplicate(filename=filename, file_size=file_size)
            if duplicate_file is not None:
                duplicate_document = getattr(duplicate_file, "document", None)
                duplicate_id = getattr(duplicate_document, "id", None) or getattr(
                    duplicate_file,
                    "document_id",
                    "unknown",
                )
                raise DuplicateDocumentUploadError(
                    f"Duplicate file already exists for document {duplicate_id}."
                )

        mime_type = upload_file.content_type or ALLOWED_DOCUMENT_TYPES[extension]
        title = Path(filename).stem or filename
        storage_path = ""
        uploaded_to_storage = False

        try:
            try:
                document = await self._repository.create_document(
                    title=title,
                    source_type=extension.removeprefix("."),
                    status="uploaded",
                    uploaded_by_user_id=uploaded_by_user_id,
                    organization_id=organization_id,
                    knowledge_base_id=knowledge_base_id,
                    visibility=visibility,
                    access=access,
                )
            except TypeError:
                document = await self._repository.create_document(
                    title=title,
                    source_type=extension.removeprefix("."),
                    status="uploaded",
                )
            storage_path = self._build_storage_path(document.id, filename)
            await upload_file.seek(0)
            await self._storage.put_file(
                object_name=storage_path,
                data=upload_file.file,
                length=file_size,
                content_type=mime_type,
            )
            uploaded_to_storage = True
            await self._repository.create_document_file(
                document_id=document.id,
                filename=filename,
                mime_type=mime_type,
                storage_path=storage_path,
                file_size=file_size,
            )
            await self._repository.commit()
        except Exception as exc:
            await self._repository.rollback()
            if uploaded_to_storage:
                try:
                    await self._storage.delete_file(object_name=storage_path)
                except Exception:
                    pass
            raise DocumentUploadError("Failed to upload document.") from exc

        return DocumentUploadResponse(
            document_id=document.id,
            filename=filename,
            status=document.status,
            storage_path=storage_path,
        )

    @staticmethod
    def _clean_filename(filename: str | None) -> str:
        cleaned = Path((filename or "").replace("\\", "/")).name.strip()
        if not cleaned:
            raise UnsupportedDocumentTypeError("Uploaded file must have a filename.")
        return cleaned

    @staticmethod
    def _build_storage_path(document_id: object, filename: str) -> str:
        extension = Path(filename).suffix.lower()
        object_id = uuid4()
        return f"documents/{document_id}/original/{object_id}{extension}"

    @staticmethod
    async def _get_file_size(upload_file: UploadFile) -> int:
        def get_size() -> int:
            file_obj = upload_file.file
            current_position = file_obj.tell()
            file_obj.seek(0, 2)
            size = file_obj.tell()
            file_obj.seek(current_position)
            return size

        return await anyio.to_thread.run_sync(get_size)
