import unittest

from llm.persistent_queue import PersistentQueueConfig


class PersistentQueueConfigTests(unittest.TestCase):
    def test_defaults_are_positive(self):
        config = PersistentQueueConfig()
        self.assertGreater(config.poll_seconds, 0)
        self.assertGreater(config.stale_seconds, 0)
        self.assertGreater(config.max_attempts, 0)


if __name__ == "__main__":
    unittest.main()
