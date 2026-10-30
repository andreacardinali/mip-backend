import asyncio
from pathlib import Path
from unittest.mock import Mock, patch
from uuid import UUID

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.exc import DBAPIError

ENTITY_ID = UUID("00000000-0000-0000-0000-000000000001")
USER_ID = "00000000-0000-0000-0000-000000000002"


@pytest.fixture
def routes():
    with (
        patch("sqlalchemy.create_engine"),
        patch(
            "config.settings.settings", Mock(DATABASE_URL="postgresql+psycopg://test")
        ),
    ):
        from routers import entities, market_screener
    return entities, market_screener


@pytest.mark.parametrize("payload", [None, {"entity": {"entity_id": str(ENTITY_ID)}}])
def test_overview_uses_authenticated_user_and_closes_session(
    monkeypatch, payload, routes
):
    entities, _ = routes
    captured = {}

    class Session:
        def execute(self, statement, params):
            captured.update(params)
            assert "sp_get_user_entity_overview" in str(statement)
            return self

        def scalar_one_or_none(self):
            return payload

        def close(self):
            captured["closed"] = True

    monkeypatch.setattr(entities, "SessionLocal", Session)
    if payload is None:
        with pytest.raises(HTTPException) as caught:
            entities.get_entity_overview(ENTITY_ID, {"user_id": USER_ID})
        assert caught.value.status_code == 404
    else:
        assert entities.get_entity_overview(ENTITY_ID, {"user_id": USER_ID}) == payload
    assert captured == {"user_id": USER_ID, "entity_id": str(ENTITY_ID), "closed": True}


@pytest.mark.parametrize("missing", [False, True])
def test_context_instruments_keeps_owner_checks(monkeypatch, missing, routes):
    _, market_screener = routes
    captured = {}

    class Session:
        def close(self):
            captured["closed"] = True

    class Repository:
        def __init__(self, session):
            pass

        def context_instruments(self, **params):
            captured.update(params)
            if missing:
                original = Exception("missing context")
                original.pgcode = "P0002"
                raise DBAPIError("test", {}, original)
            return [{"entity_id": str(ENTITY_ID)}]

    monkeypatch.setattr(market_screener, "SessionLocal", Session)
    monkeypatch.setattr(market_screener, "MarketScreenerApiRepository", Repository)
    if missing:
        with pytest.raises(HTTPException) as caught:
            market_screener.list_context_instruments(
                "UNIVERSE", ENTITY_ID, {"user_id": USER_ID}
            )
        assert caught.value.status_code == 404
    else:
        assert market_screener.list_context_instruments(
            "UNIVERSE", ENTITY_ID, {"user_id": USER_ID}
        ) == [{"entity_id": str(ENTITY_ID)}]
    assert captured == {
        "user_id": USER_ID,
        "context_type": "UNIVERSE",
        "context_id": str(ENTITY_ID),
        "closed": True,
    }


@pytest.mark.parametrize("watchlist_match", [None, False, True])
def test_opportunities_forward_user_membership_filters(
    monkeypatch, routes, watchlist_match
):
    _, market_screener = routes
    captured = {}

    class Session:
        def close(self):
            captured["closed"] = True

    class Repository:
        def __init__(self, session):
            pass

        def list_opportunities(self, **params):
            captured.update(params)
            return []

    monkeypatch.setattr(market_screener, "SessionLocal", Session)
    monkeypatch.setattr(market_screener, "MarketScreenerApiRepository", Repository)
    assert (
        market_screener.list_screener_opportunities(
            portfolio_match=True,
            watchlist_match=watchlist_match,
            strategy_code=None,
            minimum_score=0,
            limit=1,
            current_user={"user_id": USER_ID},
        )
        == []
    )
    assert captured["user_id"] == USER_ID
    assert captured["portfolio_match"] is True
    assert captured["watchlist_match"] is watchlist_match
    assert captured["limit"] == 1
    assert captured["closed"] is True


