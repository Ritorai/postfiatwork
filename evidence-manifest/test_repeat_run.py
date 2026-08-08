"""Repeat-run byte test for manifest.py's file output.

WHAT THIS ADDS THAT test_manifest.py DOES NOT

`test_manifest.TestCli.test_build_stdout_repeatable` already runs `build`
twice and compares *stdout*. It does not touch the `-o/--out` write path, it
runs both passes from the same working directory, and both passes inherit the
same `PYTHONHASHSEED` from the ambient environment. This file closes those
three gaps:

  * it compares the bytes of the file `-o` writes, not stdout;
  * it stages the fixture into a *fresh* temporary directory for each pass, so
    a path leaking into the output would show up as drift;
  * it runs the two passes under two *different* `PYTHONHASHSEED` values.

That last point is the load-bearing one, and it is measured rather than
asserted. CPython randomises `str` hashing per process, so a tool that
iterates a `set` built from strings can emit a different order in two separate
processes while looking perfectly stable inside one. A CI runner that exports
`PYTHONHASHSEED=0` -- a common "make it reproducible" reflex -- hides that
entirely from any test that lets the child inherit the ambient value.

Mutating `canonicalize` to iterate `set(value)` instead of `sorted(value)`
and `serialize` to pass `sort_keys=False`, then running with
`PYTHONHASHSEED=0` exported (three runs each, stable):

    python3 -m unittest test_manifest      ->  Ran 29 tests / OK
    python3 -m unittest test_repeat_run    ->  Ran 25 tests / FAILED (failures=9)

The whole existing suite passes on a seed-dependent build; this file does not.
`sortkey-detector/README.md` limitation 5 names this blind spot in the
abstract ("could still differ across separate Python processes with different
hash seeds ... not something this detector independently varies and checks").
No committed test in this repository varied the seed until now.

Every *byte-comparison* failure prints BOTH SHA-256 digests and both byte
lengths, so a reviewer reading only the failure output can tell a content
change from a truncation without re-running anything. The assertions that
compare exit codes, stdout text or the fixture's own shape fail through plain
`assertEqual`, since there are no digests to print.

Scripts are invoked through `sys.executable`; nothing here needs an executable
bit, so every file this delivery adds is mode 100644.

Standard library only. Run with:

    python3 -m unittest test_repeat_run -v
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "manifest.py")

#: The fixture this file adds. Small on purpose, but shaped to exercise the
#: parts of canonicalisation where an ordering bug would actually show:
#: unsorted keys at two nesting depths, whitespace that rule 2 collapses,
#: non-ASCII, a duplicate submission_id/cid pair, mixed scalar types, and an
#: odd leaf count so the Merkle promote rule runs.
FIXTURE = "submissions_repeat.json"

#: Two seeds that are not each other and not the ambient value. Chosen as
#: constants rather than drawn from a RNG so the test itself has no hidden
#: nondeterminism.
SEED_A = "1"
SEED_B = "4242"


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def env_with(seed):
    """A clean child environment.

    PYTHONDONTWRITEBYTECODE keeps __pycache__ out of the staged directory;
    PYTHONUNBUFFERED is removed because it changes nothing here and its
    presence would be one more inherited variable this test does not control.
    """
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.pop("PYTHONUNBUFFERED", None)
    if seed is None:
        env.pop("PYTHONHASHSEED", None)
    else:
        env["PYTHONHASHSEED"] = seed
    return env


class RepeatRunMixin(object):
    """Stage the CLI plus a fixture into a throwaway directory and run it."""

    def stage(self, fixture=FIXTURE):
        workdir = tempfile.mkdtemp(prefix="em_repeat_")
        # Only remove the directory this call created. Never a parent of it.
        self.addCleanup(shutil.rmtree, workdir, True)
        shutil.copy2(SCRIPT, os.path.join(workdir, "manifest.py"))
        shutil.copy2(os.path.join(HERE, fixture),
                     os.path.join(workdir, fixture))
        return workdir

    def build_to_file(self, seed=None, fixture=FIXTURE, out_name="out.json"):
        """Run `build ... -o` in a fresh directory; return (bytes, stdout, rc)."""
        workdir = self.stage(fixture)
        out_path = os.path.join(workdir, out_name)
        proc = subprocess.run(
            [sys.executable, os.path.join(workdir, "manifest.py"),
             "build", fixture, "-o", out_name],
            cwd=workdir, capture_output=True, text=True, env=env_with(seed))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        with open(out_path, "rb") as fh:
            return fh.read(), proc.stdout, proc.returncode

    def build_to_stdout(self, seed=None, fixture=FIXTURE):
        workdir = self.stage(fixture)
        proc = subprocess.run(
            [sys.executable, os.path.join(workdir, "manifest.py"),
             "build", fixture],
            cwd=workdir, capture_output=True, env=env_with(seed))
        self.assertEqual(proc.returncode, 0, proc.stderr.decode("utf-8", "replace"))
        return proc.stdout

    def assertSameBytes(self, first, second, what):
        if first == second:
            return
        self.fail(
            "%s differ between the two passes\n"
            "  pass 1: sha256=%s bytes=%d\n"
            "  pass 2: sha256=%s bytes=%d"
            % (what, sha256(first), len(first), sha256(second), len(second)))


class TestSecondRunIsByteIdentical(RepeatRunMixin, unittest.TestCase):

    def test_output_file_bytes_are_identical_across_two_runs(self):
        first, _, _ = self.build_to_file()
        second, _, _ = self.build_to_file()
        self.assertSameBytes(first, second, "output-file bytes")

    def test_output_file_bytes_survive_two_different_hash_seeds(self):
        """The one a seed-inheriting comparison cannot make.

        The pass above pops PYTHONHASHSEED so each child randomises; this one
        pins two named, unequal values instead, so a failure is reproducible
        from the message alone and cannot be masked by a CI runner that
        exports a fixed seed.
        """
        first, _, _ = self.build_to_file(seed=SEED_A)
        second, _, _ = self.build_to_file(seed=SEED_B)
        self.assertSameBytes(
            first, second,
            "output-file bytes under PYTHONHASHSEED=%s vs %s" % (SEED_A, SEED_B))

    def test_stdout_bytes_survive_two_different_hash_seeds(self):
        first = self.build_to_stdout(seed=SEED_A)
        second = self.build_to_stdout(seed=SEED_B)
        self.assertSameBytes(first, second, "stdout bytes under two hash seeds")

    def test_summary_line_is_identical_across_two_runs(self):
        _, first, _ = self.build_to_file(seed=SEED_A)
        _, second, _ = self.build_to_file(seed=SEED_B)
        self.assertEqual(first, second)
        self.assertIn("batch_root=", first)
        self.assertIn("records=7", first)

    def test_two_different_staging_directories_give_the_same_bytes(self):
        """No absolute path, cwd or temp-directory name reaches the output."""
        first, _, _ = self.build_to_file()
        second, _, _ = self.build_to_file()
        self.assertSameBytes(first, second, "output-file bytes across two temp dirs")
        for probe in (tempfile.gettempdir().encode("utf-8"), b"em_repeat_"):
            self.assertNotIn(probe, first)

    def test_rerunning_into_the_same_output_path_is_a_no_op(self):
        """The literal reading of "a second run leaves its output unchanged".

        Every other pass here writes into a fresh directory, so the -o target
        never pre-exists. That misses a whole class of regression: `open(...,
        "a")` instead of `"w"` doubles the file on the second run and every
        other test in this file stays green.
        """
        workdir = self.stage()
        out = os.path.join(workdir, "out.json")
        seen = []
        for _ in range(2):
            proc = subprocess.run(
                [sys.executable, os.path.join(workdir, "manifest.py"),
                 "build", FIXTURE, "-o", "out.json"],
                cwd=workdir, capture_output=True, text=True, env=env_with(SEED_A))
            self.assertEqual(proc.returncode, 0, proc.stderr)
            with open(out, "rb") as fh:
                seen.append(fh.read())
        self.assertSameBytes(seen[0], seen[1],
                             "output-file bytes on a rerun into the same path")

    def test_output_name_does_not_change_the_bytes(self):
        first, _, _ = self.build_to_file(out_name="out.json")
        second, _, _ = self.build_to_file(out_name="a-different-name.json")
        self.assertSameBytes(first, second, "output-file bytes under two -o names")

    def test_file_output_equals_stdout_output(self):
        from_file, _, _ = self.build_to_file()
        from_stdout = self.build_to_stdout()
        self.assertSameBytes(from_file, from_stdout,
                             "the -o file and the stdout form")

    def test_the_shipped_fixture_repeats_too(self):
        """submissions.json, not just the fixture this delivery adds."""
        first, _, _ = self.build_to_file(seed=SEED_A, fixture="submissions.json")
        second, _, _ = self.build_to_file(seed=SEED_B, fixture="submissions.json")
        self.assertSameBytes(first, second, "submissions.json output-file bytes")


class TestVerifyIsAlsoStable(RepeatRunMixin, unittest.TestCase):

    def _verify(self, seed, manifest_name):
        workdir = self.stage()
        shutil.copy2(os.path.join(HERE, manifest_name),
                     os.path.join(workdir, manifest_name))
        return subprocess.run(
            [sys.executable, os.path.join(workdir, "manifest.py"),
             "verify", manifest_name],
            cwd=workdir, capture_output=True, text=True, env=env_with(seed))

    def test_verify_output_and_exit_code_repeat(self):
        first = self._verify(SEED_A, "manifest_run1.json")
        second = self._verify(SEED_B, "manifest_run1.json")
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual((first.stdout, first.stderr, first.returncode),
                         (second.stdout, second.stderr, second.returncode))

    def test_drift_report_repeats_verbatim(self):
        """The failure path has to be byte-stable too, or diffing it is useless."""
        first = self._verify(SEED_A, "tampered_manifest.json")
        second = self._verify(SEED_B, "tampered_manifest.json")
        self.assertEqual(first.returncode, 1)
        self.assertEqual((first.stdout, first.stderr, first.returncode),
                         (second.stdout, second.stderr, second.returncode))

    def test_a_freshly_built_manifest_verifies(self):
        workdir = self.stage()
        proc = subprocess.run(
            [sys.executable, os.path.join(workdir, "manifest.py"),
             "build", FIXTURE, "-o", "fresh.json"],
            cwd=workdir, capture_output=True, text=True, env=env_with(SEED_A))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        check = subprocess.run(
            [sys.executable, os.path.join(workdir, "manifest.py"),
             "verify", "fresh.json"],
            cwd=workdir, capture_output=True, text=True, env=env_with(SEED_B))
        self.assertEqual(check.returncode, 0, check.stderr)
        self.assertIn("records=7", check.stdout)


class TestTheCommittedArtifactsAreNotStale(RepeatRunMixin, unittest.TestCase):
    """The two run artifacts in this directory are checked-in numbers.

    They are what README.md's expected-results table quotes. If manifest.py's
    output ever changes and nobody regenerates them, this goes red instead of
    the table quietly becoming fiction.
    """

    def test_committed_run_artifacts_match_a_fresh_build(self):
        fresh, _, _ = self.build_to_file(fixture="submissions.json")
        for name in ("manifest_run1.json", "manifest_run2.json"):
            with open(os.path.join(HERE, name), "rb") as fh:
                committed = fh.read()
            self.assertSameBytes(fresh, committed,
                                 "a fresh build and the committed %s" % name)


class TestTheSourceCannotDriftBetweenRuns(unittest.TestCase):
    """The gap a two-pass byte comparison structurally cannot close.

    Both passes run back to back, in the same session, from copies made by
    `shutil.copy2` (which preserves mtime) with an inherited environment. Any
    drift source that is *constant across those few seconds* -- a clock read
    at day granularity, an input file's mtime, an environment variable --
    produces identical bytes in both passes and sails through every
    comparison above. Regenerating the committed artifacts afterwards, which
    is what a contributor would do, then makes the staleness tests green too.

    So the byte comparison is paired with a structural one: `manifest.py`
    imports nothing that can tell the time, read the environment, or stat a
    file, and does not reach for one at runtime either. That is checkable by
    parsing, needs no second run, and is what actually kills
    `"built_on": time.strftime("%Y-%m-%d")`, `os.path.getmtime(...)` and
    `os.environ.get("USER")`.
    """

    #: Deliberately a sorted list, not a set: a failure message that reorders
    #: itself between runs is a bad failure message.
    ALLOWED_IMPORTS = ["argparse", "hashlib", "json", "re", "sys"]

    #: Names that would let a clock, the environment or the filesystem back in
    #: without a top-level import.
    FORBIDDEN_CALLS = ("__import__", "eval", "exec", "compile")

    def setUp(self):
        import ast
        with open(os.path.join(HERE, "manifest.py"), "r", encoding="utf-8") as fh:
            self.source = fh.read()
        self.tree = ast.parse(self.source)
        self.ast = ast

    def _imported(self):
        names = set()
        for node in self.ast.walk(self.tree):
            if isinstance(node, self.ast.Import):
                for a in node.names:
                    names.add(a.name.split(".")[0])
            elif isinstance(node, self.ast.ImportFrom):
                if node.level == 0 and node.module:
                    names.add(node.module.split(".")[0])
        return names

    def test_it_imports_nothing_that_can_tell_the_time_or_read_the_world(self):
        self.assertEqual(sorted(self._imported()), self.ALLOWED_IMPORTS)

    def test_no_runtime_import_or_eval_smuggles_one_back_in(self):
        called = set()
        for node in self.ast.walk(self.tree):
            if isinstance(node, self.ast.Call) and isinstance(node.func, self.ast.Name):
                called.add(node.func.id)
        offenders = sorted(called.intersection(self.FORBIDDEN_CALLS))
        self.assertEqual(offenders, [],
                         "manifest.py calls %s, which can reintroduce a clock, "
                         "the environment or the filesystem without an import"
                         % (offenders,))

    def test_no_attribute_path_reaches_a_clock_the_environment_or_a_stat(self):
        banned = ("time", "clock", "now", "utcnow", "today", "environ",
                  "getenv", "getmtime", "getctime", "getcwd", "uname",
                  "gethostname", "getpid", "random", "urandom")
        seen = []
        for node in self.ast.walk(self.tree):
            if isinstance(node, self.ast.Attribute) and node.attr in banned:
                seen.append((getattr(node, "lineno", -1), node.attr))
        self.assertEqual(sorted(seen), [],
                         "manifest.py touches %s" % (sorted(seen),))


class TestTheReadmeNumbersAreCurrent(RepeatRunMixin, unittest.TestCase):
    """Every digest README.md quotes is re-derived here, not trusted.

    README.md's expected-results table quotes four 64-hex values: a batch root
    and an output-file SHA-256 for each of the two fixtures. A committed
    number nobody recomputes is a number that goes quietly wrong. This class
    reads the table out of README.md and rebuilds all four.
    """

    ROW = re.compile(
        r"^\|\s*`(?P<fixture>[^`]+)`\s+(?P<kind>batch_root|manifest SHA-256)\s*"
        r"\|\s*`(?P<value>[0-9a-f]{64})`\s*\|\s*$",
        re.MULTILINE)

    def setUp(self):
        with open(os.path.join(HERE, "README.md"), "r", encoding="utf-8") as fh:
            self.readme = fh.read()
        self.quoted = {(m.group("fixture"), m.group("kind")): m.group("value")
                       for m in self.ROW.finditer(self.readme)}

    def test_the_table_has_a_row_for_every_fixture_and_kind(self):
        expected = {(f, k)
                    for f in ("submissions.json", "submissions_repeat.json")
                    for k in ("batch_root", "manifest SHA-256")}
        self.assertEqual(set(self.quoted), expected,
                         "README.md's digest table no longer has exactly the "
                         "four rows this test re-derives")

    def test_every_quoted_digest_matches_a_fresh_build(self):
        for fixture in ("submissions.json", "submissions_repeat.json"):
            data, summary, _ = self.build_to_file(seed=SEED_A, fixture=fixture)
            root = summary.split("batch_root=", 1)[1].split("\n", 1)[0].strip()
            self.assertEqual(
                self.quoted[(fixture, "batch_root")], root,
                "README.md quotes a stale batch_root for %s" % fixture)
            self.assertEqual(
                self.quoted[(fixture, "manifest SHA-256")], sha256(data),
                "README.md quotes a stale manifest SHA-256 for %s" % fixture)

    def test_no_two_rows_share_a_digest(self):
        values = list(self.quoted.values())
        self.assertEqual(len(values), len(set(values)),
                         "two table rows quote the same digest, so one of them "
                         "cannot be doing any work")


class TestTheFixtureEarnsItsPlace(unittest.TestCase):
    """A fixture that does not exercise the rules is not evidence of anything."""

    def setUp(self):
        with open(os.path.join(HERE, FIXTURE), "r", encoding="utf-8") as fh:
            self.records = json.load(fh)

    def test_it_is_a_short_array_of_objects(self):
        self.assertIsInstance(self.records, list)
        self.assertEqual(len(self.records), 7)
        for r in self.records:
            self.assertIsInstance(r, dict)

    def test_the_leaf_count_is_odd_so_the_promote_rule_runs(self):
        self.assertEqual(len(self.records) % 2, 1)

    def test_it_contains_keys_that_are_not_in_sorted_order(self):
        unsorted_somewhere = any(list(r) != sorted(r) for r in self.records)
        self.assertTrue(unsorted_somewhere,
                        "no record has out-of-order keys, so rule 1 is untested")

    def test_it_contains_whitespace_rule_2_has_to_collapse(self):
        blob = json.dumps(self.records)
        self.assertTrue(any(m in blob for m in ("\\t", "\\n", "   ")),
                        "no messy whitespace, so rule 2 is untested")

    def test_it_contains_non_ascii(self):
        self.assertTrue(any(ord(c) > 127
                            for c in json.dumps(self.records, ensure_ascii=False)))

    def test_it_repeats_one_submission_id(self):
        ids = [r.get("submission_id") for r in self.records]
        self.assertLess(len(set(ids)), len(ids),
                        "no duplicate submission_id, so limitation EM-4 is untested")


if __name__ == "__main__":
    unittest.main()
