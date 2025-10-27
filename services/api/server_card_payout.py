# server_card_payout.py (estratto produzione)
import os, requests
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

STRIPE_API_KEY = os.getenv("STRIPE_API_KEY")            # sk_live_...
STRIPE_CONNECT_ACCOUNT = os.getenv("STRIPE_CONNECT_ACCOUNT")  # acct_...
STRIPE_API_BASE = "https://api.stripe.com"

app = FastAPI()

def s_headers(account: str | None = None):
    if not STRIPE_API_KEY: raise HTTPException(400, "STRIPE_API_KEY missing")
    h = {"Authorization": f"Bearer {STRIPE_API_KEY}"}
    if account: h["Stripe-Account"] = account
    return h

def http_raise(r: requests.Response):
    if r.status_code >= 400:
        raise HTTPException(r.status_code, {"url": r.request.url, "body": r.text[:2000]})

class CardTokenIn(BaseModel):
    token: str
    name: str | None = None

@app.post("/card-token")
def save_card_token(body: CardTokenIn):
    # qui potresti associare token a un order_id/utente nel DB
    return {"ok": True, "received_token": body.token}

def stripe_add_external_card(card_token: str) -> str:
    url = f"{STRIPE_API_BASE}/v1/accounts/{STRIPE_CONNECT_ACCOUNT}/external_accounts"
    r = requests.post(url, headers=s_headers(STRIPE_CONNECT_ACCOUNT),
                      data={"external_account": card_token}, timeout=30)
    http_raise(r)
    return r.json()["id"]  # es. "card_xxx"

def stripe_create_payout(destination_external_id: str, amount_eur: float, speed: str = "instant"):
    url = f"{STRIPE_API_BASE}/v1/payouts"
    r = requests.post(url, headers=s_headers(STRIPE_CONNECT_ACCOUNT), timeout=30, data={
        "amount": int(round(amount_eur*100)),
        "currency": "eur",
        "method": speed,               # instant | standard
        "destination": destination_external_id,
        "description": "OTC payout (prod)"
    })
    http_raise(r)
    return r.json()

# Esempio: funzione da richiamare nel tuo /offramp/trigger-payout
def do_card_payout(card_token: str, eur_amount: float):
    ext_id = stripe_add_external_card(card_token)
    payout = stripe_create_payout(ext_id, eur_amount, speed="instant")
    return {"external_id": ext_id, "payout": payout}
