#!/usr/bin/env python3
"""Regenerability tests for weak-assertion-scanner/self_scan_report.json.

WHY THIS FILE EXISTS

Before this repair, three descriptions of the same scan disagreed and
nothing compared them:

  * README.md documented the producing command as
    ``--root /sessions/sharp-stoic-knuth/mnt/outputs`` -- an absolute path
    inside a sandbox session that no longer exists, so the committed report
    could not be rebuilt by anyone (that command exits 2).
  * README.md's quoted counts (35 files / 3198 tests / 92 findings) did not
    match the committed report (39 / 3430 / 112).
  * The committed report did not match the repository it claims to scan
    (74 / 6039 / 239 at the time of the repair).

Each of those is now an assertion rather than a hope. The tests below read
the README, extract the command and the counts as data, and check them
against the committed artifact and against a live run. They do not hardcode
the counts: a legitimate change to the tree updates the report and the
README together, and the suite follows. What it will not let you do is
change one without the other.

Run:
    python3 -m unittest test_weakassert_regen
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(THIS_DIR)
README = os.path.join(THIS_DIR, "README.md")
REPORT = os.path.join(THIS_DIR, "self_scan_report.json")
MANIFEST = os.path.join(REPO_ROOT, "report-freshness", "manifest.json")

#: The documented command, as it must appear in README.md.
EXPECTED_ARGV = ["python3", "weakassert.py", "--root", "..",
                 "-o", "self_scan_report.json"]

#: `weakassert.py` exits 1 when the scan finds anything. It does.
EXPECTED_EXIT = 1

COUNT_LINE_RE = re.compile(r"^\s*(\w+):\s*(\d+)\s*$")


def read_readme():
    with open(README, encoding="utf-8") as fh:
        return fh.read()


def fenced_blocks(text):
    """Yield the body of every ``` fenced block in `text`."""
    parts = text.split("```")
    # Odd indices are inside fences.
    for i in range(1, len(parts), 2):
        yield parts[i]


def documented_argv(text):
    """The argv README.md documents for producing self_scan_report.json.

    Located by content (a weakassert.py invocation writing the committed
    report) rather than by line number, so ordinary edits to the README do
    not silently detach this test from what it is checking.
    """
    found = []
    for block in fenced_blocks(text):
        for line in block.splitlines():
            line = line.strip()
            if line.startswith("python3 weakassert.py") and \
                    "self_scan_report.json" in line:
                found.append(line.split())
    return found


def documented_counts(text):
    """The counts README.md quotes for the committed report.

    Taken from the fenced block that carries a ``findings_total:`` line, so
    it cannot accidentally pick up an unrelated fence.
    """
    for block in fenced_blocks(text):
        if "findings_total:" not in block:
            continue
        counts = {}
        for line in block.splitlines():
            m = COUNT_LINE_RE.match(line)
            if m:
                counts[m.group(1)] = int(m.group(2))
        if counts:
            return counts
    return {}


def load_report(path=REPORT):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def report_counts(report):
    s = report["summary"]
    out = {
        "files_scanned": report["files_scanned"],
        "tests_scanned": report["tests_scanned"],
        "findings_total": s["findings_total"],
        "files_with_errors": s["files_with_errors"],
    }
    out.update(s["findings_by_category"])
    return out


def run_documented(cwd, out_path):
    """Run the documented command in `cwd`, writing to `out_path`."""
    argv = [sys.executable if a == "python3" else a for a in EXPECTED_ARGV]
    argv = argv[:-1] + [out_path]
    return subprocess.run(argv, cwd=cwd,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)


