#!/usr/bin/env python3
"""Unit tests for contradict.py. Standard library only (unittest)."""

import contextlib
import io
import json
import os
import shutil
import stat
import sys
import tempfile
import textwrap
import unittest

import contradict as c

HERE = os.path.dirname(os.path.abspath(__file__))


def write_json(path, obj):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh)


def write_text(path, text):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def run_cli(argv):
    """Run contradict.main(argv), capturing stdout/stderr and exit code."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = c.main(argv)
    return code, out.getvalue(), err.getvalue()


class TempDirMixin:
    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="contradict_test_")
        self.addCleanup(shutil.rmtree, self._tmp, ignore_errors=True)

    def mkcase(self, name="case"):
        path = os.path.join(self._tmp, name)
        os.makedirs(path, exist_ok=True)
        return path

    def mkroot(self):
        return self._tmp


# ==========================================================================
# Canonical JSON
# ==========================================================================

class CanonicalJsonTests(unittest.TestCase):
    def test_sorted_keys(self):
        text = c.canonical_dumps({"b": 1, "a": 2})
        self.assertTrue(text.startswith('{"a":2,"b":1}'))

    def test_compact_separators(self):
        text = c.canonical_dumps({"a": [1, 2], "b": {"c": 3}})
        self.assertNotIn(", ", text)
        self.assertNotIn(": ", text)

    def test_trailing_newline(self):
        text = c.canonical_dumps({"a": 1})
        self.assertTrue(text.endswith("\n"))
        self.assertEqual(text.count("\n"), 1)

    def test_ensure_ascii(self):
        text = c.canonical_dumps({"a": "café"})
        self.assertNotIn("é", text)
        self.assertIn("\\u00e9", text)

    def test_nested_sorted_keys(self):
        text = c.canonical_dumps({"z": {"y": 1, "x": 2}})
        self.assertIn('"x":2,"y":1', text)

    def test_round_trips(self):
        obj = {"a": [1, 2, {"z": 1, "a": 2}], "b": None, "c": True}
        text = c.canonical_dumps(obj)
        self.assertEqual(json.loads(text), obj)

    def test_deterministic_across_calls(self):
        obj = {"k" + str(i): i for i in range(20)}
        self.assertEqual(c.canonical_dumps(obj), c.canonical_dumps(dict(reversed(list(obj.items())))))


# ==========================================================================
# Adapter registry
# ==========================================================================

class AdapterRegistryTests(unittest.TestCase):
    def test_ten_adapters_registered(self):
        self.assertEqual(len(c.ADAPTERS), 10)

    def test_every_adapter_has_required_keys(self):
        for aid, adapter in c.ADAPTERS.items():
            with self.subTest(adapter=aid):
                self.assertIn("dirname", adapter)
                self.assertIn("script", adapter)
                self.assertIn("required_inputs", adapter)
                self.assertIn("args", adapter)

    def test_every_adapter_required_inputs_is_tuple(self):
        for aid, adapter in c.ADAPTERS.items():
            with self.subTest(adapter=aid):
                self.assertIsInstance(adapter["required_inputs"], tuple)
                self.assertGreater(len(adapter["required_inputs"]), 0)

    def test_known_filenames_derived_from_all_adapters(self):
        expected = set()
        for adapter in c.ADAPTERS.values():
            expected.update(adapter["required_inputs"])
        self.assertEqual(expected, set(c.KNOWN_INPUT_FILENAMES))

    def test_queue_auditor_and_staleness_use_distinct_filenames(self):
        # Regression test for a real bug: both tools were once wired to
        # the same "tasks.json" filename despite requiring incompatible
        # record schemas (queue-auditor needs a wrapped {"tasks": [...]}
        # object with list/reward/created_at/deadline; staleness-monitor
        # needs a bare array with only task_id/title/status/created_at/
        # deadline). Sharing a filename made every case built for one of
        # them spuriously "applicable" to the other, which then failed
        # with EXECUTION_FAILURE on the mismatched schema.
        qa = set(c.ADAPTERS["queue-auditor"]["required_inputs"])
        sm = set(c.ADAPTERS["staleness-monitor"]["required_inputs"])
        self.assertTrue(qa.isdisjoint(sm))

    def test_preflight_and_queue_auditor_do_not_share_a_filename(self):
        pf = set(c.ADAPTERS["preflight"]["required_inputs"])
        qa = set(c.ADAPTERS["queue-auditor"]["required_inputs"])
        self.assertTrue(pf.isdisjoint(qa))

    def test_default_checkers_root_is_next_to_script(self):
        self.assertTrue(c.DEFAULT_CHECKERS_ROOT.endswith("checkers"))
        self.assertTrue(os.path.isabs(c.DEFAULT_CHECKERS_ROOT))

    def test_reference_now_is_a_fixed_constant_not_wall_clock(self):
        # Called twice, must be identical -- this is a plain string
        # constant, never datetime.now()/utcnow()/time.time().
        self.assertEqual(c.REFERENCE_NOW, "2026-06-01T00:00:00Z")


def _make_applicability_test(adapter_id):
    def test_applicable_when_inputs_present(self):
        case_dir = self.mkcase()
        for name in c.ADAPTERS[adapter_id]["required_inputs"]:
            write_json(os.path.join(case_dir, name), [])
        self.assertTrue(c.is_applicable(adapter_id, case_dir))

    def test_not_applicable_when_inputs_missing(self):
        case_dir = self.mkcase()
        self.assertFalse(c.is_applicable(adapter_id, case_dir))

    return test_applicable_when_inputs_present, test_not_applicable_when_inputs_missing


class AdapterApplicabilityTests(TempDirMixin, unittest.TestCase):
    pass


for _aid in sorted(c.ADAPTERS):
    _present, _missing = _make_applicability_test(_aid)
    _present.__name__ = "test_applicable_{}".format(_aid.replace("-", "_"))
    _missing.__name__ = "test_not_applicable_{}".format(_aid.replace("-", "_"))
    setattr(AdapterApplicabilityTests, _present.__name__, _present)
    setattr(AdapterApplicabilityTests, _missing.__name__, _missing)


class AdapterApplicabilityPartialTests(TempDirMixin, unittest.TestCase):
    def test_preflight_needs_both_files(self):
        case_dir = self.mkcase()
        write_json(os.path.join(case_dir, "tasks.json"), [])
        self.assertFalse(c.is_applicable("preflight", case_dir))
        write_json(os.path.join(case_dir, "evidence.json"), [])
        self.assertTrue(c.is_applicable("preflight", case_dir))

    def test_link_integrity_needs_both_files(self):
        case_dir = self.mkcase()
        write_json(os.path.join(case_dir, "lifecycle.json"), [])
        self.assertFalse(c.is_applicable("link-integrity", case_dir))
        write_json(os.path.join(case_dir, "evidence.json"), [])
        self.assertTrue(c.is_applicable("link-integrity", case_dir))

    def test_reward_reconciler_needs_both_files(self):
        case_dir = self.mkcase()
        write_json(os.path.join(case_dir, "expected_rewards.json"), [])
        self.assertFalse(c.is_applicable("reward-reconciler", case_dir))
        write_json(os.path.join(case_dir, "recorded_payouts.json"), [])
        self.assertTrue(c.is_applicable("reward-reconciler", case_dir))

    def test_reward_anomaly_needs_both_files(self):
        case_dir = self.mkcase()
        write_json(os.path.join(case_dir, "reward_tasks.json"), [])
        self.assertFalse(c.is_applicable("reward-anomaly", case_dir))
        write_json(os.path.join(case_dir, "reward_payouts.json"), [])
        self.assertTrue(c.is_applicable("reward-anomaly", case_dir))

    def test_directory_with_wrong_filename_is_not_applicable(self):
        case_dir = self.mkcase()
        write_json(os.path.join(case_dir, "wrong_name.json"), [])
        for aid in c.ADAPTERS:
            self.assertFalse(c.is_applicable(aid, case_dir))


# ==========================================================================
# discover_checker_path / run_checker
# ==========================================================================

FAKE_OK_SCRIPT = textwrap.dedent("""
    import json, sys
    print(json.dumps({"status": "clean", "findings": []}))
    sys.exit(0)
