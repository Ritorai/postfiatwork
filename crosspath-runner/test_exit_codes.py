#!/usr/bin/env python3
"""Focused regression coverage for the defect fixed alongside this file:
crosspath.py's README exit-code table was correct but invisible to
transcript-drift/driftcheck.py's own table scanner (see README.md,
"### Exit codes", for the full account).

Why a separate file instead of extending test_crosspath.py: test_crosspath.py
already drives exit 0/1/2 incidentally as a side effect of testing other
behaviour (e.g. test_only_selects_a_subset happens to assert code == 0,
test_tool_that_writes_no_report_is_an_execution_error happens to assert
code == 2). Nothing in that file states, in one place, "these are the three
exit codes the README documents, and here is each one produced for real and
checked against the table". That is what a reviewer chasing this specific
defect needs to find quickly, so it gets its own file rather than being
mixed into 70+ pre-existing tests covering unrelated behaviour. It reuses
test_crosspath.py's tree-building helpers rather than duplicating them,
since both files live in the same directory and test the same module.

Two things are covered:

1. TestExitCodesEndToEnd -- runs crosspath.py as a real subprocess (not
   main() called in-process) and drives it to actually exit 0, actually
   exit 1, and actually exit 2 (both documented causes: setup error and
   execution error), asserting real subprocess.returncode each time.

2. TestReadmeExitTableIsMachineReadable -- the regression test that would
   have caught the original defect. It re-implements the exact matching
   rules transcript-drift/driftcheck.py uses to harvest exit-code claims
   from a README (a table scan gated on the header cell starting with the
   word "exit", plus a prose regex) and asserts, against this directory's
   actual README.md text, that (a) the exit-code table's header now
   matches driftcheck's TABLE_EXIT_HEADER_RE, and (b) the set of exit codes
   harvestable from the README is exactly the set of exit codes this same
   test run actually observed from crosspath.py -- so the assertion is
   never a hardcoded {0, 1, 2}, it is "the doc matches the tool, checked
   the same way driftcheck checks it, in this run."
"""

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import crosspath  # noqa: E402
import test_crosspath as tc  # noqa: E402 -- reuse TreeMixin/write/fixtures

README_PATH = os.path.join(HERE, "README.md")


def run_scenario_exit_code(tools):
    """Standalone helper (no unittest.TestCase / addCleanup needed): build a
    throwaway tree for `tools` ({name: source}), run crosspath.py on it as a
    real subprocess, return only the exit code. Uses a context-managed temp
    dir so cleanup happens even though this isn't a TreeMixin instance."""
    with tempfile.TemporaryDirectory() as base:
        root = os.path.join(base, "tree")
        os.makedirs(root)
        entries = {}
        for name, source in tools.items():
            tc.write(os.path.join(root, name, "tool.py"), source)
            entries[name] = {
                "status": "baselined",
                "command": list(tc.FILE_CMD),
                "report_mode": "file",
                "expected_exit_code": 0,
                "expected_report_sha256": None,
            }
        manifest = os.path.join(root, "manifest.json")
        tc.write(manifest, json.dumps({"tools": entries}))
        proc = subprocess.run(
            [tc.PY, tc.CROSSPATH_PY, "--root", root, "--manifest", manifest],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=300)
        return proc.returncode

# transcript-drift lives two directories away from where this deliverable is
# extracted to for standalone review (see the "Final step" of the task this
# file was written for), so it is not always present as a sibling. The
# regexes below are re-implemented copies, not an import, precisely so this
# file's primary assertions never depend on that directory existing. Where
# it *is* available (running inside the full repository checkout), a second,
# best-effort test cross-checks the copies below against the live source
# text of transcript-drift/driftcheck.py, purely as a drift canary; that one
# test is skipped, not failed, if the sibling file is absent.
TRANSCRIPT_DRIFT_PY = os.path.normpath(
    os.path.join(HERE, "..", "transcript-drift", "driftcheck.py"))

# Verbatim copies of transcript-drift/driftcheck.py's matching rules.
TABLE_EXIT_HEADER_RE = re.compile(r"^\|\s*\**exit\b", re.I)
TABLE_ROW_INT_RE = re.compile(r"^\|\s*\*{0,2}`?(-?\d+)`?\*{0,2}\s*\|")
PROSE_EXIT_RE = re.compile(r"exit(?:\s+code)?[\s=]*\*{0,2}`?(-?\d+)`?\*{0,2}")


def harvest_readme_exit_claims(text):
    """Mirror of driftcheck.readme_exit_claims: prose matches plus a table
    scan gated on a header cell that starts with the word 'exit'."""
    out = set()
    for m in PROSE_EXIT_RE.finditer(text):
        out.add(int(m.group(1)))
    in_table = False
    for line in text.splitlines():
        if TABLE_EXIT_HEADER_RE.match(line):
            in_table = True
            continue
        if not in_table:
            continue
        if not line.startswith("|"):
            in_table = False
            continue
        m = TABLE_ROW_INT_RE.match(line)
        if m:
            out.add(int(m.group(1)))
    return out


