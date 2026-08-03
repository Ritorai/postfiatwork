"""unittest suite for preflight.py.

Run with:
    python3 -m unittest test_preflight -v
"""

import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest

import preflight


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

def make_task(task_id="T-1", title="Task", status="in_review", required_evidence=None):
    if required_evidence is None:
        required_evidence = ["url"]
    return {
        "task_id": task_id,
        "title": title,
        "status": status,
        "required_evidence": required_evidence,
    }


def make_evidence(submission_id="S-1", task_id="T-1", evidence_type="url",
                   value="https://example.com", notes=""):
    return {
        "submission_id": submission_id,
        "task_id": task_id,
        "evidence_type": evidence_type,
        "value": value,
        "notes": notes,
    }


def issues_by_code(issues, code):
    return [i for i in issues if i["code"] == code]


class TempDirMixin:
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="preflight_test_")

    def tearDown(self):
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def write_json(self, name, obj):
        path = os.path.join(self._tmpdir, name)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(obj, f)
        return path

    def write_raw(self, name, text):
        path = os.path.join(self._tmpdir, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path

    def path_in_tmp(self, name):
        return os.path.join(self._tmpdir, name)


# ---------------------------------------------------------------------------
# whitespace / emptiness detection
# ---------------------------------------------------------------------------

class WhitespaceDetectionTests(unittest.TestCase):
    def test_ascii_space_only_is_whitespace(self):
        self.assertTrue(preflight._is_unicode_whitespace_only("   "))

    def test_empty_string_is_not_whitespace_only(self):
        # len(s) > 0 is required; "" is handled separately as EMPTY, not
        # as "whitespace-only".
        self.assertFalse(preflight._is_unicode_whitespace_only(""))

    def test_non_whitespace_string_is_false(self):
        self.assertFalse(preflight._is_unicode_whitespace_only("hello"))

    def test_mixed_whitespace_and_text_is_false(self):
        self.assertFalse(preflight._is_unicode_whitespace_only("  hello  "))

    def test_tab_newline_cr_combo_is_whitespace(self):
        self.assertTrue(preflight._is_unicode_whitespace_only("\t\n\r\f\v"))


class ValueEmptinessTests(unittest.TestCase):
    def test_none_is_empty(self):
        self.assertTrue(preflight._value_is_empty(None))

    def test_empty_string_is_empty(self):
        self.assertTrue(preflight._value_is_empty(""))

    def test_ascii_whitespace_string_is_empty(self):
        self.assertTrue(preflight._value_is_empty("   "))

    def test_nonempty_string_is_not_empty(self):
        self.assertFalse(preflight._value_is_empty("hello"))

    def test_string_with_leading_trailing_space_and_content_not_empty(self):
        self.assertFalse(preflight._value_is_empty("  hello  "))

    def test_integer_zero_is_not_empty(self):
        self.assertFalse(preflight._value_is_empty(0))

    def test_integer_nonzero_is_not_empty(self):
        self.assertFalse(preflight._value_is_empty(42))

    def test_float_is_not_empty(self):
        self.assertFalse(preflight._value_is_empty(3.14))

    def test_false_boolean_is_not_empty(self):
        self.assertFalse(preflight._value_is_empty(False))

    def test_true_boolean_is_not_empty(self):
        self.assertFalse(preflight._value_is_empty(True))

    def test_empty_list_is_not_empty(self):
        # Deliberate: spec only covers missing/null/empty-string/whitespace.
        self.assertFalse(preflight._value_is_empty([]))

    def test_nonempty_list_is_not_empty(self):
        self.assertFalse(preflight._value_is_empty(["x"]))

    def test_empty_dict_is_not_empty(self):
        self.assertFalse(preflight._value_is_empty({}))

    def test_nonempty_dict_is_not_empty(self):
        self.assertFalse(preflight._value_is_empty({"a": 1}))


# Dynamically generate one pair of tests per unicode whitespace character:
# one confirming a string made purely of that character (repeated) is
# treated as empty, and one confirming that same character next to real
# content is NOT treated as empty. This exercises unicode-awareness (not
# just ASCII) as required by the spec.
UNICODE_WHITESPACE_CHARS = {
    "SPACE": "\u0020",
    "TAB": "\u0009",
    "LINE_FEED": "\u000A",
    "CARRIAGE_RETURN": "\u000D",
    "VERTICAL_TAB": "\u000B",
    "FORM_FEED": "\u000C",
    "NO_BREAK_SPACE": "\u00A0",
    "OGHAM_SPACE_MARK": "\u1680",
    "EN_QUAD": "\u2000",
    "EM_QUAD": "\u2001",
    "EN_SPACE": "\u2002",
    "EM_SPACE": "\u2003",
    "THREE_PER_EM_SPACE": "\u2004",
    "FOUR_PER_EM_SPACE": "\u2005",
    "SIX_PER_EM_SPACE": "\u2006",
    "FIGURE_SPACE": "\u2007",
    "PUNCTUATION_SPACE": "\u2008",
    "THIN_SPACE": "\u2009",
    "HAIR_SPACE": "\u200A",
    "LINE_SEPARATOR": "\u2028",
    "PARAGRAPH_SEPARATOR": "\u2029",
    "NARROW_NO_BREAK_SPACE": "\u202F",
    "MEDIUM_MATHEMATICAL_SPACE": "\u205F",
    "IDEOGRAPHIC_SPACE": "\u3000",
}


def _make_pure_whitespace_test(char):
    def test(self):
        value = char * 3
        self.assertTrue(preflight._value_is_empty(value))
        self.assertTrue(preflight._is_unicode_whitespace_only(value))
    return test


def _make_mixed_whitespace_test(char):
    def test(self):
        value = char + "x" + char
        self.assertFalse(preflight._value_is_empty(value))
        self.assertFalse(preflight._is_unicode_whitespace_only(value))
    return test


class UnicodeWhitespaceGeneratedTests(unittest.TestCase):
    pass


for _name, _char in UNICODE_WHITESPACE_CHARS.items():
    setattr(
        UnicodeWhitespaceGeneratedTests,
        "test_pure_{}_is_empty".format(_name.lower()),
        _make_pure_whitespace_test(_char),
    )
    setattr(
        UnicodeWhitespaceGeneratedTests,
        "test_mixed_{}_is_not_empty".format(_name.lower()),
        _make_mixed_whitespace_test(_char),
    )


# ---------------------------------------------------------------------------
# load_records
# ---------------------------------------------------------------------------

class LoadRecordsTests(TempDirMixin, unittest.TestCase):
    def test_file_not_found_raises(self):
        with self.assertRaises(preflight.PreflightInputError):
            preflight.load_records(self.path_in_tmp("nope.json"))

    def test_directory_raises(self):
        with self.assertRaises(preflight.PreflightInputError):
            preflight.load_records(self._tmpdir)

    def test_invalid_json_raises(self):
        path = self.write_raw("bad.json", "{not valid json")
        with self.assertRaises(preflight.PreflightInputError):
            preflight.load_records(path)

    def test_empty_file_raises(self):
        path = self.write_raw("empty.json", "")
        with self.assertRaises(preflight.PreflightInputError):
            preflight.load_records(path)

    def test_top_level_string_raises(self):
        path = self.write_raw("str.json", json.dumps("just a string"))
        with self.assertRaises(preflight.PreflightInputError):
            preflight.load_records(path)

    def test_top_level_number_raises(self):
        path = self.write_raw("num.json", json.dumps(42))
        with self.assertRaises(preflight.PreflightInputError):
            preflight.load_records(path)

    def test_top_level_bool_raises(self):
        path = self.write_raw("bool.json", json.dumps(True))
        with self.assertRaises(preflight.PreflightInputError):
            preflight.load_records(path)

    def test_top_level_null_raises(self):
        path = self.write_raw("null.json", json.dumps(None))
        with self.assertRaises(preflight.PreflightInputError):
            preflight.load_records(path)

    def test_single_object_is_wrapped_in_list(self):
        path = self.write_json("one.json", make_task())
        records = preflight.load_records(path)
        self.assertEqual(records, [make_task()])

    def test_array_is_returned_as_is(self):
        data = [make_task("T-1"), make_task("T-2")]
        path = self.write_json("many.json", data)
        records = preflight.load_records(path)
        self.assertEqual(records, data)

    def test_empty_array_returns_empty_list(self):
        path = self.write_json("empty_arr.json", [])
        self.assertEqual(preflight.load_records(path), [])

    def test_array_with_non_object_items_preserved(self):
        path = self.write_json("weird.json", ["not-an-object", 5, None])
        records = preflight.load_records(path)
        self.assertEqual(records, ["not-an-object", 5, None])


# ---------------------------------------------------------------------------
# _check_task_record
# ---------------------------------------------------------------------------

class CheckTaskRecordTests(unittest.TestCase):
    def check(self, record):
        issues = []
        task_id, valid = preflight._check_task_record(0, record, issues)
        return task_id, valid, issues

    def test_valid_record(self):
        task_id, valid, issues = self.check(make_task())
        self.assertEqual(task_id, "T-1")
        self.assertTrue(valid)
        self.assertEqual(issues, [])

    def test_not_a_dict(self):
        task_id, valid, issues = self.check("not a dict")
        self.assertIsNone(task_id)
        self.assertFalse(valid)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["code"], preflight.MALFORMED_RECORD)
        self.assertIsNone(issues[0]["field"])

    def test_missing_task_id(self):
        rec = make_task()
        del rec["task_id"]
        task_id, valid, issues = self.check(rec)
        self.assertIsNone(task_id)
        self.assertFalse(valid)
        self.assertTrue(any(i["field"] == "task_id" for i in issues))

    def test_empty_string_task_id(self):
        rec = make_task(task_id="")
        task_id, valid, issues = self.check(rec)
        self.assertIsNone(task_id)
        self.assertFalse(valid)

    def test_non_string_task_id(self):
        rec = make_task()
        rec["task_id"] = 123
        task_id, valid, issues = self.check(rec)
        self.assertIsNone(task_id)
        self.assertFalse(valid)

    def test_missing_title(self):
        rec = make_task()
        del rec["title"]
        _, valid, issues = self.check(rec)
        self.assertFalse(valid)
        self.assertTrue(any(i["field"] == "title" for i in issues))

    def test_non_string_title(self):
        rec = make_task()
        rec["title"] = ["not", "a", "string"]
        _, valid, _ = self.check(rec)
        self.assertFalse(valid)

    def test_empty_string_title_is_valid(self):
        rec = make_task(title="")
        _, valid, issues = self.check(rec)
        self.assertTrue(valid)
        self.assertEqual(issues, [])

    def test_missing_status(self):
        rec = make_task()
        del rec["status"]
        _, valid, issues = self.check(rec)
        self.assertFalse(valid)
        self.assertTrue(any(i["field"] == "status" for i in issues))

    def test_empty_string_status_invalid(self):
        rec = make_task(status="")
        _, valid, _ = self.check(rec)
        self.assertFalse(valid)

    def test_non_string_status(self):
        rec = make_task(status=123)
        _, valid, issues = self.check(rec)
        self.assertFalse(valid)
        self.assertTrue(any(i["field"] == "status" for i in issues))

    def test_missing_required_evidence(self):
        rec = make_task()
        del rec["required_evidence"]
        _, valid, issues = self.check(rec)
        self.assertFalse(valid)
        self.assertTrue(any(i["field"] == "required_evidence" for i in issues))

    def test_required_evidence_not_a_list(self):
        rec = make_task(required_evidence="url")
        _, valid, _ = self.check(rec)
        self.assertFalse(valid)

    def test_required_evidence_with_non_string_item(self):
        rec = make_task(required_evidence=["url", 5])
        _, valid, _ = self.check(rec)
        self.assertFalse(valid)

    def test_required_evidence_empty_list_is_valid(self):
        rec = make_task(required_evidence=[])
        _, valid, issues = self.check(rec)
        self.assertTrue(valid)
        self.assertEqual(issues, [])

    def test_multiple_bad_fields_produce_multiple_issues(self):
        rec = {"task_id": 1, "status": 2}
        _, valid, issues = self.check(rec)
        self.assertFalse(valid)
        fields = {i["field"] for i in issues}
        self.assertEqual(fields, {"task_id", "title", "status", "required_evidence"})


