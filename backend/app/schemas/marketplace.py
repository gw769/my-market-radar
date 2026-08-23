from typing import Literal

from pydantic import BaseModel, Field, field_validator

Platform = Literal["shopee", "lazada"]


class KeywordCreate(BaseModel):
    keyword: str = Field(min_length=2, max_length=200)
    platforms: list[Platform] = Field(default_factory=lambda: ["shopee", "lazada"])
    results_limit: int = Field(default=20, ge=10, le=40)
    tracking_enabled: bool = True
    daily_time: str = "20:00"
    timezone: str = "Asia/Kuala_Lumpur"

    @field_validator("keyword")
    @classmethod
    def clean_keyword(cls, value: str) -> str:
        value = " ".join(value.split())
        if not value:
            raise ValueError("关键词不能为空")
        return value

    @field_validator("platforms")
    @classmethod
    def unique_platforms(cls, value: list[Platform]) -> list[Platform]:
        if not value:
            raise ValueError("至少选择一个平台")
        return list(dict.fromkeys(value))

    @field_validator("daily_time")
    @classmethod
    def validate_time(cls, value: str) -> str:
        try:
            hour, minute = (int(part) for part in value.split(":"))
        except Exception as exc:
            raise ValueError("时间格式必须为 HH:MM") from exc
        if not 0 <= hour <= 23 or not 0 <= minute <= 59:
            raise ValueError("时间格式必须为 HH:MM")
        return f"{hour:02d}:{minute:02d}"


class KeywordUpdate(BaseModel):
    tracking_enabled: bool | None = None
    daily_time: str | None = None
    results_limit: int | None = Field(default=None, ge=10, le=40)

    @field_validator("daily_time")
    @classmethod
    def validate_time(cls, value: str | None) -> str | None:
        if value is None:
            return None
        hour, minute = (int(part) for part in value.split(":"))
        if not 0 <= hour <= 23 or not 0 <= minute <= 59:
            raise ValueError("时间格式必须为 HH:MM")
        return f"{hour:02d}:{minute:02d}"
