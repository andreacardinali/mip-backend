import unittest

from llm.persistent_queue import PersistentQueueConfig


class RPDConfigTests(unittest.TestCase):
    def test_gemini_daily_defaults(self):
        config = PersistentQueueConfig(requests_per_day=1500)
        self.assertEqual(config.requests_per_day, 1500)
        self.assertEqual(config.daily_reset_timezone, "America/Los_Angeles")


if __name__ == "__main__":
    unittest.main()
