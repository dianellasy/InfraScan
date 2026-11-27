"""Cloudflare R2 (S3 API) media storage.

Configuration comes only from the process environment: `R2_BUCKET`,
`R2_ENDPOINT`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`. Credentials, signed
URLs, and object contents are never logged.
"""

from __future__ import annotations

import os
from contextlib import closing
from typing import BinaryIO

import boto3
from boto3.s3.transfer import TransferConfig
from botocore.config import Config
from botocore.exceptions import ClientError

# `get` is for JPEG-sized objects only; video goes through `download_to_file`.
MAX_GET_BYTES = 32 * 1024 * 1024
_CHUNK_BYTES = 1024 * 1024


class StoredObjectTooLarge(ValueError):
    """The stored object exceeds the caller's byte limit."""


class R2MediaStorageAdapter:
    def __init__(self) -> None:
        self._bucket = os.environ["R2_BUCKET"]
        self._s3 = boto3.client(
            "s3",
            endpoint_url=os.environ["R2_ENDPOINT"],
            aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
            aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
            region_name="auto",
            config=Config(connect_timeout=10, read_timeout=60, retries={"max_attempts": 3}),
        )
        self._transfer = TransferConfig(
            multipart_threshold=16 * 1024 * 1024,
            multipart_chunksize=16 * 1024 * 1024,
            max_concurrency=4,
            use_threads=True,
        )

    def put(self, data: bytes, object_key: str, content_type: str) -> None:
        self._s3.put_object(
            Bucket=self._bucket, Key=object_key, Body=data, ContentType=content_type
        )

    def put_file(self, source: BinaryIO, object_key: str, content_type: str) -> None:
        source.seek(0)
        self._s3.upload_fileobj(
            source,
            self._bucket,
            object_key,
            ExtraArgs={"ContentType": content_type},
            Config=self._transfer,
        )

    def get(self, key: str) -> bytes:
        response = self._s3.get_object(Bucket=self._bucket, Key=key)
        with closing(response["Body"]) as body:
            if response.get("ContentLength", 0) > MAX_GET_BYTES:
                raise StoredObjectTooLarge("Stored object exceeds the in-memory limit")
            data = body.read(MAX_GET_BYTES + 1)
        if len(data) > MAX_GET_BYTES:
            raise StoredObjectTooLarge("Stored object exceeds the in-memory limit")
        return data

    def download_to_file(self, key: str, target: BinaryIO, max_bytes: int) -> int:
        response = self._s3.get_object(Bucket=self._bucket, Key=key)
        total = 0
        with closing(response["Body"]) as body:
            if response.get("ContentLength", 0) > max_bytes:
                raise StoredObjectTooLarge("Stored object exceeds the allowed size")
            for chunk in body.iter_chunks(chunk_size=_CHUNK_BYTES):
                total += len(chunk)
                if total > max_bytes:
                    raise StoredObjectTooLarge("Stored object exceeds the allowed size")
                target.write(chunk)
        target.flush()
        return total

    def delete(self, key: str) -> None:
        self._s3.delete_object(Bucket=self._bucket, Key=key)

    def presign_put(
        self, key: str, content_type: str, content_length: int, expires_in: int
    ) -> str:
        """A URL the browser can PUT exactly `content_length` bytes of `content_type` to.

        Both headers are part of the signature, so R2 rejects a different
        type or size. The bucket needs a CORS rule allowing PUT from the
        frontend origin (see scripts/configure_r2_cors.py).
        """
        return self._s3.generate_presigned_url(
            "put_object",
            Params={
                "Bucket": self._bucket,
                "Key": key,
                "ContentType": content_type,
                "ContentLength": content_length,
            },
            ExpiresIn=expires_in,
            HttpMethod="PUT",
        )

    def size(self, key: str) -> int | None:
        """Size in bytes of a stored object, or None if it does not exist."""
        try:
            response = self._s3.head_object(Bucket=self._bucket, Key=key)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in {"404", "NoSuchKey", "NotFound"}:
                return None
            raise
        return int(response["ContentLength"])
