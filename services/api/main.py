# services/api/main.py
import os
import requests
from fastapi import FastAPI, HTTPException, Body
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, Dict

# ==========================================================
#  🔧 CONFIGURAZIONE
# ==========================================================
SERVICE_NAME = "changenow-offramp-pro"
PROVIDER = os.getenv("PROVIDER", "wise").lower()

# --- NOWPayments (crypto payouts) ---
NP_BASE_URL    = os.getenv("NP_BASE_URL", "https://api.nowpayments.io/v1").rstrip("/")
NP_PAYOUT_PATH = os.getenv("NP_PAYOUT_PATH", "/payouts")
NP_USE_JWT     = os.getenv("NP_USE_JWT", "false").lower() == "true"
NP_API_KEY     = os.getenv("NP_API_KEY")

# --- Wise (SEPA payouts) ---
WISE_API_TOKEN       = os.getenv("WISE_API_TOKEN")
WISE_PROFILE_ID      = os.getenv("WISE_PROFILE_ID")  # id numerico profilo business
WISE_SOURCE_CURRENCY = os.getenv("WISE_SOURCE_CURRENCY", "EUR")
# opzionale: override esplicito (live/sandbox)
WISE_BASE_URL_ENV    = os.getenv("WISE_BASE_URL")  # es. https://api.transferwise.com oppure https://api.sandbox.transferwise.tech

# ==========================================================
#  ⚙️ FASTAPI
# ==========================================================
app = FastAPI(title=SERVICE_NAME, version="1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# “DB” in memoria (sostituisci con storage persistente se vuoi)
ORDERS: Dict[int, Dict] = {}
LISTINGS: Dict[str, Dict] = {}

# ==========================================================
#  📦 MODELLI
# ==========================================================
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
    method: str = "SEPA"  # "SEPA" -> Wise, "CRYPTO" -> NOWPayments

# ==========================================================
#  🪙 NOWPAYMENTS (CRYPTO PAYOUTS)
# ==========================================================
def np_headers():
    if NP_USE_JWT:
        raise RuntimeError("JWT non abilitato: usa NP_USE_JWT=false e x-api-key")
    return {"x-api-key": NP_API_KEY, "Content-Type": "application/json"}

def create_payout(payload: dict):
    """
    Tenta prima NP_PAYOUT_PATH, poi fallback automatico all'altro (/payout <-> /payouts).
    Espone gli errori != 404, e se entrambi 404 solleva HTTP 404 con dettaglio.
    """
    base = NP_BASE_URL
    headers = np_headers()
    idem = payload.get("idempotency_key", f"np-{payload.get('order_id','')}")
    headers["Idempotency-Key"] = idem

    primary = f"{base}{NP_PAYOUT_PATH}"
    alternate = f"{base}/payout" if NP_PAYOUT_PATH.rstrip("/") == "/payouts" else f"{base}/payouts"

    for url in (primary, alternate):
        r = requests.post(url, json=payload, headers=headers, timeout=30)
        if r.status_code == 404:
            continue
        try:
            r.raise_for_status()
            return r.json()
        except Exception as e:
            raise HTTPException(status_code=r.status_code, detail={"message": str(e), "response": r.text})
    raise HTTPException(status_code=404, detail={"message": "Payout endpoint not found", "tried": [primary, alternate]})

# ==========================================================
#  💶 WISE (SEPA PAYOUTS) — PATCH ROBUSTA
# ==========================================================
def wise_base_url() -> str:
    # default live; se usi sandbox: export WISE_BASE_URL=https://api.sandbox.transferwise.tech
    base = (WISE_BASE_URL_ENV or "https://api.transferwise.com").rstrip("/")
    return base + "/v1"

def wise_headers() -> dict:
    return {"Authorization": f"Bearer {WISE_API_TOKEN}", "Content-Type": "application/json"}

def _post_json(url: str, payload: dict) -> dict:
    r = requests.post(url, json=payload, headers=wise_headers(), timeout=30)
    if r.status_code >= 400:
        # Espone il messaggio Wise per diagnosi immediata
        raise HTTPException(status_code=r.status_code, detail={"message": r.text})
    return r.json()

def wise_create_quote(amount_eur: float) -> dict:
    """
    Tenta schema 'sourceCurrency/targetCurrency' e, in caso di 400, prova 'source/target'.
    Aggiunge rateType/payOut/preferredPayIn per massimizzare compatibilità.
    """
    url = f"{wise_base_url()}/quotes"

    payload_a = {
        "profile": int(WISE_PROFILE_ID),
        "sourceCurrency": WISE_SOURCE_CURRENCY,
        "targetCurrency": "EUR",
        "sourceAmount": amount_eur,
        "rateType": "FIXED",
        "payOut": "BANK_TRANSFER",
        "preferredPayIn": "BALANCE",
    }
    try:
        return _post_json(url, payload_a)
    except HTTPException as e:
        if e.status_code != 400:
            raise

    payload_b = {
        "profile": int(WISE_PROFILE_ID),
        "source": WISE_SOURCE_CURRENCY,
        "target": "EUR",
        "sourceAmount": amount_eur,
        "rateType": "FIXED",
        "payOut": "BANK_TRANSFER",
        "preferredPayIn": "BALANCE",
    }
    return _post_json(url, payload_b)

def wise_create_recipient(name: str, iban: str) -> dict:
    url = f"{wise_base_url()}/accounts"
    payload = {
        "currency": "EUR",
        "type": "iban",
        "profile": int(WISE_PROFILE_ID),
        "ownedByCustomer": False,
        "details": {
            "legalType": "PRIVATE",
            "IBAN": iban,
            "accountHolderName": name,
        },
    }
    return _post_json(url, payload)

def wise_create_transfer(quote_id: str, recipient_id: int, amount_eur: float, note: str) -> dict:
    url = f"{wise_base_url()}/transfers"
    payload = {
        "targetAccount": recipient_id,
        "quoteUuid": quote_id,
        "customerTransactionId": f"tx-{recipient_id}-{int(amount_eur * 100)}",
        "details": {
            "reference": note[:35],  # riferimento corto accettato da molti istituti
            "transferPurpose": "verification.transfers.payout",
            "sourceOfFunds": "other",
        },
    }
    return _post_json(url, payload)

def wise_fund_transfer(transfer_id: int) -> dict:
    url = f"{wise_base_url()}/transfers/{transfer_id}/payments"
    payload = {"type": "BALANCE"}
    return _post_json(url, payload)

def wise_payout(name: str, iban: str, amount_eur: float, note: str) -> dict:
    quote = wise_create_quote(amount_eur)
    recipient = wise_create_recipient(name, iban)
    transfer = wise_create_transfer(quote["id"], recipient["id"], amount_eur, note)
    fund = wise_fund_transfer(transfer["id"])
    return {
        "quote_id": quote["id"],
        "recipient_id": recipient["id"],
        "transfer_id": transfer["id"],
        "fund_status": fund,
    }

# ==========================================================
#  🧩 ROTTE BASE & HEALTH
# ==========================================================
@app.get("/")
def root():
    return {"ok": True, "service": SERVICE_NAME, "provider": PROVIDER}

@app.get("/offramp/health")
def health(verbose: Optional[bool] = False):
    data = {"ok": True, "service": SERVICE_NAME, "provider": PROVIDER}
    if verbose:
        data.update({
            "NP_BASE_URL": NP_BASE_URL,
            "auth_mode": "api_key" if not NP_USE_JWT else "jwt",
            "WISE_BASE_URL": wise_base_url(),
            "WISE_PROFILE_ID": WISE_PROFILE_ID,
        })
    return data

@app.get("/nowpayments/health")
def nowpayments_health():
    try:
        h = np_headers()
        urls = [f"{NP_BASE_URL}/status", f"{NP_BASE_URL}/payouts"]
        for u in urls:
            try:
                r = requests.get(u, headers=h, timeout=10)
                if r.status_code in (200, 204, 401, 403, 404, 405):
                    return {"ok": True, "status_code": r.status_code, "checked": u, "auth_mode": "api_key"}
            except Exception:
                continue
        return {"ok": False, "status": "unreachable"}
    except Exception as e:
        return {"ok": False, "error": str(e)}

# ==========================================================
#  💰 OTC LISTINGS
# ==========================================================
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
    LISTINGS[sym] = {
        "price_eur": data.price_eur,
        "available_amount": data.available_amount,
    }
    return {"ok": True, "token": sym, "price_eur": data.price_eur, "available_amount": data.available_amount}

# ==========================================================
#  🧾 CREATE ORDER
# ==========================================================
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
        "redirect_url": data.redirect_url,
    }

