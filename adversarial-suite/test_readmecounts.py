#!/usr/bin/env python3
"""Test suite for readmecounts.py. Standard library only.

Run with:
    python3 -m unittest test_readmecounts -v

Two controls carry most of the weight here, because a checker like this
fails in two directions and each direction needs its own proof:

  * BOTH SIDES MUST BE LIVE. If the expected value were hardcoded in
    readmecounts.py, the check would pass forever regardless of the README;
    if the measured value were hardcoded, it would pass forever regardless
    of the tree. TestNothingIsHardcoded mutates each side independently in
    a throwaway copy and requires the report to move with it.

  * A STALE CLAIM MUST FAIL. TestStaleClaimFails takes the real README,
    changes one number, and requires a mismatch and exit 1 -- on the real
    repository, not on a toy fixture.

Every temporary directory this file creates is removed by the exact path it
was created at; nothing removes a path it did not create.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import readmecounts as rc  # noqa: E402

PY = sys.executable or "python3"
SCRIPT = os.path.join(HERE, "readmecounts.py")
README = os.path.join(HERE, "README.md")

#: A marker string that must not occur anywhere else in this repository.
#: Used by the negative control below, which asserts exactly that.
MARKER = "qzr-readmecounts-marker-4b81"


def read(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def read_bytes(path):
    with open(path, "rb") as fh:
        return fh.read()


def write(path, text):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)


class Sandbox(object):
    """A copy of this directory's checkable files, at a fresh path."""

    FILES = ("README.md", "test_adversarial.py", "make_fixtures.py",
             "expected_results.json", "test_readmecounts.py")

    def __init__(self, prefix="readmecounts_test_"):
        self.parent = tempfile.mkdtemp(prefix=prefix)
        self.root = os.path.join(self.parent, "adversarial-suite")
        os.makedirs(self.root)
        for name in self.FILES:
            shutil.copy2(os.path.join(HERE, name),
                         os.path.join(self.root, name))

    @property
    def readme(self):
        return os.path.join(self.root, "README.md")

    def edit(self, name, old, new, count=1):
        path = os.path.join(self.root, name)
        text = read(path)
        if old not in text:
            raise AssertionError("sandbox edit target not present: %r" % old)
        write(path, text.replace(old, new, count))

    def report(self, **kw):
        return rc.build_report(self.readme, self.root, **kw)

    def close(self):
        shutil.rmtree(self.parent)     # created by this object, by full path


def state_of(report, claim):
    for c in report["claims"]:
        if c["claim"] == claim:
            return c
    raise AssertionError("no claim named %r in report" % claim)


# ==========================================================================
# The real repository passes
# ==========================================================================

class TestRealRepositoryIsConsistent(unittest.TestCase):

    def setUp(self):
        self.report = rc.build_report(README, HERE)

    def test_no_claim_fails(self):
        self.assertEqual(self.report["failing"], [])

    def test_every_cheap_claim_is_found_and_matches(self):
        for c in self.report["claims"]:
            if c["claim"] in rc.EXPENSIVE:
                continue
            self.assertEqual(c["state"], rc.MATCH,
                             "%s: %s" % (c["claim"], c))

    def test_every_claim_has_a_known_state(self):
        known = {rc.MATCH, rc.MISMATCH, rc.DISAGREEMENT, rc.NOT_FOUND,
                 rc.UNLOCATED, rc.SKIPPED}
        for c in self.report["claims"]:
            self.assertIn(c["state"], known)

    def test_every_locator_name_has_a_measurer(self):
        self.assertEqual(sorted(rc.LOCATORS), sorted(rc.MEASURERS))

    def test_the_expensive_claim_is_skipped_by_default(self):
        self.assertEqual(state_of(self.report, "results_digest")["state"],
                         rc.SKIPPED)


# ==========================================================================
# Measurements, checked against the tree by an independent route
# ==========================================================================

