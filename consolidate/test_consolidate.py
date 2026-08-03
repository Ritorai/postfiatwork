"""unittest suite for consolidate.py.

Run with: python3 -m unittest test_consolidate -v
"""
import copy
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

import consolidate as c


def write(path, obj_or_text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        if isinstance(obj_or_text, str):
            fh.write(obj_or_text)
        else:
            fh.write(json.dumps(obj_or_text))


class TempDirMixin(object):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="consolidate_test_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def p(self, *parts):
        return os.path.join(self.tmp, *parts)


# ---------------------------------------------------------------------------
# canonical serialisation
# ---------------------------------------------------------------------------

class TestCanonicalDumps(unittest.TestCase):
    def test_ends_with_single_newline(self):
        out = c.canonical_dumps({"a": 1})
        self.assertTrue(out.endswith("\n"))
        self.assertFalse(out.endswith("\n\n"))

    def test_sorted_keys(self):
        out = c.canonical_dumps({"b": 1, "a": 2})
        self.assertEqual(out, '{"a":2,"b":1}\n')

    def test_compact_separators(self):
        out = c.canonical_dumps({"a": [1, 2], "b": {"c": 1}})
        self.assertNotIn(", ", out)
        self.assertNotIn(": ", out)

    def test_ensure_ascii(self):
        out = c.canonical_dumps({"a": "café"})
        self.assertIn("\\u00e9", out)
        self.assertNotIn("é", out)

    def test_deterministic_across_calls(self):
        obj = {"z": 1, "a": [3, 2, 1], "m": {"y": 1, "x": 2}}
        self.assertEqual(c.canonical_dumps(obj), c.canonical_dumps(obj))


class TestHelpers(unittest.TestCase):
    def test_worst_severity_empty(self):
        self.assertIsNone(c.worst_severity([]))

    def test_worst_severity_picks_highest_rank(self):
        self.assertEqual(c.worst_severity(["info", "critical", "warning"]), "critical")

    def test_worst_severity_ignores_unknown(self):
        self.assertEqual(c.worst_severity(["bogus", "warning"]), "warning")

    def test_worst_severity_all_unknown(self):
        self.assertIsNone(c.worst_severity(["bogus", "nope"]))

    def test_make_finding_shape(self):
        f = c.make_finding("t", "r.json", "task_1", "CODE", "error", "detail text")
        self.assertEqual(set(f.keys()),
                          {"source_tool", "source_report", "task_id", "code", "severity", "detail"})

    def test_make_finding_invalid_severity_falls_back_to_error(self):
        f = c.make_finding("t", "r.json", None, "CODE", "not-a-real-severity", "d")
        self.assertEqual(f["severity"], "error")

    def test_detail_from_sorted_and_deterministic(self):
        item = {"b": 1, "a": 2, "code": "X"}
        d1 = c._detail_from(item, exclude={"code"})
        d2 = c._detail_from(dict(item), exclude={"code"})
        self.assertEqual(d1, d2)
        self.assertTrue(d1.startswith("a="))

    def test_detail_from_empty_after_exclude(self):
        item = {"code": "X"}
        d = c._detail_from(item, exclude={"code"})
        self.assertEqual(d, "(no additional detail)")

    def test_severity_rank_order(self):
        self.assertLess(c.SEVERITY_RANK["info"], c.SEVERITY_RANK["warning"])
        self.assertLess(c.SEVERITY_RANK["warning"], c.SEVERITY_RANK["error"])
        self.assertLess(c.SEVERITY_RANK["error"], c.SEVERITY_RANK["critical"])


# ---------------------------------------------------------------------------
# discovery
# ---------------------------------------------------------------------------

class TestDiscovery(TempDirMixin, unittest.TestCase):
    def test_empty_directory(self):
        self.assertEqual(c.discover_json_files(self.tmp), [])

    def test_finds_json_only(self):
        write(self.p("a.json"), {"x": 1})
        write(self.p("b.txt"), "hello")
        write(self.p("README.md"), "# hi")
        self.assertEqual(c.discover_json_files(self.tmp), ["a.json"])

    def test_sorted_output(self):
        write(self.p("zeta.json"), {})
        write(self.p("alpha.json"), {})
        write(self.p("mid.json"), {})
        self.assertEqual(c.discover_json_files(self.tmp), ["alpha.json", "mid.json", "zeta.json"])

    def test_recurses_into_subdirectories(self):
        write(self.p("top.json"), {})
        write(self.p("sub", "inner.json"), {})
        write(self.p("sub", "deeper", "innermost.json"), {})
        found = c.discover_json_files(self.tmp)
        self.assertEqual(found, ["sub/deeper/innermost.json", "sub/inner.json", "top.json"])

    def test_relative_paths_use_forward_slash(self):
        write(self.p("sub", "inner.json"), {})
        found = c.discover_json_files(self.tmp)
        self.assertIn("/", found[0])
        self.assertNotIn("\\", found[0])

    def test_case_insensitive_extension(self):
        write(self.p("upper.JSON"), {})
        self.assertEqual(c.discover_json_files(self.tmp), ["upper.JSON"])

    def test_paths_are_relative_not_absolute(self):
        write(self.p("a.json"), {})
        found = c.discover_json_files(self.tmp)
        for rel in found:
            self.assertFalse(os.path.isabs(rel))
            self.assertNotIn(self.tmp, rel)

    def test_ignores_pycache(self):
        write(self.p("__pycache__", "x.json"), {})
        write(self.p("real.json"), {})
        found = c.discover_json_files(self.tmp)
        self.assertIn("real.json", found)
        self.assertIn("__pycache__/x.json", found)  # not special-cased; still discovered as *.json


# ---------------------------------------------------------------------------
# adapters -- one section per observed real report shape
# ---------------------------------------------------------------------------

LIFECYCLE_DIRTY = {
    "finding_counts": {"BACKWARD_TRANSITION": 1},
    "findings": [
        {"code": "BACKWARD_TRANSITION", "detail": "'submitted' -> 'proposed' moves backward",
         "line": 8, "task_id": "task_backward"},
    ],
    "report_version": "1.0", "status": "issues",
    "totals": {"events": 5, "findings": 1, "tasks": 2},
}

LIFECYCLE_CLEAN = {
    "finding_counts": {}, "findings": [], "report_version": "1.0", "status": "ok",
    "totals": {"events": 2, "findings": 0, "tasks": 1},
}

XRPL_AUDITOR_DIRTY = {
    "issue_counts": {"UNKNOWN_TASK_ID": 1},
    "issues": [{"detail": "task_id not in roster", "index": 0, "issue": "UNKNOWN_TASK_ID",
                "payout_id": "p1", "task_id": "task_x"}],
    "report_version": "1.0", "status": "issues",
    "totals": {"distinct_tx_hashes": 1, "issues": 1, "payouts": 1, "roster_tasks": 0,
               "well_formed_payouts": 0},
}

REWARD_RECONCILER_DIRTY = {
    "findings": [{"expected_amount": "1.000000", "issue": "MISSING_PAYOUT",
                  "payout_amount": None, "task_id": "task_y", "wallet": "rABC"}],
    "issue_counts": {"MISSING_PAYOUT": 1}, "precision": "0.000001", "report_version": "1.0",
    "status": "mismatched",
    "totals": {"expected_records": 1, "expected_total": "1.000000", "findings": 1,
               "net_delta": "1.000000", "payout_records": 0, "payout_total": "0.000000"},
}

QUEUE_AUDITOR_DIRTY = {
    "findings": [{"code": "DUPLICATE_TASK_ID", "detail": "task_id 'DUP-1' appears 2 times",
                  "task_id": "DUP-1"}],
    "finding_count": 1, "result": "findings", "task_count": 3,
}

WALLET_RECONCILER_DIRTY = {
    "closing_delta": "-1", "computed_closing_balance": "9", "event_count": 2,
    "finding_counts": {"NEGATIVE_RUNNING_BALANCE": 1},
    "findings": [{"balance": "-5", "code": "NEGATIVE_RUNNING_BALANCE", "context": None,
                  "event_id": "e1", "index": 0}],
    "ledger_version": "1.0", "opening_balance": "0",
    "sign_convention": {"reward": "+"}, "stated_closing_balance": "10", "status": "findings",
    "trace": [],
}

PREFLIGHT_DIRTY = {
    "issues": [{"code": "ORPHAN_EVIDENCE", "evidence_type": "text",
                "message": "evidence S1 references unknown task", "submission_id": "S1",
                "task_id": "T9"}],
    "ready": False,
    "summary": {"evidence_count": 1, "issue_count": 1, "issue_counts_by_code": {"ORPHAN_EVIDENCE": 1},
                "task_count": 1},
}

LINK_INTEGRITY_DIRTY = {
    "schema_version": "1.0",
    "summary": {"counts_by_code": {"UNKNOWN_TASK_REFERENCE": 1}, "is_clean": False, "violation_count": 1},
    "violations": [{"code": "UNKNOWN_TASK_REFERENCE", "evidence_index": 0,
                     "message": "evidence references unknown task", "submission_id": "s1",
                     "submitted_at": "2026-01-01T00:00:00Z", "task_id": "task-unknown"}],
}

LINK_INTEGRITY_MULTI_TASK = {
    "schema_version": "1.0",
    "summary": {"counts_by_code": {"DUPLICATE_SUBMISSION_ID": 1}, "is_clean": False, "violation_count": 1},
    "violations": [{"code": "DUPLICATE_SUBMISSION_ID", "count": 2,
                     "message": "submission_id appears twice", "submission_id": "sub-DUPE",
                     "task_ids": ["task-A", "task-B"]}],
}

SCHEMA_CHECKER_DIRTY = {
    "exit_code": 1, "io_errors": [], "ok": False, "payload_source": "payloads.json",
    "schema_errors": [], "schema_source": "schema.json",
    "status": "violations", "summary": {"PATTERN_MISMATCH": 1}, "tool_version": "1.0.0",
    "violation_count": 1,
    "violations": [{"code": "PATTERN_MISMATCH", "message": "bad pattern", "pointer": "/batch_id"}],
}

STALENESS_DIRTY = {
    "findings": {
        "critical": [{"age_human": "2d", "age_seconds": 172800, "bucket": "critical",
                       "code": "OVERDUE_PROPOSED", "deadline": "2026-07-31T00:00:00Z",
                       "message": "deadline passed", "status": "proposed", "task_id": "S-1",
                       "title": "t"}],
        "info": [], "warning": [],
    },
    "generated_at": "2026-08-02T00:00:00Z",
    "summary": {"critical": 1, "info": 0, "total_findings": 1, "total_tasks": 5, "warning": 0},
    "windows": {"accepted_stale_hours": 48, "submitted_stale_hours": 72},
}

XRPL_ADDRESS_DIRTY = {
    "addresses": [
        {"address": "raLnyS4PTuc5SgXGHqYA894a4eoKqoFwu", "index": 0, "issues": ["BAD_CHECKSUM"],
         "kind": "classic", "valid": False},
        {"address": "rDENY", "index": 1, "issues": ["DENYLISTED"], "kind": "classic", "valid": False},
    ],
    "issue_counts": {"BAD_CHECKSUM": 1, "DENYLISTED": 1}, "report_version": "1.0", "status": "issues",
    "totals": {"addresses": 2, "invalid": 2, "valid": 0},
}

EVENT_LINTER_DIRTY = {
    "report_version": "1.0", "status": "violations",
    "tasks": [{"event_count": 0, "status": "violations", "task_id": "task_bad", "violation_count": 1,
               "violations": [{"detail": "missing field", "index": 3, "task_id": "task_bad",
                                "violation": "MALFORMED_EVENT"}]}],
}

EVENT_LINTER_NULL_INNER_TASK = {
    "report_version": "1.0", "status": "violations",
    "tasks": [{"event_count": 0, "status": "violations", "task_id": "outer_task", "violation_count": 1,
               "violations": [{"detail": "bad", "index": 1, "task_id": None,
                                "violation": "MALFORMED_EVENT"}]}],
}

EVIDENCE_HARNESS_DIRTY = {
    "bundle_dir": "bundle_bad", "bundle_files": ["a.py"],
    "checks": [{"check": "min_test_count", "detail": "only 4 tests, need 10", "evidence": [],
                "gaps": ["need more tests"], "status": "fail"},
               {"check": "require_hashes", "detail": "ok", "evidence": [], "gaps": [], "status": "pass"}],
    "requirements_source": "requirements.json",
}

EVIDENCE_SCORER_DIRTY = {
    "config": {"artifact_target": 6, "target_length": 800, "threshold": 0.5,
               "weights": {"artifacts": 0.35, "length": 0.15, "originality": 0.25, "specificity": 0.25}},
    "records": [{"components": {}, "evidence": {}, "passed": False, "score": 0.1,
                 "submission_id": "sub1"}],
    "report_version": "1.0", "status": "fail", "totals": {"failed": 1, "passed": 0, "records": 1},
}

THROUGHPUT_DIRTY = {
    "config": {"min_tasks": 2, "refusal_ceiling": 0.5},
    "contributors": [{"contributor": "dave", "counts": {}, "grade": "D",
                       "median_accept_to_submit_hours": 1.0, "median_submit_to_terminal_hours": 2.0,
                       "over_ceiling": True, "refusal_rate": 0.66}],
    "grade_counts": {"D": 1}, "report_version": "1.0", "status": "ceiling_breach",
    "totals": {"contributors": 1, "events": 3, "over_ceiling": 1},
}

BUDGET_DIRTY = {
    "config": {"budget_cap": "20.000000", "horizon_weeks": "4"},
    "history": {"burn_per_week": "7.5", "mean_reward": "5", "records": 6, "span_days": 28.0,
                "stdev_reward": "3.44", "total_rewarded": "30"},
    "open_tasks": {"committed": "11.5", "records": 3}, "over_budget": True,
    "projection": {"high": "62.19", "low": "20.80", "projected_burn": "30",
                    "projected_total": "41.5", "variance_band": "20.69"},
    "report_version": "1.0", "status": "over_budget",
}

SYBIL_DIRTY = {
    "clusters": [{"alert": True, "pairs": [], "score": 1.0, "signals": ["shared_cid"], "size": 2,
                  "wallets": ["rB", "rA"]}],
    "config": {"alert_threshold": 0.8, "burst_window": 300, "length_tolerance": 0.05,
               "link_threshold": 0.5, "weights": {}},
    "report_version": "1.0", "status": "alert",
    "totals": {"alerting_clusters": 1, "clusters": 1, "linked_pairs": 1, "records": 2,
               "scored_pairs": 1, "wallets": 2},
}

SYBIL_NON_ALERTING = {
    "clusters": [{"alert": False, "pairs": [], "score": 0.4, "signals": [], "size": 2,
                  "wallets": ["rC", "rD"]}],
    "config": {"alert_threshold": 0.8, "burst_window": 300, "length_tolerance": 0.05,
               "link_threshold": 0.5, "weights": {}},
    "report_version": "1.0", "status": "clear",
    "totals": {"alerting_clusters": 0, "clusters": 1, "linked_pairs": 0, "records": 2,
               "scored_pairs": 1, "wallets": 2},
}

DUP_DETECTOR_DIRTY = {
    "comparison_count": 1, "config": {"shingle_size": 5, "threshold": 0.6}, "flagged_count": 1,
    "flagged_pairs": [{"overlap_count": 10, "overlapping_shingles": ["a b c"], "score": 0.9,
                        "submission_id_a": "SUB-1", "submission_id_b": "SUB-2"}],
    "record_count": 2, "version": "1.0.0",
}

NOT_A_REPORT = {"hello": "world", "unrelated": True}

EVIDENCE_MANIFEST_LIKE = {
    "algorithm": "sha256", "batch_root": "deadbeef", "entries": [], "leaf_prefix": "leaf:",
    "manifest_version": "1.0", "node_prefix": "node:", "odd_node_policy": "promote", "record_count": 0,
}


class AdapterTestCase(unittest.TestCase):
    """Common assertions shared by every per-adapter test."""

    def assert_matches(self, tool_name, adapter_fn, data):
        result = adapter_fn(data, "r.json")
        self.assertIsNotNone(result, "%s adapter should match its own shape" % tool_name)
        return result

    def assert_no_match(self, adapter_fn, data):
        self.assertIsNone(adapter_fn(data, "r.json"))


class TestLifecycleLinterAdapter(AdapterTestCase):
    def test_matches_dirty(self):
        findings = self.assert_matches("lifecycle-linter", c.adapt_lifecycle_linter, LIFECYCLE_DIRTY)
        self.assertEqual(len(findings), 1)
        f = findings[0]
        self.assertEqual(f["source_tool"], "lifecycle-linter")
        self.assertEqual(f["task_id"], "task_backward")
        self.assertEqual(f["code"], "BACKWARD_TRANSITION")
        self.assertEqual(f["severity"], "error")
        self.assertIn("backward", f["detail"])

    def test_matches_clean_zero_findings(self):
        findings = self.assert_matches("lifecycle-linter", c.adapt_lifecycle_linter, LIFECYCLE_CLEAN)
        self.assertEqual(findings, [])

    def test_does_not_match_unrelated(self):
        self.assert_no_match(c.adapt_lifecycle_linter, NOT_A_REPORT)

    def test_does_not_match_xrpl_auditor_shape(self):
        self.assert_no_match(c.adapt_lifecycle_linter, XRPL_AUDITOR_DIRTY)

    def test_source_report_propagated(self):
        findings = c.adapt_lifecycle_linter(LIFECYCLE_DIRTY, "sub/dir/report.json")
        self.assertEqual(findings[0]["source_report"], "sub/dir/report.json")


class TestXrplAuditorAdapter(AdapterTestCase):
    def test_matches_dirty(self):
        findings = self.assert_matches("xrpl-auditor", c.adapt_xrpl_auditor, XRPL_AUDITOR_DIRTY)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["code"], "UNKNOWN_TASK_ID")
        self.assertEqual(findings[0]["task_id"], "task_x")

    def test_uses_issue_field_as_code_not_code_field(self):
        findings = c.adapt_xrpl_auditor(XRPL_AUDITOR_DIRTY, "r.json")
        self.assertEqual(findings[0]["code"], XRPL_AUDITOR_DIRTY["issues"][0]["issue"])

    def test_does_not_match_lifecycle_shape(self):
        self.assert_no_match(c.adapt_xrpl_auditor, LIFECYCLE_DIRTY)

    def test_does_not_match_reward_reconciler_shape(self):
        self.assert_no_match(c.adapt_xrpl_auditor, REWARD_RECONCILER_DIRTY)

    def test_handles_null_task_id_item(self):
        data = {
            "issues": [{"detail": "malformed", "index": 0, "issue": "MALFORMED_RECORD",
                        "payout_id": None, "task_id": None}],
            "issue_counts": {}, "report_version": "1.0", "status": "issues",
            "totals": {"distinct_tx_hashes": 0, "issues": 1, "payouts": 1, "roster_tasks": 0,
                       "well_formed_payouts": 0},
        }
        findings = c.adapt_xrpl_auditor(data, "r.json")
        self.assertIsNone(findings[0]["task_id"])


