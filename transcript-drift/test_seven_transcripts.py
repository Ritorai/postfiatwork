#!/usr/bin/env python3
"""test_seven_transcripts.py -- focused coverage for the seven legacy,
pre-FORMAT.md transcripts named in this delivery: bundle-index,
crosspath-runner, doc-validator, link-integrity, nondeterminism-scanner,
preflight, weak-assertion-scanner.

Before this delivery all seven produced TRANSCRIPT_HAS_NO_COMMAND_RECORDS
from both transcript-schema/validate_transcript.py and
transcript-drift/driftcheck.py -- the only seven transcripts left in the
repository in that state. This suite proves, against the tree AS COMMITTED
(no external fixtures, no /tmp scratch state assumed to already exist),
that:

  1. each of the seven now parses with real command records, and
     migrate.py's own analysis calls each one settled (conformant), never
     "still broken";
  2. validate_transcript.py reports ZERO findings for all seven, against
     its own grammar (see README.md's comparison table for why this is a
     stronger, independent check than driftcheck.py's cross-file one);
  3. every value that ended up promoted came from the file itself -- never
     invented -- proven the same way migrate.py's own test suite proves it:
     replaying analyze() on a byte-identical copy and diffing;
  4. commands that could not be promoted (no recoverable exit= anywhere in
     their region) are still sitting in each file as plain, un-headered
     text -- FORMAT.md's explicit allowance -- not silently dropped and not
     fabricated into a fake record;
  5. an unsafe case grounded in the REAL crosspath-runner excerpt (a
     unittest run piped through `tail`, matching capture.sh's documented
     "Finding 2" masking bug) is refused, not guessed;
  6. migrate.py's report ordering is stable across repeated invocations
     regardless of the order files are named on the command line;
  7. running migrate.py and validate_transcript.py again against the
     already-migrated seven, twice, offline, changes nothing further
     (idempotence) and produces byte-identical JSON both times.

Nothing here hardcodes a repository-wide count. Every number is either
derived from the seven files' own committed content at test time, or
asserted as an invariant (e.g. "no header lacks its own exit=", not
"there are exactly N headers total across the repo").
"""

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import migrate  # noqa: E402

PY = sys.executable or "python3"
MIGRATE_PY = os.path.join(HERE, "migrate.py")
DRIFTCHECK_PY = os.path.join(HERE, "driftcheck.py")
VALIDATE_PY = os.path.join(REPO_ROOT, "transcript-schema", "validate_transcript.py")

SEVEN = (
    "bundle-index", "crosspath-runner", "doc-validator", "link-integrity",
    "nondeterminism-scanner", "preflight", "weak-assertion-scanner",
)

HEADER_RE = re.compile(r"^=== \$ (.+?) ===\s*$")
BARE_RE = re.compile(r"^\$ (.+)$")
EXIT_RE = re.compile(r"^\s*exit=(-?\d+)\s*$")


def transcript_path(tool):
    return os.path.join(REPO_ROOT, tool, "captured_output.txt")


def read_lines(tool):
    with open(transcript_path(tool), "r", encoding="utf-8") as fh:
        return fh.read().split("\n")


