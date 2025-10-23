from __future__ import annotations
import httpx
from typing import Dict, Any, Optional
from ..config import settings
from loguru import logger

class NowPaymentsClient:
    def __init__(self, api_key: str | None = None, base_url: str | None = None):
        self.api_key = api_key or settings.NOWPAYMENTS_API_KEY
        self.base_url = (base_url or str(settings.NOWPAYMENTS_BASE_URL)).rstrip("/")
        self.headers = {"x-api-key": self.api_key, "Content-Type": "application/json"}

    async def create_payout(self, amount_eur: float, iban: str, beneficiary_name: str, reference: Optional[str] = None) -> Dict[str, Any]:
    url = f"{self.base_url}/payout"
    payload: Dict[str, Any] = {
        "amount": amount_eur,
        "currency": "eur",
        "payout_address": iban,                # IBAN o oggetto bancario secondo il tuo account NP
        "beneficiary_name": beneficiary_name,  # nome intestatario
        "reference": reference or "NENO OTC Sell"
    }
    async with httpx.AsyncClient(timeout=60.0) as client:
        r = await client.post(url, headers=self.headers, json=payload)
        r.raise_for_status()
        return r.json()  # atteso: contiene "id" o "payout_id"
