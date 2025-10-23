# services/api/routers/nowpayments.py
from __future__ import annotations

import hmac
import json
import os
from hashlib import sha256
from typing import Dict

from fastapi import APIRouter, Header, HTTPException, Request

# Riutilizziamo lo storage dell'altro router tramite import “soft”.
try:
    from services.api.routers.offramp import (  # type: ignore
        _get_order,
        _update_order,
        NOWPAY_IPN_SECRET,
    )
except Exception:
    # Fallback: senza funzioni condivise non possiamo procedere in modo coerente.
    raise

router = APIRouter(tags=["nowpayments", "webhooks"])


def _secure_compare(a: str, b: str) -> bool:
    try:
        return hmac.compare_digest(a, b)
    except Exception:
        # compat
        if len(a) != len(b):
            return False
        result = 0
        for x, y in zip(a.encode(), b.encode()):
            result |= x ^ y
        return result == 0


def _compute_sig(raw: bytes) -> str:
    secret = NOWPAY_IPN_SECRET or os.getenv("NOWPAYMENTS_IPN_SECRET", "")
    if not secret:
        return ""
    mac = hmac.new(secret.encode("utf-8"), raw, sha256)
    return mac.hexdigest()


@router.post("/offramp/webhooks/nowpayments")
async def nowpayments_ipn(request: Request, x_nowpayments_sig: str = Header(default="")):
    """
    IPN NOWPayments:
    - Verifica firma HMAC su RAW body (hex digest).
    - Se `payout_status == finished`, marca l’ordine come `completed`.
    - Salva sempre `nowpayments_payout_id` se presente.
    """
    raw = await request.body()
    if not NOWPAY_IPN_SECRET and not os.getenv("NOWPAYMENTS_IPN_SECRET"):
        raise HTTPException(status_code=500, detail="IPN secret non configurato")

    calc = _compute_sig(raw)
    if not calc or not x_nowpayments_sig or not _secure_compare(calc, x_nowpayments_sig):
        raise HTTPException(status_code=401, detail="Firma IPN non valida")

    try:
        payload: Dict = json.loads(raw.decode("utf-8"))
    except Exception:
        raise HTTPException(status_code=400, detail="Body IPN non è JSON")

    payout_id = str(payload.get("payout_id", "")) or str(payload.get("id", ""))
    payout_status = str(payload.get("payout_status", "")).lower()

    # Trova ordine per payout_id oppure per extra.order_id se presente
    order_id = payload.get("extra", {}).get("order_id")
    if not order_id:
        # Non tutti gli IPN rimandano l'order_id: proviamo un reverse mapping
        # In questo semplice setup assumiamo che l’order_id sia noto e già salvato
        # nel campo nowpayments_payout_id
        # -> Qui non abbiamo l’indice, quindi richiediamo explicit order_id.
        raise HTTPException(status_code=422, detail="IPN senza extra.order_id")

    order = _get_order(order_id)
    if payout_id and not order.get("nowpayments_payout_id"):
        order = _update_order(order_id, nowpayments_payout_id=payout_id)

    if payout_status == "finished":
        order = _update_order(order_id, status="completed")

    return {"ok": True, "order_id": order_id, "status": order["status"], "payout_id": order.get("nowpayments_payout_id")}
