# services/api/routers/offramp.py
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from typing import Dict, Optional

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

# ============================================================================
# Fallback storage (se il package DB non è disponibile)
# ============================================================================
try:
    # Facoltativo: se hai un modulo DB, usa quello.
    # Esempio d'uso atteso:
    #   from services.api.db.orders import OrdersRepo
    #   from services.api.db.otc import ListingsRepo
    from services.api.db.orders import OrdersRepo  # type: ignore
    from services.api.db.otc import ListingsRepo  # type: ignore
    _ORDERS = None
    _LISTINGS = None
    HAS_DB = True
except Exception:
    HAS_DB = False
    _ORDERS: Dict[str, Dict] = {}
    _LISTINGS: Dict[str, Dict] = {}

# ============================================================================
# Config NOWPayments
# ============================================================================
NOWPAY_BASE_URL = os.getenv("NOWPAYMENTS_BASE_URL", "https://api.nowpayments.io/v1")
NOWPAY_API_KEY = os.getenv("NOWPAYMENTS_API_KEY", "")
NOWPAY_IPN_SECRET = os.getenv("NOWPAYMENTS_IPN_SECRET", "")

if not NOWPAY_API_KEY:
    # Non blocco il boot dell’app; blocco solo al momento del trigger.
    pass

# ============================================================================
# Schemi
# ============================================================================
class CreateOrderReq(BaseModel):
    token_symbol: str = Field(..., example="NENO")
    amount_tokens: float = Field(..., gt=0)
    iban: str
    beneficiary_name: str
    redirect_url: Optional[str] = None


class OrderOut(BaseModel):
    order_id: str
    token_symbol: str
    amount_tokens: float
    price_eur: float
    amount_eur: float
    iban: str
    beneficiary_name: str
    status: str
    changenow_tx_id: Optional[str] = None
    nowpayments_payout_id: Optional[str] = None
    created_at: str
    updated_at: str
    changenow_payment_url: Optional[str] = None


# ============================================================================
# Helpers storage
# ============================================================================
def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_price_eur(token_symbol: str) -> float:
    if HAS_DB:
        price = ListingsRepo.get_price_eur(token_symbol)  # type: ignore
        if price is None:
            raise HTTPException(status_code=404, detail="Listing non trovato")
        return float(price)
    # fallback in-memory
    listing = _LISTINGS.get(token_symbol.upper())
    if not listing:
        raise HTTPException(status_code=404, detail="Listing non trovato")
    return float(listing["price_eur"])


def _save_order(obj: Dict) -> None:
    if HAS_DB:
        OrdersRepo.save(obj)  # type: ignore
    else:
        _ORDERS[obj["order_id"]] = obj


def _get_order(order_id: str) -> Dict:
    if HAS_DB:
        data = OrdersRepo.get(order_id)  # type: ignore
        if not data:
            raise HTTPException(status_code=404, detail="Ordine non trovato")
        return dict(data)
    # fallback
    if order_id not in _ORDERS:
        raise HTTPException(status_code=404, detail="Ordine non trovato")
    return _ORDERS[order_id]


def _update_order(order_id: str, **patch) -> Dict:
    if HAS_DB:
        data = OrdersRepo.update(order_id, **patch)  # type: ignore
        if not data:
            raise HTTPException(status_code=404, detail="Ordine non trovato")
        return dict(data)
    obj = _get_order(order_id)
    obj.update(patch)
    obj["updated_at"] = _utcnow_iso()
    _ORDERS[order_id] = obj
    return obj


# ============================================================================
# Router
# ============================================================================
router = APIRouter(prefix="/offramp", tags=["offramp"])


@router.post("/create-order", response_model=OrderOut)
async def create_order(body: CreateOrderReq):
    """
    Crea un ordine di vendita OTC.
    Calcola amount_eur = amount_tokens * price_eur (price da listing).
    """
    price_eur = _get_price_eur(body.token_symbol)
    amount_eur = float(body.amount_tokens) * float(price_eur)

    order_id = str(uuid.uuid4())
    now = _utcnow_iso()

    # URL precompilato ChangeNOW (solo redirect informativo)
    # Qui assumiamo che si venda USDT->EUR per l'ammontare EUR calcolato.
    cn_ref = os.getenv("CHANGENOW_REF_ID", "7f1d4fb8a0d97b")
    redirect_qs = f"&redirect_url={body.redirect_url}" if body.redirect_url else ""
    changenow_payment_url = (
        f"https://changenow.io/sell?from=usdt&to=eur&amount={amount_eur}"
        f"&ref_id={cn_ref}{redirect_qs}"
    )

    order = dict(
        order_id=order_id,
        token_symbol=body.token_symbol.upper(),
        amount_tokens=float(body.amount_tokens),
        price_eur=float(price_eur),
        amount_eur=float(amount_eur),
        iban=body.iban,
        beneficiary_name=body.beneficiary_name,
        status="quoted",
        changenow_tx_id=None,
        nowpayments_payout_id=None,
        created_at=now,
        updated_at=now,
        changenow_payment_url=changenow_payment_url,
    )
    _save_order(order)
    return order


@router.get("/sales/{order_id}", response_model=OrderOut)
async def get_order(order_id: str):
    return _get_order(order_id)


@router.post("/trigger-payout/{order_id}")
async def trigger_payout(order_id: str):
    """
    Crea un payout REALE su NOWPayments e salva:
    - status -> 'payout_pending'
    - nowpayments_payout_id -> reale (non fittizio)
    Restituisce sempre JSON (mai stringhe nude).
    """
    if not NOWPAY_API_KEY:
        raise HTTPException(status_code=500, detail="NOWPAYMENTS_API_KEY mancante")

    order = _get_order(order_id)
    if order["status"] in ("payout_pending", "completed"):
        # Idempotenza semplice
        return {
            "ok": True,
            "order_id": order_id,
            "status": order["status"],
            "payout_id": order.get("nowpayments_payout_id"),
        }

    payload = {
        "payout_type": "bank",
        "currency": "eur",
        "amount": float(order["amount_eur"]),
        "iban": order["iban"],
        "extra": {
            "beneficiary_name": order["beneficiary_name"],
            "order_id": order_id,
        },
    }

    headers = {"x-api-key": NOWPAY_API_KEY, "Content-Type": "application/json"}

    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(f"{NOWPAY_BASE_URL}/payout", json=payload, headers=headers)
        # Cerco sempre di restituire JSON
        try:
            data = r.json()
        except Exception:
            raise HTTPException(status_code=502, detail="Risposta NOWPayments non JSON")

    if r.status_code >= 300:
        # NON creiamo ID fittizi; ritorniamo l'errore del provider
        return {"ok": False, "provider_status": r.status_code, "provider_body": data}

    payout_id = data.get("id") or data.get("payout_id")
    if not payout_id:
        # Anche qui non forziamo ID finti
        return {"ok": False, "error": "Nessun payout_id nella risposta NOWPayments", "raw": data}

    # Persist: stato pendente fino a IPN
    order = _update_order(
        order_id,
        status="payout_pending",
        nowpayments_payout_id=str(payout_id),
    )

    return {"ok": True, "order_id": order_id, "payout_id": payout_id, "status": order["status"]}
