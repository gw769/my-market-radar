from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.marketplace import TrackedKeyword
from app.models.user import User
from app.services.marketplace.trend import build_keyword_trend

router = APIRouter(prefix="/api/trends", tags=["Malaysia marketplace trends"])


@router.get("/keywords/{keyword_id}")
def keyword_trend(
    keyword_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    keyword = (
        db.query(TrackedKeyword)
        .filter(TrackedKeyword.id == keyword_id, TrackedKeyword.user_id == current_user.id)
        .first()
    )
    if not keyword:
        raise HTTPException(status_code=404, detail="关键词不存在")
    return {"success": True, "data": build_keyword_trend(db, keyword.id)}
