# in cima al file
from services.api.db import metadata, engine
metadata.create_all(engine)
# services/api/main.py
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from services.api.config import settings
from services.api.routers.offramp import router as offramp_router
from services.api.routers.otc import router as otc_router
from services.api.routers.changenow import router as changenow_router

app = FastAPI(title="changenow-offramp-pro")

# CORS config
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def health():
    return {"ok": True, "service": "changenow-offramp-pro", "env": "production"}

# Routers
app.include_router(otc_router)
app.include_router(changenow_router)
app.include_router(offramp_router)