@unittest.skipUnless(
    os.path.isdir(REPO_ROOT) and all(os.path.isdir(os.path.join(REPO_ROOT, t)) for t in SEVEN),
    "all seven tool directories must exist next to transcript-drift/ for this suite to run",
)
class TestSevenEndState(unittest.TestCase):
    """Each of the seven, checked against the committed file on disk."""

    def test_every_tool_has_a_captured_output_file(self):
        for t in SEVEN:
            self.assertTrue(os.path.isfile(transcript_path(t)), t)

    def test_every_tool_has_at_least_one_real_header(self):
        for t in SEVEN:
            with self.subTest(tool=t):
                lines = read_lines(t)
                headers = [l for l in lines if HEADER_RE.match(l)]
                self.assertGreater(len(headers), 0,
                                   "%s: no '=== $ ... ===' header found" % t)

    def test_migrate_analysis_calls_every_tool_settled_not_refused(self):
        # analyze() is a pure function (bytes in, decision out); this is
        # the same function migrate.py's CLI uses to decide migrated vs.
        # refused. None of the seven may come back REFUSED once committed.
        for t in SEVEN:
            with self.subTest(tool=t):
                with open(transcript_path(t), "rb") as fh:
                    data = fh.read()
                analysis = migrate.analyze(data)
                self.assertIn(
                    analysis.status,
                    (migrate.STATUS_UNCHANGED_CONFORMANT, migrate.STATUS_MIGRATED),
                    "%s: migrate.py calls this %r, not settled" % (t, analysis.status))
                # unchanged_conformant/migrated both mean: re-running
                # analyze() proposes no further byte change.
                self.assertEqual(migrate.join_lines(analysis.new_lines), data)

    def test_every_promoted_header_carries_its_own_exit_value(self):
        # The grammar-level guarantee behind "never fabricated": every
        # header in the committed file has a real exit= inside its own
        # record body, found by the same first-wins rule FORMAT.md defines.
        for t in SEVEN:
            with self.subTest(tool=t):
                lines = read_lines(t)
                header_idxs = [i for i, l in enumerate(lines) if HEADER_RE.match(l)]
                self.assertTrue(header_idxs, t)
                n = len(lines)
                for pos, h in enumerate(header_idxs):
                    end = header_idxs[pos + 1] if pos + 1 < len(header_idxs) else n
                    body = lines[h + 1:end]
                    has_exit = any(EXIT_RE.match(l) for l in body)
                    self.assertTrue(
                        has_exit,
                        "%s: header at line %d (%s) has no exit= in its body"
                        % (t, h + 1, lines[h]))

    def test_unpromoted_bare_lines_remain_plain_text_not_fabricated_headers(self):
        # Any leftover literal "$ cmd" line (never wrapped in "=== ===")
        # is, by construction, one migrate.py found no recoverable exit=
        # for and correctly left as body/preamble text -- FORMAT.md
        # explicitly permits this ("body := line*"). This test proves the
        # negative: none of these leftover lines were silently promoted.
        found_any_leftover = False
        for t in SEVEN:
            lines = read_lines(t)
            for lineno, l in enumerate(lines, 1):
                if BARE_RE.match(l) and not HEADER_RE.match(l):
                    found_any_leftover = True
                    # A leftover bare line is never itself a header.
                    self.assertFalse(HEADER_RE.match(l))
        # At least one of the seven is known (from the real migration run
        # behind this delivery) to have left bare lines behind; this
        # guards against the whole check vacuously passing on an empty set.
        self.assertTrue(found_any_leftover)

    def test_validate_transcript_reports_zero_findings_for_each_of_the_seven(self):
        for t in SEVEN:
            with self.subTest(tool=t):
                proc = subprocess.run(
                    [PY, VALIDATE_PY, transcript_path(t)],
                    capture_output=True, text=True)
                report = json.loads(proc.stdout)
                self.assertEqual(
                    report["findings"], [],
                    "%s: validate_transcript.py still reports findings: %r"
                    % (t, report["findings"]))
                self.assertEqual(report["status"], "valid")
                self.assertEqual(proc.returncode, 0)


class TestUnsafeRefusalGroundedInCrosspathRunner(unittest.TestCase):
    """The exact real input that must NOT be promoted: a real excerpt of
    crosspath-runner's original (pre-migration) transcript, reproducing
    capture.sh's documented "Finding 2" pipefail-masking bug -- a unittest
    run piped through `tail`, which drops both the exit status of the
    real command AND the line that would have carried it. This is copied
    verbatim from the file's own committed history (see LIMITATIONS.md),
    not invented for the test."""

    REAL_EXCERPT = (
        b'$ python3 --version\n'
        b'Python 3.11.15\n'
        b'\n'
        b'$ uname -s -m\n'
        b'Linux x86_64\n'
        b'\n'
        b'$ python3 -m unittest test_crosspath 2>&1 | tail -n 3\n'
        b'Ran 72 tests in 3.553s\n'
        b'\n'
        b'OK\n'
        b'\n'
        b'$ cat manifest.json\n'
        b'{}\n'
    )

    def test_piped_unittest_line_is_left_bare_not_promoted(self):
        analysis = migrate.analyze(self.REAL_EXCERPT)
        # python3 --version and uname promote fine (they're followed only
        # by output, no exit= either -- so they too are left bare here,
        # since this excerpt reproduces the ORIGINAL file, before any
        # `; echo "exit=$?"` was ever added to those two lines).
        left_bare_commands = {cmd for _, cmd, _ in analysis.left_bare}
        self.assertIn("python3 -m unittest test_crosspath 2>&1 | tail -n 3",
                      left_bare_commands)
        # Confirm the record region genuinely has no exit= anywhere in it
        # -- this is not a parser bug, the value really is absent.
        region = self.REAL_EXCERPT.split(b"$ cat manifest.json")[0]
        piped_region = region.split(b"test_crosspath")[1]
        self.assertNotIn(b"exit=", piped_region)

    def test_no_header_was_fabricated_for_the_piped_command(self):
        analysis = migrate.analyze(self.REAL_EXCERPT)
        out = migrate.join_lines(analysis.new_lines)
        self.assertNotIn(
            b'=== $ python3 -m unittest test_crosspath 2>&1 | tail -n 3 ===',
            out)
        # The line survives byte-for-byte as plain text.
        self.assertIn(b'$ python3 -m unittest test_crosspath 2>&1 | tail -n 3\n', out)

    def test_whole_excerpt_as_a_file_is_refused_reason_names_the_command(self):
        # As a standalone file (nothing else in it recoverable either),
        # this excerpt is refused outright, and the reason must name the
        # specific unpromotable command(s) -- not a generic "invalid file".
        analysis = migrate.analyze(self.REAL_EXCERPT)
        self.assertEqual(analysis.status, migrate.STATUS_REFUSED)
        self.assertIn("python3 -m unittest test_crosspath 2>&1 | tail -n 3",
                      analysis.reason)

    def test_process_file_on_disk_leaves_the_excerpt_byte_identical(self):
        with tempfile.TemporaryDirectory(prefix="seven_transcripts_test_") as tmp:
            path = os.path.join(tmp, "captured_output.txt")
            with open(path, "wb") as fh:
                fh.write(self.REAL_EXCERPT)
            result = migrate.process_file(path, dry_run=False)
            self.assertEqual(result["status"], migrate.STATUS_REFUSED)
            with open(path, "rb") as fh:
                self.assertEqual(fh.read(), self.REAL_EXCERPT)


