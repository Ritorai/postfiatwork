"""Focused tests for readmeindex.py.

Run: python3 -m unittest test_readmeindex
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

import readmeindex as R

HERE = os.path.dirname(os.path.abspath(__file__))


class TempTreeMixin:
    def setUp(self):
        # Track the directory we created. Never remove its parent -- removing
        # os.path.dirname(mkdtemp()) is the system temp dir itself.
        self._dirs = []

    def tearDown(self):
        for d in self._dirs:
            shutil.rmtree(d, ignore_errors=True)

    def tree(self, spec):
        root = tempfile.mkdtemp(prefix="readmeindex_test_")
        self._dirs.append(root)
        for name, text in spec.items():
            d = os.path.join(root, name)
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, "README.md"), "w", encoding="utf-8", newline="\n") as fh:
                fh.write(text)
        return root


# --------------------------------------------------------------- extraction

class TestStrongSummaryRule(unittest.TestCase):
    def test_bold_count_with_ok(self):
        s, c, ev = R.extract_claim([(5, "**65 tests, `OK`, exit 0.** CPython 3.10.12")])
        self.assertEqual((s, c), (R.STATUS_CLAIM, 65))
        self.assertEqual(ev[0]["rule"], "strong_summary")
        self.assertEqual(ev[0]["line"], 5)

    def test_ran_count_with_ok_in_table_cell(self):
        s, c, _ = R.extract_claim([(21, "| tests | `Ran 36 tests` / `OK` |")])
        self.assertEqual((s, c), (R.STATUS_CLAIM, 36))

    def test_all_passing_counts_as_success_marker(self):
        s, c, _ = R.extract_claim([(223, "- `python3 -m unittest x -v` -> **168 tests, all passing**")])
        self.assertEqual((s, c), (R.STATUS_CLAIM, 168))

    def test_count_without_success_marker_is_not_strong(self):
        # A bare "Ran N tests" inside prose about another tool must not win.
        s, c, ev = R.extract_claim([(120, "| transcript says `Ran 41 tests`, README claims 3 |")])
        self.assertNotEqual(ev[0]["rule"], "strong_summary")

    def test_strong_beats_a_conflicting_bare_mention(self):
        lines = [
            (4, 'checkable claims - "Ran 26 tests", "exit 1", a block of commands'),
            (159, "**57 tests, `OK`, exit 0.**"),
        ]
        s, c, _ = R.extract_claim(lines)
        self.assertEqual((s, c), (R.STATUS_CLAIM, 57))

    def test_two_conflicting_strong_values_are_ambiguous(self):
        lines = [(1, "**10 tests, `OK`**"), (2, "**20 tests, `OK`**")]
        s, c, _ = R.extract_claim(lines)
        self.assertEqual(s, R.STATUS_AMBIGUOUS)
        self.assertIsNone(c)


class TestSelfReportRule(unittest.TestCase):
    def test_word_between_number_and_tests(self):
        # "170 unit tests" -- the shape a naive /(\d+)\s+tests?/ misses.
        s, c, ev = R.extract_claim([(357, "test_bundle_index.py    170 unit tests")])
        self.assertEqual((s, c), (R.STATUS_CLAIM, 170))
        self.assertEqual(ev[0]["rule"], "self_report")

    def test_two_words_between_number_and_tests(self):
        s, c, _ = R.extract_claim([(248, "- `test_claimhist.py` - 154 unit/integration tests")])
        self.assertEqual((s, c), (R.STATUS_CLAIM, 154))

    def test_same_file_named_twice_with_same_count_is_one_claim(self):
        lines = [(1, "test_x.py 40 tests"), (9, "test_x.py 40 unit tests")]
        s, c, _ = R.extract_claim(lines)
        self.assertEqual((s, c), (R.STATUS_CLAIM, 40))

    def test_same_file_with_conflicting_counts_is_ambiguous(self):
        lines = [(1, "test_x.py 40 tests"), (9, "test_x.py 41 tests")]
        s, c, _ = R.extract_claim(lines)
        self.assertEqual(s, R.STATUS_AMBIGUOUS)


class TestFixtureSumRegression(unittest.TestCase):
    """The bug that running the tool on real data exposed.

    An earlier rule treated distinct test_*.py self-reports as additive. On
    commit-claim-auditor that summed a bundled FIXTURE (test_example.py, 3
    tests) with the real suite (test_claimhist.py, 154) and reported a
    confident 157 -- a number that appears nowhere in that README.
    """

    def test_distinct_test_files_do_not_silently_sum(self):
        lines = [
            (154, "recompute to `CURRENT` (the bundled `test_example.py` really has 3 tests),"),
            (248, "- `test_claimhist.py` - 154 unit/integration tests (`python3 -m unittest"),
        ]
        s, c, ev = R.extract_claim(lines)
        self.assertEqual(s, R.STATUS_AMBIGUOUS)
        self.assertIsNone(c, "must not invent 157")
        self.assertEqual(sorted({e["value"] for e in ev}), [3, 154])

    def test_stated_total_still_wins_over_component_files(self):
        # regression-checker states its own total, so it stays a confident claim.
        lines = [
            (27, "| `test_regress_newline.py` | 8 tests pinning the newline fix |"),
            (28, "| `test_regress_integrity.py` | 35 tests pinning the exit-code fix |"),
            (272, "**174 tests, `OK`** (131 + 8 + 35), starting from a deleted `fixtures/`;"),
        ]
        s, c, _ = R.extract_claim(lines)
        self.assertEqual((s, c), (R.STATUS_CLAIM, 174))


class TestBareAndMissing(unittest.TestCase):
    def test_single_bare_count(self):
        s, c, ev = R.extract_claim([(353, "`python3 -m unittest test_x -v` runs 152 tests covering: canonical")])
        self.assertEqual((s, c), (R.STATUS_CLAIM, 152))
        self.assertEqual(ev[0]["rule"], "bare")

    def test_invocation_line_only_is_missing(self):
        s, c, ev = R.extract_claim([(32, "python3 -m unittest test_bundleverify -v")])
        self.assertEqual(s, R.STATUS_MISSING)
        self.assertIsNone(c)
        self.assertEqual(ev, [])

    def test_no_candidate_lines_is_missing(self):
        s, c, _ = R.extract_claim([])
        self.assertEqual(s, R.STATUS_MISSING)

    def test_conflicting_bare_counts_are_ambiguous(self):
        s, c, _ = R.extract_claim([(1, "7 tests"), (2, "9 tests")])
        self.assertEqual(s, R.STATUS_AMBIGUOUS)


# --------------------------------------------------------------- prefilter

RULE_POSITIVE_FIXTURES = [
    "**65 tests, `OK`, exit 0.**",
    "| tests | `Ran 36 tests` / `OK` |",
    "- `python3 -m unittest x -v` -> **168 tests, all passing**",
    "test_bundle_index.py    170 unit tests",
    "- `test_claimhist.py` - 154 unit/integration tests",
    "`python3 -m unittest test_x -v` runs 152 tests covering: canonical",
    "**174 tests, `OK`** (131 + 8 + 35)",
    "test_claimcheck.py            224 unit tests (180 pre-existing + 44 new)",
    "- `test_weakassert.py` - 202 unittest tests",
    "7 tests",
]


class TestPrefilterIsSuperset(unittest.TestCase):
    def test_every_rule_positive_fixture_passes_the_prefilter(self):
        self.assertTrue(RULE_POSITIVE_FIXTURES)
        for text in RULE_POSITIVE_FIXTURES:
            with self.subTest(text=text):
                self.assertIsNotNone(
                    R.PREFILTER.search(text),
                    "prefilter would drop a line a rule matches",
                )

    def test_every_fixture_actually_fires_a_rule(self):
        # Without this the superset test above could pass vacuously.
        for text in RULE_POSITIVE_FIXTURES:
            with self.subTest(text=text):
                status, _, _ = R.extract_claim([(1, text)])
                self.assertNotEqual(status, R.STATUS_MISSING)


# --------------------------------------------------------------- discovery

class TestDiscovery(TempTreeMixin, unittest.TestCase):
    def test_discovers_only_dirs_with_readme(self):
        root = self.tree({"alpha": "# Alpha\n\n**5 tests, `OK`**\n", "beta": "# Beta\n\n7 tests\n"})
        os.makedirs(os.path.join(root, "no_readme"))
        found = R.discover_from_root(root)
        self.assertEqual(sorted(found), ["alpha", "beta"])

    def test_titles_and_counts(self):
        root = self.tree({"alpha": "# Alpha Tool\n\n**5 tests, `OK`**\n"})
        tools = R.build_tools(R.discover_from_root(root))
        self.assertEqual(tools[0]["title"], "Alpha Tool")
        self.assertEqual(tools[0]["claimed_tests"], 5)

    def test_dotted_dirs_ignored(self):
        root = self.tree({"alpha": "# Alpha\n\n5 tests\n"})
        hidden = os.path.join(root, ".git")
        os.makedirs(hidden)
        with open(os.path.join(hidden, "README.md"), "w") as fh:
            fh.write("# Nope\n\n9 tests\n")
        self.assertEqual(sorted(R.discover_from_root(root)), ["alpha"])


class TestRootAndCorpusAgree(TempTreeMixin, unittest.TestCase):
    """The corpus is only trustworthy if it yields the same answer as the tree."""

    def test_full_scan_equals_corpus_scan(self):
        spec = {
            "alpha": "# Alpha\n\nprose\n**12 tests, `OK`**\nmore prose\n",
            "beta": "# Beta\n\ntest_beta.py  34 unit tests\n",
            "gamma": "# Gamma\n\npython3 -m unittest test_gamma -v\n",
            "delta": "# Delta\n\n3 tests\nand 4 tests\n",
        }
        root = self.tree(spec)
        from_root = R.build_tools(R.discover_from_root(root))
        self.assertTrue(from_root)

        # Emit a corpus the same way the committed one was produced.
        rows = []
        for name in sorted(spec):
            text = spec[name]
            lines = text.split("\n")
            for i, line in enumerate(lines, start=1):
                if R.HEADING.match(line):
                    rows.append("%s\t%d\tH\t%s" % (name, i, line))
                    break
            for i, line in enumerate(lines, start=1):
                if R.PREFILTER.search(line):
                    rows.append("%s\t%d\tT\t%s" % (name, i, line))
            rows.append("%s\t%d\tN\t-" % (name, len(lines)))
        fd, path = tempfile.mkstemp(suffix=".tsv")
        os.close(fd)
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("\n".join(rows) + "\n")
        try:
            from_corpus = R.build_tools(R.discover_from_corpus(path))
        finally:
            os.unlink(path)

        strip = lambda ts: [(t["tool"], t["title"], t["status"], t["claimed_tests"]) for t in ts]
        self.assertEqual(strip(from_root), strip(from_corpus))
        self.assertEqual(
            strip(from_root),
            [
                ("alpha", "Alpha", R.STATUS_CLAIM, 12),
                ("beta", "Beta", R.STATUS_CLAIM, 34),
                ("delta", "Delta", R.STATUS_AMBIGUOUS, None),
                ("gamma", "Gamma", R.STATUS_MISSING, None),
            ],
        )


# --------------------------------------------------------------- index diff

INDEX_README = """# demo

