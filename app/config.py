from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    APP_NAME: str = "Order Fulfillment & Inventory Management Platform"
    ENV: str = "development"

    DATABASE_URL: str = "postgresql+psycopg://postgres:postgres@localhost:5432/order_fulfillment"

    JWT_SECRET_KEY: str = "change-me-in-production"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 180   # 3 hours
    REFRESH_TOKEN_EXPIRE_MINUTES: int = 180  # 3 hours (used instead of days)
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7       # kept for backward compat (not used)

    # Order-cancellation cutoff: customers may not cancel once the order has
    # reached or passed this status.
    CANCELLABLE_STATUSES: tuple = ("pending", "confirmed", "processing")

    LOW_STOCK_CHECK_ENABLED: bool = True


@lru_cache
def get_settings() -> Settings:
    return Settings()
