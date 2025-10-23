import httpx
from typing import Any, Dict, Optional
from services.api.config import settings

class NowPaymentsClient:
    def __init__(self):
        self.base_url = "https://api.nowpayments.io/v1"
        self.headers = {
            "x-api-key": settings.NOWPAYMENTS_API_KEY,
            "Content-Type": "application/json"
        }

    async def create_payout(
        self,
        amount_eur: float,
        iban: str,
        beneficiary_name: str,
        reference: Optional[str] = None
    ) -> Dict[str, Any]:
        url = f"{self.base_url}/payout"
        payload: Dict[str, Any] = {
            "amount": amount_eur,
            "currency": "eur",
            "payout_address": iban,
            "beneficiary_name": beneficiary_name,
            "reference": reference or "NENO OTC Sell"
        }

        async with httpx.AsyncClient(timeout=60.0) as client:
            r = await client.post(url, headers=self.headers, json=payload)
            r.raise_for_status()
            return r.json()
