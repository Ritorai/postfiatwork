#!/usr/bin/env python3
"""Tests for the Task Lifecycle Event Linter."""
import json, os, subprocess, sys, tempfile, unittest
import lifecycle_lint as L

HERE = os.path.dirname(os.path.abspath(__file__))


def ev(task_id, state, ts, line=1):
    return {"line": line, "task_id": task_id, "state": state, "occurred_at": ts}


def codes(findings):
    return sorted({f["code"] for f in findings})


class TestValidHistories(unittest.TestCase):
    def test_full_happy_path_clean(self):
        evs = [ev("t", "proposed", "1", 1), ev("t", "accepted", "2", 2),
               ev("t", "submitted", "3", 3), ev("t", "verification_requested", "4", 4),
               ev("t", "rewarded", "5", 5)]
        self.assertEqual(L.lint(evs), [])

    def test_resubmit_loop_allowed_not_duplicate(self):
        evs = [ev("t", "proposed", "1", 1), ev("t", "accepted", "2", 2),
               ev("t", "submitted", "3", 3), ev("t", "verification_requested", "4", 4),
               ev("t", "submitted", "5", 5), ev("t", "rewarded", "6", 6)]
        self.assertEqual(L.lint(evs), [])  # resubmission after verification is legal

    def test_early_refusal_clean(self):
        evs = [ev("t", "proposed", "1", 1), ev("t", "refused", "2", 2)]
        self.assertEqual(L.lint(evs), [])

    def test_submitted_direct_to_rewarded_clean(self):
        evs = [ev("t", "proposed", "1", 1), ev("t", "accepted", "2", 2),
               ev("t", "submitted", "3", 3), ev("t", "rewarded", "4", 4)]
        self.assertEqual(L.lint(evs), [])


class TestViolations(unittest.TestCase):
    def test_missing_start(self):
        evs = [ev("t", "accepted", "1", 1), ev("t", "submitted", "2", 2)]
        self.assertIn(L.MISSING_START, codes(L.lint(evs)))

    def test_skipped_state(self):
        evs = [ev("t", "proposed", "1", 1), ev("t", "submitted", "2", 2)]
        self.assertIn(L.SKIPPED_STATE, codes(L.lint(evs)))

    def test_backward_transition(self):
        evs = [ev("t", "proposed", "1", 1), ev("t", "accepted", "2", 2),
               ev("t", "submitted", "3", 3), ev("t", "proposed", "4", 4)]
        self.assertIn(L.BACKWARD_TRANSITION, codes(L.lint(evs)))

    def test_duplicate_state_back_to_back(self):
        evs = [ev("t", "proposed", "1", 1), ev("t", "accepted", "2", 2),
               ev("t", "accepted", "3", 3)]
        self.assertIn(L.DUPLICATE_STATE, codes(L.lint(evs)))

    def test_post_terminal_event(self):
        evs = [ev("t", "proposed", "1", 1), ev("t", "accepted", "2", 2),
               ev("t", "submitted", "3", 3), ev("t", "rewarded", "4", 4),
               ev("t", "submitted", "5", 5)]
        self.assertIn(L.POST_TERMINAL_EVENT, codes(L.lint(evs)))

    def test_non_monotonic_time(self):
        evs = [ev("t", "proposed", "2026-01-02", 1), ev("t", "accepted", "2026-01-01", 2)]
        self.assertIn(L.NON_MONOTONIC_TIME, codes(L.lint(evs)))

    def test_tasks_are_independent(self):
        evs = [ev("good", "proposed", "1", 1), ev("good", "accepted", "2", 2),
               ev("bad", "submitted", "3", 3)]
        bad = [f for f in L.lint(evs) if f["task_id"] == "bad"]
        good = [f for f in L.lint(evs) if f["task_id"] == "good"]
        self.assertTrue(bad)
        self.assertEqual(good, [])


