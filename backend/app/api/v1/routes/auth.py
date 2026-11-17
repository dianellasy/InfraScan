from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.auth import AuthenticatedUser, require_authenticated

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/me")
async def get_current_user(
    user: Annotated[AuthenticatedUser, Depends(require_authenticated)],
) -> dict[str, str | None]:
    return {
        "id": str(user.id),
        "role": user.role,
        "municipality_id": user.municipality_id,
    }
