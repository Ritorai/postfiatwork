#!/usr/bin/env python3
"""Tests for the toy artefact shipped in the good evidence bundle."""
import unittest

from sum_lines import sum_lines


class TestSumLines(unittest.TestCase):
    def test_empty_iterable(self):
        self.assertEqual(sum_lines([]), 0)

    def test_single_value(self):
        self.assertEqual(sum_lines(["4"]), 4)

    def test_two_values(self):
        self.assertEqual(sum_lines(["4", "5"]), 9)

    def test_blank_lines_ignored(self):
        self.assertEqual(sum_lines(["1", "", "2"]), 3)

    def test_only_blank_lines(self):
        self.assertEqual(sum_lines(["", "   ", "\n"]), 0)

    def test_trailing_newlines_stripped(self):
        self.assertEqual(sum_lines(["7\n", "8\n"]), 15)

    def test_leading_whitespace_stripped(self):
        self.assertEqual(sum_lines(["   6"]), 6)

    def test_negative_values(self):
        self.assertEqual(sum_lines(["-3", "5"]), 2)

    def test_zero(self):
        self.assertEqual(sum_lines(["0", "0"]), 0)

    def test_large_values(self):
        self.assertEqual(sum_lines(["1000000", "2000000"]), 3000000)

    def test_non_numeric_raises(self):
        with self.assertRaises(ValueError):
            sum_lines(["nope"])

    def test_generator_input(self):
        self.assertEqual(sum_lines(str(n) for n in range(5)), 10)


if __name__ == "__main__":
    unittest.main()
