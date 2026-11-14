from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from app.schemas import APIError, APIErrorResponse


def error_response(
    status_code: int, code: str, message: str, field: str | None = None
) -> JSONResponse:
    payload = APIErrorResponse(
        error=APIError(code=code, message=message, field=field)
    )
    return JSONResponse(
        status_code=status_code,
        content=payload.model_dump(mode="json", exclude_none=True),
    )


async def _not_implemented_handler(
    request: Request, exc: NotImplementedError
) -> JSONResponse:
    return error_response(
        status.HTTP_501_NOT_IMPLEMENTED,
        "NOT_IMPLEMENTED",
        "This endpoint is not implemented yet.",
    )


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(NotImplementedError, _not_implemented_handler)
