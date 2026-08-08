"""Focused tests for readmeindex.py.

Run: python3 -m unittest test_readmeindex
"""

import contextlib
import errno
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
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
        # sorted(), not the set itself: on a mismatch unittest prints the
        # offending members in set-iteration order, which varies with
        # PYTHONHASHSEED. ATOMIC_WRITE_EVIDENCE.txt commits this test's
        # failure message from the pre-fix source, and a message that
        # reorders itself between runs makes that file irreproducible.
        self.assertEqual(
            sorted(imports),
            ["argparse", "json", "os", "re", "stat", "sys", "tempfile"])


# ------------------------------------------------------------ atomic writes
#
# The defect these cover: both output paths used to be written with a plain
# `open(path, "w")`, which truncates the destination before the first byte of
# the replacement is written. A write that failed part-way therefore left a
# prefix of the new output where the old output had been. `--rewrite` is
# aimed at a hand-maintained root README, so that is a destructive failure
# mode, not a cosmetic one.
#
# Every test below that states the atomicity guarantee is written so that it
# FAILS against the pre-fix source -- twenty-six of the twenty-eight. The
# other two pin the success path, which the pre-fix source already got
# right. ATOMIC_WRITE_EVIDENCE.txt records both runs.


class PartialWriteFailure(OSError):
    """The failure a full disk produces: some bytes landed, then ENOSPC."""


class _TruncatingHandle:
    """A write handle that commits a prefix to disk and then raises.

    Flushing the prefix before raising is the whole point. A proxy that
    swallowed the bytes would make the direct-write code look innocent,
    because nothing would be on disk to observe.
    """

    def __init__(self, handle, allow):
        self._handle = handle
        self._allow = allow

    def write(self, text):
        self._handle.write(text[: self._allow])
        self._handle.flush()
        raise PartialWriteFailure(errno.ENOSPC, "No space left on device")

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self._handle.close()
        return False


@contextlib.contextmanager
def failing_writes(allow=40):
    """Make every write-mode open() inside readmeindex fail after `allow` chars.

    Patched on the module rather than on builtins so the test cannot disturb
    unittest's own file handling, and so reads -- discovery, the root README
    -- keep working normally.
    """
    calls = []
    real_open = open

    def fake_open(file, mode="r", *args, **kwargs):
        handle = real_open(file, mode, *args, **kwargs)
        if "w" not in mode:
            return handle
        calls.append(file)
        return _TruncatingHandle(handle, allow)

    R.open = fake_open
    try:
        yield calls
    finally:
        del R.open


@contextlib.contextmanager
def interrupting_writes(allow=40):
    """Like failing_writes, but the write is interrupted rather than failing.

    KeyboardInterrupt does not inherit from Exception, so this is what tells
    `except BaseException` apart from `except Exception` in the helper.
    """
    calls = []
    real_open = open

    def fake_open(file, mode="r", *args, **kwargs):
        handle = real_open(file, mode, *args, **kwargs)
        if "w" not in mode:
            return handle
        calls.append(file)
        return _InterruptedHandle(handle, allow)

    R.open = fake_open
    try:
        yield calls
    finally:
        del R.open


class _InterruptedHandle(_TruncatingHandle):
    def write(self, text):
        self._handle.write(text[: self._allow])
        self._handle.flush()
        raise KeyboardInterrupt()


def drain(path, case):
    """Read a FIFO to completion so the writer is not left blocking."""
    with open(path, encoding="utf-8") as fh:
        case.drained = fh.read()


SENTINEL = "PREVIOUS OUTPUT -- MUST SURVIVE A FAILED WRITE\n" * 6


def read_bytes(path):
    with open(path, "rb") as fh:
        return fh.read()


