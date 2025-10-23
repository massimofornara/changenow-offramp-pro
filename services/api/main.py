from fastapi import FastAPI

from services.api.routers.offramp import router as offramp_router
from services.api.routers.nowpayments import router as nowpayments_router
from services.api.routers.changenow_widget import router as changenow_widget_router

app = FastAPI(title="changenow-offramp-pro")

app.include_router(offramp_router)
app.include_router(nowpayments_router)
app.include_router(changenow_widget_router)

@app.get("/")
async def root():
    return {"ok": True, "service": "changenow-offramp-pro", "env": "production"}
