# services/api/routers/offramp.py
from __future__ import annotations

import os
import sqlite3
import json
from typing import Any, Dict, Optional

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

# ---------------------------
# Config / DB helpers
# ---------------------------
DB_PATH = os.getenv("DB_PATH", "data.db")
NOWPAYMENTS_BASE_URL = os.getenv("NOWPAYMENTS_BASE_URL", "https://api.nowpayments.io/v1").rstrip("/")
NOWPAYMENTS_JWT = os.getenv("NOWPAYMENTS_JWT", "").strip()          # preferito: token Bearer fornito dalla dashboard/supporto
NOWPAYMENTS_API_KEY = os.getenv("NOWPAYMENTS_API_KEY", "").strip()  # opzionale
NOWPAYMENTS_BANK_EXTRA_JSON = os.getenv("NOWPAYMENTS_BANK_EXTRA_JSON", "").strip()  # opzionale JSON

def _conn() -> sqlite3.Connection:
    # assicurati cartella se path contiene directory
    dirpath = os.path.dirname(DB_PATH)
    if dirpath:
        try:
            os.makedirs(dirpath, exist_ok=True)
        except Exception:
            pass
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def _init_db() -> None:
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
        status TEXT NOT NULL,
        nowpayments_payout_id TEXT,
        redirect_url TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
    """)
    conn.commit()
    conn.close()

_init_db()

def _rowdict(row: sqlite3.Row) -> Dict[str, Any]:
    return {k: row[k] for k in row.keys()}

# ---------------------------
# Schemas
# ---------------------------
class SetPriceIn(BaseModel):
    token_symbol: str = Field(..., example="NENO")
    price_eur: float = Field(..., gt=0)
    available_amount: float = Field(..., ge=0)

class CreateOrderIn(BaseModel):
    token_symbol: str
    amount_tokens: float = Field(..., gt=0)
    iban: str
    beneficiary_name: str
    redirect_url: Optional[str] = ""

# ---------------------------
# Router
# ---------------------------
router = APIRouter(prefix="", tags=["offramp"])

@router.get("/offramp/health")
async def health():
    return {"ok": True, "router": "offramp"}

# OTC endpoints (same as before)
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
async def listings():
    conn = _conn()
    cur = conn.cursor()
    rows = cur.execute("SELECT * FROM otc_listings ORDER BY token_symbol").fetchall()
    conn.close()
    return [_rowdict(r) for r in rows]

@router.post("/offramp/create-order")
async def create_order(body: CreateOrderIn):
    conn = _conn()
    cur = conn.cursor()
    row = cur.execute("SELECT price_eur, available_amount FROM otc_listings WHERE token_symbol=?", (body.token_symbol.upper(),)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, detail="Listing non trovato")

    price_eur = float(row["price_eur"])
    eur_amount = price_eur * float(body.amount_tokens)

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
    }

# ---------------------------
# NOWPayments payout helper (uses JWT if provided)
# ---------------------------
async def _call_nowpayments_payout(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Tenta /payout e poi /payouts. Usa Authorization: Bearer <JWT> se NOWPAYMENTS_JWT è impostato.
    In alternativa include x-api-key se presente.
    """
    headers = {"Content-Type": "application/json"}
    if NOWPAYMENTS_JWT:
        headers["Authorization"] = f"Bearer {NOWPAYMENTS_JWT}"
    if NOWPAYMENTS_API_KEY:
        headers["x-api-key"] = NOWPAYMENTS_API_KEY

    async with httpx.AsyncClient(timeout=60) as client:
        # prova /payout
        url1 = f"{NOWPAYMENTS_BASE_URL}/payout"
        r1 = await client.post(url1, json=payload, headers=headers)
        try:
            data1 = r1.json()
        except Exception:
            data1 = {"raw": r1.text}
        if r1.status_code in (200, 201):
            return data1
        # se non ok, prova fallback /payouts
        url2 = f"{NOWPAYMENTS_BASE_URL}/payouts"
        r2 = await client.post(url2, json=payload, headers=headers)
        try:
            data2 = r2.json()
        except Exception:
            data2 = {"raw": r2.text}
        if r2.status_code in (200, 201):
            return data2
        # altrimenti solleva con info provider
        # preferisci data2 se presente, altrimenti data1
        raise HTTPException(status_code=502, detail={"message": "NOWPayments payout error", "response1": data1, "response2": data2, "status1": r1.status_code, "status2": r2.status_code})

# ---------------------------
# Trigger payout endpoint (reale)
# ---------------------------
@router.post("/offramp/trigger-payout/{order_id}")
async def trigger_payout(order_id: int):
    conn = _conn()
    cur = conn.cursor()
    sale = cur.execute("SELECT * FROM sales WHERE id=?", (order_id,)).fetchone()
    if not sale:
        conn.close()
        raise HTTPException(404, detail="Ordine non trovato")

    # idempotenza
    if sale["status"] not in ("created", "failed"):
        res = _rowdict(sale)
        conn.close()
        return res

    # verifica campi necessari
    if not sale["iban"] or not sale["beneficiary_name"]:
        conn.close()
        raise HTTPException(400, detail="IBAN o beneficiary_name mancanti")

    # costruisci payout payload (adatta se il tuo account richiede campi extra)
    withdrawal = {
        "currency": "eur",
        "amount": float(sale["eur_amount"]),
        "address": sale["iban"],
        "withdrawal_description": f"Offramp order #{order_id} - {sale['beneficiary_name']}",
    }

    # merge campi bancari extra da env JSON (se forniti)
    if NOWPAYMENTS_BANK_EXTRA_JSON:
        try:
            extra = json.loads(NOWPAYMENTS_BANK_EXTRA_JSON)
            if isinstance(extra, dict):
                withdrawal.update(extra)
        except Exception:
            # ignora JSON malformato (ma log in futuro)
            pass

    payload = {"payouts": [withdrawal]}

    # chiamata reale
    try:
        res = await _call_nowpayments_payout(payload)
    except HTTPException as e:
        conn.close()
        # Propaga il dettaglio del provider (senza cambiare lo stato dell'ordine)
        raise e
    except Exception as e:
        # errore generico
        conn.close()
        raise HTTPException(status_code=502, detail=f"Errore interno creazione payout: {e}")

    # Estrai payout_id coerente dalle possibili risposte
    payout_id = None
    if isinstance(res, dict):
        payout_id = res.get("payout_id") or res.get("id")
        if not payout_id:
            # alcuni endpoint ritornano { "withdrawals": [ {"id": "..." , ...} ] }
            withdrawals = res.get("withdrawals") or res.get("payouts") or []
            if isinstance(withdrawals, (list, tuple)) and withdrawals:
                first = withdrawals[0]
                if isinstance(first, dict):
                    payout_id = first.get("id") or first.get("payout_id")

    if not payout_id:
        # provider ha risposto 2xx ma senza payout_id: non proseguiamo
        conn.close()
        raise HTTPException(status_code=502, detail={"error": "NOWPayments response without payout_id", "raw": res})

    # Salva payout_id e imposta payout_pending
    cur.execute("UPDATE sales SET nowpayments_payout_id=?, status='payout_pending', updated_at=datetime('now') WHERE id=?", (str(payout_id), order_id))
    conn.commit()
    sale = cur.execute("SELECT * FROM sales WHERE id=?", (order_id,)).fetchone()
    conn.close()

    out = _rowdict(sale)
    out["_provider_raw"] = res
    return out
