#!/usr/bin/env python3
"""Tests for pipe_classify.py.

Three layers, deliberately separated:

1. **Grammar** -- ``classify_bars`` against hand-written strings whose
   correct answer is decidable by reading POSIX quoting rules.
2. **Live cross-check** -- ``pipe_classify.scan`` against the real
   repository tree, asserted to describe the SAME population as
   ``pipe_scan.scan`` (the tool it annotates).  If the two ever disagree
   on which records carry a ``|``, that is a bug in one of them and this
   suite fails rather than quietly reporting a prettier number.
3. **Executable ground truth** -- for a sample of classified commands,
   the classification is checked against what a real shell does, so the
   labels are not merely internally consistent.  A command labelled
   ``pipeline`` is one whose exit status ``set -o pipefail`` can change;
   a command labelled non-pipeline is one where it cannot.

Run:
    python3 -m unittest test_pipe_classify
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(THIS_DIR)
if THIS_DIR not in sys.path:
    sys.path.insert(0, THIS_DIR)

import pipe_classify as pc  # noqa: E402


def kinds(command):
    return [b["kind"] for b in pc.classify_bars(command)]


class TestUnquotedPipelines(unittest.TestCase):
    def test_simple_pipe(self):
        self.assertEqual(kinds("a | b"), [pc.PIPELINE])

    def test_pipe_without_spaces(self):
        self.assertEqual(kinds("a|b"), [pc.PIPELINE])

    def test_two_stage_pipeline(self):
        self.assertEqual(kinds("a | b | c"), [pc.PIPELINE, pc.PIPELINE])

    def test_is_shell_pipeline_true(self):
        self.assertTrue(pc.is_shell_pipeline("cat f | wc -l"))

    def test_pipe_at_end_is_still_a_pipeline_token(self):
        # Syntactically incomplete, but the character IS the operator.
        self.assertEqual(kinds("a |"), [pc.PIPELINE])

    def test_pipe_after_semicolon(self):
        self.assertEqual(kinds("x ; a | b"), [pc.PIPELINE])

    def test_pipe_inside_command_substitution_is_a_pipeline(self):
        self.assertEqual(kinds("echo $(a | b)"), [pc.PIPELINE])

    def test_pipe_inside_backticks_is_a_pipeline(self):
        self.assertEqual(kinds("echo `a | b`"), [pc.PIPELINE])

    def test_pipe_ampersand_is_a_pipeline(self):
        # bash `|&` pipes stdout AND stderr; the first char is the pipe.
        self.assertEqual(kinds("a |& b"), [pc.PIPELINE])

    def test_pipe_inside_double_quotes_within_substitution_is_quoted(self):
        self.assertEqual(kinds('echo $(grep "a|b" f)'), [pc.QUOTED])


class TestOrOperator(unittest.TestCase):
    def test_or_operator_both_chars_reported(self):
        self.assertEqual(kinds("a || b"), [pc.OR_OPERATOR, pc.OR_OPERATOR])

    def test_or_is_not_a_pipeline(self):
        self.assertFalse(pc.is_shell_pipeline("a || b"))

    def test_and_or_chain(self):
        self.assertEqual(
            kinds("test -f x && echo Y || echo N"),
            [pc.OR_OPERATOR, pc.OR_OPERATOR],
        )

    def test_or_then_pipe(self):
        self.assertEqual(
            kinds("a || b | c"),
            [pc.OR_OPERATOR, pc.OR_OPERATOR, pc.PIPELINE],
        )

    def test_three_bars_is_or_plus_pipe(self):
        # `a ||| b` tokenises as `||` then `|`.
        self.assertEqual(
            kinds("a ||| b"),
            [pc.OR_OPERATOR, pc.OR_OPERATOR, pc.PIPELINE],
        )

    def test_four_bars_is_two_ors(self):
        self.assertEqual(kinds("a |||| b"), [pc.OR_OPERATOR] * 4)


class TestSingleQuotes(unittest.TestCase):
    def test_bar_inside_single_quotes(self):
        self.assertEqual(kinds("grep 'a|b' f"), [pc.QUOTED])

    def test_escaped_bar_inside_single_quotes_is_quoted_not_escaped(self):
        # Backslash is NOT special inside single quotes, so BOTH the
        # backslash and the bar are literal; the bar is `quoted`.
        self.assertEqual(kinds(r"grep 'a\|b' f"), [pc.QUOTED])

    def test_backslash_cannot_close_a_single_quote(self):
        # The `\'` does not end the quote in POSIX sh; the bar stays quoted.
        self.assertEqual(kinds("grep 'a\\' | b"), [pc.PIPELINE])

    def test_pipe_after_closing_single_quote(self):
        self.assertEqual(kinds("grep 'a' | b"), [pc.PIPELINE])

    def test_two_single_quoted_regions(self):
        self.assertEqual(kinds("grep 'a|b' f | grep 'c|d'"),
                         [pc.QUOTED, pc.PIPELINE, pc.QUOTED])


class TestDoubleQuotes(unittest.TestCase):
    def test_bar_inside_double_quotes(self):
        self.assertEqual(kinds('grep "a|b" f'), [pc.QUOTED])

    def test_escaped_bar_inside_double_quotes_is_quoted(self):
        # In double quotes `\` only escapes $ ` " \ newline, so `\|` is
        # two literal chars -- the bar is quoted, not `escaped`.
        self.assertEqual(kinds(r'grep "a\|b" f'), [pc.QUOTED])

    def test_escaped_double_quote_does_not_close_the_string(self):
        self.assertEqual(kinds(r'echo "he said \"a|b\"" '), [pc.QUOTED])

    def test_escaped_backslash_then_close_quote(self):
        # `"a\\"` closes after the escaped backslash, so the bar is a pipe.
        self.assertEqual(kinds('echo "a\\\\" | b'), [pc.PIPELINE])

    def test_single_quote_inside_double_quotes_is_literal(self):
        self.assertEqual(kinds("""grep "it's a|b" f"""), [pc.QUOTED])

    def test_double_quote_inside_single_quotes_is_literal(self):
        self.assertEqual(kinds("""grep 'say "a|b"' f"""), [pc.QUOTED])


class TestEscapedOutsideQuotes(unittest.TestCase):
    def test_escaped_bar_unquoted(self):
        self.assertEqual(kinds(r"echo a\|b"), [pc.ESCAPED])

    def test_escaped_bar_is_not_a_pipeline(self):
        self.assertFalse(pc.is_shell_pipeline(r"echo a\|b"))

    def test_escaped_backslash_then_pipe(self):
        # `\\` consumes itself; the following `|` is a real pipe.
        self.assertEqual(kinds(r"echo a\\ | b"), [pc.PIPELINE])

    def test_trailing_lone_backslash_does_not_crash(self):
        self.assertEqual(kinds("echo a\\"), [])


class TestReconciliation(unittest.TestCase):
    SAMPLES = [
        "a | b",
        "a || b",
        r"grep 'a\|b' f",
        r'grep "a\|b" f',
        r"echo a\|b",
        "a ||| b",
        "echo $(a | b)",
        "grep 'a|b' f | grep \"c|d\" ",
        "no bars here",
        "",
    ]

    def test_every_bar_character_is_classified_exactly_once(self):
        for s in self.SAMPLES:
            with self.subTest(s=s):
                self.assertEqual(len(pc.classify_bars(s)), s.count("|"))

    def test_indices_are_strictly_increasing(self):
        for s in self.SAMPLES:
            with self.subTest(s=s):
                idx = [b["index"] for b in pc.classify_bars(s)]
                self.assertEqual(idx, sorted(set(idx)))

    def test_every_index_points_at_a_bar(self):
        for s in self.SAMPLES:
            with self.subTest(s=s):
                for b in pc.classify_bars(s):
                    self.assertEqual(s[b["index"]], "|")

    def test_every_kind_is_known(self):
        for s in self.SAMPLES:
            with self.subTest(s=s):
                for b in pc.classify_bars(s):
                    self.assertIn(b["kind"], pc.ALL_KINDS)

    def test_non_pipeline_kinds_are_the_complement_of_pipeline(self):
        self.assertEqual(
            sorted(pc.NON_PIPELINE_KINDS + (pc.PIPELINE,)),
            sorted(pc.ALL_KINDS),
        )

    def test_classify_command_counts_sum_to_bar_count(self):
        for s in self.SAMPLES:
            with self.subTest(s=s):
                info = pc.classify_command(s)
                self.assertEqual(sum(info["counts"].values()), info["bar_count"])

    def test_is_pipeline_agrees_with_counts(self):
        for s in self.SAMPLES:
            with self.subTest(s=s):
                info = pc.classify_command(s)
                self.assertEqual(info["is_pipeline"], info["counts"][pc.PIPELINE] > 0)

    def test_no_bars_means_empty_classification(self):
        self.assertEqual(pc.classify_bars("python3 -m unittest test_x"), [])


class TestAgreementWithPipeScan(unittest.TestCase):
    """pipe_classify must describe exactly the population pipe_scan counts."""

    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, THIS_DIR)
        import pipe_scan  # noqa: E402
        cls.pipe_scan = pipe_scan
        cls.scan_report = pipe_scan.scan(REPO_ROOT)
        cls.cls_report = pc.scan(REPO_ROOT)

    def test_same_transcript_file_count(self):
        self.assertEqual(self.cls_report["transcript_files_scanned"],
                         self.scan_report["transcript_files_scanned"])

    def test_same_total_command_records(self):
        self.assertEqual(self.cls_report["total_command_records"],
                         self.scan_report["total_command_records"])

    def test_same_bar_record_total(self):
        self.assertEqual(self.cls_report["total_records_with_a_bar"],
                         self.scan_report["total_piped_records"])

    def test_same_directory_set(self):
        self.assertEqual(
            sorted(t["tool"] for t in self.cls_report["tools"]),
            sorted(d["tool"] for d in self.scan_report["files_with_piped_records"]),
        )

    def test_same_per_directory_counts(self):
        mine = {t["tool"]: t["records_with_a_bar"] for t in self.cls_report["tools"]}
        theirs = {d["tool"]: d["piped_records"]
                  for d in self.scan_report["files_with_piped_records"]}
        self.assertEqual(mine, theirs)

    def test_pipeline_subset_is_not_larger_than_the_bar_population(self):
        self.assertLessEqual(self.cls_report["total_pipeline_records"],
                             self.cls_report["total_records_with_a_bar"])

    def test_split_is_exhaustive(self):
        r = self.cls_report
        self.assertEqual(
            r["total_pipeline_records"] + r["total_non_pipeline_bar_records"],
            r["total_records_with_a_bar"],
        )

    def test_the_over_count_is_real_and_nonzero(self):
        # The whole reason this file exists. If this ever reaches zero,
        # pipe_scan's raw number has become exact and this tool's
        # disclosure value has changed -- that should be a deliberate
        # edit, not a silent pass.
        self.assertGreater(self.cls_report["total_non_pipeline_bar_records"], 0)


class TestLiveRecordClassification(unittest.TestCase):
    """Assert the verdict for each real record, by shape rather than by
    a frozen count -- so a future transcript edit changes the population
    without silently invalidating the expectations."""

    @classmethod
    def setUpClass(cls):
        cls.report = pc.scan(REPO_ROOT)
        cls.records = [r for t in cls.report["tools"] for r in t["records"]]

    def test_there_is_at_least_one_record_of_each_verdict(self):
        self.assertTrue(any(r["is_pipeline"] for r in self.records))
        self.assertTrue(any(not r["is_pipeline"] for r in self.records))

    def test_every_grep_pattern_record_is_not_a_pipeline(self):
        # A record whose only bars are inside a quoted grep pattern.
        greps = [r for r in self.records
                 if r["command"].startswith("grep ") and r["counts"][pc.QUOTED]]
        self.assertTrue(greps, "expected at least one quoted-grep record")
        for r in greps:
            with self.subTest(cmd=r["command"]):
                self.assertFalse(r["is_pipeline"])

    def test_every_stdin_pipe_record_is_a_pipeline(self):
        # The `cat X | prog -` / `echo X | prog -` stdin-path records.
        stdin_pipes = [r for r in self.records
                       if r["command"].startswith(("cat ", "echo "))
                       and " | " in r["command"]]
        self.assertTrue(stdin_pipes, "expected at least one stdin-pipe record")
        for r in stdin_pipes:
            with self.subTest(cmd=r["command"]):
                self.assertTrue(r["is_pipeline"])

    def test_logical_or_record_is_not_a_pipeline(self):
        ors = [r for r in self.records if r["counts"][pc.OR_OPERATOR]]
        self.assertTrue(ors, "expected at least one `||` record")
        for r in ors:
            with self.subTest(cmd=r["command"]):
                self.assertFalse(r["is_pipeline"])

    def test_every_record_actually_contains_a_bar(self):
        for r in self.records:
            with self.subTest(cmd=r["command"]):
                self.assertIn("|", r["command"])

    def test_line_numbers_point_at_the_header(self):
        for t in self.report["tools"]:
            path = os.path.join(REPO_ROOT, t["tool"], "captured_output.txt")
            with open(path, encoding="utf-8", errors="replace") as fh:
                lines = fh.read().splitlines()
            for r in t["records"]:
                with self.subTest(tool=t["tool"], line=r["line"]):
                    self.assertEqual(lines[r["line"] - 1],
                                     "=== $ %s ===" % r["command"])


class TestShellGroundTruth(unittest.TestCase):
    """The labels are checked against a real shell, not just themselves.

    Definition used: a command is a pipeline iff there is an arrangement
    of it whose exit status differs between plain `bash -c` and
    `bash -c 'set -o pipefail; ...'`.  That is precisely the property the
    whole pipefail series is about.
    """

    def run_both(self, cmd):
        plain = subprocess.run(["bash", "-c", cmd],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        pf = subprocess.run(["bash", "-c", "set -o pipefail; " + cmd],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return plain.returncode, pf.returncode

    def test_bash_is_available(self):
        self.assertTrue(shutil.which("bash"), "these tests require bash")

    def test_failing_first_stage_masked_without_pipefail(self):
        plain, pf = self.run_both("false | true")
        self.assertEqual(plain, 0)      # masked
        self.assertNotEqual(pf, 0)      # unmasked
        self.assertTrue(pc.is_shell_pipeline("false | true"))

    def test_successful_pipeline_is_zero_either_way(self):
        plain, pf = self.run_both("true | true")
        self.assertEqual(plain, 0)
        self.assertEqual(pf, 0)
        self.assertTrue(pc.is_shell_pipeline("true | true"))

    def test_or_operator_status_is_unaffected_by_pipefail(self):
        cmd = "false || true"
        plain, pf = self.run_both(cmd)
        self.assertEqual(plain, 0)
        self.assertEqual(pf, 0)
        self.assertFalse(pc.is_shell_pipeline(cmd))

    def test_quoted_bar_is_one_process_and_unaffected(self):
        cmd = "grep -c 'a\\|b' /dev/null"
        plain, pf = self.run_both(cmd)
        self.assertEqual(plain, pf)
        self.assertFalse(pc.is_shell_pipeline(cmd))

    def test_escaped_bar_is_one_process_and_unaffected(self):
        cmd = r"echo a\|b"
        plain, pf = self.run_both(cmd)
        self.assertEqual(plain, 0)
        self.assertEqual(pf, 0)
        self.assertFalse(pc.is_shell_pipeline(cmd))

    def test_pipefail_changes_status_only_for_labelled_pipelines(self):
        cases = [
            "false | true",
            "false || true",
            r"echo a\|b",
            "grep -c 'x\\|y' /dev/null",
            "true | false",
        ]
        for cmd in cases:
            with self.subTest(cmd=cmd):
                plain, pf = self.run_both(cmd)
                if plain != pf:
                    self.assertTrue(
                        pc.is_shell_pipeline(cmd),
                        "pipefail changed the status of a command labelled "
                        "non-pipeline: %r" % cmd,
                    )


class TestDeterminismAndRelocation(unittest.TestCase):
    def test_scan_is_byte_identical_across_two_runs(self):
        a = pc.canonical_json(pc.scan(REPO_ROOT))
        b = pc.canonical_json(pc.scan(REPO_ROOT))
        self.assertEqual(a, b)

    def test_report_is_relocation_invariant(self):
        """Copy the tree to a differently-NAMED absolute path and rescan.

        A same-named copy would not prove much: the point is that no
        absolute path, directory name or cwd leaks into the report.
        """
        here = pc.canonical_json(pc.scan(REPO_ROOT))
        tmp = tempfile.mkdtemp(prefix="pipeclass_reloc_")
        try:
            dest = os.path.join(tmp, "a-different-tree-name")
            shutil.copytree(REPO_ROOT, dest,
                            ignore=shutil.ignore_patterns(".git"))
            there = pc.canonical_json(pc.scan(dest))
        finally:
            # Only ever remove the directory this test created itself.
            shutil.rmtree(tmp)
        self.assertEqual(here, there)

    #: Planted in the relocation path so a location leak is unmistakable.
    #: Chosen not to occur anywhere in the repository's own text.
    MARKER = "zqx-location-marker-7f3a"

    def test_report_leaks_no_trace_of_where_the_tree_lives(self):
        """Copy the tree under a uniquely-named path and rescan: the marker
        must not appear anywhere in the report.

        Why a planted marker instead of the obvious "assert `/tmp`,
        `/home`, `/root` are absent": that blunt version FAILS here, and
        correctly so -- five of the classified records are `grep`
        invocations whose *pattern argument* is literally
        `"/tmp\\|/home\\|/sessions"`. Those are transcript data being
        quoted back, not paths this process learned from its environment,
        and the only way to make such a check pass would be to stop
        reporting the command text -- damaging the evidence to please the
        checker. This repository has documented that use-versus-mention
        trap before; this is the same trap, avoided by asserting on a
        string that can only come from the location.
        """
        tmp = tempfile.mkdtemp(prefix="pipeclass_leak_")
        try:
            dest = os.path.join(tmp, self.MARKER)
            shutil.copytree(REPO_ROOT, dest,
                            ignore=shutil.ignore_patterns(".git"))
            text = pc.canonical_json(pc.scan(dest))
        finally:
            # Only ever remove the directory this test created itself.
            shutil.rmtree(tmp)
        self.assertNotIn(self.MARKER, text)
        self.assertNotIn(tmp, text)

    def test_the_marker_is_absent_from_the_tree_so_the_check_is_meaningful(self):
        """Negative control: if MARKER already occurred in the repository,
        the assertion above could pass (or fail) for unrelated reasons."""
        hits = []
        for dirpath, dirnames, filenames in os.walk(REPO_ROOT):
            dirnames[:] = [d for d in dirnames
                           if d not in (".git", "__pycache__")]
            for fn in filenames:
                p = os.path.join(dirpath, fn)
                try:
                    with open(p, encoding="utf-8", errors="ignore") as fh:
                        if self.MARKER in fh.read():
                            hits.append(os.path.relpath(p, REPO_ROOT))
                except OSError:
                    continue
        # This test file names the marker in a string literal; that is the
        # only permitted occurrence.
        self.assertEqual(hits, [os.path.join("index-generator",
                                             "test_pipe_classify.py")])


class TestCli(unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, os.path.join(THIS_DIR, "pipe_classify.py")] + list(args),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=THIS_DIR)

    def test_repo_root_scan_exits_zero_and_emits_json(self):
        p = self.run_cli("--repo-root", REPO_ROOT)
        self.assertEqual(p.returncode, 0)
        json.loads(p.stdout.decode())

    def test_bad_repo_root_exits_two(self):
        p = self.run_cli("--repo-root", os.path.join(THIS_DIR, "no_such_dir_xyz"))
        self.assertEqual(p.returncode, 2)
        self.assertIn(b"not a directory", p.stderr)

    def test_single_command_mode(self):
        p = self.run_cli("--command", "a | b")
        self.assertEqual(p.returncode, 0)
        obj = json.loads(p.stdout.decode())
        self.assertTrue(obj["is_pipeline"])

    def test_single_command_mode_on_a_quoted_bar(self):
        p = self.run_cli("--command", "grep -c 'a\\|b' f")
        obj = json.loads(p.stdout.decode())
        self.assertFalse(obj["is_pipeline"])

    def test_output_file_is_written_and_matches_stdout(self):
        tmp = tempfile.mkdtemp(prefix="pipeclass_cli_")
        try:
            out = os.path.join(tmp, "r.json")
            p1 = self.run_cli("--repo-root", REPO_ROOT, "-o", out)
            self.assertEqual(p1.returncode, 0)
            with open(out, encoding="utf-8") as fh:
                written = fh.read()
            p2 = self.run_cli("--repo-root", REPO_ROOT)
            self.assertEqual(written, p2.stdout.decode())
        finally:
            shutil.rmtree(tmp)

    def test_json_is_canonical(self):
        p = self.run_cli("--repo-root", REPO_ROOT)
        text = p.stdout.decode()
        self.assertEqual(text, pc.canonical_json(json.loads(text)))


class TestCommittedReportIsFresh(unittest.TestCase):
    REPORT = os.path.join(THIS_DIR, "pipe_classification_report.json")

    def test_committed_report_exists(self):
        self.assertTrue(os.path.isfile(self.REPORT))

    def test_committed_report_matches_a_live_rescan_byte_for_byte(self):
        with open(self.REPORT, encoding="utf-8") as fh:
            committed = fh.read()
        self.assertEqual(committed, pc.canonical_json(pc.scan(REPO_ROOT)))


if __name__ == "__main__":
    unittest.main()
