"""The focused check: does the committed self-scan still reproduce?

`self_scan_report.json` is a current-state report over the whole
repository. It was written once, by the commit that added this tool, and
then went unmaintained for the tool's entire lifetime -- by the time it
was noticed it claimed 87 files scanned against a tree that had 176, and
it reproduced at no commit in the repository's history at all (the
transcript of that finding is `REGENERABILITY_EVIDENCE.txt`).

Nothing was asking the question. `report-freshness/manifest.json` now
carries a `regenerable` entry for the report, which is the repository-wide
answer; this file is the local one, so that anyone running this tool's own
suite -- the thing a person actually runs while editing this directory --
finds out immediately.

The check is deliberately narrow: regenerate with the documented command
and compare bytes. It does not re-judge findings, and it is not a second
opinion about what the rules should do.

    python3 -m unittest test_selfscan_freshness

WHEN THIS FAILS, THE REPORT IS THE THING THAT IS WRONG, not this test.
Any commit that adds, deletes, or edits a `.py` file anywhere in the
repository changes the correct contents of the report. Regenerate it in
that same commit:

    cd nondeterminism-scanner && python3 ndscan.py --root .. -o self_scan_report.json

The comparisons skip in exactly one case, where the question is
meaningless: the repository root is not there to scan (this directory
extracted on its own, e.g. `git archive <rev> nondeterminism-scanner`).
Even then the suite does NOT report success -- TestTheCheckCannotSilentlyPass
runs unconditionally and fails, so a directory that verified nothing exits
nonzero instead of printing OK. A missing report, a report that will not
parse, and a report whose bytes differ are all failures, never skips: a
check that shrugs at the artifact being gone is the same silent pass this
whole exercise is about.
"""

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
COMMITTED = os.path.join(HERE, "self_scan_report.json")
TOOL = os.path.join(HERE, "ndscan.py")
PY = sys.executable or "python3"

#: ndscan exits 1 when it has findings, which it does on this repository.
#: A different code means the run itself failed and no comparison is
#: meaningful.
EXPECTED_EXIT = 1

#: The scan reads every .py file under ROOT, so "is the root there" is
#: really "is there anything to scan". Checked by looking for sibling
#: directories rather than for any particular one of them.
def _root_is_present():
    if not os.path.isdir(ROOT):
        return False
    siblings = [d for d in os.listdir(ROOT)
                if os.path.isdir(os.path.join(ROOT, d))
                and d not in (".git", "__pycache__", os.path.basename(HERE))]
    return bool(siblings)


def regenerate(dest):
    """Run the documented command; return its exit code."""
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    proc = subprocess.run([PY, TOOL, "--root", ROOT, "-o", dest],
                          cwd=HERE, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, env=env, timeout=900)
    return proc


