# services/api/config.py
import os

def _split_env(name: str, default: str = "*") -> list[str]:
    """
    Restituisce una lista a partire da una env separata da virgole.
    Esempio: "https://neonoble.it,https://blkpanthcoin.world" -> ["https://neonoble.it","https://blkpanthcoin.world"]
    """
    val = os.getenv(name, default)
    # consenti anche il valore singolo "*" (CORS aperto)
    if val.strip() == "*":
        return ["*"]
    return [s.strip() for s in val.split(",") if s.strip()]

class Settings:
    # App/infra
    ENV = os.getenv("ENV", "production")
    DATABASE_URL = os.getenv("DATABASE_URL", "")

    # CORS
    # Usa ALLOWED_ORIGINS come nel tuo .env.example
    ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "*")

    # ChangeNOW
    CHANGENOW_BASE_URL = os.getenv("CHANGENOW_BASE_URL", "https://api.changenow.io/v2")
    CHANGENOW_API_KEY = os.getenv("CHANGENOW_API_KEY", "")
    CHANGENOW_REF_ID = os.getenv("CHANGENOW_REF_ID", "")
    CHANGENOW_PUBLIC_SELL_URL = os.getenv("CHANGENOW_PUBLIC_SELL_URL", "https://changenow.io/sell")

    # NOWPayments
    NOWPAYMENTS_BASE_URL = os.getenv("NOWPAYMENTS_BASE_URL", "https://api.nowpayments.io/v1")
    NOWPAYMENTS_API_KEY = os.getenv("NOWPAYMENTS_API_KEY", "")
    NOWPAYMENTS_IPN_SECRET = os.getenv("NOWPAYMENTS_IPN_SECRET", "")

    # Varie
    WEBHOOK_BASE = os.getenv("WEBHOOK_BASE", "")
    REFERRER_DOMAIN = os.getenv("REFERRER_DOMAIN", "")

    # Proprietà per main.py
    @property
    def cors_origins(self) -> list[str]:
        return _split_env("ALLOWED_ORIGINS", self.ALLOWED_ORIGINS)

settings = Settings()