# ---------------------------------------------------------------------------
# _check_evidence_record
# ---------------------------------------------------------------------------

class CheckEvidenceRecordTests(unittest.TestCase):
    def check(self, record):
        issues = []
        valid = preflight._check_evidence_record(0, record, issues)
        return valid, issues

    def test_valid_record(self):
        valid, issues = self.check(make_evidence())
        self.assertTrue(valid)
        self.assertEqual(issues, [])

    def test_not_a_dict(self):
        valid, issues = self.check(42)
        self.assertFalse(valid)
        self.assertEqual(issues[0]["code"], preflight.MALFORMED_RECORD)

    def test_missing_submission_id(self):
        rec = make_evidence()
        del rec["submission_id"]
        valid, issues = self.check(rec)
        self.assertFalse(valid)
        self.assertTrue(any(i["field"] == "submission_id" for i in issues))

    def test_empty_submission_id(self):
        rec = make_evidence(submission_id="")
        valid, _ = self.check(rec)
        self.assertFalse(valid)

    def test_non_string_submission_id(self):
        rec = make_evidence()
        rec["submission_id"] = 5
        valid, _ = self.check(rec)
        self.assertFalse(valid)

    def test_missing_task_id(self):
        rec = make_evidence()
        del rec["task_id"]
        valid, issues = self.check(rec)
        self.assertFalse(valid)
        self.assertTrue(any(i["field"] == "task_id" for i in issues))

    def test_missing_evidence_type(self):
        rec = make_evidence()
        del rec["evidence_type"]
        valid, issues = self.check(rec)
        self.assertFalse(valid)
        self.assertTrue(any(i["field"] == "evidence_type" for i in issues))

    def test_empty_evidence_type(self):
        rec = make_evidence(evidence_type="")
        valid, _ = self.check(rec)
        self.assertFalse(valid)

    def test_missing_value_key(self):
        rec = make_evidence()
        del rec["value"]
        valid, issues = self.check(rec)
        self.assertFalse(valid)
        self.assertTrue(any(i["field"] == "value" for i in issues))

    def test_value_null_is_not_malformed(self):
        rec = make_evidence(value=None)
        valid, issues = self.check(rec)
        self.assertTrue(valid)
        self.assertEqual(issues, [])

    def test_value_number_is_not_malformed(self):
        rec = make_evidence(value=42)
        valid, issues = self.check(rec)
        self.assertTrue(valid)

    def test_value_list_is_not_malformed(self):
        rec = make_evidence(value=[1, 2, 3])
        valid, issues = self.check(rec)
        self.assertTrue(valid)

    def test_value_dict_is_not_malformed(self):
        rec = make_evidence(value={"a": 1})
        valid, issues = self.check(rec)
        self.assertTrue(valid)

    def test_notes_missing_is_fine(self):
        rec = make_evidence()
        del rec["notes"]
        valid, issues = self.check(rec)
        self.assertTrue(valid)

    def test_notes_wrong_type_is_fine(self):
        rec = make_evidence()
        rec["notes"] = 12345
        valid, issues = self.check(rec)
        self.assertTrue(valid)


