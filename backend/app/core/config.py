from pathlib import Path
from urllib.parse import quote_plus

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    APP_NAME: str = "MY Marketplace Analyzer"
    APP_VERSION: str = "1.0.1"
    DEBUG: bool = False
    SECRET_KEY: str = "dev-secret-key-change-in-production"

    DATA_DIR: str = ""
    DATABASE_TYPE: str = "sqlite"
    MYSQL_HOST: str = ""
    MYSQL_PORT: int = 3306
    MYSQL_USER: str = ""
    MYSQL_PASSWORD: str = ""
    MYSQL_DATABASE: str = "marketplace_ai"

    BROWSER_PROFILE_DIR: str = ""
    BROWSER_EXECUTABLE: str = ""
    BROWSER_CDP_PORT: int = 9223
    BROWSER_START_TIMEOUT_SECONDS: float = 8.0
    BROWSER_HEADLESS_FALLBACK: bool = True
    COLLECTION_TIMEOUT_SECONDS: int = 45
    DEFAULT_RESULTS_LIMIT: int = 20
    DEFAULT_DAILY_TIME: str = "20:00"
    DEFAULT_TIMEZONE: str = "Asia/Kuala_Lumpur"

    LLM_PROVIDER: str = ""
    LLM_MODEL: str = "deepseek-chat"
    LLM_API_KEY: str = ""
    LLM_BASE_URL: str = "https://api.deepseek.com"

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
        if self.DATABASE_TYPE.lower() == "mysql":
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
