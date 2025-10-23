# services/api/services/nowpayments.py
import aiohttp
from services.api.config import settings

class NowPaymentsClient:
    """
    Client minimale per NOWPayments payout. Usa /v1/payout come primaria
    e fallback su /v1/payouts (alcuni account/ambienti differiscono).
    """
    def __init__(self):
        if not settings.NOWPAYMENTS_API_KEY:
            raise RuntimeError("NOWPAYMENTS_API_KEY missing")
        self.base_url = settings.NOWPAYMENTS_BASE_URL.rstrip("/")
        self.headers = {
            "x-api-key": settings.NOWPAYMENTS_API_KEY,
            "Content-Type": "application/json",
        }

    async def _post_json(self, url: str, payload: dict) -> dict:
        async with aiohttp.ClientSession(headers=self.headers) as s:
            async with s.post(url, json=payload) as r:
                data = await r.json(content_type=None)
                if r.status >= 400:
                    raise RuntimeError(f"NOWPayments {r.status}: {data}")
                return data

    async def create_payout(
        self, *, amount_eur: float, iban: str, beneficiary_name: str, reference: str
    ) -> dict:
        """
        Crea payout EUR (SEPA). Alcuni account richiedono campi addizionali
        (es. titolare, SWIFT/BIC). Se ottieni 4xx, arricchisci il payload.
        """
        payload = {
            "withdrawals": [{
                "address": iban,              # IBAN
                "amount": amount_eur,         # EUR
                "currency": "eur",            # valuta fiat
                "extra_id": beneficiary_name, # alias/memo
                "custom_id": reference        # referenza nostro ordine
            }]
        }

        # Primo tentativo
        try:
            return await self._post_json(f"{self.base_url}/payout", payload)
        except Exception:
            # Fallback su /payouts
            return await self._post_json(f"{self.base_url}/payouts", payload)
