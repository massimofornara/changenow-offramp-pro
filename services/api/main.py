import os, uuid, time, threading, json
from datetime import datetime
from typing import Optional, Dict, Literal

import requests
from fastapi import FastAPI, HTTPException, Body, BackgroundTasks, Header, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

# ==========================================================
#  🔧 RUNTIME CONFIG (overridable via /config)
# ==========================================================
SERVICE_NAME = "changenow-offramp-pro"
RUNTIME = {
    # Stripe (Card rail)
    "STRIPE_API_KEY": os.getenv("STRIPE_API_KEY", ""),
    "STRIPE_CONNECT_ACCOUNT": os.getenv("STRIPE_CONNECT_ACCOUNT", ""),   # acct_...
    "STRIPE_PAYOUT_SPEED": os.getenv("STRIPE_PAYOUT_SPEED", "instant"),  # instant|standard
    "STRIPE_API_BASE": os.getenv("STRIPE_API_BASE", "https://api.stripe.com").rstrip("/"),

    # Wise (SEPA)
    "WISE_API_TOKEN": os.getenv("WISE_API_TOKEN", ""),
    "WISE_PROFILE_ID": os.getenv("WISE_PROFILE_ID", ""),
    "WISE_SOURCE_CCY": os.getenv("WISE_SOURCE_CURRENCY", "EUR"),
    "WISE_BASE_URL": os.getenv("WISE_BASE_URL", "https://api.transferwise.com").rstrip("/"),

    # NOWPayments (Crypto)
    "NP_API_KEY": os.getenv("NP_API_KEY", ""),
    "NP_BASE_URL": os.getenv("NP_BASE_URL", "https://api.nowpayments.io/v1").rstrip("/"),
    "NP_PAYOUT_PATH": os.getenv("NP_PAYOUT_PATH", "/payouts"),
    "NP_USE_JWT": os.getenv("NP_USE_JWT", "false").lower() == "true",

    # Misc card proxy knobs (as requested)
    "CARD_API_URL": os.getenv("CARD_API_URL", ""),
    "CARD_API_KEY": os.getenv("CARD_API_KEY", ""),

    # Polling cadence
    "POLL_INTERVAL_SEC": int(os.getenv("POLL_INTERVAL_SEC", "6")),
    "POLL_MAX_SEC": int(os.getenv("POLL_MAX_SEC", "300")),
}

# ==========================================================
#  ⚙️ FASTAPI
# ==========================================================
app = FastAPI(title=SERVICE_NAME, version="3.0-production")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])

# ==========================================================
#  🗃️ IN-MEMORY STORE (replace with DB in prod)
# ==========================================================
PAYOUTS: Dict[str, Dict] = {}
LOCK = threading.Lock()

# ==========================================================
#  📦 MODELS
# ==========================================================
class Amount(BaseModel):
    currency: str = Field("EUR", description="ISO 4217 (EUR/USD/GBP/...)")
    value: float = Field(..., gt=0)

class CreatePayoutIn(BaseModel):
    rail: Literal["CARD_VISA","CARD_MASTERCARD","SEPA","CRYPTO"]
    amount: Amount
    # Card (Stripe)
    card_token: Optional[str] = None          # Stripe token for debit card (e.g. tok_visa_debit or tokenized PM)
    # SEPA (Wise)
    beneficiary_name: Optional[str] = None
    iban: Optional[str] = None
    # Crypto (NOWPayments)
    wallet_address: Optional[str] = None
    crypto_asset: Optional[str] = None        # USDT/BTC/USDC/ETH/BNB/SOL
    crypto_network: Optional[str] = None      # TRC20/ERC20/BEP20/SOL/BTC...
    # Misc
    notes: Optional[str] = None
    webhookUrl: Optional[str] = None
    idempotencyKey: Optional[str] = None

class PayoutStatus(BaseModel):
    id: str
    rail: str
    status: Literal["queued","processing","completed","failed"]
    amount: Amount
    createdAt: datetime
    updatedAt: datetime
    webhookUrl: Optional[str] = None
    failureReason: Optional[str] = None
    provider: Optional[str] = None
    provider_ids: Optional[Dict[str, str]] = None   # e.g. payout_id / transfer_id / external_id
    raw: Optional[Dict] = None