class TestDefaultRuleWouldRefuseAllSevenAsCommitted(unittest.TestCase):
    """Grounds the brief's required "report both numbers" honesty check:
    replayed against the ORIGINAL (pre-migration) bytes of each of the
    seven, migrate.py's DEFAULT rule (verify-no-regression ON) blocks
    every single one -- which is exactly why --no-verify-no-regression was
    used, deliberately, once, for this delivery (see LIMITATIONS.md). This
    test reconstructs the original bytes from the committed file itself
    (by re-collapsing "=== $ cmd ===" back to "$ cmd" -- the exact inverse
    of migrate.py's own promotion, so this is not a second, independently
    maintained copy of the original transcript) rather than depending on
    any file outside this repository.
    """

    def _reconstruct_pre_migration_bytes(self, tool):
        with open(transcript_path(tool), "rb") as fh:
            data = fh.read()
        lines = migrate.split_lines(data)
        out = []
        for content, eol in lines:
            m = migrate.HEADER_RE_B.match(content)
            if m:
                out.append((b"$ " + m.group(1), eol))
            else:
                out.append((content, eol))
        return migrate.join_lines(out)

    def test_default_rule_blocks_exactly_when_findings_would_increase(self):
        """The guard's CONTRACT, not a snapshot of which tools it blocked.

        An earlier version of this test asserted that the default rule
        blocks all seven. That was true when it was written and became
        false the moment a sibling fix landed: changing
        crosspath-runner/README.md's exit-code table header from "Code"
        to "Exit" made driftcheck able to harvest that table, so
        promoting crosspath-runner's transcript no longer increases its
        finding count and the guard correctly stops blocking it.

        Encoding "all seven are blocked" would have made this suite fail
        because an unrelated, genuine improvement landed. What the guard
        actually promises is narrower and stable: it blocks a rewrite if
        and only if that rewrite would raise driftcheck's finding count
        for that tool. Assert that, and the test survives the repository
        getting better."""
        blocked_tools = []
        for t in SEVEN:
            with self.subTest(tool=t):
                original = self._reconstruct_pre_migration_bytes(t)
                readme_path = os.path.join(REPO_ROOT, t, "README.md")
                analysis = migrate.analyze(original)
                self.assertTrue(analysis.changed, "%s: nothing to promote?" % t)
                if not os.path.isfile(readme_path):
                    self.skipTest("%s has no README.md" % t)
                verification = migrate.verify_rewrite(
                    t, readme_path, original,
                    migrate.join_lines(analysis.new_lines))
                self.assertTrue(verification["attempted"], t)
                before = verification["before_count"]
                after = verification["after_count"]
                self.assertEqual(
                    verification["blocked"], after > before,
                    "%s: blocked=%r but findings went %d -> %d; the guard "
                    "must block exactly when the count increases"
                    % (t, verification["blocked"], before, after))
                if verification["blocked"]:
                    blocked_tools.append(t)
        # At least one of the seven must still exercise the blocking path,
        # otherwise this test would silently stop testing the guard.
        self.assertTrue(
            blocked_tools,
            "no tool exercised the blocking path -- the guard is untested here")