class TestMalformedParsing(unittest.TestCase):
    def test_invalid_json_line(self):
        _, f = L.parse_jsonl("not json\n")
        self.assertEqual(f[0]["code"], L.MALFORMED_RECORD)

    def test_non_object_line(self):
        _, f = L.parse_jsonl('["a"]\n')
        self.assertEqual(f[0]["code"], L.MALFORMED_RECORD)

    def test_missing_fields(self):
        _, f = L.parse_jsonl('{"task_id":"t","state":"proposed"}\n')
        self.assertIn("missing field", f[0]["detail"])

    def test_unknown_state(self):
        _, f = L.parse_jsonl('{"task_id":"t","state":"teleported","occurred_at":"1"}\n')
        self.assertEqual(f[0]["code"], L.UNKNOWN_STATE)

    def test_blank_lines_ignored(self):
        e, f = L.parse_jsonl('\n\n{"task_id":"t","state":"proposed","occurred_at":"1"}\n\n')
        self.assertEqual(len(e), 1)
        self.assertEqual(f, [])

    def test_line_numbers_preserved(self):
        _, f = L.parse_jsonl('{"task_id":"t","state":"proposed","occurred_at":"1"}\nbroken\n')
        self.assertEqual(f[0]["line"], 2)


class TestReportDeterminism(unittest.TestCase):
    def test_finding_order_stable(self):
        evs = [ev("z", "submitted", "1", 9), ev("a", "submitted", "1", 1)]
        r1 = L.build_report(L.lint(evs), 2, 2)
        r2 = L.build_report(L.lint(list(reversed(evs))), 2, 2)
        self.assertEqual([f["task_id"] for f in r1["findings"]],
                         [f["task_id"] for f in r2["findings"]])

    def test_serialize_repeatable(self):
        r = L.build_report(L.lint([ev("t", "submitted", "1", 1)]), 1, 1)
        self.assertEqual(L.serialize(r), L.serialize(r))

    def test_clean_status(self):
        r = L.build_report([], 0, 0)
        self.assertEqual(r["status"], "clean")

    def test_counts_aggregate(self):
        evs = [ev("a", "submitted", "1", 1), ev("b", "submitted", "1", 2)]
        r = L.build_report(L.lint(evs), 2, 2)
        self.assertEqual(r["finding_counts"][L.MISSING_START], 2)


class TestCli(unittest.TestCase):
    def _cli(self, *a):
        return subprocess.run([sys.executable, os.path.join(HERE, "lifecycle_lint.py"), *a],
                              capture_output=True, text=True)

    def test_valid_fixture_exit_zero(self):
        p = self._cli(os.path.join(HERE, "events_valid.jsonl"))
        self.assertEqual(p.returncode, 0)
        self.assertEqual(json.loads(p.stdout)["status"], "clean")

    def test_invalid_fixture_exit_one(self):
        p = self._cli(os.path.join(HERE, "events_invalid.jsonl"))
        self.assertEqual(p.returncode, 1)

    def test_missing_file_exit_two(self):
        p = self._cli("/nonexistent.jsonl")
        self.assertEqual(p.returncode, 2)
        self.assertIn("UNREADABLE_INPUT", p.stderr)

    def test_repeated_runs_byte_identical(self):
        f = os.path.join(HERE, "events_invalid.jsonl")
        self.assertEqual(self._cli(f).stdout, self._cli(f).stdout)

    def test_invalid_fixture_covers_every_code(self):
        p = self._cli(os.path.join(HERE, "events_invalid.jsonl"))
        got = set(json.loads(p.stdout)["finding_counts"])
        for c in (L.MALFORMED_RECORD, L.UNKNOWN_STATE, L.MISSING_START, L.DUPLICATE_STATE,
                  L.SKIPPED_STATE, L.BACKWARD_TRANSITION, L.POST_TERMINAL_EVENT,
                  L.NON_MONOTONIC_TIME):
            self.assertIn(c, got, f"{c} not exercised by events_invalid.jsonl")


if __name__ == "__main__":
    unittest.main(verbosity=2)
