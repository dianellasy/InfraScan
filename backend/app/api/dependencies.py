from fastapi import HTTPException, Request, status

from app.services.contracts import InfraScanService


def get_service(request: Request) -> InfraScanService:
    service = getattr(request.app.state, "infrascan_service", None)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="InfraScan service is not configured",
        )
    return service
