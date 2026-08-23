from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, field_validator

from app.core.config import get_settings

Platform = Literal["shopee", "lazada"]
settings = get_settings()


def _clean_time(value: str) -> str:
    try:
        parts = value.split(":")
        if len(parts) != 2:
            raise ValueError
        hour, minute = (int(part) for part in parts)
    except Exception as exc:
        raise ValueError("时间格式必须为 HH:MM") from exc
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError("时间格式必须为 HH:MM")
    return f"{hour:02d}:{minute:02d}"


def _clean_timezone(value: str) -> str:
    value = value.strip()
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError("无效时区") from exc
    return value


class KeywordCreate(BaseModel):
    keyword: str = Field(min_length=2, max_length=200)
    platforms: list[Platform] = Field(default_factory=lambda: ["shopee", "lazada"])
    results_limit: int = Field(default=settings.DEFAULT_RESULTS_LIMIT, ge=10, le=40)
    tracking_enabled: bool = True
    daily_time: str = settings.DEFAULT_DAILY_TIME
    timezone: str = settings.DEFAULT_TIMEZONE

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
        return _clean_time(value)

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        return _clean_timezone(value)


class KeywordUpdate(BaseModel):
    tracking_enabled: bool | None = None
    daily_time: str | None = None
    results_limit: int | None = Field(default=None, ge=10, le=40)
    platforms: list[Platform] | None = None
    timezone: str | None = None

    @field_validator("daily_time")
    @classmethod
    def validate_time(cls, value: str | None) -> str | None:
        return _clean_time(value) if value is not None else None

    @field_validator("platforms")
    @classmethod
    def unique_platforms(cls, value: list[Platform] | None) -> list[Platform] | None:
        if value is None:
            return None
        if not value:
            raise ValueError("至少选择一个平台")
        return list(dict.fromkeys(value))

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str | None) -> str | None:
        return _clean_timezone(value) if value is not None else None
