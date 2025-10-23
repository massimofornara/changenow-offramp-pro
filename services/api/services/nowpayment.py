# services/api/services/nowpayments.py
import os, httpx

class NowPaymentsClient:
    def __init__(self):
        self.base_url = os.getenv("NOWPAYMENTS_BASE_URL", "https://api.nowpayments.io/v1")
        self.api_key = os.getenv("NOWPAYMENTS_API_KEY")
        if not self.api_key:
            raise RuntimeError("NOWPAYMENTS_API_KEY missing")

    async def create_payout(self, *, amount_eur: float, iban: str, beneficiary_name: str, reference: str):
        payload = {
            "amount": amount_eur,
            "currency": "eur",
            "payout_type": "bank_transfer",
            "iban": iban,
            "beneficiary_name": beneficiary_name,
            "reference": reference,
        }
        headers = {"x-api-key": self.api_key, "Content-Type": "application/json"}

        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(f"{self.base_url}/payout", json=payload, headers=headers)
        if r.status_code // 100 != 2:
            # Propaga errore – il router lo trasformerà in 4xx/5xx senza cambiare lo stato ordine
            raise RuntimeError(f"NOWPayments payout error {r.status_code}: {r.text}")
        return r.json()