class TestRewardReconcilerAdapter(AdapterTestCase):
    def test_matches_dirty(self):
        findings = self.assert_matches("reward-reconciler", c.adapt_reward_reconciler,
                                        REWARD_RECONCILER_DIRTY)
        self.assertEqual(findings[0]["code"], "MISSING_PAYOUT")
        self.assertEqual(findings[0]["task_id"], "task_y")

    def test_synthesizes_detail_when_absent(self):
        findings = c.adapt_reward_reconciler(REWARD_RECONCILER_DIRTY, "r.json")
        self.assertIn("wallet=", findings[0]["detail"])

    def test_does_not_match_xrpl_auditor_shape(self):
        self.assert_no_match(c.adapt_reward_reconciler, XRPL_AUDITOR_DIRTY)

    def test_does_not_match_queue_auditor_shape(self):
        self.assert_no_match(c.adapt_reward_reconciler, QUEUE_AUDITOR_DIRTY)


class TestQueueAuditorAdapter(AdapterTestCase):
    def test_matches_dirty(self):
        findings = self.assert_matches("queue-auditor", c.adapt_queue_auditor, QUEUE_AUDITOR_DIRTY)
        self.assertEqual(findings[0]["code"], "DUPLICATE_TASK_ID")
        self.assertEqual(findings[0]["task_id"], "DUP-1")

    def test_does_not_match_lifecycle_shape(self):
        self.assert_no_match(c.adapt_queue_auditor, LIFECYCLE_DIRTY)

    def test_placeholder_task_id_preserved(self):
        data = {"findings": [{"code": "MALFORMED_RECORD", "detail": "bad row",
                               "task_id": "<index:4>"}], "finding_count": 1, "result": "findings",
                "task_count": 1}
        findings = c.adapt_queue_auditor(data, "r.json")
        self.assertEqual(findings[0]["task_id"], "<index:4>")


