from pathlib import Path


def test_api_enriches_existing_opportunity_payloads() -> None:
    repository = Path("market_screener/api_repository.py").read_text(encoding="utf-8")
    assert repository.count("sp_market_screener_enrich_opportunity_payload") == 2
    assert "sp_market_screener_list_user_opportunities" in repository
    assert "sp_market_screener_get_user_opportunity" in repository


def test_gui_handles_missing_signal_arrays() -> None:
    page = Path("../mip-frontend/src/pages/ScreenerPage.tsx").read_text(
        encoding="utf-8"
    )
    assert "selected.signal_reasons ?? []" in page