class TestExitCodesEndToEnd(tc.TreeMixin, unittest.TestCase):
    """Drives crosspath.py as a real subprocess to produce each documented
    exit code for real, from fixture trees built fresh in temp directories.
    """

    # -- exit 0: unambiguous ------------------------------------------------
    # A single tool, whose report is byte-identical no matter where it runs,
    # compared for real across two copies made by crosspath.py itself
    # (mode "copied", the normal usage from the README). Nothing here is a
    # synthetic in-process call to main(); this is the actual CLI, the
    # actual two-copy machinery, the actual subprocess exit status.
    def test_all_tools_identical_exits_zero_unambiguously(self):
        code, report, err = self.run_tree({"clean": tc.CLEAN})
        self.assertEqual(code, 0, msg="stderr was: %r" % err)
        self.assertEqual(report["status"], "identical")
        self.assertEqual(report["summary"]["divergent"], 0)
        self.assertEqual(report["summary"]["error"], 0)
        self.assertEqual(report["summary"]["identical"], 1)
        self.assertEqual(report["code_counts"][crosspath.C_LEAK], 0)
        self.assertEqual(report["code_counts"][crosspath.C_HASH], 0)

    def test_exit_zero_also_holds_with_several_identical_tools(self):
        # Guards against a single-tool tree coincidentally exiting 0.
        code, report, err = self.run_tree({"a": tc.CLEAN, "b": tc.CLEAN})
        self.assertEqual(code, 0, msg="stderr was: %r" % err)
        self.assertEqual(report["summary"]["identical"], 2)
        self.assertEqual(report["summary"]["divergent"], 0)
        self.assertEqual(report["summary"]["error"], 0)

    # -- exit 1: at least one tool diverged ---------------------------------
    def test_one_divergent_tool_exits_one(self):
        code, report, err = self.run_tree({"leaky": tc.LEAKY})
        self.assertEqual(code, 1, msg="stderr was: %r" % err)
        self.assertEqual(report["status"], "divergent")
        self.assertEqual(report["summary"]["divergent"], 1)
        self.assertEqual(report["summary"]["error"], 0)
        self.assertIn(crosspath.C_LEAK, self.by_tool(report)["leaky"]["codes"])

    # -- exit 2: both documented causes -------------------------------------
    def test_setup_error_exits_two(self):
        # "setup error" half of the exit-2 row: an unreadable manifest,
        # never reaching the point where any tool would run.
        root = self.tmp()
        code, out, err = self.run_cli(
            ["--root", root, "--manifest", os.path.join(root, "absent.json")])
        self.assertEqual(code, 2, msg="stderr was: %r" % err)
        payload = json.loads(out)
        self.assertEqual(payload["status"], "error")

    def test_execution_error_exits_two(self):
        # "a tool could not be executed" half of the exit-2 row: the tool
        # runs but never produces the report file crosspath.py asked for.
        code, report, err = self.run_tree({"crash": tc.CRASHER})
        self.assertEqual(code, 2, msg="stderr was: %r" % err)
        self.assertEqual(report["status"], "error")
        self.assertEqual(report["summary"]["error"], 1)
        self.assertIn(crosspath.C_ERROR, self.by_tool(report)["crash"]["codes"])

    def test_execution_error_and_setup_error_are_both_exit_two_not_distinguished(self):
        # The README documents exactly one code, 2, for two distinct causes.
        # This test exists so that if crosspath.py were ever changed to
        # split them into different exit codes, it would fail here rather
        # than only being noticed by re-reading the table by eye.
        root = self.tmp()
        setup_code, _, _ = self.run_cli(
            ["--root", root, "--manifest", os.path.join(root, "absent.json")])
        exec_code, _, _ = self.run_tree({"crash": tc.CRASHER})
        self.assertEqual(setup_code, exec_code)
        self.assertEqual(setup_code, 2)