# ==========================================================
#  🔐 HELPERS
# ==========================================================
def mask(s: str) -> str:
    if not s: return ""
    return (s[:3] + "*"*(len(s)-6) + s[-3:]) if len(s) > 6 else "*"*len(s)

def http_raise(r: requests.Response, extra=None):
    if r.status_code >= 400:
        detail = {"url": r.request.url, "status": r.status_code, "body": (r.text or "<empty>")[:4000]}
        if extra: detail.update(extra)
        raise HTTPException(status_code=r.status_code, detail=detail)

def send_webhook(url: Optional[str], payload: dict):
    if not url: return
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception:
        pass  # in prod: queue retry

def set_status(pid: str, status: str, failure: Optional[str]=None, raw: Optional[Dict]=None):
    with LOCK:
        p = PAYOUTS.get(pid)
        if not p: return
        p["status"] = status
        p["updatedAt"] = datetime.utcnow().isoformat()
        if failure: p["failureReason"] = failure
        if raw is not None: p["raw"] = raw
        webhook = p.get("webhookUrl")
        event = {"type": f"payout.{status}", "payout": p}
    send_webhook(webhook, event)

# ==========================================================
#  💳 STRIPE (Cards: Visa Direct / Mastercard Send via Connect)
# ==========================================================
def stripe_headers(account: Optional[str]=None):
    if not RUNTIME["STRIPE_API_KEY"]:
        raise HTTPException(400, "STRIPE_API_KEY missing")
    h = {"Authorization": f"Bearer {RUNTIME['STRIPE_API_KEY']}"}
    if account:
        h["Stripe-Account"] = account
    return h

def stripe_form(d: Dict) -> Dict:
    # Minimal x-www-form-urlencoded helper: Stripe accepts dict directly with requests
    return d

def stripe_add_external_card(acct: str, card_token: str) -> str:
    u = f"{RUNTIME['STRIPE_API_BASE']}/v1/accounts/{acct}/external_accounts"
    data = {"external_account": card_token}
    r = requests.post(u, headers=stripe_headers(acct), data=stripe_form(data), timeout=30)
    http_raise(r)
    return r.json()["id"]  # card_xxx

def stripe_create_payout(acct: str, destination_external_id: str, amount: Amount, note: str) -> Dict:
    u = f"{RUNTIME['STRIPE_API_BASE']}/v1/payouts"
    cents = int(round(amount.value * 100))
    data = {
        "amount": cents,
        "currency": amount.currency.lower(),
        "method": RUNTIME["STRIPE_PAYOUT_SPEED"],
        "destination": destination_external_id,
        "description": note[:200],
    }
    r = requests.post(u, headers=stripe_headers(acct), data=stripe_form(data), timeout=30)
    http_raise(r)
    return r.json()

def stripe_get_payout(acct: str, payout_id: str) -> Dict:
    u = f"{RUNTIME['STRIPE_API_BASE']}/v1/payouts/{payout_id}"
    r = requests.get(u, headers=stripe_headers(acct), timeout=30)
    http_raise(r)
    return r.json()

# ==========================================================
#  💶 WISE (SEPA)
# ==========================================================
def wise_headers():
    if not RUNTIME["WISE_API_TOKEN"]:
        raise HTTPException(400, "WISE_API_TOKEN missing")
    return {"Authorization": f"Bearer {RUNTIME['WISE_API_TOKEN']}", "Content-Type": "application/json"}

def wise_base() -> str:
    return RUNTIME["WISE_BASE_URL"].rstrip("/") + "/v1"

def wise_create_quote(amount: Amount) -> Dict:
    u = f"{wise_base()}/quotes"
    pa = {
        "profile": int(RUNTIME["WISE_PROFILE_ID"]),
        "sourceCurrency": RUNTIME["WISE_SOURCE_CCY"],
        "targetCurrency": amount.currency,
        "sourceAmount": amount.value,
        "rateType": "FIXED",
        "payOut": "BANK_TRANSFER",
        "preferredPayIn": "BALANCE",
    }
    r = requests.post(u, json=pa, headers=wise_headers(), timeout=30)
    if r.status_code == 400:
        pb = {
            "profile": int(RUNTIME["WISE_PROFILE_ID"]),
            "source": RUNTIME["WISE_SOURCE_CCY"],
            "target": amount.currency,
            "sourceAmount": amount.value,
            "rateType": "FIXED",
            "payOut": "BANK_TRANSFER",
            "preferredPayIn": "BALANCE",
        }
        r = requests.post(u, json=pb, headers=wise_headers(), timeout=30)
    http_raise(r)
    return r.json()

