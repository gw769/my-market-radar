from fastapi import APIRouter
from app.api import auth, discovery, marketplace, trend

api_router = APIRouter()

api_router.include_router(auth.router)
api_router.include_router(marketplace.router)
api_router.include_router(discovery.router)
api_router.include_router(trend.router)

__all__ = ["api_router"]
