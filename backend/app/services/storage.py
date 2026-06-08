from __future__ import annotations

from functools import lru_cache
from typing import BinaryIO, Protocol

import anyio
from minio import Minio
from minio.error import S3Error

from app.core.config import settings


class StorageClient(Protocol):
    async def put_file(
        self,
        *,
        object_name: str,
        data: BinaryIO,
        length: int,
        content_type: str,
    ) -> str: ...

    async def get_file(self, *, object_name: str) -> bytes: ...

    async def delete_file(self, *, object_name: str) -> None: ...


class MinioStorageClient:
    def __init__(
        self,
        *,
        endpoint: str,
        access_key: str,
        secret_key: str,
        bucket_name: str,
        secure: bool,
    ) -> None:
        self._bucket_name = bucket_name
        self._client = Minio(
            endpoint,
            access_key=access_key,
            secret_key=secret_key,
            secure=secure,
        )

    async def put_file(
        self,
        *,
        object_name: str,
        data: BinaryIO,
        length: int,
        content_type: str,
    ) -> str:
        await anyio.to_thread.run_sync(self._ensure_bucket)
        data.seek(0)
        await anyio.to_thread.run_sync(
            lambda: self._client.put_object(
                self._bucket_name,
                object_name,
                data,
                length=length,
                content_type=content_type,
            ),
        )
        return object_name

    async def get_file(self, *, object_name: str) -> bytes:
        return await anyio.to_thread.run_sync(lambda: self._read_object(object_name))

    async def delete_file(self, *, object_name: str) -> None:
        await anyio.to_thread.run_sync(
            lambda: self._client.remove_object(self._bucket_name, object_name),
        )

    def _read_object(self, object_name: str) -> bytes:
        response = self._client.get_object(self._bucket_name, object_name)
        try:
            return response.read()
        finally:
            response.close()
            response.release_conn()

    def _ensure_bucket(self) -> None:
        if self._client.bucket_exists(self._bucket_name):
            return

        try:
            self._client.make_bucket(self._bucket_name)
        except S3Error as exc:
            if exc.code not in {"BucketAlreadyExists", "BucketAlreadyOwnedByYou"}:
                raise


@lru_cache
def get_storage_client() -> StorageClient:
    return MinioStorageClient(
        endpoint=settings.minio_endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
        bucket_name=settings.minio_bucket,
        secure=settings.minio_secure,
    )