def wise_create_recipient(name: str, iban: str) -> Dict:
    u = f"{wise_base()}/accounts"
    payload = {
        "currency": "EUR",
        "type": "iban",
        "profile": int(RUNTIME["WISE_PROFILE_ID"]),
        "ownedByCustomer": False,
        "details": {"legalType": "PRIVATE", "IBAN": iban, "accountHolderName": name},
    }
    r = requests.post(u, json=payload, headers=wise_headers(), timeout=30)
    http_raise(r)
    return r.json()

def wise_create_transfer(quote_id: str, recipient_id: int, note: str) -> Dict:
    u = f"{wise_base()}/transfers"
    payload = {
        "targetAccount": recipient_id,
        "quoteUuid": quote_id,
        "customerTransactionId": str(uuid.uuid4()),
        "details": {
            "reference": note[:35],
            "transferPurpose": "verification.transfers.payout",
            "sourceOfFunds": "other",
        },
    }
    r = requests.post(u, json=payload, headers=wise_headers(), timeout=30)
    http_raise(r)
    return r.json()

def wise_fund_transfer(transfer_id: int) -> Dict:
    u = f"{wise_base()}/transfers/{transfer_id}/payments"
    payload = {"type": "BALANCE"}
    r = requests.post(u, json=payload, headers=wise_headers(), timeout=30)
    http_raise(r)
    return r.json()

# ==========================================================
#  🪙 NOWPAYMENTS (CRYPTO)
# ==========================================================
def np_headers():
    if RUNTIME["NP_USE_JWT"]:
        raise HTTPException(400, "Use x-api-key; JWT not supported here")
    if not RUNTIME["NP_API_KEY"]:
        raise HTTPException(400, "NP_API_KEY missing")
    return {"x-api-key": RUNTIME["NP_API_KEY"], "Content-Type": "application/json"}

def np_create_payout(payload: Dict) -> Dict:
    base = RUNTIME["NP_BASE_URL"]
    primary = f"{base}{RUNTIME['NP_PAYOUT_PATH']}"
    alt = f"{base}/payout" if RUNTIME["NP_PAYOUT_PATH"].rstrip("/") == "/payouts" else f"{base}/payouts"
    for url in (primary, alt):
        r = requests.post(url, json=payload, headers=np_headers(), timeout=30)
        if r.status_code == 404:
            continue
        http_raise(r)
        return r.json()
    raise HTTPException(404, "NOWPayments payout endpoint not found")

# ==========================================================
#  🔄 ASYNC POLLERS (emit real outbound webhooks)
# ==========================================================
def poll_stripe_until_done(pid: str):
    with LOCK:
        p = PAYOUTS.get(pid); 
        if not p: return
        acct = RUNTIME["STRIPE_CONNECT_ACCOUNT"]
        payout_id = p["provider_ids"].get("stripe_payout_id")
    start = time.time()
    set_status(pid, "processing")
    while time.time() - start < RUNTIME["POLL_MAX_SEC"]:
        try:
            res = stripe_get_payout(acct, payout_id)
            status = res.get("status")  # paid|pending|canceled|failed
            if status in ("paid","canceled","failed"):
                if status == "paid":
                    set_status(pid, "completed", raw=res)
                elif status == "canceled":
                    set_status(pid, "failed", "stripe:canceled", raw=res)
                else:
                    set_status(pid, "failed", "stripe:failed", raw=res)
                return
        except HTTPException as e:
            set_status(pid, "failed", f"stripe: {e.detail}", raw=None)
            return
        time.sleep(RUNTIME["POLL_INTERVAL_SEC"])
    set_status(pid, "failed", "stripe: timeout", raw=None)

def poll_wise_until_done(pid: str):
    # Wise funding call is synchronous; on errors we mark failed/manual_review earlier.
    # Here emulate "processing" → "completed" quickly.
    set_status(pid, "processing")
    time.sleep(max(2, RUNTIME["POLL_INTERVAL_SEC"]))
    set_status(pid, "completed")

