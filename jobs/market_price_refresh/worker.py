from services.market_price_refresh import process_price_refresh_queue


def run() -> dict:
    return process_price_refresh_queue()
