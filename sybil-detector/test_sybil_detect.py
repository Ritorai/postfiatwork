#!/usr/bin/env python3
"""Tests for the Configurable Sybil Wallet-Cluster Detector."""
import json, os, subprocess, sys, tempfile, unittest
import sybil_detect as S

HERE = os.path.dirname(os.path.abspath(__file__))


def rec(sid, wallet, cid="QmX" + "0" * 43, length=1000, ts="2026-07-30T09:00:00Z"):
    return {"submission_id": sid, "wallet": wallet, "cid": cid,
            "evidence_length": length, "submitted_at": ts}


def load(recs):
    fh = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
    json.dump(recs, fh); fh.close()
    try:
        return S.load_records(fh.name)
    finally:
        os.unlink(fh.name)


def cfg(**over):
    c = S.load_config(None, {})
    c.update(over)
    return c


class TestSignals(unittest.TestCase):
    def test_shared_cid_fires(self):
        r = load([rec("a", "w1", cid="QmSame" + "0" * 40, ts="2026-01-01T00:00:00Z"),
                  rec("b", "w2", cid="QmSame" + "0" * 40, ts="2026-06-01T00:00:00Z", length=1)])
        self.assertIn(S.SHARED_CID, S.pair_signals(r[0], r[1], cfg()))

    def test_length_match_within_tolerance(self):
        r = load([rec("a", "w1", length=1000, ts="2026-01-01T00:00:00Z"),
                  rec("b", "w2", cid="QmY" + "1" * 43, length=1040, ts="2026-06-01T00:00:00Z")])
        self.assertIn(S.LENGTH_MATCH, S.pair_signals(r[0], r[1], cfg(length_tolerance=0.05)))

    def test_length_match_outside_tolerance(self):
        r = load([rec("a", "w1", length=1000, ts="2026-01-01T00:00:00Z"),
                  rec("b", "w2", cid="QmY" + "1" * 43, length=2000, ts="2026-06-01T00:00:00Z")])
        self.assertNotIn(S.LENGTH_MATCH, S.pair_signals(r[0], r[1], cfg(length_tolerance=0.05)))

    def test_zero_lengths_match(self):
        r = load([rec("a", "w1", length=0, ts="2026-01-01T00:00:00Z"),
                  rec("b", "w2", cid="QmY" + "1" * 43, length=0, ts="2026-06-01T00:00:00Z")])
        self.assertIn(S.LENGTH_MATCH, S.pair_signals(r[0], r[1], cfg()))

    def test_burst_timing_within_window(self):
        r = load([rec("a", "w1", ts="2026-07-30T09:00:00Z", length=1),
                  rec("b", "w2", cid="QmY" + "1" * 43, ts="2026-07-30T09:02:00Z", length=99999)])
        self.assertIn(S.BURST_TIMING, S.pair_signals(r[0], r[1], cfg(burst_window=300)))

    def test_burst_timing_outside_window(self):
        r = load([rec("a", "w1", ts="2026-07-30T09:00:00Z", length=1),
                  rec("b", "w2", cid="QmY" + "1" * 43, ts="2026-07-30T12:00:00Z", length=99999)])
        self.assertNotIn(S.BURST_TIMING, S.pair_signals(r[0], r[1], cfg(burst_window=300)))

    def test_same_wallet_pairs_skipped(self):
        r = load([rec("a", "w1"), rec("b", "w1")])
        self.assertEqual(S.analyze(r, cfg())["totals"]["scored_pairs"], 0)


class TestClustering(unittest.TestCase):
    def test_three_wallets_one_cluster(self):
        rep = S.analyze(S.load_records(os.path.join(HERE, "submissions_sybil.json")), cfg())
        big = [c for c in rep["clusters"] if len(c["wallets"]) == 3]
        self.assertEqual(len(big), 1)
        self.assertEqual(big[0]["wallets"], ["rSyb1", "rSyb2", "rSyb3"])

    def test_loner_not_clustered(self):
        rep = S.analyze(S.load_records(os.path.join(HERE, "submissions_sybil.json")), cfg())
        for c in rep["clusters"]:
            self.assertNotIn("rLoner", c["wallets"])

    def test_clean_fixture_has_no_alert(self):
        rep = S.analyze(S.load_records(os.path.join(HERE, "submissions_clean.json")), cfg())
        self.assertEqual(rep["status"], "clear")

    def test_cluster_score_is_max_pair_score(self):
        rep = S.analyze(S.load_records(os.path.join(HERE, "submissions_sybil.json")), cfg())
        for c in rep["clusters"]:
            self.assertEqual(c["score"], max(p["score"] for p in c["pairs"]))

    def test_link_threshold_controls_clustering(self):
        recs = S.load_records(os.path.join(HERE, "submissions_sybil.json"))
        loose = S.analyze(recs, cfg(link_threshold=0.1))
        tight = S.analyze(recs, cfg(link_threshold=0.95))
        self.assertGreaterEqual(loose["totals"]["clusters"], tight["totals"]["clusters"])


