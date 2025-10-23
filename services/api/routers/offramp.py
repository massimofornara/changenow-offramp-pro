from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from services.api.db import get_db
from services.api.models import OfframpOrder, OtcListing
from services.api.services.nowpayments import NowPaymentsClient, NowPaymentsError

router = APIRouter(prefix="/offramp", tags=["offramp"])

@router.post("/trigger-payout/{order_id}")
def trigger_payout(order_id: str, db: Session = Depends(get_db)):
    """
    Payout REALE su NOWPayments, nessun ID fittizio.
    - Se NOWPayments risponde 2xx con payout_id -> salva e imposta 'payout_pending'
    - Se NOWPayments risponde errore -> ritorna errore provider (non tocca l'ordine)
    """
    order = db.query(OfframpOrder).filter(OfframpOrder.id == order_id).one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="Ordine non trovato")

    if order.status in ("payout_pending", "completed"):
        # Idempotenza: se già avviato/finito, non rilanciamo il payout
        return {
            "ok": True,
            "order_id": order_id,
            "status": order.status,
            "payout_id": order.nowpayments_payout_id,
        }

    if not order.iban or not order.beneficiary_name:
        raise HTTPException(status_code=400, detail="IBAN o beneficiary_name mancanti")

    try:
        client = NowPaymentsClient()
    except RuntimeError as e:
        # API key mancante
        raise HTTPException(status_code=503, detail=str(e))

    # Call provider reale
    try:
        res = client.create_bank_payout(
            amount_eur=float(order.amount_eur),
            iban=order.iban,
            beneficiary_name=order.beneficiary_name,
            reference=order_id,
        )
    except NowPaymentsError as e:
        # Non alteriamo l'ordine: ritorniamo errore provider
        raise HTTPException(status_code=502, detail={"provider_status": e.status, "provider_body": e.body})

    # Estrai payout_id (diverse varianti di risposta)
    payout_id = (
        str(res.get("payout_id") or
            res.get("id") or
            (res.get("withdrawals") or [{}])[0].get("id") or "")
    )
    if not payout_id:
        # Anche se 2xx, senza payout_id non procediamo
        raise HTTPException(status_code=502, detail={"error": "Risposta NOWPayments senza payout_id", "raw": res})

    # Persist: imposta pendente e salva payout_id
    order.nowpayments_payout_id = payout_id
    order.status = "payout_pending"
    order.updated_at = datetime.utcnow()
    db.add(order)
    db.commit()
    db.refresh(order)

    return {"ok": True, "order_id": order.id, "payout_id": payout_id, "status": order.status, "provider_raw": res}