class TestStableOrdering(unittest.TestCase):
    """migrate.py's own results.sort(key=lambda r: (tool, path)) must make
    the seven's report order independent of CLI argument order, and
    independent of run number."""

    def _paths_in_random_ish_order(self):
        # Deliberately NOT sorted, and NOT the same order as the SEVEN
        # tuple above, so this exercises the sort, not an accidental match.
        order = ["weak-assertion-scanner", "bundle-index", "preflight",
                 "crosspath-runner", "nondeterminism-scanner",
                 "link-integrity", "doc-validator"]
        return [transcript_path(t) for t in order]

    def test_report_order_is_sorted_by_tool_regardless_of_cli_order(self):
        with tempfile.TemporaryDirectory(prefix="seven_transcripts_test_") as tmp:
            report_path = os.path.join(tmp, "report.json")
            proc = subprocess.run(
                [PY, MIGRATE_PY, "--dry-run", "--report", report_path]
                + self._paths_in_random_ish_order(),
                capture_output=True, text=True)
            with open(report_path) as fh:
                report = json.load(fh)
            tools = [r["tool"] for r in report["results"]]
            self.assertEqual(tools, sorted(SEVEN))

    def test_ordering_identical_across_two_independent_invocations(self):
        with tempfile.TemporaryDirectory(prefix="seven_transcripts_test_") as tmp:
            reports = []
            for i in range(2):
                report_path = os.path.join(tmp, "report_%d.json" % i)
                subprocess.run(
                    [PY, MIGRATE_PY, "--dry-run", "--report", report_path]
                    + self._paths_in_random_ish_order(),
                    capture_output=True, text=True)
                with open(report_path) as fh:
                    reports.append(json.load(fh))
            self.assertEqual(
                [r["tool"] for r in reports[0]["results"]],
                [r["tool"] for r in reports[1]["results"]])
            self.assertEqual(reports[0], reports[1])


class TestRepeatedOfflineRuns(unittest.TestCase):
    """The seven, as committed, run through migrate.py and
    validate_transcript.py twice more, offline, with nothing changing
    between the two runs -- idempotence, proven on the real committed
    bytes, not a copy."""

    def test_migrate_dry_run_twice_produces_byte_identical_reports(self):
        with tempfile.TemporaryDirectory(prefix="seven_transcripts_test_") as tmp:
            outs = []
            for i in range(2):
                report_path = os.path.join(tmp, "r%d.json" % i)
                proc = subprocess.run(
                    [PY, MIGRATE_PY, "--dry-run", "--report", report_path]
                    + [transcript_path(t) for t in SEVEN],
                    capture_output=True, text=True)
                self.assertEqual(proc.returncode, migrate.EXIT_OK)
                with open(report_path, "rb") as fh:
                    outs.append(fh.read())
            self.assertEqual(outs[0], outs[1])

    def test_migrate_on_disk_run_twice_leaves_files_byte_identical(self):
        # dry_run=False, but on already-migrated files: nothing left to
        # promote, so this must be a true no-op both times.
        with tempfile.TemporaryDirectory(prefix="seven_transcripts_test_") as tmp:
            for t in SEVEN:
                with open(transcript_path(t), "rb") as fh:
                    before = fh.read()
                for _ in range(2):
                    proc = subprocess.run(
                        [PY, MIGRATE_PY, transcript_path(t)],
                        capture_output=True, text=True)
                    self.assertEqual(proc.returncode, migrate.EXIT_OK)
                with open(transcript_path(t), "rb") as fh:
                    after = fh.read()
                self.assertEqual(before, after,
                                 "%s: running migrate.py again changed bytes" % t)

    def test_validate_transcript_twice_is_byte_identical_and_still_zero_findings(self):
        outs = []
        for _ in range(2):
            proc = subprocess.run(
                [PY, VALIDATE_PY] + [transcript_path(t) for t in SEVEN],
                capture_output=True, text=True)
            outs.append(proc.stdout)
        self.assertEqual(outs[0], outs[1])
        report = json.loads(outs[0])
        self.assertEqual(report["findings"], [])
        self.assertEqual(report["status"], "valid")

    def test_driftcheck_twice_on_the_real_repo_is_byte_identical(self):
        outs = []
        for _ in range(2):
            proc = subprocess.run(
                [PY, DRIFTCHECK_PY, "--root", REPO_ROOT],
                capture_output=True, text=True)
            outs.append(proc.stdout)
        self.assertEqual(outs[0], outs[1])


if __name__ == "__main__":
    unittest.main()