# ---------------------------------------------------------------------------
# analyze() - the cross-file checks
# ---------------------------------------------------------------------------

class AnalyzeReadyTests(unittest.TestCase):
    def test_matching_single_task_and_evidence_is_ready(self):
        tasks = [make_task("T-1", required_evidence=["url"])]
        ev = [make_evidence("S-1", "T-1", "url", "https://x.example")]
        ready, issues, summary = preflight.analyze(tasks, ev)
        self.assertTrue(ready)
        self.assertEqual(issues, [])
        self.assertEqual(summary["issue_count"], 0)

    def test_both_empty_arrays_is_ready(self):
        ready, issues, summary = preflight.analyze([], [])
        self.assertTrue(ready)
        self.assertEqual(issues, [])
        self.assertEqual(summary["task_count"], 0)
        self.assertEqual(summary["evidence_count"], 0)

    def test_task_with_empty_required_evidence_and_no_evidence_is_ready(self):
        tasks = [make_task("T-1", required_evidence=[])]
        ready, issues, summary = preflight.analyze(tasks, [])
        self.assertTrue(ready)
        self.assertEqual(issues, [])

    def test_multiple_evidence_records_same_type_satisfy_requirement_once(self):
        tasks = [make_task("T-1", required_evidence=["url"])]
        ev = [
            make_evidence("S-1", "T-1", "url", "https://a.example"),
            make_evidence("S-2", "T-1", "url", "https://b.example"),
        ]
        ready, issues, summary = preflight.analyze(tasks, ev)
        self.assertTrue(ready)
        self.assertEqual(issues_by_code(issues, preflight.TASK_MISSING_EVIDENCE), [])
        self.assertEqual(issues_by_code(issues, preflight.EVIDENCE_TYPE_MISMATCH), [])

    def test_summary_issue_counts_by_code_includes_all_codes_when_ready(self):
        ready, issues, summary = preflight.analyze([], [])
        self.assertEqual(set(summary["issue_counts_by_code"].keys()), set(preflight.ALL_CODES))
        self.assertTrue(all(v == 0 for v in summary["issue_counts_by_code"].values()))