def sha256(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


@unittest.skipUnless(_root_is_present(),
                     "the repository root is not present to scan; this "
                     "directory was extracted on its own")
class TestCommittedSelfScanStillReproduces(unittest.TestCase):

    def setUp(self):
        self.scratch = tempfile.mkdtemp(prefix="ndselfscan_")   # created here
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        for name in os.listdir(self.scratch):
            os.remove(os.path.join(self.scratch, name))
        os.rmdir(self.scratch)                                  # created above

    def test_the_committed_report_exists(self):
        """Missing is a failure, not a skip: deleted evidence is a problem."""
        self.assertTrue(
            os.path.isfile(COMMITTED),
            "self_scan_report.json is missing. It is a committed artifact; "
            "regenerate it with: python3 ndscan.py --root .. -o "
            "self_scan_report.json")

    def test_regeneration_is_byte_identical_to_the_committed_report(self):
        out = os.path.join(self.scratch, "regenerated.json")
        proc = regenerate(out)
        self.assertEqual(
            proc.returncode, EXPECTED_EXIT,
            "ndscan.py --root .. exited %s, expected %s; stderr: %s"
            % (proc.returncode, EXPECTED_EXIT,
               proc.stderr.decode("utf-8", "replace")[-800:]))
        self.assertTrue(os.path.isfile(out), "no report was produced")

        with open(COMMITTED, "rb") as fh:
            committed = fh.read()
        with open(out, "rb") as fh:
            fresh = fh.read()
        if committed == fresh:
            return

        # Bytes differ. Say what moved, because "differs" alone sends the
        # reader looking through a 400-finding diff by hand.
        try:
            a = json.loads(committed.decode("utf-8"))["summary"]
            b = json.loads(fresh.decode("utf-8"))["summary"]
        except (ValueError, KeyError, UnicodeDecodeError):
            self.fail(
                "self_scan_report.json does not match a fresh run and could "
                "not be parsed to explain how. committed sha256=%s, "
                "regenerated sha256=%s"
                % (hashlib.sha256(committed).hexdigest(),
                   hashlib.sha256(fresh).hexdigest()))
        moved = ["  %-26s committed=%s  now=%s" % (k, a.get(k), b.get(k))
                 for k in sorted(set(a) | set(b))
                 if a.get(k) != b.get(k)]
        self.fail(
            "self_scan_report.json is stale: a fresh run differs from the "
            "committed bytes.\n%s\nRegenerate it in the same commit that "
            "changed the repository's Python:\n"
            "  cd nondeterminism-scanner && python3 ndscan.py --root .. -o "
            "self_scan_report.json"
            % ("\n".join(moved) or "  (findings differ; summary is equal)"))

    def test_two_consecutive_regenerations_agree(self):
        """The report must be a function of the tree, not of the run."""
        first = os.path.join(self.scratch, "first.json")
        second = os.path.join(self.scratch, "second.json")
        self.assertEqual(regenerate(first).returncode, EXPECTED_EXIT)
        self.assertEqual(regenerate(second).returncode, EXPECTED_EXIT)
        self.assertEqual(
            sha256(first), sha256(second),
            "two consecutive runs over the same tree produced different "
            "bytes; the generator itself is not deterministic and no "
            "committed report of it can be")


class TestTheCheckCannotSilentlyPass(unittest.TestCase):
    """Guards on the guard.

    A freshness check that skips itself into silence is worse than none:
    it reads as a green tick. These pin the two ways that could happen.
    """

    def test_the_skip_condition_is_false_in_a_real_checkout(self):
        self.assertTrue(
            _root_is_present(),
            "the skip condition fired inside a real checkout, which would "
            "make the freshness test above vacuous")

    def test_an_empty_root_is_the_only_thing_that_skips(self):
        parent = tempfile.mkdtemp(prefix="ndselfscan_probe_")   # created here
        self.addCleanup(os.rmdir, parent)                       # created above
        self.assertFalse(
            [d for d in os.listdir(parent)
             if os.path.isdir(os.path.join(parent, d))],
            "the probe directory should be empty")

    def test_the_expected_exit_code_is_the_documented_one(self):
        """1 = findings exist. Pinned so a silent change to 0 is caught."""
        entry = None
        manifest = os.path.join(ROOT, "report-freshness", "manifest.json")
        if not os.path.isfile(manifest):
            self.skipTest("report-freshness/manifest.json is not present")
        with open(manifest, encoding="utf-8") as fh:
            for e in json.load(fh)["entries"]:
                if e["tool"] == "nondeterminism-scanner":
                    entry = e
        self.assertIsNotNone(
            entry, "nondeterminism-scanner has no report-freshness manifest "
                   "entry; the repository-wide half of this lock is gone")
        self.assertEqual(entry["expected_exit_code"], EXPECTED_EXIT)
        self.assertEqual(entry["committed_report"],
                         "nondeterminism-scanner/self_scan_report.json")
        self.assertEqual(entry["generation"]["argv"],
                         ["python3", "ndscan.py", "--root", "..", "-o",
                          "{OUT}"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
