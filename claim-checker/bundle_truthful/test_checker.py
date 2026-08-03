"""Unit tests for checker.py, used as bundle content for claimcheck fixtures.

Discovered and run by claimcheck itself (python3 -m unittest discover) to
verify the TEST_COUNT_CLAIM in notes_truthful.txt. All three tests pass.
"""
import unittest

import checker


class TestChecker(unittest.TestCase):
    def test_main_returns_zero(self):
        self.assertEqual(checker.main(), 0)

    def test_main_is_callable(self):
        self.assertTrue(callable(checker.main))

    def test_module_has_docstring(self):
        self.assertTrue(checker.__doc__)


if __name__ == "__main__":
    unittest.main()