class TestWalletReconcilerAdapter(AdapterTestCase):
    def test_matches_dirty(self):
        findings = self.assert_matches("wallet-reconciler", c.adapt_wallet_reconciler,
                                        WALLET_RECONCILER_DIRTY)
        self.assertEqual(findings[0]["code"], "NEGATIVE_RUNNING_BALANCE")

    def test_task_id_always_none(self):
        findings = c.adapt_wallet_reconciler(WALLET_RECONCILER_DIRTY, "r.json")
        self.assertIsNone(findings[0]["task_id"])

    def test_balance_codes_escalated_to_critical(self):
        findings = c.adapt_wallet_reconciler(WALLET_RECONCILER_DIRTY, "r.json")
        self.assertEqual(findings[0]["severity"], "critical")

    def test_other_codes_default_error(self):
        data = dict(WALLET_RECONCILER_DIRTY)
        data["findings"] = [{"code": "UNKNOWN_EVENT_TYPE", "event_id": "e3", "index": 3, "type": "x"}]
        findings = c.adapt_wallet_reconciler(data, "r.json")
        self.assertEqual(findings[0]["severity"], "error")

    def test_missing_detail_is_synthesized(self):
        data = dict(WALLET_RECONCILER_DIRTY)
        data["findings"] = [{"code": "DUPLICATE_EVENT_ID", "event_id": "e2", "first_index": 1,
                              "index": 2}]
        findings = c.adapt_wallet_reconciler(data, "r.json")
        self.assertIn("event_id=", findings[0]["detail"])

    def test_does_not_match_lifecycle_shape(self):
        self.assert_no_match(c.adapt_wallet_reconciler, LIFECYCLE_DIRTY)


