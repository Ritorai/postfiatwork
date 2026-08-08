"""Tests for check_counts.py, the stale-number check.

Three tests carry most of the weight.

`test_every_committed_value_is_actually_read` walks every path the check
claims to cover, edits that one value in a temporary copy of the reports,
and requires the check to name it. A check that quietly stopped looking at
a field would pass a hand-written happy path; it cannot pass that walk.

`test_recomputation_agrees_with_the_tool_on_generated_fixtures` and
`test_recomputation_agrees_with_the_tool_on_shaped_fixtures` drive the
tool and the check over the same inputs and require them to agree on
every path. check_counts.py does not import throughput.py -- that is the
point of it -- so this is where the claim that the two implement the same
rule is established. It establishes agreement, not correctness: the check
is a transliteration, so a bug shared with the tool survives both.
"""
import ast
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

import check_counts as C
import throughput as T

HERE = os.path.dirname(os.path.abspath(__file__))

REPORTS = tuple(report for _, report, _ in C.PAIRS)
FIXTURES = ("events_ok.json", "events_breach.json")

#: Every path across the three reports, plus a contributor-order and a
#: canonical-form check for each of them.
EXPECTED_PATHS_CHECKED = 135

SUMMARY = ("checked=%d stale=0 missing=0 unexpected=0 duplicate=0 format=0\n"
           % EXPECTED_PATHS_CHECKED)


