# services/api/routers/offramp.py
import hmac, hashlib, json
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from services.api.db import get_db, orders
from services.api.services.nowpayments import NowPaymentsClient
from services.api.config import settings

router = APIRouter(prefix="/offramp", tags=["offramp"])

@router.post("/trigger-payout/{order_id}")
async def trigger_payout(order_id: str, db: Session = Depends(get_db)):
    """
    Crea un payout REALE su NOWPayments e salva il payout_id.
    Non altera lo status dell'ordine (resta 'quoted' finché arriva l'IPN).
    """
    row = db.execute(select(orders).where(orders.c.id == order_id)).mappings().first()
    if not row:
        raise HTTPException(404, "Order not found")

    if not settings.NOWPAYMENTS_API_KEY:
        raise HTTPException(400, "NOWPayments API key not configured")

    if not row["iban"] or not row["beneficiary_name"]:
        raise HTTPException(400, "IBAN or beneficiary_name missing")

    np = NowPaymentsClient()
    res = await np.create_payout(
        amount_eur=row["amount_eur"],
        iban=row["iban"],
        beneficiary_name=row["beneficiary_name"],
        reference=order_id,
    )

    # Estrarre payout_id da risposte /payout | /payouts
    payout_id = str(
        res.get("payout_id")
        or res.get("id")
        or (res.get("withdrawals") or [{}])[0].get("id")
        or ""
    )
    if not payout_id:
        raise HTTPException(502, f"NOWPayments response without payout id: {res}")

    db.execute(
        update(orders).where(orders.c.id == order_id).values(nowpayments_payout_id=payout_id)
    )

    # Rispondi sempre JSON
    return {"ok": True, "order_id": order_id, "payout_id": payout_id, "np_raw": res}

@router.post("/webhooks/nowpayments")
async def nowpayments_webhook(request: Request, db: Session = Depends(get_db)):
    """
    Webhook IPN NOWPayments: verifica HMAC e chiude l'ordine quando payout_status = finished.
    Header: x-nowpayments-sig
    Body:   {..., payout_id: "...", payout_status: "finished" }
    """
    body_bytes = await request.body()
    sig_hdr = request.headers.get("x-nowpayments-sig", "")

    secret = (settings.NOWPAYMENTS_IPN_SECRET or "").encode()
    computed = hmac.new(secret, body_bytes, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig_hdr, computed):
        raise HTTPException(401, "Invalid signature")

    try:
        payload = json.loads(body_bytes.decode("utf-8") or "{}")
    except Exception:
        raise HTTPException(400, "Invalid JSON")

    payout_id = str(payload.get("payout_id", "") or "")
    payout_status = str(payload.get("payout_status", "") or "").lower()

    if payout_id and payout_status == "finished":
        db.execute(
            update(orders)
            .where(orders.c.nowpayments_payout_id == payout_id)
            .values(status="completed")
        )

    return {"ok": True}
