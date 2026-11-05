"""S3 access for document files.

The database stores document METADATA (name, size, type, key); the bytes live
in S3 under `storage_key`. Keeping files out of PostgreSQL keeps the database
small and its backups fast, and files survive replacing the app server.

boto3 is synchronous. Every call below goes through `run_in_threadpool`, so a
slow upload never freezes the event loop (the same rule as argon2 hashing).
"""

import logging
from collections.abc import AsyncIterator, Callable, Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from starlette.concurrency import iterate_in_threadpool, run_in_threadpool

from project_dashboard.core.config import Settings

if TYPE_CHECKING:
    from types_boto3_s3 import S3Client

logger = logging.getLogger(__name__)

CHUNK_SIZE = 64 * 1024
S3_DELETE_BATCH = 1000  # S3's maximum keys per DeleteObjects request


class StorageError(Exception):
    """S3 is unreachable or refused the request."""


@dataclass(frozen=True)
class StoredObject:
    """A downloaded object: an async stream of chunks plus its size."""

    chunks: AsyncIterator[bytes]
    size: int


class ObjectStorage(Protocol):
    """What the services need from a storage backend (makes it replaceable in tests)."""

    async def put(self, key: str, data: bytes, content_type: str) -> None: ...
    async def get(self, key: str) -> StoredObject: ...
    async def delete(self, keys: Iterable[str]) -> None: ...


class S3Storage:
    def __init__(self, settings: Settings) -> None:
        self.bucket = settings.s3_bucket
        self.region = settings.aws_region
        secret = settings.aws_secret_access_key
        self._client: S3Client = boto3.client(
            "s3",
            region_name=settings.aws_region,
            endpoint_url=settings.s3_endpoint_url,
            # None = boto3's default chain (environment, then the IAM role).
            aws_access_key_id=settings.aws_access_key_id,
            aws_secret_access_key=secret.get_secret_value() if secret else None,
            # Fail fast instead of hanging a request for a minute.
            config=Config(connect_timeout=5, read_timeout=30, retries={"max_attempts": 3}),
        )

    async def ensure_bucket(self) -> None:
        """Create the bucket if missing - only for the local emulator."""

        def create() -> None:
            try:
                self._client.head_bucket(Bucket=self.bucket)
            except ClientError:
                if self.region == "us-east-1":  # the one region that rejects a constraint
                    self._client.create_bucket(Bucket=self.bucket)
                else:
                    self._client.create_bucket(
                        Bucket=self.bucket,
                        CreateBucketConfiguration={"LocationConstraint": self.region},  # type: ignore[typeddict-item]
                    )

        await self._run(create)

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        def put_object() -> None:
            self._client.put_object(
                Bucket=self.bucket, Key=key, Body=data, ContentType=content_type
            )

        await self._run(put_object)

    async def get(self, key: str) -> StoredObject:
        response = await self._run(lambda: self._client.get_object(Bucket=self.bucket, Key=key))
        # iter_chunks() is a blocking generator; iterate_in_threadpool pulls
        # each chunk in a worker thread, so streaming a big file stays async.
        chunks = iterate_in_threadpool(response["Body"].iter_chunks(CHUNK_SIZE))
        return StoredObject(chunks=chunks, size=response["ContentLength"])

    async def delete(self, keys: Iterable[str]) -> None:
        key_list = list(keys)
        for start in range(0, len(key_list), S3_DELETE_BATCH):
            batch = key_list[start : start + S3_DELETE_BATCH]

            def delete_batch(batch: list[str] = batch) -> None:
                self._client.delete_objects(
                    Bucket=self.bucket,
                    Delete={"Objects": [{"Key": k} for k in batch], "Quiet": True},
                )

            await self._run(delete_batch)

    async def _run[T](self, call: Callable[[], T]) -> T:
        try:
            return await run_in_threadpool(call)
        except (BotoCoreError, ClientError) as exc:
            logger.exception("S3 request failed")
            raise StorageError(str(exc)) from exc
