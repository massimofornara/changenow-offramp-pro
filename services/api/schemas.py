from pydantic import BaseModel, Field, AnyHttpUrl, constr
from typing import Optional, List
from uuid import UUID

class OTCListing(BaseModel):
    token_symbol: constr(strip_whitespace=True, to_lower=False)
    price_eur: float
    available_amount: float
    updated_at: Optional[str] = None

class SetPriceRequest(BaseModel):
    token_symbol: constr(strip_whitespace=True, to_lower=False)
    price_eur: float
    available_amount: float

class SellOrderRequest(BaseModel):
    token_symbol: str = "NENO"
    amount_tokens: float
    iban: str
    beneficiary_name: str
    redirect_url: Optional[AnyHttpUrl] = None

class SellOrderResponse(BaseModel):
    order_id: UUID
    status: str
    amount_eur: float
    changenow_payment_url: Optional[AnyHttpUrl] = None

class OrderOut(BaseModel):
    order_id: UUID
    token_symbol: str
    amount_tokens: float
    price_eur: Optional[float] = None
    amount_eur: Optional[float] = None
    iban: Optional[str] = None
    beneficiary_name: Optional[str] = None
    status: str
    changenow_tx_id: Optional[str] = None
    nowpayments_payout_id: Optional[str] = None
    created_at: str
    updated_at: str

class NPWebhook(BaseModel):
    # Minimal placeholder; adapt to NOWPayments IPN schema
    payment_id: Optional[str] = None
    payout_id: Optional[str] = None
    order_id: Optional[str] = None
    payment_status: Optional[str] = None
    payout_status: Optional[str] = None
    amount: Optional[float] = None
    currency: Optional[str] = None
    signature: Optional[str] = None
    created_at: Optional[str] = None
