import os, uuid, time, requests
from typing import Dict, Optional
from fastapi import FastAPI, HTTPException, Body, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# ==========================================================
# Config & App
# ==========================================================
SERVICE_NAME = "changenow-offramp-pro"
PROVIDER = os.getenv("PROVIDER", "stripe").lower()   # default rail se non specificato in trigger

# Stripe (Card payouts via Connect external accounts)
STRIPE_API_KEY        = os.getenv("STRIPE_API_KEY", "")
STRIPE_CONNECT_ACCOUNT= os.getenv("STRIPE_CONNECT_ACCOUNT", "")  # opzionale
STRIPE_PAYOUT_SPEED   = os.getenv("STRIPE_PAYOUT_SPEED", "instant")  # instant|standard
STRIPE_API_BASE       = "https://api.stripe.com"

# Wise (SEPA)
WISE_API_TOKEN       = os.getenv("WISE_API_TOKEN", "")
WISE_PROFILE_ID      = os.getenv("WISE_PROFILE_ID", "")
WISE_SOURCE_CURRENCY = os.getenv("WISE_SOURCE_CURRENCY", "EUR")
WISE_BASE_URL_ENV    = os.getenv("WISE_BASE_URL", "https://api.transferwise.com").rstrip("/")

# NOWPayments (Crypto)
NP_API_KEY     = os.getenv("NP_API_KEY", "")
NP_BASE_URL    = os.getenv("NP_BASE_URL", "https://api.nowpayments.io/v1").rstrip("/")
NP_PAYOUT_PATH = os.getenv("NP_PAYOUT_PATH", "/payouts")  # verrà normalizzato sotto
NP_USE_JWT     = os.getenv("NP_USE_JWT", "false").lower() == "true"  # normalmente FALSE

# CORS
ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "*").split(",")