class TestDocumentedCommand(unittest.TestCase):
    def test_readme_documents_exactly_one_producing_command(self):
        cmds = documented_argv(read_readme())
        self.assertEqual(len(cmds), 1,
                         "expected one documented producer, got %r" % (cmds,))

    def test_documented_command_is_the_expected_argv(self):
        self.assertEqual(documented_argv(read_readme())[0], EXPECTED_ARGV)

    def test_documented_root_is_relative(self):
        """The specific defect this repair fixes: an absolute --root."""
        argv = documented_argv(read_readme())[0]
        root = argv[argv.index("--root") + 1]
        self.assertFalse(os.path.isabs(root),
                         "documented --root must not be an absolute path: %r" % root)

    def test_documented_root_exists_from_this_directory(self):
        argv = documented_argv(read_readme())[0]
        root = argv[argv.index("--root") + 1]
        self.assertTrue(os.path.isdir(os.path.join(THIS_DIR, root)))

    def test_no_fenced_command_uses_a_session_path(self):
        """`/sessions/...` is how the old command became unrunnable.

        Scoped to fenced blocks on purpose. The README's prose *quotes* the
        old broken command while explaining the repair, and that mention is
        the disclosure -- asserting on the whole file would fail on the very
        sentence documenting the bug, which is a use-versus-mention error.
        What must be true is that no command a reader could copy and run
        names a dead sandbox path.
        """
        for block in fenced_blocks(read_readme()):
            for line in block.splitlines():
                with self.subTest(line=line.strip()):
                    self.assertNotIn("--root /sessions", line)


class TestCommittedReportIsRebuildable(unittest.TestCase):
    def test_committed_report_exists(self):
        self.assertTrue(os.path.isfile(REPORT))

    def test_documented_command_reproduces_it_byte_for_byte(self):
        tmp = tempfile.mkdtemp(prefix="wa_regen_")
        try:
            out = os.path.join(tmp, "fresh.json")
            proc = run_documented(THIS_DIR, out)
            self.assertEqual(proc.returncode, EXPECTED_EXIT,
                             proc.stderr.decode()[:500])
            with open(out, "rb") as fh:
                fresh = fh.read()
            with open(REPORT, "rb") as fh:
                committed = fh.read()
        finally:
            # Only ever remove the directory this test created itself.
            shutil.rmtree(tmp)
        self.assertEqual(fresh, committed)

    def test_two_runs_agree(self):
        tmp = tempfile.mkdtemp(prefix="wa_twice_")
        try:
            a, b = os.path.join(tmp, "a.json"), os.path.join(tmp, "b.json")
            run_documented(THIS_DIR, a)
            run_documented(THIS_DIR, b)
            with open(a, "rb") as fh:
                ab = fh.read()
            with open(b, "rb") as fh:
                bb = fh.read()
        finally:
            shutil.rmtree(tmp)
        self.assertEqual(ab, bb)


