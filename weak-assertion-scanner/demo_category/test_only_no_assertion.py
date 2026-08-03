"""Small fixture used only to demonstrate that --category changes the
verdict: this file trips WA001 only, none of the other three categories."""
import unittest


class TestDemo(unittest.TestCase):
    def test_nothing_is_checked(self):
        x = 1
        y = 2
