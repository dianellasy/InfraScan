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
