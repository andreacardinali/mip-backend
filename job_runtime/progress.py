from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class BatchProgress:
    job: str
    total_items: int
    max_items_per_batch: int
    started_at: float = field(default_factory=time.monotonic)
    completed_items: int = 0
    completed_batches: int = 0
    llm_calls: int = 0
    split_batches: int = 0
    failed_items: int = 0

    def __post_init__(self) -> None:
        if self.total_items < 0:
            raise ValueError("total_items must be >= 0")
        if self.max_items_per_batch < 1:
            raise ValueError("max_items_per_batch must be >= 1")

    @property
    def planned_batches(self) -> int:
        if self.total_items == 0:
            return 0
        return math.ceil(self.total_items / self.max_items_per_batch)

    @property
    def elapsed_seconds(self) -> float:
        return max(0.0, time.monotonic() - self.started_at)

    @property
    def estimated_remaining_seconds(self) -> float | None:
        if self.completed_items < 1:
            return None
        remaining = max(0, self.total_items - self.completed_items)
        return self.elapsed_seconds / self.completed_items * remaining

    def record_llm_call(self, count: int = 1) -> None:
        self.llm_calls += count

    def record_split(self, count: int = 1) -> None:
        self.split_batches += count

    def complete_batch(self, item_count: int, *, failed_items: int = 0) -> None:
        if item_count < 0 or failed_items < 0:
            raise ValueError("progress increments must be >= 0")
        self.completed_batches += 1
        self.completed_items = min(self.total_items, self.completed_items + item_count)
        self.failed_items += failed_items

    def summary(self) -> dict[str, Any]:
        remaining = self.estimated_remaining_seconds
        return {
            "total_items": self.total_items,
            "max_items_per_batch": self.max_items_per_batch,
            "planned_batches": self.planned_batches,
            "completed_items": self.completed_items,
            "remaining_items": max(0, self.total_items - self.completed_items),
            "completed_batches": self.completed_batches,
            "llm_calls": self.llm_calls,
            "split_batches": self.split_batches,
            "failed_items": self.failed_items,
            "elapsed_seconds": round(self.elapsed_seconds, 3),
            "estimated_remaining_seconds": (
                None if remaining is None else round(remaining, 3)
            ),
        }

    def log_plan(self, logger) -> None:
        logger.info("Job workload planned summary=%s", self.summary())

    def log_progress(self, logger) -> None:
        logger.info("Job workload progress summary=%s", self.summary())

    def log_completed(self, logger) -> None:
        logger.info("Job workload completed summary=%s", self.summary())
