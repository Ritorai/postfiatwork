"""Unit tests for checker.py, used as bundle content for claimcheck fixtures.

Discovered and run by claimcheck itself (python3 -m unittest discover) to
verify TEST_COUNT_CLAIM entries in notes_false.txt. There are exactly two
tests here -- notes_false.txt deliberately claims a different number.
"""
import unittest

import checker


class TestChecker(unittest.TestCase):
    def test_main_returns_zero(self):
        self.assertEqual(checker.main(), 0)

    def test_main_is_callable(self):
        self.assertTrue(callable(checker.main))


if __name__ == "__main__":
    unittest.main()
