import os

class Settings:
    NOWPAYMENTS_API_KEY = os.getenv("NOWPAYMENTS_API_KEY", "")
    NOWPAYMENTS_IPN_SECRET = os.getenv("NOWPAYMENTS_IPN_SECRET", "")
    DATABASE_URL = os.getenv("DATABASE_URL", "")
    ENV = os.getenv("ENV", "production")

settings = Settings()
