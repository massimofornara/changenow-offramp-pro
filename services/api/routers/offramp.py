from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select, insert, update
from uuid import uuid4
from ..db import SessionLocal, otc_listings, orders
from ..schemas import SellOrderRequest, SellOrderResponse, OrderOut, NPWebhook
from ..config import settings
from ..services.changenow import ChangeNowClient
from ..services.nowpayments import NowPaymentsClient
from ..utils.hmac_verify import verify_hmac_sha256
from loguru import logger
from datetime import datetime

router = APIRouter(prefix="/offramp", tags=["Offramp"])

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@router.post("/create-order", response_model=SellOrderResponse)
async def create_order(body: SellOrderRequest, db = Depends(get_db)):
    token = body.token_symbol.upper()
    amount_tokens = body.amount_tokens
    if amount_tokens <= 0:
        raise HTTPException(400, "amount_tokens must be > 0")

    # Lookup OTC price for NENO
    res = db.execute(select(otc_listings).where(otc_listings.c.token_symbol == token)).mappings().first()
    if not res:
        raise HTTPException(400, f"Token {token} non presente nel listing OTC")

    price_eur = float(res["price_eur"])
    amount_eur = amount_tokens * price_eur

    order_id = uuid4()
    db.execute(
        insert(orders).values(
            id=order_id,
            token_symbol=token,
            amount_tokens=amount_tokens,
            price_eur=price_eur,
            amount_eur=amount_eur,
            iban=body.iban,
            beneficiary_name=body.beneficiary_name,
            status="queued",
            redirect_url=str(body.redirect_url) if body.redirect_url else None,
            logs={"events": [{"ts": datetime.utcnow().isoformat(), "msg": "order_created", "amount_eur": amount_eur}]},
        )
    )
    db.commit()

    # Create ChangeNOW SELL URL (public flow — user completes KYC & payout)
    cn = ChangeNowClient()
    sell_url = cn.public_sell_url(from_ticker="usdt", to_currency="eur", amount=amount_eur, redirect_url=str(body.redirect_url) if body.redirect_url else None)

    db.execute(update(orders).where(orders.c.id == order_id).values(status="quoted", logs={"events":[{"ts": datetime.utcnow().isoformat(), "msg":"changenow_url_generated", "sell_url": sell_url, "amount_eur": amount_eur}]}))
    db.commit()

    return SellOrderResponse(order_id=order_id, status="quoted", amount_eur=amount_eur, changenow_payment_url=sell_url)

@router.get("/sales", response_model=list[OrderOut])
def list_sales(db = Depends(get_db)):
    rows = db.execute(select(orders)).mappings().all()
    out = []
    for r in rows:
        out.append(OrderOut(
            order_id=r["id"],
            token_symbol=r["token_symbol"],
            amount_tokens=r["amount_tokens"],
            price_eur=r["price_eur"],
            amount_eur=r["amount_eur"],
            iban=r["iban"],
            beneficiary_name=r["beneficiary_name"],
            status=r["status"],
            changenow_tx_id=r["changenow_tx_id"],
            nowpayments_payout_id=r["nowpayments_payout_id"],
            created_at=r["created_at"].isoformat() if r["created_at"] else "",
            updated_at=r["updated_at"].isoformat() if r["updated_at"] else "",
        ))
    return out

@router.get("/sales/{order_id}", response_model=OrderOut)
def get_sale(order_id: str, db = Depends(get_db)):
    row = db.execute(select(orders).where(orders.c.id == order_id)).mappings().first()
    if not row:
        raise HTTPException(404, "Order not found")
    return OrderOut(
        order_id=row["id"],
        token_symbol=row["token_symbol"],
        amount_tokens=row["amount_tokens"],
        price_eur=row["price_eur"],
        amount_eur=row["amount_eur"],
        iban=row["iban"],
        beneficiary_name=row["beneficiary_name"],
        status=row["status"],
        changenow_tx_id=row["changenow_tx_id"],
        nowpayments_payout_id=row["nowpayments_payout_id"],
        created_at=row["created_at"].isoformat() if row["created_at"] else "",
        updated_at=row["updated_at"].isoformat() if row["updated_at"] else "",
    )

@router.post("/webhooks/nowpayments")
async def nowpayments_webhook(request: Request, body: NPWebhook, db = Depends(get_db)):
    raw = await request.body()
    sig = request.headers.get("x-nowpayments-sig") or body.signature or ""
    if not verify_hmac_sha256(raw, sig, settings.NOWPAYMENTS_IPN_SECRET):
        raise HTTPException(401, "Invalid signature")

    payout_id = body.payout_id or body.payment_id or body.order_id or ""
    status = (body.payout_status or body.payment_status or "").lower()

    if not payout_id:
        return {"ok": True}  # niente da fare

    # collega via payout_id; se non trovato, prova via reference=order_id
    row = db.execute(select(orders).where(orders.c.nowpayments_payout_id == payout_id)).mappings().first()
    if not row:
        try_ref = db.execute(select(orders).where(orders.c.id == payout_id)).mappings().first()
        row = try_ref or row

    if not row:
        return {"ok": True, "note": "payout non collegato ad alcun ordine"}

    new_status = row["status"]
    if status in ("finished", "confirmed", "success", "completed"):
        new_status = "completed"
    elif status in ("failed", "rejected", "chargeback"):
        new_status = "failed"
    else:
        new_status = "payout_pending"

    db.execute(update(orders)
               .where(orders.c.id == row["id"])
               .values(status=new_status))
    db.commit()
    return {"ok": True, "order_id": str(row["id"]), "status": new_status}
   
@router.post("/trigger-payout/{order_id}")
async def trigger_payout(order_id: str, db = Depends(get_db)):
    row = db.execute(select(orders).where(orders.c.id == order_id)).mappings().first()
    if not row:
        raise HTTPException(404, "Order not found")
    if not row["iban"] or not row["beneficiary_name"]:
        raise HTTPException(400, "IBAN/beneficiary_name mancanti")
    if row["status"] not in ("quoted", "processing", "payout_pending"):
        raise HTTPException(400, f"Stato non valido per payout: {row['status']}")

    np = NowPaymentsClient()
    payout = await np.create_payout(
        amount_eur=row["amount_eur"],
        iban=row["iban"],
        beneficiary_name=row["beneficiary_name"],
        reference=str(order_id)
    )
    payout_id = str(payout.get("payout_id") or payout.get("id") or payout.get("payment_id") or "")
    if not payout_id:
        raise HTTPException(502, f"Payout creation response unexpected: {payout}")

    db.execute(update(orders)
               .where(orders.c.id == row["id"])
               .values(status="payout_pending", nowpayments_payout_id=payout_id))
    db.commit()
    return {"ok": True, "order_id": order_id, "payout_id": payout_id, "status": "payout_pending"}
