from contextlib import closing
import os
from typing import BinaryIO

import boto3
from boto3.s3.transfer import TransferConfig
from botocore.config import Config


class R2MediaStorageAdapter:
    def __init__(self) -> None:
        self._bucket = os.environ["R2_BUCKET"]
        self._s3 = boto3.client(
            "s3", endpoint_url=os.environ["R2_ENDPOINT"],
            aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
            aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
            region_name="auto",
            config=Config(connect_timeout=10, read_timeout=60, retries={"max_attempts": 3}),
        )
        self._transfer = TransferConfig(multipart_threshold=8 * 1024 * 1024,
                                       multipart_chunksize=8 * 1024 * 1024,
                                       max_concurrency=2)

    def put(self, data: bytes, object_key: str, content_type: str) -> None:
        self._s3.put_object(Bucket=self._bucket, Key=object_key, Body=data, ContentType=content_type)

    def put_file(self, source: BinaryIO, key: str, content_type: str) -> None:
        source.seek(0)
        self._s3.upload_fileobj(source, self._bucket, key,
                                ExtraArgs={"ContentType": content_type}, Config=self._transfer)

    def get(self, key: str) -> bytes:
        response = self._s3.get_object(Bucket=self._bucket, Key=key)
        with closing(response["Body"]) as body:
            return body.read()

    def download_to(self, key: str, target: BinaryIO, max_bytes: int) -> None:
        response = self._s3.get_object(Bucket=self._bucket, Key=key)
        with closing(response["Body"]) as body:
            if response.get("ContentLength", 0) > max_bytes:
                raise ValueError("Stored media exceeds the allowed size")
            total = 0
            for chunk in body.iter_chunks(chunk_size=1024 * 1024):
                total += len(chunk)
                if total > max_bytes:
                    raise ValueError("Stored media exceeds the allowed size")
                target.write(chunk)
        target.flush()

    def delete(self, key: str) -> None:
        self._s3.delete_object(Bucket=self._bucket, Key=key)
