#!/usr/bin/env python3
"""Tests for the JSON-array Task Lifecycle Event Linter."""
import json, os, subprocess, sys, unittest
import event_lint as E

HERE = os.path.dirname(os.path.abspath(__file__))


def ev(tid, state, ts, idx=0):
    return {"index": idx, "task_id": tid, "state": state, "occurred_at": ts}


def vios(findings):
    return sorted({f["violation"] for f in findings})


class TestValidSequences(unittest.TestCase):
    def test_full_path_clean(self):
        h = [ev("t", "proposed", "1", 0), ev("t", "accepted", "2", 1),
             ev("t", "submitted", "3", 2), ev("t", "verification_requested", "4", 3),
             ev("t", "rewarded", "5", 4)]
        self.assertEqual(E.lint_task("t", h), [])

    def test_resubmission_loop_clean(self):
        h = [ev("t", "proposed", "1", 0), ev("t", "accepted", "2", 1),
             ev("t", "submitted", "3", 2), ev("t", "verification_requested", "4", 3),
             ev("t", "submitted", "5", 4), ev("t", "rewarded", "6", 5)]
        self.assertEqual(E.lint_task("t", h), [])

    def test_early_refuse_clean(self):
        h = [ev("t", "proposed", "1", 0), ev("t", "refused", "2", 1)]
        self.assertEqual(E.lint_task("t", h), [])

    def test_submitted_to_rewarded_clean(self):
        h = [ev("t", "proposed", "1", 0), ev("t", "accepted", "2", 1),
             ev("t", "submitted", "3", 2), ev("t", "rewarded", "4", 3)]
        self.assertEqual(E.lint_task("t", h), [])


class TestViolationClasses(unittest.TestCase):
    def test_illegal_transition(self):
        h = [ev("t", "proposed", "1", 0), ev("t", "submitted", "2", 1)]
        self.assertIn(E.ILLEGAL_TRANSITION, vios(E.lint_task("t", h)))

    def test_post_terminal(self):
        h = [ev("t", "proposed", "1", 0), ev("t", "accepted", "2", 1),
             ev("t", "submitted", "3", 2), ev("t", "rewarded", "4", 3),
             ev("t", "submitted", "5", 4)]
        self.assertIn(E.POST_TERMINAL_EVENT, vios(E.lint_task("t", h)))

    def test_timestamp_disorder(self):
        h = [ev("t", "proposed", "2026-01-02", 0), ev("t", "accepted", "2026-01-01", 1)]
        self.assertIn(E.TIMESTAMP_DISORDER, vios(E.lint_task("t", h)))

    def test_duplicate_event(self):
        h = [ev("t", "proposed", "1", 0), ev("t", "accepted", "2", 1),
             ev("t", "accepted", "2", 2)]
        self.assertIn(E.DUPLICATE_EVENT, vios(E.lint_task("t", h)))

    def test_missing_proposed(self):
        h = [ev("t", "accepted", "1", 0), ev("t", "submitted", "2", 1)]
        self.assertIn(E.MISSING_PROPOSED, vios(E.lint_task("t", h)))

    def test_same_state_different_timestamp_not_duplicate(self):
        h = [ev("t", "proposed", "1", 0), ev("t", "accepted", "2", 1),
             ev("t", "accepted", "3", 2)]
        self.assertNotIn(E.DUPLICATE_EVENT, vios(E.lint_task("t", h)))

    def test_post_terminal_suppresses_transition_noise(self):
        h = [ev("t", "proposed", "1", 0), ev("t", "refused", "2", 1),
             ev("t", "accepted", "3", 2)]
        v = [f["violation"] for f in E.lint_task("t", h)]
        self.assertEqual(v, [E.POST_TERMINAL_EVENT])


