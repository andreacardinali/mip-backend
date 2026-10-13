from fastapi import FastAPI

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