from fastapi import APIRouter
from app.api import auth, marketplace

api_router = APIRouter()

api_router.include_router(auth.router)
api_router.include_router(marketplace.router)

__all__ = ["api_router"]
