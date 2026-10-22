import json
import logging
import socket
import time
from dataclasses import dataclass
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from llm.base import LLMProvider, LLMRequest, LLMResponse
from llm.rate_limit import estimate_tokens
from llm.retry_hints import exception_retry_delay

logger = logging.getLogger(__name__)


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


@dataclass(frozen=True, slots=True)
class QueueFailureResult:
    status: str
    retry_scheduled: bool
    available_at: object | None
    attempt_count: int
    max_attempts: int


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

    def generate_json(
        self,
        request: LLMRequest,
    ) -> LLMResponse:
        self._maybe_maintain_queue()

        request_id = self._enqueue(request)
        started = time.monotonic()
        deadline = started + self.config.wait_timeout_seconds

        logger.info(
            "LLM queue request enqueued "
            "request_id=%s queue_key=%s "
            "wait_timeout_seconds=%.3f",
            request_id,
            self.queue_key,
            self.config.wait_timeout_seconds,
        )

        while True:
            remaining_seconds = deadline - time.monotonic()

            if remaining_seconds <= 0:
                status = self._timeout_request(
                    request_id,
                )

                raise TimeoutError(
                    "Timed out waiting for LLM queue " f"request {request_id}: {status}"
                )

            if self._claim(request_id):
                logger.info(
                    "LLM queue request claimed "
                    "request_id=%s "
                    "remaining_seconds=%.3f",
                    request_id,
                    remaining_seconds,
                )

                response = self._execute(
                    request_id,
                    request,
                    deadline=deadline,
                )

                if response is not None:
                    return response

            terminal = self._read_terminal(request_id)

            if terminal is not None:
                return terminal

            sleep_seconds = min(
                self.config.poll_seconds,
                max(
                    0.0,
                    deadline - time.monotonic(),
                ),
            )

            if sleep_seconds <= 0:
                continue

            time.sleep(sleep_seconds)

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

    def _execute(
        self,
        request_id: UUID,
        request: LLMRequest,
        deadline: float,
    ) -> LLMResponse | None:
        try:
            response = self.provider.generate_json(request)
        except Exception as exc:
            remaining_seconds = max(
                0.0,
                deadline - time.monotonic(),
            )

            failure = self._fail(
                request_id,
                exc,
                remaining_seconds=(remaining_seconds),
            )

            logger.warning(
                "LLM queue request failed "
                "request_id=%s "
                "status=%s "
                "attempt=%s/%  "
                "retry_scheduled=%s "
                "available_at=%s "
                "remaining_seconds=%.3f "
                "erro*_type=%s error=%s",
                request_id,
                failure.status,
                failure.attempt_count,
                failure.max_attempts,
                failure.retry_scheduled,
                failure.available_at,
                remaining_seconds,
                type(exc).__name__,
                exc,
            )

            if failure.retry_scheduled:
                return None

            raise

        self._complete(
            request_id,
            response,
        )

        logger.info(
            "LLM queue request completed " "request_id=%s",
            request_id,
        )

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

    def _fail(
        self,
        request_id: UUID,
        exc: Exception,
        *,
        remaining_seconds: float,
    ) -> QueueFailureResult:
        suggested_delay = exception_retry_delay(exc)

        with self.session_factory() as db:
            current = (
                db.execute(
                    text(
                        """
                        SELECT
                            attempt_count,
                            max_attempts,
                            queue_key
                        FROM llm_request_queue
                        WHERE request_id = :request_id
                        FOR UPDATE
                        """
                    ),
                    {
                        "request_id": request_id,
                    },
                )
                .mappings()
                .one()
            )

            attempt_count = int(current["attempt_count"])
            max_attempts = int(current["max_attempts"])

            default_delay = min(
                300.0,
                float(2**attempt_count),
            )

            requested_wait = max(
                0.0,
                float(
                    suggested_delay if suggested_delay is not None else default_delay
                ),
            )

            required_remaining_seconds = requested_wait + self.config.poll_seconds

            retry = (
                attempt_count < max_attempts
                and remaining_seconds > required_remaining_seconds
            )

            result = (
                db.execute(
                    text(
                        """
                        UPDATE llm_request_queue
                        SET
                            status = CASE
                                WHEN :retry
                                THEN 'RETRY'
                                ELSE 'FAILED'
                            END,
                            available_at = CASE
                                WHEN :retry
                                THEN now()
                                     + make_interval(
                                         secs => :wait
                                     )
                                ELSE available_at
                            END,
                            last_error = :error,
                            locked_at = NULL,
                            locked_by = NULL,
                            lease_expires_at = NULL,
                            completed_at = CASE
                                WHEN :retry
                                THEN NULL
                                ELSE now()
                            END
                        WHERE request_id = :request_id
                        RETURNING
                            status,
                            available_at,
                            attempt_count,
                            max_attempts
                        """
                    ),
                    {
                        "request_id": request_id,
                        "retry": retry,
                        "wait": requested_wait,
                        "error": str(exc)[:4000],
                    },
                )
                .mappings()
                .one()
            )

            if retry and suggested_delay is not None:
                db.execute(
                    text(
                        """
                        INSERT INTO
                            llm_rate_limit_windows(
                                queue_key,
                                blocked_until
                            )
                        VALUES (
                            :queue_key,
                            now()
                            + make_interval(
                                secs => :wait
                            )
                        )
                        ON CONFLICT(queue_key)
                        DO UPDATE
                        SET blocked_until = GREATEST(
                            llm_rate_limit_windows
                                .blocked_until,
                            EXCLUDED.blocked_until
                        )
                        """
                    ),
                    {
                        "queue_key": current["queue_key"],
                        "wait": requested_wait,
                    },
                )

            db.commit()

        return QueueFailureResult(
            status=str(result["status"]),
            retry_scheduled=retry,
            available_at=result["available_at"],
            attempt_count=int(result["attempt_count"]),
            max_attempts=int(result["max_attempts"]),
        )

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

    def _timeout_request(
        self,
        request_id: UUID,
    ) -> dict:
        timeout_error = (
            "Queue wait timeout exceeded after "
            f"{self.config.wait_timeout_seconds:.3f} "
            "seconds"
        )

        with self.session_factory() as db:
            row = (
                db.execute(
                    text(
                        """
                        UPDATE llm_request_queue
                        SET
                            status = 'FAILED',
                            last_error = CASE
                                WHEN last_error IS NULL
                                  OR last_error = ''
                                THEN :timeout_error
                                ELSE
                                    last_error
                                    || E'\n'
                                    || :timeout_error
                            END,
                            locked_at = NULL,
                            locked_by = NULL,
                            lease_expires_at = NULL,
                            completed_at = now()
                        WHERE request_id = :request_id
                          AND status IN (
                              'PENDING',
                              'RETRY'
                          )
                        RETURNING
                            status,
                            estimated_tokens,
                            attempt_count,
                            max_attempts,
                            created_at,
                            available_at,
                            locked_at,
                            lease_expires_at,
                            last_error
                        """
                    ),
                    {
                        "request_id": request_id,
                        "timeout_error": timeout_error,
                    },
                )
                .mappings()
                .one_or_none()
            )

            if row is None:
                row = (
                    db.execute(
                        text(
                            """
                            SELECT
                                status,
                                estimated_tokens,
                                attempt_count,
                                max_attempts,
                                created_at,
                                available_at,
                                locked_at,
                                lease_expires_at,
                                last_error
                            FROM llm_request_queue
                            WHERE request_id = :request_id
                            """
                        ),
                        {
                            "request_id": request_id,
                        },
                    )
                    .mappings()
                    .one_or_none()
                )

            db.commit()

        return dict(row or {})

    def _read_status(
        self,
        request_id: UUID,
    ) -> dict:
        with self.session_factory() as db:
            row = (
                db.execute(
                    text(
                        """
                    SELECT
                        status,
                        estimated_tokens,
                        attempt_count,
                        max_attempts,
                        created_at,
                        available_at,
                        locked_at,
                        lease_expires_at,
                        last_error
                    FROM llm_request_queue
                    WHERE request_id = :request_id
                    """
                    ),
                    {
                        "request_id": request_id,
                    },
                )
                .mappings()
                .one_or_none()
            )

        return dict(row or {})
