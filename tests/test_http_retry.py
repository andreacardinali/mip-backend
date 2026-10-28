import logging
import traceback
import unittest

import pytest

from http_client import testing as httpx
from http_client.retry import (
    RetryDelayStrategy,
    RetryPolicy,
    request_with_retry,
)


def test_shared_http_redacts_explicit_secret_in_errors_and_logs(caplog):
    token = "123456:test-secret"
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(503, text=f"unavailable for {token}")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with caplog.at_level(logging.INFO):
            with pytest.raises(httpx.HTTPStatusError) as captured:
                request_with_retry(
                    client,
                    "POST",
                    f"https://api.telegram.org/bot{token}/sendMessage",
                    policy=RetryPolicy(max_retries=3),
                    sensitive_values=(token,),
                    json={"text": "test"},
                )
    assert len(requests) == 1
    assert token not in str(captured.value)
    assert token not in caplog.text
    assert captured.value.response.status_code == 503


class SequenceTransport(httpx.BaseTransport):
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def handle_request(self, request):
        item = self.responses[min(self.calls, len(self.responses) - 1)]
        self.calls += 1
        if isinstance(item, int):
            return httpx.Response(item, request=request)
        status, headers, body = item
        return httpx.Response(status, request=request, headers=headers, json=body)


class RetryTests(unittest.TestCase):
    def test_sensitive_requests_keep_status_and_hide_tokens(self):
        for status in [401, 403, 404, 429, 500]:
            with self.subTest(status=status):

                def handler(request, status=status):
                    self.assertEqual(request.url.params["token"], "query-secret")
                    self.assertEqual(request.headers["X-API-Key"], "header-secret")
                    return httpx.Response(
                        status,
                        json={"message": "query-secret header-secret bearer-secret"},
                    )

                with (
                    httpx.Client(transport=httpx.MockTransport(handler)) as client,
                    self.assertLogs(level=logging.INFO) as captured,
                    self.assertRaises(httpx.HTTPStatusError) as error,
                ):
                    request_with_retry(
                        client,
                        "GET",
                        "https://example.test?token=query-secret",
                        headers={
                            "X-API-Key": "header-secret",
                            "Authorization": "Bearer bearer-secret",
                        },
                        policy=RetryPolicy(),
                    )
                self.assertEqual(error.exception.response.status_code, status)
                diagnostics = "\n".join(captured.output) + "".join(
                    traceback.format_exception(error.exception)
                )
                for secret in ["query-secret", "header-secret", "bearer-secret"]:
                    self.assertNotIn(secret, diagnostics)

    def test_client_default_credentials_are_redacted(self):
        def handler(request):
            return httpx.Response(
                500, json={"message": "default-secret default-header form-secret"}
            )

        with (
            httpx.Client(
                transport=httpx.MockTransport(handler),
                params={"token": "default-secret"},
                headers={"X-API-Key": "default-header"},
            ) as client,
            self.assertLogs(level=logging.INFO) as captured,
            self.assertRaises(httpx.HTTPStatusError) as error,
        ):
            request_with_retry(
                client,
                "POST",
                "https://example.test",
                json={"credentials": {"api_key": "form-secret"}},
                policy=RetryPolicy(),
            )
        output = "\n".join(captured.output) + str(error.exception)
        for secret in ["default-secret", "default-header", "form-secret"]:
            self.assertNotIn(secret, output)

    def test_retry_redacts_encoded_params_and_echoed_credentials(self):
        secret = "sensitive +/value"
        transport = SequenceTransport(
            [(503, {}, {"message": secret, "api_key": "response-secret"}), 200]
        )
        with (
            httpx.Client(transport=transport) as client,
            self.assertLogs(level=logging.INFO) as captured,
        ):
            response = request_with_retry(
                client,
                "GET",
                "https://example.test",
                params={"api_key": secret},
                policy=RetryPolicy(max_retries=1, jitter_seconds=0),
                sleep=lambda _: None,
            )
        self.assertEqual(response.status_code, 200)
        output = "\n".join(captured.output)
        self.assertNotIn(secret, output)
        self.assertNotIn("sensitive+%2B%2Fvalue", output)
        self.assertNotIn("response-secret", output)
        self.assertIn("[REDACTED]", output)

    def test_network_failures_preserve_type_without_leaking_tokens(self):
        attempts = 0

        def handler(request):
            nonlocal attempts
            attempts += 1
            raise httpx.ReadTimeout(f"timeout at {request.url}", request=request)

        with (
            httpx.Client(transport=httpx.MockTransport(handler)) as client,
            self.assertLogs(level=logging.WARNING) as captured,
            self.assertRaises(httpx.ReadTimeout) as error,
        ):
            request_with_retry(
                client,
                "GET",
                "https://example.test?token=network-secret",
                policy=RetryPolicy(max_retries=1, jitter_seconds=0),
                sleep=lambda _: None,
            )
        self.assertEqual(attempts, 2)
        self.assertNotIn("network-secret", str(error.exception))
        self.assertNotIn("network-secret", "\n".join(captured.output))

    def test_get_retries_then_succeeds(self):
        transport = SequenceTransport([503, 200])
        with httpx.Client(transport=transport) as client:
            response = request_with_retry(
                client,
                "GET",
                "https://example.test",
                policy=RetryPolicy(max_retries=2, jitter_seconds=0),
                sleep=lambda _: None,
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(transport.calls, 2)

    def test_post_is_not_retried_by_default(self):
        transport = SequenceTransport([503, 200])
        with httpx.Client(transport=transport) as client:
            with self.assertRaises(httpx.HTTPStatusError):
                request_with_retry(
                    client,
                    "POST",
                    "https://example.test",
                    policy=RetryPolicy(max_retries=2, jitter_seconds=0),
                    sleep=lambda _: None,
                )
        self.assertEqual(transport.calls, 1)

    def test_post_can_be_explicitly_retried(self):
        transport = SequenceTransport([503, 200])
        with httpx.Client(transport=transport) as client:
            response = request_with_retry(
                client,
                "POST",
                "https://example.test",
                policy=RetryPolicy(max_retries=2, jitter_seconds=0),
                retry_non_idempotent=True,
                sleep=lambda _: None,
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(transport.calls, 2)

    def test_retry_after_header_controls_delay(self):
        sleeps = []
        transport = SequenceTransport([(429, {"Retry-After": "42"}, {}), 200])
        with httpx.Client(transport=transport) as client:
            request_with_retry(
                client,
                "POST",
                "https://example.test",
                policy=RetryPolicy(max_retries=1, jitter_seconds=0),
                retry_non_idempotent=True,
                sleep=sleeps.append,
            )
        self.assertEqual(sleeps, [42.0])

    def test_response_hint_controls_delay(self):
        sleeps = []
        transport = SequenceTransport([(429, {}, {"retry_after": 41.88}), 200])
        with httpx.Client(transport=transport) as client:
            request_with_retry(
                client,
                "POST",
                "https://example.test",
                policy=RetryPolicy(
                    max_retries=1,
                    jitter_seconds=0,
                    delay_strategy=RetryDelayStrategy.HYBRID,
                    response_delay_resolver=lambda response: response.json().get(
                        "retry_after"
                    ),
                ),
                retry_non_idempotent=True,
                sleep=sleeps.append,
            )
        self.assertEqual(sleeps, [41.88])

    def test_retry_log_contains_response_error(self):
        responses = iter(
            [
                httpx.Response(
                    500,
                    json={
                        "error": {
                            "message": "temporary failure",
                        }
                    },
                ),
                httpx.Response(
                    200,
                    json={"ok": True},
                ),
            ]
        )

        transport = httpx.MockTransport(lambda request: next(responses))

        with httpx.Client(transport=transport) as client:
            with self.assertLogs(
                "http_client.retry",
                level=logging.WARNING,
            ) as captured:
                response = request_with_retry(
                    client,
                    "POST",
                    "https://example.invalid",
                    policy=RetryPolicy(
                        max_retries=1,
                        jitter_seconds=0,
                    ),
                    retry_non_idempotent=True,
                    sleep=lambda _: None,
                )

        self.assertEqual(response.status_code, 200)

        log_output = "\n".join(captured.output)

        self.assertIn("status=500", log_output)
        self.assertIn(
            "temporary failure",
            log_output,
        )
        self.assertIn("attempt=1/2", log_output)

    def test_retry_log_contains_exception_message(self):
        attempts = 0

        def handler(
            request: httpx.Request,
        ) -> httpx.Response:
            nonlocal attempts
            attempts += 1

            if attempts == 1:
                raise httpx.ReadTimeout(
                    "provider read timed out",
                    request=request,
                )

            return httpx.Response(
                200,
                json={"ok": True},
            )

        transport = httpx.MockTransport(handler)

        with httpx.Client(transport=transport) as client:
            with self.assertLogs(
                "http_client.retry",
                level=logging.WARNING,
            ) as captured:
                response = request_with_retry(
                    client,
                    "POST",
                    "https://example.invalid",
                    policy=RetryPolicy(
                        max_retries=1,
                        jitter_seconds=0,
                    ),
                    retry_non_idempotent=True,
                    sleep=lambda _: None,
                )

        self.assertEqual(response.status_code, 200)

        log_output = "\n".join(captured.output)

        self.assertIn(
            "error_type=ReadTimeout",
            log_output,
        )
        self.assertIn(
            "provider read timed out",
            log_output,
        )
        self.assertIn(
            "attempt=1/2",
            log_output,
        )


if __name__ == "__main__":
    unittest.main()
