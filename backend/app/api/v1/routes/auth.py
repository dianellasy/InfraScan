from fastapi import APIRouter

from app.api.auth import CurrentUser
from app.schemas import APIErrorResponse, CurrentUserDTO

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get(
    "/me",
    response_model=CurrentUserDTO,
    summary="The authenticated caller's Supabase user ID and InfraScan role",
    responses={
        401: {"model": APIErrorResponse, "description": "Missing, invalid, or expired token"},
        503: {"model": APIErrorResponse, "description": "AUTHENTICATION_UNAVAILABLE"},
    },
)
def get_current_user(user: CurrentUser) -> CurrentUserDTO:
    """`role` comes from the token's signed `app_metadata`; missing or unknown roles
    are reported as `citizen`. Role changes apply after the session's token refreshes."""
    return CurrentUserDTO(
        id=user.id, role=user.role, municipality_id=user.municipality_id
    )