class TestPreflightAdapter(AdapterTestCase):
    def test_matches_dirty(self):
        findings = self.assert_matches("preflight", c.adapt_preflight, PREFLIGHT_DIRTY)
        self.assertEqual(findings[0]["code"], "ORPHAN_EVIDENCE")
        self.assertEqual(findings[0]["task_id"], "T9")
        self.assertIn("references unknown task", findings[0]["detail"])

    def test_missing_task_id_is_none(self):
        data = {"issues": [{"code": "DUPLICATE_SUBMISSION_ID", "count": 2,
                             "message": "dup", "submission_id": "S1"}], "ready": False,
                "summary": {}}
        findings = c.adapt_preflight(data, "r.json")
        self.assertIsNone(findings[0]["task_id"])

    def test_does_not_match_link_integrity_shape(self):
        self.assert_no_match(c.adapt_preflight, LINK_INTEGRITY_DIRTY)


class TestLinkIntegrityAdapter(AdapterTestCase):
    def test_matches_dirty(self):
        findings = self.assert_matches("link-integrity", c.adapt_link_integrity, LINK_INTEGRITY_DIRTY)
        self.assertEqual(findings[0]["code"], "UNKNOWN_TASK_REFERENCE")
        self.assertEqual(findings[0]["task_id"], "task-unknown")

    def test_multi_task_violation_has_null_task_id(self):
        findings = c.adapt_link_integrity(LINK_INTEGRITY_MULTI_TASK, "r.json")
        self.assertEqual(len(findings), 1)
        self.assertIsNone(findings[0]["task_id"])

    def test_does_not_match_schema_checker_shape(self):
        self.assert_no_match(c.adapt_link_integrity, SCHEMA_CHECKER_DIRTY)

    def test_does_not_match_preflight_shape(self):
        self.assert_no_match(c.adapt_link_integrity, PREFLIGHT_DIRTY)


class TestSchemaCheckerAdapter(AdapterTestCase):
    def test_matches_dirty(self):
        findings = self.assert_matches("schema-checker", c.adapt_schema_checker, SCHEMA_CHECKER_DIRTY)
        self.assertEqual(findings[0]["code"], "PATTERN_MISMATCH")
        self.assertIsNone(findings[0]["task_id"])

    def test_pointer_folded_into_detail(self):
        findings = c.adapt_schema_checker(SCHEMA_CHECKER_DIRTY, "r.json")
        self.assertIn("/batch_id", findings[0]["detail"])

    def test_does_not_match_link_integrity_shape(self):
        self.assert_no_match(c.adapt_schema_checker, LINK_INTEGRITY_DIRTY)


class TestStalenessMonitorAdapter(AdapterTestCase):
    def test_matches_dirty(self):
        findings = self.assert_matches("staleness-monitor", c.adapt_staleness_monitor, STALENESS_DIRTY)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["severity"], "critical")
        self.assertEqual(findings[0]["task_id"], "S-1")

    def test_findings_must_be_dict_not_list(self):
        self.assert_no_match(c.adapt_staleness_monitor, LIFECYCLE_DIRTY)

    def test_bucket_used_as_severity(self):
        data = {
            "findings": {"warning": [{"code": "STALE_ACCEPTED", "message": "m", "task_id": "S-2",
                                       "bucket": "warning"}], "critical": [], "info": []},
            "generated_at": "x", "windows": {},
        }
        findings = c.adapt_staleness_monitor(data, "r.json")
        self.assertEqual(findings[0]["severity"], "warning")

    def test_all_three_buckets_flattened(self):
        data = {
            "findings": {
                "critical": [{"code": "A", "message": "a", "task_id": "1", "bucket": "critical"}],
                "warning": [{"code": "B", "message": "b", "task_id": "2", "bucket": "warning"}],
                "info": [{"code": "C", "message": "c", "task_id": "3", "bucket": "info"}],
            },
            "generated_at": "x", "windows": {},
        }
        findings = c.adapt_staleness_monitor(data, "r.json")
        self.assertEqual(len(findings), 3)
        self.assertEqual({f["severity"] for f in findings}, {"critical", "warning", "info"})


class TestXrplAddressAdapter(AdapterTestCase):
    def test_matches_dirty(self):
        findings = self.assert_matches("xrpl-address", c.adapt_xrpl_address, XRPL_ADDRESS_DIRTY)
        self.assertEqual(len(findings), 2)

    def test_denylisted_is_critical(self):
        findings = c.adapt_xrpl_address(XRPL_ADDRESS_DIRTY, "r.json")
        codes_sev = {f["code"]: f["severity"] for f in findings}
        self.assertEqual(codes_sev["DENYLISTED"], "critical")
        self.assertEqual(codes_sev["BAD_CHECKSUM"], "error")

    def test_task_id_always_none(self):
        findings = c.adapt_xrpl_address(XRPL_ADDRESS_DIRTY, "r.json")
        self.assertTrue(all(f["task_id"] is None for f in findings))

    def test_valid_address_with_no_issues_yields_nothing(self):
        data = {"addresses": [{"address": "rX", "index": 0, "issues": [], "kind": "classic",
                                "valid": True}], "issue_counts": {}, "report_version": "1.0",
                "status": "ok", "totals": {"addresses": 1, "invalid": 0, "valid": 1}}
        findings = c.adapt_xrpl_address(data, "r.json")
        self.assertEqual(findings, [])

    def test_does_not_match_xrpl_auditor_shape(self):
        self.assert_no_match(c.adapt_xrpl_address, XRPL_AUDITOR_DIRTY)


class TestEventLinterAdapter(AdapterTestCase):
    def test_matches_dirty(self):
        findings = self.assert_matches("event-linter", c.adapt_event_linter, EVENT_LINTER_DIRTY)
        self.assertEqual(findings[0]["code"], "MALFORMED_EVENT")
        self.assertEqual(findings[0]["task_id"], "task_bad")

    def test_falls_back_to_outer_task_id_when_inner_null(self):
        findings = c.adapt_event_linter(EVENT_LINTER_NULL_INNER_TASK, "r.json")
        self.assertEqual(findings[0]["task_id"], "outer_task")

    def test_does_not_match_lifecycle_shape(self):
        self.assert_no_match(c.adapt_event_linter, LIFECYCLE_DIRTY)

    def test_does_not_match_staleness_shape(self):
        self.assert_no_match(c.adapt_event_linter, STALENESS_DIRTY)


class TestEvidenceHarnessAdapter(AdapterTestCase):
    def test_matches_dirty(self):
        findings = self.assert_matches("evidence-harness", c.adapt_evidence_harness,
                                        EVIDENCE_HARNESS_DIRTY)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["code"], "CHECK_FAILED_MIN_TEST_COUNT")

    def test_passing_checks_excluded(self):
        findings = c.adapt_evidence_harness(EVIDENCE_HARNESS_DIRTY, "r.json")
        codes = [f["code"] for f in findings]
        self.assertNotIn("CHECK_FAILED_REQUIRE_HASHES", codes)

    def test_task_id_none(self):
        findings = c.adapt_evidence_harness(EVIDENCE_HARNESS_DIRTY, "r.json")
        self.assertIsNone(findings[0]["task_id"])

    def test_does_not_match_evidence_scorer_shape(self):
        self.assert_no_match(c.adapt_evidence_harness, EVIDENCE_SCORER_DIRTY)


