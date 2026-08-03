"""Weak sample test suite.

Every test class here demonstrates one or more of the four weakassert
categories on purpose. Running `weakassert.py --root samples_weak` must
report findings in all four categories and exit 1.
"""
import unittest

import subject


class TestNoAssertion(unittest.TestCase):
    def test_empty_body(self):
        pass

    def test_only_a_docstring(self):
        """This test documents intent but checks nothing."""

    def test_only_local_assignment(self):
        x = 1 + 1
        y = x * 2


class TestCallOnly(unittest.TestCase):
    def test_call_only_expression_statement(self):
        subject.add(2, 3)

    def test_call_only_captured_in_variable(self):
        result = subject.compute(4)

    def test_call_only_widget_construction(self):
        w = subject.Widget(5)
        w.scale(2)


class TestSelfDerivedExpectation(unittest.TestCase):
    def test_tautological_add(self):
        self.assertEqual(subject.add(2, 3), subject.add(2, 3))

    def test_tautological_compute_different_call_sites(self):
        actual = subject.compute(7)
        self.assertEqual(actual, subject.compute(7))

    def test_widget_scale_self_derived(self):
        w = subject.Widget(5)
        self.assertEqual(w.scale(3), subject.Widget(5).scale(3))


class TestSkipped(unittest.TestCase):
    @unittest.skip("temporarily disabled while investigating a flake")
    def test_decorator_skip(self):
        self.assertEqual(subject.add(1, 1), 2)

    @unittest.skipIf(True, "always skipped in this sample")
    def test_decorator_skip_if(self):
        self.assertEqual(subject.add(1, 1), 2)

    @unittest.skipUnless(False, "never runs in this sample")
    def test_decorator_skip_unless(self):
        self.assertEqual(subject.add(1, 1), 2)

    def test_body_only_skip_test(self):
        self.skipTest("not implemented yet")


@unittest.skip("whole class disabled for this sample")
class TestWholeClassSkipped(unittest.TestCase):
    def test_member_a(self):
        self.assertEqual(subject.add(1, 1), 2)

    def test_member_b(self):
        self.assertEqual(subject.add(2, 2), 4)
