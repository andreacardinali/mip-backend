import unittest

import httpx

from http_client.retry import RetryPolicy, request_with_retry


class SequenceTransport(httpx.BaseTransport):
    def __init__(self, statuses):
        self.statuses = list(statuses)
        self.calls = 0

    def handle_request(self, request):
        status = self.statuses[min(self.calls, len(self.statuses) - 1)]
        self.calls += 1
        return httpx.Response(status, request=request, headers={"Retry-After": "0"})


class RetryTests(unittest.TestCase):
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

    def test_max_retries_is_after_initial_attempt(self):
        transport = SequenceTransport([503, 503, 503])
        with httpx.Client(transport=transport) as client:
            with self.assertRaises(httpx.HTTPStatusError):
                request_with_retry(
                    client,
                    "GET",
                    "https://example.test",
                    policy=RetryPolicy(max_retries=2, jitter_seconds=0),
                    sleep=lambda _: None,
                )
        self.assertEqual(transport.calls, 3)


if __name__ == "__main__":
    unittest.main()