@pytest.mark.parametrize("scope", ["ALL", "PORTFOLIO", "WATCHLIST", "SIGNALS"])
def test_entity_list_filters_before_limit_for_authenticated_user(monkeypatch, routes, scope):
    entities, _ = routes
    captured = {}

    class Session:
        def execute(self, statement, params):
            assert "sp_get_user_entities" in str(statement)
            captured.update(params)
            return [Mock(_mapping={"entity_id": str(ENTITY_ID)})]

        def close(self):
            captured["closed"] = True

    monkeypatch.setattr(entities, "SessionLocal", Session)
    assert asyncio.run(entities.get_entities(
        current_user={"user_id": USER_ID}, scope=scope, query="  ABC  "
    )) == [{"entity_id": str(ENTITY_ID)}]
    assert captured == {
        "user_id": USER_ID, "scope": scope, "query": "ABC", "closed": True,
    }


@pytest.mark.parametrize("scope", ["ALL", "PORTFOLIO", "WATCHLIST", "SIGNALS", "UNKNOWN"])
def test_entity_list_http_scope_validation(monkeypatch, routes, scope):
    entities, _ = routes
    session = Mock()
    session.execute.return_value = []
    monkeypatch.setattr(entities, "SessionLocal", lambda: session)
    app = FastAPI()
    app.include_router(entities.router, prefix="/entities")
    app.dependency_overrides[entities.get_current_user] = lambda: {"user_id": USER_ID}
    with TestClient(app) as client:
        response = client.get("/entities", params={"scope": scope})
    assert response.status_code == (422 if scope == "UNKNOWN" else 200)
    if scope == "UNKNOWN":
        session.execute.assert_not_called()
    else:
        assert session.execute.call_args.args[1]["scope"] == scope
        session.close.assert_called_once()


def test_entity_list_installation_matches_authoritative_schema():
    root = Path(__file__).resolve().parents[1]
    schema = (root / "mip_schema.sql").read_text(encoding="utf-8")
    installer = (root / "oneshot" / "install_entity_list_filters.sql").read_text(
        encoding="utf-8"
    )
    signature = "FUNCTION public.sp_get_user_entities"
    schema_body = schema.split(signature, 1)[1].split("$$;", 1)[0]
    assert schema_body == installer.split(signature, 1)[1].split("$$;", 1)[0]
    assert "sp_get_user_entity_asset_match(" in schema_body
    assert "o.entity_id = e.entity_id AND o.active" in schema_body
    assert schema_body.index("p_scope = 'PORTFOLIO'") < schema_body.index("LIMIT")
    assert schema_body.index("strpos(lower(value)") < schema_body.index("LIMIT")
    assert "LEFT JOIN public.instruments" not in schema_body
    assert "NULL::varchar AS symbol, NULL::varchar AS exchange" in schema_body
    assert "WHERE i.entity_id = m.entity_id AND EXISTS (" in schema_body
    assert "ARRAY[i.symbol::text, i.exchange::text]" in schema_body
    assert "ORDER BY m.name, m.entity_id\nLIMIT" in schema_body


def test_overview_installation_matches_authoritative_schema():
    root = Path(__file__).resolve().parents[1]
    schema = (root / "mip_schema.sql").read_text(encoding="utf-8")
    installer = (root / "oneshot" / "install_entity_overview.sql").read_text(
        encoding="utf-8"
    )
    signature = "FUNCTION public.sp_get_user_entity_overview"
    schema_body = schema.split(signature, 1)[1].split("$$;", 1)[0]
    installer_body = installer.split(signature, 1)[1].split("$$;", 1)[0]
    assert schema_body == installer_body
    assert "sp_get_user_entity_asset_match(p_user_id, e.entity_id)" in schema_body
    assert "ne.entity_id = e.entity_id" in schema_body
    assert "o.entity_id = e.entity_id AND o.active" in schema_body
