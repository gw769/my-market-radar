from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

from app.core.security import get_current_user
from app.models.user import User
from app.services.marketplace.ai import MarketplaceAIError, ai_status, translate_keyword


router = APIRouter(prefix="/api/ai", tags=["Marketplace AI"])


class KeywordTranslationRequest(BaseModel):
    keyword: str = Field(min_length=2, max_length=200)

    @field_validator("keyword")
    @classmethod
    def clean_keyword(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if not cleaned:
            raise ValueError("关键词不能为空")
        return cleaned


@router.get("/status")
def get_ai_status(current_user: User = Depends(get_current_user)):
    return {"success": True, "data": ai_status()}


@router.post("/translate-keyword")
def translate_marketplace_keyword(
    payload: KeywordTranslationRequest,
    current_user: User = Depends(get_current_user),
):
    try:
        localization = translate_keyword(payload.keyword)
    except MarketplaceAIError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {
        "success": True,
        "data": {
            **localization,
            "score_authority": "deterministic_rules",
        },
    }

