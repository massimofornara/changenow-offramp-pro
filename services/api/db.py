from __future__ import annotations
from sqlalchemy import create_engine, MetaData, Table, Column, String, Float, DateTime, Integer, JSON, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import sessionmaker
from datetime import datetime
import uuid
from .config import settings

engine = create_engine(str(settings.DATABASE_URL), pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
metadata = MetaData()

otc_listings = Table(
    "otc_listings",
    metadata,
    Column("token_symbol", String, primary_key=True),
    Column("price_eur", Float, nullable=False),
    Column("available_amount", Float, nullable=False),
    Column("updated_at", DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP")),
)

orders = Table(
    "orders",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
    Column("token_symbol", String, nullable=False),
    Column("amount_tokens", Float, nullable=False),
    Column("price_eur", Float, nullable=True),
    Column("amount_eur", Float, nullable=True),
    Column("iban", String, nullable=True),
    Column("beneficiary_name", String, nullable=True),
    Column("status", String, nullable=False, default="queued"),
    Column("changenow_tx_id", String, nullable=True),
    Column("nowpayments_payout_id", String, nullable=True),
    Column("redirect_url", String, nullable=True),
    Column("logs", JSON, nullable=True),
    Column("created_at", DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP")),
    Column("updated_at", DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP")),
)

def init_db():
    metadata.create_all(engine)
    # Seed OTC listing if empty
    with engine.begin() as conn:
        res = conn.execute(text("SELECT COUNT(*) FROM otc_listings"))
        count = res.scalar() or 0
        if count == 0:
            conn.execute(
                otc_listings.insert().values(
                    token_symbol=settings.OTC_DEFAULT_TOKEN_SYMBOL,
                    price_eur=settings.OTC_DEFAULT_PRICE_EUR,
                    available_amount=settings.OTC_DEFAULT_AVAILABLE_AMOUNT,
                    updated_at=datetime.utcnow(),
                )
            )
