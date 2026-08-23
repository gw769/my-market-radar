from pydantic import BaseModel
from typing import Optional, Any


class ApiResponse(BaseModel):
    success: bool = True
    message: str = "操作成功"
    data: Optional[Any] = None


class ErrorResponse(BaseModel):
    success: bool = False
    message: str
    detail: Optional[str] = None
