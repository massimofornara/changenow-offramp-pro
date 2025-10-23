import os
import sqlite3
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

# --- DB helpers (sqlite3, nessun import circolare) ----------------------------
DB_PATH = os.getenv("DB_PATH", "data.db")

def _conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True) if "/" in DB_PATH else None
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def _init():
    conn = _conn()
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS otc_listings (
        token_symbol TEXT PRIMARY KEY,
        price_eur REAL NOT NULL,
        available_amount REAL NOT NULL,
        updated_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS sales (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        token_symbol TEXT NOT NULL,
        amount_tokens REAL NOT NULL,
        price_eur REAL NOT NULL,
        eur_amount REAL NOT NULL,
        iban TEXT NOT NULL,
        beneficiary_name TEXT NOT NULL,
        status TEXT NOT NULL,                 -- created | payout_pending | completed | failed
        nowpayments_payout_id TEXT,
        redirect_url TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
    """)
    conn.commit()
    conn.close()

_init()

def _rowdict(row: sqlite3.Row) -> Dict[str, Any]:
    return {k: row[k] for k in row.keys()}

# --- Schemas ------------------------------------------------------------------
class SetPriceIn(BaseModel):
    token_symbol: str = Field(..., examples=["NENO"])
    price_eur: float = Field(..., gt=0)
    available_amount: float = Field(..., ge=0)

class CreateOrderIn(BaseModel):
    token_symbol: str
    amount_tokens: float = Field(..., gt=0)
    iban: str
    beneficiary_name: str
    redirect_url: Optional[str] = ""

# --- Router -------------------------------------------------------------------
router = APIRouter(prefix="", tags=["offramp"])

@router.get("/offramp/health")
async def health():
    return {"ok": True, "router": "offramp"}

# ============= OTC LISTINGS ===================================================

@router.post("/otc/set-price")
async def set_price(body: SetPriceIn):
    conn = _conn()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO otc_listings (token_symbol, price_eur, available_amount, updated_at)
        VALUES (?, ?, ?, datetime('now'))
        ON CONFLICT(token_symbol) DO UPDATE SET
            price_eur=excluded.price_eur,
            available_amount=excluded.available_amount,
            updated_at=datetime('now');
    """, (body.token_symbol.upper(), body.price_eur, body.available_amount))
    conn.commit()
    conn.close()
    return {"ok": True, "token": body.token_symbol.upper(), "price_eur": float(body.price_eur)}

@router.get("/otc/listings")
async def listings() -> List[Dict[str, Any]]:
    conn = _conn()
    cur = conn.cursor()
    rows = cur.execute("SELECT * FROM otc_listings ORDER BY token_symbol").fetchall()
    conn.close()
    return [_rowdict(r) for r in rows]

# ============= CREATE ORDER ===================================================

@router.post("/offramp/create-order")
async def create_order(body: CreateOrderIn):
    # recupera listing
    conn = _conn()
    cur = conn.cursor()
    row = cur.execute("SELECT price_eur, available_amount FROM otc_listings WHERE token_symbol=?",
                      (body.token_symbol.upper(),)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, detail="Listing non trovato")

    price_eur = float(row["price_eur"])
    eur_amount = price_eur * float(body.amount_tokens)
    # NB: qui potresti anche scalare available_amount se vuoi “prenotare”

    cur.execute("""
        INSERT INTO sales (token_symbol, amount_tokens, price_eur, eur_amount, iban, beneficiary_name,
                           status, redirect_url, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, 'created', ?, datetime('now'), datetime('now'))
    """, (
        body.token_symbol.upper(), body.amount_tokens, price_eur, eur_amount,
        body.iban, body.beneficiary_name, body.redirect_url or ""
    ))
    order_id = cur.lastrowid
    conn.commit()
    sale = cur.execute("SELECT * FROM sales WHERE id=?", (order_id,)).fetchone()
    conn.close()

    return {
        "order_id": order_id,
        "status": "created",
        "eur_amount": eur_amount,
        "price_eur": price_eur,
        "token_symbol": body.token_symbol.upper(),
        "redirect_url": body.redirect_url or "",
        # puoi generare un link di vendita ChangeNOW dal tuo altro router, qui lo lasciamo informativo
    }

@router.get("/offramp/sales/{order_id}")
async def get_sale(order_id: int):
    conn = _conn()
    cur = conn.cursor()
    row = cur.execute("SELECT * FROM sales WHERE id=?", (order_id,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404, detail="Ordine non trovato")
    return _rowdict(row)

# ============= TRIGGER REAL NOWPAYMENTS PAYOUT ================================

NOW_API_KEY = os.getenv("NOWPAYMENTS_API_KEY", "").strip()
NOW_BASE_URL = os.getenv("NOWPAYMENTS_BASE_URL", "https://api.nowpayments.io/v1").rstrip("/")

async def _create_nowpayments_payout(*, amount_eur: float, iban: str, beneficiary: str, note: str) -> Dict[str, Any]:
    """
    Chiamata reale a NOWPayments payout.
    L'API esatta può variare; qui usiamo un formato comune:
      POST /v1/payout
      Headers: x-api-key
      Body:
        {
          "payouts": [{
            "currency": "eur",
            "amount": <float>,
            "address": "<IBAN>",
            "withdrawal_description": "note"
          }]
        }
    """
    if not NOW_API_KEY:
        raise HTTPException(500, detail="NOWPayments API key mancante (NOWPAYMENTS_API_KEY)")

    url = f"{NOW_BASE_URL}/payout"
    payload = {
        "payouts": [{
            "currency": "eur",
            "amount": float(amount_eur),
            "address": iban,
            "withdrawal_description": note or f"Payout {beneficiary}"
        }]
    }
    headers = {"x-api-key": NOW_API_KEY, "Content-Type": "application/json"}

    async with httpx.AsyncClient(timeout=40) as client:
        r = await client.post(url, json=payload, headers=headers)
    # Consideriamo 200/201 come ok
    if r.status_code not in (200, 201):
        raise HTTPException(r.status_code, detail={"message": "NOWPayments payout error", "response": r.text})
    data = r.json()
    # normalizza un campo id se presente
    payout_id = data.get("id") or data.get("payout_id") or data.get("data", {}).get("id")
    return {"raw": data, "payout_id": payout_id}

@router.post("/offramp/trigger-payout/{order_id}")
async def trigger_payout(order_id: int):
    # carica ordine
    conn = _conn()
    cur = conn.cursor()
    sale = cur.execute("SELECT * FROM sales WHERE id=?", (order_id,)).fetchone()
    if not sale:
        conn.close()
        raise HTTPException(404, detail="Ordine non trovato")
    if sale["status"] not in ("created", "failed"):
        conn.close()
        return {"order_id": order_id, "status": sale["status"], "nowpayments_payout_id": sale["nowpayments_payout_id"]}

    # chiama NOWPayments
    try:
        res = await _create_nowpayments_payout(
            amount_eur=float(sale["eur_amount"]),
            iban=sale["iban"],
            beneficiary=sale["beneficiary_name"],
            note=f"Offramp order #{order_id} - {sale['beneficiary_name']}",
        )
        npid = res["payout_id"]
        cur.execute("""
            UPDATE sales SET status='payout_pending', nowpayments_payout_id=?, updated_at=datetime('now')
            WHERE id=?
        """, (npid, order_id))
        conn.commit()
        sale = cur.execute("SELECT * FROM sales WHERE id=?", (order_id,)).fetchone()
        return _rowdict(sale) | {"nowpayments_response": res["raw"]}
    except HTTPException:
        conn.close()
        raise
    except Exception as e:
        # fallimento generico
        cur.execute("UPDATE sales SET status='failed', updated_at=datetime('now') WHERE id=?", (order_id,))
        conn.commit()
        conn.close()
        raise HTTPException(502, detail=f"Errore trigger payout: {e}")
    finally:
        conn.close()
