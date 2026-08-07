#!/usr/bin/env python3
"""Tests for regenpre.py.

The real preflight regenerates ten transcripts and twenty-seven reports and
takes minutes. These tests do not run it against this repository. They build
a tiny fixture repository with its own manifest, baselines and capture.sh,
and exercise every state against that -- so the suite is fast, and a failure
points at regenpre.py rather than at whatever a sibling tool happens to be
doing today.

The pure functions (normalise, classify) are tested directly against
hand-written strings whose correct answer is decidable by reading them.

Run:
    python3 -m unittest test_regenpre
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if THIS_DIR not in sys.path:
    sys.path.insert(0, THIS_DIR)

import regenpre as rp  # noqa: E402


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


class TestNormalise(unittest.TestCase):
    def test_duration_is_masked(self):
        out, counts = rp.normalise("Ran 12 tests in 0.403s\nOK\n")
        self.assertIn("<DURATION>s", out)
        self.assertEqual(counts["unittest_duration"], 1)

    def test_test_count_is_not_masked(self):
        a, _ = rp.normalise("Ran 12 tests in 0.1s")
        b, _ = rp.normalise("Ran 13 tests in 0.1s")
        self.assertNotEqual(a, b)

    def test_singular_test_is_masked_too(self):
        out, counts = rp.normalise("Ran 1 test in 0.001s")
        self.assertIn("<DURATION>s", out)
        self.assertEqual(counts["unittest_duration"], 1)

    def test_tempdir_is_masked(self):
        out, counts = rp.normalise("/tmp/indexgen_test_0e3_4592/x.json")
        self.assertIn("/tmp/<TEMPDIR>", out)
        self.assertEqual(counts["tempdir_name"], 1)

    def test_two_different_tempdirs_normalise_together(self):
        a, _ = rp.normalise("/tmp/indexgen_test_aaaaaaaa/x")
        b, _ = rp.normalise("/tmp/indexgen_test_bbbbbbbb/x")
        self.assertEqual(a, b)

    def test_plain_tmp_path_without_random_suffix_is_left_alone(self):
        out, counts = rp.normalise("/tmp/report.json")
        self.assertEqual(out, "/tmp/report.json")
        self.assertEqual(counts["tempdir_name"], 0)

    def test_counts_are_reported_for_every_pattern(self):
        _, counts = rp.normalise("nothing here")
        self.assertEqual(sorted(counts), sorted(n for n, _, _ in rp.VOLATILE_PATTERNS))

    def test_multiple_substitutions_are_counted(self):
        _, counts = rp.normalise("Ran 1 test in 0.1s\nRan 2 tests in 0.2s\n")
        self.assertEqual(counts["unittest_duration"], 2)

    def test_normalise_is_idempotent(self):
        once, _ = rp.normalise("Ran 5 tests in 1.25s")
        twice, _ = rp.normalise(once)
        self.assertEqual(once, twice)


class TestClassify(unittest.TestCase):
    def c(self, a, b):
        return rp.classify(a.encode(), b.encode())

    def test_identical_is_match(self):
        state, reason, _ = self.c("same\n", "same\n")
        self.assertEqual(state, rp.MATCH)
        self.assertIsNone(reason)

    def test_duration_difference_is_volatile_only(self):
        state, _, masked = self.c("Ran 3 tests in 0.10s\n",
                                  "Ran 3 tests in 0.99s\n")
        self.assertEqual(state, rp.VOLATILE_ONLY)
        self.assertEqual(masked["unittest_duration"], 1)

    def test_tempdir_difference_is_volatile_only(self):
        state, _, _ = self.c("/tmp/ig_test_aaaaaaaa/x\n",
                             "/tmp/ig_test_bbbbbbbb/x\n")
        self.assertEqual(state, rp.VOLATILE_ONLY)

    def test_test_count_difference_is_drift_not_volatile(self):
        state, _, _ = self.c("Ran 3 tests in 0.1s\n", "Ran 4 tests in 0.1s\n")
        self.assertEqual(state, rp.DRIFT)

    def test_python_version_difference_is_environment(self):
        state, reason, _ = self.c("Python 3.10.12\n", "Python 3.11.15\n")
        self.assertEqual(state, rp.ENVIRONMENT)
        self.assertIn("python_version", reason)

    def test_unittest_id_format_difference_is_environment(self):
        state, reason, _ = self.c("test_a (mod.C) ... ok\n",
                                  "test_a (mod.C.test_a) ... ok\n")
        self.assertEqual(state, rp.ENVIRONMENT)
        self.assertIn("unittest_test_id", reason)

    def test_content_difference_is_drift(self):
        state, reason, _ = self.c("findings: 3\n", "findings: 4\n")
        self.assertEqual(state, rp.DRIFT)
        self.assertIn("outside every masked", reason)

    def test_environment_is_preferred_over_drift_when_both_present(self):
        # A version banner plus a duration: still environment, not drift.
        state, _, _ = self.c("Python 3.10.12\nRan 1 test in 0.1s\n",
                             "Python 3.11.15\nRan 1 test in 0.9s\n")
        self.assertEqual(state, rp.ENVIRONMENT)

    def test_ok_states_do_not_include_drift_or_error(self):
        self.assertNotIn(rp.DRIFT, rp.OK_STATES)
        self.assertNotIn(rp.ERROR, rp.OK_STATES)
        self.assertNotIn(rp.ENVIRONMENT, rp.OK_STATES)


class FixtureRepo:
    """A minimal repository exercising all three phases."""

    def __init__(self, base, good=True):
        self.root = os.path.join(base, "fixture-repo")
        os.makedirs(self.root)
        self._make_gen_tool()
        self._make_baseline_tool(good)
        self._make_transcript_tool(good)

    def _make_gen_tool(self):
        d = os.path.join(self.root, "gen-tool")
        write(os.path.join(d, "gen.py"),
              "import sys\n"
              "open(sys.argv[sys.argv.index('-o') + 1], 'w').write('REPORT\\n')\n"
              "sys.exit(1)\n")
        write(os.path.join(d, "report.json"), "REPORT\n")
        write(os.path.join(self.root, "report-freshness", "manifest.json"),
              json.dumps({"schema_version": 2, "entries": [{
                  "id": "gen-tool:report.json", "tool": "gen-tool",
                  "kind": "regenerable",
                  "generation": {"argv": ["python3", "gen.py", "-o", "{OUT}"],
                                 "cwd": "gen-tool"},
                  "committed_report": "gen-tool/report.json",
                  "expected_exit_code": 1}]}))

    def _make_baseline_tool(self, good):
        d = os.path.join(self.root, "base-tool")
        body = "hello\n" if good else "changed\n"
        write(os.path.join(d, "b.py"),
              "import sys\n"
              "open(sys.argv[sys.argv.index('-o') + 1], 'w').write(%r)\n"
              % body)
        import hashlib
        digest = hashlib.sha256(b"hello\n").hexdigest()
        write(os.path.join(self.root, "regression-checker", "baselines.json"),
              json.dumps({"tools": {"base-tool": {
                  "command": ["python3", "b.py", "-o", "{REPORT}"],
                  "expected_exit_code": 0,
                  "expected_report_sha256": digest,
                  "report_mode": "file", "status": "baselined"}}}))

    def _make_transcript_tool(self, good):
        d = os.path.join(self.root, "tx-tool")
        write(os.path.join(d, "capture.sh"),
              "#!/usr/bin/env bash\n"
              "cd \"$(dirname \"$0\")\"\n"
              "printf 'Ran 2 tests in 0.%s s\\n' \"$RANDOM\" "
              "| sed 's/ s$/s/' > captured_output.txt\n")
        write(os.path.join(d, "captured_output.txt"),
              "Ran 2 tests in 0.111s\n" if good
              else "Ran 9 tests in 0.111s\n")


class TestFixtureRuns(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="regenpre_fixture_")
        self.addCleanup(shutil.rmtree, self.tmp)

    def report(self, good=True, phases=None):
        fx = FixtureRepo(self.tmp if good else
                         tempfile.mkdtemp(prefix="regenpre_bad_", dir=self.tmp),
                         good=good)
        return fx, rp.build_report(fx.root, phases or sorted(rp.PHASES), None)

    def test_all_three_phases_produce_items(self):
        _, r = self.report()
        self.assertEqual(sorted({i["phase"] for i in r["items"]}),
                         ["baselines", "manifest", "transcripts"])

    def test_good_fixture_has_no_failing_items(self):
        _, r = self.report()
        self.assertEqual(r["failing"], 0, r["items"])

    def test_transcript_duration_noise_is_volatile_only(self):
        _, r = self.report(phases=["transcripts"])
        self.assertEqual(r["items"][0]["state"], rp.VOLATILE_ONLY)

    def test_bad_fixture_reports_drift(self):
        _, r = self.report(good=False)
        states = {i["name"]: i["state"] for i in r["items"]}
        self.assertEqual(states["base-tool"], rp.DRIFT)
        self.assertEqual(states["tx-tool"], rp.DRIFT)

    def test_counts_sum_to_total(self):
        _, r = self.report()
        self.assertEqual(sum(r["counts"].values()), r["total"])

    def test_failing_counts_only_non_ok_states(self):
        _, r = self.report()
        expected = sum(v for k, v in r["counts"].items()
                       if k not in rp.OK_STATES)
        self.assertEqual(r["failing"], expected)

    def test_items_are_sorted(self):
        _, r = self.report()
        keys = [(i["phase"], i["name"]) for i in r["items"]]
        self.assertEqual(keys, sorted(keys))

    def test_only_filter_restricts_items(self):
        fx = FixtureRepo(self.tmp)
        r = rp.build_report(fx.root, ["baselines"], {"base-tool"})
        self.assertEqual([i["name"] for i in r["items"]], ["base-tool"])
        r2 = rp.build_report(fx.root, ["baselines"], {"nope"})
        self.assertEqual(r2["items"], [])

    def test_missing_transcript_is_an_error_not_a_crash(self):
        fx = FixtureRepo(self.tmp)
        os.remove(os.path.join(fx.root, "tx-tool", "captured_output.txt"))
        r = rp.build_report(fx.root, ["transcripts"], None)
        self.assertEqual(r["items"][0]["state"], rp.ERROR)

    def test_unrunnable_baseline_is_an_error(self):
        fx = FixtureRepo(self.tmp)
        os.remove(os.path.join(fx.root, "base-tool", "b.py"))
        r = rp.build_report(fx.root, ["baselines"], None)
        self.assertEqual(r["items"][0]["state"], rp.ERROR)


class TestWorkingTreeIsNeverWritten(unittest.TestCase):
    def test_fixture_bytes_are_unchanged_after_a_full_run(self):
        tmp = tempfile.mkdtemp(prefix="regenpre_clean_")
        self.addCleanup(shutil.rmtree, tmp)
        fx = FixtureRepo(tmp)
        before = {}
        for dirpath, _dirs, files in os.walk(fx.root):
            for fn in files:
                p = os.path.join(dirpath, fn)
                with open(p, "rb") as fh:
                    before[os.path.relpath(p, fx.root)] = fh.read()
        rp.build_report(fx.root, sorted(rp.PHASES), None)
        after = {}
        for dirpath, _dirs, files in os.walk(fx.root):
            for fn in files:
                p = os.path.join(dirpath, fn)
                with open(p, "rb") as fh:
                    after[os.path.relpath(p, fx.root)] = fh.read()
        self.assertEqual(sorted(before), sorted(after),
                         "a file was created or removed in the tree")
        self.assertEqual(before, after, "a file's bytes changed in the tree")

    def test_no_scratch_directory_survives(self):
        tmp = tempfile.mkdtemp(prefix="regenpre_leak_")
        self.addCleanup(shutil.rmtree, tmp)
        fx = FixtureRepo(tmp)
        parent = tempfile.gettempdir()
        before = {n for n in os.listdir(parent) if n.startswith("regenpre_")}
        rp.build_report(fx.root, sorted(rp.PHASES), None)
        after = {n for n in os.listdir(parent) if n.startswith("regenpre_")}
        self.assertEqual(after - before, set())


class TestFreshCopy(unittest.TestCase):
    def test_git_and_pycache_are_excluded(self):
        tmp = tempfile.mkdtemp(prefix="regenpre_copy_")
        self.addCleanup(shutil.rmtree, tmp)
        src = os.path.join(tmp, "src")
        write(os.path.join(src, "a.txt"), "a\n")
        write(os.path.join(src, ".git", "config"), "x\n")
        write(os.path.join(src, "__pycache__", "m.pyc"), "y\n")
        parent, tree = rp.fresh_copy(src)
        try:
            self.assertTrue(os.path.isfile(os.path.join(tree, "a.txt")))
            self.assertFalse(os.path.exists(os.path.join(tree, ".git")))
            self.assertFalse(os.path.exists(os.path.join(tree, "__pycache__")))
        finally:
            shutil.rmtree(parent)


class TestCli(unittest.TestCase):
    def run_cli(self, *args, cwd=None):
        return subprocess.run(
            [sys.executable, os.path.join(THIS_DIR, "regenpre.py")] + list(args),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=cwd or THIS_DIR)

    def test_bad_root_exits_2(self):
        p = self.run_cli("--root", os.path.join(THIS_DIR, "no_such_dir_xyz"))
        self.assertEqual(p.returncode, 2)
        self.assertIn(b"not a directory", p.stderr)

    def test_unknown_phase_exits_2(self):
        p = self.run_cli("--phase", "nonsense")
        self.assertEqual(p.returncode, 2)
        self.assertIn(b"unknown --phase", p.stderr)

    def test_clean_fixture_exits_0_and_emits_canonical_json(self):
        tmp = tempfile.mkdtemp(prefix="regenpre_cli_")
        self.addCleanup(shutil.rmtree, tmp)
        fx = FixtureRepo(tmp)
        p = self.run_cli("--root", fx.root)
        self.assertEqual(p.returncode, 0, p.stderr.decode()[:400])
        text = p.stdout.decode()
        self.assertEqual(text, rp.canonical_json(json.loads(text)))

    def test_drifting_fixture_exits_1(self):
        tmp = tempfile.mkdtemp(prefix="regenpre_cli_bad_")
        self.addCleanup(shutil.rmtree, tmp)
        fx = FixtureRepo(tmp, good=False)
        p = self.run_cli("--root", fx.root)
        self.assertEqual(p.returncode, 1)

    def test_output_file_matches_stdout(self):
        tmp = tempfile.mkdtemp(prefix="regenpre_cli_o_")
        self.addCleanup(shutil.rmtree, tmp)
        fx = FixtureRepo(tmp)
        out = os.path.join(tmp, "r.json")
        self.run_cli("--root", fx.root, "-o", out)
        with open(out, encoding="utf-8") as fh:
            written = fh.read()
        p = self.run_cli("--root", fx.root)
        # The transcript phase has a genuinely random duration, so compare
        # the parts that must not vary rather than raw bytes.
        a, b = json.loads(written), json.loads(p.stdout.decode())
        self.assertEqual(a["counts"], b["counts"])
        self.assertEqual([i["name"] for i in a["items"]],
                         [i["name"] for i in b["items"]])


class TestOnlyMustSelectSomething(unittest.TestCase):
    """The repaired defect: --only used to accept anything.

    `--phase` was validated from the start; `--only` was not. An unknown
    name simply filtered every item away, and the run then wrote a report
    with total 0, failing 0, every count zero, and exited 0 -- an
    authoritative green result from a run that checked nothing. A typo of
    a real directory name ("shebangmode" for "shebang-mode") was enough,
    and nothing was printed to say so.

    build_report() keeps its old library-level behaviour of returning an
    empty item list for an unmatched filter (TestFixtureRuns.
    test_only_filter_restricts_items still pins that); the rejection is at
    the CLI boundary, which is where a human's typo arrives.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="regenpre_only_")   # created here
        self.addCleanup(shutil.rmtree, self.tmp)               # created above
        self.fx = FixtureRepo(self.tmp)

    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, os.path.join(THIS_DIR, "regenpre.py"),
             "--root", self.fx.root] + list(args),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=THIS_DIR)

    def test_unknown_only_name_exits_2_and_says_so(self):
        p = self.run_cli("--only", "no-such-tool")
        self.assertEqual(p.returncode, 2, p.stdout.decode()[:300])
        self.assertIn(b"unknown --only", p.stderr)
        self.assertIn(b"no-such-tool", p.stderr)

    def test_unknown_only_name_writes_no_green_report(self):
        """The failure this repair exists to stop.

        Before: stdout carried a full report with total 0 / failing 0 and
        the process exited 0.
        """
        p = self.run_cli("--only", "no-such-tool")
        self.assertEqual(p.stdout, b"")
        self.assertNotEqual(p.returncode, 0)

    def test_a_typo_of_a_real_name_is_rejected(self):
        p = self.run_cli("--only", "gentool")          # real name: gen-tool
        self.assertEqual(p.returncode, 2)
        self.assertIn(b"gentool", p.stderr)

    def test_the_error_lists_the_names_that_do_work(self):
        p = self.run_cli("--only", "no-such-tool")
        for name in (b"gen-tool", b"base-tool", b"tx-tool"):
            self.assertIn(name, p.stderr)

    def test_one_bad_name_among_good_ones_is_still_rejected(self):
        p = self.run_cli("--only", "gen-tool,no-such-tool")
        self.assertEqual(p.returncode, 2)
        self.assertIn(b"no-such-tool", p.stderr)
        self.assertNotIn(b"unknown --only: gen-tool", p.stderr)

    def test_an_empty_only_still_means_no_filter(self):
        """`--only ""` was falsy at every previous commit.

        It therefore meant "no filter" and the run checked everything.
        Rejecting it would change the findings path, not repair it -- an
        earlier draft of this repair did exactly that, and a drifting
        repository stopped exiting 1.
        """
        p = self.run_cli("--only", "")
        self.assertEqual(p.returncode, 0, p.stderr.decode()[:400])
        self.assertEqual(json.loads(p.stdout.decode())["total"], 3)

    def test_an_empty_only_on_a_drifting_repository_still_exits_1(self):
        tmp = tempfile.mkdtemp(prefix="regenpre_empty_bad_")   # created here
        self.addCleanup(shutil.rmtree, tmp)                    # created above
        bad = FixtureRepo(tmp, good=False)
        p = subprocess.run(
            [sys.executable, os.path.join(THIS_DIR, "regenpre.py"),
             "--root", bad.root, "--only", ""],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=THIS_DIR)
        self.assertEqual(p.returncode, 1, p.stderr.decode()[:400])
        self.assertGreater(json.loads(p.stdout.decode())["failing"], 0)

    def test_a_non_empty_only_that_names_nothing_is_rejected(self):
        """The shape that really did produce a green empty report.

        `--only ","` parsed to {""} at the parent: a filter that matches
        no tool, so every item was dropped and the run exited 0.
        """
        for value in (",", "  ", " , , "):
            p = self.run_cli("--only", value)
            self.assertEqual(p.returncode, 2, "%r was accepted" % value)
            self.assertIn(b"names nothing", p.stderr)
            self.assertEqual(p.stdout, b"")

    def test_a_real_name_in_the_wrong_phase_is_rejected_distinctly(self):
        """Also a run that would check nothing, but a different mistake.

        gen-tool is a manifest entry; asking for it in the transcripts
        phase is not a typo, it is a combination that selects no items.
        The message has to say which, or the fix just moves the confusion.
        """
        p = self.run_cli("--only", "gen-tool", "--phase", "transcripts")
        self.assertEqual(p.returncode, 2)
        self.assertIn(b"selects nothing in phase", p.stderr)
        self.assertNotIn(b"unknown --only", p.stderr)

    def test_valid_only_still_runs_and_exits_0(self):
        p = self.run_cli("--only", "gen-tool")
        self.assertEqual(p.returncode, 0, p.stderr.decode()[:400])
        report = json.loads(p.stdout.decode())
        self.assertEqual([i["name"] for i in report["items"]],
                         ["gen-tool:report.json"])

    def test_valid_only_with_a_matching_phase_still_runs(self):
        p = self.run_cli("--only", "gen-tool", "--phase", "manifest")
        self.assertEqual(p.returncode, 0, p.stderr.decode()[:400])
        self.assertEqual(json.loads(p.stdout.decode())["total"], 1)

    def test_valid_only_still_reports_a_real_failure_as_exit_1(self):
        """The findings path must be untouched by a usage-error change."""
        tmp = tempfile.mkdtemp(prefix="regenpre_only_bad_")   # created here
        self.addCleanup(shutil.rmtree, tmp)                   # created above
        bad = FixtureRepo(tmp, good=False)
        p = subprocess.run(
            [sys.executable, os.path.join(THIS_DIR, "regenpre.py"),
             "--root", bad.root, "--only", "base-tool"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=THIS_DIR)
        self.assertEqual(p.returncode, 1, p.stderr.decode()[:400])

    def test_no_only_flag_at_all_is_unaffected(self):
        p = self.run_cli()
        self.assertEqual(p.returncode, 0, p.stderr.decode()[:400])
        self.assertEqual(json.loads(p.stdout.decode())["total"], 3)


class TestSelectableTargets(unittest.TestCase):
    """The discovery the rejection rests on. It must run nothing."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="regenpre_sel_")    # created here
        self.addCleanup(shutil.rmtree, self.tmp)               # created above
        self.fx = FixtureRepo(self.tmp)

    def test_each_phase_reports_its_own_names(self):
        found = rp.selectable_targets(self.fx.root, set(rp.PHASES))
        self.assertEqual(found["manifest"], {"gen-tool"})
        self.assertEqual(found["baselines"], {"base-tool"})
        self.assertEqual(found["transcripts"], {"tx-tool"})

    def test_only_the_requested_phases_are_discovered(self):
        found = rp.selectable_targets(self.fx.root, {"baselines"})
        self.assertEqual(set(found), {"baselines"})

    def test_a_missing_inventory_is_an_empty_set_not_a_crash(self):
        os.remove(os.path.join(self.fx.root, "regression-checker",
                               "baselines.json"))
        found = rp.selectable_targets(self.fx.root, set(rp.PHASES))
        self.assertEqual(found["baselines"], set())

    #: Every way an inventory can be wrong. The first is the only one an
    #: earlier version survived: the rest parse as JSON and then fail on
    #: shape, which escaped as TypeError/AttributeError and aborted the
    #: run with exit 1 -- indistinguishable from a real finding.
    BROKEN_MANIFESTS = [
        ("not json", "{not json at all"),
        ("a json list", "[1, 2, 3]"),
        ("a json string", '"entries"'),
        ("a json number", "7"),
        ("entries is a string", '{"entries": "nope"}'),
        ("entries is an object", '{"entries": {"a": 1}}'),
        ("entries key missing", '{"schema_version": 2}'),
        ("entries holds strings", '{"entries": ["a", "b"]}'),
        ("entry has no tool", '{"entries": [{"kind": "regenerable"}]}'),
        ("tool is not a string",
         '{"entries": [{"kind": "regenerable", "tool": 7}]}'),
        ("empty file", ""),
    ]

    def test_no_shape_of_broken_manifest_raises(self):
        for label, body in self.BROKEN_MANIFESTS:
            write(os.path.join(self.fx.root, "report-freshness",
                               "manifest.json"), body)
            with self.subTest(label):
                found = rp.selectable_targets(self.fx.root, set(rp.PHASES))
                self.assertEqual(found["manifest"], set())
                # the other phases must still be discovered
                self.assertEqual(found["baselines"], {"base-tool"})

    def test_no_shape_of_broken_baselines_raises(self):
        for label, body in [("not json", "{nope"), ("a list", "[]"),
                            ("tools is a list", '{"tools": []}'),
                            ("tools missing", "{}"),
                            ("tools is a string", '{"tools": "x"}')]:
            write(os.path.join(self.fx.root, "regression-checker",
                               "baselines.json"), body)
            with self.subTest(label):
                found = rp.selectable_targets(self.fx.root, set(rp.PHASES))
                self.assertEqual(found["baselines"], set())

    def test_a_broken_inventory_in_an_unselected_phase_does_not_abort(self):
        """The regression this pins, end to end through the CLI.

        `--phase baselines` never reads manifest.json in the phase
        functions. Validation reads all three inventories so it can tell
        "no such name" from "wrong phase", so a broken manifest.json now
        reaches code it did not reach before -- and must not turn a
        working run into a traceback.
        """
        write(os.path.join(self.fx.root, "report-freshness", "manifest.json"),
              '{"entries": [1, 2, 3]}')
        p = subprocess.run(
            [sys.executable, os.path.join(THIS_DIR, "regenpre.py"),
             "--root", self.fx.root, "--phase", "baselines",
             "--only", "base-tool"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=THIS_DIR)
        self.assertNotIn(b"Traceback", p.stderr)
        self.assertEqual(p.returncode, 0, p.stderr.decode()[:400])
        self.assertEqual(json.loads(p.stdout.decode())["total"], 1)

    def test_discovery_does_not_regenerate_anything(self):
        """It reads inventories; it must not run a generator.

        Pinned by breaking every generator first: if discovery executed
        any of them this would raise or hang, and the names would still
        have to come back.
        """
        for rel in ("gen-tool/gen.py", "base-tool/b.py", "tx-tool/capture.sh"):
            write(os.path.join(self.fx.root, rel), "exit 1\n")
        found = rp.selectable_targets(self.fx.root, set(rp.PHASES))
        self.assertEqual(found["manifest"], {"gen-tool"})
        self.assertEqual(found["transcripts"], {"tx-tool"})


class TestReportShape(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="regenpre_shape_")
        self.addCleanup(shutil.rmtree, self.tmp)
        fx = FixtureRepo(self.tmp)
        self.report = rp.build_report(fx.root, sorted(rp.PHASES), None)

    def test_masked_patterns_are_disclosed_in_the_report(self):
        self.assertEqual(self.report["volatile_patterns"],
                         [n for n, _, _ in rp.VOLATILE_PATTERNS])
        self.assertEqual(self.report["environment_patterns"],
                         [n for n, _ in rp.ENVIRONMENT_PATTERNS])

    def test_every_item_has_the_required_keys(self):
        for it in self.report["items"]:
            with self.subTest(name=it["name"]):
                for key in ("phase", "name", "command", "state", "reason"):
                    self.assertIn(key, it)

    def test_every_state_is_known(self):
        known = (rp.MATCH, rp.VOLATILE_ONLY, rp.ENVIRONMENT, rp.DRIFT, rp.ERROR)
        for it in self.report["items"]:
            with self.subTest(name=it["name"]):
                self.assertIn(it["state"], known)

    def test_report_has_no_absolute_fixture_path_in_names(self):
        for it in self.report["items"]:
            with self.subTest(name=it["name"]):
                self.assertFalse(os.path.isabs(it["name"]))

    def test_canonical_json_round_trips(self):
        text = rp.canonical_json(self.report)
        self.assertEqual(text, rp.canonical_json(json.loads(text)))


if __name__ == "__main__":
    unittest.main()