app = FastAPI(title=SERVICE_NAME, version="2.2.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS if ALLOWED_ORIGINS != ["*"] else ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==========================================================
# In-memory storage (sostituisci con DB in produzione)
# ==========================================================
ORDERS: Dict[int, Dict] = {}
LISTINGS: Dict[str, Dict] = {}

# ==========================================================
# Models
# ==========================================================
class SetPriceIn(BaseModel):
    token_symbol: str
    price_eur: float
    available_amount: Optional[int] = 0

class CreateOrderIn(BaseModel):
    token_symbol: str
    amount_tokens: float
    price_eur: float
    payout_channel: Optional[str] = None  # "CARD_VISA" | "CARD_MASTERCARD" | "SEPA" | "CRYPTO"
    # Card
    card_token: Optional[str] = None
    # SEPA
    beneficiary_name: Optional[str] = None
    iban: Optional[str] = None
    # Crypto
    wallet_address: Optional[str] = None
    crypto_asset: Optional[str] = None      # USDT|BTC|USDC|ETH|BNB|SOL
    crypto_network: Optional[str] = None    # TRC20|ERC20|BEP20|SOL|BTC...
    # Generali
    redirect_url: Optional[str] = None
    notes: Optional[str] = None

class PayoutIn(BaseModel):
    method: str = ""  # se vuoto, usa order.payout_channel o default PROVIDER
    # override opzionali
    card_token: Optional[str] = None
    beneficiary_name: Optional[str] = None
    iban: Optional[str] = None
    wallet_address: Optional[str] = None
    crypto_asset: Optional[str] = None
    crypto_network: Optional[str] = None

# ==========================================================
# Helpers comuni
# ==========================================================
def http_raise(r: requests.Response, extra: Optional[dict] = None):
    if r.status_code >= 400:
        detail = {
            "url": getattr(r.request, "url", ""),
            "status": r.status_code,
            "body": (r.text or "")[:2000]
        }
        if extra: detail.update(extra)
        raise HTTPException(status_code=r.status_code, detail=detail)

def status_async_transition(order_id: int):
    """Simula transizioni asincrone di stato (queued -> processing -> completed)."""
    try:
        time.sleep(1.5)
        ORDERS[order_id]["status"] = "processing"
        time.sleep(1.5)
        # Se il provider ha un errore registrato, fallisci
        if ORDERS[order_id].get("payout_error"):
            ORDERS[order_id]["status"] = "failed"
        else:
            ORDERS[order_id]["status"] = "completed"
    except Exception as e:
        ORDERS[order_id]["status"] = "failed"
        ORDERS[order_id]["payout_error"] = {"message": str(e)}

# ==========================================================
# Stripe (Card) helpers
# ==========================================================
def s_headers(account: Optional[str] = None) -> dict:
    if not STRIPE_API_KEY:
        raise HTTPException(400, "STRIPE_API_KEY missing")
    h = {"Authorization": f"Bearer {STRIPE_API_KEY}"}
    if account:
        h["Stripe-Account"] = account
    return h

def stripe_add_external_card(card_token: str) -> str:
    """Aggiungi la carta tokenizzata come external account sull'eventuale Connected Account."""
    if STRIPE_CONNECT_ACCOUNT:
        url = f"{STRIPE_API_BASE}/v1/accounts/{STRIPE_CONNECT_ACCOUNT}/external_accounts"
        r = requests.post(url, headers=s_headers(STRIPE_CONNECT_ACCOUNT),
                          data={"external_account": card_token}, timeout=30)
        http_raise(r)
        return r.json()["id"]  # "card_xxx"
    else:
        # In assenza di Connect, creiamo una "card token" utilizzabile per un Charge/PI (non payout direct).
        # Qui simuliamo external_id = token (per esempio di routing interno).
        return card_token

def stripe_create_payout_to_external(external_id: str, amount_eur: float, speed: str = None):
    """Crea payout su external account (richiede saldo e abilitazioni)."""
    speed = speed or STRIPE_PAYOUT_SPEED
    url = f"{STRIPE_API_BASE}/v1/payouts"
    r = requests.post(url, headers=s_headers(STRIPE_CONNECT_ACCOUNT or None), timeout=30, data={
        "amount": int(round(amount_eur * 100)),
        "currency": "eur",
        "method": speed,      # instant|standard
        "destination": external_id,
        "description": "OTC payout (card)"
    })
    http_raise(r)
    return r.json()

# ==========================================================
# Wise (SEPA) helpers
# ==========================================================
def wise_base_v1() -> str:
    return f"{WISE_BASE_URL_ENV}/v1"

def wise_headers() -> dict:
    if not WISE_API_TOKEN:
        raise HTTPException(400, "WISE_API_TOKEN missing")
    return {"Authorization": f"Bearer {WISE_API_TOKEN}", "Content-Type": "application/json"}

def wise_create_quote(amount_eur: float) -> dict:
    url = f"{wise_base_v1()}/quotes"
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
    # fallback legacy se necessario
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

def wise_create_recipient(name: str, iban: str) -> dict:
    url = f"{wise_base_v1()}/accounts"
    payload = {
        "currency": "EUR",
        "type": "iban",
        "profile": int(WISE_PROFILE_ID),
        "ownedByCustomer": False,
        "details": {"legalType": "PRIVATE", "IBAN": iban, "accountHolderName": name},
    }
    r = requests.post(url, json=payload, headers=wise_headers(), timeout=30)
    http_raise(r)
    return r.json()

def wise_create_transfer(quote_id: str, recipient_id: int, note: str) -> dict:
    url = f"{wise_base_v1()}/transfers"
    payload = {
        "targetAccount": recipient_id,
        "quoteUuid": quote_id,
        "customerTransactionId": str(uuid.uuid4()),  # deve essere UUID
        "details": {
            "reference": (note or "OTC payout")[:35],
            "transferPurpose": "verification.transfers.payout",
            "sourceOfFunds": "other",
        },
    }
    r = requests.post(url, json=payload, headers=wise_headers(), timeout=30)
    http_raise(r)
    return r.json()

def wise_fund_transfer(transfer_id: int) -> dict:
    url = f"{wise_base_v1()}/transfers/{transfer_id}/payments"
    payload = {"type": "BALANCE"}
    r = requests.post(url, json=payload, headers=wise_headers(), timeout=30)
    http_raise(r)
    return r.json()

def wise_payout(name: str, iban: str, amount_eur: float, note: str) -> dict:
    try:
        q = wise_create_quote(amount_eur)
    except HTTPException as e:
        raise HTTPException(e.status_code, {"stage":"quote", **e.detail})
    try:
        rcp = wise_create_recipient(name, iban)
    except HTTPException as e:
        raise HTTPException(e.status_code, {"stage":"recipient", **e.detail})
    try:
        tr = wise_create_transfer(q["id"], rcp["id"], note)
    except HTTPException as e:
        raise HTTPException(e.status_code, {"stage":"transfer", **e.detail})
    try:
        fd = wise_fund_transfer(tr["id"])
    except HTTPException as e:
        # se mancano fondi, ritorna stato "manual_review"
        if e.status_code in (400, 402, 422):
            return {
                "quote_id": q["id"], "recipient_id": rcp["id"],
                "transfer_id": tr["id"],
                "fund_status": {"fallback": True, "status": "manual_review", "reason": e.detail},
            }
        raise HTTPException(e.status_code, {"stage":"fund", **e.detail})
    return {
        "quote_id": q["id"], "recipient_id": rcp["id"],
        "transfer_id": tr["id"], "fund_status": fd,
    }

# ==========================================================
# NOWPayments (Crypto) helpers
# ==========================================================
def np_headers() -> dict:
    if NP_USE_JWT:
        raise HTTPException(400, "NP_USE_JWT=true non supportato in questa build (usa x-api-key).")
    if not NP_API_KEY:
        raise HTTPException(400, "NP_API_KEY missing")
    return {"x-api-key": NP_API_KEY, "Content-Type": "application/json"}

def np_try_payout(payload: dict) -> dict:
    """Prova più endpoint noti, perché la disponibilità varia per account."""
    base = NP_BASE_URL
    paths = [
        NP_PAYOUT_PATH,                 # es. /payouts
        "/payouts", "/payout", "/mass-payout", "/payments", "/withdrawal"
    ]
    tried = []
    for p in paths:
        url = f"{base}{p if p.startswith('/') else '/'+p}"
        tried.append(url)
        r = requests.post(url, json=payload, headers=np_headers(), timeout=30)
        if r.status_code == 404:
            continue
        http_raise(r)
        return r.json()
    raise HTTPException(404, {"message":"NP payout endpoint not found", "tried": tried})

def np_payout(asset: str, address: str, amount_eur: float, network: Optional[str], order_id: int) -> dict:
    payload = {
        "asset": asset.upper(),
        "amount_fiat": float(amount_eur),
        "fiat_currency": "EUR",
        "destination_address": address,
        "network": (network or "").upper() or None,
        "idempotency_key": f"payout-{order_id}",
        "order_id": order_id
    }
    return np_try_payout(payload)

# ==========================================================
# Routes
# ==========================================================
@app.get("/health")
def health():
    return {
        "ok": True,
        "service": SERVICE_NAME,
        "provider_default": PROVIDER,
        "rails": {
            "card(stripe)": bool(STRIPE_API_KEY),
            "sepa(wise)": bool(WISE_API_TOKEN and WISE_PROFILE_ID),
            "crypto(nowpayments)": bool(NP_API_KEY),
        }
    }

# --- OTC listing ---
@app.post("/otc/set-price")
def set_price(data: SetPriceIn):
    sym = data.token_symbol.upper()
    LISTINGS[sym] = {"price_eur": data.price_eur, "available_amount": data.available_amount, "updated": int(time.time())}
    return {"ok": True, "token": sym, "price_eur": data.price_eur, "available_amount": data.available_amount}

# --- Create order ---
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
        "payout_channel": (data.payout_channel or "").upper() or None,
        # Card
        "card_token": data.card_token,
        # SEPA
        "beneficiary_name": data.beneficiary_name,
        "iban": data.iban,
        # Crypto
        "wallet_address": data.wallet_address,
        "crypto_asset": (data.crypto_asset or "").upper() or None,
        "crypto_network": (data.crypto_network or "").upper() or None,
        # Generic
        "eur_amount": eur_amount,
        "redirect_url": data.redirect_url,
        "notes": data.notes,
        "created": int(time.time()),
    }
    return {"order_id": new_id, "status": "created", "eur_amount": eur_amount, "price_eur": data.price_eur, "token_symbol": data.token_symbol}

