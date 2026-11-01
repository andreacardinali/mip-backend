from dataclasses import asdict

from market_analysis.quant_prefilter import score_evaluated_signal
from services.event_contract import EventEnvelope, stable_event_key
from services.event_persistence import save_event

COMPUTATION_VERSION = "market-analysis-v1"


def save_scan_evaluations(
    db, repository, *, run_id, members, result, config, quant_config
):
    event_ids = {}
    if not result.evaluations:
        return event_ids
    member_by_id = {str(member.instrument_id): member for member in members}
    profile = {
        "version": COMPUTATION_VERSION,
        "config": {
            "period": config.period,
            "interval": config.interval,
            "timeframe": config.timeframe,
            "technical_config": asdict(config.technical_config),
        },
    }
    profile_key = stable_event_key(profile)
    for evaluation in result.evaluations:
        assessment_config = asdict(config)
        assessment_config["enabled_strategies"] = sorted(set(evaluation.strategy_codes))
        assessment_profile = {
            "version": COMPUTATION_VERSION,
            "config": assessment_config,
            "quant": asdict(quant_config),
        }
        member = member_by_id[str(evaluation.instrument_id)]
        subjects = [
            {
                "instrument_id": str(member.instrument_id),
                "entity_id": str(member.entity_id) if member.entity_id else None,
            }
        ]
        observation_key = stable_event_key(
            member.instrument_id, profile_key, evaluation.observed_at
        )
        observation_id = save_event(
            db,
            EventEnvelope(
                event_type="MARKET_OBSERVATION",
                stage="ACQUIRED",
                source="MIP",
                event_key=observation_key,
                subjects=subjects,
                occurred_at=evaluation.observed_at,
                payload={
                    "profile": profile,
                    "snapshot": evaluation.snapshot,
                    "input_fingerprint": evaluation.input_fingerprint,
                    "bar_count": evaluation.bar_count,
                    "timeframe": evaluation.timeframe,
                },
            ),
            lake="analytical",
        )
        candidates = []
        for evaluated in result.candidates:
            if str(evaluated.member.instrument_id) != str(evaluation.instrument_id):
                continue
            quant_score, breakdown = score_evaluated_signal(
                evaluated, config=quant_config
            )
            candidates.append(
                {
                    "candidate": evaluated.candidate.to_payload(),
                    "scores": evaluated.scores.to_dict(),
                    "quant": {"score": quant_score, "breakdown": breakdown},
                }
            )
        candidates.sort(
            key=lambda item: (
                item["candidate"]["strategy_code"],
                item["candidate"]["direction"],
            )
        )
        assessment_id = save_event(
            db,
            EventEnvelope(
                event_type="SIGNAL_ASSESSMENT",
                stage="VALIDATED",
                source="MIP",
                event_key=stable_event_key(observation_key, assessment_profile),
                subjects=subjects,
                occurred_at=evaluation.observed_at,
                parent_event_ids=[observation_id],
                payload={
                    "profile": assessment_profile,
                    "strategy_codes": sorted(set(evaluation.strategy_codes)),
                    "candidates": candidates,
                    "outcome": "SIGNAL" if candidates else "NO_SIGNAL",
                },
            ),
            lake="analytical",
        )
        for event_id in (observation_id, assessment_id):
            repository.link_scan_event(run_id=run_id, event_id=event_id)
        event_ids[str(evaluation.instrument_id)] = assessment_id
    return event_ids