class TestConfigurability(unittest.TestCase):
    def test_defaults_applied(self):
        c = S.load_config(None, {})
        self.assertEqual(c["alert_threshold"], 0.8)
        self.assertEqual(c["weights"][S.SHARED_CID], 0.6)

    def test_config_file_overrides(self):
        c = S.load_config(os.path.join(HERE, "config_strict.json"), {})
        self.assertEqual(c["weights"][S.SHARED_CID], 0.7)
        self.assertEqual(c["burst_window"], 120)

    def test_cli_overrides_beat_file(self):
        c = S.load_config(os.path.join(HERE, "config_strict.json"), {"alert_threshold": 0.25})
        self.assertEqual(c["alert_threshold"], 0.25)

    def test_unknown_config_key_rejected(self):
        fh = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        json.dump({"nope": 1}, fh); fh.close()
        try:
            with self.assertRaises(S.InputError):
                S.load_config(fh.name, {})
        finally:
            os.unlink(fh.name)

    def test_unknown_signal_weight_rejected(self):
        fh = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        json.dump({"weights": {"telepathy": 1}}, fh); fh.close()
        try:
            with self.assertRaises(S.InputError):
                S.load_config(fh.name, {})
        finally:
            os.unlink(fh.name)

    def test_alert_threshold_changes_status(self):
        recs = S.load_records(os.path.join(HERE, "submissions_clean.json"))
        self.assertEqual(S.analyze(recs, cfg(alert_threshold=0.01, link_threshold=0.01))["status"], "clear")


class TestMalformed(unittest.TestCase):
    def test_missing_field(self):
        with self.assertRaises(S.InputError):
            load([{"submission_id": "a", "wallet": "w"}])

    def test_bad_evidence_length_type(self):
        with self.assertRaises(S.InputError):
            load([rec("a", "w1", length="2000")])

    def test_negative_evidence_length(self):
        with self.assertRaises(S.InputError):
            load([rec("a", "w1", length=-5)])

    def test_bad_timestamp(self):
        with self.assertRaises(S.InputError):
            load([rec("a", "w1", ts="yesterday")])

    def test_non_array(self):
        fh = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        json.dump({"a": 1}, fh); fh.close()
        try:
            with self.assertRaises(S.InputError):
                S.load_records(fh.name)
        finally:
            os.unlink(fh.name)


class TestCli(unittest.TestCase):
    def _cli(self, *a):
        return subprocess.run([sys.executable, os.path.join(HERE, "sybil_detect.py"), *a],
                              capture_output=True, text=True)

    def test_clean_exit_zero(self):
        p = self._cli(os.path.join(HERE, "submissions_clean.json"))
        self.assertEqual(p.returncode, 0)
        self.assertEqual(json.loads(p.stdout)["status"], "clear")

    def test_sybil_exit_one(self):
        p = self._cli(os.path.join(HERE, "submissions_sybil.json"))
        self.assertEqual(p.returncode, 1)
        self.assertEqual(json.loads(p.stdout)["status"], "alert")

    def test_missing_file_exit_two(self):
        p = self._cli("/nonexistent.json")
        self.assertEqual(p.returncode, 2)
        self.assertIn("INVALID_INPUT", p.stderr)

    def test_repeated_runs_identical(self):
        f = os.path.join(HERE, "submissions_sybil.json")
        self.assertEqual(self._cli(f).stdout, self._cli(f).stdout)

    def test_strict_config_still_alerts_on_shared_cid(self):
        p = self._cli(os.path.join(HERE, "submissions_sybil.json"),
                      "-c", os.path.join(HERE, "config_strict.json"))
        self.assertEqual(p.returncode, 1)

    def test_raising_alert_threshold_clears(self):
        p = self._cli(os.path.join(HERE, "submissions_sybil.json"), "--alert-threshold", "1.5")
        self.assertEqual(p.returncode, 0)
        self.assertEqual(json.loads(p.stdout)["status"], "clear")


if __name__ == "__main__":
    unittest.main(verbosity=2)
