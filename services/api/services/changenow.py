from __future__ import annotations
import httpx
from typing import Optional, Dict, Any
from ..config import settings
from loguru import logger

class ChangeNowClient:
    def __init__(self, api_key: str | None = None, base_url: str | None = None):
        self.api_key = api_key or settings.CHANGENOW_API_KEY
        self.base_url = (base_url or str(settings.CHANGENOW_BASE_URL)).rstrip("/")
        self.headers = {"x-changenow-api-key": self.api_key, "Content-Type": "application/json"}

    async def get_min_amount(self, from_ticker: str, to_currency: str) -> Dict[str, Any]:
        url = f"{self.base_url}/min-amount/{from_ticker}/{to_currency}"
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.get(url, headers=self.headers)
            r.raise_for_status()
            return r.json()

    async def estimate(self, from_ticker: str, to_currency: str, from_amount: float) -> Dict[str, Any]:
        url = f"{self.base_url}/exchange-amount/{from_amount}/{from_ticker}_{to_currency}"
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.get(url, headers=self.headers)
            r.raise_for_status()
            return r.json()

    async def create_sell_transaction(self, from_ticker: str, to_currency: str, from_amount: float,
                                      payout_address: str, refund_address: Optional[str] = None,
                                      partner_ref_id: Optional[str] = None) -> Dict[str, Any]:
        """Creates a SELL (crypto -> fiat currency) transaction.
        Note: Endpoint/fields may vary; adapt to latest ChangeNOW docs.
        """
        url = f"{self.base_url}/transactions"
        payload = {
            "fromCurrency": from_ticker.lower(),
            "toCurrency": to_currency.lower(),  # typically 'eur'
            "fromAmount": str(from_amount),
            "payoutAddress": payout_address,    # IBAN or provider-specific handle if supported
        }
        if refund_address:
            payload["refundAddress"] = refund_address
        if settings.CHANGENOW_REF_ID or partner_ref_id:
            payload["referralCode"] = partner_ref_id or settings.CHANGENOW_REF_ID

        logger.info(f"Creating ChangeNOW sell tx: {payload}")
        async with httpx.AsyncClient(timeout=60.0) as client:
            r = await client.post(url, headers=self.headers, json=payload)
            # For sandbox-less environments, this may return 4xx until approved/account ready.
            if r.status_code >= 400:
                logger.error(f"ChangeNOW create tx error: {r.status_code} {r.text}")
            r.raise_for_status()
            return r.json()

    def public_sell_url(self, from_ticker: str, to_currency: str, amount: float, redirect_url: Optional[str] = None) -> str:
        base = str(settings.CHANGENOW_PUBLIC_URL) if hasattr(settings, "CHANGENOW_PUBLIC_URL") else str(settings.CHANGENOW_PUBLIC_SELL_URL)
        params = {
            "from": from_ticker.lower(),
            "to": to_currency.lower(),
            "amount": str(amount),
        }
        if settings.CHANGENOW_REF_ID:
            params["ref_id"] = settings.CHANGENOW_REF_ID
        if redirect_url:
            params["redirect_url"] = redirect_url
        # Build querystring
        from urllib.parse import urlencode
        return f"{base}?{urlencode(params)}"