class TestMalformed(unittest.TestCase):
    def test_non_object(self):
        _, bad = E.partition(["nope"])
        self.assertEqual(bad[0]["violation"], E.MALFORMED_EVENT)

    def test_missing_fields(self):
        _, bad = E.partition([{"task_id": "t", "state": "proposed"}])
        self.assertIn("missing field", bad[0]["detail"])

    def test_blank_field(self):
        _, bad = E.partition([{"task_id": "t", "state": "   ", "occurred_at": "1"}])
        self.assertEqual(bad[0]["violation"], E.MALFORMED_EVENT)

    def test_unknown_state(self):
        _, bad = E.partition([{"task_id": "t", "state": "warp", "occurred_at": "1"}])
        self.assertEqual(bad[0]["violation"], E.UNKNOWN_STATE)

    def test_index_preserved(self):
        _, bad = E.partition([{"task_id": "t", "state": "proposed", "occurred_at": "1"}, "bad"])
        self.assertEqual(bad[0]["index"], 1)

    def test_non_array_raises(self):
        import tempfile
        fh = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        json.dump({"a": 1}, fh); fh.close()
        try:
            with self.assertRaises(E.InputError):
                E.load_events(fh.name)
        finally:
            os.unlink(fh.name)


class TestReportShape(unittest.TestCase):
    def test_tasks_sorted_and_grouped(self):
        clean, bad = E.partition([
            {"task_id": "zzz", "state": "proposed", "occurred_at": "1"},
            {"task_id": "aaa", "state": "proposed", "occurred_at": "1"},
        ])
        r = E.build_report(clean, bad)
        self.assertEqual([t["task_id"] for t in r["tasks"]], ["aaa", "zzz"])

    def test_per_task_counts(self):
        clean, bad = E.partition([
            {"task_id": "t", "state": "proposed", "occurred_at": "1"},
            {"task_id": "t", "state": "submitted", "occurred_at": "2"},
        ])
        r = E.build_report(clean, bad)
        self.assertEqual(r["tasks"][0]["event_count"], 2)
        self.assertEqual(r["tasks"][0]["violation_count"], 1)

    def test_clean_status(self):
        self.assertEqual(E.build_report([], [])["status"], "clean")

    def test_serialize_repeatable(self):
        clean, bad = E.partition([{"task_id": "t", "state": "submitted", "occurred_at": "1"}])
        r = E.build_report(clean, bad)
        self.assertEqual(E.serialize(r), E.serialize(r))


class TestCli(unittest.TestCase):
    def _cli(self, *a):
        return subprocess.run([sys.executable, os.path.join(HERE, "event_lint.py"), *a],
                              capture_output=True, text=True)

    def test_valid_exit_zero(self):
        p = self._cli(os.path.join(HERE, "events_valid.json"))
        self.assertEqual(p.returncode, 0)
        self.assertEqual(json.loads(p.stdout)["status"], "clean")

    def test_invalid_exit_one(self):
        p = self._cli(os.path.join(HERE, "events_invalid.json"))
        self.assertEqual(p.returncode, 1)

    def test_missing_file_exit_two(self):
        p = self._cli("/nonexistent.json")
        self.assertEqual(p.returncode, 2)
        self.assertIn("UNREADABLE_INPUT", p.stderr)

    def test_repeated_runs_identical(self):
        f = os.path.join(HERE, "events_invalid.json")
        self.assertEqual(self._cli(f).stdout, self._cli(f).stdout)

    def test_all_violation_classes_covered(self):
        p = self._cli(os.path.join(HERE, "events_invalid.json"))
        got = set(json.loads(p.stdout)["violation_counts"])
        for c in (E.ILLEGAL_TRANSITION, E.POST_TERMINAL_EVENT, E.TIMESTAMP_DISORDER,
                  E.DUPLICATE_EVENT, E.MISSING_PROPOSED, E.MALFORMED_EVENT, E.UNKNOWN_STATE):
            self.assertIn(c, got, f"{c} not exercised")


if __name__ == "__main__":
    unittest.main(verbosity=2)
