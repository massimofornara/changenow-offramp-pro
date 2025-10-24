# services/api/services/nowpayments.py
from __future__ import annotations

import os
import time
import httpx
from typing import Any, Dict

class NowPaymentsError(RuntimeError):
    def __init__(self, status: int, body: Any):
        super().__init__(f"NOWPayments {status}: {body}")
        self.status = status
        self.body = body

class NowPaymentsClient:
    """
    Client per payout bancari/mass payouts:
    - /v1/auth -> JWT (scade ~5 minuti)
    - /v1/payout (o /v1/payouts) -> richiede Authorization: Bearer <JWT>
    - x-api-key NON basta per payout.
    """
    def __init__(self) -> None:
        self.base_url = os.getenv("NOWPAYMENTS_BASE_URL", "https://api.nowpayments.io/v1").rstrip("/")
        self.api_key   = os.getenv("NOWPAYMENTS_API_KEY", "")  # può servire per altre chiamate
        self.email     = os.getenv("NOWPAY_EMAIL", "")
        self.password  = os.getenv("NOWPAY_PASSWORD", "")
        if not self.email or not self.password:
            raise RuntimeError("NOWPAY_EMAIL / NOWPAY_PASSWORD non configurati")

        self.timeout = float(os.getenv("NOWPAYMENTS_TIMEOUT", "30"))
        self._jwt: str = ""
        self._jwt_exp: float = 0.0

        self._client = httpx.Client(timeout=self.timeout)

    # ----------------- auth -----------------
    def _auth(self) -> None:
        """Ottiene un nuovo JWT e imposta la scadenza locale (5 minuti)."""
        url = f"{self.base_url}/auth"
        payload = {"email": self.email, "password": self.password}
        r = self._client.post(url, json=payload)
        try:
            data = r.json()
        except Exception:
            raise NowPaymentsError(r.status_code, r.text)

        if r.status_code >= 400 or "token" not in data:
            raise NowPaymentsError(r.status_code, data)

        self._jwt = data["token"]
        # il token scade in ~5 minuti: rinnovo un po’ prima (4 min)
        self._jwt_exp = time.time() + 4 * 60

    def _ensure_jwt(self) -> str:
        if not self._jwt or time.time() >= self._jwt_exp:
            self._auth()
        return self._jwt

    # ----------------- helpers -----------------
    def _post_json(self, path: str, json_body: Dict[str, Any], use_bearer: bool = False) -> Dict[str, Any]:
        headers = {"Content-Type": "application/json"}
        if use_bearer:
            headers["Authorization"] = f"Bearer {self._ensure_jwt()}"
        # opzionale: alcune integrazioni usano anche x-api-key
        if self.api_key:
            headers["x-api-key"] = self.api_key

        r = self._client.post(f"{self.base_url}{path}", json=json_body, headers=headers)
        try:
            data = r.json()
        except Exception:
            data = {"raw": r.text}

        if r.status_code >= 400:
            raise NowPaymentsError(r.status_code, data)
        return data

    # ----------------- API payout -----------------
    def create_bank_payout(self, *, amount_eur: float, iban: str, beneficiary_name: str, reference: str) -> Dict[str, Any]:
        """
        Crea un payout. Alcuni account accettano /payout, altri /payouts.
        Necessita Authorization: Bearer <JWT>.
        """
        withdrawal = {
            "currency": "eur",
            "amount": float(amount_eur),
            "address": iban,                       # IBAN
            "withdrawal_description": reference,   # memo/descrizione
            # altri campi bancari specifici del tuo profilo possono essere richiesti:
            # "bank_swift": "...", "bank_country": "...", ecc.
        }
        payload = {"payouts": [withdrawal]}

        # 1) prova /payout
        try:
            return self._post_json("/payout", payload, use_bearer=True)
        except NowPaymentsError as e:
            # fallback a /payouts se non supportato
            if e.status in (404, 405, 422):
                return self._post_json("/payouts", payload, use_bearer=True)
            raise
