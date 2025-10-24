import os
import requests
from fastapi import FastAPI, HTTPException, Body
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, Dict

# =========================
# 🔧 CONFIGURAZIONE GLOBALE
# =========================
SERVICE_NAME = "changenow-offramp-pro"
PROVIDER = os.getenv("PROVIDER", "nowpayments")

# NOWPayments setup
NP_BASE_URL = os.getenv("NP_BASE_URL", "https://api.nowpayments.io/v1").rstrip("/")
NP_PAYOUT_PATH = os.getenv("NP_PAYOUT_PATH", "/payouts")
NP_USE_JWT = os.getenv("NP_USE_JWT", "false").lower() == "true"
NP_API_KEY = os.getenv("NP_API_KEY")

# =========================
# 🚀 APP FASTAPI
# =========================
app = FastAPI(title=SERVICE_NAME, version="1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# =========================
# 📦 DATI IN-MEMORY (mock DB)
# =========================
ORDERS: Dict[int, Dict] = {}
LISTINGS: Dict[str, Dict] = {}

# =========================
# 📄 SCHEMI
# =========================
class SetPriceIn(BaseModel):
    token_symbol: str
    price_eur: float
    available_amount: Optional[int] = 0


class CreateOrderIn(BaseModel):
    token_symbol: str
    amount_tokens: float
    price_eur: float
    beneficiary_name: str
    iban: str
    redirect_url: Optional[str] = None
    notes: Optional[str] = None


class PayoutIn(BaseModel):
    method: str = "SEPA"


# =========================
# 🧱 FUNZIONI NOWPAYMENTS
# =========================
def np_headers():
    """Header auth per NOWPayments (API key o JWT)."""
    if NP_USE_JWT:
        # fallback JWT (non usato in produzione)
        token = get_or_refresh_jwt_somehow()  # definisci se serve davvero
        return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    # default: API key
    return {"x-api-key": NP_API_KEY, "Content-Type": "application/json"}


def create_payout(payload: dict):
    """Crea payout su NOWPayments (fiat settlement)."""
    url = f"{NP_BASE_URL}{NP_PAYOUT_PATH}"
    idem = payload.get("idempotency_key", str(payload.get("order_id", "")) or "np-")
    headers = np_headers()
    headers["Idempotency-Key"] = idem
    r = requests.post(url, json=payload, headers=headers, timeout=30)
    try:
        r.raise_for_status()
    except Exception as e:
        raise HTTPException(status_code=r.status_code, detail={"message": str(e), "response": r.text})
    return r.json()


# =========================
# 🔗 ENDPOINTS
# =========================

@app.get("/")
def root():
    return {"ok": True, "service": SERVICE_NAME, "env": os.getenv("RENDER_ENV", "production")}


# ---- HEALTH ----
@app.get("/offramp/health")
def health(verbose: Optional[bool] = False):
    data = {"ok": True, "service": SERVICE_NAME, "provider": PROVIDER}
    if verbose:
        data.update({
            "NP_BASE_URL": NP_BASE_URL,
            "auth_mode": "jwt" if NP_USE_JWT else "api_key"
        })
    return data


@app.get("/nowpayments/health")
def nowpayments_health():
    try:
        h = np_headers()
        r = requests.get(f"{NP_BASE_URL}/status", headers=h, timeout=10)
        return {"ok": r.status_code == 200, "status_code": r.status_code, "auth_mode": "jwt" if NP_USE_JWT else "api_key"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ---- OTC LISTINGS ----
@app.get("/otc/listings")
def get_listings(token_symbol: Optional[str] = None):
    if token_symbol:
        sym = token_symbol.upper()
        if sym not in LISTINGS:
            raise HTTPException(status_code=404, detail="Token not listed")
        return {sym: LISTINGS[sym]}
    return LISTINGS


@app.post("/otc/set-price")
def set_price(data: SetPriceIn):
    sym = data.token_symbol.upper()
    LISTINGS[sym] = {"price_eur": data.price_eur, "available_amount": data.available_amount}
    return {"ok": True, "token": sym, "price_eur": data.price_eur, "available_amount": data.available_amount}


# ---- CREATE ORDER ----
@app.post("/offramp/create-order")
def create_order(data: CreateOrderIn):
    new_id = len(ORDERS) + 1
    eur_amount = data.amount_tokens * data.price_eur
    ORDERS[new_id] = {
        "order_id": new_id,
        "status": "created",
        "token_symbol": data.token_symbol.upper(),
        "price_eur": data.price_eur,
        "amount_tokens": data.amount_tokens,
        "beneficiary_name": data.beneficiary_name,
        "iban": data.iban,
        "eur_amount": eur_amount,
        "redirect_url": data.redirect_url,
        "notes": data.notes,
    }
    return {
        "order_id": new_id,
        "status": "created",
        "eur_amount": eur_amount,
        "price_eur": data.price_eur,
        "token_symbol": data.token_symbol,
        "redirect_url": data.redirect_url
    }


# ---- TRIGGER PAYOUT ----
@app.post("/offramp/trigger-payout/{order_id}")
def trigger_payout(order_id: str, payload: PayoutIn = Body(...)):
    try:
        order_key = int(order_id)
    except ValueError:
        order_key = order_id

    order = ORDERS.get(order_key)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    payout_payload = {
        "order_id": order_key,
        "currency": "EUR",
        "amount": order["eur_amount"],
        "payout_address": order["iban"],
        "beneficiary_name": order["beneficiary_name"],
        "method": payload.method,
        "idempotency_key": f"payout-{order_key}"
    }

    try:
        response = create_payout(payout_payload)
        order["status"] = "queued"
        order["payout_response"] = response
        return {"ok": True, "order_id": order_key, "status": "queued", "response": response}
    except HTTPException as e:
        order["status"] = "failed"
        order["payout_error"] = e.detail
        raise


# ---- GET ORDER STATUS ----
@app.get("/offramp/orders/{order_id}")
def get_order(order_id: str):
    try:
        key = int(order_id)
    except ValueError:
        key = order_id
    order = ORDERS.get(key)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    return order
