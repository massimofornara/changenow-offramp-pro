from fastapi import APIRouter, Query

router = APIRouter(prefix="/changenow", tags=["changenow"])

@router.get("/health")
async def health():
    return {"ok": True, "router": "changenow_widget"}

@router.get("/widget-sell-eur")
async def widget_sell_eur(
    amount: float = Query(..., gt=0),
    from_symbol: str = Query("usdt"),
    redirect_url: str = Query(""),
):
    # Qui normalmente restituiresti l'URL del widget ChangeNOW SELL precompilato.
    # Lasciamo un payload “ready to use” così puoi costruire facilmente il link lato FE.
    return {
        "ok": True,
        "intent": "sell_eur_widget",
        "amount": amount,
        "from_symbol": from_symbol.lower(),
        "redirect_url": redirect_url,
        "hint": "Costruisci il link del widget con questi parametri lato FE."
    }
