from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from pydantic import ValidationError

from app.api.dependencies import get_service
from app.schemas import InferenceJobAccepted, InferenceMetadata
from app.services.contracts import InfraScanService

router = APIRouter(prefix="/inference", tags=["inference"])


@router.post(
    "/jobs",
    response_model=InferenceJobAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Submit media for road-damage inference",
)
async def submit_inference(
    files: Annotated[list[UploadFile], File(description="Images or videos to analyze")],
    metadata: Annotated[str, Form(description="JSON-encoded inference metadata")],
    service: Annotated[InfraScanService, Depends(get_service)],
) -> InferenceJobAccepted:
    try:
        parsed_metadata = InferenceMetadata.model_validate_json(metadata)
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=exc.errors(),
        ) from exc

    return await service.submit_inference(files, parsed_metadata)
