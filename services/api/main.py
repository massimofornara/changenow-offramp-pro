# services/api/main.py
import os, requests
from fastapi import FastAPI, HTTPException, Body
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, Dict

SERVICE_NAME = "changenow-offramp-pro"
PROVIDER = os.getenv("PROVIDER", "nowpayments")

NP_BASE_URL   = os.getenv("NP_BASE_URL", "https://api.nowpayments.io/v1").rstrip("/")
NP_PAYOUT_PATH= os.getenv("NP_PAYOUT_PATH", "/payouts")
NP_USE_JWT    = os.getenv("NP_USE_JWT", "false").lower() == "true"
NP_API_KEY    = os.getenv("NP_API_KEY")

app = FastAPI(title=SERVICE_NAME, version="1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

ORDERS: Dict[int, Dict] = {}
LISTINGS: Dict[str, Dict] = {}

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

def np_headers():
    if NP_USE_JWT:
        raise RuntimeError("JWT mode not configured. Set NP_USE_JWT=false to use API key.")
    return {"x-api-key": NP_API_KEY, "Content-Type": "application/json"}

def create_payout(payload: dict):
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

@app.get("/")
def root():
    return {"ok": True, "service": SERVICE_NAME, "provider": PROVIDER}

@app.get("/offramp/health")
def health(verbose: Optional[bool] = False):
    data = {"ok": True, "service": SERVICE_NAME, "provider": PROVIDER}
    if verbose:
        data.update({"NP_BASE_URL": NP_BASE_URL, "auth_mode": "api_key" if not NP_USE_JWT else "jwt"})
    return data

@app.get("/nowpayments/health")
def nowpayments_health():
    try:
        h = np_headers()
        status_url = os.getenv("NP_STATUS_URL")
        urls = [status_url] if status_url else [
            "https://api.nowpayments.io/status",
            f"{NP_BASE_URL}/status",
            f"{NP_BASE_URL}/payouts",
        ]
        last = None
        for u in urls:
            try:
                r = requests.get(u, headers=h, timeout=10)
                if r.status_code in (200, 204