""")

FAKE_FINDINGS_SCRIPT = textwrap.dedent("""
    import json, sys
    print(json.dumps({"status": "issues", "findings": [{"code": "X"}]}))
    sys.exit(1)
""")

FAKE_EXIT2_SCRIPT = textwrap.dedent("""
    import sys
    print("bad input", file=sys.stderr)
    sys.exit(2)
""")

FAKE_BADEXIT_SCRIPT = textwrap.dedent("""
    import sys
    sys.exit(17)
""")

FAKE_NONJSON_SCRIPT = textwrap.dedent("""
    print("not json at all")
""")

FAKE_NONOBJECT_JSON_SCRIPT = textwrap.dedent("""
    import json
    print(json.dumps([1, 2, 3]))
""")

FAKE_SLOW_SCRIPT = textwrap.dedent("""
    import time
    time.sleep(5)
""")

FAKE_ECHO_ARGS_SCRIPT = textwrap.dedent("""
    import json, sys
    print(json.dumps({"status": "clean", "argv": sys.argv[1:], "cwd_files": []}))
""")


class FakeCheckerMixin(TempDirMixin):
    def make_fake_checker(self, adapter_id, script_body, dirname=None, script_name=None,
                           extra_args=()):
        base = adapter_id if dirname is None else dirname
        checkers_root = os.path.join(self._tmp, "fake_checkers_root")
        real_dirname = c.ADAPTERS[adapter_id]["dirname"] if dirname is None else dirname
        real_script = c.ADAPTERS[adapter_id]["script"] if script_name is None else script_name
        script_dir = os.path.join(checkers_root, real_dirname)
        os.makedirs(script_dir, exist_ok=True)
        script_path = os.path.join(script_dir, real_script)
        write_text(script_path, script_body)
        return checkers_root


class RunCheckerTests(FakeCheckerMixin, unittest.TestCase):
    def test_unavailable_when_checkers_root_empty(self):
        case_dir = self.mkcase()
        result = c.run_checker("preflight", case_dir, os.path.join(self._tmp, "nope"))
        self.assertEqual(result["state"], "unavailable")
        self.assertIsNone(result["report"])
        self.assertEqual(result["id"], "preflight")

    def test_unavailable_detail_has_no_path(self):
        case_dir = self.mkcase()
        result = c.run_checker("preflight", case_dir, os.path.join(self._tmp, "nope"))
        self.assertNotIn(self._tmp, result["detail"])
        self.assertNotIn("/", result["detail"])

    def test_ok_state_parses_json_report(self):
        case_dir = self.mkcase()
        root = self.make_fake_checker("preflight", FAKE_OK_SCRIPT)
        result = c.run_checker("preflight", case_dir, root)
        self.assertEqual(result["state"], "ok")
        self.assertEqual(result["returncode"], 0)
        self.assertEqual(result["report"], {"status": "clean", "findings": []})

    def test_ok_state_with_findings_exit_1(self):
        case_dir = self.mkcase()
        root = self.make_fake_checker("preflight", FAKE_FINDINGS_SCRIPT)
        result = c.run_checker("preflight", case_dir, root)
        self.assertEqual(result["state"], "ok")
        self.assertEqual(result["returncode"], 1)

    def test_exit_2_is_execution_failure(self):
        case_dir = self.mkcase()
        root = self.make_fake_checker("preflight", FAKE_EXIT2_SCRIPT)
        result = c.run_checker("preflight", case_dir, root)
        self.assertEqual(result["state"], "execution_failure")
        self.assertEqual(result["returncode"], 2)
        self.assertIsNone(result["report"])

    def test_exit_2_detail_never_contains_raw_stderr(self):
        case_dir = self.mkcase()
        root = self.make_fake_checker("preflight", FAKE_EXIT2_SCRIPT)
        result = c.run_checker("preflight", case_dir, root)
        self.assertNotIn("bad input", result["detail"])

    def test_unexpected_exit_code_is_execution_failure(self):
        case_dir = self.mkcase()
        root = self.make_fake_checker("preflight", FAKE_BADEXIT_SCRIPT)
        result = c.run_checker("preflight", case_dir, root)
        self.assertEqual(result["state"], "execution_failure")
        self.assertEqual(result["returncode"], 17)

    def test_non_json_stdout_is_execution_failure(self):
        case_dir = self.mkcase()
        root = self.make_fake_checker("preflight", FAKE_NONJSON_SCRIPT)
        result = c.run_checker("preflight", case_dir, root)
        self.assertEqual(result["state"], "execution_failure")
        self.assertIn("non-JSON", result["detail"])

    def test_non_object_json_is_execution_failure(self):
        case_dir = self.mkcase()
        root = self.make_fake_checker("preflight", FAKE_NONOBJECT_JSON_SCRIPT)
        result = c.run_checker("preflight", case_dir, root)
        self.assertEqual(result["state"], "execution_failure")

    def test_timeout_is_execution_failure_not_a_hang(self):
        case_dir = self.mkcase()
        root = self.make_fake_checker("preflight", FAKE_SLOW_SCRIPT)
        result = c.run_checker("preflight", case_dir, root, timeout=0.5)
        self.assertEqual(result["state"], "execution_failure")
        self.assertIn("timed out", result["detail"])

    def test_timeout_detail_mentions_seconds_value(self):
        case_dir = self.mkcase()
        root = self.make_fake_checker("preflight", FAKE_SLOW_SCRIPT)
        result = c.run_checker("preflight", case_dir, root, timeout=0.25)
        self.assertIn("0.25", result["detail"])

    def test_subprocess_invoked_with_relative_case_files(self):
        case_dir = self.mkcase()
        write_json(os.path.join(case_dir, "tasks.json"), [])
        write_json(os.path.join(case_dir, "evidence.json"), [])
        root = self.make_fake_checker("preflight", FAKE_ECHO_ARGS_SCRIPT)
        result = c.run_checker("preflight", case_dir, root)
        self.assertEqual(result["state"], "ok")
        for token in result["report"]["argv"]:
            self.assertFalse(os.path.isabs(token))

    def test_result_dict_has_expected_keys(self):
        case_dir = self.mkcase()
        root = self.make_fake_checker("preflight", FAKE_OK_SCRIPT)
        result = c.run_checker("preflight", case_dir, root)
        self.assertEqual(
            set(result), {"id", "state", "returncode", "report", "detail"}
        )

    def test_relative_checkers_root_still_works(self):
        # Regression test for a real bug: run_checker() launches the
        # checker subprocess with cwd set to the CASE directory (so any
        # relative paths the checker itself prints stay relative). If
        # the checker's own script path was left relative (as it would
        # be for a relative --checkers-root), Python resolves that
        # relative script path against the subprocess's cwd -- the case
        # directory, not the caller's original working directory -- and
        # fails to find the script at all, which looks indistinguishable
        # from the checker rejecting its input (also exit 2) unless you
        # inspect stderr. discover_checker_path() must always return an
        # absolute path so this can never happen.
        case_dir = self.mkcase()
        root = self.make_fake_checker("preflight", FAKE_OK_SCRIPT)
        rel_root = os.path.relpath(root, case_dir)
        old_cwd = os.getcwd()
        os.chdir(case_dir)
        try:
            resolved = c.discover_checker_path("preflight", rel_root)
            self.assertTrue(os.path.isabs(resolved))
            result = c.run_checker("preflight", case_dir, rel_root)
        finally:
            os.chdir(old_cwd)
        self.assertEqual(result["state"], "ok")

    def test_discover_checker_path_absolute_even_for_relative_root(self):
        root = self.make_fake_checker("preflight", FAKE_OK_SCRIPT)
        rel_root = os.path.relpath(root, os.getcwd())
        resolved = c.discover_checker_path("preflight", rel_root)
        self.assertTrue(os.path.isabs(resolved))


# ==========================================================================
# make_issue / issue_sort_key
# ==========================================================================

class MakeIssueTests(unittest.TestCase):
    def test_checkers_sorted(self):
        issue = c.make_issue("X", ("b", "a"))
        self.assertEqual(issue["checkers"], ["a", "b"])

    def test_defaults_are_empty_not_none(self):
        issue = c.make_issue("X", ("a",))
        self.assertEqual(issue["claims"], {})
        self.assertEqual(issue["subject"], {})

    def test_message_stored_verbatim(self):
        issue = c.make_issue("X", ("a",), message="hello")
        self.assertEqual(issue["message"], "hello")

    def test_single_checker_tuple(self):
        issue = c.make_issue("CHECKER_UNAVAILABLE", ("preflight",))
        self.assertEqual(issue["checkers"], ["preflight"])


class IssueSortKeyTests(unittest.TestCase):
    def test_sorts_by_code_first(self):
        i1 = c.make_issue("B_CODE", ("x",))
        i2 = c.make_issue("A_CODE", ("x",))
        ordered = sorted([i1, i2], key=c.issue_sort_key)
        self.assertEqual([i["code"] for i in ordered], ["A_CODE", "B_CODE"])

    def test_sorts_by_checkers_when_code_equal(self):
        i1 = c.make_issue("X", ("z",))
        i2 = c.make_issue("X", ("a",))
        ordered = sorted([i1, i2], key=c.issue_sort_key)
        self.assertEqual(ordered[0]["checkers"], ["a"])

    def test_sorts_by_subject_when_code_and_checkers_equal(self):
        i1 = c.make_issue("X", ("a",), subject={"task_id": "T2"})
        i2 = c.make_issue("X", ("a",), subject={"task_id": "T1"})
        ordered = sorted([i1, i2], key=c.issue_sort_key)
        self.assertEqual(ordered[0]["subject"]["task_id"], "T1")

    def test_stable_and_repeatable(self):
        issues = [
            c.make_issue("X", ("a",), subject={"task_id": "T3"}),
            c.make_issue("X", ("a",), subject={"task_id": "T1"}),
            c.make_issue("Y", ("a",), subject={"task_id": "T2"}),
        ]
        first = sorted(issues, key=c.issue_sort_key)
        second = sorted(list(reversed(issues)), key=c.issue_sort_key)
        self.assertEqual(first, second)

    def test_key_is_a_plain_tuple_of_hashables(self):
        issue = c.make_issue("X", ("a", "b"), subject={"k": 1}, claims={"a": "v"})
        key = c.issue_sort_key(issue)
        self.assertIsInstance(key, tuple)
        # Must not raise -- every element must be orderable/hashable.
        hash(key)


# ==========================================================================
# Comparators (fed hand-built normalized report dicts -- no subprocess)
# ==========================================================================

class CompareLinkageTests(unittest.TestCase):
    def test_no_issues_when_both_link(self):
        reports = {
            "preflight": {"issues": []},
            "link-integrity": {"violations": []},
        }
        self.assertEqual(c.compare_linkage(reports), [])

    def test_contradiction_when_preflight_linked_but_link_integrity_orphaned(self):
        reports = {
            "preflight": {"issues": []},
            "link-integrity": {"violations": [
                {"code": "UNKNOWN_TASK_REFERENCE", "task_id": "T1", "submission_id": "S1"},
            ]},
        }
        issues = c.compare_linkage(reports)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["code"], c.LINKAGE_CONTRADICTION)
        self.assertEqual(issues[0]["subject"]["submission_id"], "S1")

    def test_contradiction_when_link_integrity_linked_but_preflight_orphaned(self):
        reports = {
            "preflight": {"issues": [
                {"code": "ORPHAN_EVIDENCE", "task_id": "T1", "submission_id": "S1"},
            ]},
            "link-integrity": {"violations": []},
        }
        issues = c.compare_linkage(reports)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["claims"]["preflight"], "orphaned")
        self.assertEqual(issues[0]["claims"]["link-integrity"], "linked")

    def test_no_contradiction_when_both_orphaned(self):
        reports = {
            "preflight": {"issues": [
                {"code": "ORPHAN_EVIDENCE", "task_id": "T1", "submission_id": "S1"},
            ]},
            "link-integrity": {"violations": [
                {"code": "UNKNOWN_TASK_REFERENCE", "task_id": "T1", "submission_id": "S1"},
            ]},
        }
        self.assertEqual(c.compare_linkage(reports), [])

    def test_evidence_type_mismatch_is_scope_divergence_not_contradiction(self):
        reports = {
            "preflight": {"issues": [
                {"code": "EVIDENCE_TYPE_MISMATCH", "task_id": "T1", "submission_id": "S1"},
            ]},
            "link-integrity": {"violations": []},
        }
        issues = c.compare_linkage(reports)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["code"], c.SCOPE_DIVERGENCE)

    def test_scope_divergence_never_coded_as_contradiction(self):
        reports = {
            "preflight": {"issues": [
                {"code": "EVIDENCE_TYPE_MISMATCH", "task_id": "T1", "submission_id": "S1"},
            ]},
            "link-integrity": {"violations": []},
        }
        issues = c.compare_linkage(reports)
        self.assertNotIn(issues[0]["code"], c.CONTRADICTION_CODES)

    def test_multiple_submissions_sorted_by_submission_id(self):
        reports = {
            "preflight": {"issues": []},
            "link-integrity": {"violations": [
                {"code": "UNKNOWN_TASK_REFERENCE", "task_id": "T2", "submission_id": "S2"},
                {"code": "UNKNOWN_TASK_REFERENCE", "task_id": "T1", "submission_id": "S1"},
            ]},
        }
        issues = c.compare_linkage(reports)
        self.assertEqual(
            [i["subject"]["submission_id"] for i in issues], ["S1", "S2"]
        )

    def test_unrelated_preflight_issue_codes_ignored(self):
        reports = {
            "preflight": {"issues": [
                {"code": "DUPLICATE_SUBMISSION_ID", "submission_id": "S9"},
                {"code": "UNSUBMITTABLE_STATUS", "task_id": "T9"},
            ]},
            "link-integrity": {"violations": []},
        }
        self.assertEqual(c.compare_linkage(reports), [])


class CompareAmountTests(unittest.TestCase):
    def test_no_issues_when_neither_flags(self):
        reports = {"reward-reconciler": {"findings": []}, "reward-anomaly": {"findings": []}}
        self.assertEqual(c.compare_amount(reports), [])

    def test_contradiction_on_differing_amounts(self):
        reports = {
            "reward-reconciler": {"findings": [
                {"task_id": "T1", "issue": "AMOUNT_MISMATCH", "payout_amount": "12.000000"},
            ]},
            "reward-anomaly": {"findings": [
                {"task_id": "T1", "code": "AMOUNT_ABOVE_PRICE", "amount": "15.00"},
            ]},
        }
        issues = c.compare_amount(reports)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["code"], c.AMOUNT_CONTRADICTION)

    def test_no_contradiction_when_amounts_equal_different_formatting(self):
        reports = {
            "reward-reconciler": {"findings": [
                {"task_id": "T1", "issue": "AMOUNT_MISMATCH", "payout_amount": "12.000000"},
            ]},
            "reward-anomaly": {"findings": [
                {"task_id": "T1", "code": "AMOUNT_BELOW_PRICE", "amount": "12"},
            ]},
        }
        self.assertEqual(c.compare_amount(reports), [])

    def test_below_price_code_also_considered(self):
        reports = {
            "reward-reconciler": {"findings": [
                {"task_id": "T1", "issue": "AMOUNT_MISMATCH", "payout_amount": "5.00"},
            ]},
            "reward-anomaly": {"findings": [
                {"task_id": "T1", "code": "AMOUNT_BELOW_PRICE", "amount": "3.00"},
            ]},
        }
        issues = c.compare_amount(reports)
        self.assertEqual(len(issues), 1)

    def test_no_comparison_when_only_one_side_has_finding(self):
        reports = {
            "reward-reconciler": {"findings": [
                {"task_id": "T1", "issue": "AMOUNT_MISMATCH", "payout_amount": "5.00"},
            ]},
            "reward-anomaly": {"findings": []},
        }
        self.assertEqual(c.compare_amount(reports), [])

    def test_unrelated_task_ids_not_compared(self):
        reports = {
            "reward-reconciler": {"findings": [
                {"task_id": "T1", "issue": "AMOUNT_MISMATCH", "payout_amount": "5.00"},
            ]},
            "reward-anomaly": {"findings": [
                {"task_id": "T2", "code": "AMOUNT_ABOVE_PRICE", "amount": "9.00"},
            ]},
        }
        self.assertEqual(c.compare_amount(reports), [])

    def test_wallet_mismatch_is_scope_divergence(self):
        reports = {
            "reward-reconciler": {"findings": [
                {"task_id": "T1", "issue": "WALLET_MISMATCH"},
            ]},
            "reward-anomaly": {"findings": []},
        }
        issues = c.compare_amount(reports)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["code"], c.SCOPE_DIVERGENCE)

    def test_missing_payout_and_duplicate_payout_ignored(self):
        reports = {
            "reward-reconciler": {"findings": [
                {"task_id": "T1", "issue": "MISSING_PAYOUT", "payout_amount": None},
                {"task_id": "T2", "issue": "DUPLICATE_PAYOUT", "payout_amount": "9.00"},
            ]},
            "reward-anomaly": {"findings": []},
        }
        self.assertEqual(c.compare_amount(reports), [])

    def test_amounts_differ_helper_handles_none(self):
        self.assertFalse(c._amounts_differ(None, None))
        self.assertTrue(c._amounts_differ(None, "1"))
        self.assertTrue(c._amounts_differ("1", None))

    def test_amounts_differ_helper_handles_non_numeric_fallback(self):
        self.assertFalse(c._amounts_differ("abc", "abc"))
        self.assertTrue(c._amounts_differ("abc", "def"))


class CompareTimestampTests(unittest.TestCase):
    def test_no_issue_when_no_impossible_timestamps(self):
        reports = {
            "link-integrity": {"violations": []},
            "lifecycle-linter": {"findings": []},
        }
        self.assertEqual(c.compare_timestamp(reports), [])

    def test_contradiction_when_lifecycle_linter_silent(self):
        reports = {
            "link-integrity": {"violations": [
                {"code": "IMPOSSIBLE_TIMESTAMP", "source": "lifecycle", "task_id": "T1",
                 "value": "bad", "reason": "nope"},
            ]},
            "lifecycle-linter": {"findings": []},
        }
        issues = c.compare_timestamp(reports)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["code"], c.TIMESTAMP_CONTRADICTION)

    def test_no_contradiction_when_lifecycle_linter_also_has_a_finding(self):
        reports = {
            "link-integrity": {"violations": [
                {"code": "IMPOSSIBLE_TIMESTAMP", "source": "lifecycle", "task_id": "T1",
                 "value": "bad", "reason": "nope"},
            ]},
            "lifecycle-linter": {"findings": [
                {"task_id": "T1", "code": "UNKNOWN_STATE", "line": 1, "detail": "..."},
            ]},
        }
        self.assertEqual(c.compare_timestamp(reports), [])

    def test_evidence_source_impossible_timestamp_ignored(self):
        reports = {
            "link-integrity": {"violations": [
                {"code": "IMPOSSIBLE_TIMESTAMP", "source": "evidence", "task_id": "T1",
                 "value": "bad", "reason": "nope"},
            ]},
            "lifecycle-linter": {"findings": []},
        }
        self.assertEqual(c.compare_timestamp(reports), [])

    def test_other_violation_codes_ignored(self):
        reports = {
            "link-integrity": {"violations": [
                {"code": "UNKNOWN_TASK_REFERENCE", "task_id": "T1"},
            ]},
            "lifecycle-linter": {"findings": []},
        }
        self.assertEqual(c.compare_timestamp(reports), [])

    def test_multiple_tasks_each_evaluated_independently(self):
        reports = {
            "link-integrity": {"violations": [
                {"code": "IMPOSSIBLE_TIMESTAMP", "source": "lifecycle", "task_id": "T1",
                 "value": "bad1", "reason": "r1"},
                {"code": "IMPOSSIBLE_TIMESTAMP", "source": "lifecycle", "task_id": "T2",
                 "value": "bad2", "reason": "r2"},
            ]},
            "lifecycle-linter": {"findings": [
                {"task_id": "T2", "code": "UNKNOWN_STATE", "line": 1, "detail": "..."},
            ]},
        }
        issues = c.compare_timestamp(reports)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["subject"]["task_id"], "T1")


class CompareLifecycleScopeTests(unittest.TestCase):
    def test_no_issue_when_no_duplicate_state(self):
        reports = {"lifecycle-linter": {"findings": []}, "event-linter": {"tasks": []}}
        self.assertEqual(c.compare_lifecycle_scope(reports), [])

    def test_scope_divergence_when_event_linter_silent(self):
        reports = {
            "lifecycle-linter": {"findings": [
                {"task_id": "T1", "code": "DUPLICATE_STATE", "line": 3, "detail": "..."},
            ]},
            "event-linter": {"tasks": [
                {"task_id": "T1", "violations": []},
            ]},
        }
        issues = c.compare_lifecycle_scope(reports)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["code"], c.SCOPE_DIVERGENCE)

    def test_no_divergence_when_event_linter_also_flags_duplicate_event(self):
        reports = {
            "lifecycle-linter": {"findings": [
                {"task_id": "T1", "code": "DUPLICATE_STATE", "line": 3, "detail": "..."},
            ]},
            "event-linter": {"tasks": [
                {"task_id": "T1", "violations": [
                    {"index": 2, "task_id": "T1", "violation": "DUPLICATE_EVENT"},
                ]},
            ]},
        }
        self.assertEqual(c.compare_lifecycle_scope(reports), [])

    def test_never_emits_a_contradiction_code(self):
        reports = {
            "lifecycle-linter": {"findings": [
                {"task_id": "T1", "code": "DUPLICATE_STATE", "line": 3, "detail": "..."},
            ]},
            "event-linter": {"tasks": [{"task_id": "T1", "violations": []}]},
        }
        issues = c.compare_lifecycle_scope(reports)
        for issue in issues:
            self.assertNotIn(issue["code"], c.CONTRADICTION_CODES)

    def test_other_lifecycle_linter_codes_ignored(self):
        reports = {
            "lifecycle-linter": {"findings": [
                {"task_id": "T1", "code": "POST_TERMINAL_EVENT", "line": 3, "detail": "..."},
            ]},
            "event-linter": {"tasks": [{"task_id": "T1", "violations": []}]},
        }
        self.assertEqual(c.compare_lifecycle_scope(reports), [])

    def test_task_absent_from_event_linter_report_still_flagged(self):
        reports = {
            "lifecycle-linter": {"findings": [
                {"task_id": "T9", "code": "DUPLICATE_STATE", "line": 1, "detail": "..."},
            ]},
            "event-linter": {"tasks": []},
        }
        issues = c.compare_lifecycle_scope(reports)
        self.assertEqual(len(issues), 1)


class CompareValidityTests(unittest.TestCase):
    def test_no_issue_when_both_clean(self):
        reports = {
            "queue-auditor": {"findings": []},
            "staleness-monitor": {"findings": {"critical": [], "warning": [], "info": []}},
        }
        self.assertEqual(c.compare_validity(reports), [])

    def test_contradiction_on_null_deadline(self):
        reports = {
            "queue-auditor": {"findings": [
                {"code": "MALFORMED_RECORD", "task_id": "T1",
                 "detail": "missing required field 'deadline'"},
            ]},
            "staleness-monitor": {"findings": {"critical": [], "warning": [], "info": []}},
        }
        issues = c.compare_validity(reports)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["code"], c.VALIDITY_CONTRADICTION)

    def test_no_contradiction_when_staleness_also_flags_malformed_deadline(self):
        reports = {
            "queue-auditor": {"findings": [
                {"code": "MALFORMED_RECORD", "task_id": "T1",
                 "detail": "missing required field 'deadline'"},
            ]},
            "staleness-monitor": {"findings": {
                "critical": [{"task_id": "T1", "code": "MALFORMED_DEADLINE"}],
                "warning": [], "info": [],
            }},
        }
        self.assertEqual(c.compare_validity(reports), [])

    def test_malformed_record_about_other_field_ignored(self):
        reports = {
            "queue-auditor": {"findings": [
                {"code": "MALFORMED_RECORD", "task_id": "T1",
                 "detail": "missing required field 'title'"},
            ]},
            "staleness-monitor": {"findings": {"critical": [], "warning": [], "info": []}},
        }
        self.assertEqual(c.compare_validity(reports), [])

    def test_overdue_proposed_is_scope_divergence(self):
        reports = {
            "queue-auditor": {"findings": []},
            "staleness-monitor": {"findings": {
                "critical": [{"task_id": "T1", "code": "OVERDUE_PROPOSED"}],
                "warning": [], "info": [],
            }},
        }
        issues = c.compare_validity(reports)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["code"], c.SCOPE_DIVERGENCE)

    def test_stale_accepted_and_submitted_both_scope_divergence(self):
        reports = {
            "queue-auditor": {"findings": []},
            "staleness-monitor": {"findings": {
                "critical": [],
                "warning": [{"task_id": "T1", "code": "STALE_ACCEPTED"}],
                "info": [{"task_id": "T2", "code": "STALE_SUBMITTED"}],
            }},
        }
        issues = c.compare_validity(reports)
        self.assertEqual(len(issues), 2)
        for issue in issues:
            self.assertEqual(issue["code"], c.SCOPE_DIVERGENCE)

    def test_findings_grouped_across_all_three_buckets(self):
        reports = {
            "queue-auditor": {"findings": [
                {"code": "MALFORMED_RECORD", "task_id": "T1",
                 "detail": "missing required field 'deadline'"},
            ]},
            "staleness-monitor": {"findings": {
                "critical": [{"task_id": "T2", "code": "MALFORMED_DEADLINE"}],
                "warning": [],
                "info": [{"task_id": "T1", "code": "MALFORMED_DEADLINE"}],
            }},
        }
        self.assertEqual(c.compare_validity(reports), [])


class CompareIdentityTests(unittest.TestCase):
    def test_no_issue_when_no_flagged_pairs(self):
        reports = {"evidence-scorer": {"records": []}, "dup-detector": {"flagged_pairs": []}}
        self.assertEqual(c.compare_identity(reports), [])

    def test_contradiction_when_both_fully_original(self):
        reports = {
            "evidence-scorer": {"records": [
                {"submission_id": "A", "components": {"originality": 1.0}},
                {"submission_id": "B", "components": {"originality": 1.0}},
            ]},
            "dup-detector": {"flagged_pairs": [
                {"submission_id_a": "A", "submission_id_b": "B", "score": 0.75},
            ]},
        }
        issues = c.compare_identity(reports)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["code"], c.IDENTITY_CONTRADICTION)

    def test_no_contradiction_when_originality_below_one(self):
        reports = {
            "evidence-scorer": {"records": [
                {"submission_id": "A", "components": {"originality": 0.0}},
                {"submission_id": "B", "components": {"originality": 0.0}},
            ]},
            "dup-detector": {"flagged_pairs": [
                {"submission_id_a": "A", "submission_id_b": "B", "score": 1.0},
            ]},
        }
        self.assertEqual(c.compare_identity(reports), [])

    def test_no_contradiction_when_only_one_side_fully_original(self):
        reports = {
            "evidence-scorer": {"records": [
                {"submission_id": "A", "components": {"originality": 1.0}},
                {"submission_id": "B", "components": {"originality": 0.4}},
            ]},
            "dup-detector": {"flagged_pairs": [
                {"submission_id_a": "A", "submission_id_b": "B", "score": 0.75},
            ]},
        }
        self.assertEqual(c.compare_identity(reports), [])

    def test_missing_submission_id_in_scorer_records_handled_gracefully(self):
        reports = {
            "evidence-scorer": {"records": [
                {"submission_id": "A", "components": {"originality": 1.0}},
            ]},
            "dup-detector": {"flagged_pairs": [
                {"submission_id_a": "A", "submission_id_b": "B", "score": 0.75},
            ]},
        }
        self.assertEqual(c.compare_identity(reports), [])

    def test_subject_contains_both_submission_ids(self):
        reports = {
            "evidence-scorer": {"records": [
                {"submission_id": "A", "components": {"originality": 1.0}},
                {"submission_id": "B", "components": {"originality": 1.0}},
            ]},
            "dup-detector": {"flagged_pairs": [
                {"submission_id_a": "A", "submission_id_b": "B", "score": 0.9},
            ]},
        }
        issues = c.compare_identity(reports)
        self.assertEqual(issues[0]["subject"], {"submission_id_a": "A", "submission_id_b": "B"})


class SafeGetTests(unittest.TestCase):
    def test_returns_value_at_path(self):
        self.assertEqual(c._safe_get({"a": {"b": 1}}, "a", "b"), 1)

    def test_returns_none_for_missing_key(self):
        self.assertIsNone(c._safe_get({"a": {}}, "a", "b"))

    def test_returns_none_for_missing_intermediate(self):
        self.assertIsNone(c._safe_get({}, "a", "b"))

    def test_handles_list_index(self):
        self.assertEqual(c._safe_get({"a": [10, 20]}, "a", 1), 20)

    def test_handles_out_of_range_index(self):
        self.assertIsNone(c._safe_get({"a": [10]}, "a", 5))

    def test_handles_non_container_leaf(self):
        self.assertIsNone(c._safe_get({"a": 5}, "a", "b"))


# ==========================================================================
# discover_cases
# ==========================================================================

class DiscoverCasesTests(TempDirMixin, unittest.TestCase):
    def test_nonexistent_root_raises(self):
        with self.assertRaises(c.InputError):
            c.discover_cases(os.path.join(self._tmp, "nope"))

    def test_file_instead_of_directory_raises(self):
        path = os.path.join(self._tmp, "f.txt")
        write_text(path, "x")
        with self.assertRaises(c.InputError):
            c.discover_cases(path)

    def test_leaf_case_with_direct_files(self):
        root = self.mkcase("leaf")
        write_json(os.path.join(root, "tasks.json"), [])
        write_json(os.path.join(root, "evidence.json"), [])
        cases = c.discover_cases(root)
        self.assertEqual(len(cases), 1)
        self.assertEqual(cases[0][0], "leaf")
        self.assertEqual(cases[0][1], root)

    def test_container_with_subdirectories(self):
        root = self.mkroot()
        os.makedirs(os.path.join(root, "case_b"))
        os.makedirs(os.path.join(root, "case_a"))
        cases = c.discover_cases(root)
        self.assertEqual([cid for cid, _ in cases], ["case_a", "case_b"])

    def test_container_sorted_deterministically(self):
        root = self.mkroot()
        for name in ["zeta", "alpha", "mid"]:
            os.makedirs(os.path.join(root, name))
        cases = c.discover_cases(root)
        self.assertEqual([cid for cid, _ in cases], ["alpha", "mid", "zeta"])

    def test_completely_empty_root_yields_no_cases(self):
        root = self.mkroot()
        self.assertEqual(c.discover_cases(root), [])

    def test_subdirectory_with_no_recognized_files_is_still_a_case(self):
        root = self.mkroot()
        os.makedirs(os.path.join(root, "empty_one"))
        cases = c.discover_cases(root)
        self.assertEqual([cid for cid, _ in cases], ["empty_one"])

    def test_files_at_container_level_that_are_not_known_inputs_are_ignored(self):
        root = self.mkroot()
        write_text(os.path.join(root, "README.md"), "hi")
        os.makedirs(os.path.join(root, "case_x"))
        cases = c.discover_cases(root)
        self.assertEqual([cid for cid, _ in cases], ["case_x"])

    def test_trailing_slash_does_not_change_case_id(self):
        root = self.mkcase("leaf2")
        write_json(os.path.join(root, "submissions.json"), [])
        cases = c.discover_cases(root + os.sep)
        self.assertEqual(cases[0][0], "leaf2")


# ==========================================================================
# process_case (using tiny fake checkers, no real subprocess dependency)
# ==========================================================================

class ProcessCaseTests(FakeCheckerMixin, unittest.TestCase):
    def test_no_applicable_checkers_yields_empty_case(self):
        case_dir = self.mkcase()
        result = c.process_case("mycase", case_dir, os.path.join(self._tmp, "no_root"), 5)
        self.assertEqual(result["case_id"], "mycase")
        self.assertEqual(result["checkers_applicable"], [])
        self.assertEqual(result["issue_count"], 0)

    def test_checker_unavailable_recorded_when_applicable_but_missing_script(self):
        case_dir = self.mkcase()
        write_json(os.path.join(case_dir, "tasks.json"), [])
        write_json(os.path.join(case_dir, "evidence.json"), [])
        result = c.process_case("c1", case_dir, os.path.join(self._tmp, "missing_root"), 5)
        self.assertIn("preflight", result["checkers_applicable"])
        codes = [i["code"] for i in result["issues"]]
        self.assertIn(c.CHECKER_UNAVAILABLE, codes)

    def test_not_applicable_checkers_listed_separately(self):
        case_dir = self.mkcase()
        write_json(os.path.join(case_dir, "tasks.json"), [])
        write_json(os.path.join(case_dir, "evidence.json"), [])
        result = c.process_case("c1", case_dir, os.path.join(self._tmp, "missing_root"), 5)
        self.assertIn("dup-detector", result["checkers_not_applicable"])

    def test_issues_are_sorted(self):
        case_dir = self.mkcase()
        for name in c.ADAPTERS["dup-detector"]["required_inputs"]:
            write_json(os.path.join(case_dir, name), [])
        # dup-detector applicable but unavailable; evidence-scorer applicable but unavailable too
        result = c.process_case("c1", case_dir, os.path.join(self._tmp, "missing_root"), 5)
        keys = [c.issue_sort_key(i) for i in result["issues"]]
        self.assertEqual(keys, sorted(keys))

    def test_execution_failure_from_fake_slow_checker(self):
        case_dir = self.mkcase()
        write_json(os.path.join(case_dir, "tasks.json"), [])
        write_json(os.path.join(case_dir, "evidence.json"), [])
        root = self.make_fake_checker("preflight", FAKE_SLOW_SCRIPT)
        # give link-integrity's dependency too so only preflight matters here
        result = c.process_case("c1", case_dir, root, 0.3)
        codes = [i["code"] for i in result["issues"]]
        self.assertIn(c.EXECUTION_FAILURE, codes)

    def test_counts_match_issue_list(self):
        case_dir = self.mkcase()
        write_json(os.path.join(case_dir, "tasks.json"), [])
        write_json(os.path.join(case_dir, "evidence.json"), [])
        result = c.process_case("c1", case_dir, os.path.join(self._tmp, "missing_root"), 5)
        total = (result["contradiction_count"] + result["execution_issue_count"]
                 + result["scope_divergence_count"])
        self.assertEqual(total, result["issue_count"])
        self.assertEqual(result["issue_count"], len(result["issues"]))


# ==========================================================================
# build_report
# ==========================================================================

class BuildReportTests(unittest.TestCase):
    def test_empty_case_list_is_agree(self):
        report = c.build_report([])
        self.assertEqual(report["status"], "agree")
        self.assertEqual(report["summary"]["case_count"], 0)

    def test_agree_when_no_issues_anywhere(self):
        cases = [{
            "case_id": "a", "checkers_applicable": [], "checkers_not_applicable": [],
            "contradiction_count": 0, "execution_issue_count": 0, "issue_count": 0,
            "issues": [], "scope_divergence_count": 0,
        }]
        self.assertEqual(c.build_report(cases)["status"], "agree")

    def test_scope_divergence_alone_is_still_agree(self):
        issue = c.make_issue(c.SCOPE_DIVERGENCE, ("a", "b"))
        cases = [{
            "case_id": "a", "checkers_applicable": [], "checkers_not_applicable": [],
            "contradiction_count": 0, "execution_issue_count": 0, "issue_count": 1,
            "issues": [issue], "scope_divergence_count": 1,
        }]
        self.assertEqual(c.build_report(cases)["status"], "agree")

    def test_contradiction_gives_contradictions_found(self):
        issue = c.make_issue(c.AMOUNT_CONTRADICTION, ("a", "b"))
        cases = [{
            "case_id": "a", "checkers_applicable": [], "checkers_not_applicable": [],
            "contradiction_count": 1, "execution_issue_count": 0, "issue_count": 1,
            "issues": [issue], "scope_divergence_count": 0,
        }]
        self.assertEqual(c.build_report(cases)["status"], "contradictions_found")

    def test_execution_error_takes_priority_over_contradiction(self):
        contradiction = c.make_issue(c.AMOUNT_CONTRADICTION, ("a", "b"))
        execution = c.make_issue(c.EXECUTION_FAILURE, ("a",))
        cases = [{
            "case_id": "a", "checkers_applicable": [], "checkers_not_applicable": [],
            "contradiction_count": 1, "execution_issue_count": 1, "issue_count": 2,
            "issues": [contradiction, execution], "scope_divergence_count": 0,
        }]
        self.assertEqual(c.build_report(cases)["status"], "execution_error")

    def test_code_counts_present_for_every_known_code_even_when_zero(self):
        report = c.build_report([])
        self.assertEqual(set(report["code_counts"]), set(c.ALL_CODES))
        self.assertTrue(all(v == 0 for v in report["code_counts"].values()))

    def test_cases_sorted_by_case_id_in_output(self):
        cases = [
            {"case_id": "z", "checkers_applicable": [], "checkers_not_applicable": [],
             "contradiction_count": 0, "execution_issue_count": 0, "issue_count": 0,
             "issues": [], "scope_divergence_count": 0},
            {"case_id": "a", "checkers_applicable": [], "checkers_not_applicable": [],
             "contradiction_count": 0, "execution_issue_count": 0, "issue_count": 0,
             "issues": [], "scope_divergence_count": 0},
        ]
        report = c.build_report(cases)
        self.assertEqual([cs["case_id"] for cs in report["cases"]], ["a", "z"])

    def test_tool_version_and_report_version_present(self):
        report = c.build_report([])
        self.assertEqual(report["tool_version"], c.TOOL_VERSION)
        self.assertEqual(report["report_version"], c.REPORT_VERSION)

    def test_status_to_exit_code_mapping_covers_all_statuses(self):
        for status in ("agree", "contradictions_found", "execution_error"):
            self.assertIn(status, c.STATUS_TO_EXIT_CODE)


# ==========================================================================
# CLI argument parsing
# ==========================================================================

class ArgParsingTests(unittest.TestCase):
    def test_missing_positional_arg_exits_2(self):
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as cm:
                c.build_arg_parser().parse_args([])
        self.assertEqual(cm.exception.code, 2)

    def test_unknown_flag_exits_2(self):
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as cm:
                c.build_arg_parser().parse_args(["case_root", "--bogus"])
        self.assertEqual(cm.exception.code, 2)

    def test_output_flag_short_form(self):
        args = c.build_arg_parser().parse_args(["root", "-o", "out.json"])
        self.assertEqual(args.output, "out.json")

    def test_output_flag_long_form(self):
        args = c.build_arg_parser().parse_args(["root", "--output", "out.json"])
        self.assertEqual(args.output, "out.json")

    def test_checkers_root_flag(self):
        args = c.build_arg_parser().parse_args(["root", "--checkers-root", "/somewhere"])
        self.assertEqual(args.checkers_root, "/somewhere")

    def test_checkers_root_defaults_to_none(self):
        args = c.build_arg_parser().parse_args(["root"])
        self.assertIsNone(args.checkers_root)

    def test_timeout_flag_parses_float(self):
        args = c.build_arg_parser().parse_args(["root", "--timeout", "3.5"])
        self.assertEqual(args.timeout, 3.5)

    def test_timeout_defaults(self):
        args = c.build_arg_parser().parse_args(["root"])
        self.assertEqual(args.timeout, c.DEFAULT_TIMEOUT)


class MainTimeoutValidationTests(TempDirMixin, unittest.TestCase):
    def test_zero_timeout_rejected(self):
        root = self.mkcase("leaf")
        write_json(os.path.join(root, "submissions.json"), [])
        code, out, err = run_cli([root, "--timeout", "0"])
        self.assertEqual(code, 2)
        self.assertIn("timeout", err)

    def test_negative_timeout_rejected(self):
        root = self.mkcase("leaf")
        code, out, err = run_cli([root, "--timeout", "-1"])
        self.assertEqual(code, 2)


# ==========================================================================
# Full CLI integration against the REAL bundled checkers
# ==========================================================================

CHECKERS_ROOT = os.path.join(HERE, "checkers")


class RealCheckerIntegrationTests(unittest.TestCase):
    """Runs the real, vendored checker scripts end to end for each of the
    six shipped case pairs, without relying on the repo's own
    cases_agree/cases_conflict fixtures (built fresh in a temp dir so
    this test class is self-contained)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="contradict_integ_")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def _case(self, name, files):
        path = os.path.join(self.tmp, name)
        os.makedirs(path, exist_ok=True)
        for fname, content in files.items():
            full = os.path.join(path, fname)
            if fname.endswith(".jsonl"):
                write_text(full, content)
            else:
                write_json(full, content)
        return path

    def test_linkage_agree_end_to_end(self):
        path = self._case("linkage_ok", {
            "tasks.json": [{"task_id": "T1", "title": "t", "status": "in_review",
                             "required_evidence": ["url"]}],
            "lifecycle.json": [{"task_id": "T1", "state": "proposed", "at": "2026-01-01T00:00:00Z"}],
            "evidence.json": [{"submission_id": "S1", "task_id": "T1", "evidence_type": "url",
                                "value": "x", "submitted_at": "2026-01-02T00:00:00Z"}],
        })
        code, out, err = run_cli([path, "--checkers-root", CHECKERS_ROOT])
        self.assertEqual(code, 0)
        report = json.loads(out)
        self.assertEqual(report["status"], "agree")

    def test_linkage_conflict_end_to_end(self):
        path = self._case("linkage_bad", {
            "tasks.json": [{"task_id": "T1", "title": "t", "status": "in_review",
                             "required_evidence": ["url"]}],
            "lifecycle.json": [],
            "evidence.json": [{"submission_id": "S1", "task_id": "T1", "evidence_type": "url",
                                "value": "x", "submitted_at": "2026-01-02T00:00:00Z"}],
        })
        code, out, err = run_cli([path, "--checkers-root", CHECKERS_ROOT])
        self.assertEqual(code, 1)
        report = json.loads(out)
        self.assertEqual(report["code_counts"]["LINKAGE_CONTRADICTION"], 1)

    def test_amount_agree_end_to_end(self):
        path = self._case("amount_ok", {
            "expected_rewards.json": [{"task_id": "T1", "wallet": "rWALLET", "amount": "5.000000"}],
            "recorded_payouts.json": [{"task_id": "T1", "wallet": "rWALLET", "amount": "5.000000"}],
            "reward_tasks.json": [{"task_id": "T1", "status": "rewarded", "price": "5.00"}],
            "reward_payouts.json": [{"payout_id": "P1", "task_id": "T1", "amount": "5.00",
                                      "at": "2026-01-01T00:00:00Z"}],
        })
        code, out, err = run_cli([path, "--checkers-root", CHECKERS_ROOT])
        self.assertEqual(code, 0)

    def test_amount_conflict_end_to_end(self):
        path = self._case("amount_bad", {
            "expected_rewards.json": [{"task_id": "T1", "wallet": "rWALLET", "amount": "5.000000"}],
            "recorded_payouts.json": [{"task_id": "T1", "wallet": "rWALLET", "amount": "8.000000"}],
            "reward_tasks.json": [{"task_id": "T1", "status": "rewarded", "price": "5.00"}],
            "reward_payouts.json": [{"payout_id": "P1", "task_id": "T1", "amount": "9.00",
                                      "at": "2026-01-01T00:00:00Z"}],
        })
        code, out, err = run_cli([path, "--checkers-root", CHECKERS_ROOT])
        self.assertEqual(code, 1)
        report = json.loads(out)
        self.assertEqual(report["code_counts"]["AMOUNT_CONTRADICTION"], 1)

    def test_validity_agree_end_to_end(self):
        path = self._case("validity_ok", {
            "queue_tasks.json": {"tasks": [
                {"task_id": "T1", "title": "t", "status": "outstanding", "list": "outstanding",
                 "reward": 5, "created_at": "2026-01-01T00:00:00Z", "deadline": "2026-01-05T00:00:00Z"},
            ]},
            "staleness_tasks.json": [
                {"task_id": "T1", "title": "t", "status": "outstanding",
                 "created_at": "2026-01-01T00:00:00Z", "deadline": "2026-01-05T00:00:00Z"},
            ],
        })
        code, out, err = run_cli([path, "--checkers-root", CHECKERS_ROOT])
        self.assertEqual(code, 0)

    def test_validity_conflict_end_to_end(self):
        path = self._case("validity_bad", {
            "queue_tasks.json": {"tasks": [
                {"task_id": "T1", "title": "t", "status": "proposed", "list": "proposed",
                 "reward": 5, "created_at": "2026-01-01T00:00:00Z", "deadline": None},
            ]},
            "staleness_tasks.json": [
                {"task_id": "T1", "title": "t", "status": "proposed",
                 "created_at": "2026-01-01T00:00:00Z", "deadline": None},
            ],
        })
        code, out, err = run_cli([path, "--checkers-root", CHECKERS_ROOT])
        self.assertEqual(code, 1)
        report = json.loads(out)
        self.assertEqual(report["code_counts"]["VALIDITY_CONTRADICTION"], 1)

    def test_timestamp_conflict_end_to_end(self):
        path = self._case("timestamp_bad", {
            "lifecycle.json": [{"task_id": "T1", "state": "proposed", "at": "2026-13-40T00:00:00Z"}],
            "evidence.json": [{"submission_id": "S1", "task_id": "T1", "evidence_type": "url",
                                "value": "x", "submitted_at": "2026-01-01T00:00:00Z"}],
            "events.jsonl": '{"task_id": "T1", "state": "proposed", "occurred_at": "2026-13-40T00:00:00Z"}\n',
        })
        code, out, err = run_cli([path, "--checkers-root", CHECKERS_ROOT])
        self.assertEqual(code, 1)
        report = json.loads(out)
        self.assertEqual(report["code_counts"]["TIMESTAMP_CONTRADICTION"], 1)

    def test_only_applicable_checkers_are_run(self):
        path = self._case("solo", {
            "submissions.json": [{"submission_id": "S1", "text": "hello world this is fine"}],
        })
        code, out, err = run_cli([path, "--checkers-root", CHECKERS_ROOT])
        self.assertEqual(code, 0)
        report = json.loads(out)
        applicable = report["cases"][0]["checkers_applicable"]
        self.assertEqual(set(applicable), {"dup-detector", "evidence-scorer"})

    def test_multi_case_root_aggregates_across_cases(self):
        root = os.path.join(self.tmp, "multi")
        os.makedirs(root)
        good = os.path.join(root, "good")
        bad = os.path.join(root, "bad")
        os.makedirs(good)
        os.makedirs(bad)
        write_json(os.path.join(good, "expected_rewards.json"),
                   [{"task_id": "T1", "wallet": "rW", "amount": "1.000000"}])
        write_json(os.path.join(good, "recorded_payouts.json"),
                   [{"task_id": "T1", "wallet": "rW", "amount": "1.000000"}])
        write_json(os.path.join(bad, "expected_rewards.json"),
                   [{"task_id": "T2", "wallet": "rW", "amount": "1.000000"}])
        write_json(os.path.join(bad, "recorded_payouts.json"),
                   [{"task_id": "T2", "wallet": "rW", "amount": "9.000000"}])
        write_json(os.path.join(bad, "reward_tasks.json"),
                   [{"task_id": "T2", "status": "rewarded", "price": "1.00"}])
        write_json(os.path.join(bad, "reward_payouts.json"),
                   [{"payout_id": "P2", "task_id": "T2", "amount": "5.00", "at": "2026-01-01T00:00:00Z"}])
        code, out, err = run_cli([root, "--checkers-root", CHECKERS_ROOT])
        report = json.loads(out)
        self.assertEqual(report["summary"]["case_count"], 2)
        self.assertEqual(code, 1)


