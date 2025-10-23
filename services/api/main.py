from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from services.api.config import settings

# ... istanzia FastAPI prima

from .db import init_db
from .routers.otc import router as otc_router
from .routers.offramp import router as offramp_router
from .routers.changenow_widget import router as cn_widget_router

app = FastAPI(title="ChangeNOW Offramp PRO (with NOWPayments)", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
def on_startup():
    init_db()

app.include_router(otc_router)
app.include_router(offramp_router)
app.include_router(cn_widget_router)

@app.get("/")
def root():
    return {"ok": True, "service": "changenow-offramp-pro", "env": settings.ENV}
