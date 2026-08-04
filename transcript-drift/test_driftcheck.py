#!/usr/bin/env python3
"""Test suite for driftcheck.py. Stdlib-only.

Every scenario is a throwaway tool tree built under tempfile, so the suite
never depends on the sibling tool directories existing.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import driftcheck as dc  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
DRIFTCHECK_PY = os.path.join(HERE, "driftcheck.py")
PY = sys.executable or "python3"

GOOD_TRANSCRIPT = """\
=== $ python3 -m unittest test_thing ===
Ran 12 tests in 0.5s

OK
exit=0

=== $ python3 thing.py fixture.json ===
status=clean
exit=0

=== $ python3 thing.py bad.json ===
status=violations
exit=1
"""

GOOD_README = """\
# thing

```
python3 -m unittest test_thing
python3 thing.py fixture.json
python3 thing.py bad.json
```

| tests | `Ran 12 tests` / `OK` |
| clean | exit **0** |
| bad | exit **1** |
"""


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


class TreeMixin:
    def tmp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        return td.name

    def tree(self, tools):
        """tools: {name: (readme_or_None, transcript_or_None)}"""
        root = os.path.join(self.tmp(), "repo")
        os.makedirs(root)
        for name, (rd, tr) in tools.items():
            os.makedirs(os.path.join(root, name), exist_ok=True)
            if rd is not None:
                write(os.path.join(root, name, "README.md"), rd)
            if tr is not None:
                write(os.path.join(root, name, "captured_output.txt"), tr)
        return root

    def run_cli(self, args):
        p = subprocess.run([PY, DRIFTCHECK_PY] + args, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, timeout=120)
        return p.returncode, p.stdout.decode(), p.stderr.decode()

    def codes(self, report, tool=None):
        return sorted(f["code"] for f in report["findings"]
                      if tool is None or f["tool"] == tool)


# ---------------------------------------------------------------------------
# Transcript parsing
# ---------------------------------------------------------------------------

class TestParseTranscript(unittest.TestCase):
    def test_records_found(self):
        recs, _ = dc.parse_transcript(GOOD_TRANSCRIPT)
        self.assertEqual(len(recs), 3)

    def test_command_captured(self):
        recs, _ = dc.parse_transcript(GOOD_TRANSCRIPT)
        self.assertEqual(recs[1]["command"], "python3 thing.py fixture.json")

    def test_exit_code_captured(self):
        recs, _ = dc.parse_transcript(GOOD_TRANSCRIPT)
        self.assertEqual([r["exit_code"] for r in recs], [0, 0, 1])

    def test_test_count_captured(self):
        recs, _ = dc.parse_transcript(GOOD_TRANSCRIPT)
        self.assertEqual(recs[0]["test_count"], 12)

    def test_verdicts_captured(self):
        _, v = dc.parse_transcript(GOOD_TRANSCRIPT)
        self.assertEqual(v, ["OK"])

    def test_failed_verdict_captured(self):
        _, v = dc.parse_transcript("=== $ x ===\nRan 2 tests in 0.1s\n\nFAILED (failures=1)\nexit=1\n")
        self.assertEqual(v, ["FAILED"])

    def test_no_records_in_empty_text(self):
        recs, _ = dc.parse_transcript("just some prose\n")
        self.assertEqual(recs, [])

    def test_preamble_before_first_record_ignored(self):
        recs, _ = dc.parse_transcript("intro line\nexit=9\n" + GOOD_TRANSCRIPT)
        self.assertEqual(len(recs), 3)
        self.assertEqual(recs[0]["exit_code"], 0)

    def test_first_exit_wins_within_a_record(self):
        recs, _ = dc.parse_transcript("=== $ a ===\nexit=3\nexit=4\n")
        self.assertEqual(recs[0]["exit_code"], 3)

    def test_negative_exit_code_parsed(self):
        recs, _ = dc.parse_transcript("=== $ a ===\nexit=-9\n")
        self.assertEqual(recs[0]["exit_code"], -9)

    def test_line_number_recorded(self):
        recs, _ = dc.parse_transcript(GOOD_TRANSCRIPT)
        self.assertEqual(recs[0]["line"], 1)


class TestNormaliseCmd(unittest.TestCase):
    def test_collapses_whitespace(self):
        self.assertEqual(dc.normalise_cmd("python3   a.py    b"), "python3 a.py b")

    def test_strips_echo_exit_suffix(self):
        self.assertEqual(dc.normalise_cmd('python3 a.py ; echo "exit=$?"'), "python3 a.py")

    def test_strips_unquoted_echo_exit_suffix(self):
        self.assertEqual(dc.normalise_cmd("python3 a.py ; echo exit=$?"), "python3 a.py")

    def test_strips_tail_pipe(self):
        self.assertEqual(dc.normalise_cmd("python3 a.py | tail -n 3"), "python3 a.py")

    def test_leaves_other_pipes_alone(self):
        self.assertEqual(dc.normalise_cmd("python3 a.py | jq ."), "python3 a.py | jq .")


# ---------------------------------------------------------------------------
# README parsing
# ---------------------------------------------------------------------------

class TestParseReadme(unittest.TestCase):
    def test_commands_from_fence(self):
        c = dc.parse_readme(GOOD_README)["commands"]
        self.assertIn("python3 thing.py fixture.json", c)
        self.assertEqual(len(c), 3)

    def test_prose_outside_fence_is_not_a_command(self):
        c = dc.parse_readme("run python3 thing.py yourself\n")["commands"]
        self.assertEqual(c, [])

    def test_comment_lines_in_fence_ignored(self):
        c = dc.parse_readme("```\n# python3 nope.py\npython3 yes.py\n```\n")["commands"]
        self.assertEqual(c, ["python3 yes.py"])

    def test_test_counts_extracted(self):
        self.assertIn(12, dc.parse_readme(GOOD_README)["test_counts"])

    def test_bold_test_count_extracted(self):
        self.assertIn(174, dc.parse_readme("**174 tests**, `OK`")["test_counts"])

    def test_exit_claims_extracted(self):
        claims = dc.readme_exit_claims(GOOD_README)
        self.assertEqual(claims, {0, 1})

    def test_exit_code_word_form_extracted(self):
        self.assertIn(2, dc.readme_exit_claims("returns exit code 2 on setup error"))


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------

class TestCompare(unittest.TestCase):
    def test_matching_pair_has_no_findings(self):
        f, _ = dc.compare("t", GOOD_README, GOOD_TRANSCRIPT)
        self.assertEqual(f, [])

    def test_test_count_mismatch(self):
        rd = GOOD_README.replace("Ran 12 tests", "Ran 99 tests")
        f, _ = dc.compare("t", rd, GOOD_TRANSCRIPT)
        self.assertEqual([x["code"] for x in f], [dc.D_TEST_COUNT])

    def test_test_count_mismatch_reports_both_sides(self):
        rd = GOOD_README.replace("Ran 12 tests", "Ran 99 tests")
        f, _ = dc.compare("t", rd, GOOD_TRANSCRIPT)
        self.assertEqual(f[0]["detail"]["transcript_ran"], [12])
        self.assertIn(99, f[0]["detail"]["readme_claims"])

    def test_test_count_not_claimed(self):
        rd = "# thing\n\n```\npython3 -m unittest test_thing\npython3 thing.py fixture.json\npython3 thing.py bad.json\n```\nexit 0 exit 1\n"
        f, _ = dc.compare("t", rd, GOOD_TRANSCRIPT)
        self.assertIn(dc.D_TEST_UNCLAIMED, [x["code"] for x in f])

    def test_exit_code_mismatch(self):
        tr = GOOD_TRANSCRIPT.replace("exit=1", "exit=7")
        f, _ = dc.compare("t", GOOD_README, tr)
        codes = [x["code"] for x in f]
        self.assertIn(dc.D_EXIT_MISMATCH, codes)

    def test_exit_code_mismatch_names_the_unacknowledged_code(self):
        tr = GOOD_TRANSCRIPT.replace("exit=1", "exit=7")
        f, _ = dc.compare("t", GOOD_README, tr)
        m = [x for x in f if x["code"] == dc.D_EXIT_MISMATCH][0]
        self.assertEqual(m["detail"]["unacknowledged"], [7])

    def test_readme_command_not_in_transcript(self):
        rd = GOOD_README.replace("python3 thing.py bad.json", "python3 thing.py never_run.json")
        f, _ = dc.compare("t", rd, GOOD_TRANSCRIPT)
        m = [x for x in f if x["code"] == dc.D_CMD_NOT_RUN][0]
        self.assertEqual(m["detail"]["commands"], ["python3 thing.py never_run.json"])

    def test_transcript_with_no_records(self):
        f, _ = dc.compare("t", GOOD_README, "$ python3 thing.py\nexit=0\n")
        self.assertEqual([x["code"] for x in f], [dc.D_NO_RECORDS])

    def test_record_with_no_exit_line(self):
        tr = "=== $ python3 -m unittest test_thing ===\nRan 12 tests in 0.5s\n\nOK\n"
        f, _ = dc.compare("t", GOOD_README, tr)
        self.assertIn(dc.D_RECORD_NO_EXIT, [x["code"] for x in f])

    def test_transcript_showing_failure(self):
        tr = GOOD_TRANSCRIPT.replace("OK", "FAILED (failures=1)")
        f, _ = dc.compare("t", GOOD_README, tr)
        self.assertIn(dc.D_TEST_FAILED, [x["code"] for x in f])

    def test_stats_are_returned(self):
        _, s = dc.compare("t", GOOD_README, GOOD_TRANSCRIPT)
        self.assertEqual(s["records"], 3)
        self.assertEqual(s["transcript_exits"], [0, 1])


# ---------------------------------------------------------------------------
# End to end
# ---------------------------------------------------------------------------

class TestEndToEnd(TreeMixin, unittest.TestCase):
    def test_clean_tree_exits_zero(self):
        root = self.tree({"thing": (GOOD_README, GOOD_TRANSCRIPT)})
        code, out, err = self.run_cli(["--root", root])
        self.assertEqual(code, 0, msg=err + out)
        self.assertEqual(json.loads(out)["status"], "clean")

    def test_drifting_tree_exits_one(self):
        rd = GOOD_README.replace("Ran 12 tests", "Ran 99 tests")
        root = self.tree({"thing": (rd, GOOD_TRANSCRIPT)})
        code, out, _ = self.run_cli(["--root", root])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out)["status"], "drift")

    def test_missing_transcript_reported(self):
        root = self.tree({"thing": (GOOD_README, None)})
        code, out, _ = self.run_cli(["--root", root])
        self.assertEqual(code, 1)
        self.assertIn(dc.D_MISSING_TRANSCRIPT, self.codes(json.loads(out)))

    def test_missing_readme_reported(self):
        root = self.tree({"thing": (None, GOOD_TRANSCRIPT)})
        code, out, _ = self.run_cli(["--root", root])
        self.assertEqual(code, 1)
        self.assertIn(dc.D_MISSING_README, self.codes(json.loads(out)))

    def test_bad_root_exits_two(self):
        code, out, _ = self.run_cli(["--root", "/nonexistent-dir-xyz"])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(out)["status"], "error")

    def test_bad_inventory_exits_two(self):
        root = self.tree({"thing": (GOOD_README, GOOD_TRANSCRIPT)})
        code, out, _ = self.run_cli(["--root", root, "--inventory", "/nope.json"])
        self.assertEqual(code, 2)

    def test_malformed_inventory_exits_two(self):
        root = self.tree({"thing": (GOOD_README, GOOD_TRANSCRIPT)})
        bad = os.path.join(self.tmp(), "inv.json")
        write(bad, "{not json")
        code, out, _ = self.run_cli(["--root", root, "--inventory", bad])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(out)["status"], "error")

    def test_unwritable_output_exits_two(self):
        root = self.tree({"thing": (GOOD_README, GOOD_TRANSCRIPT)})
        code, out, err = self.run_cli(["--root", root, "-o", "/no/such/dir/r.json"])
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertEqual(json.loads(err)["status"], "error")

    def test_inventory_adds_presence_only_coverage(self):
        root = self.tree({"thing": (GOOD_README, GOOD_TRANSCRIPT)})
        inv = os.path.join(self.tmp(), "inv.json")
        write(inv, json.dumps({"thing": {"readme": True, "transcript": True},
                               "elsewhere": {"readme": True, "transcript": False}}))
        code, out, _ = self.run_cli(["--root", root, "--inventory", inv])
        report = json.loads(out)
        self.assertEqual(code, 1)
        self.assertEqual(report["coverage"]["content_compared"], 1)
        self.assertIn("elsewhere", report["coverage"]["presence_only_tools"])
        self.assertIn(dc.D_MISSING_TRANSCRIPT, self.codes(report, "elsewhere"))

    def test_coverage_never_claims_content_it_did_not_read(self):
        root = self.tree({"thing": (GOOD_README, GOOD_TRANSCRIPT)})
        inv = os.path.join(self.tmp(), "inv.json")
        write(inv, json.dumps({"far": {"readme": True, "transcript": True}}))
        _, out, _ = self.run_cli(["--root", root, "--inventory", inv])
        report = json.loads(out)
        self.assertNotIn("far", report["coverage"]["compared_tools"])
        self.assertNotIn("far", report["stats"])

    def test_findings_sorted_and_deterministic(self):
        rd = GOOD_README.replace("Ran 12 tests", "Ran 99 tests")
        root = self.tree({"zeta": (rd, GOOD_TRANSCRIPT), "alpha": (rd, GOOD_TRANSCRIPT)})
        _, a, _ = self.run_cli(["--root", root])
        _, b, _ = self.run_cli(["--root", root])
        self.assertEqual(a, b)
        keys = [(f["tool"], f["code"]) for f in json.loads(a)["findings"]]
        self.assertEqual(keys, sorted(keys))

    def test_report_has_no_timestamps(self):
        root = self.tree({"thing": (GOOD_README, GOOD_TRANSCRIPT)})
        _, out, _ = self.run_cli(["--root", root])
        low = out.lower()
        for banned in ("timestamp", "duration", "elapsed", "generated_at"):
            self.assertNotIn(banned, low)

    def test_drift_counts_cover_every_code(self):
        root = self.tree({"thing": (GOOD_README, GOOD_TRANSCRIPT)})
        _, out, _ = self.run_cli(["--root", root])
        self.assertEqual(set(json.loads(out)["drift_counts"]), set(dc.ALL_CODES))

    def test_output_file_single_trailing_newline(self):
        root = self.tree({"thing": (GOOD_README, GOOD_TRANSCRIPT)})
        p = os.path.join(self.tmp(), "r.json")
        self.run_cli(["--root", root, "-o", p])
        with open(p, "rb") as fh:
            data = fh.read()
        self.assertTrue(data.endswith(b"\n"))
        self.assertFalse(data.endswith(b"\n\n"))


class TestReadmeExitTable(unittest.TestCase):
    """The markdown exit table is how nearly every README in this repository
    documents its exit codes. Not reading it made EXIT_CODE_MISMATCH silently
    unfireable on most of them."""

    TABLE = (
        "# t\n\n"
        "| Exit | Meaning |\n"
        "|---|---|\n"
        "| `0` | clean |\n"
        "| `1` | drift found |\n"
        "| `2` | setup error |\n\n"
        "Prose after the table.\n"
    )

    def test_table_rows_are_claims(self):
        self.assertEqual(dc.readme_exit_claims(self.TABLE), {0, 1, 2})

    def test_bold_and_bare_rows_also_count(self):
        t = "| Exit | Meaning |\n|---|---|\n| **3** | x |\n| 4 | y |\n"
        self.assertEqual(dc.readme_exit_claims(t), {3, 4})

    def test_negative_exit_row(self):
        t = "| Exit | Meaning |\n|---|---|\n| `-9` | killed |\n"
        self.assertIn(-9, dc.readme_exit_claims(t))

    def test_table_ends_at_first_non_pipe_line(self):
        t = ("| Exit | Meaning |\n|---|---|\n| `0` | clean |\n"
             "\n| Rows | Of |\n|---|---|\n| 7 | unrelated table |\n")
        self.assertEqual(dc.readme_exit_claims(t), {0})

    def test_non_exit_table_is_not_harvested(self):
        t = "| Probe | Tool |\n|---|---|\n| 5 | thing |\n"
        self.assertEqual(dc.readme_exit_claims(t), set())

    def test_prose_and_table_claims_union(self):
        self.assertEqual(dc.readme_exit_claims("exit 7\n" + self.TABLE),
                         {0, 1, 2, 7})

    def test_table_claim_silences_mismatch_end_to_end(self):
        readme = self.TABLE + "\n```\npython3 t.py\n```\n"
        transcript = "=== $ python3 t.py ===\nexit=2\n"
        findings, _ = dc.compare("t", readme, transcript)
        self.assertNotIn(dc.D_EXIT_MISMATCH, {f["code"] for f in findings})

    def test_exit_outside_table_still_mismatches(self):
        readme = ("| Exit | Meaning |\n|---|---|\n| `0` | clean |\n"
                  "\n```\npython3 t.py\n```\n")
        transcript = "=== $ python3 t.py ===\nexit=2\n"
        findings, _ = dc.compare("t", readme, transcript)
        self.assertIn(dc.D_EXIT_MISMATCH, {f["code"] for f in findings})


class TestStdlibOnlyImports(unittest.TestCase):
    ALLOWED = {"argparse", "json", "os", "re", "sys"}

    def test_only_allow_listed_imports(self):
        import ast
        with open(DRIFTCHECK_PY, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        found = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                found.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                found.add(node.module.split(".")[0])
        self.assertEqual(found - self.ALLOWED, set())


if __name__ == "__main__":
    unittest.main()