class TestMeasurements(unittest.TestCase):

    def test_test_count_matches_an_independent_count(self):
        text = read(os.path.join(HERE, "test_adversarial.py"))
        independent = len(re.findall(r"^\s*def (test_\w+)\s*\(", text, re.M))
        self.assertEqual(rc.measure_test_count(HERE), independent)

    def test_grep_count_is_lines_not_matches(self):
        d = tempfile.mkdtemp(prefix="readmecounts_grep_")
        try:
            write(os.path.join(d, "test_adversarial.py"),
                  "def test_a(): pass  # def test_b\n"
                  "def test_c(): pass\n")
            self.assertEqual(rc.measure_grep_test_count(d), 2)
        finally:
            shutil.rmtree(d)

    def test_ast_count_ignores_a_def_inside_a_string(self):
        d = tempfile.mkdtemp(prefix="readmecounts_ast_")
        try:
            write(os.path.join(d, "test_adversarial.py"),
                  'SAMPLE = "def test_not_real(self): pass"\n'
                  "def test_real(self):\n    pass\n")
            self.assertEqual(rc.measure_test_count(d), 1)
            self.assertEqual(rc.measure_grep_test_count(d), 2)
        finally:
            shutil.rmtree(d)

    def test_fixture_counts_do_not_import_the_generator(self):
        """A module body with a side effect must not run."""
        d = tempfile.mkdtemp(prefix="readmecounts_noimport_")
        try:
            canary = os.path.join(d, "canary.txt")
            write(os.path.join(d, "make_fixtures.py"),
                  "open(%r, 'w').write('imported')\n"
                  "FIXTURES_B64 = {'a': 'AA==', 'b': 'AA=='}\n"
                  "EMPTY_DIRS = ['x']\n" % canary)
            self.assertEqual(rc.measure_fixture_file_count(d), 2)
            self.assertEqual(rc.measure_empty_dir_count(d), 1)
            self.assertFalse(os.path.exists(canary),
                             "make_fixtures.py was executed")
        finally:
            shutil.rmtree(d)

    def test_case_count_reads_the_cases_object(self):
        d = tempfile.mkdtemp(prefix="readmecounts_cases_")
        try:
            write(os.path.join(d, "expected_results.json"),
                  json.dumps({"cases": {"a": 1, "b": 2, "c": 3}}))
            self.assertEqual(rc.measure_case_count(d), 3)
        finally:
            shutil.rmtree(d)

    def test_own_test_count_measures_this_very_file(self):
        """Dog-fooding: the checker counts its own suite the same way."""
        text = read(os.path.join(HERE, "test_readmecounts.py"))
        independent = len(re.findall(r"^\s*def (test_\w+)\s*\(", text, re.M))
        self.assertEqual(rc.measure_own_test_count(HERE), independent)

    def _stub_suite(self, d, cases, digest="a" * 64, gen_exit=0):
        """A tiny stand-in for the real 10s suite.

        Lets the fixtures/ lifecycle and the cases= cross-check be tested
        in milliseconds. Without this the whole of measure_results_digest
        -- including the part that used to destroy a pre-existing
        fixtures/ -- had no coverage at all.
        """
        write(os.path.join(d, "make_fixtures.py"),
              "import os, sys\n"
              "os.makedirs('fixtures', exist_ok=True)\n"
              "open(os.path.join('fixtures', 'generated.txt'), 'w').write('x')\n"
              "sys.exit(%d)\n" % gen_exit)
        write(os.path.join(d, "test_adversarial.py"),
              "import sys\n"
              "sys.stderr.write('RESULTS_DIGEST sha256=%s cases=%d\\n')\n"
              % (digest, cases))

    def test_with_digest_removes_a_fixtures_dir_it_created(self):
        d = tempfile.mkdtemp(prefix="readmecounts_fx_")
        try:
            self._stub_suite(d, cases=3)
            self.assertEqual(rc.measure_results_digest(d, case_count=3),
                             "a" * 64)
            self.assertFalse(os.path.exists(os.path.join(d, "fixtures")))
        finally:
            shutil.rmtree(d)

    def test_with_digest_preserves_a_fixtures_dir_it_did_not_create(self):
        """The bug this test exists for destroyed real data.

        measure_results_digest guarded its own rmtree and then shelled
        out to make_fixtures.py, whose generate() removes the destination
        itself. Guarding only the lines you wrote is not the same as not
        destroying anything.
        """
        d = tempfile.mkdtemp(prefix="readmecounts_fx2_")
        try:
            self._stub_suite(d, cases=3)
            fixtures = os.path.join(d, "fixtures")
            write(os.path.join(fixtures, "CANARY.txt"), "do not delete")
            os.makedirs(os.path.join(fixtures, "sub"))
            rc.measure_results_digest(d, case_count=3)
            self.assertTrue(os.path.isfile(os.path.join(fixtures,
                                                        "CANARY.txt")))
            self.assertEqual(read(os.path.join(fixtures, "CANARY.txt")),
                             "do not delete")
            self.assertTrue(os.path.isdir(os.path.join(fixtures, "sub")))
            self.assertFalse(os.path.exists(os.path.join(fixtures,
                                                         "generated.txt")))
        finally:
            shutil.rmtree(d)

    def test_a_zero_case_digest_is_a_setup_error_not_a_match(self):
        """The vacuous pass: sha256 of an empty result set is still a hash."""
        d = tempfile.mkdtemp(prefix="readmecounts_fx3_")
        try:
            self._stub_suite(d, cases=0)
            with self.assertRaises(rc.SetupError):
                rc.measure_results_digest(d)
        finally:
            shutil.rmtree(d)

    def test_a_case_count_mismatch_is_a_setup_error(self):
        d = tempfile.mkdtemp(prefix="readmecounts_fx4_")
        try:
            self._stub_suite(d, cases=5)
            with self.assertRaises(rc.SetupError):
                rc.measure_results_digest(d, case_count=6)
        finally:
            shutil.rmtree(d)

    def test_a_failing_generator_is_a_setup_error(self):
        d = tempfile.mkdtemp(prefix="readmecounts_fx5_")
        try:
            self._stub_suite(d, cases=3, gen_exit=3)
            with self.assertRaises(rc.SetupError):
                rc.measure_results_digest(d, case_count=3)
            self.assertFalse(os.path.exists(os.path.join(d, "fixtures")))
        finally:
            shutil.rmtree(d)

    def test_a_value_at_the_end_of_a_sentence_is_swept(self):
        r"""The one-character bypass the second review found.

        `(?![\w.])` refuses to match when the next character is a full
        stop, so a stale number written at the end of an English sentence
        was invisible both to the sweep and to the control that scans this
        checker for hardcoded values.
        """
        sb = Sandbox()
        try:
            measured = state_of(sb.report(), "case_count")["measured"]
            write(sb.readme, read(sb.readme) +
                  "\n\nThe number of cases recorded is %s.\n" % measured)
            c = state_of(sb.report(), "case_count")
            self.assertEqual(c["state"], rc.UNLOCATED)
        finally:
            sb.close()

    def test_a_decimal_is_not_a_standalone_occurrence(self):
        """The control for the test above: not simply looser."""
        sb = Sandbox()
        try:
            measured = state_of(sb.report(), "case_count")["measured"]
            write(sb.readme, read(sb.readme) +
                  "\n\nThe cases run took %s.4 seconds and %sms.\n"
                  % (measured, measured))
            c = state_of(sb.report(), "case_count")
            self.assertEqual(c["state"], rc.MATCH, c["unlocated_occurrences"])
        finally:
            sb.close()

    def test_missing_input_is_a_setup_error(self):
        d = tempfile.mkdtemp(prefix="readmecounts_missing_")
        try:
            for fn in (rc.measure_test_count, rc.measure_case_count,
                       rc.measure_fixture_file_count):
                with self.assertRaises(rc.SetupError):
                    fn(d)
        finally:
            shutil.rmtree(d)


