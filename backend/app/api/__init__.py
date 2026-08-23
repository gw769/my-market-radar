from fastapi import APIRouter
from app.api import auth, discovery, marketplace

api_router = APIRouter()

api_router.include_router(auth.router)
api_router.include_router(marketplace.router)
api_router.include_router(discovery.router)

__all__ = ["api_router"]
