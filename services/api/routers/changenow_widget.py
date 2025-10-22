from fastapi import APIRouter, Query
from urllib.parse import urlencode, quote
from ..config import settings

router = APIRouter(prefix="/changenow", tags=["ChangeNOW"])

@router.get("/widget-sell-eur")
def widget_sell_eur(amount: float = Query(..., gt=0), from_symbol: str = "usdt", redirect_url: str | None = None):
    base = str(settings.CHANGENOW_PUBLIC_SELL_URL)
    qs = {
        "from": from_symbol.lower(),
        "to": "eur",
        "amount": str(amount),
    }
    if settings.CHANGENOW_REF_ID:
        qs["ref_id"] = settings.CHANGENOW_REF_ID
    if redirect_url:
        qs["redirect_url"] = redirect_url
    url = f"{base}?{urlencode(qs)}"
    return {"url": url}
