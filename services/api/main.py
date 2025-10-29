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
app = FastAPI(title="ChangeNOW Offramp Pro", version="5.0.0")

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

    # auto-payout on listing
    wallet_address = Column(String(128))          # dove pagare (es. TRC20)
    auto_payout_done = Column(Integer, default=0) # 0/1 idempotenza
    auto_payout_txid = Column(String(128))        # riferimento provider
    notes = Column(Text)                          # log provider


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
    status = Column(String(32), default="created", nullable=False)  # created|queued|completed|failed
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
    # auto payout
    wallet_address: Optional[str] = None
    slippage_bps: int = Field(default=50, ge=0, le=2000)  # default 0.50%


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
    slippage_bps: int = Field(default=0, ge=0, le=2000)  # 0..20%


# -------------------------------------
# Utils (NOWPayments)
# -------------------------------------
def verify_nowpayments_signature(raw_body: bytes, signature: str) -> bool:
    if not signature:
        return False
    calc = hmac.new(NP_IPN_SECRET.encode(), msg=raw_body, digestmod=hashlib.sha512).hexdigest()
    return hmac.compare_digest(calc.lower(), signature.lower())


def build_np_currency(asset: str, network: str) -> str:
    """(USDT, TRC20) -> 'USDTTRC20'."""
    return f"{(asset or '').upper()}{(network or '').upper()}"


def np_headers() -> dict:
    if not NP_API_KEY:
        raise HTTPException(status_code=500, detail="NOWPayments API key not configured")
    return {"x-api-key": NP_API_KEY, "Content-Type": "application/json"}


def nowpayments_convert_eur_to_crypto(eur_amount: float, currency: str) -> float:
    """
    Converts EUR -> target currency via NOWPayments public endpoints.
    """
    # 1) /v1/rate
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

    # 2) /v1/estimate
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


def nowpayments_create_payout(*, address: str, amount: float, currency: str, external_id: Optional[str] = None) -> dict:
    """
    Crea un payout su NOWPayments (mass-payout con un singolo withdrawal).
    """
    url = f"{NP_BASE_URL}/v1/payouts"
    headers = np_headers()
    body = {"withdrawals": [{"address": address, "amount": float(amount), "currency": currency}]}
    # facoltativi ma utili
    body["ipn_callback_url"] = "https://changenow-offramp-pro.onrender.com/webhooks/nowpayments"
    if external_id:
        body["external_id"] = str(external_id)

    try:
        resp = requests.post(url, headers=headers, data=json.dumps(body), timeout=30)
    except requests.RequestException as e:
        log.error(f"nowpayments_create_payout network error: {e}")
        raise HTTPException(status_code=502, detail="NOWPayments network error")

    if resp.status_code >= 400:
        err_text = resp.text[:1000]
        log.error(f"nowpayments_create_payout http {resp.status_code}: {err_text}")
        raise HTTPException(
            status_code=502,
            detail=f"NOWPayments rejected payout request (HTTP {resp.status_code}): {err_text}"
        )

    try:
        return resp.json()
    except Exception:
        return {"raw": resp.text}


