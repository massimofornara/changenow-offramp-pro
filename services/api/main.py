# services/api/main.py
import os
import uuid
import time
from typing import Optional, Dict, Literal

import requests
from fastapi import FastAPI, HTTPException, Body
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

# ==========================================================
#  🔧 CONFIG
# ==========================================================
SERVICE_NAME = "changenow-offramp-pro"

# --- OTC defaults (facoltativi) ---
OTC_DEFAULT_TOKEN = os.getenv("OTC_DEFAULT_TOKEN", "NENO").upper()
OTC_DEFAULT_PRICE = float(os.getenv("OTC_DEFAULT_PRICE", "5"))
OTC_DEFAULT_AMOUNT = float(os.getenv("OTC_DEFAULT_AMOUNT", "100000"))

# --- Provider selector (informativo) ---
PROVIDER = os.getenv("PROVIDER", "stripe").lower()

# --- STRIPE (Card payouts) ---
STRIPE_API_KEY = os.getenv("STRIPE_API_KEY", "").strip()
STRIPE_CONNECT_ACCOUNT = os.getenv("STRIPE_CONNECT_ACCOUNT", "").strip()  # opzionale (acct_...)
STRIPE_CURRENCY = os.getenv("STRIPE_CURRENCY", "eur").lower()

# --- NOWPayments (Crypto) ---
NP_BASE_URL = os.getenv("NP_BASE_URL", "https://api.nowpayments.io/v1").rstrip("/")
NP_PAYOUT_PATH = os.getenv("NP_PAYOUT_PATH", "/payouts")
NP_API_KEY = os.getenv("NP_API_KEY", "").strip()
NP_USE_JWT = os.getenv("NP_USE_JWT", "false").lower() == "true"

# --- Wise (SEPA) ---
WISE_API_TOKEN = os.getenv("WISE_API_TOKEN", "").strip()
WISE_PROFILE_ID = os.getenv("WISE_PROFILE_ID", "").strip()
WISE_SOURCE_CURRENCY = os.getenv("WISE_SOURCE_CURRENCY", "EUR").upper()
WISE_BASE_URL_ENV = os.getenv("WISE_BASE_URL", "").strip()  # es: https://api.transferwise.com

# ==========================================================
#  ⚙️ FASTAPI
# ==========================================================
app = FastAPI(title=SERVICE_NAME, version="3.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"],
)

# In-memory store (swap con DB in produzione)
LISTINGS: Dict[str, Dict] = {}
ORDERS: Dict[int, Dict] = {}
PAYOUTS: Dict[str, Dict] = {}

# ==========================================================
#  📦 MODELS
# ==========================================================
class SetPriceIn(BaseModel):
    token_symbol: str
    price_eur: float
    available_amount: float = Field(0, ge=0)

class CreateOrderIn(BaseModel):
    token_symbol: str
    amount_tokens: float = Field(..., gt=0)
    price_eur: float = Field(..., gt=0)
    # opzionali info SEPA
    beneficiary_name: Optional[str] = None
    iban: Optional[str] = None
    # multi-rail hint lato ordine
    payout_channel: Optional[str] = None  # "CARD_VISA" | "CARD_MASTERCARD" | "SEPA" | "CRYPTO"
    # carta
    card_token: Optional[str] = None
    # crypto
    wallet_address: Optional[str] = None
    crypto_asset: Optional[str] = None
    crypto_network: Optional[str] = None
    # altro
    redirect_url: Optional[str] = None
    notes: Optional[str] = None

class OfframpPayoutLegacyIn(BaseModel):
    method: Literal["CRYPTO", "SEPA", "CARD_VISA", "CARD_MASTERCARD"]
    # CRYPTO
    wallet_address: Optional[str] = None
    crypto_asset: Optional[str] = None
    crypto_network: Optional[str] = None
    # SEPA
    beneficiary_name: Optional[str] = None
    iban: Optional[str] = None
    # CARD
    card_token: Optional[str] = None

class PayoutCreateIn(BaseModel):
    order_id: int
    channel: Literal["CARD_VISA", "CARD_MASTERCARD", "SEPA", "CRYPTO"]
    # Card
    card_token: Optional[str] = None
    # SEPA
    beneficiary_name: Optional[str] = None
    iban: Optional[str] = None
    # Crypto
    wallet_address: Optional[str] = None
    crypto_asset: Optional[str] = None
    crypto_network: Optional[str] = None
    note: Optional[str] = "OTC payout"

# ==========================================================
#  🔐 HELPERS
# ==========================================================
def http_raise(r: requests.Response, extra=None):
    if r.status_code >= 400:
        detail = {
            "url": getattr(r.request, "url", "<unknown>"),
            "status": r.status_code,
            "body": (r.text or "<empty>")[:4000],
        }
        if extra:
            detail.update(extra)
        raise HTTPException(status_code=422, detail=detail)