# ==========================================================================
# Determinism, byte-stability, relocation, and no-path-leak guarantees
# ==========================================================================

class DeterminismTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="contradict_determ_")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def _build_conflict_root(self, base):
        root = os.path.join(base, "cases_conflict")
        case = os.path.join(root, "amount_conflict")
        os.makedirs(case)
        write_json(os.path.join(case, "expected_rewards.json"),
                   [{"task_id": "T1", "wallet": "rW", "amount": "1.000000"}])
        write_json(os.path.join(case, "recorded_payouts.json"),
                   [{"task_id": "T1", "wallet": "rW", "amount": "9.000000"}])
        write_json(os.path.join(case, "reward_tasks.json"),
                   [{"task_id": "T1", "status": "rewarded", "price": "1.00"}])
        write_json(os.path.join(case, "reward_payouts.json"),
                   [{"payout_id": "P1", "task_id": "T1", "amount": "20.00",
                     "at": "2026-01-01T00:00:00Z"}])
        return root

    def test_repeated_runs_byte_identical(self):
        root = self._build_conflict_root(self.tmp)
        out1 = os.path.join(self.tmp, "r1.json")
        out2 = os.path.join(self.tmp, "r2.json")
        code1, _, _ = run_cli([root, "--checkers-root", CHECKERS_ROOT, "-o", out1])
        code2, _, _ = run_cli([root, "--checkers-root", CHECKERS_ROOT, "-o", out2])
        self.assertEqual(code1, 1)
        self.assertEqual(code2, 1)
        with open(out1, "rb") as f1, open(out2, "rb") as f2:
            self.assertEqual(f1.read(), f2.read())

    def test_report_contains_no_absolute_paths(self):
        root = self._build_conflict_root(self.tmp)
        code, out, err = run_cli([root, "--checkers-root", CHECKERS_ROOT])
        self.assertNotIn(self.tmp, out)
        self.assertNotIn("/tmp", out)
        self.assertNotIn("/sessions", out)
        self.assertNotIn("/home", out)

    def test_report_contains_no_backslash_windows_paths(self):
        root = self._build_conflict_root(self.tmp)
        code, out, err = run_cli([root, "--checkers-root", CHECKERS_ROOT])
        self.assertNotIn("C:\\", out)

    def test_relocation_produces_identical_bytes(self):
        root = self._build_conflict_root(self.tmp)
        out1 = os.path.join(self.tmp, "orig.json")
        run_cli([root, "--checkers-root", CHECKERS_ROOT, "-o", out1])

        relocated_base = tempfile.mkdtemp(prefix="contradict_relocated_")
        self.addCleanup(shutil.rmtree, relocated_base, ignore_errors=True)
        deeper = os.path.join(relocated_base, "some", "other", "nesting")
        os.makedirs(deeper)
        relocated_root = shutil.copytree(root, os.path.join(deeper, "cases_conflict"))
        relocated_checkers = shutil.copytree(CHECKERS_ROOT, os.path.join(deeper, "checkers"))

        out2 = os.path.join(deeper, "relocated.json")
        run_cli([relocated_root, "--checkers-root", relocated_checkers, "-o", out2])

        with open(out1, "rb") as f1, open(out2, "rb") as f2:
            self.assertEqual(f1.read(), f2.read())

    def test_output_file_written_with_trailing_newline_only(self):
        root = self._build_conflict_root(self.tmp)
        out1 = os.path.join(self.tmp, "r1.json")
        run_cli([root, "--checkers-root", CHECKERS_ROOT, "-o", out1])
        with open(out1, "rb") as f:
            data = f.read()
        self.assertEqual(data.count(b"\n"), 1)
        self.assertTrue(data.endswith(b"\n"))

    def test_output_flag_suppresses_stdout(self):
        root = self._build_conflict_root(self.tmp)
        out1 = os.path.join(self.tmp, "r1.json")
        code, out, err = run_cli([root, "--checkers-root", CHECKERS_ROOT, "-o", out1])
        self.assertEqual(out, "")