def read_text(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def quiet_main(*argv):
    """main() with the report swallowed.

    Without -o the report goes to stdout, and a test that only cares
    about a file on disk should not print 40 lines of JSON into the
    suite output.
    """
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        return R.main(list(argv))


class AtomicWriteMixin(TempTreeMixin):
    def scratch(self):
        d = tempfile.mkdtemp(prefix="readmeindex_atomic_")
        self._dirs.append(d)
        return d

    def seeded(self, directory, name="out.md", text=SENTINEL):
        path = os.path.join(directory, name)
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        return path

    def direct_write_errno(self, path):
        """The errno the replaced `open(path, "w")` raises for this path.

        Comparing against this rather than a literal keeps these tests about
        parity with the code that was removed, which is the actual claim,
        and keeps them honest on a platform that picks a different errno.
        """
        try:
            with open(path, "w", encoding="utf-8", newline="\n"):
                pass
        except OSError as exc:
            return exc.errno
        self.fail("the direct write did not raise for %r" % path)

    def assertNoTempLitter(self, directory):
        strays = [n for n in os.listdir(directory) if n.startswith(".readmeindex-")]
        self.assertEqual(strays, [], "a temp file was left behind")


class TestWriteTextAtomically(AtomicWriteMixin, unittest.TestCase):
    def test_creates_a_new_file_with_exactly_the_text(self):
        d = self.scratch()
        path = os.path.join(d, "new.md")
        R.write_text_atomically(path, "hello\nthere\n")
        self.assertEqual(read_text(path), "hello\nthere\n")

    def test_overwrites_an_existing_file_completely(self):
        d = self.scratch()
        path = self.seeded(d)
        R.write_text_atomically(path, "short\n")
        self.assertEqual(read_text(path), "short\n")

    def test_failed_write_leaves_the_previous_bytes_byte_identical(self):
        d = self.scratch()
        path = self.seeded(d)
        before = read_bytes(path)
        with failing_writes() as calls:
            with self.assertRaises(PartialWriteFailure):
                R.write_text_atomically(path, "REPLACEMENT " * 50)
        self.assertTrue(calls, "the failure injector never fired")
        self.assertEqual(read_bytes(path), before)

    def test_failed_write_leaves_no_temp_file(self):
        d = self.scratch()
        path = self.seeded(d)
        with failing_writes() as calls:
            with self.assertRaises(PartialWriteFailure):
                R.write_text_atomically(path, "REPLACEMENT " * 50)
        self.assertTrue(calls, "the failure injector never fired")
        self.assertNoTempLitter(d)
        self.assertEqual(sorted(os.listdir(d)), ["out.md"])

    def test_failed_write_to_a_new_path_creates_nothing(self):
        d = self.scratch()
        path = os.path.join(d, "never.md")
        with failing_writes() as calls:
            with self.assertRaises(PartialWriteFailure):
                R.write_text_atomically(path, "REPLACEMENT " * 50)
        self.assertTrue(calls, "the failure injector never fired")
        self.assertFalse(os.path.exists(path))
        self.assertEqual(os.listdir(d), [])

    def test_successful_write_leaves_no_temp_file(self):
        d = self.scratch()
        path = self.seeded(d)
        R.write_text_atomically(path, "fine\n")
        self.assertEqual(sorted(os.listdir(d)), ["out.md"])

    def test_an_existing_destination_keeps_its_mode(self):
        # mkstemp creates 0600. Without the chmod step this is exactly how a
        # temp-file rewrite silently narrows a file's permissions.
        d = self.scratch()
        path = self.seeded(d)
        os.chmod(path, 0o644)
        R.write_text_atomically(path, "fine\n")
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o644)

    def test_an_unusual_existing_mode_is_preserved_too(self):
        d = self.scratch()
        path = self.seeded(d)
        os.chmod(path, 0o640)
        R.write_text_atomically(path, "fine\n")
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o640)

    def test_a_new_destination_gets_the_mode_plain_open_would_have_given(self):
        d = self.scratch()
        previous = os.umask(0o027)
        try:
            reference = os.path.join(d, "reference.md")
            with open(reference, "w", encoding="utf-8") as fh:
                fh.write("x")
            path = os.path.join(d, "atomic.md")
            R.write_text_atomically(path, "x")
        finally:
            os.umask(previous)
        self.assertEqual(
            stat.S_IMODE(os.stat(path).st_mode),
            stat.S_IMODE(os.stat(reference).st_mode),
        )
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o640)

    def test_a_symlinked_destination_is_written_through_not_replaced(self):
        # open(path, "w") follows a symlink. Replacing the link with a regular
        # file would be a behaviour change smuggled in with the fix.
        d = self.scratch()
        real = self.seeded(d, "real.md")
        link = os.path.join(d, "link.md")
        os.symlink(real, link)
        R.write_text_atomically(link, "through\n")
        self.assertTrue(os.path.islink(link))
        self.assertEqual(read_text(real), "through\n")

    def test_newline_translation_matches_the_direct_write(self):
        d = self.scratch()
        path = os.path.join(d, "nl.md")
        R.write_text_atomically(path, "a\nb\n")
        self.assertEqual(read_bytes(path), b"a\nb\n")

    def test_a_directory_destination_still_raises_like_a_direct_write(self):
        d = self.scratch()
        target = os.path.join(d, "subdir")
        os.makedirs(target)
        with self.assertRaises(IsADirectoryError):
            R.write_text_atomically(target, "x\n")
        self.assertTrue(os.path.isdir(target))
        self.assertNoTempLitter(d)

    def test_a_character_device_destination_is_written_through_not_replaced(self):
        # `-o /dev/null` is a real way to say "run it and discard the report".
        # os.replace onto a device node destroys the node, and as root it
        # would succeed. The device used here is a private clone in a temp
        # directory, so a regression damages the fixture and not /dev/null.
        d = self.scratch()
        node = os.path.join(d, "nulldev")
        try:
            os.mknod(node, 0o600 | stat.S_IFCHR, os.makedev(1, 3))
        except (OSError, AttributeError) as exc:
            self.skipTest("cannot create a character device here: %s" % exc)
        R.write_text_atomically(node, "discarded\n")
        self.assertTrue(stat.S_ISCHR(os.stat(node).st_mode))
        self.assertFalse(os.path.isfile(node))
        self.assertNoTempLitter(d)

    def test_a_keyboard_interrupt_still_removes_the_temp_file(self):
        # The reason the handler catches BaseException rather than Exception.
        # With `except Exception` this test is the only thing that goes red.
        d = self.scratch()
        path = self.seeded(d)
        before = read_bytes(path)
        with interrupting_writes() as calls:
            with self.assertRaises(KeyboardInterrupt):
                R.write_text_atomically(path, "REPLACEMENT " * 50)
        self.assertTrue(calls, "the failure injector never fired")
        self.assertEqual(read_bytes(path), before)
        self.assertNoTempLitter(d)
        self.assertEqual(sorted(os.listdir(d)), ["out.md"])

    def test_the_temp_file_is_a_sibling_of_the_destination(self):
        # os.replace is atomic only within one filesystem, so the temp file
        # has to live in the destination's own directory. Dropping `dir=`
        # would put it under the system temp directory and break that on any
        # machine where /tmp is a separate mount -- silently, and never on
        # this one, which is exactly why it needs pinning rather than trusting
        # a passing suite.
        d = self.scratch()
        path = self.seeded(d)
        seen = []
        real_mkstemp = R.tempfile.mkstemp

        def spy(*args, **kwargs):
            seen.append(kwargs.get("dir"))
            return real_mkstemp(*args, **kwargs)

        R.tempfile.mkstemp = spy
        try:
            R.write_text_atomically(path, "fine\n")
        finally:
            R.tempfile.mkstemp = real_mkstemp
        self.assertEqual(seen, [os.path.dirname(os.path.realpath(path))])

    def test_a_successful_write_leaks_no_file_descriptor(self):
        # mkstemp hands back an open fd. Forgetting to close it leaks one per
        # call, which no assertion about file contents would ever notice.
        d = self.scratch()
        path = self.seeded(d)
        R.write_text_atomically(path, "warm up\n")
        before = len(os.listdir("/proc/self/fd")) if os.path.isdir("/proc/self/fd") else None
        if before is None:
            self.skipTest("no /proc/self/fd on this platform")
        for _ in range(5):
            R.write_text_atomically(path, "again\n")
        self.assertEqual(len(os.listdir("/proc/self/fd")), before)

    def test_a_fifo_destination_is_written_through_not_replaced(self):
        # The /dev/stdout shape, without touching /dev/stdout. os.stat must be
        # asked about the path as given: os.path.realpath("/dev/stdout")
        # resolves to /proc/<pid>/fd/1 and then to a "pipe:[...]" name that
        # does not exist, so a guard placed after the resolution misses it and
        # tries to mkstemp inside a pipe.
        d = self.scratch()
        fifo = os.path.join(d, "pipe")
        try:
            os.mkfifo(fifo)
        except (OSError, AttributeError) as exc:
            self.skipTest("cannot create a FIFO here: %s" % exc)
        # daemon: if the code under test never opens the FIFO for writing --
        # which is exactly what a regression here looks like -- the reader
        # blocks on open() forever. A daemon thread lets the interpreter exit
        # and the assertion below report the failure, instead of hanging the
        # whole suite.
        self.drained = None
        reader = threading.Thread(target=drain, args=(fifo, self), daemon=True)
        reader.start()
        try:
            R.write_text_atomically(fifo, "through the pipe\n")
        finally:
            reader.join(10)
        self.assertTrue(stat.S_ISFIFO(os.stat(fifo).st_mode))
        self.assertEqual(self.drained, "through the pipe\n")
        self.assertNoTempLitter(d)

    def test_a_symlink_into_proc_fd_is_written_through(self):
        # The /dev/stdout shape, reproduced exactly and without touching
        # /dev/stdout: os.stat() through the link sees a pipe, while
        # os.path.realpath() invents a "pipe:[...]" name that does not exist.
        # A regular-file guard placed AFTER the resolution therefore sees a
        # phantom, decides the destination is new, and tries to mkstemp inside
        # a pipe. The FIFO test above does not catch that -- realpath on a
        # FIFO is the FIFO -- so this is the one that pins the ordering.
        if not os.path.isdir("/proc/self/fd"):
            self.skipTest("no /proc/self/fd on this platform")
        d = self.scratch()
        read_fd, write_fd = os.pipe()
        link = os.path.join(d, "stdout-like")
        os.symlink("/proc/self/fd/%d" % write_fd, link)
        self.assertTrue(stat.S_ISFIFO(os.stat(link).st_mode))
        self.assertFalse(os.path.exists(os.path.realpath(link)),
                         "premise: realpath must resolve to a phantom")
        got = []

        def read_all():
            with os.fdopen(read_fd, "r", encoding="utf-8") as fh:
                got.append(fh.read())

        reader = threading.Thread(target=read_all, daemon=True)
        reader.start()
        try:
            R.write_text_atomically(link, "down the pipe\n")
        finally:
            os.close(write_fd)
            reader.join(10)
        self.assertEqual(got, ["down the pipe\n"])
        self.assertNoTempLitter(d)

    def test_a_symlink_loop_raises_instead_of_being_replaced(self):
        # os.stat raises ELOOP, not ENOENT. Treating every OSError as "the
        # destination does not exist" sent this straight to mkstemp and
        # os.replace, which quietly turned the loop into a regular file.
        d = self.scratch()
        a, b = os.path.join(d, "a"), os.path.join(d, "b")
        os.symlink(b, a)
        os.symlink(a, b)
        with self.assertRaises(OSError) as caught:
            R.write_text_atomically(a, "should not land\n")
        self.assertEqual(caught.exception.errno, errno.ELOOP)
        self.assertTrue(os.path.islink(a))
        self.assertFalse(os.path.isfile(a))
        self.assertNoTempLitter(d)

    def test_a_trailing_slash_on_an_existing_file_still_raises(self):
        # os.stat("f.md/") raises ENOTDIR while realpath("f.md/") is "f.md",
        # so without the guards this wrote straight through the slash. The
        # assertion is parity with the direct write, not a hard-coded errno.
        d = self.scratch()
        path = self.seeded(d)
        before = read_bytes(path)
        with self.assertRaises(OSError) as caught:
            R.write_text_atomically(path + os.sep, "should not land\n")
        self.assertEqual(caught.exception.errno,
                         self.direct_write_errno(path + os.sep))
        self.assertEqual(read_bytes(path), before)
        self.assertNoTempLitter(d)

    def test_a_trailing_slash_on_a_new_path_still_raises(self):
        # Here os.stat raises ENOENT, so the classifier above cannot help and
        # only the explicit separator check stops realpath from silently
        # dropping the slash and writing a regular file.
        d = self.scratch()
        path = os.path.join(d, "new.md")
        with self.assertRaises(OSError) as caught:
            R.write_text_atomically(path + os.sep, "should not land\n")
        self.assertEqual(caught.exception.errno,
                         self.direct_write_errno(path + os.sep))
        self.assertFalse(os.path.exists(path))
        self.assertEqual(os.listdir(d), [])

    def test_non_ascii_round_trips_as_utf8(self):
        d = self.scratch()
        path = os.path.join(d, "u.md")
        R.write_text_atomically(path, "café\n")
        self.assertEqual(read_bytes(path), b"caf\xc3\xa9\n")