class AnalyzeOrphanEvidenceTests(unittest.TestCase):
    def test_evidence_for_unknown_task_is_orphan(self):
        tasks = [make_task("T-1", required_evidence=["url"])]
        ev = [make_evidence("S-1", "T-999", "url", "https://x.example")]
        ready, issues, summary = preflight.analyze(tasks, ev)
        self.assertFalse(ready)
        orphans = issues_by_code(issues, preflight.ORPHAN_EVIDENCE)
        self.assertEqual(len(orphans), 1)
        self.assertEqual(orphans[0]["task_id"], "T-999")
        self.assertEqual(orphans[0]["submission_id"], "S-1")

    def test_evidence_for_malformed_task_becomes_orphan(self):
        tasks = [{"task_id": "T-1", "title": "x", "status": 999, "required_evidence": []}]
        ev = [make_evidence("S-1", "T-1", "url", "https://x.example")]
        ready, issues, summary = preflight.analyze(tasks, ev)
        self.assertFalse(ready)
        self.assertTrue(any(i["code"] == preflight.MALFORMED_RECORD for i in issues))
        self.assertTrue(any(i["code"] == preflight.ORPHAN_EVIDENCE for i in issues))

    def test_no_orphan_when_task_id_matches(self):
        tasks = [make_task("T-1", required_evidence=["url"])]
        ev = [make_evidence("S-1", "T-1", "url", "https://x.example")]
        _, issues, _ = preflight.analyze(tasks, ev)
        self.assertEqual(issues_by_code(issues, preflight.ORPHAN_EVIDENCE), [])


class AnalyzeTaskMissingEvidenceTests(unittest.TestCase):
    def test_required_type_with_no_evidence_at_all_is_missing(self):
        tasks = [make_task("T-1", required_evidence=["url"])]
        _, issues, _ = preflight.analyze(tasks, [])
        missing = issues_by_code(issues, preflight.TASK_MISSING_EVIDENCE)
        self.assertEqual(len(missing), 1)
        self.assertEqual(missing[0]["evidence_type"], "url")

    def test_one_of_two_required_types_missing(self):
        tasks = [make_task("T-1", required_evidence=["url", "text"])]
        ev = [make_evidence("S-1", "T-1", "url", "https://x.example")]
        _, issues, _ = preflight.analyze(tasks, ev)
        missing = issues_by_code(issues, preflight.TASK_MISSING_EVIDENCE)
        self.assertEqual(len(missing), 1)
        self.assertEqual(missing[0]["evidence_type"], "text")

    def test_present_but_empty_value_does_not_count_as_missing(self):
        # A record exists (even though empty) so it is not "missing" -- it
        # is reported via EMPTY_EVIDENCE_VALUE instead.
        tasks = [make_task("T-1", required_evidence=["url"])]
        ev = [make_evidence("S-1", "T-1", "url", "")]
        _, issues, _ = preflight.analyze(tasks, ev)
        self.assertEqual(issues_by_code(issues, preflight.TASK_MISSING_EVIDENCE), [])
        self.assertEqual(len(issues_by_code(issues, preflight.EMPTY_EVIDENCE_VALUE)), 1)

    def test_no_missing_evidence_issue_when_required_evidence_empty(self):
        tasks = [make_task("T-1", required_evidence=[])]
        _, issues, _ = preflight.analyze(tasks, [])
        self.assertEqual(issues_by_code(issues, preflight.TASK_MISSING_EVIDENCE), [])

    def test_duplicate_entries_in_required_evidence_deduped(self):
        # Regression test: a task whose required_evidence list accidentally
        # repeats a type (e.g. ["url", "url", "text"]) must produce exactly
        # one TASK_MISSING_EVIDENCE issue per distinct type, not one per
        # repetition. This was a real bug found during testing: the fix
        # deduplicates required_evidence before generating issues.
        tasks = [make_task("T-1", required_evidence=["url", "url", "text"])]
        _, issues, summary = preflight.analyze(tasks, [])
        missing = issues_by_code(issues, preflight.TASK_MISSING_EVIDENCE)
        self.assertEqual(len(missing), 2)
        types_seen = sorted(i["evidence_type"] for i in missing)
        self.assertEqual(types_seen, ["text", "url"])
        self.assertEqual(summary["issue_count"], 2)

    def test_duplicate_entries_in_required_evidence_with_matching_evidence(self):
        tasks = [make_task("T-1", required_evidence=["url", "url"])]
        ev = [make_evidence("S-1", "T-1", "url", "https://x.example")]
        _, issues, summary = preflight.analyze(tasks, ev)
        self.assertEqual(issues_by_code(issues, preflight.TASK_MISSING_EVIDENCE), [])
        self.assertEqual(summary["issue_count"], 0)