def cents(amount_eur: float) -> int:
    return int(round(amount_eur * 100))

# ============ NOWPayments helpers (CRYPTO) ============
def np_headers():
    if NP_USE_JWT:
        raise HTTPException(status_code=400, detail="NP_USE_JWT=true non supportato qui; usa x-api-key")
    if not NP_API_KEY:
        raise HTTPException(status_code=400, detail="NP_API_KEY mancante per CRYPTO")
    return {"x-api-key": NP_API_KEY, "Content-Type": "application/json"}

def np_create_payout(payload: dict):
    base = NP_BASE_URL
    paths = [NP_PAYOUT_PATH, "/payouts", "/payout"]  # prova varianti
    headers = np_headers()
    headers["Idempotency-Key"] = payload.get("idempotency_key") or f"np-{uuid.uuid4()}"
    last_err = None
    for p in paths:
        url = f"{base}{p}"
        r = requests.post(url, json=payload, headers=headers, timeout=30)
        if r.status_code == 404:
            last_err = r
            continue
        http_raise(r)
        return r.json()
    http_raise(last_err or r, extra={"message": "NP payout endpoint not found", "tried": paths})

# ============ Wise helpers (SEPA) ============
def wise_base_url() -> str:
    base = (WISE_BASE_URL_ENV or "https://api.transferwise.com").rstrip("/")
    return base + "/v1"

def wise_headers() -> dict:
    if not WISE_API_TOKEN or not WISE_PROFILE_ID:
        raise HTTPException(status_code=400, detail="WISE_API_TOKEN/WISE_PROFILE_ID mancanti")
    return {"Authorization": f"Bearer {WISE_API_TOKEN}", "Content-Type": "application/json"}

def wise_post(url: str, payload: dict):
    r = requests.post(url, json=payload, headers=wise_headers(), timeout=30)
    http_raise(r)
    return r.json()

def wise_quote(amount_eur: float):
    url = f"{wise_base_url()}/quotes"
    # schema moderno, fallback legacy
    payload = {
        "profile": int(WISE_PROFILE_ID),
        "sourceCurrency": WISE_SOURCE_CURRENCY,
        "targetCurrency": "EUR",
        "sourceAmount": amount_eur,
        "rateType": "FIXED",
        "payOut": "BANK_TRANSFER",
        "preferredPayIn": "BALANCE",
    }
    r = requests.post(url, json=payload, headers=wise_headers(), timeout=30)
    if r.status_code == 400:
        payload = {
            "profile": int(WISE_PROFILE_ID),
            "source": WISE_SOURCE_CURRENCY,
            "target": "EUR",
            "sourceAmount": amount_eur,
            "rateType": "FIXED",
            "payOut": "BANK_TRANSFER",
            "preferredPayIn": "BALANCE",
        }
        r = requests.post(url, json=payload, headers=wise_headers(), timeout=30)
    http_raise(r)
    return r.json()

def wise_create_recipient(name: str, iban: str):
    url = f"{wise_base_url()}/accounts"
    payload = {
        "currency": "EUR",
        "type": "iban",
        "profile": int(WISE_PROFILE_ID),
        "ownedByCustomer": False,
        "details": {"legalType": "PRIVATE", "IBAN": iban, "accountHolderName": name},
    }
    return wise_post(url, payload)

def wise_create_transfer(quote_id: str, recipient_id: int, note: str):
    url = f"{wise_base_url()}/transfers"
    payload = {
        "targetAccount": recipient_id,
        "quoteUuid": quote_id,
        "customerTransactionId": str(uuid.uuid4()),
        "details": {
            "reference": (note or "OTC payout")[:35],
            "transferPurpose": "verification.transfers.payout",
            "sourceOfFunds": "other",
        },
    }
    return wise_post(url, payload)

def wise_fund_transfer(transfer_id: int):
    url = f"{wise_base_url()}/transfers/{transfer_id}/payments"
    payload = {"type": "BALANCE"}
    return wise_post(url, payload)

def wise_payout(name: str, iban: str, amount_eur: float, note: str):
    q = wise_quote(amount_eur)
    r = wise_create_recipient(name, iban)
    t = wise_create_transfer(q["id"], r["id"], note)
    try:
        f = wise_fund_transfer(t["id"])
    except HTTPException as e:
        # se non hai balance → fallback a manual_review
        if e.status_code in (400, 402, 422):
            return {
                "quote_id": q["id"], "recipient_id": r["id"],
                "transfer_id": t["id"],
                "fund_status": {"fallback": True, "status": "manual_review", "reason": e.detail},
            }
        raise
    return {"quote_id": q["id"], "recipient_id": r["id"], "transfer_id": t["id"], "fund_status": f}

