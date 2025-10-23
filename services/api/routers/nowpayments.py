import os
import hmac
import hashlib
import json
import sqlite3
from fastapi import APIRouter, Header, HTTPException, Request

DB_PATH = os.getenv("DB_PATH", "data.db")

def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

NP_IPN_SECRET = os.getenv("NOWPAYMENTS_IPN_SECRET", "").encode()

router = APIRouter(prefix="/nowpayments", tags=["nowpayments"])

@router.get("/health")
async def health():
    return {"ok": True, "router": "nowpayments"}

def _verify_signature(raw_body: bytes, sig: str) -> bool:
    if not NP_IPN_SECRET:
        # se non configurato, accettiamo per debug (ma segnaliamo)
        return True
    digest = hmac.new(NP_IPN_SECRET, raw_body, hashlib.sha512).hexdigest()
    return hmac.compare_digest(digest, sig or "")

@router.post("/ipn")
async def ipn(request: Request, x_nowpayments_sig: str = Header(default="")):
    raw = await request.body()
    if not _verify_signature(raw, x_nowpayments_sig):
        raise HTTPException(403, detail="Firma IPN non valida")

    payload = json.loads(raw.decode("utf-8") or "{}")
    payout_id = payload.get("payout_id") or payload.get("id")
    status = payload.get("status") or payload.get("payment_status")

    if not payout_id:
        raise HTTPException(400, detail="payout_id mancante nell'IPN")

    conn = _conn()
    cur = conn.cursor()
    # trova l'ordine con quel payout_id
    row = cur.execute("SELECT id FROM sales WHERE nowpayments_payout_id = ?", (str(payout_id),)).fetchone()
    if not row:
        conn.close()
        # accettiamo comunque
        return {"ok": True, "message": "payout non associato", "payout_id": payout_id}

    order_id = int(row["id"])
    # mappa uno stato finale
    final = "completed" if str(status).lower() in ("finished", "confirmed", "completed", "sent") else "payout_pending"
    cur.execute("UPDATE sales SET status=?, updated_at=datetime('now') WHERE id=?", (final, order_id))
    conn.commit()
    conn.close()

    return {"ok": True, "order_id": order_id, "status": final}