class TestEvidenceScorerAdapter(AdapterTestCase):
    def test_matches_dirty(self):
        findings = self.assert_matches("evidence-scorer", c.adapt_evidence_scorer,
                                        EVIDENCE_SCORER_DIRTY)
        self.assertEqual(findings[0]["code"], "EVIDENCE_SCORE_BELOW_THRESHOLD")
        self.assertEqual(findings[0]["severity"], "warning")

    def test_passed_records_excluded(self):
        data = dict(EVIDENCE_SCORER_DIRTY)
        data["records"] = [{"components": {}, "evidence": {}, "passed": True, "score": 0.9,
                             "submission_id": "s"}]
        findings = c.adapt_evidence_scorer(data, "r.json")
        self.assertEqual(findings, [])

    def test_does_not_match_dup_detector_shape(self):
        self.assert_no_match(c.adapt_evidence_scorer, DUP_DETECTOR_DIRTY)


class TestThroughputReporterAdapter(AdapterTestCase):
    def test_matches_dirty(self):
        findings = self.assert_matches("throughput-reporter", c.adapt_throughput_reporter,
                                        THROUGHPUT_DIRTY)
        self.assertEqual(findings[0]["code"], "REFUSAL_CEILING_BREACH")
        self.assertEqual(findings[0]["severity"], "warning")
        self.assertIsNone(findings[0]["task_id"])

    def test_under_ceiling_excluded(self):
        data = dict(THROUGHPUT_DIRTY)
        data["contributors"] = [{"contributor": "erin", "counts": {}, "grade": "A",
                                  "median_accept_to_submit_hours": 1.0,
                                  "median_submit_to_terminal_hours": 2.0,
                                  "over_ceiling": False, "refusal_rate": 0.0}]
        findings = c.adapt_throughput_reporter(data, "r.json")
        self.assertEqual(findings, [])

    def test_does_not_match_budget_forecaster_shape(self):
        self.assert_no_match(c.adapt_throughput_reporter, BUDGET_DIRTY)


class TestBudgetForecasterAdapter(AdapterTestCase):
    def test_matches_over_budget(self):
        findings = self.assert_matches("budget-forecaster", c.adapt_budget_forecaster, BUDGET_DIRTY)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["severity"], "critical")
        self.assertIsNone(findings[0]["task_id"])

    def test_within_budget_yields_no_findings(self):
        data = dict(BUDGET_DIRTY)
        data["over_budget"] = False
        findings = c.adapt_budget_forecaster(data, "r.json")
        self.assertEqual(findings, [])

    def test_matches_even_without_config(self):
        data = dict(BUDGET_DIRTY)
        del data["config"]
        findings = c.adapt_budget_forecaster(data, "r.json")
        self.assertEqual(len(findings), 1)

    def test_does_not_match_throughput_shape(self):
        self.assert_no_match(c.adapt_budget_forecaster, THROUGHPUT_DIRTY)


class TestSybilDetectorAdapter(AdapterTestCase):
    def test_matches_alerting_cluster(self):
        findings = self.assert_matches("sybil-detector", c.adapt_sybil_detector, SYBIL_DIRTY)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["severity"], "critical")
        self.assertIsNone(findings[0]["task_id"])

    def test_non_alerting_cluster_yields_no_findings(self):
        findings = c.adapt_sybil_detector(SYBIL_NON_ALERTING, "r.json")
        self.assertEqual(findings, [])

    def test_wallets_sorted_in_detail(self):
        findings = c.adapt_sybil_detector(SYBIL_DIRTY, "r.json")
        idx_a = findings[0]["detail"].index("rA")
        idx_b = findings[0]["detail"].index("rB")
        self.assertLess(idx_a, idx_b)

    def test_does_not_match_dup_detector_shape(self):
        self.assert_no_match(c.adapt_sybil_detector, DUP_DETECTOR_DIRTY)


class TestDupDetectorAdapter(AdapterTestCase):
    def test_matches_dirty(self):
        findings = self.assert_matches("dup-detector", c.adapt_dup_detector, DUP_DETECTOR_DIRTY)
        self.assertEqual(findings[0]["code"], "DUPLICATE_CANDIDATE")
        self.assertEqual(findings[0]["severity"], "warning")
        self.assertIsNone(findings[0]["task_id"])

    def test_empty_pairs_yields_no_findings(self):
        data = dict(DUP_DETECTOR_DIRTY)
        data["flagged_pairs"] = []
        findings = c.adapt_dup_detector(data, "r.json")
        self.assertEqual(findings, [])

    def test_does_not_match_sybil_shape(self):
        self.assert_no_match(c.adapt_dup_detector, SYBIL_DIRTY)


class TestUnrecognisedShapes(unittest.TestCase):
    def test_plain_dict_not_a_report(self):
        tool, findings = c.run_adapters(NOT_A_REPORT, "r.json")
        self.assertIsNone(tool)
        self.assertIsNone(findings)

    def test_evidence_manifest_shape_is_unrecognised(self):
        tool, findings = c.run_adapters(EVIDENCE_MANIFEST_LIKE, "r.json")
        self.assertIsNone(tool)

    def test_top_level_list_is_unrecognised(self):
        tool, findings = c.run_adapters([], "r.json")
        self.assertIsNone(tool)

    def test_top_level_string_is_unrecognised(self):
        tool, findings = c.run_adapters("hello", "r.json")
        self.assertIsNone(tool)

    def test_top_level_number_is_unrecognised(self):
        tool, findings = c.run_adapters(42, "r.json")
        self.assertIsNone(tool)

    def test_top_level_null_is_unrecognised(self):
        tool, findings = c.run_adapters(None, "r.json")
        self.assertIsNone(tool)

    def test_every_known_adapter_matches_exactly_one_of_the_registry(self):
        samples = [
            LIFECYCLE_DIRTY, XRPL_AUDITOR_DIRTY, REWARD_RECONCILER_DIRTY, QUEUE_AUDITOR_DIRTY,
            WALLET_RECONCILER_DIRTY, PREFLIGHT_DIRTY, LINK_INTEGRITY_DIRTY, SCHEMA_CHECKER_DIRTY,
            STALENESS_DIRTY, XRPL_ADDRESS_DIRTY, EVENT_LINTER_DIRTY, EVIDENCE_HARNESS_DIRTY,
            EVIDENCE_SCORER_DIRTY, THROUGHPUT_DIRTY, BUDGET_DIRTY, SYBIL_DIRTY, DUP_DETECTOR_DIRTY,
        ]
        for sample in samples:
            matches = [name for name, fn in c.ADAPTERS if fn(sample, "r.json") is not None]
            self.assertEqual(len(matches), 1,
                              "expected exactly one adapter match, got %r for sample %r" %
                              (matches, sample))


# ---------------------------------------------------------------------------
# build_report / pipeline (using real files on disk)
# ---------------------------------------------------------------------------

