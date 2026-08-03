#!/usr/bin/env python3
"""Tests for the Contributor Throughput and Reliability Reporter."""
import json, os, subprocess, sys, tempfile, unittest
from datetime import timedelta
import throughput as T

HERE = os.path.dirname(os.path.abspath(__file__))


def cfg(ceiling=0.5, min_tasks=2):
    return {"refusal_ceiling": ceiling, "min_tasks": min_tasks}


def ev(task, who, state, ts):
    return {"task_id": task, "contributor": who, "state": state, "occurred_at": ts}


def load(recs):
    fh = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
    json.dump(recs, fh); fh.close()
    try:
        return T.load_events(fh.name)
    finally:
        os.unlink(fh.name)


def by_name(rep):
    return {c["contributor"]: c for c in rep["contributors"]}


class TestCounts(unittest.TestCase):
    def test_counts_per_outcome(self):
        rep = T.analyze(T.load_events(os.path.join(HERE, "events_ok.json")), cfg())
        bob = by_name(rep)["bob"]
        self.assertEqual(bob["counts"]["rewarded"], 1)
        self.assertEqual(bob["counts"]["refused"], 1)
        self.assertEqual(bob["counts"]["terminal"], 2)

    def test_tasks_seen_counts_distinct_tasks(self):
        rep = T.analyze(T.load_events(os.path.join(HERE, "events_ok.json")), cfg())
        self.assertEqual(by_name(rep)["alice"]["counts"]["tasks_seen"], 2)

    def test_duplicate_state_uses_first_occurrence(self):
        evs = load([ev("t", "x", "accepted", "2026-01-01T00:00:00Z"),
                    ev("t", "x", "submitted", "2026-01-01T02:00:00Z"),
                    ev("t", "x", "submitted", "2026-01-01T09:00:00Z"),
                    ev("t", "x", "rewarded", "2026-01-01T10:00:00Z")])
        rep = T.analyze(evs, cfg(min_tasks=1))
        self.assertEqual(by_name(rep)["x"]["median_accept_to_submit_hours"], 2.0)

    def test_contributor_with_only_accept(self):
        rep = T.analyze(T.load_events(os.path.join(HERE, "events_ok.json")), cfg())
        carol = by_name(rep)["carol"]
        self.assertEqual(carol["counts"]["terminal"], 0)
        self.assertEqual(carol["refusal_rate"], 0.0)


class TestRefusalRate(unittest.TestCase):
    def test_half_refused(self):
        rep = T.analyze(T.load_events(os.path.join(HERE, "events_ok.json")), cfg())
        self.assertEqual(by_name(rep)["bob"]["refusal_rate"], 0.5)

    def test_zero_refused(self):
        rep = T.analyze(T.load_events(os.path.join(HERE, "events_ok.json")), cfg())
        self.assertEqual(by_name(rep)["alice"]["refusal_rate"], 0.0)

    def test_no_terminal_no_divide_by_zero(self):
        rep = T.analyze(load([ev("t", "solo", "accepted", "2026-01-01T00:00:00Z")]), cfg())
        self.assertEqual(by_name(rep)["solo"]["refusal_rate"], 0.0)

    def test_two_thirds_refused(self):
        rep = T.analyze(T.load_events(os.path.join(HERE, "events_breach.json")), cfg())
        self.assertAlmostEqual(by_name(rep)["dave"]["refusal_rate"], 2 / 3, places=5)


class TestMedians(unittest.TestCase):
    def test_median_of_two(self):
        rep = T.analyze(T.load_events(os.path.join(HERE, "events_ok.json")), cfg())
        self.assertEqual(by_name(rep)["alice"]["median_accept_to_submit_hours"], 8.0)

    def test_median_submit_to_terminal(self):
        rep = T.analyze(T.load_events(os.path.join(HERE, "events_ok.json")), cfg())
        self.assertEqual(by_name(rep)["alice"]["median_submit_to_terminal_hours"], 21.0)

    def test_none_when_no_pairs(self):
        rep = T.analyze(load([ev("t", "solo", "accepted", "2026-01-01T00:00:00Z")]), cfg())
        self.assertIsNone(by_name(rep)["solo"]["median_accept_to_submit_hours"])

    def test_negative_duration_excluded(self):
        """A submitted timestamp before accepted is data corruption, not a -5h median."""
        evs = load([ev("t", "x", "accepted", "2026-01-02T00:00:00Z"),
                    ev("t", "x", "submitted", "2026-01-01T00:00:00Z")])
        rep = T.analyze(evs, cfg(min_tasks=1))
        self.assertIsNone(by_name(rep)["x"]["median_accept_to_submit_hours"])

    def test_median_of_three_is_middle(self):
        evs = []
        for i, h in enumerate((1, 5, 100)):
            evs += [ev(f"t{i}", "x", "accepted", "2026-01-01T00:00:00Z"),
                    ev(f"t{i}", "x", "submitted", f"2026-01-01T{h:02d}:00:00Z" if h < 24 else "2026-01-05T04:00:00Z")]
        rep = T.analyze(load(evs), cfg(min_tasks=1))
        self.assertEqual(by_name(rep)["x"]["median_accept_to_submit_hours"], 5.0)


