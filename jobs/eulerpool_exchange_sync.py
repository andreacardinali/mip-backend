from market_data.exchange_sync import sync_exchange_data


def run():
    from config.settings import settings
    from db import SessionLocal
    from market_data.providers.eulerpool import EulerpoolProvider

    key = settings.get("EULERPOOL_API_KEY")
    if not key:
        raise ValueError("EULERPOOL_API_KEY is required in backend configuration")
    return sync_exchange_data(EulerpoolProvider(key), SessionLocal)
