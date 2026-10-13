from fastapi import FastAPI
from db import engine
from sqlalchemy import text

from routers.watchlists import router as watchlists
from routers.portfolios import router as portfolios

app = FastAPI(
    title="Market Intelligence Platform"
)

app.include_router(
    watchlists,
    prefix="/api/watchlists"
)

app.include_router(
    portfolios,
    prefix="/api/portfolios"
)



@app.get("/health")
def health():
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))

    return {
        "status": "ok"
    }