class TestGrading(unittest.TestCase):
    def test_grade_a_fast_and_clean(self):
        self.assertEqual(T.grade(0.0, 6.0, 5, cfg()), "A")

    def test_grade_b_clean_but_slow(self):
        self.assertEqual(T.grade(0.0, 100.0, 5, cfg()), "B")

    def test_grade_c_moderate_refusals(self):
        self.assertEqual(T.grade(0.4, 6.0, 5, cfg()), "C")

    def test_grade_d_above_ceiling(self):
        self.assertEqual(T.grade(0.9, 6.0, 5, cfg()), "D")

    def test_insufficient_data_wins(self):
        self.assertEqual(T.grade(0.0, 1.0, 1, cfg(min_tasks=2)), "INSUFFICIENT_DATA")

    def test_ceiling_is_configurable(self):
        self.assertEqual(T.grade(0.4, 6.0, 5, cfg(ceiling=0.3)), "D")


class TestBreachAndStatus(unittest.TestCase):
    def test_breach_detected(self):
        rep = T.analyze(T.load_events(os.path.join(HERE, "events_breach.json")), cfg())
        self.assertEqual(rep["status"], "ceiling_breach")
        self.assertTrue(by_name(rep)["dave"]["over_ceiling"])

    def test_clean_status(self):
        rep = T.analyze(T.load_events(os.path.join(HERE, "events_ok.json")), cfg())
        self.assertEqual(rep["status"], "ok")

    def test_insufficient_data_never_breaches(self):
        """One refusal out of one task must not brand a newcomer as over ceiling."""
        evs = load([ev("t", "new", "accepted", "2026-01-01T00:00:00Z"),
                    ev("t", "new", "submitted", "2026-01-01T01:00:00Z"),
                    ev("t", "new", "refused", "2026-01-01T02:00:00Z")])
        rep = T.analyze(evs, cfg(min_tasks=2))
        self.assertFalse(by_name(rep)["new"]["over_ceiling"])
        self.assertEqual(rep["status"], "ok")

    def test_sorted_worst_first(self):
        rep = T.analyze(T.load_events(os.path.join(HERE, "events_breach.json")), cfg())
        rates = [c["refusal_rate"] for c in rep["contributors"]]
        self.assertEqual(rates, sorted(rates, reverse=True))


class TestMalformed(unittest.TestCase):
    def test_missing_field(self):
        with self.assertRaises(T.InputError):
            load([{"task_id": "t", "contributor": "x", "state": "accepted"}])

    def test_unknown_state(self):
        with self.assertRaises(T.InputError):
            load([ev("t", "x", "teleported", "2026-01-01T00:00:00Z")])

    def test_bad_timestamp(self):
        with self.assertRaises(T.InputError):
            load([ev("t", "x", "accepted", "yesterday")])

    def test_non_array(self):
        fh = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        json.dump({"a": 1}, fh); fh.close()
        try:
            with self.assertRaises(T.InputError):
                T.load_events(fh.name)
        finally:
            os.unlink(fh.name)


class TestCli(unittest.TestCase):
    def _cli(self, *a):
        return subprocess.run([sys.executable, os.path.join(HERE, "throughput.py"), *a],
                              capture_output=True, text=True)

    def test_ok_exit_zero(self):
        p = self._cli(os.path.join(HERE, "events_ok.json"))
        self.assertEqual(p.returncode, 0)

    def test_breach_exit_one(self):
        p = self._cli(os.path.join(HERE, "events_breach.json"))
        self.assertEqual(p.returncode, 1)

    def test_missing_file_exit_two(self):
        p = self._cli("/nonexistent.json")
        self.assertEqual(p.returncode, 2)
        self.assertIn("INVALID_INPUT", p.stderr)

    def test_repeated_runs_identical(self):
        f = os.path.join(HERE, "events_breach.json")
        self.assertEqual(self._cli(f).stdout, self._cli(f).stdout)

    def test_raising_ceiling_clears_breach(self):
        p = self._cli(os.path.join(HERE, "events_breach.json"), "--refusal-ceiling", "0.99")
        self.assertEqual(p.returncode, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