class AnalyzeEvidenceTypeMismatchTests(unittest.TestCase):
    def test_type_not_in_required_list_is_mismatch(self):
        tasks = [make_task("T-1", required_evidence=["url"])]
        ev = [make_evidence("S-1", "T-1", "code", "print(1)")]
        _, issues, _ = preflight.analyze(tasks, ev)
        mismatches = issues_by_code(issues, preflight.EVIDENCE_TYPE_MISMATCH)
        self.assertEqual(len(mismatches), 1)
        self.assertEqual(mismatches[0]["evidence_type"], "code")

    def test_type_in_required_list_is_not_mismatch(self):
        tasks = [make_task("T-1", required_evidence=["url", "code"])]
        ev = [make_evidence("S-1", "T-1", "code", "print(1)")]
        _, issues, _ = preflight.analyze(tasks, ev)
        self.assertEqual(issues_by_code(issues, preflight.EVIDENCE_TYPE_MISMATCH), [])

    def test_case_sensitive_mismatch(self):
        tasks = [make_task("T-1", required_evidence=["url"])]
        ev = [make_evidence("S-1", "T-1", "URL", "https://x.example")]
        _, issues, _ = preflight.analyze(tasks, ev)
        self.assertEqual(len(issues_by_code(issues, preflight.EVIDENCE_TYPE_MISMATCH)), 1)
        # Because "URL" != "url", the task's "url" requirement also looks
        # unmet -- both codes fire for what may be a single typo.
        self.assertEqual(len(issues_by_code(issues, preflight.TASK_MISSING_EVIDENCE)), 1)

    def test_evidence_for_task_with_empty_required_list_is_always_mismatch(self):
        tasks = [make_task("T-1", required_evidence=[])]
        ev = [make_evidence("S-1", "T-1", "url", "https://x.example")]
        _, issues, _ = preflight.analyze(tasks, ev)
        self.assertEqual(len(issues_by_code(issues, preflight.EVIDENCE_TYPE_MISMATCH)), 1)


class AnalyzeEmptyEvidenceValueTests(unittest.TestCase):
    def test_null_value_flagged(self):
        tasks = [make_task("T-1")]
        ev = [make_evidence("S-1", "T-1", "url", None)]
        _, issues, _ = preflight.analyze(tasks, ev)
        self.assertEqual(len(issues_by_code(issues, preflight.EMPTY_EVIDENCE_VALUE)), 1)

    def test_empty_string_value_flagged(self):
        tasks = [make_task("T-1")]
        ev = [make_evidence("S-1", "T-1", "url", "")]
        _, issues, _ = preflight.analyze(tasks, ev)
        self.assertEqual(len(issues_by_code(issues, preflight.EMPTY_EVIDENCE_VALUE)), 1)

    def test_ascii_whitespace_value_flagged(self):
        tasks = [make_task("T-1")]
        ev = [make_evidence("S-1", "T-1", "url", "   \t\n")]
        _, issues, _ = preflight.analyze(tasks, ev)
        self.assertEqual(len(issues_by_code(issues, preflight.EMPTY_EVIDENCE_VALUE)), 1)

    def test_unicode_nbsp_value_flagged(self):
        tasks = [make_task("T-1")]
        ev = [make_evidence("S-1", "T-1", "url", "  ")]
        _, issues, _ = preflight.analyze(tasks, ev)
        self.assertEqual(len(issues_by_code(issues, preflight.EMPTY_EVIDENCE_VALUE)), 1)

    def test_unicode_ideographic_space_value_flagged(self):
        tasks = [make_task("T-1")]
        ev = [make_evidence("S-1", "T-1", "url", "\u3000\u3000")]
        _, issues, _ = preflight.analyze(tasks, ev)
        self.assertEqual(len(issues_by_code(issues, preflight.EMPTY_EVIDENCE_VALUE)), 1)

    def test_non_empty_value_not_flagged(self):
        tasks = [make_task("T-1")]
        ev = [make_evidence("S-1", "T-1", "url", "https://x.example")]
        _, issues, _ = preflight.analyze(tasks, ev)
        self.assertEqual(issues_by_code(issues, preflight.EMPTY_EVIDENCE_VALUE), [])

    def test_numeric_value_not_flagged_even_if_zero(self):
        tasks = [make_task("T-1")]
        ev = [make_evidence("S-1", "T-1", "url", 0)]
        _, issues, _ = preflight.analyze(tasks, ev)
        self.assertEqual(issues_by_code(issues, preflight.EMPTY_EVIDENCE_VALUE), [])

    def test_empty_list_value_not_flagged(self):
        tasks = [make_task("T-1")]
        ev = [make_evidence("S-1", "T-1", "url", [])]
        _, issues, _ = preflight.analyze(tasks, ev)
        self.assertEqual(issues_by_code(issues, preflight.EMPTY_EVIDENCE_VALUE), [])

    def test_empty_dict_value_not_flagged(self):
        tasks = [make_task("T-1")]
        ev = [make_evidence("S-1", "T-1", "url", {})]
        _, issues, _ = preflight.analyze(tasks, ev)
        self.assertEqual(issues_by_code(issues, preflight.EMPTY_EVIDENCE_VALUE), [])

    def test_empty_value_still_reported_for_orphan_evidence(self):
        ev = [make_evidence("S-1", "T-999", "url", "")]
        _, issues, _ = preflight.analyze([], ev)
        self.assertEqual(len(issues_by_code(issues, preflight.EMPTY_EVIDENCE_VALUE)), 1)
        self.assertEqual(len(issues_by_code(issues, preflight.ORPHAN_EVIDENCE)), 1)