class TestBuildReportPipeline(TempDirMixin, unittest.TestCase):
    def test_empty_directory_zero_findings(self):
        result, exit_code = c.build_report(self.tmp, None)
        self.assertEqual(exit_code, c.EXIT_NO_FINDINGS)
        self.assertEqual(result["totals"]["findings_total"], 0)
        self.assertEqual(result["totals"]["reports_scanned"], 0)
        self.assertEqual(result["severity_rollup"]["worst_severity"], None)

    def test_directory_with_only_non_json_files(self):
        write(self.p("notes.txt"), "not json")
        write(self.p("readme.md"), "# hi")
        result, exit_code = c.build_report(self.tmp, None)
        self.assertEqual(exit_code, c.EXIT_NO_FINDINGS)
        self.assertEqual(result["totals"]["reports_scanned"], 0)

    def test_single_clean_report_exit_zero(self):
        write(self.p("a.json"), LIFECYCLE_CLEAN)
        result, exit_code = c.build_report(self.tmp, None)
        self.assertEqual(exit_code, c.EXIT_NO_FINDINGS)

    def test_single_dirty_report_exit_one(self):
        write(self.p("a.json"), LIFECYCLE_DIRTY)
        result, exit_code = c.build_report(self.tmp, None)
        self.assertEqual(exit_code, c.EXIT_FINDINGS)
        self.assertEqual(result["totals"]["findings_total"], 1)

    def test_invalid_json_file_becomes_unrecognised_finding(self):
        write(self.p("broken.json"), "{not valid json,,,")
        result, exit_code = c.build_report(self.tmp, None)
        self.assertEqual(exit_code, c.EXIT_FINDINGS)
        self.assertEqual(result["totals"]["reports_unrecognised"], 1)
        codes = [f["code"] for f in result["ungrouped_findings"]]
        self.assertIn("INVALID_JSON", codes)

    def test_empty_array_file_becomes_unrecognised_finding(self):
        write(self.p("empty.json"), "[]")
        result, exit_code = c.build_report(self.tmp, None)
        self.assertEqual(exit_code, c.EXIT_FINDINGS)
        codes = [f["code"] for f in result["ungrouped_findings"]]
        self.assertIn("UNRECOGNISED_REPORT_SHAPE", codes)

    def test_unrecognised_report_shape_never_silently_dropped(self):
        write(self.p("odd.json"), NOT_A_REPORT)
        result, exit_code = c.build_report(self.tmp, None)
        self.assertEqual(result["totals"]["reports_scanned"], 1)
        self.assertEqual(result["totals"]["reports_unrecognised"], 1)
        self.assertEqual(result["totals"]["findings_total"], 1)
        self.assertEqual(exit_code, c.EXIT_FINDINGS)

    def test_unrecognised_report_survives_critical_threshold(self):
        write(self.p("odd.json"), NOT_A_REPORT)
        result, exit_code = c.build_report(self.tmp, "critical")
        self.assertEqual(result["totals"]["findings_total"], 1)
        self.assertEqual(exit_code, c.EXIT_FINDINGS)

    def test_unicode_in_detail_survives_round_trip(self):
        data = dict(LIFECYCLE_DIRTY)
        data["findings"] = [{"code": "X", "detail": "unicode café éè 中文",
                              "line": 1, "task_id": "t1"}]
        write(self.p("u.json"), data)
        result, exit_code = c.build_report(self.tmp, None)
        text = c.canonical_dumps(result)
        parsed = json.loads(text)
        detail = parsed["findings_by_task"][0]["findings"][0]["detail"]
        self.assertIn("café", detail)
        self.assertTrue(text.isascii())

    def test_nested_subdirectories_are_scanned(self):
        write(self.p("sub", "deep", "a.json"), LIFECYCLE_DIRTY)
        result, exit_code = c.build_report(self.tmp, None)
        self.assertEqual(exit_code, c.EXIT_FINDINGS)
        self.assertEqual(result["reports"][0]["source_report"], "sub/deep/a.json")

    def test_two_identical_content_files_both_counted_as_separate_sources(self):
        write(self.p("one.json"), LIFECYCLE_DIRTY)
        write(self.p("two.json"), LIFECYCLE_DIRTY)
        result, exit_code = c.build_report(self.tmp, None)
        self.assertEqual(result["totals"]["reports_scanned"], 2)
        self.assertEqual(result["totals"]["findings_total"], 2)
        group = result["findings_by_task"][0]
        self.assertEqual(len(group["contributing_sources"]), 2)
        self.assertEqual(len(group["findings"]), 2)

    def test_null_task_id_goes_to_ungrouped(self):
        data = dict(XRPL_AUDITOR_DIRTY)
        data["issues"] = [{"detail": "x", "index": 0, "issue": "MALFORMED_RECORD",
                            "payout_id": None, "task_id": None}]
        write(self.p("a.json"), data)
        result, exit_code = c.build_report(self.tmp, None)
        self.assertEqual(result["findings_by_task"], [])
        self.assertEqual(len(result["ungrouped_findings"]), 1)
        self.assertIsNone(result["ungrouped_findings"][0]["task_id"])

    def test_cross_tool_merge_same_task_id(self):
        write(self.p("lifecycle.json"), {
            "finding_counts": {}, "findings": [{"code": "X", "detail": "d1", "line": 1,
                                                 "task_id": "shared"}],
            "report_version": "1.0", "status": "issues", "totals": {"events": 1, "findings": 1, "tasks": 1},
        })
        write(self.p("xrpl.json"), {
            "issue_counts": {}, "issues": [{"detail": "d2", "index": 0, "issue": "Y",
                                             "payout_id": "p1", "task_id": "shared"}],
            "report_version": "1.0", "status": "issues",
            "totals": {"distinct_tx_hashes": 1, "issues": 1, "payouts": 1, "roster_tasks": 1,
                       "well_formed_payouts": 1},
        })
        result, exit_code = c.build_report(self.tmp, None)
        self.assertEqual(len(result["findings_by_task"]), 1)
        group = result["findings_by_task"][0]
        self.assertEqual(group["task_id"], "shared")
        self.assertEqual(len(group["findings"]), 2)
        tools = {s["source_tool"] for s in group["contributing_sources"]}
        self.assertEqual(tools, {"lifecycle-linter", "xrpl-auditor"})

    def test_severity_rollup_counts(self):
        write(self.p("budget.json"), BUDGET_DIRTY)  # 1 critical
        write(self.p("dup.json"), DUP_DETECTOR_DIRTY)  # 1 warning
        write(self.p("lifecycle.json"), LIFECYCLE_DIRTY)  # 1 error
        result, exit_code = c.build_report(self.tmp, None)
        counts = result["severity_rollup"]["counts"]
        self.assertEqual(counts["critical"], 1)
        self.assertEqual(counts["warning"], 1)
        self.assertEqual(counts["error"], 1)
        self.assertEqual(counts["info"], 0)
        self.assertEqual(result["severity_rollup"]["worst_severity"], "critical")

    def test_severity_threshold_filters_lower_severities(self):
        write(self.p("budget.json"), BUDGET_DIRTY)  # critical
        write(self.p("dup.json"), DUP_DETECTOR_DIRTY)  # warning
        result, exit_code = c.build_report(self.tmp, "critical")
        self.assertEqual(result["totals"]["findings_total"], 1)
        self.assertEqual(result["severity_rollup"]["counts"]["warning"], 0)

    def test_severity_threshold_none_keeps_everything(self):
        write(self.p("dup.json"), DUP_DETECTOR_DIRTY)
        r_none, _ = c.build_report(self.tmp, None)
        r_info, _ = c.build_report(self.tmp, "info")
        self.assertEqual(r_none["totals"]["findings_total"], r_info["totals"]["findings_total"])

    def test_severity_threshold_can_zero_out_findings_and_exit_zero(self):
        write(self.p("dup.json"), DUP_DETECTOR_DIRTY)  # warning only
        result, exit_code = c.build_report(self.tmp, "critical")
        self.assertEqual(exit_code, c.EXIT_NO_FINDINGS)
        self.assertEqual(result["totals"]["findings_total"], 0)

    def test_reports_list_always_includes_all_scanned_files_regardless_of_threshold(self):
        write(self.p("dup.json"), DUP_DETECTOR_DIRTY)
        result, exit_code = c.build_report(self.tmp, "critical")
        self.assertEqual(len(result["reports"]), 1)
        self.assertEqual(result["reports"][0]["finding_count"], 1)

    def test_no_absolute_paths_anywhere_in_output(self):
        write(self.p("a.json"), LIFECYCLE_DIRTY)
        result, exit_code = c.build_report(self.tmp, None)
        text = c.canonical_dumps(result)
        self.assertNotIn(self.tmp, text)

    def test_findings_by_task_sorted_by_task_id(self):
        write(self.p("a.json"), {
            "finding_counts": {}, "findings": [
                {"code": "X", "detail": "d", "line": 1, "task_id": "zeta"},
                {"code": "X", "detail": "d", "line": 1, "task_id": "alpha"},
            ], "report_version": "1.0", "status": "issues",
            "totals": {"events": 1, "findings": 2, "tasks": 2},
        })
        result, exit_code = c.build_report(self.tmp, None)
        ids = [g["task_id"] for g in result["findings_by_task"]]
        self.assertEqual(ids, sorted(ids))

    def test_unreadable_file_permission_error_becomes_finding(self):
        path = self.p("locked.json")
        write(path, LIFECYCLE_CLEAN)
        try:
            os.chmod(path, 0o000)
            if os.access(path, os.R_OK):
                self.skipTest("running as a user that bypasses file permissions")
            result, exit_code = c.build_report(self.tmp, None)
            self.assertEqual(result["totals"]["reports_unrecognised"], 1)
        finally:
            os.chmod(path, 0o644)


