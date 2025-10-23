from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, insert, update
from ..db import SessionLocal, otc_listings
from ..schemas import OTCListing, SetPriceRequest
from ..config import settings

router = APIRouter(prefix="/otc", tags=["OTC"])

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@router.get("/listings", response_model=list[OTCListing])
def get_listings(db = Depends(get_db)):
    res = db.execute(select(otc_listings)).mappings().all()
    out = []
    for r in res:
        row = dict(r)
        if row.get("updated_at"):
            # cast a stringa ISO per evitare 500
            row["updated_at"] = row["updated_at"].isoformat()
        out.append(OTCListing(**row))
    return out
@router.post("/set-price")
def set_price(body: SetPriceRequest, db = Depends(get_db)):
    token = body.token_symbol.upper()
    existing = db.execute(select(otc_listings).where(otc_listings.c.token_symbol == token)).mappings().first()
    if existing:
        db.execute(update(otc_listings)
                   .where(otc_listings.c.token_symbol == token)
                   .values(price_eur=body.price_eur, available_amount=body.available_amount))
    else:
        db.execute(insert(otc_listings)
                   .values(token_symbol=token, price_eur=body.price_eur, available_amount=body.available_amount))
    db.commit()
    return {"ok": True, "token": token, "price_eur": f"{body.price_eur}"}