class AnalyzeDuplicateSubmissionIdTests(unittest.TestCase):
    def test_two_duplicates_flagged_once_with_count_2(self):
        tasks = [make_task("T-1", required_evidence=["url"])]
        ev = [
            make_evidence("S-1", "T-1", "url", "https://a.example"),
            make_evidence("S-1", "T-1", "url", "https://b.example"),
        ]
        _, issues, _ = preflight.analyze(tasks, ev)
        dups = issues_by_code(issues, preflight.DUPLICATE_SUBMISSION_ID)
        self.assertEqual(len(dups), 1)
        self.assertEqual(dups[0]["count"], 2)

    def test_three_duplicates_flagged_once_with_count_3(self):
        tasks = [make_task("T-1", required_evidence=["url"])]
        ev = [make_evidence("S-1", "T-1", "url", "v{}".format(i)) for i in range(3)]
        _, issues, _ = preflight.analyze(tasks, ev)
        dups = issues_by_code(issues, preflight.DUPLICATE_SUBMISSION_ID)
        self.assertEqual(len(dups), 1)
        self.assertEqual(dups[0]["count"], 3)

    def test_unique_submission_ids_not_flagged(self):
        tasks = [make_task("T-1", required_evidence=["url"])]
        ev = [
            make_evidence("S-1", "T-1", "url", "https://a.example"),
            make_evidence("S-2", "T-1", "url", "https://b.example"),
        ]
        _, issues, _ = preflight.analyze(tasks, ev)
        self.assertEqual(issues_by_code(issues, preflight.DUPLICATE_SUBMISSION_ID), [])

    def test_malformed_duplicate_records_not_counted(self):
        # Records missing required fields never enter the duplicate check.
        tasks = [make_task("T-1", required_evidence=["url"])]
        ev = [
            {"submission_id": "S-1", "task_id": "T-1", "evidence_type": "url"},  # missing value key -> malformed
            {"submission_id": "S-1", "task_id": "T-1", "evidence_type": "url"},
        ]
        _, issues, _ = preflight.analyze(tasks, ev)
        self.assertEqual(issues_by_code(issues, preflight.DUPLICATE_SUBMISSION_ID), [])
        self.assertEqual(len(issues_by_code(issues, preflight.MALFORMED_RECORD)), 2)


class AnalyzeUnsubmittableStatusTests(unittest.TestCase):
    def test_refused_status_flagged(self):
        tasks = [make_task("T-1", status="refused", required_evidence=[])]
        _, issues, _ = preflight.analyze(tasks, [])
        flagged = issues_by_code(issues, preflight.UNSUBMITTABLE_STATUS)
        self.assertEqual(len(flagged), 1)
        self.assertEqual(flagged[0]["status"], "refused")

    def test_rewarded_status_flagged(self):
        tasks = [make_task("T-1", status="rewarded", required_evidence=[])]
        _, issues, _ = preflight.analyze(tasks, [])
        self.assertEqual(len(issues_by_code(issues, preflight.UNSUBMITTABLE_STATUS)), 1)

    def test_in_review_status_not_flagged(self):
        tasks = [make_task("T-1", status="in_review", required_evidence=[])]
        _, issues, _ = preflight.analyze(tasks, [])
        self.assertEqual(issues_by_code(issues, preflight.UNSUBMITTABLE_STATUS), [])

    def test_status_case_sensitive(self):
        tasks = [make_task("T-1", status="Refused", required_evidence=[])]
        _, issues, _ = preflight.analyze(tasks, [])
        self.assertEqual(issues_by_code(issues, preflight.UNSUBMITTABLE_STATUS), [])


class AnalyzeMalformedRecordTests(unittest.TestCase):
    def test_malformed_task_reported(self):
        tasks = [{"title": "no id"}]
        _, issues, summary = preflight.analyze(tasks, [])
        self.assertTrue(any(i["code"] == preflight.MALFORMED_RECORD for i in issues))
        self.assertEqual(summary["task_count"], 1)

    def test_malformed_evidence_reported(self):
        ev = [{"task_id": "T-1"}]
        _, issues, summary = preflight.analyze([], ev)
        self.assertTrue(any(i["code"] == preflight.MALFORMED_RECORD for i in issues))
        self.assertEqual(summary["evidence_count"], 1)

    def test_non_dict_task_item_reported(self):
        tasks = ["not a dict"]
        _, issues, _ = preflight.analyze(tasks, [])
        m = issues_by_code(issues, preflight.MALFORMED_RECORD)
        self.assertEqual(len(m), 1)
        self.assertIsNone(m[0]["field"])

    def test_non_dict_evidence_item_reported(self):
        ev = [None]
        _, issues, _ = preflight.analyze([], ev)
        m = issues_by_code(issues, preflight.MALFORMED_RECORD)
        self.assertEqual(len(m), 1)


class AnalyzeDuplicateTaskIdTests(unittest.TestCase):
    def test_duplicate_task_id_does_not_crash_and_first_wins(self):
        tasks = [
            make_task("T-1", required_evidence=["url"]),
            make_task("T-1", required_evidence=["code"]),
        ]
        ev = [make_evidence("S-1", "T-1", "url", "https://x.example")]
        ready, issues, summary = preflight.analyze(tasks, ev)
        self.assertEqual(summary["task_count"], 2)
        # First occurrence's required_evidence (["url"]) wins, so the "url"
        # evidence satisfies it and there is no mismatch/missing issue.
        self.assertEqual(issues_by_code(issues, preflight.EVIDENCE_TYPE_MISMATCH), [])
        self.assertEqual(issues_by_code(issues, preflight.TASK_MISSING_EVIDENCE), [])


# ---------------------------------------------------------------------------
# determinism / canonical JSON
# ---------------------------------------------------------------------------

