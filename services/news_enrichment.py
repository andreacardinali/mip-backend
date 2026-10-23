import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from config.settings import settings
from llm.base import LLMProvider, LLMRequest
from llm.config import LLMConfig
from llm.factory import create_provider

logger = logging.getLogger(__name__)

PROMPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "jobs"
    / "news_enrichment"
    / "prompts"
    / "news_entity_scoring.txt"
)

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "scores": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "news_entity_id": {"type": "string"},
                    "relevance": {"type": "number"},
                    "sentiment": {"type": "number"},
                    "impact": {"type": "number"},
                },
                "required": [
                    "news_entity_id",
                    "relevance",
                    "sentiment",
                    "impact",
                ],
                "additionalProperties": False,
            },
        }
    },
    "required": ["scores"],
    "additionalProperties": False,
}


@dataclass(frozen=True, slots=True)
class NewsScore:
    news_entity_id: str
    relevance: float
    sentiment: float
    impact: float


@dataclass(slots=True)
class EnrichmentStats:
    pending: int = 0
    batches: int = 0
    split_batches: int = 0
    llm_calls: int = 0
    scored: int = 0
    failed_items: int = 0

    def to_dict(self) -> dict[str, int]:
        return {
            "pending": self.pending,
            "batches": self.batches,
            "split_batches": self.split_batches,
            "llm_calls": self.llm_calls,
            "scored": self.scored,
            "failed_items": self.failed_items,
        }


def _as_int(name: str, default: int, *, minimum: int = 1) -> int:
    value = int(settings.get(name, default))
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return value


def _clamp(value: Any, minimum: float, maximum: float) -> float:
    numeric = float(value)
    return max(minimum, min(maximum, numeric))


def _load_pending(db: Session, limit: int) -> list[dict[str, Any]]:
    rows = db.execute(
        text(
            """
            SELECT *
            FROM public.sp_news_list_pending_entity_scores(:limit)
            """
        ),
        {"limit": limit},
    ).mappings().all()
    return [dict(row) for row in rows]


def _batches(values: list[dict[str, Any]], size: int):
    for start in range(0, len(values), size):
        yield values[start : start + size]


def _prompt(rows: list[dict[str, Any]]) -> str:
    template = PROMPT_PATH.read_text(encoding="utf-8")
    payload = [
        {
            "news_entity_id": str(row["news_entity_id"]),
            "entity_name": row.get("entity_name"),
            "symbols": row.get("symbols") or [],
            "title": row.get("title"),
            "summary": row.get("summary"),
            "source_name": row.get("source_name"),
            "published_at": (
                row["published_at"].isoformat()
                if hasattr(row.get("published_at"), "isoformat")
                else row.get("published_at")
            ),
        }
        for row in rows
    ]
    input_json = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    placeholder = "{input_json}"
    if placeholder not in template:
        raise ValueError(
            f"Prompt template is missing placeholder: {placeholder}"
        )
    return template.replace(placeholder, input_json)


def _parse_scores(
    data: dict[str, Any],
    expected_ids: set[str],
) -> list[NewsScore]:
    raw_scores = data.get("scores")
    if not isinstance(raw_scores, list):
        raw_scores = data.get("news_entity_scores")
    if not isinstance(raw_scores, list):
        raw_scores = data.get("results")
    if not isinstance(raw_scores, list):
        available_keys = sorted(str(key) for key in data)
        raise ValueError(
            "LLM response must contain a scores array; "
            f"available_keys={available_keys}"
        )

    scores: list[NewsScore] = []
    seen: set[str] = set()

    for item in raw_scores:
        if not isinstance(item, dict):
            raise ValueError("Each score must be an object")

        score_id = str(item.get("news_entity_id") or "").strip()
        if score_id not in expected_ids:
            raise ValueError(f"Unexpected news_entity_id: {score_id}")
        if score_id in seen:
            raise ValueError(f"Duplicate news_entity_id: {score_id}")

        seen.add(score_id)
        scores.append(
            NewsScore(
                news_entity_id=score_id,
                relevance=_clamp(item.get("relevance"), 0.0, 1.0),
                sentiment=_clamp(item.get("sentiment"), -1.0, 1.0),
                impact=_clamp(item.get("impact"), 0.0, 1.0),
            )
        )

    missing = expected_ids - seen
    if missing:
        raise ValueError(
            f"Missing news_entity_id values: {sorted(missing)}"
        )

    return scores


def _persist_scores(db: Session, scores: list[NewsScore]) -> int:
    payload = [
        {
            "news_entity_id": score.news_entity_id,
            "relevance": score.relevance,
            "sentiment": score.sentiment,
            "impact": score.impact,
        }
        for score in scores
    ]

    return int(
        db.execute(
            text(
                """
                SELECT public.sp_news_update_entity_scores(
                    CAST(:scores AS jsonb)
                )
                """
            ),
            {
                "scores": json.dumps(
                    payload,
                    separators=(",", ":"),
                )
            },
        ).scalar_one()
    )


def _score_batch(
    db: Session,
    *,
    provider: LLMProvider,
    rows: list[dict[str, Any]],
    max_tokens: int,
    stats: EnrichmentStats,
) -> None:
    if not rows:
        return

    expected_ids = {
        str(row["news_entity_id"])
        for row in rows
    }
    stats.llm_calls += 1

    try:
        response = provider.generate_json(
            LLMRequest(
                prompt=_prompt(rows),
                temperature=0.0,
                max_tokens=max_tokens,
                grounded_search=False,
                response_schema=RESPONSE_SCHEMA,
            )
        )
        scores = _parse_scores(response.data, expected_ids)

        with db.begin_nested():
            updated = _persist_scores(db, scores)
        db.commit()
        stats.scored += updated
        logger.info(
            "News enrichment batch committed size=%s updated=%s",
            len(rows),
            updated,
        )
        return
    except Exception as error:
        if db.in_transaction():
            db.rollback()
        if len(rows) == 1:
            stats.failed_items += 1
            logger.exception(
                "News enrichment item failed "
                "news_entity_id=%s error=%s",
                next(iter(expected_ids)),
                error,
            )
            return

        midpoint = len(rows) // 2
        left = rows[:midpoint]
        right = rows[midpoint:]
        stats.split_batches += 1

        logger.warning(
            "News enrichment batch failed; splitting "
            "size=%s left=%s right=%s error=%s",
            len(rows),
            len(left),
            len(right),
            error,
        )

        _score_batch(
            db,
            provider=provider,
            rows=left,
            max_tokens=max_tokens,
            stats=stats,
        )
        _score_batch(
            db,
            provider=provider,
            rows=right,
            max_tokens=max_tokens,
            stats=stats,
        )


def enrich_news(db: Session) -> dict[str, int]:
    limit = _as_int("NEWS_ENRICHMENT_LIMIT", 200)
    batch_size = _as_int("NEWS_ENRICHMENT_BATCH_SIZE", 4)
    max_tokens = _as_int("NEWS_ENRICHMENT_MAX_TOKENS", 8192)

    config = LLMConfig.from_settings(
        settings,
        prefix="NEWS_ENRICHMENT_LLM",
        default_provider="gemini",
        default_timeout=120.0,
    )
    provider = create_provider(config)
    pending = _load_pending(db, limit)
    db.rollback()
    stats = EnrichmentStats(pending=len(pending))

    for batch in _batches(pending, batch_size):
        stats.batches += 1
        _score_batch(
            db,
            provider=provider,
            rows=batch,
            max_tokens=max_tokens,
            stats=stats,
        )

    result = stats.to_dict()
    logger.info("News enrichment stats=%s", result)
    return result
