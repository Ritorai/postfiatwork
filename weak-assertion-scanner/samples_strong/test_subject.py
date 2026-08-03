"""Strong (non-weak) sample test suite.

Every test here is intentionally written to look like the tricky-but-valid
patterns real test suites use, specifically so weakassert.py must NOT flag
any of them. Running `weakassert.py --root samples_strong` must produce
zero findings and exit 0.
"""
import unittest

import subject


def assert_positive(case, value):
    """Module-level helper used by a test via a bare call (one-level
    resolution should find the bare `assert` inside this helper)."""
    assert value > 0


class TestArithmetic(unittest.TestCase):
    def setUp(self):
        # An assertion in setUp must never "leak" into sibling test methods.
        self.assertTrue(True)
        self.base = 10

    def _check_add(self, a, b, expected):
        """Same-class helper that does the real assertion; tests call it
        via self._check_add(...) and must be resolved one level deep."""
        self.assertEqual(subject.add(a, b), expected)

    def test_add_direct(self):
        self.assertEqual(subject.add(2, 3), 5)

    def test_add_via_same_class_helper(self):
        self._check_add(2, 3, 5)

    def test_add_via_module_level_helper(self):
        assert_positive(self, subject.add(1, 2))

    def test_divide_raises_as_context_manager(self):
        with self.assertRaises(ValueError):
            subject.divide(1, 0)

    def test_divide_raises_as_plain_call(self):
        self.assertRaises(ValueError, subject.divide, 1, 0)

    def test_loop_only_assertion(self):
        """The only assertion is inside a for loop - must not be WA001."""
        for x in range(3):
            self.assertEqual(subject.add(x, 1), x + 1)

    def test_try_except_assertion(self):
        """The only assertion is inside a try/except - must not be WA001."""
        try:
            result = subject.divide(10, 2)
        except ValueError:
            self.fail("divide raised unexpectedly")
        else:
            self.assertEqual(result, 5.0)

    def test_subtest_assertions(self):
        for a, b, expected in [(1, 1, 2), (2, 2, 4)]:
            with self.subTest(a=a, b=b):
                self.assertEqual(subject.add(a, b), expected)

    @unittest.expectedFailure
    def test_expected_failure_is_not_a_skip(self):
        """expectedFailure is a real decorator but is not a skip marker."""
        self.assertEqual(subject.add(1, 1), 3)

    def test_expected_value_is_a_literal_not_derived(self):
        """assertEqual's expected side is a hand-authored literal, not a
        call into the subject module - must not be WA003."""
        self.assertEqual(subject.add(4, 4), 8)

    def test_expected_from_local_computation(self):
        """expected is computed with plain arithmetic, not by calling the
        subject module - must not be WA003."""
        expected = 4 + 4
        self.assertEqual(subject.add(4, 4), expected)


class TestGreeter(unittest.TestCase):
    def test_greet_message(self):
        g = subject.Greeter("Ada")
        self.assertEqual(g.greet(), "hello, Ada")

    def test_greet_name_stored(self):
        g = subject.Greeter("Grace")
        self.assertEqual(g.name, "Grace")


def test_module_level_function_with_bare_assert():
    """A test_* function that lives outside any class - must be scanned
    and must not be flagged, since it has a direct bare assert."""
    assert subject.add(1, 2) == 3
