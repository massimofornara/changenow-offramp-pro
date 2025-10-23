# services/api/main.py
from __future__ import annotations

import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse

# Importa solo i router realmente esistenti
from services.api.routers.otc import router as otc_router
from services.api.routers.offramp import router as offramp_router
from services.api.routers.nowpayments import router as nowpayments_router


# -----------------------------------------------------------------------------
# Inizializzazione dell'app FastAPI
# -----------------------------------------------------------------------------
app = FastAPI(
    title="ChangeNOW Offramp Pro",
    description="API backend per flussi OTC + Offramp con trigger payout reale",
    version="1.0.0",
)

# -----------------------------------------------------------------------------
# CORS (necessario per frontend / widget esterni)
# -----------------------------------------------------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # puoi restringere in produzione
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# -----------------------------------------------------------------------------
# Routers registrati
# -----------------------------------------------------------------------------
app.include_router(otc_router)
app.include_router(offramp_router)
app.include_router(nowpayments_router)

# -----------------------------------------------------------------------------
# Healthcheck e base endpoint
# -----------------------------------------------------------------------------
@app.get("/", response_class=JSONResponse)
async def root():
    """
    Health check semplice, utile per Render.
    """
    return {
        "ok": True,
        "service": "changenow-offramp-pro",
        "env": os.getenv("ENV", "production"),
    }


# -----------------------------------------------------------------------------
# Error handler base (catch globale)
# -----------------------------------------------------------------------------
@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    print(f"[ERROR] {type(exc).__name__}: {exc}")
    return JSONResponse(
        status_code=500,
        content={
            "ok": False,
            "error": str(exc),
            "detail": "Errore interno del server. Controlla i log Render.",
        },
    )


# -----------------------------------------------------------------------------
# Avvio locale (solo debug)
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "services.api.main:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", 8000)),
        reload=True,
    )