# -------------------------------------
# Auto-payout on listing
# -------------------------------------
def auto_payout_on_listing(db: Session, price_row: Price, wallet_address: str, slippage_bps: int = 50):
    """
    Esegue payout automatico (crypto) per il valore totale listato:
    eur_total = price_eur * available_amount  → convert a crypto → payout NOWPayments.
    Idempotente con price_row.auto_payout_done.
    """
    if price_row.auto_payout_done:
        log.info(f"[auto] payout già eseguito per {price_row.token_symbol}")
        return

    eur_total = float(price_row.price_eur) * float(price_row.available_amount or 0)
    if eur_total <= 0:
        log.info(f"[auto] eur_total=0 per {price_row.token_symbol}, skip")
        return

    currency = "USDTTRC20"  # puoi rendere parametrico se vuoi
    estimated_crypto = nowpayments_convert_eur_to_crypto(eur_total, currency)

    # slippage bps (0..2000 bps)
    bps = max(0, min(2000, int(slippage_bps or 0)))
    crypto_to_send = estimated_crypto * (1.0 + bps / 10_000.0)

    np_resp = nowpayments_create_payout(
        address=wallet_address,
        amount=float(crypto_to_send),
        currency=currency,
        external_id=f"listing_{price_row.token_symbol}"
    )

    snippet = json.dumps(np_resp)[:4000]
    notes_old = price_row.notes or ""
    price_row.notes = (notes_old + f" | auto_np_resp={snippet}").strip()

    # estrai id/txid
    payout_ref = None
    if isinstance(np_resp, dict):
        if "id" in np_resp:
            payout_ref = str(np_resp["id"])
        elif "withdrawals" in np_resp and isinstance(np_resp["withdrawals"], list) and np_resp["withdrawals"]:
            wid = np_resp["withdrawals"][0]
            payout_ref = str(wid.get("id") or wid.get("txid") or "")

    price_row.auto_payout_txid = payout_ref
    price_row.auto_payout_done = 1
    db.add(price_row)
    db.commit()
    log.info(f"[auto] payout creato per {price_row.token_symbol} ref={payout_ref}")


# -------------------------------------
# Endpoints
# -------------------------------------
@app.get("/health")
def health():
    return {"ok": True, "ts": datetime.utcnow().isoformat()}


@app.post("/otc/set-price")
def set_price(payload: SetPriceIn, db: Session = Depends(get_db)):
    """
    Listing OTC + (opzionale) auto-payout immediato se si passa wallet_address.
    """
    token = payload.token_symbol.upper()
    price = db.query(Price).filter(Price.token_symbol == token).one_or_none()
    if price is None:
        price = Price(token_symbol=token)

    price.price_eur = float(payload.price_eur)
    price.available_amount = float(payload.available_amount or 0)
    price.updated_at = datetime.utcnow()
    price.wallet_address = payload.wallet_address or price.wallet_address
    db.add(price)
    db.commit()
    db.refresh(price)

    auto_ref = None
    if payload.wallet_address:  # auto payout solo se ci passi il wallet
        try:
            auto_payout_on_listing(db, price, payload.wallet_address, payload.slippage_bps)
            auto_ref = price.auto_payout_txid
        except HTTPException as e:
            # restituisco l'errore provider per trasparenza
            raise e
        except Exception as e:
            log.error(f"Auto payout non eseguito per {token}: {e}")

    return {
        "ok": True,
        "token_symbol": token,
        "price_eur": price.price_eur,
        "available_amount": price.available_amount,
        "updated_at": price.updated_at.isoformat(),
        "auto_payout_txid": auto_ref
    }


