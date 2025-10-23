# services/api/db.py
import os
from datetime import datetime
from sqlalchemy import (
    create_engine, MetaData, Table, Column,
    String, Float, DateTime
)
from sqlalchemy.orm import sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL env var is missing")

# Connessione SQLAlchemy
engine = create_engine(DATABASE_URL, pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False, future=True)
metadata = MetaData()

# Tabella ordini OTC / off-ramp
orders = Table(
    "orders", metadata,
    Column("id", String, primary_key=True),
    Column("token_symbol", String, nullable=False),
    Column("amount_tokens", Float, nullable=False),
    Column("price_eur", Float, nullable=False),
    Column("amount_eur", Float, nullable=False),
    Column("iban", String, nullable=True),
    Column("beneficiary_name", String, nullable=True),
    Column("status", String, nullable=False, default="quoted"),
    Column("changenow_tx_id", String, nullable=True),
    Column("nowpayments_payout_id", String, nullable=True),
    Column("created_at", DateTime, default=datetime.utcnow),
    Column("updated_at", DateTime, default=datetime.utcnow, onupdate=datetime.utcnow),
)

def get_db():
    """
    Dependency FastAPI per ottenere una sessione SQLAlchemy.
    Gestisce commit/rollback automaticamente.
    """
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
