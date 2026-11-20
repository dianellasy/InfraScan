"""Count incoming bytes before the multipart parser consumes them."""
from starlette.formparsers import MultiPartException
from app.api.errors import error_response
from app.services.video import MAX_VIDEO_BYTES

MAX_REQUEST_BYTES = MAX_VIDEO_BYTES + 64 * 1024  # bounded multipart/metadata overhead


class UploadLimitMiddleware:
    def __init__(self, app, max_bytes=MAX_REQUEST_BYTES):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("path") != "/api/v1/inference/jobs" or scope["method"] != "POST":
            return await self.app(scope, receive, send)
        headers = dict(scope.get("headers", []))
        length = headers.get(b"content-length")
        if length and length.isdigit() and int(length) > self.max_bytes:
            return await error_response(413, "FILE_TOO_LARGE", "Upload exceeds the request size limit.", "file")(scope, receive, send)
        total = 0

        async def limited_receive():
            nonlocal total
            message = await receive()
            if message["type"] == "http.request":
                total += len(message.get("body", b""))
                if total > self.max_bytes:
                    scope.setdefault("state", {})["upload_too_large"] = True
                    # Starlette's parser closes its temporary files on this exception.
                    raise MultiPartException("Upload exceeds the request size limit.")
            return message

        await self.app(scope, limited_receive, send)