# ==========================================================================
# THE CONTROL: neither side of the comparison is hardcoded
# ==========================================================================

class TestNothingIsHardcoded(unittest.TestCase):

    def test_no_claimed_value_appears_in_the_checker_source(self):
        r"""The checker must not contain the numbers it checks.

        Every value is checked, including the short ones. An earlier
        version of this test skipped values shorter than three characters
        to avoid matching line noise, which quietly exempted a third of
        the claims -- `own_test_count` and `empty_dir_count` could have
        been hardcoded and this test would still have passed. Instead of
        a length cut-off, the search is for the value as a STANDALONE
        NUMBER -- which does not match `3` inside `2000` or `[3:]`, and
        which DOES match a number at the end of a sentence. An earlier
        version used `(?![\w.])`, so a value written `122.` was invisible
        to this control and to the sweep it shares a pattern with; one
        full stop was the whole bypass.

        The whole file is searched, docstring included: a number in a
        docstring is exactly the kind of unchecked prose claim this tool
        exists to catch, and exempting the docstring would reintroduce it
        one line below the code.
        """
        src = read(SCRIPT)
        report = rc.build_report(README, HERE)
        checked = 0
        for c in report["claims"]:
            if c["state"] != rc.MATCH:
                continue
            value = c["measured"]
            if not value.isdigit():
                continue
            checked += 1
            pat = re.compile(rc._standalone_number_pattern(value))
            hits = [src[:m.start()].count("\n") + 1
                    for m in pat.finditer(src)]
            self.assertEqual(
                hits, [],
                "readmecounts.py names the value of %s (%s) at line(s) %s"
                % (c["claim"], value, hits))
        self.assertGreaterEqual(checked, 4,
                                "this control checked almost nothing")

    def test_moving_the_readme_number_moves_the_expectation(self):
        """No literal number here: the edit is derived from the locators."""
        sb = Sandbox()
        try:
            before = state_of(sb.report(), "case_count")
            self.assertEqual(before["state"], rc.MATCH)
            text, old_value, new_value = rc.bump_claim(read(sb.readme),
                                                       "case_count")
            write(sb.readme, text)
            after = state_of(sb.report(), "case_count")
            self.assertIn(new_value, after["claimed_distinct"])
            self.assertNotEqual(after["state"], rc.MATCH)
            self.assertNotEqual(old_value, new_value)
        finally:
            sb.close()

    def test_moving_the_tree_moves_the_measurement(self):
        """Add a real test method; the measured count must go up by one."""
        sb = Sandbox()
        try:
            before = state_of(sb.report(), "test_count")
            self.assertEqual(before["state"], rc.MATCH)
            path = os.path.join(sb.root, "test_adversarial.py")
            write(path, read(path) +
                  "\n\nclass _AddedByTheControl(unittest.TestCase):\n"
                  "    def test_added_%s(self):\n        pass\n" % "control")
            after = state_of(sb.report(), "test_count")
            self.assertEqual(int(after["measured"]),
                             int(before["measured"]) + 1)
            self.assertEqual(after["state"], rc.MISMATCH)
        finally:
            sb.close()