# ============ Stripe helpers (CARD_VISA / CARD_MASTERCARD) ============
def stripe_headers():
    if not STRIPE_API_KEY:
        raise HTTPException(status_code=400, detail="STRIPE_API_KEY mancante")
    headers = {"Authorization": f"Bearer {STRIPE_API_KEY}"}
    if STRIPE_CONNECT_ACCOUNT:
        headers["Stripe-Account"] = STRIPE_CONNECT_ACCOUNT
    return headers

def stripe_card_payout(card_token: str, amount_eur: float, note: str, scheme: str):
    """
    Payout su carta tramite Stripe.
    ATTENZIONE: richiede capabilities (Payouts/Issuing/Transfers). In caso contrario Stripe risponderà 403/402.
    Questa implementazione usa l'endpoint /v1/payouts (saldo -> esterno). Per carte richiede setup lato account.
    """
    url = "https://api.stripe.com/v1/payouts"
    payload = {
        "amount": cents(amount_eur),
        "currency": STRIPE_CURRENCY,
        # metadata utili al tracciamento
        "metadata[scheme]": scheme,
        "metadata[note]": (note or "OTC payout")[:50],
    }
    # NB: il collegamento del "destination" (es. carta) dipende dalla tua configurazione account/connected.
    # Se serve un connected account: headers includono 'Stripe-Account'
    r = requests.post(url, data=payload, headers=stripe_headers(), timeout=30)
    http_raise(r)
    return r.json()

# ==========================================================
#  🧩 ROUTES
# ==========================================================
@app.get("/")
def root():
    return {"ok": True, "service": SERVICE_NAME, "provider": PROVIDER}

@app.get("/health")
def health():
    return {
        "ok": True,
        "service": SERVICE_NAME,
        "provider": PROVIDER,
        "endpoints": ["/otc/set-price", "/otc/price/{token}", "/offramp/create-order",
                      "/offramp/trigger-payout/{order_id}", "/payouts", "/payouts/{payout_id}"],
        "stripe_connected": bool(STRIPE_API_KEY),
        "nowpayments": bool(NP_API_KEY),
        "wise": bool(WISE_API_TOKEN and WISE_PROFILE_ID),
    }

# ---------- OTC ----------
@app.post("/otc/set-price")
def set_price(data: SetPriceIn):
    sym = data.token_symbol.upper()
    LISTINGS[sym] = {
        "token_symbol": sym,
        "price_eur": data.price_eur,
        "available_amount": data.available_amount,
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "auto_payout_txid": None,
    }
    return {
        "ok": True,
        "token_symbol": sym,
        "price_eur": data.price_eur,
        "available_amount": data.available_amount,
        "updated_at": LISTINGS[sym]["updated_at"],
        "auto_payout_txid": LISTINGS[sym]["auto_payout_txid"],
    }

@app.get("/otc/price/{token_symbol}")
def get_price(token_symbol: str):
    sym = token_symbol.upper()
    if sym not in LISTINGS:
        raise HTTPException(status_code=404, detail="Token not listed")
    return LISTINGS[sym]

# ---------- CREATE ORDER ----------
@app.post("/offramp/create-order")
def create_order(data: CreateOrderIn):
    order_id = len(ORDERS) + 1
    eur_amount = data.amount_tokens * data.price_eur
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%S")
    ORDERS[order_id] = {
        "order_id": order_id,
        "status": "created",
        "token_symbol": data.token_symbol.upper(),
        "amount_tokens": data.amount_tokens,
        "price_eur": data.price_eur,
        "eur_amount": eur_amount,
        "payout_channel": (data.payout_channel or "").upper() or None,
        "beneficiary_name": data.beneficiary_name,
        "iban": data.iban,
        "card_token": data.card_token,
        "wallet_address": data.wallet_address,
        "crypto_asset": (data.crypto_asset or "").upper() if data.crypto_asset else "",
        "crypto_network": (data.crypto_network or "").upper() if data.crypto_network else "",
        "created_at": now_iso,
        "notes": data.notes or "",
        "payout_txid": None,
    }
    return {
        "order_id": order_id,
        "status": "created",
        "eur_amount": eur_amount,
        "price_eur": data.price_eur,
        "token_symbol": data.token_symbol.upper(),
    }

