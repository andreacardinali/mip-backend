import json
import logging
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.orm import Session

from config.settings import settings
from llm.base import LLMProvider, LLMRequest
from llm.config import LLMConfig
from llm.factory import create_provider
from llm.fingerprint import fingerprint
from prompting.repository import FileSystemPromptRepository

logger = logging.getLogger(__name__)
PROMPTS = FileSystemPromptRepository(
    Path(__file__).resolve().parents[1] / "jobs" / "news_enrichment" / "prompts"
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
                "required": ["news_entity_id", "relevance", "sentiment", "impact"],
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

    def to_dict(self):
        return {
            "pending": self.pending,
            "batches": self.batches,
            "split_batches": self.split_batches,
            "llm_calls": self.llm_calls,
            "scored": self.scored,
            "failed_items": self.failed_items,
        }


def _as_int(name, default, minimum=1):
    value = int(settings.get(name, default))
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return value


def _clamp(value, minimum, maximum):
    return max(minimum, min(maximum, float(value)))


def _load_pending(db, limit):
    return [
        dict(row)
        for row in db.execute(
            text("SELECT * FROM public.sp_news_list_pending_entity_scores(:limit)"),
            {"limit": limit},
        )
        .mappings()
        .all()
    ]


def _batches(values, size):
    for start in range(0, len(values), size):
        yield values[start : start + size]


def _prompt(rows):
    template = PROMPTS.get("news_entity_scoring")
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
    return template.render(
        input_json=json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    )


def _parse_scores(data, expected_ids):
    raw = data.get("scores") or data.get("news_entity_scores") or data.get("results")
    if not isinstance(raw, list):
        raise ValueError("LLM response must contain a scores array")
    result = []
    seen = set()
    for item in raw:
        item_id = str(item.get("news_entity_id") or "").strip()
        if item_id not in expected_ids or item_id in seen:
            raise ValueError(f"Unexpected or duplicate news_entity_id: {item_id}")
        seen.add(item_id)
        result.append(
            NewsScore(
                item_id,
                _clamp(item.get("relevance"), 0, 1),
                _clamp(item.get("sentiment"), -1, 1),
                _clamp(item.get("impact"), 0, 1),
            )
        )
    if expected_ids - seen:
        raise ValueError(f"Missing news_entity_id values: {sorted(expected_ids-seen)}")
    return result


def _persist_scores(db, scores):
    payload = [score.__dict__ for score in scores]
    return int(
        db.execute(
            text("SELECT public.sp_news_update_entity_scores(CAST(:scores AS jsonb))"),
            {"scores": json.dumps(payload, separators=(",", ":"))},
        ).scalar_one()
    )


def _score_batch(db, *, provider: LLMProvider, rows, max_tokens, stats):
    if not rows:
        return
    expected = {str(row["news_entity_id"]) for row in rows}
    stats.llm_calls += 1
    template = PROMPTS.get("news_entity_scoring")
    contract_fingerprint = fingerprint(
        {
            "template": template.fingerprint,
            "response_schema": RESPONSE_SCHEMA,
        }
    )
    try:
        response = provider.generate_json(
            LLMRequest(
                prompt=_prompt(rows),
                prompt_fingerprint=contract_fingerprint,
                temperature=0,
                max_tokens=max_tokens,
                grounded_search=False,
                response_schema=RESPONSE_SCHEMA,
            )
        )
        scores = _parse_scores(response.data, expected)
        with db.begin_nested():
            updated = _persist_scores(db, scores)
        db.commit()
        stats.scored += updated
    except Exception as error:
        if db.in_transaction():
            db.rollback()
        if len(rows) == 1:
            stats.failed_items += 1
            logger.exception(
                "News enrichment item failed news_entity_id=%s error=%s",
                next(iter(expected)),
                error,
            )
            return
        midpoint = len(rows) // 2
        stats.split_batches += 1
        _score_batch(
            db,
            provider=provider,
            rows=rows[:midpoint],
            max_tokens=max_tokens,
            stats=stats,
        )
        _score_batch(
            db,
            provider=provider,
            rows=rows[midpoint:],
            max_tokens=max_tokens,
            stats=stats,
        )


def enrich_news(db: Session):
    limit = _as_int("NEWS_ENRICHMENT_RUN_MAX_ENTITIES", 200)
    batch_size = _as_int("NEWS_ENRICHMENT_LLM_REQUEST_MAX_ENTITIES", 4)
    max_tokens = _as_int("NEWS_ENRICHMENT_LLM_REQUEST_MAX_OUTPUT_TOKENS", 8192)
    config = LLMConfig.from_settings(
        settings,
        prefix="NEWS_ENRICHMENT_LLM",
        default_provider="gemini",
        default_timeout_seconds=120.0,
    )
    provider = create_provider(config)
    pending = _load_pending(db, limit)
    db.rollback()
    stats = EnrichmentStats(pending=len(pending))
    for batch in _batches(pending, batch_size):
        stats.batches += 1
        _score_batch(
            db, provider=provider, rows=batch, max_tokens=max_tokens, stats=stats
        )
    return stats.to_dict()