class TestDestinationMode(AtomicWriteMixin, unittest.TestCase):
    def test_existing_file_reports_its_own_bits(self):
        d = self.scratch()
        path = self.seeded(d)
        os.chmod(path, 0o604)
        self.assertEqual(R.destination_mode(path), 0o604)

    def test_missing_file_reports_the_umask_default(self):
        d = self.scratch()
        previous = os.umask(0o022)
        try:
            self.assertEqual(R.destination_mode(os.path.join(d, "nope")), 0o644)
        finally:
            os.umask(previous)

    def test_missing_file_under_a_tighter_umask(self):
        d = self.scratch()
        previous = os.umask(0o077)
        try:
            self.assertEqual(R.destination_mode(os.path.join(d, "nope")), 0o600)
        finally:
            os.umask(previous)

    def test_reading_the_umask_restores_it(self):
        previous = os.umask(0o007)
        try:
            R.destination_mode(os.path.join(self.scratch(), "nope"))
            restored = os.umask(0o022)
            self.assertEqual(restored, 0o007)
        finally:
            os.umask(previous)


class TestCliOutputsAreAtomic(AtomicWriteMixin, unittest.TestCase):
    """The same guarantee, exercised through main() rather than the helper."""

    def _root(self):
        root = self.tree({"alpha": "# Alpha\n\n**5 tests, `OK`**\n"})
        readme = os.path.join(root, "ROOT.md")
        with open(readme, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(INDEX_README)
        return root, readme

    def test_failed_rewrite_leaves_the_previous_readme_byte_identical(self):
        root, readme = self._root()
        d = self.scratch()
        out = self.seeded(d, "regenerated.md")
        before = read_bytes(out)
        with failing_writes() as calls:
            with self.assertRaises(PartialWriteFailure):
                quiet_main("--root", root, "--root-readme", readme, "--rewrite", out)
        self.assertTrue(calls, "the failure injector never fired")
        self.assertEqual(read_bytes(out), before)
        self.assertNoTempLitter(d)

    def test_failed_report_write_leaves_the_previous_report_byte_identical(self):
        root, readme = self._root()
        d = self.scratch()
        out = self.seeded(d, "report.json", '{"previous": true}\n')
        before = read_bytes(out)
        with failing_writes() as calls:
            with self.assertRaises(PartialWriteFailure):
                quiet_main("--root", root, "--root-readme", readme, "-o", out)
        self.assertTrue(calls, "the failure injector never fired")
        self.assertEqual(read_bytes(out), before)
        self.assertNoTempLitter(d)

    def test_rewrite_in_place_survives_a_failed_write(self):
        # The shape the tool is actually for: --rewrite aimed at the very file
        # --root-readme just read.
        root, readme = self._root()
        before = read_bytes(readme)
        with failing_writes() as calls:
            with self.assertRaises(PartialWriteFailure):
                quiet_main("--root", root, "--root-readme", readme, "--rewrite", readme)
        self.assertTrue(calls, "the failure injector never fired")
        self.assertEqual(read_bytes(readme), before)
        self.assertNoTempLitter(os.path.dirname(readme))

    def test_successful_rewrite_still_produces_the_regenerated_table(self):
        root, readme = self._root()
        d = self.scratch()
        out = self.seeded(d, "regenerated.md")
        quiet_main("--root", root, "--root-readme", readme, "--rewrite", out)
        text = read_text(out)
        self.assertIn("| [`alpha`](alpha) | 5 | Alpha |", text)
        self.assertEqual(sorted(os.listdir(d)), ["regenerated.md"])

    def test_successful_report_write_is_the_same_bytes_as_stdout(self):
        root, _ = self._root()
        d = self.scratch()
        out = os.path.join(d, "report.json")
        quiet_main("--root", root, "-o", out)
        code, stdout, _ = run_cli("--root", root)
        self.assertEqual(code, 0)
        self.assertEqual(read_text(out), stdout)


if __name__ == "__main__":
    unittest.main()