@app.get("/offramp/orders/{order_id}")
def get_order(order_id: int):
    order = ORDERS.get(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    return order

# ---------- LEGACY TRIGGER (CRYPTO) ----------
@app.post("/offramp/trigger-payout/{order_id}")
def trigger_payout_legacy(order_id: int, payload: OfframpPayoutLegacyIn):
    """
    Endpoint legacy: supporta solo CRYPTO in questa build.
    Usa /payouts per CARD_VISA/CARD_MASTERCARD o SEPA.
    """
    if payload.method != "CRYPTO":
        raise HTTPException(status_code=400, detail="Only CRYPTO payout supported in this endpoint")

    order = ORDERS.get(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    # parametri crypto
    asset = (payload.crypto_asset or order.get("crypto_asset") or "").upper()
    network = (payload.crypto_network or order.get("crypto_network") or "").upper()
    wallet = payload.wallet_address or order.get("wallet_address")
    if not (NP_API_KEY and asset and wallet):
        raise HTTPException(status_code=400, detail="NP_API_KEY, crypto_asset, wallet_address richiesti")

    np_payload = {
        "order_id": order_id,
        "asset": asset,
        "network": network or None,
        "amount_fiat": order["eur_amount"],
        "fiat_currency": "EUR",
        "destination_address": wallet,
        "idempotency_key": f"payout-{order_id}",
    }
    resp = np_create_payout(np_payload)
    order["status"] = "queued"
    order["payout_txid"] = resp.get("id") or resp.get("payout_id")
    return {"ok": True, "provider": "nowpayments", "order_id": order_id, "status": "queued", "response": resp}

# ---------- NEW: /payouts (CARD / SEPA / CRYPTO) ----------
@app.post("/payouts")
def create_payout(data: PayoutCreateIn):
    order = ORDERS.get(data.order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    payout_id = str(uuid.uuid4())
    PAYOUTS[payout_id] = {
        "payout_id": payout_id,
        "order_id": data.order_id,
        "channel": data.channel,
        "status": "queued",
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "error": None,
        "provider_response": None,
    }

    amount_eur = float(order["eur_amount"])
    note = data.note or order.get("notes") or "OTC payout"

    try:
        if data.channel in ("CARD_VISA", "CARD_MASTERCARD"):
            if not data.card_token and not order.get("card_token"):
                raise HTTPException(status_code=400, detail="card_token richiesto per payout su carta")
            # NB: il token è lato client; qui viene usato solo come riferimento/metadato.
            # La chiamata reale dipende dalle tue capabilities Stripe.
            scheme = "VISA" if data.channel == "CARD_VISA" else "MASTERCARD"
            resp = stripe_card_payout(card_token=(data.card_token or order.get("card_token")),
                                      amount_eur=amount_eur, note=note, scheme=scheme)
            PAYOUTS[payout_id].update({"status": "processing", "provider_response": resp})
            order["status"] = "processing"

        elif data.channel == "SEPA":
            name = data.beneficiary_name or order.get("beneficiary_name")
            iban = data.iban or order.get("iban")
            if not (name and iban):
                raise HTTPException(status_code=400, detail="beneficiary_name e iban richiesti per SEPA")
            resp = wise_payout(name, iban, amount_eur, note)
            # se manca balance -> manual_review, altrimenti processing
            status = "processing"
            if resp.get("fund_status", {}).get("fallback"):
                status = "manual_review"
            PAYOUTS[payout_id].update({"status": status, "provider_response": resp})
            order["status"] = status

        elif data.channel == "CRYPTO":
            asset = (data.crypto_asset or order.get("crypto_asset") or "").upper()
            wallet = data.wallet_address or order.get("wallet_address")
            network = (data.crypto_network or order.get("crypto_network") or "").upper() or None
            if not (NP_API_KEY and asset and wallet):
                raise HTTPException(status_code=400, detail="NP_API_KEY, crypto_asset, wallet_address richiesti")
            np_payload = {
                "order_id": data.order_id,
                "asset": asset,
                "network": network,
                "amount_fiat": amount_eur,
                "fiat_currency": "EUR",
                "destination_address": wallet,
                "idempotency_key": f"payout-{data.order_id}",
            }
            resp = np_create_payout(np_payload)
            PAYOUTS[payout_id].update({"status": "processing", "provider_response": resp})
            order["status"] = "processing"

        else:
            raise HTTPException(status_code=400, detail=f"Canale non supportato: {data.channel}")

    except HTTPException as e:
        PAYOUTS[payout_id].update({"status": "failed", "error": e.detail})
        order["status"] = "failed"
        raise
    except Exception as e:
        PAYOUTS[payout_id].update({"status": "failed", "error": {"message": str(e)}})
        order["status"] = "failed"
        raise HTTPException(status_code=500, detail={"message": str(e)})

    return {"payout_id": payout_id, "order_id": data.order_id, "status": PAYOUTS[payout_id]["status"]}

@app.get("/payouts/{payout_id}")
def get_payout(payout_id: str):
    p = PAYOUTS.get(payout_id)
    if not p:
        raise HTTPException(status_code=404, detail="Payout not found")
    return p
