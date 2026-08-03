#!/usr/bin/env python3
"""Tests for the Deterministic Task-Reward Budget Forecaster."""
import json, os, subprocess, sys, tempfile, unittest
from decimal import Decimal
import forecast as F

HERE = os.path.dirname(os.path.abspath(__file__))


def cfg(horizon="4", cap=None):
    return {"horizon_weeks": Decimal(horizon),
            "budget_cap": Decimal(cap).quantize(F.SCALE) if cap is not None else None}


def h(tid, reward, ts):
    return {"task_id": tid, "reward": Decimal(reward).quantize(F.SCALE),
            "rewarded_at": ts, "_ts": F._ts(ts, "x")}


def o(tid, est):
    return {"task_id": tid, "estimate": Decimal(est).quantize(F.SCALE)}


def load_hist(recs):
    fh = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
    json.dump(recs, fh); fh.close()
    try:
        return F.load_history(fh.name)
    finally:
        os.unlink(fh.name)


class TestEmptyHistory(unittest.TestCase):
    def test_empty_history_no_crash(self):
        r = F.forecast([], [], cfg())
        self.assertEqual(r["history"]["records"], 0)
        self.assertEqual(r["status"], "within_budget")

    def test_empty_history_burn_is_null(self):
        r = F.forecast([], [], cfg())
        self.assertIsNone(r["history"]["burn_per_week"])
        self.assertIsNone(r["history"]["mean_reward"])

    def test_empty_history_with_open_tasks_still_commits(self):
        r = F.forecast([], [o("g", "5")], cfg())
        self.assertEqual(r["open_tasks"]["committed"], "5.000000")
        self.assertEqual(r["projection"]["projected_total"], "5.000000")

    def test_empty_everything_totals_zero(self):
        r = F.forecast([], [], cfg())
        self.assertEqual(r["projection"]["projected_total"], "0.000000")


class TestSingleRecord(unittest.TestCase):
    def test_single_record_burn_is_null(self):
        """One point in time cannot establish a rate."""
        r = F.forecast([h("a", "5", "2026-07-15T09:00:00Z")], [], cfg())
        self.assertIsNone(r["history"]["burn_per_week"])
        self.assertIsNone(r["history"]["span_days"])

    def test_single_record_stdev_zero(self):
        r = F.forecast([h("a", "5", "2026-07-15T09:00:00Z")], [], cfg())
        self.assertEqual(r["history"]["stdev_reward"], "0.000000")

    def test_single_record_mean_is_the_value(self):
        r = F.forecast([h("a", "5", "2026-07-15T09:00:00Z")], [], cfg())
        self.assertEqual(r["history"]["mean_reward"], "5.000000")

    def test_single_record_no_variance_band(self):
        r = F.forecast([h("a", "5", "2026-07-15T09:00:00Z")], [o("g", "2")], cfg())
        self.assertEqual(r["projection"]["variance_band"], "0.000000")


class TestBurnRate(unittest.TestCase):
    def test_burn_over_known_span(self):
        """20 over 14 days = 2 weeks -> 10/week."""
        hist = [h("a", "10", "2026-07-01T00:00:00Z"), h("b", "10", "2026-07-15T00:00:00Z")]
        r = F.forecast(hist, [], cfg())
        self.assertEqual(r["history"]["burn_per_week"], "10.000000")

    def test_span_days_computed(self):
        hist = [h("a", "1", "2026-07-01T00:00:00Z"), h("b", "1", "2026-07-08T00:00:00Z")]
        self.assertEqual(F.forecast(hist, [], cfg())["history"]["span_days"], 7.0)

    def test_projection_scales_with_horizon(self):
        hist = [h("a", "10", "2026-07-01T00:00:00Z"), h("b", "10", "2026-07-15T00:00:00Z")]
        r2 = F.forecast(hist, [], cfg(horizon="2"))
        r4 = F.forecast(hist, [], cfg(horizon="4"))
        self.assertEqual(r2["projection"]["projected_burn"], "20.000000")
        self.assertEqual(r4["projection"]["projected_burn"], "40.000000")

    def test_zero_horizon_projects_only_committed(self):
        hist = [h("a", "10", "2026-07-01T00:00:00Z"), h("b", "10", "2026-07-15T00:00:00Z")]
        r = F.forecast(hist, [o("g", "3")], cfg(horizon="0"))
        self.assertEqual(r["projection"]["projected_total"], "3.000000")


class TestDecimalSafety(unittest.TestCase):
    def test_no_float_drift(self):
        hist = [h("a", "0.1", "2026-07-01T00:00:00Z"), h("b", "0.2", "2026-07-08T00:00:00Z")]
        r = F.forecast(hist, [], cfg())
        self.assertEqual(r["history"]["total_rewarded"], "0.300000")

    def test_float_reward_rejected(self):
        with self.assertRaises(F.InputError):
            load_hist([{"task_id": "a", "reward": 3.5, "rewarded_at": "2026-07-01T00:00:00Z"}])

    def test_negative_reward_rejected(self):
        with self.assertRaises(F.InputError):
            load_hist([{"task_id": "a", "reward": "-1", "rewarded_at": "2026-07-01T00:00:00Z"}])

    def test_integer_reward_accepted(self):
        recs = load_hist([{"task_id": "a", "reward": 3, "rewarded_at": "2026-07-01T00:00:00Z"}])
        self.assertEqual(recs[0]["reward"], Decimal("3.000000"))

    def test_committed_is_exact(self):
        r = F.forecast([], [o("a", "0.1"), o("b", "0.2")], cfg())
        self.assertEqual(r["open_tasks"]["committed"], "0.300000")


