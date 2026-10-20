from job_runtime.database import run_transactional
from market_data.factory import market_data
from services.exchange_rates import refresh_exchange_rates


def run() -> dict:
    return run_transactional(
        lambda db: refresh_exchange_rates(
            db,
            market_data_service=market_data,
        )
    )
