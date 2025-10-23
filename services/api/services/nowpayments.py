# services/api/services/nowpayments.py
from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional

import httpx


class NowPaymentsError(RuntimeError):
    """Errore proveniente da NOWPayments (HTTP >= 400)."""
    def __init__(self, status: int, body: Any):
        super().__init__(f"NOWPayments {status}: {body}")
        self.status = status
        self.body = body


class NowPaymentsClient:
    """
    Client minimale NOWPayments per payout bancari in EUR.
    - Usa 'x-api-key' da env NOWPAYMENTS_API_KEY
    - Base URL da env NOWPAYMENTS_BASE_URL (default: https://api.nowpayments.io/v1)
    - Prova /payout poi /payouts (fallback)
    - Supporta campi extra via env NOWPAYMENTS_BANK_EXTRA_JSON (JSON string)
      es: {"bank_swift":"DEUTDEFFXXX","bank_country":"DE","beneficiary_address":"..."}
    """

    def __init__(self) -> None:
        self.api_key = os.getenv("NOWPAYMENTS_API_KEY", "")
        if not self.api_key:
            raise RuntimeError("NOWPAYMENTS_API_KEY non configurata")

        self.base_url = os.getenv("NOWPAYMENTS_BASE_URL", "https://api.nowpayments.io/v1").rstrip("/")
        self.timeout = float(os.getenv("NOWPAYMENTS_TIMEOUT", "30"))
        self._bank_extra_json = os.getenv("NOWPAYMENTS_BANK_EXTRA_JSON", "").strip()

        headers = {
            "x-api-key": self.api_key,
            "Content-Type": "application/json",
        }
        self._client = httpx.Client(base_url=self.base_url, headers=headers, timeout=self.timeout)

    def _close(self) -> None:
        try:
            self._client.close()
        except Exception:
            pass

    def __del__(self) -> None:
        self._close()

    # ------- Helpers -------
    def _post_json(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        r = self._client.post(path, json=payload)
        # Alcune risposte possono non avere content-type json corretto
        try:
            data = r.json()
        except Exception:
            data = {"raw": r.text}

        if r.status_code >= 400:
            raise NowPaymentsError(r.status_code, data)
        return data

    def _build_withdrawal(
        self,
        *,
        amount_eur: float,
        iban: str,
        beneficiary_name: str,
        reference: str,
    ) -> Dict[str, Any]:
        """
        Struttura withdrawal generica. Alcuni account richiedono campi extra:
        - bank_swift / bank_country / bank_name / beneficiary_address / bank_address
        - puoi passarli via NOWPAYMENTS_BANK_EXTRA_JSON (stringa JSON).
        """
        wd: Dict[str, Any] = {
            "amount": float(amount_eur),
            "currency": "eur",
            "address": iban,                # IBAN
            "extra_id": beneficiary_name,   # usato come memo/beneficiario
            "custom_id": reference,         # collega all'order_id
        }

        if self._bank_extra_json:
            try:
                extra = json.loads(self._bank_extra_json)
                if isinstance(extra, dict):
                    wd.update(extra)        # merge campi extra
            except Exception:
                # ignora JSON malformato
                pass

        return wd

    # ------- API -------

    def create_bank_payout(
        self,
        *,
        amount_eur: float,
        iban: str,
        beneficiary_name: str,
        reference: str,
    ) -> Dict[str, Any]:
        """
        Crea payout EUR su NOWPayments.
        Ritorna il dict della risposta provider.
        Solleva NowPaymentsError su HTTP >= 400.
        """
        withdrawal = self._build_withdrawal(
            amount_eur=amount_eur,
            iban=iban,
            beneficiary_name=beneficiary_name,
            reference=reference,
        )

        # Formato 1: /v1/payout accetta {"withdrawals":[{...}]}
        payload_v1 = {"withdrawals": [withdrawal]}

        # 1) prova /payout
        try:
            return self._post_json("/payout", payload_v1)
        except NowPaymentsError as e:
            # Se 404/405/422, potrebbe essere endpoint diverso: tenta /payouts
            if e.status in (404, 405, 422):
                # Alcuni ambienti accettano lo stesso payload anche su /payouts
                return self._post_json("/payouts", payload_v1)
            raise
