#!/usr/bin/env python3
"""Tests for stdout_contract_mutations.py.

The harness produces every number in README.md's "The stdout contract"
section, so it needs the same scrutiny as anything else that generates a
committed figure. These tests exercise it on throwaway copies; none of
them touches this directory.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import stdout_contract_mutations as M  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
HARNESS = os.path.join(HERE, "stdout_contract_mutations.py")
PY = sys.executable or "python3"


def run_harness(*args, cwd=None):
    """Run the harness that LIVES IN `cwd`, not this directory's copy.

    The harness locates its subject from its own __file__, so invoking
    this directory's script with cwd set elsewhere would measure this
    directory and quietly ignore the copy the test just prepared.
    """
    where = cwd or HERE
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    return subprocess.run(
        [PY, os.path.join(where, "stdout_contract_mutations.py")] + list(args),
        cwd=where, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env=env, timeout=1800)


class TestTheMutationsAreRealEdits(unittest.TestCase):
    """Every replacement must differ from the anchor it replaces."""

    def test_the_anchor_appears_exactly_once_in_the_subject(self):
        with open(os.path.join(HERE, "coverage_audit.py"),
                  encoding="utf-8") as fh:
            source = fh.read()
        self.assertEqual(source.count(M.ANCHOR), 1)

    def test_every_replacement_differs_from_the_anchor(self):
        for name, replacement, _why in M.MUTATIONS:
            self.assertNotEqual(replacement, M.ANCHOR,
                                "%s replaces the anchor with itself" % name)

    def test_every_mutation_has_a_reason_a_reader_can_use(self):
        for name, _replacement, why in M.MUTATIONS:
            self.assertTrue(why.strip(), "%s has no stated reason" % name)

    def test_mutation_names_are_unique(self):
        names = [n for n, _, _ in M.MUTATIONS]
        self.assertEqual(sorted(names), sorted(set(names)))


class TestSetupErrorsAreNotResults(unittest.TestCase):
    """The failure modes that would fabricate a number."""

    def test_a_target_that_does_not_exist_exits_two(self):
        """The defect this guard exists for.

        unittest does not report zero tests for an unresolvable name -- it
        synthesises a _FailedTest and reports "Ran 1 test ... FAILED".
        Since this harness reads "nonzero exit" as "the mutation was
        caught", an unguarded run would credit a nonexistent test with
        catching all five and print "caught 5 of 5".
        """
        proc = run_harness("--target",
                           "test_coverage_audit.TestCLI.test_no_such_test")
        self.assertEqual(proc.returncode, 2)
        message = proc.stderr.decode("utf-8", "replace")
        self.assertIn("did not resolve to a real test", message)
        self.assertNotIn(b"caught 5 of 5", proc.stdout)

    def test_a_module_that_does_not_exist_exits_two(self):
        proc = run_harness("--target", "test_no_such_module.TestCLI")
        self.assertEqual(proc.returncode, 2)
        self.assertNotIn(b"caught", proc.stdout)

    def test_an_unknown_revision_exits_two(self):
        proc = run_harness("--rev", "no-such-rev-zzz")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("git archive",
                      proc.stderr.decode("utf-8", "replace"))

    def test_a_red_control_exits_two_and_attributes_nothing(self):
        """If the suite is already failing, no result is attributable.

        Built by copying this directory and breaking coverage_audit.py
        before the harness ever runs, so the unmutated control is red.
        """
        parent = tempfile.mkdtemp(prefix="scm_test_")     # created here
        self.addCleanup(shutil.rmtree, parent)
        dest = os.path.join(parent, "regression-checker")
        shutil.copytree(HERE, dest,
                        ignore=shutil.ignore_patterns("__pycache__"))
        path = os.path.join(dest, "coverage_audit.py")
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text.replace(M.ANCHOR,
                                  "            sys.stdout.write('BROKEN\\n')\n"
                                  "        return exit_code"))
        proc = run_harness(cwd=dest)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("control run FAILED",
                      proc.stderr.decode("utf-8", "replace"))
        self.assertNotIn(b"caught", proc.stdout)

    def test_an_anchor_that_no_longer_matches_exits_two(self):
        """A refactor that moves the anchor must stop the harness.

        The edit below is behaviour-PRESERVING -- it only adds a trailing
        comment -- so the unmutated control still passes and execution
        reaches the anchor check rather than being stopped by the
        red-control guard first. A harness whose anchor silently stopped
        matching would apply no mutation at all and report that every
        mutation was caught.
        """
        parent = tempfile.mkdtemp(prefix="scm_test_")     # created here
        self.addCleanup(shutil.rmtree, parent)
        dest = os.path.join(parent, "regression-checker")
        shutil.copytree(HERE, dest,
                        ignore=shutil.ignore_patterns("__pycache__"))
        path = os.path.join(dest, "coverage_audit.py")
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        moved = ("            sys.stdout.write(text)  # emit the report\n"
                 "        return exit_code")
        self.assertNotIn(M.ANCHOR, moved)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text.replace(M.ANCHOR, moved))
        proc = run_harness(cwd=dest)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("anchor matched 0 times",
                      proc.stderr.decode("utf-8", "replace"))
        self.assertNotIn(b"caught", proc.stdout)


class TestItLeavesTheTreeAlone(unittest.TestCase):

    def test_list_does_not_touch_coverage_audit(self):
        path = os.path.join(HERE, "coverage_audit.py")
        with open(path, "rb") as fh:
            before = fh.read()
        proc = run_harness("--list")
        self.assertEqual(proc.returncode, 0)
        with open(path, "rb") as fh:
            self.assertEqual(fh.read(), before,
                             "the harness mutated the real source")

    def test_list_names_every_mutation(self):
        proc = run_harness("--list")
        out = proc.stdout.decode("utf-8")
        for name, _, _ in M.MUTATIONS:
            self.assertIn(name, out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
