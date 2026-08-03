#!/usr/bin/env python3
"""Tests for the Objective Evidence Quality Scorer."""
import json, os, subprocess, sys, tempfile, unittest
import score_evidence as S

HERE = os.path.dirname(os.path.abspath(__file__))
HASH = "bc5a197234abcba48ef039e9d0f3dd20c590dfa9782c057481550c8c7d9e7b56"
CID = "QmYvH6Y2VpnFUops1WaVY9fCKy1c6u6BFdDJfEwpffgLuE"


def cfg(**over):
    c = S.load_config(None, {})
    c.update(over)
    return c


def rec(sid, text):
    return {"submission_id": sid, "text": text}


class TestArtifactDetection(unittest.TestCase):
    def test_code_fence_counted_as_one_block(self):
        c, _ = S.count_artifacts("```\ncode\n```")
        self.assertEqual(c["code_fences"], 1)

    def test_shell_prompt_detected(self):
        c, _ = S.count_artifacts("$ python3 run.py\n")
        self.assertEqual(c["shell_lines"], 1)

    def test_exit_code_detected(self):
        c, _ = S.count_artifacts("exit=1")
        self.assertEqual(c["exit_codes"], 1)

    def test_exit_code_wordy_form(self):
        c, _ = S.count_artifacts("exit code: 2")
        self.assertEqual(c["exit_codes"], 1)

    def test_hash_detected(self):
        c, _ = S.count_artifacts(f"digest {HASH} here")
        self.assertEqual(c["hashes"], 1)

    def test_cid_detected(self):
        c, _ = S.count_artifacts(f"cid {CID} here")
        self.assertEqual(c["cids"], 1)

    def test_url_detected(self):
        c, _ = S.count_artifacts("see https://example.org/spec for details")
        self.assertEqual(c["urls"], 1)

    def test_path_detected(self):
        c, _ = S.count_artifacts("file at /outputs/tool.py exists")
        self.assertGreaterEqual(c["paths"], 1)

    def test_prose_has_no_artifacts(self):
        _, total = S.count_artifacts("I finished the work and it went fine overall.")
        self.assertEqual(total, 0)


class TestSpecificity(unittest.TestCase):
    def test_trailing_punctuation_not_specific(self):
        """'Done.' must not count as specific just because of the full stop."""
        ratio, specific, _ = S.specificity_ratio("Done.")
        self.assertEqual(specific, 0)

    def test_tiny_sample_is_damped(self):
        """A one-token record cannot be 100% specific; it is unmeasurable."""
        ratio, _, _ = S.specificity_ratio("v1.2")
        self.assertLess(ratio, 0.2)

    def test_full_confidence_at_threshold(self):
        text = " ".join(f"item_{i}" for i in range(S.MIN_TOKENS_FOR_CONFIDENCE))
        ratio, _, n = S.specificity_ratio(text)
        self.assertEqual(n, S.MIN_TOKENS_FOR_CONFIDENCE)
        self.assertAlmostEqual(ratio, 1.0, places=6)

    def test_prose_low_specificity(self):
        ratio, _, _ = S.specificity_ratio(
            "the work is now complete and everything looks fine to me overall today " * 3)
        self.assertLess(ratio, 0.2)

    def test_empty_text(self):
        self.assertEqual(S.specificity_ratio(""), (0.0, 0, 0))


class TestBoilerplate(unittest.TestCase):
    def test_identical_records_lose_originality(self):
        t = "This is a shared boilerplate sentence used across submissions."
        rep = S.score_all([rec("a", t), rec("b", t)], cfg())
        for r in rep["records"]:
            self.assertEqual(r["components"]["originality"], 0.0)

    def test_unique_records_keep_originality(self):
        rep = S.score_all([rec("a", "A wholly unique first statement about alpha work."),
                           rec("b", "A completely different second statement on beta work.")], cfg())
        for r in rep["records"]:
            self.assertEqual(r["components"]["originality"], 1.0)

    def test_partial_overlap_partial_penalty(self):
        shared = "This exact sentence appears in both of the submissions here."
        a = shared + " But this one is unique to the first record entirely."
        b = shared + " While this other one belongs only to the second record."
        rep = S.score_all([rec("a", a), rec("b", b)], cfg())
        for r in rep["records"]:
            self.assertAlmostEqual(r["components"]["originality"], 0.5, places=6)

    def test_short_fragments_ignored(self):
        rep = S.score_all([rec("a", "Ok. Fine."), rec("b", "Ok. Fine.")], cfg())
        self.assertEqual(rep["records"][0]["components"]["originality"], 1.0)

    def test_single_record_is_original(self):
        rep = S.score_all([rec("a", "One single sentence that stands entirely alone here.")], cfg())
        self.assertEqual(rep["records"][0]["components"]["originality"], 1.0)