# ---------------------------------------------------------------------------
# determinism
# ---------------------------------------------------------------------------

class TestDeterminism(TempDirMixin, unittest.TestCase):
    def _populate(self, root):
        write(os.path.join(root, "lifecycle.json"), LIFECYCLE_DIRTY)
        write(os.path.join(root, "xrpl.json"), XRPL_AUDITOR_DIRTY)
        write(os.path.join(root, "sub", "dup.json"), DUP_DETECTOR_DIRTY)
        write(os.path.join(root, "odd.json"), NOT_A_REPORT)

    def test_two_runs_byte_identical(self):
        self._populate(self.tmp)
        result1, _ = c.build_report(self.tmp, None)
        result2, _ = c.build_report(self.tmp, None)
        self.assertEqual(c.canonical_dumps(result1), c.canonical_dumps(result2))

    def test_relocated_directory_produces_identical_output(self):
        self._populate(self.p("orig"))
        result1, _ = c.build_report(self.p("orig"), None)
        text1 = c.canonical_dumps(result1)

        relocated = tempfile.mkdtemp(prefix="consolidate_reloc_")
        try:
            shutil.copytree(self.p("orig"), os.path.join(relocated, "copy"))
            result2, _ = c.build_report(os.path.join(relocated, "copy"), None)
            text2 = c.canonical_dumps(result2)
            self.assertEqual(text1, text2)
        finally:
            shutil.rmtree(relocated, ignore_errors=True)

    def test_output_independent_of_filesystem_walk_order(self):
        # discover_json_files always ends with a full sort, so the walk
        # order returned by os.walk cannot influence the final result.
        self._populate(self.tmp)
        files_a = c.discover_json_files(self.tmp)
        files_b = list(reversed(c.discover_json_files(self.tmp)))
        self.assertNotEqual(files_a, files_b)  # sanity: reversal actually differs
        self.assertEqual(sorted(files_a), files_a)


# ---------------------------------------------------------------------------
# CLI: argument parsing, exit codes, stdout/-o output
# ---------------------------------------------------------------------------

class TestCLI(TempDirMixin, unittest.TestCase):
    def run_cli(self, argv):
        old_stdout, old_stderr = sys.stdout, sys.stderr
        sys.stdout = out = io.StringIO()
        sys.stderr = err = io.StringIO()
        try:
            code = c.main(argv)
        finally:
            sys.stdout, sys.stderr = old_stdout, old_stderr
        return code, out.getvalue(), err.getvalue()

    def test_nonexistent_root_exit_2(self):
        code, out, err = self.run_cli([self.p("does_not_exist")])
        self.assertEqual(code, c.EXIT_USAGE_ERROR)
        self.assertIn("does not exist", err)

    def test_root_is_a_file_not_directory_exit_2(self):
        write(self.p("file.json"), {})
        code, out, err = self.run_cli([self.p("file.json")])
        self.assertEqual(code, c.EXIT_USAGE_ERROR)

    def test_clean_directory_exit_0(self):
        write(self.p("clean.json"), LIFECYCLE_CLEAN)
        code, out, err = self.run_cli([self.tmp])
        self.assertEqual(code, c.EXIT_NO_FINDINGS)

    def test_dirty_directory_exit_1(self):
        write(self.p("dirty.json"), LIFECYCLE_DIRTY)
        code, out, err = self.run_cli([self.tmp])
        self.assertEqual(code, c.EXIT_FINDINGS)

    def test_stdout_contains_valid_json_with_trailing_newline(self):
        write(self.p("dirty.json"), LIFECYCLE_DIRTY)
        code, out, err = self.run_cli([self.tmp])
        self.assertTrue(out.endswith("\n"))
        json.loads(out)  # must parse

    def test_output_flag_writes_file_not_stdout(self):
        write(self.p("dirty.json"), LIFECYCLE_DIRTY)
        outfile = self.p("out.json")
        code, out, err = self.run_cli([self.tmp, "-o", outfile])
        self.assertEqual(out, "")
        self.assertTrue(os.path.exists(outfile))
        with open(outfile) as fh:
            json.load(fh)

    def test_output_long_flag_equivalent(self):
        write(self.p("dirty.json"), LIFECYCLE_DIRTY)
        outfile = self.p("out2.json")
        code, out, err = self.run_cli([self.tmp, "--output", outfile])
        self.assertTrue(os.path.exists(outfile))

    def test_severity_threshold_flag_accepted(self):
        write(self.p("dirty.json"), LIFECYCLE_DIRTY)
        code, out, err = self.run_cli([self.tmp, "--severity-threshold", "critical"])
        self.assertEqual(code, c.EXIT_NO_FINDINGS)

    def test_severity_threshold_invalid_choice_exit_2(self):
        write(self.p("dirty.json"), LIFECYCLE_DIRTY)
        with self.assertRaises(SystemExit) as ctx:
            self.run_cli([self.tmp, "--severity-threshold", "not-a-level"])
        self.assertEqual(ctx.exception.code, c.EXIT_USAGE_ERROR)

    def test_missing_required_root_arg_exit_2(self):
        with self.assertRaises(SystemExit) as ctx:
            self.run_cli([])
        self.assertEqual(ctx.exception.code, c.EXIT_USAGE_ERROR)

    def test_help_flag_exits_zero(self):
        with self.assertRaises(SystemExit) as ctx:
            self.run_cli(["--help"])
        self.assertEqual(ctx.exception.code, 0)

    def test_output_written_file_ends_with_single_newline(self):
        write(self.p("dirty.json"), LIFECYCLE_DIRTY)
        outfile = self.p("out3.json")
        self.run_cli([self.tmp, "-o", outfile])
        with open(outfile, "rb") as fh:
            content = fh.read()
        self.assertTrue(content.endswith(b"\n"))
        self.assertFalse(content.endswith(b"\n\n"))

    def test_two_cli_runs_produce_byte_identical_output_files(self):
        write(self.p("scan", "dirty.json"), LIFECYCLE_DIRTY)
        write(self.p("scan", "sub", "xrpl.json"), XRPL_AUDITOR_DIRTY)
        out1, out2 = self.p("r1.json"), self.p("r2.json")
        self.run_cli([self.p("scan"), "-o", out1])
        self.run_cli([self.p("scan"), "-o", out2])
        with open(out1, "rb") as f1, open(out2, "rb") as f2:
            self.assertEqual(f1.read(), f2.read())

    def test_output_file_written_inside_scanned_root_excludes_itself_on_rerun(self):
        # Regression test for a real bug found during development: writing
        # -o inside the same directory being scanned used to make the next
        # run "discover" the previous run's own output as an extra
        # unrecognised report, corrupting totals/exit code between runs.
        write(self.p("dirty.json"), LIFECYCLE_DIRTY)
        outfile = self.p("report.json")
        code1, _, _ = self.run_cli([self.tmp, "-o", outfile])
        with open(outfile) as fh:
            first = json.load(fh)
        code2, _, _ = self.run_cli([self.tmp, "-o", outfile])
        with open(outfile) as fh:
            second = json.load(fh)
        self.assertEqual(code1, code2)
        self.assertEqual(first["totals"]["reports_scanned"], 1)
        self.assertEqual(second["totals"]["reports_scanned"], 1)
        self.assertEqual(first, second)

    def test_no_absolute_path_leakage_via_cli(self):
        write(self.p("dirty.json"), LIFECYCLE_DIRTY)
        code, out, err = self.run_cli([self.tmp])
        self.assertNotIn(self.tmp, out)

    def test_unwritable_output_path_exit_2(self):
        write(self.p("dirty.json"), LIFECYCLE_DIRTY)
        bad_path = self.p("no_such_dir", "out.json")
        code, out, err = self.run_cli([self.tmp, "-o", bad_path])
        self.assertEqual(code, c.EXIT_USAGE_ERROR)