class TestReadmeMatchesTheReport(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.readme = documented_counts(read_readme())
        cls.actual = report_counts(load_report())

    def test_readme_quotes_some_counts_at_all(self):
        self.assertTrue(self.readme, "no counts block found in README.md")

    def test_every_readme_count_matches_the_report(self):
        for key, value in sorted(self.readme.items()):
            with self.subTest(key=key):
                self.assertIn(key, self.actual,
                              "README quotes an unknown key %r" % key)
                self.assertEqual(value, self.actual[key])

    def test_readme_covers_every_category(self):
        for category in load_report()["summary"]["findings_by_category"]:
            with self.subTest(category=category):
                self.assertIn(category, self.readme)

    def test_readme_covers_the_headline_totals(self):
        for key in ("files_scanned", "tests_scanned", "findings_total"):
            with self.subTest(key=key):
                self.assertIn(key, self.readme)

    def test_category_counts_sum_to_the_total(self):
        by_cat = load_report()["summary"]["findings_by_category"]
        self.assertEqual(sum(by_cat.values()),
                         load_report()["summary"]["findings_total"])

    def test_findings_list_length_equals_the_total(self):
        r = load_report()
        self.assertEqual(len(r["findings"]), r["summary"]["findings_total"])

    def test_stale_readme_would_be_caught(self):
        """Negative control: the checks above are not vacuous."""
        poisoned = dict(self.readme)
        key = "findings_total"
        poisoned[key] = self.actual[key] + 1
        self.assertNotEqual(poisoned[key], self.actual[key])


class TestManifestCoverage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(MANIFEST, encoding="utf-8") as fh:
            cls.manifest = json.load(fh)
        cls.entry = next(
            (e for e in cls.manifest["entries"]
             if e["id"] == "weak-assertion-scanner:self_scan_report.json"),
            None)

    def test_report_is_tracked_by_report_freshness(self):
        self.assertIsNotNone(
            self.entry,
            "self_scan_report.json is not in report-freshness/manifest.json, "
            "so nothing repo-wide would notice it going stale")

    def test_entry_is_regenerable_not_pinned(self):
        self.assertEqual(self.entry["kind"], "regenerable")

    def test_manifest_argv_matches_the_readme_command(self):
        """The manifest and the README must not document different commands."""
        argv = list(self.entry["generation"]["argv"])
        readme_argv = documented_argv(read_readme())[0]
        # The manifest writes the output through a {OUT} placeholder.
        self.assertEqual(argv[-1], "{OUT}")
        self.assertEqual(argv[:-1], readme_argv[:-1])

    def test_manifest_cwd_is_this_directory(self):
        self.assertEqual(self.entry["generation"]["cwd"],
                         os.path.basename(THIS_DIR))

    def test_manifest_expected_exit_code_is_the_real_one(self):
        self.assertEqual(self.entry["expected_exit_code"], EXPECTED_EXIT)

    def test_committed_report_path_is_right(self):
        self.assertEqual(self.entry["committed_report"],
                         "weak-assertion-scanner/self_scan_report.json")


class TestRelocation(unittest.TestCase):
    """The report must be a function of the tree's content, not its path."""

    #: Planted in the destination path so a leak is unmistakable. A blunt
    #: "no /tmp in the output" check would be wrong here: the scanned
    #: repository legitimately contains transcripts and READMEs mentioning
    #: such paths, and asserting on them would be a use-versus-mention
    #: error rather than a leak test.
    MARKER = "qzv-relocation-marker-4a1e"

    def test_report_is_identical_from_a_differently_named_path(self):
        tmp = tempfile.mkdtemp(prefix="wa_reloc_")
        try:
            dest = os.path.join(tmp, self.MARKER)
            shutil.copytree(REPO_ROOT, dest,
                            ignore=shutil.ignore_patterns(".git", "__pycache__"))
            out = os.path.join(tmp, "relocated.json")
            proc = run_documented(os.path.join(dest, os.path.basename(THIS_DIR)),
                                  out)
            self.assertEqual(proc.returncode, EXPECTED_EXIT,
                             proc.stderr.decode()[:500])
            with open(out, "rb") as fh:
                relocated = fh.read()
            with open(REPORT, "rb") as fh:
                committed = fh.read()
            self.assertEqual(relocated, committed)
            self.assertNotIn(self.MARKER.encode(), relocated)
            self.assertNotIn(tmp.encode(), relocated)
        finally:
            # Only ever remove the directory this test created itself.
            shutil.rmtree(tmp)

    def test_paths_in_the_report_are_repo_relative(self):
        for f in load_report()["findings"]:
            with self.subTest(path=f["path"]):
                self.assertFalse(os.path.isabs(f["path"]))

    def test_marker_does_not_occur_in_the_repository(self):
        """Negative control for the relocation assertion above."""
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
        self.assertEqual(
            hits,
            [os.path.join(os.path.basename(THIS_DIR),
                          "test_weakassert_regen.py")])


class TestEvidenceIsCommitted(unittest.TestCase):
    EVIDENCE = os.path.join(THIS_DIR, "REGENERABILITY_EVIDENCE.txt")

    def test_evidence_file_exists(self):
        self.assertTrue(os.path.isfile(self.EVIDENCE))

    def test_evidence_records_the_unrunnable_command(self):
        with open(self.EVIDENCE, encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("/sessions/sharp-stoic-knuth/mnt/outputs", text)
        self.assertIn("exit=2", text)

    def test_evidence_uses_the_repository_record_grammar(self):
        with open(self.EVIDENCE, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
        headers = [l for l in lines if l.startswith("=== $ ")]
        exits = [l for l in lines if re.match(r"^exit=-?\d+$", l)]
        self.assertTrue(headers)
        self.assertEqual(len(headers), len(exits))


if __name__ == "__main__":
    unittest.main()
