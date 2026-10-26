from fastapi import APIRouter

from app.api.v1.routes.analytics import router as analytics_router
from app.api.v1.routes.detections import router as detections_router
from app.api.v1.routes.inference import router as inference_router
from app.api.v1.routes.issues import router as issues_router
from app.api.v1.routes.jobs import router as jobs_router
from app.api.v1.routes.roads import router as roads_router

api_router = APIRouter()
api_router.include_router(inference_router)
api_router.include_router(issues_router)
api_router.include_router(jobs_router)
api_router.include_router(roads_router)
api_router.include_router(detections_router)
api_router.include_router(analytics_router)
