from fastapi import APIRouter
from app.api import ai, auth, discovery, marketplace, trend

api_router = APIRouter()

api_router.include_router(auth.router)
api_router.include_router(ai.router)
api_router.include_router(marketplace.router)
api_router.include_router(discovery.router)
api_router.include_router(trend.router)

__all__ = ["api_router"]
