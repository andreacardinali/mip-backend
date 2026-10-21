import unittest

import httpx

from llm.rate_limit import LLMRateLimiter, estimate_tokens
from llm.retry_hints import llm_response_delay


class RateLimiterTests(unittest.TestCase):
    def test_estimate_tokens_includes_reserved_output(self):
        self.assertEqual(estimate_tokens("12345678", 10), 12)

    def test_rejects_invalid_safety_factor(self):
        with self.assertRaises(ValueError):
            LLMRateLimiter(safety_factor=0)

    def test_groq_message_delay_is_parsed(self):
        response = httpx.Response(
            429,
            request=httpx.Request("POST", "https://example.test"),
            json={
                "error": {
                    "message": "Please try again in 41.879999999s.",
                }
            },
        )
        self.assertAlmostEqual(llm_response_delay(response), 41.879999999)


if __name__ == "__main__":
    unittest.main()