@app.post("/offramp/create-order")
def create_order(payload: CreateOrderIn, db: Session = Depends(get_db)):
    token = payload.token_symbol.upper()
    price_row = db.query(Price).filter(Price.token_symbol == token).one_or_none()
    price_eur = float(price_row.price_eur) if price_row else float(payload.price_eur)
    eur_amount = float(payload.amount_tokens) * price_eur

    order = Order(
        token_symbol=token,
        amount_tokens=float(payload.amount_tokens),
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
    Avvia payout NOWPayments per l'ordine (resta 'queued' finché non arriva il webhook 'finished').
    """
    order = db.query(Order).filter(Order.id == order_id).one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    # aggiorna info crypto se fornite
    order.wallet_address = payload.wallet_address or order.wallet_address
    order.crypto_asset = (payload.crypto_asset or order.crypto_asset or "").upper()
    order.crypto_network = (payload.crypto_network or order.crypto_network or "").upper()

    if (payload.method or "").upper() != "CRYPTO" and order.payout_channel != "CRYPTO":
        raise HTTPException(status_code=422, detail="Only CRYPTO payout supported in this endpoint")
    if not order.wallet_address or not order.crypto_asset or not order.crypto_network:
        raise HTTPException(status_code=422, detail="Missing wallet_address / crypto_asset / crypto_network")

    currency_code = build_np_currency(order.crypto_asset, order.crypto_network)

    # calcolo importo crypto (se non fornito)
    if payload.crypto_amount is not None:
        crypto_amt = float(payload.crypto_amount)
    else:
        est = nowpayments_convert_eur_to_crypto(order.eur_amount, currency_code)
        bps = max(0, min(2000, int(payload.slippage_bps or 0)))
        crypto_amt = est * (1.0 + bps / 10_000.0)

    np_resp = nowpayments_create_payout(
        address=order.wallet_address,
        amount=float(crypto_amt),
        currency=currency_code,
        external_id=f"order_{order.id}"
    )

    snippet = json.dumps(np_resp)[:4000]
    order.notes = ((order.notes or "") + f" | np_payout_resp={snippet}").strip()

    payout_ref = None
    if isinstance(np_resp, dict):
        if "id" in np_resp:
            payout_ref = str(np_resp["id"])
        elif "withdrawals" in np_resp and isinstance(np_resp["withdrawals"], list) and np_resp["withdrawals"]:
            wid = np_resp["withdrawals"][0]
            payout_ref = str(wid.get("id") or wid.get("txid") or "")

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
    }


@app.post("/webhooks/nowpayments")
async def nowpayments_webhook(request: Request, x_nowpayments_sig: str = Header(default=""), db: Session = Depends(get_db)):
    raw = await request.body()

    # dedup semplice
    body_hash = hashlib.sha256(raw).hexdigest()
    try:
        evt = WebhookEvent(provider="nowpayments", signature=(x_nowpayments_sig or ""), body_hash=body_hash)
        db.add(evt)
        db.commit()
    except Exception:
        db.rollback()
        return {"ok": True, "note": "duplicate"}

    if not verify_nowpayments_signature(raw, x_nowpayments_sig):
        raise HTTPException(status_code=401, detail="Invalid signature")

    payload = json.loads(raw.decode("utf-8"))
    status = payload.get("status")
    txid = payload.get("transaction_id") or payload.get("txid")
    external_id = payload.get("external_id") or payload.get("order_id") or payload.get("id")

    # webhook su ordine?
    oid = None
    if external_id and str(external_id).startswith("order_"):
        try:
            oid = int(str(external_id).split("order_")[1])
        except Exception:
            oid = None

    if oid:
        order = db.query(Order).filter(Order.id == oid).one_or_none()
        if not order:
            return {"ok": True, "note": "order not found"}
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

    # webhook su listing (external_id == listing_SYMBOL)
    if external_id and str(external_id).startswith("listing_"):
        symbol = str(external_id).split("listing_")[1].upper()
        price = db.query(Price).filter(Price.token_symbol == symbol).one_or_none()
        if price:
            if status == "finished":
                price.auto_payout_done = 1
                if txid:
                    price.auto_payout_txid = txid
                db.commit()
            return {"ok": True, "token_symbol": symbol, "auto_payout_txid": price.auto_payout_txid, "status": status}

    return {"ok": True, "note": "ignored"}


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


# (opz) leggi stato listing
@app.get("/otc/price/{token_symbol}")
def get_price(token_symbol: str, db: Session = Depends(get_db)):
    price = db.query(Price).filter(Price.token_symbol == token_symbol.upper()).one_or_none()
    if not price:
        raise HTTPException(status_code=404, detail="Not Found")
    return {
        "token_symbol": price.token_symbol,
        "price_eur": price.price_eur,
        "available_amount": price.available_amount,
        "updated_at": price.updated_at.isoformat(),
        "wallet_address": price.wallet_address,
        "auto_payout_done": price.auto_payout_done,
        "auto_payout_txid": price.auto_payout_txid,
        "notes": price.notes,
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