def read(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def write(path, obj):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(C.canonical(obj))


class TempCopy(object):
    """A scratch copy of the committed fixtures and reports.

    Only the files this check reads are copied, and only into a directory
    mkdtemp made for this test, which the same object removes.
    """

    def __enter__(self):
        self.dir = tempfile.mkdtemp(prefix="check_counts_")
        for name in REPORTS + FIXTURES:
            shutil.copyfile(os.path.join(HERE, name),
                            os.path.join(self.dir, name))
        return self

    def __exit__(self, *exc):
        shutil.rmtree(self.dir)
        return False

    def path(self, name):
        return os.path.join(self.dir, name)

    def load(self, name):
        return read(self.path(name))

    def save(self, name, obj):
        write(self.path(name), obj)

    def restore(self, name):
        shutil.copyfile(os.path.join(HERE, name), self.path(name))

    def run(self):
        return C.run(self.dir)

    def cli(self, *args):
        return subprocess.run(
            [sys.executable, os.path.join(HERE, "check_counts.py"),
             self.dir] + list(args),
            capture_output=True, text=True)


def bump(value):
    """A value that differs from *value* but keeps the report loadable."""
    if isinstance(value, bool):
        return not value
    if isinstance(value, int):
        return value + 1000
    if isinstance(value, float):
        return value + 1000.0
    if isinstance(value, str):
        return value + "-drifted"
    return 0                                    # None


def set_path(report, path, value):
    """Set one dotted path produced by C.flat_report on a loaded report."""
    if path.startswith("contributors["):
        who = path[len("contributors["):path.index("]")]
        rest = path[path.index("]") + 2:]
        for entry in report["contributors"]:
            if entry["contributor"] == who:
                target = entry
                break
        else:
            raise AssertionError("no contributor named " + who)
    else:
        rest = path
        target = report
    keys = rest.split(".")
    for key in keys[:-1]:
        target = target[key]
    target[keys[-1]] = value


class Sequence(object):
    """A deterministic pseudo-random sequence, spelled out in full.

    `random.Random(seed)` would do the same job, but this repository's
    nondeterminism-scanner counts any use of `random` without a
    `random.seed()` call as a finding, and a linear congruential
    generator written here is reproducible across interpreters as well as
    across runs -- `random.choice` and `random.randrange` are not
    contractually stable between Python versions.
    """

    def __init__(self, seed):
        self.state = seed & 0x7FFFFFFF

    def below(self, limit):
        self.state = (1103515245 * self.state + 12345) & 0x7FFFFFFF
        return self.state % limit

    def pick(self, items):
        return items[self.below(len(items))]


def run_the_tool(events, cfg):
    """The report throughput.py produces for *events* under *cfg*."""
    handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
    try:
        json.dump(events, handle)
        handle.close()
        loaded = T.load_events(handle.name)
    finally:
        os.unlink(handle.name)
    return T.serialize(T.analyze(loaded, cfg))


class TestCommittedArtifactsAreFresh(unittest.TestCase):

    def test_the_committed_reports_reproduce(self):
        problems, checked = C.run(HERE)
        self.assertEqual(problems, [])
        self.assertEqual(checked, EXPECTED_PATHS_CHECKED)

    def test_the_cli_exits_zero_on_the_committed_directory(self):
        proc = subprocess.run(
            [sys.executable, os.path.join(HERE, "check_counts.py")],
            capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, SUMMARY)

    def test_two_runs_print_the_same_bytes(self):
        outs = []
        for _ in range(2):
            proc = subprocess.run(
                [sys.executable, os.path.join(HERE, "check_counts.py"), HERE],
                capture_output=True, text=True)
            outs.append(proc.stdout)
        self.assertEqual(outs[0], outs[1])

    def test_the_path_total_is_derived_not_typed(self):
        # 43 paths per report -- report_version, two config values, three
        # totals, three grade_counts, status, and eleven for each of three
        # contributors -- plus an order check and a canonical-form check.
        total = 0
        for name in REPORTS:
            flat, order, repeated = C.flat_report(
                name, read(os.path.join(HERE, name)))
            self.assertEqual(repeated, [])
            self.assertEqual(len(order), 3)
            self.assertEqual(len(flat), 43, sorted(flat))
            total += len(flat) + 2
        self.assertEqual(total, EXPECTED_PATHS_CHECKED)

    def test_the_committed_reports_are_in_canonical_form(self):
        # Only the reports: the fixtures are hand-written inputs, not
        # serialize() output, and are indented for reading.
        for name in REPORTS:
            with self.subTest(name):
                with open(os.path.join(HERE, name), encoding="utf-8") as fh:
                    text = fh.read()
                self.assertEqual(text, C.canonical(json.loads(text)))


class TestStaleValuesAreCaught(unittest.TestCase):

    def test_every_committed_value_is_actually_read(self):
        walked = 0
        with TempCopy() as copy:
            for name in REPORTS:
                flat, _, _ = C.flat_report(name, read(os.path.join(HERE, name)))
                for path in sorted(flat):
                    walked += 1
                    with self.subTest(report=name, path=path):
                        report = copy.load(name)
                        set_path(report, path, bump(flat[path]))
                        copy.save(name, report)
                        try:
                            problems, checked = copy.run()
                        finally:
                            copy.restore(name)
                        self.assertEqual(checked, EXPECTED_PATHS_CHECKED)
                        self.assertEqual(
                            problems,
                            ["STALE %s %s expected=%s found=%s"
                             % (name, path, C._value(flat[path]),
                                C._value(bump(flat[path])))])
        self.assertEqual(walked, EXPECTED_PATHS_CHECKED - 2 * len(REPORTS))

    def test_bump_never_lands_back_on_the_original(self):
        # The walk above is only meaningful if every edit is a real one.
        for name in REPORTS:
            flat, _, _ = C.flat_report(name, read(os.path.join(HERE, name)))
            for path, value in sorted(flat.items()):
                with self.subTest(report=name, path=path):
                    self.assertNotEqual(C._value(bump(value)), C._value(value))

    def test_the_pinned_flags_are_compared_not_trusted(self):
        # config.* records the flags the run was given. Reading it back
        # out of the report under test would make a report naming flags
        # the documented command never passed look fresh.
        with TempCopy() as copy:
            report = copy.load("report_ok.json")
            report["config"] = {"min_tasks": 1, "refusal_ceiling": 0.99}
            copy.save("report_ok.json", report)
            problems, _ = copy.run()
        self.assertEqual(problems, [
            "STALE report_ok.json config.min_tasks expected=2 found=1",
            "STALE report_ok.json config.refusal_ceiling expected=0.5 "
            "found=0.99",
        ])

    def test_a_min_tasks_of_two_point_zero_is_not_a_min_tasks_of_two(self):
        # throughput.py parses --min-tasks with type=int, so a float here
        # could not have come from the documented command, and it grades
        # identically -- exactly the kind of value equality waves through.
        with TempCopy() as copy:
            report = copy.load("report_ok.json")
            report["config"]["min_tasks"] = 2.0
            copy.save("report_ok.json", report)
            problems, _ = copy.run()
        self.assertEqual(problems, [
            "STALE report_ok.json config.min_tasks expected=2 found=2.0"])

    def test_a_report_version_that_is_not_the_tool_s_is_caught(self):
        with TempCopy() as copy:
            report = copy.load("report_ok.json")
            report["report_version"] = "9.9"
            copy.save("report_ok.json", report)
            problems, _ = copy.run()
        self.assertEqual(problems, [
            'STALE report_ok.json report_version expected="1.0" found="9.9"'])

    def test_a_removed_report_version_is_missing_not_ignored(self):
        with TempCopy() as copy:
            report = copy.load("report_ok.json")
            del report["report_version"]
            copy.save("report_ok.json", report)
            problems, checked = copy.run()
        self.assertEqual(problems, [
            'MISSING report_ok.json report_version expected="1.0" '
            'found=absent'])
        self.assertEqual(checked, EXPECTED_PATHS_CHECKED - 1)

    def test_reordering_the_contributors_is_caught(self):
        with TempCopy() as copy:
            report = copy.load("report_ok.json")
            report["contributors"].reverse()
            copy.save("report_ok.json", report)
            problems, checked = copy.run()
        self.assertEqual(checked, EXPECTED_PATHS_CHECKED)
        self.assertEqual(problems, [
            'STALE report_ok.json contributors.order '
            'expected=["bob", "alice", "carol"] '
            'found=["carol", "alice", "bob"]'])

    def test_the_message_carries_both_values(self):
        with TempCopy() as copy:
            report = copy.load("report_ok.json")
            set_path(report, "contributors[bob].counts.rewarded", 99)
            copy.save("report_ok.json", report)
            problems, _ = copy.run()
        self.assertEqual(
            problems,
            ["STALE report_ok.json contributors[bob].counts.rewarded "
             "expected=1 found=99"])

    def test_a_drifted_timestamp_moves_a_grade_and_is_caught(self):
        # The counts are untouched by this edit. A check that traced only
        # the counting would pass a report whose grade block is wrong.
        with TempCopy() as copy:
            events = copy.load("events_ok.json")
            for event in events:
                if event["contributor"] == "alice" and event["state"] == "submitted":
                    event["occurred_at"] = "2026-08-30T09:00:00Z"
            copy.save("events_ok.json", events)
            problems, _ = copy.run()
        self.assertIn('STALE report_ok.json contributors[alice].grade '
                      'expected="B" found="A"', problems)
        self.assertIn("MISSING report_ok.json grade_counts.B expected=1 "
                      "found=absent", problems)
        self.assertIn("UNEXPECTED report_ok.json grade_counts.A not produced "
                      "by the recomputation in check_counts.py", problems)

    def test_editing_the_fixture_instead_of_the_report_also_trips(self):
        # The other direction of the same claim: the check compares two
        # committed things, so it does not matter which one moved.
        with TempCopy() as copy:
            events = copy.load("events_ok.json")
            kept = [e for e in events
                    if not (e["contributor"] == "bob" and e["state"] == "refused")]
            self.assertEqual(len(kept), len(events) - 1)
            copy.save("events_ok.json", kept)
            problems, _ = copy.run()
        for expected in [
            "STALE report_ok.json totals.events expected=12 found=13",
            "STALE report_ok.json contributors[bob].counts.refused "
            "expected=0 found=1",
            "STALE report_ok.json contributors[bob].counts.terminal "
            "expected=1 found=2",
            'STALE report_ok.json contributors[bob].grade '
            'expected="INSUFFICIENT_DATA" found="C"',
            "STALE report_ok.json contributors[bob].refusal_rate "
            "expected=0.0 found=0.5",
        ]:
            self.assertIn(expected, problems)

    def test_dropping_a_contributor_from_the_fixture_is_reported(self):
        with TempCopy() as copy:
            events = copy.load("events_ok.json")
            copy.save("events_ok.json",
                      [e for e in events if e["contributor"] != "carol"])
            problems, _ = copy.run()
        self.assertIn("STALE report_ok.json totals.contributors "
                      "expected=2 found=3", problems)
        self.assertIn('STALE report_ok.json contributors.order '
                      'expected=["bob", "alice"] '
                      'found=["bob", "alice", "carol"]', problems)

    def test_a_removed_value_is_reported_as_missing_not_skipped(self):
        with TempCopy() as copy:
            report = copy.load("report_ok.json")
            for entry in report["contributors"]:
                if entry["contributor"] == "alice":
                    del entry["counts"]["terminal"]
            copy.save("report_ok.json", report)
            problems, checked = copy.run()
        self.assertEqual(
            problems,
            ["MISSING report_ok.json contributors[alice].counts.terminal "
             "expected=2 found=absent"])
        self.assertEqual(checked, EXPECTED_PATHS_CHECKED - 1)

    def test_the_cli_prints_every_problem_not_just_the_first(self):
        with TempCopy() as copy:
            report = copy.load("report_ok.json")
            set_path(report, "totals.events", 500)
            set_path(report, "contributors[alice].counts.accepted", 501)
            copy.save("report_ok.json", report)
            proc = copy.cli()
        self.assertEqual(proc.returncode, 1, proc.stderr)
        self.assertEqual(proc.stdout.splitlines(), [
            "STALE report_ok.json contributors[alice].counts.accepted "
            "expected=2 found=501",
            "STALE report_ok.json totals.events expected=13 found=500",
            "checked=%d stale=2 missing=0 unexpected=0 duplicate=0 format=0"
            % EXPECTED_PATHS_CHECKED,
        ])


class TestARepeatedContributorCannotHide(unittest.TestCase):
    """Keying by name alone lets one entry overwrite its twin."""

    def duplicate(self, copy, which, value=999):
        report = copy.load("report_ok.json")
        twin = json.loads(json.dumps(report["contributors"][0]))
        report["contributors"].insert(0, twin)
        report["contributors"][which]["counts"]["rewarded"] = value
        copy.save("report_ok.json", report)
        return report["contributors"][which]["contributor"]

    def test_the_repetition_itself_is_reported(self):
        with TempCopy() as copy:
            self.duplicate(copy, 1)
            problems, _ = copy.run()
        self.assertIn('DUPLICATE report_ok.json contributors carries "bob" '
                      'more than once', problems)

    def test_a_value_tampered_in_either_copy_is_reported(self):
        # The first entry keeps the plain path and the second is
        # disambiguated, so neither can shelter behind the other.
        for which, expected in (
            (0, "STALE report_ok.json contributors[bob].counts.rewarded "
                "expected=1 found=999"),
            (1, "UNEXPECTED report_ok.json contributors[bob#2].counts.rewarded "
                "not produced by the recomputation in check_counts.py"),
        ):
            with self.subTest(entry=which):
                with TempCopy() as copy:
                    self.duplicate(copy, which)
                    problems, _ = copy.run()
                self.assertIn(expected, problems)


class TestCanonicalForm(unittest.TestCase):
    """A reformatted report no longer reproduces, whatever it says."""

    def assert_only_format_problem(self, problems):
        self.assertEqual(len(problems), 1, problems)
        self.assertTrue(problems[0].startswith(
            "FORMAT report_ok.json the file is not the canonical rendering"),
            problems)

    def test_a_pretty_printed_report_is_reported(self):
        with TempCopy() as copy:
            report = copy.load("report_ok.json")
            with open(copy.path("report_ok.json"), "w", encoding="utf-8") as fh:
                fh.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
            problems, checked = copy.run()
        self.assertEqual(checked, EXPECTED_PATHS_CHECKED)
        self.assert_only_format_problem(problems)

    def test_a_duplicated_json_key_is_reported(self):
        # json.loads keeps the last of two identical keys, so every value
        # still compares equal; only the bytes give it away.
        with TempCopy() as copy:
            with open(copy.path("report_ok.json"), encoding="utf-8") as fh:
                text = fh.read()
            with open(copy.path("report_ok.json"), "w", encoding="utf-8") as fh:
                fh.write('{"report_version":"0.0",' + text[1:])
            problems, _ = copy.run()
        self.assert_only_format_problem(problems)

    def test_a_missing_trailing_newline_is_reported(self):
        with TempCopy() as copy:
            with open(copy.path("report_ok.json"), encoding="utf-8") as fh:
                text = fh.read()
            with open(copy.path("report_ok.json"), "w", encoding="utf-8") as fh:
                fh.write(text.rstrip("\n"))
            problems, _ = copy.run()
        self.assert_only_format_problem(problems)


class TestCoverageIsByConstruction(unittest.TestCase):

    def test_a_value_the_recomputation_does_not_produce_is_unexpected(self):
        with TempCopy() as copy:
            report = copy.load("report_ok.json")
            report["contributors"][0]["task_count"] = 3
            report["totals"]["brand_new"] = 7
            report["a_whole_new_block"] = {"x": 1}
            report["a_list"] = [10, 11]
            copy.save("report_ok.json", report)
            problems, _ = copy.run()
        who = read(os.path.join(HERE, "report_ok.json"))["contributors"][0]["contributor"]
        self.assertEqual(sorted(problems), sorted([
            "UNEXPECTED report_ok.json a_list[0] not produced by the "
            "recomputation in check_counts.py",
            "UNEXPECTED report_ok.json a_list[1] not produced by the "
            "recomputation in check_counts.py",
            "UNEXPECTED report_ok.json a_whole_new_block.x not produced by "
            "the recomputation in check_counts.py",
            "UNEXPECTED report_ok.json totals.brand_new not produced by "
            "the recomputation in check_counts.py",
            "UNEXPECTED report_ok.json contributors[%s].task_count not "
            "produced by the recomputation in check_counts.py" % who,
        ]))

    def test_the_recomputation_produces_every_committed_path(self):
        # The other half of "no exemptions": nothing in a committed report
        # is skipped, so the guard above has nothing to be lenient about.
        for events_name, report_name, cfg in C.PAIRS:
            with self.subTest(report_name):
                events = read(os.path.join(HERE, events_name))
                want, _, _ = C.flat_report(
                    report_name, C.recompute(events, cfg, events_name))
                found, _, _ = C.flat_report(
                    report_name, read(os.path.join(HERE, report_name)))
                self.assertEqual(sorted(want), sorted(found))

    def test_the_pinned_flags_are_the_documented_ones(self):
        # README.md's rerun block produces all three reports with no
        # flags, so the pinned values must be argparse's own defaults,
        # read off throughput.py rather than restated here.
        defaults = {}
        with open(os.path.join(HERE, "throughput.py"), encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if getattr(node.func, "attr", None) != "add_argument":
                continue
            flag = node.args[-1].value if node.args else None
            for keyword in node.keywords:
                if keyword.arg == "default":
                    defaults[flag] = keyword.value.value
        self.assertEqual(defaults.get("--refusal-ceiling"), 0.5)
        self.assertEqual(defaults.get("--min-tasks"), 2)
        for _, report_name, cfg in C.PAIRS:
            with self.subTest(report_name):
                self.assertEqual(cfg, {"refusal_ceiling": 0.5, "min_tasks": 2})


class TestRecomputationRule(unittest.TestCase):
    """The parts of the rule that are easy to get wrong, stated directly."""

    CFG = {"refusal_ceiling": 0.5, "min_tasks": 2}

    def counts(self, events):
        return C.recompute(events, self.CFG, "inline")

    def ev(self, task, who, state, when):
        return {"task_id": task, "contributor": who, "state": state,
                "occurred_at": when}

    def test_rewarded_wins_over_refused_on_one_task(self):
        out = self.counts([
            self.ev("t1", "a", "rewarded", "2026-07-01T00:00:00Z"),
            self.ev("t1", "a", "refused", "2026-07-01T01:00:00Z"),
        ])
        counts = out["contributors"][0]["counts"]
        self.assertEqual(counts["rewarded"], 1)
        self.assertEqual(counts["refused"], 0)
        self.assertEqual(counts["terminal"], 1)

    def test_one_task_touched_by_two_contributors_counts_once_each(self):
        out = self.counts([
            self.ev("t1", "a", "accepted", "2026-07-01T00:00:00Z"),
            self.ev("t1", "b", "accepted", "2026-07-01T00:00:00Z"),
        ])
        self.assertEqual(out["totals"], {"contributors": 2, "events": 2,
                                         "over_ceiling": 0})
        for entry in out["contributors"]:
            self.assertEqual(entry["counts"]["tasks_seen"], 1)

    def test_the_first_occurrence_of_a_state_is_the_one_used(self):
        out = self.counts([
            self.ev("t1", "a", "accepted", "2026-07-01T00:00:00Z"),
            self.ev("t1", "a", "submitted", "2026-07-01T04:00:00Z"),
            self.ev("t1", "a", "verification_requested", "2026-07-01T05:00:00Z"),
            self.ev("t1", "a", "submitted", "2026-07-09T04:00:00Z"),
            self.ev("t1", "a", "rewarded", "2026-07-10T04:00:00Z"),
        ])
        entry = out["contributors"][0]
        self.assertEqual(entry["counts"]["submitted"], 1)
        self.assertEqual(entry["median_accept_to_submit_hours"], 4.0)

    def test_a_timestamp_without_a_zone_is_read_as_utc(self):
        out = self.counts([
            self.ev("t1", "a", "accepted", "2026-07-01T00:00:00"),
            self.ev("t1", "a", "submitted", "2026-07-01T06:00:00Z"),
        ])
        self.assertEqual(
            out["contributors"][0]["median_accept_to_submit_hours"], 6.0)

    def test_a_zone_less_timestamp_is_utc_whatever_the_local_zone_is(self):
        # `replace(tzinfo=utc)` and `astimezone(utc)` agree only on a
        # machine already running on UTC, which this one is, so the
        # question has to be asked of a subprocess in another zone.
        program = (
            "import check_counts as C\n"
            "events = ["
            "{'task_id': 't1', 'contributor': 'a', 'state': 'accepted',"
            " 'occurred_at': '2026-07-01T00:00:00'},"
            "{'task_id': 't1', 'contributor': 'a', 'state': 'submitted',"
            " 'occurred_at': '2026-07-01T06:00:00Z'}]\n"
            "out = C.recompute(events, {'refusal_ceiling': 0.5,"
            " 'min_tasks': 2}, 'inline')\n"
            "print(out['contributors'][0]"
            "['median_accept_to_submit_hours'])\n")
        for zone in ("UTC", "Etc/GMT-5", "Etc/GMT+9"):
            with self.subTest(zone):
                env = dict(os.environ, TZ=zone, PYTHONDONTWRITEBYTECODE="1")
                env.pop("PYTHONUNBUFFERED", None)
                proc = subprocess.run([sys.executable, "-c", program],
                                      cwd=HERE, env=env,
                                      capture_output=True, text=True)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertEqual(proc.stdout.strip(), "6.0")

    def test_an_offset_is_honoured_not_stripped(self):
        out = self.counts([
            self.ev("t1", "a", "accepted", "2026-07-01T00:00:00-05:00"),
            self.ev("t1", "a", "submitted", "2026-07-01T06:00:00Z"),
        ])
        self.assertEqual(
            out["contributors"][0]["median_accept_to_submit_hours"], 1.0)

    def test_the_median_keeps_four_decimal_places(self):
        # One minute is 0.0166666... hours. Rounding to 2dp would make it
        # 0.02, and every committed median here happens to be a whole
        # number, so nothing else in this directory pins the precision.
        out = self.counts([
            self.ev("t1", "a", "accepted", "2026-07-01T00:00:00Z"),
            self.ev("t1", "a", "submitted", "2026-07-01T00:01:00Z"),
        ])
        self.assertEqual(
            out["contributors"][0]["median_accept_to_submit_hours"], 0.0167)

    def test_grade_a_includes_both_of_its_boundaries(self):
        # One refusal in ten is exactly 0.1 and a day is exactly 24
        # hours; README.md documents both comparisons as inclusive.
        events = []
        for i in range(10):
            task = "t%d" % i
            events.append(self.ev(task, "a", "accepted", "2026-07-01T00:00:00Z"))
            events.append(self.ev(task, "a", "submitted", "2026-07-02T00:00:00Z"))
            events.append(self.ev(task, "a", "refused" if i == 0 else "rewarded",
                                  "2026-07-03T00:00:00Z"))
        entry = self.counts(events)["contributors"][0]
        self.assertEqual(entry["refusal_rate"], 0.1)
        self.assertEqual(entry["median_accept_to_submit_hours"], 24.0)
        self.assertEqual(entry["grade"], "A")

    def test_a_negative_duration_is_dropped_not_clamped(self):
        out = self.counts([
            self.ev("t1", "a", "accepted", "2026-07-02T00:00:00Z"),
            self.ev("t1", "a", "submitted", "2026-07-01T00:00:00Z"),
        ])
        self.assertIsNone(out["contributors"][0]["median_accept_to_submit_hours"])

    def test_no_terminal_outcome_is_zero_not_a_divide_by_zero(self):
        out = self.counts([self.ev("t1", "a", "accepted", "2026-07-01T00:00:00Z")])
        entry = out["contributors"][0]
        self.assertEqual(entry["refusal_rate"], 0.0)
        self.assertEqual(entry["grade"], "INSUFFICIENT_DATA")
        self.assertFalse(entry["over_ceiling"])

    def test_contributors_come_back_worst_first(self):
        events = []
        for who, refusals in (("clean", 0), ("half", 1), ("all", 2)):
            for i in range(2):
                task = "%s%d" % (who, i)
                events.append(self.ev(task, who, "accepted", "2026-07-01T00:00:00Z"))
                events.append(self.ev(
                    task, who, "refused" if i < refusals else "rewarded",
                    "2026-07-02T00:00:00Z"))
        out = self.counts(events)
        self.assertEqual([c["contributor"] for c in out["contributors"]],
                         ["all", "half", "clean"])
        self.assertEqual(out["status"], "ceiling_breach")

    def test_an_empty_fixture_is_a_legal_report(self):
        out = self.counts([])
        self.assertEqual(out["totals"],
                         {"contributors": 0, "events": 0, "over_ceiling": 0})
        self.assertEqual(out["grade_counts"], {})
        self.assertEqual(out["status"], "ok")


class TestAgreementWithTheTool(unittest.TestCase):
    """check_counts.py never imports throughput.py; this is the bridge."""

    SIZES = (0, 1, 2, 3, 5, 8, 13, 21, 34)
    CONFIGS = ((0.5, 2), (0.1, 1), (0.99, 3))
    ROUNDS = 12
    NAMES = ("alice", "bob", "carol", "  bob", "Bob", "zelie", "zélie")
    STATES = tuple(sorted(T.KNOWN_STATES))
    HOURS = (0, 1, 5, 24, 48, 100, -6)
    #: Whole-hour UTC, an explicit offset, a zone-less stamp, and one with
    #: sub-second precision, so the bridge is not only exercising `Z`.
    STAMPS = ("%sT%02d:00:00Z", "%sT%02d:00:00+00:00", "%sT%02d:00:00-05:00",
              "%sT%02d:00:00", "%sT%02d:00:00.500000Z")

    def fixture(self, seq, size):
        events = []
        for _ in range(size):
            hour = seq.pick(self.HOURS) + seq.below(72)
            day = "2026-07-%02d" % (1 + hour // 24 % 28)
            events.append({
                "task_id": "t%d" % (1 + seq.below(5)),
                "contributor": seq.pick(self.NAMES),
                "state": seq.pick(self.STATES),
                "occurred_at": seq.pick(self.STAMPS) % (day, hour % 24),
            })
        return events

    def agree(self, events, cfg, label):
        report = json.loads(run_the_tool(events, cfg))
        expected = C.recompute(events, cfg, label)
        problems, _ = C.compare(label, report, None, expected)
        self.assertEqual(problems, [], json.dumps(events))

    def test_recomputation_agrees_with_the_tool_on_generated_fixtures(self):
        seq = Sequence(20260808)
        runs = 0
        distinct = set()
        for size in self.SIZES:
            for ceiling, min_tasks in self.CONFIGS:
                for _ in range(self.ROUNDS):
                    events = self.fixture(seq, size)
                    distinct.add(json.dumps(events, sort_keys=True))
                    runs += 1
                    with self.subTest(size=size, ceiling=ceiling,
                                      min_tasks=min_tasks, run=runs):
                        self.agree(events, {"refusal_ceiling": ceiling,
                                            "min_tasks": min_tasks},
                                   "generated.json")
        self.assertEqual(runs, len(self.SIZES) * len(self.CONFIGS) * self.ROUNDS)
        # Both numbers are quoted in README.md. Small sizes repeat, so
        # runs and distinct fixtures are not the same figure.
        self.assertEqual((runs, len(distinct)), (324, 289))

    def test_recomputation_agrees_with_the_tool_on_shaped_fixtures(self):
        # The generator above never lands on a grade A, and grade A is the
        # one branch with a `None` guard in it. These shapes reach every
        # grade and both tie-breaks.
        def task(who, tid, accept, submit, term, state):
            out = [{"task_id": tid, "contributor": who, "state": "accepted",
                    "occurred_at": accept}]
            if submit:
                out.append({"task_id": tid, "contributor": who,
                            "state": "submitted", "occurred_at": submit})
            if term:
                out.append({"task_id": tid, "contributor": who, "state": state,
                            "occurred_at": term})
            return out

        fast = []
        for i in range(10):
            fast += task("fast", "f%d" % i, "2026-07-01T00:00:00Z",
                         "2026-07-01T06:00:00Z", "2026-07-02T00:00:00Z",
                         "refused" if i == 0 else "rewarded")
        slow = []
        for i in range(4):
            slow += task("slow", "s%d" % i, "2026-07-01T00:00:00Z",
                         "2026-07-05T00:00:00Z", "2026-07-06T00:00:00Z",
                         "rewarded")
        mixed = []
        for i in range(4):
            mixed += task("mixed", "m%d" % i, "2026-07-01T00:00:00Z",
                          "2026-07-02T00:00:00Z", "2026-07-03T00:00:00Z",
                          "refused" if i < 3 else "rewarded")
        tied_a = task("tied_a", "x", "2026-07-01T00:00:00Z",
                      "2026-07-02T00:00:00Z", "2026-07-03T00:00:00Z", "refused")
        tied_a += task("tied_a", "y", "2026-07-01T00:00:00Z",
                       "2026-07-02T00:00:00Z", "2026-07-03T00:00:00Z", "rewarded")
        tied_b = [dict(event, contributor="tied_b") for event in tied_a]
        never = task("never", "n1", "2026-07-01T00:00:00Z", None, None, None)

        shapes = {
            "grade_a_fast": fast,
            "grade_b_slow": slow,
            "grade_d_mixed": mixed,
            "tied_rates": tied_a + tied_b,
            "no_terminal": never,
            "everything": fast + slow + mixed + tied_a + tied_b + never,
        }
        seen = set()
        for label in sorted(shapes):
            for ceiling, min_tasks in self.CONFIGS:
                cfg = {"refusal_ceiling": ceiling, "min_tasks": min_tasks}
                with self.subTest(shape=label, ceiling=ceiling,
                                  min_tasks=min_tasks):
                    self.agree(shapes[label], cfg, label + ".json")
                seen.update(C.recompute(shapes[label], cfg, label)["grade_counts"])
        self.assertEqual(seen, {"A", "B", "C", "D", "INSUFFICIENT_DATA"})

    def test_the_committed_reports_are_what_the_tool_produces_now(self):
        # Belt and braces: the check agreeing with the reports is only
        # meaningful if the reports are also what the tool emits. The
        # flags come from PAIRS, not from the report being validated.
        for events_name, report_name, cfg in C.PAIRS:
            with self.subTest(report_name):
                events = read(os.path.join(HERE, events_name))
                with open(os.path.join(HERE, report_name), encoding="utf-8") as fh:
                    committed = fh.read()
                self.assertEqual(committed, run_the_tool(events, cfg))


class TestBadInput(unittest.TestCase):

    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, os.path.join(HERE, "check_counts.py")] + list(args),
            capture_output=True, text=True)

    def assert_invalid(self, proc, fragment):
        self.assertEqual(proc.returncode, 2, proc.stdout)
        self.assertEqual(proc.stdout, "")
        self.assertIn("INVALID_INPUT", proc.stderr)
        self.assertIn(fragment, proc.stderr)

    def test_missing_directory_exits_two(self):
        self.assert_invalid(
            self.run_cli(os.path.join(HERE, "no_such_directory_here")),
            "file not found: events_ok.json")

    def test_a_file_where_a_directory_belongs_exits_two(self):
        self.assert_invalid(self.run_cli(os.path.join(HERE, "throughput.py")),
                            "cannot read events_ok.json")

    def test_a_fixture_that_is_not_utf8_exits_two(self):
        with TempCopy() as copy:
            with open(copy.path("events_ok.json"), "wb") as fh:
                fh.write(b"\xff\xfe[]")
            self.assert_invalid(self.run_cli(copy.dir), "is not UTF-8")

    def test_malformed_json_exits_two(self):
        with TempCopy() as copy:
            with open(copy.path("report_ok.json"), "w", encoding="utf-8") as fh:
                fh.write("{not json")
            self.assert_invalid(self.run_cli(copy.dir),
                                "invalid JSON in report_ok.json")

    def test_a_file_of_only_whitespace_exits_two(self):
        with TempCopy() as copy:
            with open(copy.path("report_ok.json"), "w", encoding="utf-8") as fh:
                fh.write("   \n")
            self.assert_invalid(self.run_cli(copy.dir),
                                "invalid JSON in report_ok.json")

    def test_a_fixture_that_is_not_an_array_exits_two(self):
        with TempCopy() as copy:
            copy.save("events_ok.json", {"events": []})
            self.assert_invalid(self.run_cli(copy.dir), "expected a JSON array")

    def test_a_report_that_is_not_an_object_exits_two(self):
        with TempCopy() as copy:
            copy.save("report_ok.json", [])
            self.assert_invalid(self.run_cli(copy.dir),
                                "expected a JSON object, got list")

    def test_a_report_without_contributors_exits_two(self):
        with TempCopy() as copy:
            report = copy.load("report_ok.json")
            del report["contributors"]
            copy.save("report_ok.json", report)
            self.assert_invalid(self.run_cli(copy.dir), "no contributors array")

    def test_a_contributor_entry_that_is_not_an_object_exits_two(self):
        with TempCopy() as copy:
            report = copy.load("report_ok.json")
            report["contributors"][0] = "bob"
            copy.save("report_ok.json", report)
            self.assert_invalid(self.run_cli(copy.dir),
                                "contributors[0] is not an object")

    def test_a_fixture_the_tool_would_refuse_exits_two_not_one(self):
        # Reporting expected values computed from a fixture the tool
        # itself rejects would blame the report for a broken input.
        for edit, fragment in (
            ({"state": "queued"}, "unknown state"),
            ({"contributor": "  "}, "must be a non-empty string"),
            ({"occurred_at": "not-a-date"}, "not ISO-8601"),
        ):
            with self.subTest(fragment):
                with TempCopy() as copy:
                    events = copy.load("events_ok.json")
                    events[0].update(edit)
                    copy.save("events_ok.json", events)
                    proc = self.run_cli(copy.dir)
                self.assert_invalid(proc, fragment)

    def test_a_fixture_record_missing_a_field_exits_two(self):
        with TempCopy() as copy:
            events = copy.load("events_ok.json")
            del events[0]["task_id"]
            copy.save("events_ok.json", events)
            self.assert_invalid(self.run_cli(copy.dir),
                                'missing required field "task_id"')

    def test_too_many_arguments_exits_two(self):
        for args in ((HERE, HERE), ("-h", "extra"), ("--help", HERE)):
            with self.subTest(args):
                proc = self.run_cli(*args)
                self.assertEqual(proc.returncode, 2, proc.stdout)
                self.assertIn("usage:", proc.stderr)

    def test_help_exits_zero(self):
        for flag in ("-h", "--help"):
            with self.subTest(flag):
                proc = self.run_cli(flag)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertIn("Usage: python3 check_counts.py", proc.stdout)


class TestInvocationShape(unittest.TestCase):
    """The environment constraints this repository commits under."""

    SOURCE = os.path.join(HERE, "check_counts.py")

    def imported_modules(self):
        """Every module check_counts.py imports, at any nesting level.

        Parsed rather than read off `sys.modules`, which is
        process-global: running this suite alongside test_throughput
        imports throughput into the same interpreter, so membership there
        says nothing about what this file does. Line matching would not do
        either -- a docstring line here begins with the word "from".
        """
        with open(self.SOURCE, "r", encoding="utf-8") as fh:
            tree = ast.parse(fh.read(), filename=self.SOURCE)
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    names.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                names.add((node.module or "").split(".")[0])
        return sorted(names)

    def test_the_check_does_not_import_the_tool_it_checks(self):
        self.assertNotIn("throughput", self.imported_modules())

    def test_the_check_uses_only_the_standard_library(self):
        self.assertEqual(self.imported_modules(),
                         ["datetime", "json", "os", "statistics", "sys"])

    def test_neither_new_file_carries_a_shebang(self):
        # Files land at mode 100644 through GitHub's web upload, and this
        # repository's shebang-mode scanner counts a shebang on a
        # non-executable file as a finding. Both are run as
        # `python3 <file>`.
        for name in ("check_counts.py", "test_check_counts.py"):
            with self.subTest(name):
                with open(os.path.join(HERE, name), "rb") as fh:
                    self.assertNotEqual(fh.read(2), b"#!")


if __name__ == "__main__":
    unittest.main()
