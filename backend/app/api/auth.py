from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_db
from app.core.logging import logger
from app.core.security import (
    create_access_token,
    get_current_user,
    get_password_hash,
    verify_password,
)
from app.models.user import User
from app.schemas.user import UserCreate, UserLogin, UserResponse

router = APIRouter(prefix="/api/auth", tags=["认证"])
settings = get_settings()


@router.post("/register")
def register(user_data: UserCreate, db: Session = Depends(get_db)):
    if not settings.ALLOW_REGISTRATION:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="当前部署未开放自助注册",
        )

    logger.info("用户注册尝试: %s", user_data.email)

    user = db.query(User).filter(User.email == user_data.email).first()
    if user:
        return JSONResponse(status_code=400, content={"success": False, "message": "该邮箱已被注册"})

    user = db.query(User).filter(User.username == user_data.username).first()
    if user:
        return JSONResponse(status_code=400, content={"success": False, "message": "该用户名已被使用"})

    user = User(
        username=user_data.username,
        email=user_data.email,
        password_hash=get_password_hash(user_data.password),
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    access_token = create_access_token(data={"sub": user.id})
    logger.info("用户注册成功: %s", user.email)

    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user": UserResponse.model_validate(user).model_dump(),
    }


@router.post("/login")
def login(login_data: UserLogin, db: Session = Depends(get_db)):
    logger.info("登录尝试: %s", login_data.email)

    user = db.query(User).filter(User.email == login_data.email).first()

    if not user or not user.is_active or not verify_password(login_data.password, user.password_hash):
        return JSONResponse(status_code=401, content={"success": False, "message": "邮箱或密码错误"})

    access_token = create_access_token(data={"sub": user.id})
    logger.info("用户登录成功: %s", user.email)

    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user": UserResponse.model_validate(user).model_dump(),
    }


@router.get("/me")
def get_me(current_user: User = Depends(get_current_user)):
    return UserResponse.model_validate(current_user).model_dump()