class TestScoringAndThreshold(unittest.TestCase):
    def test_boilerplate_fails_default_threshold(self):
        rep = S.score_all(S.load_records(os.path.join(HERE, "evidence_mixed.json")), cfg())
        ids = {r["submission_id"]: r for r in rep["records"]}
        self.assertFalse(ids["boiler_a"]["passed"])
        self.assertFalse(ids["boiler_b"]["passed"])

    def test_tiny_record_fails(self):
        rep = S.score_all(S.load_records(os.path.join(HERE, "evidence_mixed.json")), cfg())
        ids = {r["submission_id"]: r for r in rep["records"]}
        self.assertFalse(ids["tiny_1"]["passed"])

    def test_strong_record_passes(self):
        rep = S.score_all(S.load_records(os.path.join(HERE, "evidence_mixed.json")), cfg())
        ids = {r["submission_id"]: r for r in rep["records"]}
        self.assertTrue(ids["strong_1"]["passed"])

    def test_threshold_is_configurable(self):
        recs = S.load_records(os.path.join(HERE, "evidence_mixed.json"))
        self.assertEqual(S.score_all(recs, cfg(threshold=0.0))["status"], "pass")
        self.assertEqual(S.score_all(recs, cfg(threshold=0.99))["status"], "fail")

    def test_score_is_weighted_sum(self):
        rep = S.score_all(S.load_records(os.path.join(HERE, "evidence_mixed.json")), cfg())
        c = cfg()
        for r in rep["records"]:
            expect = round(sum(r["components"][k] * c["weights"][k] for k in r["components"]), 6)
            self.assertAlmostEqual(r["score"], expect, places=6)

    def test_records_sorted_worst_first(self):
        rep = S.score_all(S.load_records(os.path.join(HERE, "evidence_mixed.json")), cfg())
        scores = [r["score"] for r in rep["records"]]
        self.assertEqual(scores, sorted(scores))


class TestConfig(unittest.TestCase):
    def test_defaults(self):
        self.assertEqual(S.load_config(None, {})["threshold"], 0.5)

    def test_file_overrides(self):
        c = S.load_config(os.path.join(HERE, "config_strict.json"), {})
        self.assertEqual(c["threshold"], 0.7)

    def test_cli_beats_file(self):
        c = S.load_config(os.path.join(HERE, "config_strict.json"), {"threshold": 0.1})
        self.assertEqual(c["threshold"], 0.1)

    def test_unknown_key_rejected(self):
        fh = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        json.dump({"bogus": 1}, fh); fh.close()
        try:
            with self.assertRaises(S.InputError):
                S.load_config(fh.name, {})
        finally:
            os.unlink(fh.name)

    def test_unknown_signal_rejected(self):
        fh = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        json.dump({"weights": {"vibes": 1}}, fh); fh.close()
        try:
            with self.assertRaises(S.InputError):
                S.load_config(fh.name, {})
        finally:
            os.unlink(fh.name)


class TestMalformed(unittest.TestCase):
    def _load(self, obj):
        fh = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        json.dump(obj, fh); fh.close()
        try:
            return S.load_records(fh.name)
        finally:
            os.unlink(fh.name)

    def test_missing_text(self):
        with self.assertRaises(S.InputError):
            self._load([{"submission_id": "a"}])

    def test_non_string_text(self):
        with self.assertRaises(S.InputError):
            self._load([{"submission_id": "a", "text": 5}])

    def test_blank_submission_id(self):
        with self.assertRaises(S.InputError):
            self._load([{"submission_id": "  ", "text": "x"}])

    def test_non_array(self):
        with self.assertRaises(S.InputError):
            self._load({"a": 1})


class TestCli(unittest.TestCase):
    def _cli(self, *a):
        return subprocess.run([sys.executable, os.path.join(HERE, "score_evidence.py"), *a],
                              capture_output=True, text=True)

    def test_pass_fixture_exit_zero(self):
        p = self._cli(os.path.join(HERE, "evidence_pass.json"))
        self.assertEqual(p.returncode, 0)
        self.assertEqual(json.loads(p.stdout)["status"], "pass")

    def test_mixed_fixture_exit_one(self):
        p = self._cli(os.path.join(HERE, "evidence_mixed.json"))
        self.assertEqual(p.returncode, 1)

    def test_missing_file_exit_two(self):
        p = self._cli("/nonexistent.json")
        self.assertEqual(p.returncode, 2)
        self.assertIn("INVALID_INPUT", p.stderr)

    def test_repeated_runs_identical(self):
        f = os.path.join(HERE, "evidence_mixed.json")
        self.assertEqual(self._cli(f).stdout, self._cli(f).stdout)

    def test_threshold_flag_flips_result(self):
        f = os.path.join(HERE, "evidence_mixed.json")
        self.assertEqual(self._cli(f, "--threshold", "0.0").returncode, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