# ==========================================================================
# Edge cases explicitly called out in the task brief
# ==========================================================================

class EdgeCaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="contradict_edge_")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_nonexistent_case_root_exits_2(self):
        code, out, err = run_cli([os.path.join(self.tmp, "ghost")])
        self.assertEqual(code, 2)
        self.assertIn("error", err)

    def test_empty_case_directory_exits_0(self):
        empty = os.path.join(self.tmp, "empty")
        os.makedirs(empty)
        code, out, err = run_cli([empty, "--checkers-root", CHECKERS_ROOT])
        self.assertEqual(code, 0)

    def test_checker_absent_exits_2(self):
        case = os.path.join(self.tmp, "case1")
        os.makedirs(case)
        write_json(os.path.join(case, "submissions.json"),
                   [{"submission_id": "S1", "text": "hello there"}])
        missing_root = os.path.join(self.tmp, "no_checkers")
        code, out, err = run_cli([case, "--checkers-root", missing_root])
        self.assertEqual(code, 2)
        report = json.loads(out)
        self.assertGreaterEqual(report["code_counts"]["CHECKER_UNAVAILABLE"], 1)

    def test_checker_that_itself_exits_2_is_execution_failure(self):
        case = os.path.join(self.tmp, "case2")
        os.makedirs(case)
        # queue-auditor's snapshot must be {"tasks": [...]}; a bare array
        # makes queue_audit.py itself exit 2 (invalid snapshot shape).
        write_json(os.path.join(case, "queue_tasks.json"), [])
        code, out, err = run_cli([case, "--checkers-root", CHECKERS_ROOT])
        self.assertEqual(code, 2)
        report = json.loads(out)
        self.assertGreaterEqual(report["code_counts"]["EXECUTION_FAILURE"], 1)

    def test_both_checkers_clean_is_agree_with_zero_issues(self):
        case = os.path.join(self.tmp, "case3")
        os.makedirs(case)
        write_json(os.path.join(case, "submissions.json"),
                   [{"submission_id": "S1", "text": "Alpha bravo charlie delta echo foxtrot."},
                    {"submission_id": "S2", "text": "Something completely unrelated and distinct here."}])
        code, out, err = run_cli([case, "--checkers-root", CHECKERS_ROOT])
        self.assertEqual(code, 0)
        report = json.loads(out)
        self.assertEqual(report["summary"]["total_issues"], 0)

    def test_case_where_only_one_checker_of_a_pair_can_evaluate(self):
        case = os.path.join(self.tmp, "case4")
        os.makedirs(case)
        write_json(os.path.join(case, "tasks.json"),
                   [{"task_id": "T1", "title": "t", "status": "in_review",
                     "required_evidence": []}])
        write_json(os.path.join(case, "evidence.json"), [])
        code, out, err = run_cli([case, "--checkers-root", CHECKERS_ROOT])
        self.assertEqual(code, 0)
        report = json.loads(out)
        applicable = report["cases"][0]["checkers_applicable"]
        self.assertEqual(applicable, ["preflight"])
        self.assertNotIn("link-integrity", applicable)


if __name__ == "__main__":
    unittest.main()
