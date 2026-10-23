from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from db import engine
from routers.admin import router as admin_router
from routers.auth import router as auth_router
from routers.catalysts import router as catalysts_router
from routers.entities import router as entities_router
from routers.imports import router as imports_router
from routers.instruments import router as instruments_router
from routers.market_data import router as market_data_router
from routers.notification_preferences import router as notification_preferences_router
from routers.portfolios import router as portfolios_router
from routers.positions import router as positions_router
from routers.search import router as search_router
from routers.watchlist_items import router as watchlist_items_router
from routers.watchlists import router as watchlists_router

app = FastAPI(title="Market Intelligence Platform")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://mip.internal:5173",
        "http://mip.internal:5174",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(admin_router, prefix="/api/admin", tags=["admin"])
app.include_router(auth_router, prefix="/api/auth", tags=["auth"])
app.include_router(entities_router, prefix="/api/entities", tags=["entities"])
app.include_router(imports_router, prefix="/api/import", tags=["import"])
app.include_router(instruments_router, prefix="/api/instruments", tags=["instruments"])
app.include_router(market_data_router, prefix="/api/market-data", tags=["market-data"])
app.include_router(
    notification_preferences_router,
    prefix="/api/notification-preferences",
    tags=["notification-preferences"],
)
app.include_router(portfolios_router, prefix="/api/portfolios", tags=["portfolios"])
app.include_router(positions_router, prefix="/api/positions", tags=["positions"])
app.include_router(search_router, prefix="/api/search", tags=["search"])
app.include_router(
    watchlist_items_router, prefix="/api/watchlist-items", tags=["watchlist-items"]
)
app.include_router(watchlists_router, prefix="/api/watchlists", tags=["watchlists"])
app.include_router(
    catalysts_router,
    prefix="/api/catalysts",
    tags=["catalysts"],
)


@app.get("/health")
def health():
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    return {"status": "ok"}
