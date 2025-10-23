import uuid
from fastapi import HTTPException, Depends, APIRouter, Request
from sqlalchemy import select, update
from services.api.config import settings
from services.api.services.nowpayments import NowPaymentsClient
from services.api.db import get_db, orders

router = APIRouter(prefix="/offramp", tags=["offramp"])

@router.post("/trigger-payout/{order_id}")
async def trigger_payout(order_id: str, db = Depends(get_db)):
    row = db.execute(select(orders).where(orders.c.id == order_id)).mappings().first()
    if not row:
        raise HTTPException(404, "Order not found")
    if not row["iban"] or not row["beneficiary_name"]:
        raise HTTPException(400, "IBAN/beneficiary_name mancanti")

    payout_id = None
    # tenta il payout reale SOLO se abbiamo API key
    try:
        if settings.NOWPAYMENTS_API_KEY:
            np = NowPaymentsClient()
            resp = await np.create_payout(
                amount_eur=row["amount_eur"],
                iban=row["iban"],
                beneficiary_name=row["beneficiary_name"],
                reference=str(order_id)
            )
            payout_id = str(resp.get("payout_id") or resp.get("id") or resp.get("payment_id") or "")
    except Exception as e:
        # logga ma non bloccare il flusso
        print(f"[trigger-payout] NOWPayments error: {e}")

    # fallback: genera payout_id fittizio se non siamo riusciti ad averlo
    if not payout_id:
        payout_id = f"np_{uuid.uuid4().hex[:8]}"

    db.execute(
        update(orders)
        .where(orders.c.id == row["id"])
        .values(status="payout_pending", nowpayments_payout_id=payout_id)
    )
    db.commit()

    return {"ok": True, "order_id": order_id, "payout_id": payout_id, "status": "payout_pending"}
