# services/api/routers/otc.py
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

# =============================================================================
# Storage: prova a usare il DB se presente, altrimenti fallback in-memory
# Atteso (se presente) un modulo con una repo simile a questa interfaccia:
#   ListingsRepo.set_price(token_symbol: str, price_eur: float, available_amount: float) -> dict
#   ListingsRepo.get_price_eur(token_symbol: str) -> Optional[float]
#   ListingsRepo.list_all() -> List[dict]
# =============================================================================
try:
    from services.api.db.otc import ListingsRepo  # type: ignore
    HAS_DB = True
except Exception:
    HAS_DB = False
    _LISTINGS_MEM: Dict[str, Dict] = {}


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# =============================================================================
# Schemi
# =============================================================================
class SetPriceReq(BaseModel):
    token_symbol: str = Field(..., description="Ticker del token, es. NENO")
    price_eur: float = Field(..., gt=0, description="Prezzo 1 token in EUR")
    available_amount: float = Field(..., ge=0, description="Quantità disponibile totale")


class ListingOut(BaseModel):
    token_symbol: str
    price_eur: float
    available_amount: float
    updated_at: str


# =============================================================================
# Helpers storage (versione compatibile DB / in-memory)
# =============================================================================
def _set_price(token_symbol: str, price_eur: float, available_amount: float) -> Dict:
    token = token_symbol.upper()
    now = _utcnow_iso()

    if HAS_DB:
        # Si delega al repository che deve occuparsi di upsert e timestamps
        data = ListingsRepo.set_price(token, float(price_eur), float(available_amount))  # type: ignore
        # Normalizzo i campi essenziali in uscita
        return {
            "token_symbol": data.get("token_symbol", token),
            "price_eur": float(data.get("price_eur", price_eur)),
            "available_amount": float(data.get("available_amount", available_amount)),
            "updated_at": str(data.get("updated_at") or now),
        }

    # fallback in-memory
    _LISTINGS_MEM[token] = {
        "token_symbol": token,
        "price_eur": float(price_eur),
        "available_amount": float(available_amount),
        "updated_at": now,
    }
    return _LISTINGS_MEM[token]


def _list_all() -> List[Dict]:
    if HAS_DB:
        items = ListingsRepo.list_all()  # type: ignore
        # Mi assicuro che updated_at sia stringa ISO
        out: List[Dict] = []
        for it in items or []:
            ua = it.get("updated_at")
            out.append(
                {
                    "token_symbol": it.get("token_symbol"),
                    "price_eur": float(it.get("price_eur", 0.0)),
                    "available_amount": float(it.get("available_amount", 0.0)),
                    "updated_at": str(ua) if isinstance(ua, str) else _utcnow_iso(),
                }
            )
        return out

    return list(_LISTINGS_MEM.values())


def _get_price_eur(token_symbol: str) -> float:
    """Usato dagli altri router (stessa interfaccia del repo)."""
    token = token_symbol.upper()
    if HAS_DB:
        price = ListingsRepo.get_price_eur(token)  # type: ignore
        if price is None:
            raise HTTPException(status_code=404, detail="Listing non trovato")
        return float(price)
    if token not in _LISTINGS_MEM:
        raise HTTPException(status_code=404, detail="Listing non trovato")
    return float(_LISTINGS_MEM[token]["price_eur"])


# =============================================================================
# Router
# =============================================================================
router = APIRouter(prefix="/otc", tags=["otc"])


@router.post("/set-price")
async def set_price(req: SetPriceReq):
    """
    Crea/Aggiorna il listing del token.
    Ritorna { ok: true, token, price_eur } per compatibilità con lo script.
    """
    saved = _set_price(req.token_symbol, req.price_eur, req.available_amount)
    return {
        "ok": True,
        "token": saved["token_symbol"],
        "price_eur": str(saved["price_eur"]),  # mantengo string per retro-compatibilità del tuo flow
    }


@router.get("/listings", response_model=List[ListingOut])
async def listings():
    """
    Tutti i listing in formato pulito.
    Garantisco updated_at in ISO UTC.
    """
    items = _list_all()
    # Normalizzo updated_at ad ISO anche in fallback
    out = []
    for it in items:
        out.append(
            ListingOut(
                token_symbol=it["token_symbol"],
                price_eur=float(it["price_eur"]),
                available_amount=float(it["available_amount"]),
                updated_at=str(it.get("updated_at") or _utcnow_iso()),
            )
        )
    return out


# =============================================================================
# Esport utilità per altri moduli (compat con gli import che avevi)
#   from services.api.routers.otc import _get_price_eur
# =============================================================================
__all__ = ["router", "_get_price_eur"]
