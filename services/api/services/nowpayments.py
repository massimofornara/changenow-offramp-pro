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
        """Create a fiat payout (SEPA) via NOWPayments or connected providers.
        NOTE: This is a placeholder; map fields to NOWPayments 'payouts' schema according to your merchant account capabilities.
        """
        url = f"{self.base_url}/payout"
        payload: Dict[str, Any] = {
            "amount": amount_eur,
            "currency": "eur",
            "payout_address": iban,     # Depending on provider, this may need a structured 'bank_account' object
            "beneficiary_name": beneficiary_name,
            "reference": reference or "NENO OTC Sell",
            # Add any required fields per NOWPayments (e.g., bank_country, bic, etc.)
        }
        logger.info(f"Creating NOWPayments payout: {payload}")
        async with httpx.AsyncClient(timeout=60.0) as client:
            r = await client.post(url, headers=self.headers, json=payload)
            if r.status_code >= 400:
                logger.error(f"NOWPayments payout error: {r.status_code} {r.text}")
            r.raise_for_status()
            return r.json()
