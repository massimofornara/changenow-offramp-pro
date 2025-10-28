from fastapi import FastAPI, HTTPException, Path, Request, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any
from threading import Lock
from datetime import datetime
import hmac, hashlib, json, os

app = FastAPI(title="ChangeNOW Offramp Pro", version="1.0.1")

# --- CORS ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ------------------------------
#          MODELS
# ------------------------------

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
    method: str = ""
    card_token: Optional[str] = None
    beneficiary_name: Optional[str] = None
    iban: Optional[str] = None
    wallet_address: Optional[str] = None
    crypto_asset: Optional[str] = None
    crypto_network: Optional[str] = None


class Order(BaseModel):
    id: int
    created_at: datetime
    token_symbol: str
    amount_tokens: float
    price_eur: float
    eur_amount: float
    payout_channel: str
    wallet_address: Optional[str] = None
    crypto_asset: Optional[str] = None
    crypto_network: Optional[str] = None
    notes: Optional[str] = None
    status: str = "created"          # created | queued | completed | failed
    payout_txid: Optional[str] = None


# ------------------------------
#  In-memory store
# ------------------------------

PRICES: Dict[str, Dict[str, Any]] = {}
ORDERS: Dict[int, Order] = {}
_order_seq = 0
_lock = Lock()

# ------------------------------
#  Utility: firma NOWPayments
# ------------------------------

NP_IPN_SECRET = os.getenv("NP_IPN_SECRET", "demo_secret")

def verify_nowpayments_signature(raw_body: bytes, signature: str) -> bool:
    if not signature:
        return False
    calc = hmac.new(NP_IPN_SECRET.encode(), msg=raw_body, digestmod=hashlib.sha512).hexdigest()
    return hmac.compare_digest(calc.lower(), signature.lower())

# ------------------------------
#          ENDPOINTS
# ------------------------------

@app.get("/health")
def health():
    return {"ok": True, "ts": datetime.utcnow().isoformat()}


@app.post("/otc/set-price")
def set_price(payload: SetPriceIn):
    PRICES[payload.token_symbol.upper()] = {
        "price_eur": float(payload.price_eur),
        "available_amount": float(payload.available_amount or 0),
        "updated_at": datetime.utcnow().isoformat(),
    }
    return {"ok": True, "token_symbol": payload.token_symbol.upper(), **PRICES[payload.token_symbol.upper()]}


@app.post("/offramp/create-order")
def create_order(payload: CreateOrderIn):
    token = payload.token_symbol.upper()
    price_eur = float(payload.price_eur)

    if token in PRICES and PRICES[token].get("price_eur") is not None:
        price_eur = float(PRICES[token]["price_eur"])

    eur_amount = float(payload.amount_tokens) * price_eur

    global _order_seq
    with _lock:
        _order_seq += 1
        oid = _order_seq

    order = Order(
        id=oid,
        created_at=datetime.utcnow(),
        token_symbol=token,
        amount_tokens=float(payload.amount_tokens),
        price_eur=price_eur,
        eur_amount=eur_amount,
        payout_channel=payload.payout_channel.upper(),
        wallet_address=payload.wallet_address,
        crypto_asset=(payload.crypto_asset.upper() if payload.crypto_asset else None),
        crypto_network=(payload.crypto_network.upper() if payload.crypto_network else None),
        notes=payload.notes,
    )
    ORDERS[oid] = order

    return {
        "order_id": order.id,
        "status": order.status,
        "eur_amount": order.eur_amount,
        "price_eur": order.price_eur,
        "token_symbol": order.token_symbol,
    }


@app.post("/offramp/trigger-payout/{order_id}")
def trigger_payout(order_id: int = Path(...), payload: PayoutIn = ...):
    """
    Avvia il payout e segna l'ordine come 'queued'.
    La conferma reale arriverà dal webhook.
    """
    order = ORDERS.get(int(order_id))
    if not order:
        raise HTTPException(status_code=404, detail="Not Found")

    if (payload.method or "").upper() == "CRYPTO" or order.payout_channel == "CRYPTO":
        order.wallet_address = payload.wallet_address or order.wallet_address
        order.crypto_asset = (payload.crypto_asset or order.crypto_asset or "").upper() or None
        order.crypto_network = (payload.crypto_network or order.crypto_network or "").upper() or None

    order.status = "queued"

    return {"ok": True, "order_id": order.id, "new_status": order.status}


@app.post("/webhooks/nowpayments")
async def nowpayments_webhook(request: Request, x_nowpayments_sig: str = Header(default="")):
    """
    Riceve conferma immediata del payout (NOWPayments IPN).
    """
    raw = await request.body()

    # Verifica firma
    if not verify_nowpayments_signature(raw, x_nowpayments_sig):
        raise HTTPException(status_code=401, detail="Invalid signature")

    payload = json.loads(raw.decode("utf-8"))
    status = payload.get("status")
    external_id = payload.get("external_id") or payload.get("order_id")
    txid = payload.get("transaction_id") or payload.get("txid") or f"SIM-{int(datetime.utcnow().timestamp())}"

    if not external_id:
        return {"ok": True, "note": "no order id"}

    oid = int(str(external_id))
    order = ORDERS.get(oid)
    if not order:
        return {"ok": True, "note": "order not found"}

    # Conferma immediata se status = finished
    if status == "finished":
        order.status = "completed"
        order.payout_txid = txid
        return {"ok": True, "order_id": oid, "new_status": "completed", "payout_txid": txid}

    # Stati intermedi
    if status in ("processing", "confirming", "pending"):
        order.status = "queued"
        return {"ok": True, "order_id": oid, "new_status": "queued"}

    # Falliti
    if status in ("failed", "canceled"):
        order.status = "failed"
        return {"ok": True, "order_id": oid, "new_status": "failed"}

    return {"ok": True, "note": "ignored status"}


@app.get("/offramp/orders/{order_id}")
def get_order(order_id: int = Path(...)):
    order = ORDERS.get(int(order_id))
    if not order:
        raise HTTPException(status_code=404, detail="Not Found")

    data = order.model_dump()
    return {
        "order_id": data["id"],
        "status": data["status"],
        "payout_txid": data.get("payout_txid"),
        "token_symbol": data["token_symbol"],
        "amount_tokens": data["amount_tokens"],
        "price_eur": data["price_eur"],
        "eur_amount": data["eur_amount"],
        "payout_channel": data["payout_channel"],
        "wallet_address": data.get("wallet_address"),
        "crypto_asset": data.get("crypto_asset"),
        "crypto_network": data.get("crypto_network"),
        "created_at": data["created_at"].isoformat(),
        "notes": data.get("notes"),
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