# ==========================================================================
# A stale claim fails -- on the real README, one number at a time
# ==========================================================================

class TestStaleClaimFails(unittest.TestCase):

    #: Every cheap claim. The mutation is derived from LOCATORS, so this
    #: list names no number and no sentence -- see rc.bump_claim.
    CLAIMS = ["test_count", "fixture_file_count", "empty_dir_count",
              "case_count", "own_test_count"]

    def test_each_stale_claim_is_caught(self):
        for claim in self.CLAIMS:
            with self.subTest(claim=claim):
                sb = Sandbox()
                try:
                    self.assertEqual(state_of(sb.report(), claim)["state"],
                                     rc.MATCH, "precondition")
                    text, _old, _new = rc.bump_claim(read(sb.readme), claim)
                    write(sb.readme, text)
                    report = sb.report()
                    self.assertIn(claim, report["failing"])
                    self.assertNotEqual(state_of(report, claim)["state"],
                                        rc.MATCH)
                finally:
                    sb.close()

    def test_two_occurrences_disagreeing_is_its_own_state(self):
        sb = Sandbox()
        try:
            text, old_value, new_value = rc.bump_claim(read(sb.readme),
                                                       "case_count")
            write(sb.readme, text)
            c = state_of(sb.report(), "case_count")
            self.assertEqual(c["state"], rc.DISAGREEMENT)
            self.assertEqual(sorted(c["claimed_distinct"]),
                             sorted([old_value, new_value]))
        finally:
            sb.close()

    def test_an_occurrence_no_locator_covers_is_a_failure(self):
        """The gap that made the first version of this tool worthless.

        Locators are hand-written. A number can sit in a sentence none of
        them match, go stale there, and be reported clean -- which is the
        very "two numbers for one fact" failure this is supposed to
        catch. Every claim now also sweeps the README for its own value
        and reports anything the locators did not already cover.
        """
        sb = Sandbox()
        try:
            self.assertEqual(state_of(sb.report(), "case_count")["state"],
                             rc.MATCH, "precondition")
            measured = state_of(sb.report(), "case_count")["measured"]
            # A new sentence, in the claim's own vocabulary, that no
            # locator knows about. This is what a future edit looks like.
            write(sb.readme, read(sb.readme) +
                  "\n\nAn appended note: there are %s cases in total.\n"
                  % measured)
            report = sb.report()
            c = state_of(report, "case_count")
            self.assertEqual(c["state"], rc.UNLOCATED)
            self.assertEqual(len(c["unlocated_occurrences"]), 1)
            self.assertIn("case_count", report["failing"])
        finally:
            sb.close()

    def test_a_number_without_the_claim_s_vocabulary_is_not_flagged(self):
        """The control for the test above: no wall of false positives."""
        sb = Sandbox()
        try:
            measured = state_of(sb.report(), "case_count")["measured"]
            write(sb.readme, read(sb.readme) +
                  "\n\nAn unrelated note about port %s and nothing else.\n"
                  % measured)
            c = state_of(sb.report(), "case_count")
            self.assertEqual(c["state"], rc.MATCH, c["unlocated_occurrences"])
        finally:
            sb.close()

    def test_the_real_readme_has_no_unlocated_occurrence(self):
        report = rc.build_report(README, HERE)
        for c in report["claims"]:
            self.assertEqual(c.get("unlocated_occurrences") or [], [],
                             "%s: %s" % (c["claim"],
                                         c.get("unlocated_occurrences")))

    def test_deleting_the_claim_text_is_reported_not_ignored(self):
        """Silence must not read as agreement."""
        sb = Sandbox()
        try:
            text = read(sb.readme)
            for pat in rc.LOCATORS["case_count"]:
                text = pat.sub("REMOVED", text)
            write(sb.readme, text)
            report = sb.report()
            self.assertEqual(state_of(report, "case_count")["state"],
                             rc.NOT_FOUND)
            self.assertIn("case_count", report["failing"])
        finally:
            sb.close()


