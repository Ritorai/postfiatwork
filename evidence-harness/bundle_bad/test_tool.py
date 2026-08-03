#!/usr/bin/env python3
"""Under-powered test suite: only four tests, which is the point of this fixture."""
import unittest

from tool import longest_word


class TestLongestWord(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(longest_word(""), "")

    def test_single(self):
        self.assertEqual(longest_word("alpha"), "alpha")

    def test_picks_longest(self):
        self.assertEqual(longest_word("a bb ccc"), "ccc")

    def test_ignores_extra_whitespace(self):
        self.assertEqual(longest_word("  a   bbbb  "), "bbbb")


if __name__ == "__main__":
    unittest.main()
