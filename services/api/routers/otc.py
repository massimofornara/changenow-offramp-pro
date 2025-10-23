# services/api/routers/otc.py
from __future__ import annotations

from datetime import datetime, timezone
from threading import Lock
from typing import Dict, List

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, validator

router = APIRouter(prefix="/otc", tags=["otc"])

# -----------------------------------------------------------------------------
# Modello/i di richiesta/risposta
# -----------------------------------------------------------------------------
class SetPriceRequest(BaseModel):
    token_symbol: str = Field(..., description="Ticker del token, es. 'NENO'")
    price_eur: float = Field(..., ge=0, description="Prezzo per 1 token in EUR")
    available_amount: float = Field(
        ..., ge=0, description="Quantità disponibile per la vendita"
    )

    @validator("token_symbol")
    def _token_upper(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("token_symbol non può essere vuoto")
        return v.upper()


class Listing(BaseModel):
    token_symbol: str
    price_eur: float
    available_amount: float
    updated_at: str  # ISO 8601


# -----------------------------------------------------------------------------
# Storage in-memory thread-safe
# (se vuoi persistenza, sposta questi dati su DB; qui evitiamo import errati)
# -----------------------------------------------------------------------------
_LISTINGS: Dict[str, Listing] = {}
_LOCK = Lock()


def _now_iso() -> str:
    # ISO 8601 con timezone UTC esplicito
    return datetime.now(timezone.utc).isoformat()


# -----------------------------------------------------------------------------
# Routes
# -----------------------------------------------------------------------------
@router.post("/set-price")
def set_price(payload: SetPriceRequest):
    """
     Crea/aggiorna il listing OTC di un token.
     Ritorna la forma rapida compatibile con lo script bash:
        {"ok": true, "token": "NENO", "price_eur": "5000.0"}
    """
    with _LOCK:
        listing = Listing(
            token_symbol=payload.token_symbol,
            price_eur=float(payload.price_eur),
            available_amount=float(payload.available_amount),
            updated_at=_now_iso(),
        )
        _LISTINGS[payload.token_symbol] = listing

    # Risposta "storica" attesa dallo script utente
    return {
        "ok": True,
        "token": listing.token_symbol,
        "price_eur": f"{listing.price_eur:.1f}",
    }


@router.get("/listings", response_model=List[Listing])
def get_listings():
    """
    Ritorna tutti i listing correnti in forma normalizzata.
    Esempio:
    [
      {
        "token_symbol": "NENO",
        "price_eur": 5000.0,
        "available_amount": 1000000.0,
        "updated_at": "2025-10-23T00:11:54.315363+00:00"
      }
    ]
    """
    with _LOCK:
        return list(_LISTINGS.values())


# -----------------------------------------------------------------------------
# Helpers opzionali (non esposte)
# -----------------------------------------------------------------------------
def _require_listing(symbol: str) -> Listing:
    symbol = symbol.strip().upper()
    with _LOCK:
        if symbol not in _LISTINGS:
            raise HTTPException(status_code=404, detail=f"Listing '{symbol}' non trovato")
        return _LISTINGS[symbol]