class DeterminismTests(unittest.TestCase):
    def test_repeated_analyze_same_input_byte_identical_output(self):
        tasks = [make_task("T-1", required_evidence=["url", "text"])]
        ev = [
            make_evidence("S-1", "T-1", "url", None),
            make_evidence("S-2", "T-1", "junk", "value"),
        ]
        out1 = preflight.to_canonical_json(preflight.build_report(tasks, ev))
        out2 = preflight.to_canonical_json(preflight.build_report(tasks, ev))
        self.assertEqual(out1, out2)

    def test_issue_order_independent_of_evidence_record_order(self):
        tasks = [make_task("T-1", required_evidence=["url", "text", "code"])]
        ev_a = [
            make_evidence("S-1", "T-1", "url", None),
            make_evidence("S-2", "T-1", "bogus", "x"),
        ]
        ev_b = list(reversed(ev_a))
        out_a = preflight.to_canonical_json(preflight.build_report(tasks, ev_a))
        out_b = preflight.to_canonical_json(preflight.build_report(tasks, ev_b))
        self.assertEqual(out_a, out_b)

    def test_issue_order_independent_of_task_record_order(self):
        tasks_a = [make_task("T-1", required_evidence=["url"]), make_task("T-2", required_evidence=["url"])]
        tasks_b = list(reversed(tasks_a))
        out_a = preflight.to_canonical_json(preflight.build_report(tasks_a, []))
        out_b = preflight.to_canonical_json(preflight.build_report(tasks_b, []))
        self.assertEqual(out_a, out_b)

    def test_canonical_json_has_sorted_keys(self):
        obj = {"b": 1, "a": 2}
        text = preflight.to_canonical_json(obj)
        self.assertEqual(text, '{"a":2,"b":1}\n')

    def test_canonical_json_uses_compact_separators(self):
        obj = {"a": [1, 2], "b": {"c": 3}}
        text = preflight.to_canonical_json(obj)
        self.assertNotIn(" ", text.rstrip("\n"))

    def test_canonical_json_ends_with_single_newline(self):
        text = preflight.to_canonical_json({"a": 1})
        self.assertTrue(text.endswith("\n"))
        self.assertFalse(text.endswith("\n\n"))

    def test_canonical_json_ascii_only(self):
        obj = {"note": "caf\u00e9 \u3000"}
        text = preflight.to_canonical_json(obj)
        self.assertTrue(all(ord(c) < 128 for c in text))
        self.assertIn("\\u", text)

    def test_no_wallclock_or_host_fields_in_report(self):
        tasks = [make_task("T-1", required_evidence=[])]
        report = preflight.build_report(tasks, [])
        blob = json.dumps(report)
        for forbidden in ("time", "timestamp", "hostname", "host", "cwd", "path"):
            self.assertNotIn(forbidden, blob.lower())


class BuildReportStructureTests(unittest.TestCase):
    def test_report_has_expected_top_level_keys(self):
        report = preflight.build_report([], [])
        self.assertEqual(set(report.keys()), {"ready", "summary", "issues"})

    def test_summary_has_expected_keys(self):
        report = preflight.build_report([], [])
        self.assertEqual(
            set(report["summary"].keys()),
            {"task_count", "evidence_count", "issue_count", "issue_counts_by_code"},
        )

    def test_ready_false_when_issues_present(self):
        tasks = [make_task("T-1", required_evidence=["url"])]
        report = preflight.build_report(tasks, [])
        self.assertFalse(report["ready"])
        self.assertGreater(len(report["issues"]), 0)

    def test_ready_true_when_no_issues(self):
        report = preflight.build_report([], [])
        self.assertTrue(report["ready"])
        self.assertEqual(report["issues"], [])


# ---------------------------------------------------------------------------
# CLI / main()
# ---------------------------------------------------------------------------

