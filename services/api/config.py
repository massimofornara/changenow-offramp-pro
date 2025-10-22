from pydantic_settings import BaseSettings
from pydantic import AnyUrl, Field
from typing import List

class Settings(BaseSettings):
    ENV: str = "development"
    PORT: int = 10000
    ALLOWED_ORIGINS: str = "*"

    DATABASE_URL: AnyUrl

    OTC_DEFAULT_TOKEN_SYMBOL: str = "NENO"
    OTC_DEFAULT_PRICE_EUR: float = 5000.0
    OTC_DEFAULT_AVAILABLE_AMOUNT: float = 1_000_000.0
    REFERRER_DOMAIN: str = "neonoble.eu"

    CHANGENOW_BASE_URL: AnyUrl = "https://api.changenow.io/v2"
    CHANGENOW_API_KEY: str
    CHANGENOW_REF_ID: str | None = None
    CHANGENOW_PUBLIC_SELL_URL: AnyUrl = "https://changenow.io/sell"

    NOWPAYMENTS_BASE_URL: AnyUrl = "https://api.nowpayments.io/v1"
    NOWPAYMENTS_API_KEY: str
    NOWPAYMENTS_IPN_SECRET: str

    WEBHOOK_BASE: AnyUrl

    class Config:
        env_file = ".env"
        extra = "ignore"

    def cors_origins(self) -> List[str]:
        raw = self.ALLOWED_ORIGINS or "*"
        if raw.strip() == "*":
            return ["*"]
        return [s.strip() for s in raw.split(",") if s.strip()]

settings = Settings()  # type: ignore
