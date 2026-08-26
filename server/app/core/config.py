from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")
    APP_NAME: str = "资源智能调度平台"
    ENVIRONMENT: str = "development"
    DATABASE_URL: str = "mysql+pymysql://root:password@127.0.0.1:3306/cro_scheduler"
    SECRET_KEY: str | None = None
    INITIAL_ADMIN_PASSWORD: str | None = None
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 480
    CORS_ORIGINS: str = "http://127.0.0.1:5173,http://localhost:5173,http://127.0.0.1:8000,http://localhost:8000"
    MAX_REQUEST_BODY_BYTES: int = 1_048_576
    AUTO_CREATE_SCHEMA: bool = True
    WECOM_OAUTH_CALLBACK_URL: str | None = None
    WECOM_OAUTH_STATE_EXPIRE_SECONDS: int = 300

    FROZEN_DAYS: int = 3
    CONFIRMED_DAYS: int = 14
    STATS_WINDOW_DAYS: int = 7
    HOURS_PER_DAY: int = 24
    PERCENT_SCALE: float = 100.0
    BOTTLENECK_BUFFER_RATE: float = 0.85
    NON_BOTTLENECK_BUFFER_RATE: float = 0.95
    SOLVER_TIMEOUT_SECONDS: int = 60
    DEVIATION_THRESHOLD_HOURS: int = 2
    DEVIATION_RATIO_THRESHOLD: float = 1.5

@lru_cache()
def get_settings():
    return Settings()