## The tools

| Tool | Tests | What it checks |
|------|------:|----------------|
| [`alpha`](alpha) | 12 | does a thing |
| [`ghost`](ghost) | 5 | no longer exists |

## Judgement calls, collected

text
"""


class TestIndexDiff(unittest.TestCase):
    def setUp(self):
        self.rows = R.parse_index(INDEX_README)

    def test_parse_index_reads_rows(self):
        self.assertEqual([r["tool"] for r in self.rows], ["alpha", "ghost"])
        self.assertEqual(self.rows[0]["tests"], 12)

    def test_missing_extra_and_aggregate(self):
        tools = [
            {"tool": "alpha", "title": "Alpha", "claimed_tests": 12, "status": R.STATUS_CLAIM},
            {"tool": "beta", "title": "Beta", "claimed_tests": 7, "status": R.STATUS_CLAIM},
        ]
        kinds = [d["kind"] for d in R.diff_index(self.rows, tools)]
        self.assertIn("missing_from_index", kinds)
        self.assertIn("extra_in_index", kinds)
        self.assertIn("aggregate_differs", kinds)

    def test_count_differs_is_reported(self):
        tools = [{"tool": "alpha", "title": "A", "claimed_tests": 99, "status": R.STATUS_CLAIM}]
        diffs = [d for d in R.diff_index(self.rows, tools) if d["kind"] == "count_differs"]
        self.assertEqual(diffs[0]["index"], 12)
        self.assertEqual(diffs[0]["derived"], 99)

    def test_index_number_against_underivable_claim(self):
        tools = [{"tool": "alpha", "title": "A", "claimed_tests": None, "status": R.STATUS_MISSING}]
        kinds = [d["kind"] for d in R.diff_index(self.rows, tools)]
        self.assertIn("count_not_derivable", kinds)

    def test_totals_only_count_derivable_claims(self):
        tools = [
            {"tool": "a", "claimed_tests": 5, "status": R.STATUS_CLAIM, "title": "A"},
            {"tool": "b", "claimed_tests": None, "status": R.STATUS_AMBIGUOUS, "title": "B"},
            {"tool": "c", "claimed_tests": None, "status": R.STATUS_MISSING, "title": "C"},
        ]
        t = R.compute_totals(tools)
        self.assertEqual(t["tests_from_claims"], 5)
        self.assertEqual(t["tools"], 3)
        self.assertEqual(t["tools_with_claim"], 1)
        self.assertEqual(t["tools_ambiguous"], 1)
        self.assertEqual(t["tools_missing"], 1)


class TestRegeneration(unittest.TestCase):
    def _tools(self):
        return [
            {"tool": "alpha", "title": "Alpha", "claimed_tests": 12, "status": R.STATUS_CLAIM},
            {"tool": "beta", "title": "Beta", "claimed_tests": None, "status": R.STATUS_AMBIGUOUS},
            {"tool": "gamma", "title": "Gamma", "claimed_tests": None, "status": R.STATUS_MISSING},
        ]

    def test_rewrite_is_idempotent(self):
        once = R.rewrite_index(INDEX_README, self._tools(), "## The tools", "## Judgement calls, collected")
        twice = R.rewrite_index(once, self._tools(), "## The tools", "## Judgement calls, collected")
        self.assertEqual(once, twice)

    def test_rewrite_preserves_surrounding_sections(self):
        out = R.rewrite_index(INDEX_README, self._tools(), "## The tools", "## Judgement calls, collected")
        self.assertTrue(out.startswith("# demo\n"))
        self.assertIn("## Judgement calls, collected", out)
        self.assertTrue(out.rstrip().endswith("text"))

    def test_ambiguous_and_missing_are_labelled_not_zeroed(self):
        out = R.rewrite_index(INDEX_README, self._tools(), "## The tools", "## Judgement calls, collected")
        self.assertIn("| ambiguous |", out)
        self.assertIn("| not stated |", out)
        self.assertNotIn("| 0 |", out)

    def test_missing_heading_is_setup_error(self):
        with self.assertRaises(R.SetupError):
            R.rewrite_index(INDEX_README, self._tools(), "## Nope", "## Judgement calls, collected")

    def test_missing_next_heading_is_setup_error(self):
        with self.assertRaises(R.SetupError):
            R.rewrite_index(INDEX_README, self._tools(), "## The tools", "## Nope")


# --------------------------------------------------------------- cli / errors

def run_cli(*args):
    proc = subprocess.run(
        [sys.executable, os.path.join(HERE, "readmeindex.py")] + list(args),
        capture_output=True, text=True, cwd=HERE,
    )
    return proc.returncode, proc.stdout, proc.stderr


class TestCliExitCodes(TempTreeMixin, unittest.TestCase):
    def test_bad_root_is_exit_2(self):
        code, _, err = run_cli("--root", os.path.join(HERE, "definitely_not_here_xyz"))
        self.assertEqual(code, 2)
        self.assertIn("setup error", err)

    def test_bad_corpus_is_exit_2(self):
        code, _, _ = run_cli("--corpus", os.path.join(HERE, "definitely_not_here_xyz.tsv"))
        self.assertEqual(code, 2)

    def test_malformed_corpus_is_exit_2(self):
        fd, path = tempfile.mkstemp(suffix=".tsv")
        os.close(fd)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("only_one_field\n")
        try:
            code, _, err = run_cli("--corpus", path)
        finally:
            os.unlink(path)
        self.assertEqual(code, 2)
        self.assertIn("4 tab-separated", err)

    def test_empty_root_is_exit_2(self):
        root = tempfile.mkdtemp(prefix="readmeindex_empty_")
        self._dirs.append(root)
        code, _, err = run_cli("--root", root)
        self.assertEqual(code, 2)
        self.assertIn("no tool directories", err)

    def test_rewrite_without_root_readme_is_exit_2(self):
        root = self.tree({"alpha": "# Alpha\n\n5 tests\n"})
        code, _, err = run_cli("--root", root, "--rewrite", os.path.join(root, "out.md"))
        self.assertEqual(code, 2)
        self.assertIn("--rewrite requires --root-readme", err)

    def test_clean_run_without_readme_is_exit_0(self):
        root = self.tree({"alpha": "# Alpha\n\n**5 tests, `OK`**\n"})
        code, out, _ = run_cli("--root", root)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["totals"]["tests_from_claims"], 5)

    def test_differences_are_exit_1(self):
        root = self.tree({"alpha": "# Alpha\n\n**5 tests, `OK`**\n"})
        readme = os.path.join(root, "ROOT.md")
        with open(readme, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(INDEX_README)
        code, _, _ = run_cli("--root", root, "--root-readme", readme)
        self.assertEqual(code, 1)

    def test_report_is_canonical_json(self):
        root = self.tree({"alpha": "# Alpha\n\n**5 tests, `OK`**\n"})
        _, out1, _ = run_cli("--root", root)
        _, out2, _ = run_cli("--root", root)
        self.assertEqual(out1, out2)
        self.assertTrue(out1.endswith("\n"))


class TestStdlibOnly(unittest.TestCase):
    def test_imports_are_stdlib(self):
        with open(os.path.join(HERE, "readmeindex.py"), encoding="utf-8") as fh:
            src = fh.read()
        imports = {
            line.split()[1].split(".")[0]
            for line in src.split("\n")
            if line.startswith("import ") or line.startswith("from ")
        }
        self.assertEqual(imports, {"argparse", "json", "os", "re", "sys"})


if __name__ == "__main__":
    unittest.main()
