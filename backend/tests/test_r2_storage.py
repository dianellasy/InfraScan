"""R2 adapter behavior against an in-memory fake S3 client (never real R2)."""

from __future__ import annotations

import io

import pytest

from app.adapters import r2_storage
from app.adapters.r2_storage import R2MediaStorageAdapter, StoredObjectTooLarge


class FakeBody:
    def __init__(self, data: bytes) -> None:
        self._stream = io.BytesIO(data)
        self.closed = False

    def read(self, size: int = -1) -> bytes:
        return self._stream.read(size)

    def iter_chunks(self, chunk_size: int):
        while chunk := self._stream.read(chunk_size):
            yield chunk

    def close(self) -> None:
        self.closed = True


class FakeS3:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.uploads: list[dict] = []
        self.bodies: list[FakeBody] = []
        self.report_length = True

    def put_object(self, Bucket, Key, Body, ContentType):
        self.objects[Key] = Body

    def upload_fileobj(self, source, bucket, key, ExtraArgs, Config):
        self.uploads.append({"bucket": bucket, "key": key, "extra": ExtraArgs, "config": Config})
        self.objects[key] = source.read()

    def get_object(self, Bucket, Key):
        body = FakeBody(self.objects[Key])
        self.bodies.append(body)
        response = {"Body": body}
        if self.report_length:
            response["ContentLength"] = len(self.objects[Key])
        return response

    def delete_object(self, Bucket, Key):
        self.objects.pop(Key, None)


@pytest.fixture
def s3(monkeypatch: pytest.MonkeyPatch) -> FakeS3:
    fake = FakeS3()
    for name, value in {
        "R2_BUCKET": "test-bucket",
        "R2_ENDPOINT": "https://example.invalid",
        "R2_ACCESS_KEY_ID": "placeholder",
        "R2_SECRET_ACCESS_KEY": "placeholder",
    }.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(r2_storage.boto3, "client", lambda *args, **kwargs: fake)
    return fake


def test_put_file_streams_with_multipart_config_from_the_start(s3: FakeS3) -> None:
    source = io.BytesIO(b"mp4-bytes")
    source.seek(4)
    R2MediaStorageAdapter().put_file(source, "input/x.mp4", "video/mp4")
    [upload] = s3.uploads
    assert upload["bucket"] == "test-bucket"
    assert upload["extra"] == {"ContentType": "video/mp4"}
    assert upload["config"].multipart_chunksize == 16 * 1024 * 1024
    assert s3.objects["input/x.mp4"] == b"mp4-bytes"


@pytest.mark.parametrize("report_length", [True, False])
def test_download_to_file_is_capped_with_or_without_content_length(
    s3: FakeS3, report_length: bool
) -> None:
    s3.report_length = report_length
    s3.objects["input/x.mp4"] = b"x" * 5000
    adapter = R2MediaStorageAdapter()
    target = io.BytesIO()
    assert adapter.download_to_file("input/x.mp4", target, 5000) == 5000
    assert target.getvalue() == b"x" * 5000
    with pytest.raises(StoredObjectTooLarge):
        adapter.download_to_file("input/x.mp4", io.BytesIO(), 4999)
    assert all(body.closed for body in s3.bodies)


def test_get_is_limited_to_image_sized_objects(s3: FakeS3, monkeypatch) -> None:
    monkeypatch.setattr(r2_storage, "MAX_GET_BYTES", 10)
    s3.objects["evidence/a.jpg"] = b"0123456789"
    s3.objects["evidence/b.jpg"] = b"0123456789A"
    adapter = R2MediaStorageAdapter()
    assert adapter.get("evidence/a.jpg") == b"0123456789"
    s3.report_length = False
    with pytest.raises(StoredObjectTooLarge):
        adapter.get("evidence/b.jpg")


def test_missing_configuration_fails_at_construction(monkeypatch) -> None:
    monkeypatch.delenv("R2_BUCKET", raising=False)
    with pytest.raises(KeyError):
        R2MediaStorageAdapter()