# ---------------------------------------------------------------------------
# fixture directories shipped with this tool
# ---------------------------------------------------------------------------

class TestShippedFixtures(unittest.TestCase):
    HERE = os.path.dirname(os.path.abspath(__file__))

    def test_reports_clean_directory_exists(self):
        self.assertTrue(os.path.isdir(os.path.join(self.HERE, "reports_clean")))

    def test_reports_mixed_directory_exists(self):
        self.assertTrue(os.path.isdir(os.path.join(self.HERE, "reports_mixed")))

    def test_reports_clean_consolidates_to_exit_zero(self):
        root = os.path.join(self.HERE, "reports_clean")
        result, exit_code = c.build_report(root, None)
        self.assertEqual(exit_code, c.EXIT_NO_FINDINGS)
        self.assertEqual(result["totals"]["findings_total"], 0)
        self.assertGreater(result["totals"]["reports_scanned"], 0)
        self.assertEqual(result["totals"]["reports_unrecognised"], 0)

    def test_reports_mixed_consolidates_to_exit_one(self):
        root = os.path.join(self.HERE, "reports_mixed")
        result, exit_code = c.build_report(root, None)
        self.assertEqual(exit_code, c.EXIT_FINDINGS)
        self.assertGreater(result["totals"]["findings_total"], 0)

    def test_reports_mixed_contains_several_distinct_source_tools(self):
        root = os.path.join(self.HERE, "reports_mixed")
        result, exit_code = c.build_report(root, None)
        tools = {r["source_tool"] for r in result["reports"] if r["recognised"]}
        self.assertGreaterEqual(len(tools), 5)

    def test_reports_mixed_includes_at_least_one_unrecognised_report(self):
        root = os.path.join(self.HERE, "reports_mixed")
        result, exit_code = c.build_report(root, None)
        self.assertGreaterEqual(result["totals"]["reports_unrecognised"], 1)

    def test_reports_mixed_has_a_cross_tool_task_merge(self):
        root = os.path.join(self.HERE, "reports_mixed")
        result, exit_code = c.build_report(root, None)
        multi_source_groups = [g for g in result["findings_by_task"]
                                if len(g["contributing_sources"]) > 1]
        self.assertGreaterEqual(len(multi_source_groups), 1)

    def test_reports_clean_no_absolute_paths(self):
        root = os.path.join(self.HERE, "reports_clean")
        result, exit_code = c.build_report(root, None)
        text = c.canonical_dumps(result)
        self.assertNotIn(self.HERE, text)


# ---------------------------------------------------------------------------
# extra edge cases explicitly called out in the task spec
# ---------------------------------------------------------------------------

class TestExplicitEdgeCases(TempDirMixin, unittest.TestCase):
    def test_directory_containing_non_json_files_only(self):
        write(self.p("a.csv"), "x,y\n1,2")
        write(self.p("b.yaml"), "k: v")
        result, exit_code = c.build_report(self.tmp, None)
        self.assertEqual(exit_code, c.EXIT_NO_FINDINGS)
        self.assertEqual(result["totals"]["reports_scanned"], 0)

    def test_json_valid_but_not_a_report(self):
        write(self.p("a.json"), {"just": "data", "no": "report shape"})
        result, exit_code = c.build_report(self.tmp, None)
        self.assertEqual(result["totals"]["reports_unrecognised"], 1)
        self.assertEqual(exit_code, c.EXIT_FINDINGS)

    def test_two_identical_reports_in_different_files_not_collapsed(self):
        write(self.p("x1.json"), XRPL_AUDITOR_DIRTY)
        write(self.p("x2.json"), XRPL_AUDITOR_DIRTY)
        result, exit_code = c.build_report(self.tmp, None)
        self.assertEqual(len(result["findings_by_task"][0]["findings"]), 2)
        self.assertEqual(len(result["findings_by_task"][0]["contributing_sources"]), 2)

    def test_finding_with_null_task_id(self):
        data = copy.deepcopy(XRPL_AUDITOR_DIRTY)
        data["issues"][0]["task_id"] = None
        write(self.p("a.json"), data)
        result, exit_code = c.build_report(self.tmp, None)
        self.assertEqual(result["findings_by_task"], [])
        self.assertEqual(len(result["ungrouped_findings"]), 1)

    def test_nested_subdirectories_multiple_levels(self):
        write(self.p("a", "b", "c", "d", "deep.json"), LIFECYCLE_DIRTY)
        result, exit_code = c.build_report(self.tmp, None)
        self.assertEqual(result["reports"][0]["source_report"], "a/b/c/d/deep.json")

    def test_report_file_that_is_empty_array(self):
        write(self.p("a.json"), "[]")
        result, exit_code = c.build_report(self.tmp, None)
        self.assertEqual(result["totals"]["reports_unrecognised"], 1)

    def test_unicode_in_detail_field(self):
        data = copy.deepcopy(LIFECYCLE_DIRTY)
        data["findings"][0]["detail"] = "日本語 😀 café"
        write(self.p("a.json"), data)
        result, exit_code = c.build_report(self.tmp, None)
        text = c.canonical_dumps(result)
        self.assertTrue(text.isascii())
        parsed = json.loads(text)
        self.assertIn("😀", parsed["findings_by_task"][0]["findings"][0]["detail"])

    def test_empty_directory(self):
        result, exit_code = c.build_report(self.tmp, None)
        self.assertEqual(exit_code, c.EXIT_NO_FINDINGS)
        self.assertEqual(result, {
            "consolidated_version": "1.0",
            "exit_code": 0,
            "params": {"severity_threshold": None},
            "reports": [],
            "severity_rollup": {"counts": {"info": 0, "warning": 0, "error": 0, "critical": 0},
                                 "worst_severity": None},
            "totals": {"reports_scanned": 0, "reports_recognised": 0, "reports_unrecognised": 0,
                       "findings_total": 0, "tasks_with_findings": 0},
            "findings_by_task": [],
            "ungrouped_findings": [],
        })


if __name__ == "__main__":
    unittest.main()
