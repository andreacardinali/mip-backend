import logging

from config.settings import settings
from db import SessionLocal
from job_runtime.requests import current_job_request
from job_runtime.run_logging import current_job_run_id
from market_analysis.scan import ScanConfig
from market_data.factory import market_data
from market_screener.analysis_request import ScreenerAnalysisRequest
from market_screener.coordination import hold_incremental_analysis_lock
from market_screener.run_repository import MarketScreenerRunRepository
from market_screener.service import analyze_screener_context

logger = logging.getLogger(__name__)


def run() -> dict:
    execution = current_job_request.get()
    if not execution or not execution.get("requested_by"):
        raise ValueError("Context analysis requires a scheduler execution request")
    request = ScreenerAnalysisRequest.model_validate(execution["payload"])
    db = SessionLocal()
    try:
        with hold_incremental_analysis_lock(
            db, owner=f"context:{request.context_type}:{request.context_id}"
        ):
            result = analyze_screener_context(
                db,
                user_id=execution["requested_by"],
                context_type=request.context_type,
                context_id=str(request.context_id),
                market_data_service=market_data,
                config=ScanConfig.from_settings(settings),
                llm_enabled=str(
                    settings.get("MARKET_SCREENER_PIPELINE_LLM_ENABLED", True)
                )
                .strip()
                .lower()
                in {"1", "true", "yes", "on"},
                llm_settings=settings,
                incremental=True,
            )
            db.commit()
        return result
    except ValueError as error:
        db.rollback()
        if str(error) == "Analysis already running":
            return {"status": "SKIPPED", "reason": "analysis_already_running"}
        _fail_run(db, error)
        raise
    except Exception as error:
        db.rollback()
        _fail_run(db, error)
        raise
    finally:
        db.close()


def _fail_run(db, error: Exception) -> None:
    try:
        MarketScreenerRunRepository(db).fail_for_job(
            job_run_id=str(current_job_run_id.get()), error_message=str(error)
        )
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Unable to mark the contextual Screener run as failed")
