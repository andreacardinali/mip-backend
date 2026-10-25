import unittest

import httpx

from http_client.retry import RetryPolicy, request_with_retry


class SequenceTransport(httpx.BaseTransport):
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def handle_request(self, request):
        status = self.responses[min(self.calls, len(self.responses) - 1)]
        self.calls += 1
        return httpx.Response(status, request=request)


class NeverRetryForbiddenTests(unittest.TestCase):
    def test_403_is_never_retried_with_default_policy(self):
        transport = SequenceTransport([403, 200])
        with httpx.Client(transport=transport) as client:
            with self.assertRaises(httpx.HTTPStatusError):
                request_with_retry(
                    client,
                    "GET",
                    "https://example.test",
                    policy=RetryPolicy(max_retries=3, jitter_seconds=0),
                    sleep=lambda _: None,
                )
        self.assertEqual(transport.calls, 1)

    def test_403_is_never_retried_even_if_consumer_marks_it_retryable(self):
        transport = SequenceTransport([403, 200])
        with httpx.Client(transport=transport) as client:
            with self.assertRaises(httpx.HTTPStatusError):
                request_with_retry(
                    client,
                    "GET",
                    "https://example.test",
                    policy=RetryPolicy(
                        max_retries=3,
                        jitter_seconds=0,
                        retryable_status_codes=frozenset({403, 503}),
                    ),
                    sleep=lambda _: None,
                )
        self.assertEqual(transport.calls, 1)


if __name__ == "__main__":
    unittest.main()
