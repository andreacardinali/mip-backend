import json
import socket
import time
from dataclasses import dataclass
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from llm.base import LLMProvider, LLMRequest, LLMResponse
from llm.rate_limit import estimate_tokens
from llm.retry_hints import exception_retry_delay


@dataclass(frozen=True, slots=True)
class PersistentQueueConfig:
    queue_key: str = ""

    max_attempts: int = 3
    tokens_per_minute: int = 0
    requests_per_minute: int = 0
    requests_per_day: int = 0
    daily_reset_timezone: str = "America/Los_Angeles"
    safety_factor: float = 0.90

    poll_seconds: float = 2.0
    stale_seconds: int = 900
    wait_timeout_seconds: float = 900.0

    lease_seconds: int = 300
    heartbeat_seconds: int = 60
    maintenance_interval_seconds: int = 600

    completed_retention_days: int = 7
    failed_retention_days: int = 30
    abandoned_retention_days: int = 30


class PersistentQueuedProvider(LLMProvider):
    """DB-backed FIFO admission queue. The claiming caller executes the request."""

    def __init__(
        self,
        provider: LLMProvider,
        session_factory: sessionmaker,
        *,
        provider_name: str,
        model: str,
        config: PersistentQueueConfig,
    ) -> None:
        if not config.queue_key.strip():
            raise ValueError(
                "PersistentQueueConfig.queue_key cannot be empty "
                "when the persistent queue is enabled"
            )
        self.provider = provider
        self.session_factory = session_factory
        self.provider_name = provider_name
        self.model = model
        self.config = config
        self.worker_id = f"{socket.gethostname()}:{uuid4()}"
        self.max_retries = getattr(provider, "max_retries", 0)
        self._last_maintenance_at = 0.0
        self._maintain_queue()

    @property
    def queue_key(self) -> str:
        return f"{self.provider_name}:{self.model}"

    def generate_json(self, request: LLMRequest) -> LLMResponse:
        self._maybe_maintain_queue()
        request_id = self._enqueue(request)
        started = time.monotonic()
        while time.monotonic() - started < self.config.wait_timeout_seconds:
            if self._claim(request_id):
                return self._execute(request_id, request)
            terminal = self._read_terminal(request_id)
            if terminal is not None:
                return terminal
            time.sleep(self.config.poll_seconds)
        raise TimeoutError(f"Timed out waiting for LLM queue request {request_id}")

    def _enqueue(self, request: LLMRequest) -> UUID:
        request_id = uuid4()
        payload = {
            "prompt": request.prompt,
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
            "grounded_search": request.grounded_search,
        }
        with self.session_factory() as db:
            db.execute(
                text(
                    """
                    INSERT INTO llm_request_queue(
                        request_id, queue_key, provider, model, request_payload,
                        estimated_tokens, max_attempts
                    ) VALUES (
                        :request_id, :queue_key, :provider, :model,
                        CAST(:payload AS jsonb), :estimated_tokens, :max_attempts
                    )
                """
                ),
                {
                    "request_id": request_id,
                    "queue_key": self.queue_key,
                    "provider": self.provider_name,
                    "model": self.model,
                    "payload": json.dumps(payload),
                    "estimated_tokens": estimate_tokens(
                        request.prompt, request.max_tokens
                    ),
                    "max_attempts": self.config.max_attempts,
                },
            )
            db.commit()
        return request_id

    def _claim(self, request_id: UUID) -> bool:
        with self.session_factory() as db:
            db.execute(
                text("SELECT sp_release_stale_llm_requests(:timeout)"),
                {"timeout": self.config.stale_seconds},
            )
            claimed = db.execute(
                text(
                    """
                    SELECT public.sp_claim_llm_request(
                        CAST(:request_id AS uuid),
                        CAST(:worker_id AS varchar),
                        CAST(:tpm AS integer),
                        CAST(:rpm AS integer),
                        CAST(:rpd AS integer),
                        CAST(:safety AS numeric),
                        CAST(:daily_reset_timezone AS varchar)
                    )
                """
                ),
                {
                    "request_id": request_id,
                    "worker_id": self.worker_id,
                    "tpm": self.config.tokens_per_minute,
                    "rpm": self.config.requests_per_minute,
                    "rpd": self.config.requests_per_day,
                    "safety": self.config.safety_factor,
                    "daily_reset_timezone": (self.config.daily_reset_timezone),
                },
            ).scalar_one()
            db.commit()
            return bool(claimed)

    def _execute(self, request_id: UUID, request: LLMRequest) -> LLMResponse:
        try:
            response = self.provider.generate_json(request)
        except Exception as exc:
            self._fail(request_id, exc)
            raise
        self._complete(request_id, response)
        return response

    def _complete(self, request_id: UUID, response: LLMResponse) -> None:
        with self.session_factory() as db:
            db.execute(
                text(
                    """
                    UPDATE llm_request_queue
                    SET status='COMPLETED', response_payload=CAST(:response AS jsonb),
                        completed_at=now(), locked_at=NULL, locked_by=NULL
                    WHERE request_id=:request_id AND status='PROCESSING'
                """
                ),
                {"request_id": request_id, "response": json.dumps(response.raw)},
            )
            db.commit()

    def _fail(self, request_id: UUID, exc: Exception) -> None:
        delay = exception_retry_delay(exc)
        with self.session_factory() as db:
            row = (
                db.execute(
                    text(
                        """
                    SELECT attempt_count, max_attempts, queue_key
                    FROM llm_request_queue WHERE request_id=:request_id FOR UPDATE
                """
                    ),
                    {"request_id": request_id},
                )
                .mappings()
                .one()
            )
            retry = int(row["attempt_count"]) < int(row["max_attempts"])
            wait = (
                delay if delay is not None else min(300, 2 ** int(row["attempt_count"]))
            )
            if delay is not None:
                db.execute(
                    text(
                        """
                        INSERT INTO llm_rate_limit_windows(queue_key, blocked_until)
                        VALUES (:queue_key, now() + make_interval(secs => :wait))
                        ON CONFLICT(queue_key) DO UPDATE
                        SET blocked_until=GREATEST(
                            llm_rate_limit_windows.blocked_until,
                            EXCLUDED.blocked_until
                        )
                    """
                    ),
                    {"queue_key": row["queue_key"], "wait": wait},
                )
            db.execute(
                text(
                    """
                    UPDATE llm_request_queue
                    SET status=:status, available_at=CASE WHEN :retry
                        THEN now() + make_interval(secs => :wait) ELSE available_at END,
                        last_error=:error, locked_at=NULL, locked_by=NULL,
                        completed_at=CASE WHEN :retry THEN NULL ELSE now() END
                    WHERE request_id=:request_id
                """
                ),
                {
                    "request_id": request_id,
                    "status": "RETRY" if retry else "FAILED",
                    "retry": retry,
                    "wait": wait,
                    "error": str(exc)[:4000],
                },
            )
            db.commit()

    def _read_terminal(self, request_id: UUID) -> LLMResponse | None:
        with self.session_factory() as db:
            row = (
                db.execute(
                    text(
                        """
                    SELECT status, response_payload, last_error
                    FROM llm_request_queue WHERE request_id=:request_id
                """
                    ),
                    {"request_id": request_id},
                )
                .mappings()
                .one()
            )
        if row["status"] == "FAILED":
            raise RuntimeError(str(row["last_error"] or "LLM queue request failed"))
        return None

    def _maintain_queue(self) -> None:
        with self.session_factory() as db:
            db.execute(
                text(
                    """
                    SELECT public.sp_maintain_llm_queue(
                        CAST(:completed_days AS integer),
                        CAST(:failed_days AS integer),
                        CAST(:abandoned_days AS integer)
                    )
                """
                ),
                {
                    "completed_days": (self.config.completed_retention_days),
                    "failed_days": (self.config.failed_retention_days),
                    "abandoned_days": (self.config.abandoned_retention_days),
                },
            ).scalar_one()

            db.commit()

    def _maybe_maintain_queue(self) -> None:
        now = time.monotonic()

        if now - self._last_maintenance_at < self.config.maintenance_interval_seconds:
            return

        self._maintain_queue()
        self._last_maintenance_at = now