class MainCliTests(TempDirMixin, unittest.TestCase):
    def run_main(self, argv):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = preflight.main(argv)
        return code, stdout.getvalue(), stderr.getvalue()

    def test_no_args_exits_2(self):
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as cm:
                preflight.main([])
        self.assertEqual(cm.exception.code, 2)

    def test_one_arg_exits_2(self):
        tasks_path = self.write_json("t.json", [make_task()])
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as cm:
                preflight.main([tasks_path])
        self.assertEqual(cm.exception.code, 2)

    def test_unknown_flag_exits_2(self):
        tasks_path = self.write_json("t.json", [make_task()])
        ev_path = self.write_json("e.json", [])
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as cm:
                preflight.main([tasks_path, ev_path, "--bogus-flag"])
        self.assertEqual(cm.exception.code, 2)

    def test_nonexistent_tasks_file_exits_2(self):
        ev_path = self.write_json("e.json", [])
        code, out, err = self.run_main([self.path_in_tmp("nope.json"), ev_path])
        self.assertEqual(code, 2)
        self.assertIn("error", err)

    def test_nonexistent_evidence_file_exits_2(self):
        tasks_path = self.write_json("t.json", [make_task(required_evidence=[])])
        code, out, err = self.run_main([tasks_path, self.path_in_tmp("nope.json")])
        self.assertEqual(code, 2)

    def test_invalid_json_exits_2(self):
        tasks_path = self.write_raw("t.json", "{bad")
        ev_path = self.write_json("e.json", [])
        code, out, err = self.run_main([tasks_path, ev_path])
        self.assertEqual(code, 2)

    def test_ready_input_exits_0_and_prints_json(self):
        tasks_path = self.write_json("t.json", [make_task("T-1", required_evidence=["url"])])
        ev_path = self.write_json("e.json", [make_evidence("S-1", "T-1", "url", "https://x.example")])
        code, out, err = self.run_main([tasks_path, ev_path])
        self.assertEqual(code, 0)
        report = json.loads(out)
        self.assertTrue(report["ready"])

    def test_issues_input_exits_1(self):
        tasks_path = self.write_json("t.json", [make_task("T-1", required_evidence=["url"])])
        ev_path = self.write_json("e.json", [])
        code, out, err = self.run_main([tasks_path, ev_path])
        self.assertEqual(code, 1)
        report = json.loads(out)
        self.assertFalse(report["ready"])

    def test_output_flag_writes_file_and_stdout_empty(self):
        tasks_path = self.write_json("t.json", [make_task("T-1", required_evidence=[])])
        ev_path = self.write_json("e.json", [])
        out_path = self.path_in_tmp("report.json")
        code, out, err = self.run_main([tasks_path, ev_path, "-o", out_path])
        self.assertEqual(code, 0)
        self.assertEqual(out, "")
        with open(out_path, encoding="utf-8") as f:
            report = json.load(f)
        self.assertTrue(report["ready"])

    def test_output_flag_long_form(self):
        tasks_path = self.write_json("t.json", [make_task("T-1", required_evidence=[])])
        ev_path = self.write_json("e.json", [])
        out_path = self.path_in_tmp("report2.json")
        code, out, err = self.run_main([tasks_path, ev_path, "--output", out_path])
        self.assertEqual(code, 0)
        self.assertTrue(os.path.exists(out_path))

    def test_output_to_unwritable_directory_exits_2(self):
        tasks_path = self.write_json("t.json", [make_task("T-1", required_evidence=[])])
        ev_path = self.write_json("e.json", [])
        bad_out = os.path.join(self._tmpdir, "no_such_dir", "report.json")
        code, out, err = self.run_main([tasks_path, ev_path, "-o", bad_out])
        self.assertEqual(code, 2)

    def test_single_object_input_files_work(self):
        tasks_path = self.write_json("t.json", make_task("T-1", required_evidence=["url"]))
        ev_path = self.write_json("e.json", make_evidence("S-1", "T-1", "url", "https://x.example"))
        code, out, err = self.run_main([tasks_path, ev_path])
        self.assertEqual(code, 0)

    def test_two_runs_produce_byte_identical_output_file(self):
        tasks_path = self.write_json(
            "t.json",
            [make_task("T-1", status="refused", required_evidence=["url", "text"])],
        )
        ev_path = self.write_json(
            "e.json",
            [
                make_evidence("S-2", "T-1", "url", None),
                make_evidence("S-1", "T-1", "bogus", "x"),
            ],
        )
        out1 = self.path_in_tmp("r1.json")
        out2 = self.path_in_tmp("r2.json")
        self.run_main([tasks_path, ev_path, "-o", out1])
        self.run_main([tasks_path, ev_path, "-o", out2])
        with open(out1, "rb") as f:
            b1 = f.read()
        with open(out2, "rb") as f:
            b2 = f.read()
        self.assertEqual(b1, b2)


class FixtureIntegrationTests(TempDirMixin, unittest.TestCase):
    """Exercise the actual fixture files shipped alongside this tool."""

    FIXTURE_DIR = os.path.dirname(os.path.abspath(__file__))

    def fixture_path(self, name):
        return os.path.join(self.FIXTURE_DIR, name)

    def run_main(self, argv):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = preflight.main(argv)
        return code, stdout.getvalue(), stderr.getvalue()

    def test_ready_fixtures_exit_0(self):
        code, out, err = self.run_main(
            [self.fixture_path("tasks_ready.json"), self.fixture_path("evidence_ready.json")]
        )
        self.assertEqual(code, 0)
        report = json.loads(out)
        self.assertTrue(report["ready"])
        self.assertEqual(report["issues"], [])

    def test_issues_fixtures_exit_1(self):
        code, out, err = self.run_main(
            [self.fixture_path("tasks_issues.json"), self.fixture_path("evidence_issues.json")]
        )
        self.assertEqual(code, 1)
        report = json.loads(out)
        self.assertFalse(report["ready"])

    def test_issues_fixtures_trigger_every_issue_code(self):
        code, out, err = self.run_main(
            [self.fixture_path("tasks_issues.json"), self.fixture_path("evidence_issues.json")]
        )
        report = json.loads(out)
        codes_seen = {i["code"] for i in report["issues"]}
        self.assertEqual(codes_seen, set(preflight.ALL_CODES))

    def test_issues_fixtures_summary_counts_match_issue_list(self):
        code, out, err = self.run_main(
            [self.fixture_path("tasks_issues.json"), self.fixture_path("evidence_issues.json")]
        )
        report = json.loads(out)
        counted = {}
        for i in report["issues"]:
            counted[i["code"]] = counted.get(i["code"], 0) + 1
        for code_name, count in report["summary"]["issue_counts_by_code"].items():
            self.assertEqual(count, counted.get(code_name, 0))


# ---------------------------------------------------------------------------
# Issue ordering / sort key
# ---------------------------------------------------------------------------

class SortKeyTests(unittest.TestCase):
    def test_sort_key_orders_by_code_alphabetically(self):
        i1 = {"code": "ORPHAN_EVIDENCE", "task_id": "T-1"}
        i2 = {"code": "DUPLICATE_SUBMISSION_ID", "submission_id": "S-1", "count": 2}
        self.assertLess(preflight._sort_key(i2), preflight._sort_key(i1))

    def test_sort_key_stable_for_identical_issue(self):
        issue = {"code": "ORPHAN_EVIDENCE", "task_id": "T-1", "submission_id": "S-1"}
        self.assertEqual(preflight._sort_key(issue), preflight._sort_key(dict(issue)))

    def test_issues_list_is_sorted_in_output(self):
        tasks = [make_task("T-1", required_evidence=["url"]), make_task("T-2", required_evidence=["url"])]
        _, issues, _ = preflight.analyze(tasks, [])
        codes = [i["code"] for i in issues]
        self.assertEqual(codes, sorted(codes))


if __name__ == "__main__":
    unittest.main()