# --- Trigger payout ---
@app.post("/offramp/trigger-payout/{order_id}")
def trigger_payout(order_id: str, payload: PayoutIn = Body(...), bg: BackgroundTasks = None):
    # fetch order
    try:
        key = int(order_id)
    except ValueError:
        raise HTTPException(400, "order_id must be integer")
    order = ORDERS.get(key)
    if not order:
        raise HTTPException(404, "Order not found")

    # rail effettiva
    method = (payload.method or order.get("payout_channel") or "").upper()
    if not method:
        # fallback: default provider
        method = {"stripe":"CARD_VISA", "wise":"SEPA", "nowpayments":"CRYPTO"}.get(PROVIDER, "CARD_VISA")

    eur_amount = float(order["eur_amount"])
    note = order.get("notes") or f"OTC payout {key}"

    try:
        if method in ("CARD_VISA", "CARD_MASTERCARD"):
            token = payload.card_token or order.get("card_token")
            if not token:
                raise HTTPException(400, "card_token required for card payout")
            external_id = stripe_add_external_card(token)
            # crea payout (richiede saldo/abilitazioni)
            payout = stripe_create_payout_to_external(external_id, eur_amount, STRIPE_PAYOUT_SPEED)
            order["status"] = "queued"
            order["payout_response"] = {"external_id": external_id, "payout": payout}
            if bg: bg.add_task(status_async_transition, key)
            return {"ok": True, "provider": "stripe", "order_id": key, "status": "queued", "response": order["payout_response"]}

        elif method == "SEPA":
            name = (payload.beneficiary_name or order.get("beneficiary_name"))
            iban = (payload.iban or order.get("iban"))
            if not (name and iban):
                raise HTTPException(400, "beneficiary_name and iban required for SEPA")
            resp = wise_payout(name, iban, eur_amount, note)
            # se funding fallisce per fondi insufficienti → manual_review
            if resp.get("fund_status", {}).get("fallback"):
                order["status"] = "manual_review"
            else:
                order["status"] = "queued"
                if bg: bg.add_task(status_async_transition, key)
            order["payout_response"] = resp
            return {"ok": True, "provider": "wise", "order_id": key, "status": order["status"], "response": resp}

        elif method == "CRYPTO":
            asset  = (payload.crypto_asset or order.get("crypto_asset") or "").upper()
            wallet = payload.wallet_address or order.get("wallet_address")
            net    = (payload.crypto_network or order.get("crypto_network") or None)
            if not (NP_API_KEY and asset and wallet):
                raise HTTPException(400, "NP_API_KEY, crypto_asset and wallet_address required for CRYPTO payout")
            resp = np_payout(asset, wallet, eur_amount, net, key)
            order["status"] = "queued"
            order["payout_response"] = resp
            if bg: bg.add_task(status_async_transition, key)
            return {"ok": True, "provider": "nowpayments", "order_id": key, "status": "queued", "response": resp}

        else:
            raise HTTPException(400, f"Unsupported payout method: {method}")

    except HTTPException as e:
        order["status"] = "failed"
        order["payout_error"] = e.detail
        raise
    except Exception as e:
        order["status"] = "failed"
        order["payout_error"] = {"message": str(e)}
        raise HTTPException(500, {"message": str(e)})

# --- Get order ---
@app.get("/offramp/orders/{order_id}")
def get_order(order_id: str):
    try:
        key = int(order_id)
    except ValueError:
        raise HTTPException(400, "order_id must be integer")
    order = ORDERS.get(key)
    if not order:
        raise HTTPException(404, "Order not found")
    return order