# ==========================================================================
# CLI
# ==========================================================================

class TestCli(unittest.TestCase):

    def run_cli(self, args, cwd=None):
        return subprocess.run([PY, SCRIPT] + list(args), cwd=cwd or HERE,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def test_clean_repository_exits_0(self):
        proc = self.run_cli([])
        self.assertEqual(proc.returncode, 0, proc.stderr.decode())

    def test_stale_claim_exits_1(self):
        sb = Sandbox()
        try:
            text, _old, _new = rc.bump_claim(read(sb.readme), "case_count")
            write(sb.readme, text)
            proc = self.run_cli(["--readme", sb.readme, "--root", sb.root])
            self.assertEqual(proc.returncode, 1)
        finally:
            sb.close()

    def test_bad_root_exits_2(self):
        proc = self.run_cli(["--root", "/no/such/dir/zzz"])
        self.assertEqual(proc.returncode, 2)
        self.assertTrue(proc.stderr)

    def test_missing_readme_exits_2(self):
        proc = self.run_cli(["--readme", "/no/such/file/zzz.md"])
        self.assertEqual(proc.returncode, 2)

    def test_unwritable_output_exits_2(self):
        proc = self.run_cli(["-o", "/no/such/dir/zzz/out.json"])
        self.assertEqual(proc.returncode, 2)

    def test_output_file_is_canonical_json(self):
        d = tempfile.mkdtemp(prefix="readmecounts_out_")
        try:
            out = os.path.join(d, "r.json")
            proc = self.run_cli(["-o", out, "--quiet"])
            self.assertEqual(proc.returncode, 0)
            self.assertEqual(proc.stdout, b"")
            with open(out, "rb") as fh:
                raw = fh.read()
            self.assertEqual(raw, rc.canonical_dumps(json.loads(raw)).encode())
        finally:
            shutil.rmtree(d)

    def test_two_runs_are_byte_identical(self):
        d = tempfile.mkdtemp(prefix="readmecounts_det_")
        try:
            a, b = os.path.join(d, "a.json"), os.path.join(d, "b.json")
            self.run_cli(["-o", a, "--quiet"])
            self.run_cli(["-o", b, "--quiet"])
            self.assertEqual(read_bytes(a), read_bytes(b))
        finally:
            shutil.rmtree(d)

    def test_relocated_copy_produces_identical_bytes(self):
        sb1 = Sandbox(prefix="readmecounts_reloc_a_")
        sb2 = Sandbox(prefix="readmecounts_reloc_zzzzzzzz_")
        d = tempfile.mkdtemp(prefix="readmecounts_relocout_")
        try:
            a, b = os.path.join(d, "a.json"), os.path.join(d, "b.json")
            self.run_cli(["--readme", sb1.readme, "--root", sb1.root,
                          "-o", a, "--quiet"])
            self.run_cli(["--readme", sb2.readme, "--root", sb2.root,
                          "-o", b, "--quiet"])
            self.assertEqual(read_bytes(a), read_bytes(b))
        finally:
            shutil.rmtree(d)
            sb1.close()
            sb2.close()


# ==========================================================================
# Marker negative control
# ==========================================================================

class TestMarkerIsUnique(unittest.TestCase):
    """The marker above must appear in this file and nowhere else.

    Without this, a future "the marker is not in the output" style check
    could pass because the marker was never anywhere to begin with.
    """

    def test_marker_occurs_only_in_this_file(self):
        repo = os.path.dirname(HERE)
        if not os.path.isdir(os.path.join(repo, ".git")):
            self.skipTest("not a checkout; cannot sweep the repository")
        hits = []
        for dirpath, dirnames, filenames in os.walk(repo):
            dirnames[:] = [d for d in dirnames
                           if d not in (".git", "__pycache__")]
            for fn in filenames:
                p = os.path.join(dirpath, fn)
                try:
                    with open(p, "r", encoding="utf-8", errors="ignore") as fh:
                        if MARKER in fh.read():
                            hits.append(os.path.relpath(p, repo))
                except OSError:
                    continue
        self.assertEqual(hits, [os.path.relpath(__file__, repo)])


if __name__ == "__main__":
    unittest.main()
