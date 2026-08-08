#!/usr/bin/env python3
"""Fixture test module for the DOC009 valid control.

Exists so that `python3 -m unittest test_entry` in README.md names a real
module. Not part of doc-validator's own suite.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import entry  # noqa: E402


class TestEntry(unittest.TestCase):
    def test_check_flag_exits_zero(self):
        self.assertEqual(entry.main(["--check"]), 0)


if __name__ == "__main__":
    unittest.main()