class TestReadmeExitTableIsMachineReadable(unittest.TestCase):
    """The regression test that would have caught the original defect: the
    README's exit-code table must actually be harvestable by the rule
    transcript-drift/driftcheck.py uses, not merely correct to a human
    reader."""

    @classmethod
    def setUpClass(cls):
        with open(README_PATH, "r", encoding="utf-8") as fh:
            cls.readme_text = fh.read()
        cls.readme_lines = cls.readme_text.splitlines()

    def test_exit_code_section_exists(self):
        self.assertIn("### Exit codes", self.readme_text)

    def test_exit_table_header_matches_driftcheck_pattern(self):
        # Locate the header row directly under "### Exit codes".
        idx = self.readme_text.index("### Exit codes")
        after = self.readme_text[idx:]
        header_line = next(
            line for line in after.splitlines() if line.strip().startswith("|"))
        self.assertTrue(
            TABLE_EXIT_HEADER_RE.match(header_line),
            msg="exit-code table header %r does not match driftcheck's "
                "TABLE_EXIT_HEADER_RE (%s); driftcheck will silently skip "
                "the whole table, harvesting none of its rows" %
                (header_line, TABLE_EXIT_HEADER_RE.pattern))

    def test_divergence_codes_table_is_untouched_and_still_headed_code(self):
        # This fix must not spill into the unrelated divergence-codes table
        # (PATH_LEAK, REPORT_HASH_DIVERGENCE, ...), which is not about exit
        # codes and should keep its own "Code" header.
        idx = self.readme_text.index("## Divergence codes")
        after = self.readme_text[idx:]
        header_line = next(
            line for line in after.splitlines() if line.strip().startswith("|"))
        self.assertEqual(header_line.strip(), "| Code | Meaning |")

    def test_harvested_exit_claims_equal_the_exit_codes_actually_observed(self):
        # No hardcoded {0, 1, 2}: this drives crosspath.py itself, right
        # here, to produce each documented exit code for real, then checks
        # that harvesting the README the way driftcheck harvests it yields
        # exactly that same set.
        zero_code = run_scenario_exit_code({"clean": tc.CLEAN})
        one_code = run_scenario_exit_code({"leaky": tc.LEAKY})
        two_code = run_scenario_exit_code({"crash": tc.CRASHER})

        observed = {zero_code, one_code, two_code}
        self.assertEqual(observed, {0, 1, 2},
                         msg="sanity check on the harness itself, not the README")

        claimed = harvest_readme_exit_claims(self.readme_text)
        self.assertEqual(
            claimed, observed,
            msg="driftcheck-style harvesting of README.md found %r but "
                "crosspath.py actually produces %r in this run; the table "
                "and/or prose no longer document every real exit code" %
                (sorted(claimed), sorted(observed)))

    def test_zero_is_reachable_only_via_the_table_not_the_prose(self):
        # Documents precisely why exit 0 was the one code driftcheck missed
        # before this fix: the prose sentence uses "exit`s`", which the
        # prose regex does not match, so 0 has always depended on the table
        # scan succeeding. This nails that dependency down.
        prose_only = set(PROSE_EXIT_RE.findall(self.readme_text))
        self.assertNotIn(
            "0", prose_only,
            msg="if this ever starts failing, the prose regex now matches "
                "'exit 0' somewhere and the table-only dependency described "
                "in README.md is no longer accurate")


@unittest.skipUnless(os.path.isfile(TRANSCRIPT_DRIFT_PY),
                     "transcript-drift/driftcheck.py not present alongside "
                     "this directory (expected when crosspath-runner is "
                     "reviewed standalone, outside the full repository)")
class TestLocalPatternsMatchLiveDriftcheckSource(unittest.TestCase):
    """Best-effort canary, not a hard dependency: when the sibling
    transcript-drift directory is present, confirm the regexes duplicated
    above have not silently drifted from the real ones in driftcheck.py.
    Parses driftcheck.py as text (no import, no sys.path change), so this
    file never becomes import-coupled to another tool directory."""

    def test_table_exit_header_pattern_matches_live_source(self):
        with open(TRANSCRIPT_DRIFT_PY, "r", encoding="utf-8") as fh:
            src = fh.read()
        m = re.search(r'TABLE_EXIT_HEADER_RE\s*=\s*re\.compile\(r"([^"]*)"',
                      src)
        self.assertIsNotNone(m, "could not locate TABLE_EXIT_HEADER_RE in "
                                "driftcheck.py; source may have moved")
        self.assertEqual(m.group(1), TABLE_EXIT_HEADER_RE.pattern)

    def test_table_row_int_pattern_matches_live_source(self):
        with open(TRANSCRIPT_DRIFT_PY, "r", encoding="utf-8") as fh:
            src = fh.read()
        m = re.search(r'TABLE_ROW_INT_RE\s*=\s*re\.compile\(r"([^"]*)"', src)
        self.assertIsNotNone(m, "could not locate TABLE_ROW_INT_RE in "
                                "driftcheck.py; source may have moved")
        self.assertEqual(m.group(1), TABLE_ROW_INT_RE.pattern)

    def test_prose_exit_pattern_matches_live_source(self):
        with open(TRANSCRIPT_DRIFT_PY, "r", encoding="utf-8") as fh:
            src = fh.read()
        m = re.search(
            r're\.finditer\(r"(exit\(\?:.*?)",\s*text\)', src)
        self.assertIsNotNone(m, "could not locate the prose exit regex in "
                                "driftcheck.py; source may have moved")
        self.assertEqual(m.group(1), PROSE_EXIT_RE.pattern)


if __name__ == "__main__":
    unittest.main()
