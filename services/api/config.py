# services/api/config.py
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    # CORS
    cors_origins: list[str] = ["*"]   # deve essere una LISTA

    # NOWPayments
    NOWPAYMENTS_API_KEY: str | None = None
    NOWPAYMENTS_BASE_URL: str = "https://api.nowpayments.io/v1"
    NOWPAYMENTS_IPN_SECRET: str | None = None

    class Config:
        env_prefix = ""       # prendi direttamente dalle env
        case_sensitive = False

settings = Settings()
