from pathlib import Path
from urllib.parse import quote_plus
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings


def _valid_daily_time(value: str) -> str:
    try:
        hour_text, minute_text = value.split(":")
        hour, minute = int(hour_text), int(minute_text)
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError("DEFAULT_DAILY_TIME 必须为 HH:MM") from exc
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError("DEFAULT_DAILY_TIME 必须为 HH:MM")
    return f"{hour:02d}:{minute:02d}"


def _valid_timezone(value: str) -> str:
    value = value.strip()
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError("DEFAULT_TIMEZONE 不是有效 IANA 时区") from exc
    return value


class Settings(BaseSettings):
    APP_NAME: str = "MY Marketplace Analyzer"
    APP_VERSION: str = "1.0.3"
    DEBUG: bool = False
    SECRET_KEY: str = "dev-secret-key-change-in-production"
    ALLOW_REGISTRATION: bool = False

    BOOTSTRAP_ADMIN_USERNAME: str = "admin"
    BOOTSTRAP_ADMIN_EMAIL: str = "admin@market.my"
    BOOTSTRAP_ADMIN_PASSWORD: str = ""

    DATA_DIR: str = ""
    DATABASE_TYPE: str = "sqlite"
    ALLOW_DATABASE_FALLBACK: bool = False
    MYSQL_HOST: str = ""
    MYSQL_PORT: int = Field(default=3306, ge=1, le=65535)
    MYSQL_USER: str = ""
    MYSQL_PASSWORD: str = ""
    MYSQL_DATABASE: str = "marketplace_ai"

    BROWSER_PROFILE_DIR: str = ""
    BROWSER_EXECUTABLE: str = ""
    BROWSER_MODE: str = "cdp"
    BROWSER_CDP_PORT: int = Field(default=9231, ge=1024, le=65535)
    BROWSER_START_TIMEOUT_SECONDS: float = Field(default=8.0, ge=1.0, le=60.0)
    BROWSER_HEADLESS_FALLBACK: bool = True
    EXTENSION_UPDATE_BASE_URL: str = "http://127.0.0.1:8011/browser-extension"
    COLLECTION_TIMEOUT_SECONDS: int = Field(default=60, ge=5, le=120)
    RUN_HEARTBEAT_SECONDS: int = Field(default=10, ge=3, le=60)
    RUN_STALE_AFTER_SECONDS: int = Field(default=240, ge=60, le=1800)
    DEFAULT_RESULTS_LIMIT: int = Field(default=20, ge=10, le=40)
    SEARCH_PAGES: int = Field(default=3, ge=1, le=5)
    DEFAULT_DAILY_TIME: str = "20:00"
    DEFAULT_TIMEZONE: str = "Asia/Kuala_Lumpur"

    LLM_PROVIDER: str = ""
    LLM_MODEL: str = "gpt-5.6-sol"
    LLM_API_KEY: str = ""
    LLM_BASE_URL: str = "https://api.openai.com"
    LLM_REASONING_EFFORT: str = "low"
    LLM_TIMEOUT_SECONDS: float = Field(default=45.0, ge=5.0, le=120.0)
    # Initial request plus at most five retries. Keep the upper bound explicit so a
    # degraded upstream cannot hold a marketplace worker forever.
    LLM_MAX_RETRIES: int = Field(default=5, ge=0, le=5)

    @field_validator("DATABASE_TYPE", mode="before")
    @classmethod
    def validate_database_type(cls, value: str) -> str:
        normalized = str(value).strip().lower()
        if normalized not in {"sqlite", "mysql"}:
            raise ValueError("DATABASE_TYPE 仅支持 sqlite 或 mysql")
        return normalized

    @field_validator("BROWSER_MODE", mode="before")
    @classmethod
    def validate_browser_mode(cls, value: str) -> str:
        normalized = str(value).strip().lower()
        if normalized not in {"cdp", "extension"}:
            raise ValueError("BROWSER_MODE 仅支持 cdp 或 extension")
        return normalized

    @field_validator("DEFAULT_DAILY_TIME")
    @classmethod
    def validate_default_daily_time(cls, value: str) -> str:
        return _valid_daily_time(value)

    @field_validator("DEFAULT_TIMEZONE")
    @classmethod
    def validate_default_timezone(cls, value: str) -> str:
        return _valid_timezone(value)

    @property
    def data_path(self) -> Path:
        path = Path(self.DATA_DIR) if self.DATA_DIR else Path(__file__).parents[2] / "data"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def browser_profile_path(self) -> Path:
        path = Path(self.BROWSER_PROFILE_DIR) if self.BROWSER_PROFILE_DIR else self.data_path / "browser-profile"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def DATABASE_URL(self) -> str:
        if self.DATABASE_TYPE == "mysql":
            user = quote_plus(self.MYSQL_USER)
            password = quote_plus(self.MYSQL_PASSWORD)
            return (
                f"mysql+pymysql://{user}:{password}"
                f"@{self.MYSQL_HOST or 'localhost'}:{self.MYSQL_PORT}/{self.MYSQL_DATABASE}"
            )
        return f"sqlite:///{self.data_path / 'marketplace_ai.db'}"

    class Config:
        env_file = str(Path(__file__).parents[3] / ".env")
        case_sensitive = True
        extra = "ignore"


def get_settings() -> Settings:
    return Settings()