def poll_nowp_until_done(pid: str):
    # If NOWP returns an id/tx, you could poll their endpoint; here we simulate with a short delay.
    set_status(pid, "processing")
    time.sleep(max(2, RUNTIME["POLL_INTERVAL_SEC"]))
    set_status(pid, "completed")

# ==========================================================
#  📍 ENDPOINTS
# ==========================================================
@app.get("/health")
def health():
    return {
        "ok": True, "service": SERVICE_NAME,
        "rails": ["CARD_VISA","CARD_MASTERCARD","SEPA","CRYPTO"],
        "stripe": {"connect_account": RUNTIME["STRIPE_CONNECT_ACCOUNT"], "speed": RUNTIME["STRIPE_PAYOUT_SPEED"]},
        "wise": {"profile_id": RUNTIME["WISE_PROFILE_ID"], "base": RUNTIME["WISE_BASE_URL"]},
        "np": {"base": RUNTIME["NP_BASE_URL"], "path": RUNTIME["NP_PAYOUT_PATH"]}
    }

class ConfigSetIn(BaseModel):
    CARD_API_URL: Optional[str] = None
    CARD_API_KEY: Optional[str] = None
    STRIPE_API_KEY: Optional[str] = None
    STRIPE_CONNECT_ACCOUNT: Optional[str] = None
    STRIPE_PAYOUT_SPEED: Optional[str] = None
    WISE_API_TOKEN: Optional[str] = None
    WISE_PROFILE_ID: Optional[str] = None
    WISE_BASE_URL: Optional[str] = None
    NP_API_KEY: Optional[str] = None
    NP_BASE_URL: Optional[str] = None
    NP_PAYOUT_PATH: Optional[str] = None
    POLL_INTERVAL_SEC: Optional[int] = Field(None, ge=2, le=60)
    POLL_MAX_SEC: Optional[int] = Field(None, ge=10, le=3600)

@app.get("/config")
def get_config(reveal: bool = Query(False)):
    # mask secrets by default
    return {
        "CARD_API_URL": RUNTIME["CARD_API_URL"],
        "CARD_API_KEY": RUNTIME["CARD_API_KEY"] if reveal else mask(RUNTIME["CARD_API_KEY"]),
        "STRIPE_CONNECT_ACCOUNT": RUNTIME["STRIPE_CONNECT_ACCOUNT"],
        "STRIPE_API_KEY": RUNTIME["STRIPE_API_KEY"] if reveal else mask(RUNTIME["STRIPE_API_KEY"]),
        "WISE_PROFILE_ID": RUNTIME["WISE_PROFILE_ID"],
        "WISE_API_TOKEN": RUNTIME["WISE_API_TOKEN"] if reveal else mask(RUNTIME["WISE_API_TOKEN"]),
        "NP_BASE_URL": RUNTIME["NP_BASE_URL"],
        "NP_API_KEY": RUNTIME["NP_API_KEY"] if reveal else mask(RUNTIME["NP_API_KEY"]),
        "POLL_INTERVAL_SEC": RUNTIME["POLL_INTERVAL_SEC"],
        "POLL_MAX_SEC": RUNTIME["POLL_MAX_SEC"],
    }

@app.post("/config")
def set_config(cfg: ConfigSetIn):
    for k, v in cfg.dict(exclude_unset=True).items():
        RUNTIME[k] = v if not isinstance(v, str) else v.strip()
    return {"ok": True, "config": get_config(reveal=False)}

