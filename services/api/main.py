import os
import hmac
import hashlib
import json
import logging
from datetime import datetime
from typing import Optional

import requests
from fastapi import FastAPI, HTTPException, Path, Request, Header, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, Text, UniqueConstraint
from sqlalchemy.orm import sessionmaker, declarative_base, Session

# -------------------------------------
# App & Config
# -------------------------------------
app = FastAPI(title="ChangeNOW Offramp Pro", version="4.2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./offramp.db")
NP_IPN_SECRET = os.getenv("NP_IPN_SECRET", "demo_secret")
NP_API_KEY = os.getenv("NP_API_KEY", "")
NP_BASE_URL = os.getenv("NP_BASE_URL", "https://api.nowpayments.io")  # override se serve

logging.basicConfig(
    level=logging.INFO,
    format='{"ts":"%(asctime)s","level":"%(levelname)s","msg":"%(message)s"}',
)
log = logging.getLogger("offramp")

# -------------------------------------
# Database setup
# -------------------------------------
engine = create_engine(
    DATABASE_URL,
    connect_args={} if not DATABASE_URL.startswith("sqlite") else {"check_same_thread": False},
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db() -> Session:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# -------------------------------------
# SQLAlchemy Models
# -------------------------------------
class Price(Base):
    __tablename__ = "prices"
    id = Column(Integer, primary_key=True)
    token_symbol = Column(String(64), index=True, unique=True, nullable=False)
    price_eur = Column(Float, nullable=False)
    available_amount = Column(Float, default=0)
    updated_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class Order(Base):
    __tablename__ = "orders"
    id = Column(Integer, primary_key=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    token_symbol = Column(String(64), nullable=False)
    amount_tokens = Column(Float, nullable=False)
    price_eur = Column(Float, nullable=False)
    eur_amount = Column(Float, nullable=False)
    payout_channel = Column(String(32), nullable=False)
    wallet_address = Column(Text)
    crypto_asset = Column(String(32))
    crypto_network = Column(String(32))
    notes = Column(Text)
    status = Column(String(32), default="created", nullable=False)
    payout_txid = Column(String(128))  # id/txid del provider


class WebhookEvent(Base):
    __tablename__ = "webhook_events"
    id = Column(Integer, primary_key=True)
    provider = Column(String(64), nullable=False)
    signature = Column(String(256), nullable=False)
    body_hash = Column(String(128), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    __table_args__ = (
        UniqueConstraint("provider", "signature", name="uq_provider_signature"),
        UniqueConstraint("provider", "body_hash", name="uq_provider_body"),
    )


Base.metadata.create_all(bind=engine)

# -------------------------------------
# Pydantic Schemas
# -------------------------------------
class SetPriceIn(BaseModel):
    token_symbol: str
    price_eur: float
    available_amount: Optional[float] = None


class CreateOrderIn(BaseModel):
    token_symbol: str
    amount_tokens: float
    price_eur: float
    payout_channel: str
    wallet_address: Optional[str] = None
    crypto_asset: Optional[str] = None
    crypto_network: Optional[str] = None
    notes: Optional[str] = None


class PayoutIn(BaseModel):
    method: str = ""                # "CRYPTO", "SEPA", ecc.
    wallet_address: Optional[str] = None
    crypto_asset: Optional[str] = None
    crypto_network: Optional[str] = None
    crypto_amount: Optional[float] = None   # se assente, calcoliamo da EUR
    slippage_bps: int = Field(default=0, ge=0, le=2000)  # 0..2000 bps (0..20%)


# -------------------------------------
# Utils
# -------------------------------------
def verify_nowpayments_signature(raw_body: bytes, signature: str) -> bool:
    if not signature:
        return False
    calc = hmac.new(NP_IPN_SECRET.encode(), msg=raw_body, digestmod=hashlib.sha512).hexdigest()
    return hmac.compare_digest(calc.lower(), signature.lower())


def build_np_currency(asset: str, network: str) -> str:
    """Converte (USDT, TRC20) -> 'USDTTRC20' (convenzione NOWPayments)."""
    return f"{(asset or '').upper()}{(network or '').upper()}"


def np_headers() -> dict:
    if not NP_API_KEY:
        raise HTTPException(status_code=500, detail="NOWPayments API key not configured")
    return {"x-api-key": NP_API_KEY, "Content-Type": "application/json"}


def nowpayments_convert_eur_to_crypto(eur_amount: float, currency: str) -> float:
    """
    Tenta la conversione EUR -> currency usando API NOWPayments.
    Usa 2 endpoint compatibili e ritorna la quantità crypto.
    """
    # Tentativo 1: /v1/rate
    try:
        url = f"{NP_BASE_URL}/v1/rate"
        params = {"currency_from": "EUR", "currency_to": currency, "amount": eur_amount}
        r = requests.get(url, params=params, headers=np_headers(), timeout=15)
        if r.status_code == 200:
            data = r.json()
            for key in ("estimated_amount", "amount", "result", "out_amount"):
                if key in data:
                    return float(data[key])
    except Exception as e:
        log.warning(f"np rate v1/rate failed: {e}")

    # Tentativo 2: /v1/estimate
    try:
        url = f"{NP_BASE_URL}/v1/estimate"
        params = {"amount": eur_amount, "currency_from": "eur", "currency_to": currency}
        r = requests.get(url, params=params, headers=np_headers(), timeout=15)
        if r.status_code == 200:
            data = r.json()
            for key in ("estimated_amount", "amount", "result", "out_amount"):
                if key in data:
                    return float(data[key])
    except Exception as e:
        log.warning(f"np estimate v1/estimate failed: {e}")

    raise HTTPException(status_code=502, detail="Cannot fetch EUR→crypto rate from NOWPayments")


def nowpayments_create_payout(*, address: str, amount: float, currency: str) -> dict:
    """
    Crea un payout su NOWPayments (mass-payout con singolo withdrawal).
    """
    url = f"{NP_BASE_URL}/v1/payouts"
    headers = np_headers()
    body = {"withdrawals": [{"address": address, "amount": float(amount), "currency": currency}]}
    try:
        resp = requests.post(url, headers=headers, data=json.dumps(body), timeout=30)
    except requests.RequestException as e:
        log.error(f"nowpayments_create_payout network error: {e}")
        raise HTTPException(status_code=502, detail="NOWPayments network error")

    if resp.status_code >= 400:
        log.error(f"nowpayments_create_payout http {resp.status_code}: {resp.text}")
        raise HTTPException(status_code=502, detail="NOWPayments rejected payout request")

    try:
        return resp.json()
    except Exception:
        return {"raw": resp.text}


# -------------------------------------
# Endpoints
# -------------------------------------
@app.get("/health")
def health():
    return {"ok": True, "ts": datetime.utcnow().isoformat()}


@app.post("/otc/set-price")
def set_price(payload: SetPriceIn, db: Session = Depends(get_db)):
    token = payload.token_symbol.upper()
    price = db.query(Price).filter(Price.token_symbol == token).one_or_none()
    if price is None:
        price = Price(token_symbol=token)
    price.price_eur = payload.price_eur
    price.available_amount = payload.available_amount or 0
    price.updated_at = datetime.utcnow()
    db.add(price)
    db.commit()
    return {"ok": True, "token_symbol": token, "price_eur": price.price_eur}


@app.post("/offramp/create-order")
def create_order(payload: CreateOrderIn, db: Session = Depends(get_db)):
    token = payload.token_symbol.upper()
    price_row = db.query(Price).filter(Price.token_symbol == token).one_or_none()
    price_eur = price_row.price_eur if price_row else payload.price_eur
    eur_amount = payload.amount_tokens * price_eur

    order = Order(
        token_symbol=token,
        amount_tokens=payload.amount_tokens,
        price_eur=price_eur,
        eur_amount=eur_amount,
        payout_channel=payload.payout_channel.upper(),
        wallet_address=payload.wallet_address,
        crypto_asset=(payload.crypto_asset or "").upper(),
        crypto_network=(payload.crypto_network or "").upper(),
        notes=payload.notes,
        status="created",
    )
    db.add(order)
    db.commit()
    db.refresh(order)
    return {
        "order_id": order.id,
        "status": order.status,
        "eur_amount": order.eur_amount,
        "price_eur": order.price_eur,
        "token_symbol": order.token_symbol,
    }


@app.post("/offramp/trigger-payout/{order_id}")
def trigger_payout(order_id: int, payload: PayoutIn, db: Session = Depends(get_db)):
    """
    Avvia payout reale su NOWPayments e lascia l'ordine in 'queued'.
    - Se crypto_amount è assente, converte automaticamente EUR -> crypto via API NOWPayments.
    - Applica slippage_bps (basis points) all'importo calcolato.
    - Salva la risposta provider in notes e memorizza un riferimento in payout_txid (id/txid).
    """
    order = db.query(Order).filter(Order.id == order_id).one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    # Aggiorna info crypto se fornite
    order.wallet_address = payload.wallet_address or order.wallet_address
    order.crypto_asset = (payload.crypto_asset or order.crypto_asset or "").upper()
    order.crypto_network = (payload.crypto_network or order.crypto_network or "").upper()

    if (payload.method or "").upper() != "CRYPTO" and order.payout_channel != "CRYPTO":
        raise HTTPException(status_code=422, detail="Only CRYPTO payout supported in this endpoint")

    if not order.wallet_address or not order.crypto_asset or not order.crypto_network:
        raise HTTPException(status_code=422, detail="Missing wallet_address / crypto_asset / crypto_network")

    currency_code = build_np_currency(order.crypto_asset, order.crypto_network)

    # Calcolo importo crypto
    if payload.crypto_amount is not None:
        crypto_amt = float(payload.crypto_amount)
    else:
        crypto_amt = nowpayments_convert_eur_to_crypto(order.eur_amount, currency_code)

    # Applica slippage (bps) con bound di sicurezza 0..2000 bps (0..20%)
    bps = int(payload.slippage_bps or 0)
    bps = max(0, min(2000, bps))
    crypto_amt *= (1.0 + bps / 10_000.0)

    # Crea payout NOWPayments
    np_resp = nowpayments_create_payout(
        address=order.wallet_address,
        amount=float(crypto_amt),
        currency=currency_code,
    )

    # Persist risposta e riferimento
    snippet = json.dumps(np_resp)[:4000]
    order.notes = (order.notes or "")
    order.notes = (order.notes + f" | np_payout_resp={snippet}").strip()

    payout_ref = None
    try:
        if isinstance(np_resp, dict):
            if "id" in np_resp and isinstance(np_resp["id"], (str, int)):
                payout_ref = str(np_resp["id"])
            elif "withdrawals" in np_resp and isinstance(np_resp["withdrawals"], list) and np_resp["withdrawals"]:
                wid = np_resp["withdrawals"][0]
                payout_ref = str(wid.get("id") or wid.get("txid") or "")
            elif "data" in np_resp and isinstance(np_resp["data"], list) and np_resp["data"]:
                wid = np_resp["data"][0]
                payout_ref = str(wid.get("id") or wid.get("txid") or "")
    except Exception:
        pass

    if payout_ref:
        order.payout_txid = payout_ref

    order.status = "queued"
    db.commit()

    return {
        "ok": True,
        "order_id": order.id,
        "new_status": order.status,
        "payout_reference": order.payout_txid,
        "currency": currency_code,
        "amount": float(crypto_amt),
        "slippage_bps": bps,
    }


@app.post("/webhooks/nowpayments")
async def nowpayments_webhook(request: Request, x_nowpayments_sig: str = Header(default=""), db: Session = Depends(get_db)):
    raw = await request.body()

    # Idempotency base (dedup)
    body_hash = hashlib.sha256(raw).hexdigest()
    try:
        evt = WebhookEvent(provider="nowpayments", signature=(x_nowpayments_sig or ""), body_hash=body_hash)
        db.add(evt)
        db.commit()
    except Exception:
        db.rollback()
        return {"ok": True, "note": "duplicate"}

    # Firma
    if not verify_nowpayments_signature(raw, x_nowpayments_sig):
        raise HTTPException(status_code=401, detail="Invalid signature")

    payload = json.loads(raw.decode("utf-8"))
    oid = int(payload.get("external_id") or payload.get("order_id", 0))
    order = db.query(Order).filter(Order.id == oid).one_or_none()
    if not order:
        return {"ok": True, "note": "order not found"}

    status = payload.get("status")
    txid = payload.get("transaction_id") or payload.get("txid")

    if status == "finished":
        order.status = "completed"
        if txid:
            order.payout_txid = txid
    elif status in ("processing", "pending", "confirming"):
        order.status = "queued"
    elif status in ("failed", "canceled"):
        order.status = "failed"

    db.commit()
    return {"ok": True, "order_id": oid, "new_status": order.status, "payout_txid": order.payout_txid}


@app.get("/offramp/orders/{order_id}")
def get_order(order_id: int, db: Session = Depends(get_db)):
    order = db.query(Order).filter(Order.id == order_id).one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="Not Found")
    return {
        "order_id": order.id,
        "status": order.status,
        "payout_txid": order.payout_txid,
        "token_symbol": order.token_symbol,
        "amount_tokens": order.amount_tokens,
        "price_eur": order.price_eur,
        "eur_amount": order.eur_amount,
        "wallet_address": order.wallet_address,
        "crypto_asset": order.crypto_asset,
        "crypto_network": order.crypto_network,
        "created_at": order.created_at.isoformat(),
        "notes": order.notes,
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