# ==========================================================
#  💸 TRIGGER PAYOUT (WISE o NOWPAYMENTS)
# ==========================================================
@app.post("/offramp/trigger-payout/{order_id}")
def trigger_payout(order_id: str, payload: PayoutIn = Body(...)):
    try:
        order_key = int(order_id)
    except ValueError:
        order_key = order_id
    order = ORDERS.get(order_key)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    eur_amount = order["eur_amount"]
    name = order["beneficiary_name"]
    iban = order["iban"]
    note = f"OTC payout {order_key}"

    try:
        # Wise se provider=wise o method=SEPA
        if PROVIDER == "wise" or payload.method.upper() == "SEPA":
            if not WISE_API_TOKEN or not WISE_PROFILE_ID:
                raise HTTPException(status_code=400, detail="WISE_API_TOKEN / WISE_PROFILE_ID mancanti")
            response = wise_payout(name, iban, eur_amount, note)
            order["status"] = "queued"
            order["payout_response"] = response
            return {"ok": True, "provider": "wise", "order_id": order_key, "status": "queued", "response": response}

        # NOWPayments se provider=nowpayments o method=CRYPTO
        elif PROVIDER == "nowpayments" or payload.method.upper() == "CRYPTO":
            if not NP_API_KEY:
                raise HTTPException(status_code=400, detail="NP_API_KEY mancante")
            response = create_payout({
                "order_id": order_key,
                "currency": "EUR",
                "amount": eur_amount,
                # Nota: NOWPayments è crypto-oriented; questi campi sono mantenuti per compatibilità payload
                "payout_address": iban,
                "beneficiary_name": name,
                "method": payload.method,
                "idempotency_key": f"payout-{order_key}",
            })
            order["status"] = "queued"
            order["payout_response"] = response
            return {"ok": True, "provider": "nowpayments", "order_id": order_key, "status": "queued", "response": response}

        else:
            raise HTTPException(status_code=400, detail="Unsupported provider or method")

    except HTTPException:
        # Rilancia HTTPException senza alterare status code
        order["status"] = "failed"
        raise
    except Exception as e:
        order["status"] = "failed"
        raise HTTPException(status_code=500, detail={"message": str(e)})

# ==========================================================
#  📋 GET ORDER
# ==========================================================
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