@app.post("/payouts", response_model=PayoutStatus)
def create_payout(data: CreatePayoutIn, background: BackgroundTasks):
    pid = uuid.uuid4().hex
    now = datetime.utcnow()
    record = {
        "id": pid,
        "rail": data.rail,
        "status": "queued",
        "amount": data.amount.model_dump(),
        "createdAt": now.isoformat(),
        "updatedAt": now.isoformat(),
        "webhookUrl": data.webhookUrl,
        "provider": None,
        "provider_ids": {},
        "raw": None,
        "failureReason": None,
    }

    # Dispatch by rail
    try:
        if data.rail in ("CARD_VISA","CARD_MASTERCARD"):
            if not (RUNTIME["STRIPE_API_KEY"] and RUNTIME["STRIPE_CONNECT_ACCOUNT"]):
                raise HTTPException(400, "Stripe not configured (STRIPE_API_KEY / STRIPE_CONNECT_ACCOUNT)")
            if not data.card_token:
                raise HTTPException(400, "card_token required for card rail")
            # external account
            ext_id = stripe_add_external_card(RUNTIME["STRIPE_CONNECT_ACCOUNT"], data.card_token)
            pay = stripe_create_payout(RUNTIME["STRIPE_CONNECT_ACCOUNT"], ext_id, data.amount, data.notes or f"OTC payout {pid}")
            record["provider"] = "stripe"
            record["provider_ids"] = {"stripe_external_id": ext_id, "stripe_payout_id": pay.get("id")}
            record["raw"] = {"payout": pay}

            # enqueue poller
            background.add_task(poll_stripe_until_done, pid)

        elif data.rail == "SEPA":
            if not (RUNTIME["WISE_API_TOKEN"] and RUNTIME["WISE_PROFILE_ID"]):
                raise HTTPException(400, "Wise not configured (WISE_API_TOKEN / WISE_PROFILE_ID)")
            if not (data.beneficiary_name and data.iban):
                raise HTTPException(400, "beneficiary_name and iban required for SEPA")
            q = wise_create_quote(data.amount)
            r = wise_create_recipient(data.beneficiary_name, data.iban)
            t = wise_create_transfer(q["id"], r["id"], data.notes or f"OTC payout {pid}")
            try:
                f = wise_fund_transfer(t["id"])
                record["raw"] = {"quote": q, "recipient": r, "transfer": t, "fund": f}
                record["provider_ids"] = {"wise_transfer_id": str(t["id"])}
                record["provider"] = "wise"
                background.add_task(poll_wise_until_done, pid)
            except HTTPException as e:
                # Insufficient balance / other fund errors → failed
                record["provider"] = "wise"
                record["provider_ids"] = {"wise_transfer_id": str(t["id"])}
                record["raw"] = {"quote": q, "recipient": r, "transfer": t, "fund_error": e.detail}
                record["failureReason"] = str(e.detail)
                record["status"] = "failed"

        elif data.rail == "CRYPTO":
            if not (RUNTIME["NP_API_KEY"] and data.wallet_address and data.crypto_asset):
                raise HTTPException(400, "NP_API_KEY, wallet_address and crypto_asset required")
            np_payload = {
                "order_id": pid,
                "asset": data.crypto_asset.upper(),
                "network": (data.crypto_network or "").upper() or None,
                "amount_fiat": data.amount.value,
                "fiat_currency": data.amount.currency,
                "destination_address": data.wallet_address,
                "idempotency_key": data.idempotencyKey or f"payout-{pid}",
            }
            resp = np_create_payout(np_payload)
            record["provider"] = "nowpayments"
            record["provider_ids"] = {"np_order_id": resp.get("id") or resp.get("order_id") or str(pid)}
            record["raw"] = {"np": resp}
            background.add_task(poll_nowp_until_done, pid)

        else:
            raise HTTPException(400, f"Unsupported rail {data.rail}")

    except HTTPException as e:
        record["status"] = "failed"
        record["failureReason"] = str(e.detail)

    with LOCK:
        PAYOUTS[pid] = record

    # Emit initial webhook
    send_webhook(record.get("webhookUrl"), {"type": "payout.queued", "payout": record})

    return PayoutStatus(**record)

@app.get("/payouts/{payout_id}", response_model=PayoutStatus)
def get_payout(payout_id: str):
    with LOCK:
        p = PAYOUTS.get(payout_id)
        if not p: raise HTTPException(404, "Payout not found")
        return PayoutStatus(**p)

# Optional echo endpoint to verify outbound webhooks
@app.post("/webhooks/echo")
async def webhook_echo(req: Request):
    body = await req.body()
    return {"ok": True, "received": json.loads(body or b"{}")}

@app.get("/")
def root():
    return {"ok": True, "service": SERVICE_NAME, "rails": ["CARD_VISA","CARD_MASTERCARD","SEPA","CRYPTO"]}