class TestBudgetCap(unittest.TestCase):
    def test_breach_detected(self):
        r = F.forecast([], [o("g", "10")], cfg(cap="5"))
        self.assertTrue(r["over_budget"])
        self.assertEqual(r["status"], "over_budget")

    def test_within_budget(self):
        r = F.forecast([], [o("g", "3")], cfg(cap="5"))
        self.assertFalse(r["over_budget"])

    def test_exactly_at_cap_is_not_a_breach(self):
        """Spending the whole budget is not overspending it."""
        r = F.forecast([], [o("g", "5")], cfg(cap="5"))
        self.assertFalse(r["over_budget"])

    def test_no_cap_never_breaches(self):
        r = F.forecast([], [o("g", "999999")], cfg())
        self.assertFalse(r["over_budget"])


class TestVarianceBand(unittest.TestCase):
    def test_uniform_history_has_no_band(self):
        hist = [h("a", "5", "2026-07-01T00:00:00Z"), h("b", "5", "2026-07-08T00:00:00Z"),
                h("c", "5", "2026-07-15T00:00:00Z")]
        self.assertEqual(F.forecast(hist, [], cfg())["projection"]["variance_band"], "0.000000")

    def test_spread_history_has_band(self):
        hist = [h("a", "1", "2026-07-01T00:00:00Z"), h("b", "50", "2026-07-08T00:00:00Z"),
                h("c", "2", "2026-07-15T00:00:00Z")]
        band = Decimal(F.forecast(hist, [], cfg())["projection"]["variance_band"])
        self.assertGreater(band, 0)

    def test_low_never_negative(self):
        hist = [h("a", "1", "2026-07-01T00:00:00Z"), h("b", "500", "2026-07-08T00:00:00Z")]
        low = Decimal(F.forecast(hist, [], cfg())["projection"]["low"])
        self.assertGreaterEqual(low, 0)

    def test_band_brackets_projection(self):
        hist = [h("a", "1", "2026-07-01T00:00:00Z"), h("b", "9", "2026-07-08T00:00:00Z")]
        p = F.forecast(hist, [], cfg())["projection"]
        self.assertLessEqual(Decimal(p["low"]), Decimal(p["projected_total"]))
        self.assertGreaterEqual(Decimal(p["high"]), Decimal(p["projected_total"]))


class TestMalformed(unittest.TestCase):
    def test_missing_field(self):
        with self.assertRaises(F.InputError):
            load_hist([{"task_id": "a", "reward": "1"}])

    def test_bad_timestamp(self):
        with self.assertRaises(F.InputError):
            load_hist([{"task_id": "a", "reward": "1", "rewarded_at": "soon"}])

    def test_bad_reward_string(self):
        with self.assertRaises(F.InputError):
            load_hist([{"task_id": "a", "reward": "lots", "rewarded_at": "2026-07-01T00:00:00Z"}])

    def test_non_array(self):
        fh = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        json.dump({"a": 1}, fh); fh.close()
        try:
            with self.assertRaises(F.InputError):
                F.load_history(fh.name)
        finally:
            os.unlink(fh.name)


class TestCli(unittest.TestCase):
    def _cli(self, *a):
        return subprocess.run([sys.executable, os.path.join(HERE, "forecast.py"), *a],
                              capture_output=True, text=True)

    def test_within_budget_exit_zero(self):
        p = self._cli(os.path.join(HERE, "history.json"),
                      "-k", os.path.join(HERE, "open_tasks.json"),
                      "--budget-cap", "10000")
        self.assertEqual(p.returncode, 0)

    def test_over_budget_exit_one(self):
        p = self._cli(os.path.join(HERE, "history.json"),
                      "-k", os.path.join(HERE, "open_tasks.json"),
                      "--budget-cap", "1")
        self.assertEqual(p.returncode, 1)
        self.assertEqual(json.loads(p.stdout)["status"], "over_budget")

    def test_empty_history_exit_zero(self):
        p = self._cli(os.path.join(HERE, "history_empty.json"))
        self.assertEqual(p.returncode, 0)

    def test_single_history_exit_zero(self):
        p = self._cli(os.path.join(HERE, "history_single.json"))
        self.assertEqual(p.returncode, 0)

    def test_missing_file_exit_two(self):
        p = self._cli("/nonexistent.json")
        self.assertEqual(p.returncode, 2)
        self.assertIn("INVALID_INPUT", p.stderr)

    def test_bad_cap_exit_two(self):
        p = self._cli(os.path.join(HERE, "history.json"), "--budget-cap", "lots")
        self.assertEqual(p.returncode, 2)

    def test_repeated_runs_identical(self):
        a = os.path.join(HERE, "history.json"); b = os.path.join(HERE, "open_tasks.json")
        self.assertEqual(self._cli(a, "-k", b).stdout, self._cli(a, "-k", b).stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
