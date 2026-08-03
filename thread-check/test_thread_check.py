"""test_thread_check.py -- stdlib-only unittest suite for thread_check.py.

Run with:  python3 -m unittest test_thread_check -v
"""

import json
import os
import subprocess
import sys
import unittest
from datetime import datetime, timedelta, timezone

import thread_check as tc

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "thread_check.py")
COMPLETE_FIXTURE = os.path.join(HERE, "threads_complete.json")
INCOMPLETE_FIXTURE = os.path.join(HERE, "threads_incomplete.json")

NOW = datetime(2026, 8, 3, 0, 0, 0, tzinfo=timezone.utc)
NOW_ISO = "2026-08-03T00:00:00Z"


def run_cli(args, cwd=HERE):
    """Run the CLI as a subprocess exactly like a real user would."""
    return subprocess.run(
        [sys.executable, SCRIPT] + args,
        cwd=cwd,
        capture_output=True,
        text=True,
    )


def seconds_ago(s):
    return tc.iso_z(NOW - timedelta(seconds=s))


def hours_ago(h):
    return seconds_ago(h * 3600)


def msg(message_id, role, at, text, in_reply_to=None, **extra):
    d = {"message_id": message_id, "role": role, "at": at, "text": text}
    if in_reply_to is not None:
        d["in_reply_to"] = in_reply_to
    d.update(extra)
    return d


def mk_thread(task_id="T-1", messages=None):
    return {"task_id": task_id, "messages": [] if messages is None else messages}


def codes_of(findings):
    return [f["code"] for f in findings]


def only(findings, code):
    return [f for f in findings if f["code"] == code]


_SIDE_FIXTURES = {
    "not_json.txt": "this is { not valid json",
    "object_not_array.json": json.dumps({"task_id": "x"}),
    "empty_array.json": "[]",
}
_SIDE_FIXTURE_PATHS = []


def setUpModule():
    for name, content in _SIDE_FIXTURES.items():
        path = os.path.join(HERE, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(content)
        _SIDE_FIXTURE_PATHS.append(path)


def tearDownModule():
    for path in _SIDE_FIXTURE_PATHS:
        if os.path.exists(path):
            os.remove(path)


# ==========================================================================
# parse_utc_timestamp (reused verbatim from loop_health.py)
# ==========================================================================

class TestParseUtcTimestampValid(unittest.TestCase):
    pass


VALID_TIMESTAMP_CASES = {
    "uppercase_z": ("2026-08-02T00:00:00Z", datetime(2026, 8, 2, 0, 0, 0, tzinfo=timezone.utc)),
    "lowercase_z": ("2026-08-02T00:00:00z", datetime(2026, 8, 2, 0, 0, 0, tzinfo=timezone.utc)),
    "plus_zero_offset": ("2026-08-02T00:00:00+00:00", datetime(2026, 8, 2, 0, 0, 0, tzinfo=timezone.utc)),
    "minus_zero_offset": ("2026-08-02T00:00:00-00:00", datetime(2026, 8, 2, 0, 0, 0, tzinfo=timezone.utc)),
    "microseconds_with_z": ("2026-08-02T00:00:00.500000Z", datetime(2026, 8, 2, 0, 0, 0, 500000, tzinfo=timezone.utc)),
    "microseconds_with_offset": ("2026-08-02T00:00:00.123456+00:00", datetime(2026, 8, 2, 0, 0, 0, 123456, tzinfo=timezone.utc)),
    "leading_trailing_whitespace": ("  2026-08-02T00:00:00Z  ", datetime(2026, 8, 2, 0, 0, 0, tzinfo=timezone.utc)),
    "end_of_year": ("2026-12-31T23:59:59Z", datetime(2026, 12, 31, 23, 59, 59, tzinfo=timezone.utc)),
    "leap_day": ("2028-02-29T12:00:00Z", datetime(2028, 2, 29, 12, 0, 0, tzinfo=timezone.utc)),
    "space_separator_with_offset": ("2026-08-02 00:00:00+00:00", datetime(2026, 8, 2, 0, 0, 0, tzinfo=timezone.utc)),
}


def _make_valid_test(raw, expected):
    def test(self):
        result = tc.parse_utc_timestamp(raw)
        self.assertEqual(result, expected)
        self.assertEqual(result.tzinfo, timezone.utc)
    return test


for _name, (_raw, _expected) in VALID_TIMESTAMP_CASES.items():
    setattr(TestParseUtcTimestampValid, f"test_valid_{_name}", _make_valid_test(_raw, _expected))


class TestParseUtcTimestampInvalid(unittest.TestCase):
    pass


INVALID_TIMESTAMP_CASES = {
    "positive_offset_0530": "2026-08-02T00:00:00+05:30",
    "negative_offset_0800": "2026-08-02T00:00:00-08:00",
    "timezone_naive": "2026-08-02T00:00:00",
    "empty_string": "",
    "whitespace_only": "   ",
    "garbage_text": "not-a-real-date",
    "bad_month": "2026-13-01T00:00:00Z",
    "bad_day": "2026-02-30T00:00:00Z",
    "bad_hour": "2026-08-02T25:00:00Z",
    "z_with_embedded_offset": "2026-08-02T00:00:00+00:00Z",
    "date_only_no_z": "2026-08-02",
    "none_value": None,
    "integer_value": 20260802,
    "float_value": 1735689600.0,
    "list_value": ["2026-08-02T00:00:00Z"],
    "dict_value": {"iso": "2026-08-02T00:00:00Z"},
    "boolean_value": True,
    "slash_date": "2026/08/02T00:00:00Z",
    "trailing_garbage_after_z": "2026-08-02T00:00:00Zgarbage",
}


def _make_invalid_test(raw):
    def test(self):
        with self.assertRaises(ValueError):
            tc.parse_utc_timestamp(raw)
    return test


for _name, _raw in INVALID_TIMESTAMP_CASES.items():
    setattr(TestParseUtcTimestampInvalid, f"test_invalid_{_name}", _make_invalid_test(_raw))


# ==========================================================================
# iso_z
# ==========================================================================

class TestIsoZ(unittest.TestCase):
    def test_replaces_plus_zero_offset_with_z(self):
        dt = datetime(2026, 8, 2, 0, 0, 0, tzinfo=timezone.utc)
        self.assertEqual(tc.iso_z(dt), "2026-08-02T00:00:00Z")

    def test_roundtrips_through_parse(self):
        dt = tc.parse_utc_timestamp("2026-08-02T13:45:30Z")
        self.assertEqual(tc.iso_z(dt), "2026-08-02T13:45:30Z")

    def test_preserves_microseconds(self):
        dt = tc.parse_utc_timestamp("2026-08-02T00:00:00.500000Z")
        self.assertIn("500000", tc.iso_z(dt))

    def test_ends_with_z_not_offset(self):
        dt = tc.parse_utc_timestamp("2026-08-02T00:00:00+00:00")
        self.assertTrue(tc.iso_z(dt).endswith("Z"))
        self.assertNotIn("+00:00", tc.iso_z(dt))


# ==========================================================================
# format_age
# ==========================================================================

class TestFormatAge(unittest.TestCase):
    pass


FORMAT_AGE_CASES = {
    "zero": (0, "0d 0h 0m"),
    "one_minute": (60, "0d 0h 1m"),
    "one_hour": (3600, "0d 1h 0m"),
    "one_day": (86400, "1d 0h 0m"),
    "example": (262800, "3d 1h 0m"),
    "sub_minute_truncated": (59, "0d 0h 0m"),
    "sub_minute_truncated_2": (119, "0d 0h 1m"),
    "just_under_a_day": (86399, "0d 23h 59m"),
    "large_value": (10 * 86400 + 5 * 3600 + 30 * 60, "10d 5h 30m"),
    "negative_one_hour": (-3600, "-0d 1h 0m"),
    "negative_zero": (-0, "0d 0h 0m"),
    "fractional_truncated_down": (90.9, "0d 0h 1m"),
    "forty_eight_hours_exactly": (48 * 3600, "2d 0h 0m"),
    "one_second": (1, "0d 0h 0m"),
}


def _make_format_age_test(seconds, expected):
    def test(self):
        self.assertEqual(tc.format_age(seconds), expected)
    return test


for _name, (_seconds, _expected) in FORMAT_AGE_CASES.items():
    setattr(TestFormatAge, f"test_{_name}", _make_format_age_test(_seconds, _expected))


# ==========================================================================
# has_question
# ==========================================================================

class TestHasQuestionPositive(unittest.TestCase):
    pass


POSITIVE_QUESTION_CASES = {
    "plain_question_mark": "Why is this broken?",
    "question_mark_mid_message": "I looked at this. Is it done? Let me know.",
    "two_questions_one_message": "What changed? Why did it change?",
    "lead_what": "What is the plan for testing this change",
    "lead_why": "Why did you choose this approach",
    "lead_how": "How does this handle errors",
    "lead_when": "When will this be ready",
    "lead_where": "Where is the config stored",
    "lead_who": "Who reviewed this change",
    "lead_which": "Which version supports this",
    "lead_can_you": "Can you explain the reasoning here",
    "lead_could_you": "Could you clarify this point",
    "lead_would_you": "Would you mind adding a test",
    "lead_will_you": "Will you update the docs",
    "lead_do_you": "Do you have a link for this",
    "lead_does_this": "Does this handle the edge case",
    "lead_is_this": "Is this expected behavior",
    "lead_are_these": "Are these tests passing",
    "please_clarify": "Please clarify the deployment steps",
    "please_explain": "Please explain your reasoning",
    "please_confirm": "Please confirm the fix works",
    "please_provide": "Please provide more context",
    "please_share": "Please share the logs",
    "please_describe": "Please describe the failure",
    "lead_in_second_sentence": "Thanks for the update. Why did you change the retry logic",
    "lead_exact_phrase_no_trailing_words": "Why",
    "unicode_with_question_mark": "这个改了什么？",
}


def _make_has_question_true_test(text):
    def test(self):
        self.assertTrue(tc.has_question(text))
    return test


for _name, _text in POSITIVE_QUESTION_CASES.items():
    setattr(TestHasQuestionPositive, f"test_{_name}", _make_has_question_true_test(_text))


class TestHasQuestionNegative(unittest.TestCase):
    pass


NEGATIVE_QUESTION_CASES = {
    "plain_statement": "This works fine.",
    "status_update": "I updated the code.",
    "thanks": "Thanks for the PR.",
    "empty_string": "",
    "praise": "Great job on this feature.",
    "multi_sentence_no_question": "I looked at this. It seems fine. Nice work.",
    "lead_word_not_at_sentence_start": "I wonder what happened here.",
    "none_value": None,
}


def _make_has_question_false_test(text):
    def test(self):
        self.assertFalse(tc.has_question(text))
    return test


for _name, _text in NEGATIVE_QUESTION_CASES.items():
    setattr(TestHasQuestionNegative, f"test_{_name}", _make_has_question_false_test(_text))


class TestHasQuestionDocumentedLimitation(unittest.TestCase):
    def test_interrogative_sentence_without_lead_or_mark_is_missed(self):
        # Documented false-negative: this is a genuine (if unusually phrased)
        # question, but it has neither '?' nor a recognized lead phrase at
        # the start of its sentence, so has_question misses it.
        self.assertFalse(tc.has_question("I wonder what happened here."))


# ==========================================================================
# has_artifact_reference / artifact_kinds
# ==========================================================================

class TestArtifactReferencePositive(unittest.TestCase):
    pass


POSITIVE_ARTIFACT_CASES = {
    "url": "See https://github.com/org/repo/pull/42 for details",
    "commit_sha_7": "fixed in abcdef1, see the diff",
    "commit_sha_long": "commit 0123456789abcdef0123456789abcdef01234567 has the fix",
    "sha256": "sha256sum: " + ("a1b2c3d4" * 8),
    "code_span": "the fix is in `retry_client.send()`",
    "test_method": "covered by test_retry_backoff_caps",
    "file_path_with_dir": "src/foo/bar.py was updated",
    "file_path_bare": "see README.md for the write-up",
    "file_path_yaml": "updated config.yaml with the new value",
    "shell_prompt_style": "$ python3 thread_check.py threads.json",
    "shell_known_binary": "run pytest test_thread_check.py to confirm",
    "git_command": "git log -1 abcdef1 shows the change",
}


def _make_artifact_true_test(text):
    def test(self):
        self.assertTrue(tc.has_artifact_reference(text))
        self.assertTrue(len(tc.artifact_kinds(text)) >= 1)
    return test


for _name, _text in POSITIVE_ARTIFACT_CASES.items():
    setattr(TestArtifactReferencePositive, f"test_{_name}", _make_artifact_true_test(_text))


class TestArtifactReferenceNegative(unittest.TestCase):
    pass


NEGATIVE_ARTIFACT_CASES = {
    "prose_assurance": "I verified this works and confirmed the tests pass.",
    "prose_ship_it": "Looks good to me, ship it.",
    "prose_checked_manually": "I checked it manually and it's fine.",
    "empty_string": "",
    "plain_seven_digit_number": "the ticket number is 1234567",
    "sentence_with_period_abbreviation": "This handles that case, e.g. retries.",
    "none_value": None,
}


def _make_artifact_false_test(text):
    def test(self):
        self.assertFalse(tc.has_artifact_reference(text))
        self.assertEqual(tc.artifact_kinds(text), [])
    return test


for _name, _text in NEGATIVE_ARTIFACT_CASES.items():
    setattr(TestArtifactReferenceNegative, f"test_{_name}", _make_artifact_false_test(_text))


class TestArtifactReferenceFalsePositiveRisk(unittest.TestCase):
    def test_url_present_but_irrelevant_still_counts(self):
        # Documented, deliberate: this check measures FORM, not substance.
        # A URL that has nothing to do with the actual question still
        # satisfies "has a concrete artifact reference."
        text = "By the way, check out https://example.com/cat-pictures, unrelated to this."
        self.assertTrue(tc.has_artifact_reference(text))

    def test_all_digit_token_is_not_treated_as_commit_sha(self):
        # A run of digits alone (no a-f letter) is NOT treated as a commit
        # SHA, specifically to avoid flagging phone numbers / ticket IDs.
        self.assertNotIn("commit_sha", tc.artifact_kinds("call me at 5551234"))

    def test_sha256_not_double_counted_as_commit_sha(self):
        kinds = tc.artifact_kinds("hash: " + ("a" * 64))
        self.assertIn("sha256", kinds)
        self.assertNotIn("commit_sha", kinds)


# ==========================================================================
# is_restatement_only
# ==========================================================================

class TestRestatementOnly(unittest.TestCase):
    def test_verbatim_quote_no_artifact_flagged(self):
        q = "Why did you change the retry logic in the client?"
        r = "Why did you change the retry logic in the client?"
        restated, overlap, new_ratio = tc.is_restatement_only(q, r)
        self.assertTrue(restated)
        self.assertEqual(overlap, 1.0)
        self.assertEqual(new_ratio, 0.0)

    def test_verbatim_quote_plus_commit_sha_not_flagged(self):
        # Required by spec: quoting the question AND supplying a concrete
        # artifact must NOT be treated as a restatement.
        q = "Why did you change the retry logic in the client?"
        r = "Why did you change the retry logic in the client? See commit abcdef1 for the fix."
        restated, _overlap, _new_ratio = tc.is_restatement_only(q, r)
        self.assertFalse(restated)

    def test_quote_plus_substantial_new_content_not_flagged(self):
        q = "Why did you change the retry logic in the client?"
        r = (
            "Why did you change the retry logic in the client? I rewrote the "
            "exponential backoff to cap at 30 seconds and added jitter to "
            "avoid thundering herd problems across replicas."
        )
        restated, _overlap, new_ratio = tc.is_restatement_only(q, r)
        self.assertFalse(restated)
        self.assertGreater(new_ratio, tc.RESTATEMENT_NEW_TOKEN_MAX_RATIO)

    def test_short_question_below_min_tokens_never_flagged(self):
        q = "Is this correct?"  # 3 distinct tokens: is, this, correct
        r = "Is this correct?"
        restated, _overlap, _new_ratio = tc.is_restatement_only(q, r)
        self.assertFalse(restated)

    def test_question_exactly_at_min_tokens_is_checked(self):
        q = "one two three four"  # exactly 4 distinct tokens
        r = "one two three four"
        restated, _overlap, _new_ratio = tc.is_restatement_only(q, r)
        self.assertTrue(restated)

    def test_overlap_exactly_at_threshold_flagged(self):
        q = "one two three four five six seven eight nine ten"
        r = "one two three four five six seven"  # 7/10 = 0.70 exactly
        restated, overlap, _new_ratio = tc.is_restatement_only(q, r)
        self.assertAlmostEqual(overlap, 0.70)
        self.assertTrue(restated)

    def test_overlap_just_below_threshold_not_flagged(self):
        q = "one two three four five six seven eight nine ten"
        r = "one two three four five six"  # 6/10 = 0.60
        restated, overlap, _new_ratio = tc.is_restatement_only(q, r)
        self.assertAlmostEqual(overlap, 0.60)
        self.assertFalse(restated)

    def test_new_token_ratio_exactly_at_threshold_flagged(self):
        q = "alpha beta gamma delta epsilon zeta eta"  # 7 tokens
        r = "alpha beta gamma delta epsilon zeta eta theta iota kappa"  # +3 new / 10 total = 0.30
        restated, _overlap, new_ratio = tc.is_restatement_only(q, r)
        self.assertAlmostEqual(new_ratio, 0.30)
        self.assertTrue(restated)

    def test_new_token_ratio_just_above_threshold_not_flagged(self):
        q = "alpha beta gamma delta epsilon zeta eta"  # 7 tokens
        r = "alpha beta gamma delta epsilon zeta eta theta iota kappa lambda"  # +4/11
        restated, _overlap, new_ratio = tc.is_restatement_only(q, r)
        self.assertGreater(new_ratio, 0.30)
        self.assertFalse(restated)

    def test_completely_different_response_not_flagged(self):
        q = "Why did you change the retry logic in the client?"
        r = "The deployment finished successfully and all health checks are green."
        restated, _overlap, _new_ratio = tc.is_restatement_only(q, r)
        self.assertFalse(restated)

    def test_empty_response_not_flagged(self):
        q = "Why did you change the retry logic in the client?"
        r = ""
        restated, _overlap, _new_ratio = tc.is_restatement_only(q, r)
        self.assertFalse(restated)

    def test_empty_question_not_flagged(self):
        q = ""
        r = "Some response text here."
        restated, _overlap, _new_ratio = tc.is_restatement_only(q, r)
        self.assertFalse(restated)

    def test_unicode_restatement(self):
        q = "为什么改了重试逻辑 客户端"
        r = q
        restated, _overlap, _new_ratio = tc.is_restatement_only(q, r)
        # Should not crash; unicode tokens are handled by \w with UNICODE flag.
        self.assertIsInstance(restated, bool)


# ==========================================================================
# process_thread -- thread-level MALFORMED_RECORD / EMPTY_THREAD
# ==========================================================================

class TestProcessThreadRecordShape(unittest.TestCase):
    def _run(self, record):
        return tc.process_thread(0, record, NOW, tc.DEFAULT_UNANSWERED_MAX_HOURS)

    def test_record_not_a_dict_string(self):
        findings, summary = self._run("not-a-dict")
        self.assertEqual(codes_of(findings), [tc.CODE_MALFORMED_RECORD])
        self.assertIsNone(summary)
        self.assertEqual(findings[0]["task_id"], "<index:0>")

    def test_record_not_a_dict_list(self):
        findings, summary = self._run(["a", "b"])
        self.assertEqual(codes_of(findings), [tc.CODE_MALFORMED_RECORD])
        self.assertIsNone(summary)

    def test_record_not_a_dict_number(self):
        findings, summary = self._run(42)
        self.assertEqual(codes_of(findings), [tc.CODE_MALFORMED_RECORD])
        self.assertIsNone(summary)

    def test_record_not_a_dict_none(self):
        findings, summary = self._run(None)
        self.assertEqual(codes_of(findings), [tc.CODE_MALFORMED_RECORD])
        self.assertIsNone(summary)

    def test_missing_task_id_key(self):
        findings, summary = self._run({"messages": []})
        self.assertEqual(codes_of(findings), [tc.CODE_MALFORMED_RECORD])
        self.assertIsNone(summary)

    def test_task_id_is_none(self):
        findings, summary = self._run({"task_id": None, "messages": []})
        self.assertEqual(codes_of(findings), [tc.CODE_MALFORMED_RECORD])
        self.assertIsNone(summary)

    def test_task_id_is_integer(self):
        findings, summary = self._run({"task_id": 123, "messages": []})
        self.assertEqual(codes_of(findings), [tc.CODE_MALFORMED_RECORD])
        self.assertIsNone(summary)

    def test_task_id_is_empty_string(self):
        findings, summary = self._run({"task_id": "", "messages": []})
        self.assertEqual(codes_of(findings), [tc.CODE_MALFORMED_RECORD])
        self.assertIsNone(summary)

    def test_missing_messages_key(self):
        findings, summary = self._run({"task_id": "T-1"})
        self.assertEqual(codes_of(findings), [tc.CODE_MALFORMED_RECORD])
        self.assertIsNone(summary)

    def test_messages_not_a_list_string(self):
        findings, summary = self._run({"task_id": "T-1", "messages": "nope"})
        self.assertEqual(codes_of(findings), [tc.CODE_MALFORMED_RECORD])
        self.assertIsNone(summary)

    def test_messages_not_a_list_dict(self):
        findings, summary = self._run({"task_id": "T-1", "messages": {}})
        self.assertEqual(codes_of(findings), [tc.CODE_MALFORMED_RECORD])
        self.assertIsNone(summary)

    def test_valid_task_id_unicode(self):
        findings, summary = self._run({"task_id": "任务-1", "messages": []})
        self.assertEqual(codes_of(findings), [tc.CODE_EMPTY_THREAD])
        self.assertEqual(summary["message_count"], 0)

    def test_malformed_record_finding_has_record_index(self):
        findings, _ = self._run("bad")
        self.assertIn("record_index", findings[0])
        self.assertEqual(findings[0]["record_index"], 0)


class TestProcessThreadEmptyThread(unittest.TestCase):
    def test_zero_messages_is_empty_thread(self):
        findings, summary = tc.process_thread(
            0, mk_thread("T-1", []), NOW, tc.DEFAULT_UNANSWERED_MAX_HOURS
        )
        self.assertEqual(codes_of(findings), [tc.CODE_EMPTY_THREAD])
        self.assertEqual(summary["message_count"], 0)
        self.assertEqual(summary["question_count"], 0)
        self.assertEqual(summary["answered_count"], 0)
        self.assertEqual(summary["unanswered_count"], 0)

    def test_empty_thread_finding_task_id(self):
        findings, _ = tc.process_thread(
            0, mk_thread("T-EMPTY", []), NOW, tc.DEFAULT_UNANSWERED_MAX_HOURS
        )
        self.assertEqual(findings[0]["task_id"], "T-EMPTY")


# ==========================================================================
# process_thread -- per-message MALFORMED_RECORD / INVALID_TIMESTAMP
# ==========================================================================

class TestProcessThreadMessageShape(unittest.TestCase):
    def _run(self, messages):
        return tc.process_thread(
            0, mk_thread("T-1", messages), NOW, tc.DEFAULT_UNANSWERED_MAX_HOURS
        )

    def test_message_not_a_dict(self):
        findings, summary = self._run(["not-a-dict"])
        self.assertEqual(codes_of(findings), [tc.CODE_MALFORMED_RECORD])
        self.assertEqual(summary["message_count"], 1)

    def test_message_missing_message_id(self):
        findings, _ = self._run([{"role": "reviewer", "at": NOW_ISO, "text": "Why?"}])
        self.assertEqual(codes_of(findings), [tc.CODE_MALFORMED_RECORD])

    def test_message_id_empty_string(self):
        findings, _ = self._run([msg("", "reviewer", NOW_ISO, "Why?")])
        self.assertEqual(codes_of(findings), [tc.CODE_MALFORMED_RECORD])

    def test_message_id_non_string(self):
        findings, _ = self._run([msg(123, "reviewer", NOW_ISO, "Why?")])
        self.assertEqual(codes_of(findings), [tc.CODE_MALFORMED_RECORD])

    def test_message_missing_role(self):
        findings, _ = self._run([{"message_id": "m1", "at": NOW_ISO, "text": "Why?"}])
        self.assertEqual(codes_of(findings), [tc.CODE_MALFORMED_RECORD])

    def test_message_role_invalid_value(self):
        findings, _ = self._run([msg("m1", "admin", NOW_ISO, "Why?")])
        self.assertEqual(codes_of(findings), [tc.CODE_MALFORMED_RECORD])

    def test_message_role_case_sensitive(self):
        findings, _ = self._run([msg("m1", "Reviewer", NOW_ISO, "Why?")])
        self.assertEqual(codes_of(findings), [tc.CODE_MALFORMED_RECORD])

    def test_message_missing_text(self):
        findings, _ = self._run([{"message_id": "m1", "role": "reviewer", "at": NOW_ISO}])
        self.assertEqual(codes_of(findings), [tc.CODE_MALFORMED_RECORD])

    def test_message_text_non_string(self):
        findings, _ = self._run([msg("m1", "reviewer", NOW_ISO, 12345)])
        self.assertEqual(codes_of(findings), [tc.CODE_MALFORMED_RECORD])

    def test_message_text_empty_string_is_legal(self):
        findings, summary = self._run([msg("m1", "reviewer", NOW_ISO, "")])
        self.assertEqual(codes_of(findings), [])
        self.assertEqual(summary["message_count"], 1)
        self.assertEqual(summary["question_count"], 0)

    def test_message_missing_at(self):
        findings, _ = self._run([{"message_id": "m1", "role": "reviewer", "text": "Why?"}])
        self.assertEqual(codes_of(findings), [tc.CODE_MALFORMED_RECORD])

    def test_message_at_non_string(self):
        findings, _ = self._run([msg("m1", "reviewer", 12345, "Why?")])
        self.assertEqual(codes_of(findings), [tc.CODE_MALFORMED_RECORD])

    def test_message_at_unparseable_string(self):
        findings, _ = self._run([msg("m1", "reviewer", "not-a-date", "Why?")])
        self.assertEqual(codes_of(findings), [tc.CODE_INVALID_TIMESTAMP])

    def test_message_at_non_utc_offset(self):
        findings, _ = self._run([msg("m1", "reviewer", "2026-08-02T00:00:00+05:30", "Why?")])
        self.assertEqual(codes_of(findings), [tc.CODE_INVALID_TIMESTAMP])

    def test_invalid_timestamp_finding_has_at_raw(self):
        findings, _ = self._run([msg("m1", "reviewer", "garbage", "Why?")])
        self.assertEqual(findings[0]["at_raw"], "garbage")

    def test_in_reply_to_non_string(self):
        findings, _ = self._run(
            [msg("m1", "contributor", NOW_ISO, "text", in_reply_to=123)]
        )
        self.assertIn(tc.CODE_MALFORMED_RECORD, codes_of(findings))

    def test_in_reply_to_empty_string(self):
        findings, _ = self._run(
            [msg("m1", "contributor", NOW_ISO, "text", in_reply_to="")]
        )
        self.assertIn(tc.CODE_MALFORMED_RECORD, codes_of(findings))

    def test_in_reply_to_explicit_null_is_legal(self):
        m = msg("m1", "contributor", NOW_ISO, "text")
        m["in_reply_to"] = None
        findings, _ = self._run([m])
        self.assertEqual(findings, [])

    def test_malformed_message_finding_has_message_index(self):
        findings, _ = self._run(["not-a-dict"])
        self.assertEqual(findings[0]["message_index"], 0)

    def test_multiple_independent_field_errors_both_reported(self):
        # role AND text both bad on the same message -> two findings.
        findings, _ = self._run([{"message_id": "m1", "role": "bogus", "at": NOW_ISO, "text": 5}])
        self.assertEqual(codes_of(findings), [tc.CODE_MALFORMED_RECORD, tc.CODE_MALFORMED_RECORD])

    def test_message_id_valid_but_role_bad_still_known_for_dangling_reply(self):
        # A message with a valid message_id but an invalid role is still
        # "identifiable" -- its id counts for DANGLING_REPLY purposes even
        # though the message itself is otherwise unusable.
        messages = [
            msg("m1", "bogus-role", NOW_ISO, "some text"),
            msg("m2", "contributor", seconds_ago(1), "reply", in_reply_to="m1"),
        ]
        findings, _ = self._run(messages)
        self.assertNotIn(tc.CODE_DANGLING_REPLY, codes_of(findings))


# ==========================================================================
# process_thread -- DANGLING_REPLY
# ==========================================================================

class TestProcessThreadDanglingReply(unittest.TestCase):
    def _run(self, messages):
        return tc.process_thread(
            0, mk_thread("T-1", messages), NOW, tc.DEFAULT_UNANSWERED_MAX_HOURS
        )

    def test_dangling_reply_to_nonexistent_id(self):
        messages = [
            msg("m1", "reviewer", hours_ago(2), "Why did this fail?"),
            msg("m2", "contributor", hours_ago(1), "Because of X.", in_reply_to="m-does-not-exist"),
        ]
        findings, _ = self._run(messages)
        self.assertIn(tc.CODE_DANGLING_REPLY, codes_of(findings))
        d = only(findings, tc.CODE_DANGLING_REPLY)[0]
        self.assertEqual(d["in_reply_to"], "m-does-not-exist")
        self.assertEqual(d["message_id"], "m2")

    def test_reply_to_existing_id_not_dangling(self):
        messages = [
            msg("m1", "reviewer", hours_ago(2), "Why did this fail?"),
            msg("m2", "contributor", hours_ago(1), "Because of X, see abcdef1.", in_reply_to="m1"),
        ]
        findings, _ = self._run(messages)
        self.assertNotIn(tc.CODE_DANGLING_REPLY, codes_of(findings))

    def test_self_reply_is_not_dangling(self):
        messages = [msg("m1", "contributor", NOW_ISO, "note", in_reply_to="m1")]
        findings, _ = self._run(messages)
        self.assertNotIn(tc.CODE_DANGLING_REPLY, codes_of(findings))

    def test_reply_to_id_that_only_exists_on_malformed_message(self):
        # m1's own message_id is valid even though its role is bad; m2's
        # reply to it should NOT be dangling.
        messages = [
            {"message_id": "m1", "role": "bogus", "at": NOW_ISO, "text": "x"},
            msg("m2", "contributor", seconds_ago(1), "y", in_reply_to="m1"),
        ]
        findings, _ = self._run(messages)
        self.assertNotIn(tc.CODE_DANGLING_REPLY, codes_of(findings))

    def test_dangling_reply_from_reviewer_message(self):
        messages = [msg("m1", "reviewer", NOW_ISO, "follow-up", in_reply_to="ghost")]
        findings, _ = self._run(messages)
        self.assertIn(tc.CODE_DANGLING_REPLY, codes_of(findings))


# ==========================================================================
# process_thread -- OUT_OF_ORDER_MESSAGE
# ==========================================================================

class TestProcessThreadOutOfOrder(unittest.TestCase):
    def _run(self, messages):
        return tc.process_thread(
            0, mk_thread("T-1", messages), NOW, tc.DEFAULT_UNANSWERED_MAX_HOURS
        )

    def test_second_message_earlier_than_first_is_flagged(self):
        messages = [
            msg("m1", "reviewer", hours_ago(1), "text one"),
            msg("m2", "contributor", hours_ago(2), "text two"),
        ]
        findings, _ = self._run(messages)
        self.assertIn(tc.CODE_OUT_OF_ORDER_MESSAGE, codes_of(findings))
        o = only(findings, tc.CODE_OUT_OF_ORDER_MESSAGE)[0]
        self.assertEqual(o["message_id"], "m2")
        self.assertEqual(o["conflicts_with_message_id"], "m1")

    def test_monotonic_order_not_flagged(self):
        messages = [
            msg("m1", "reviewer", hours_ago(3), "text one"),
            msg("m2", "contributor", hours_ago(2), "text two"),
            msg("m3", "reviewer", hours_ago(1), "text three"),
        ]
        findings, _ = self._run(messages)
        self.assertNotIn(tc.CODE_OUT_OF_ORDER_MESSAGE, codes_of(findings))

    def test_identical_timestamps_not_flagged(self):
        same = hours_ago(1)
        messages = [
            msg("m1", "reviewer", same, "text one"),
            msg("m2", "contributor", same, "text two"),
        ]
        findings, _ = self._run(messages)
        self.assertNotIn(tc.CODE_OUT_OF_ORDER_MESSAGE, codes_of(findings))

    def test_watermark_tracks_true_max_not_last_flagged_value(self):
        # m3 is earlier than m1 (flagged); m4 is between m2 and m1 but
        # later than m3 -- must compare against the running max (m1), not
        # against the just-flagged m3.
        messages = [
            msg("m1", "reviewer", hours_ago(1), "one"),
            msg("m2", "contributor", hours_ago(4), "two"),
            msg("m3", "reviewer", hours_ago(3), "three"),
        ]
        findings, _ = self._run(messages)
        out_of_order = only(findings, tc.CODE_OUT_OF_ORDER_MESSAGE)
        self.assertEqual({o["message_id"] for o in out_of_order}, {"m2", "m3"})
        self.assertEqual(only(findings, tc.CODE_OUT_OF_ORDER_MESSAGE)[0]["conflicts_with_message_id"], "m1")

    def test_malformed_and_invalid_timestamp_messages_excluded_from_check(self):
        messages = [
            msg("m1", "reviewer", hours_ago(1), "text one"),
            msg("m2", "contributor", "garbage-timestamp", "text two"),
            msg("m3", "reviewer", hours_ago(2), "text three"),
        ]
        findings, _ = self._run(messages)
        self.assertIn(tc.CODE_OUT_OF_ORDER_MESSAGE, codes_of(findings))
        self.assertEqual(len(only(findings, tc.CODE_OUT_OF_ORDER_MESSAGE)), 1)

    def test_out_of_order_finding_has_at_field(self):
        messages = [
            msg("m1", "reviewer", hours_ago(1), "text one"),
            msg("m2", "contributor", hours_ago(2), "text two"),
        ]
        findings, _ = self._run(messages)
        o = only(findings, tc.CODE_OUT_OF_ORDER_MESSAGE)[0]
        self.assertEqual(o["at"], hours_ago(2))


# ==========================================================================
# process_thread -- UNANSWERED_QUESTION / UNANSWERED_OVERDUE
# ==========================================================================

class TestProcessThreadUnanswered(unittest.TestCase):
    def _run(self, messages, unanswered_max_hours=tc.DEFAULT_UNANSWERED_MAX_HOURS):
        return tc.process_thread(0, mk_thread("T-1", messages), NOW, unanswered_max_hours)

    def test_reviewer_question_no_response_is_unanswered(self):
        messages = [msg("m1", "reviewer", hours_ago(1), "Why did this fail?")]
        findings, summary = self._run(messages)
        self.assertEqual(codes_of(findings), [tc.CODE_UNANSWERED_QUESTION])
        self.assertEqual(summary["unanswered_count"], 1)
        self.assertEqual(summary["question_count"], 1)

    def test_reviewer_non_question_no_finding(self):
        messages = [msg("m1", "reviewer", hours_ago(1), "Looks fine to me.")]
        findings, summary = self._run(messages)
        self.assertEqual(findings, [])
        self.assertEqual(summary["question_count"], 0)

    def test_answered_question_no_unanswered_finding(self):
        messages = [
            msg("m1", "reviewer", hours_ago(2), "Why did this fail?"),
            msg("m2", "contributor", hours_ago(1), "It failed because of a timeout; see `client.py`."),
        ]
        findings, summary = self._run(messages)
        self.assertNotIn(tc.CODE_UNANSWERED_QUESTION, codes_of(findings))
        self.assertEqual(summary["answered_count"], 1)
        self.assertEqual(summary["unanswered_count"], 0)

    def test_unanswered_beyond_threshold_is_overdue(self):
        messages = [msg("m1", "reviewer", hours_ago(100), "Why did this fail?")]
        findings, _ = self._run(messages, unanswered_max_hours=48)
        self.assertEqual(set(codes_of(findings)), {tc.CODE_UNANSWERED_QUESTION, tc.CODE_UNANSWERED_OVERDUE})

    def test_unanswered_within_threshold_not_overdue(self):
        messages = [msg("m1", "reviewer", hours_ago(1), "Why did this fail?")]
        findings, _ = self._run(messages, unanswered_max_hours=48)
        self.assertEqual(codes_of(findings), [tc.CODE_UNANSWERED_QUESTION])

    def test_unanswered_exactly_at_threshold_not_overdue(self):
        messages = [msg("m1", "reviewer", hours_ago(48), "Why did this fail?")]
        findings, _ = self._run(messages, unanswered_max_hours=48)
        self.assertEqual(codes_of(findings), [tc.CODE_UNANSWERED_QUESTION])

    def test_unanswered_one_second_past_threshold_is_overdue(self):
        messages = [msg("m1", "reviewer", seconds_ago(48 * 3600 + 1), "Why did this fail?")]
        findings, _ = self._run(messages, unanswered_max_hours=48)
        self.assertIn(tc.CODE_UNANSWERED_OVERDUE, codes_of(findings))

    def test_overdue_finding_has_age_fields(self):
        messages = [msg("m1", "reviewer", hours_ago(100), "Why did this fail?")]
        findings, _ = self._run(messages, unanswered_max_hours=48)
        o = only(findings, tc.CODE_UNANSWERED_OVERDUE)[0]
        self.assertEqual(o["age_seconds"], 100 * 3600)
        self.assertEqual(o["age_human"], "4d 4h 0m")
        self.assertEqual(o["unanswered_max_hours"], 48)

    def test_contributor_message_before_any_reviewer_message_ignored(self):
        messages = [
            msg("m1", "contributor", hours_ago(2), "Starting work now."),
            msg("m2", "reviewer", hours_ago(1), "Why is this taking so long?"),
        ]
        findings, summary = self._run(messages)
        self.assertEqual(codes_of(findings), [tc.CODE_UNANSWERED_QUESTION])
        self.assertEqual(summary["answered_count"], 0)

    def test_thread_with_only_reviewer_messages_all_unanswered(self):
        messages = [
            msg("m1", "reviewer", hours_ago(3), "Why did this fail?"),
            msg("m2", "reviewer", hours_ago(2), "What is the timeline?"),
            msg("m3", "reviewer", hours_ago(1), "How will you fix it?"),
        ]
        findings, summary = self._run(messages)
        self.assertEqual(codes_of(findings).count(tc.CODE_UNANSWERED_QUESTION), 3)
        self.assertEqual(summary["unanswered_count"], 3)

    def test_implicit_reply_binds_to_nearest_open_question(self):
        messages = [
            msg("m1", "reviewer", hours_ago(3), "Why did this fail?"),
            msg("m2", "reviewer", hours_ago(2), "What is the timeline?"),
            msg("m3", "contributor", hours_ago(1), "It will ship Friday, see abcdef1."),
        ]
        findings, summary = self._run(messages)
        # m3 (no in_reply_to) binds to the nearest open question (m2), so
        # m1 remains unanswered and m2 is resolved.
        self.assertEqual(summary["answered_count"], 1)
        self.assertEqual(summary["unanswered_count"], 1)
        unanswered = only(findings, tc.CODE_UNANSWERED_QUESTION)
        self.assertEqual(unanswered[0]["message_id"], "m1")

    def test_explicit_reply_can_resolve_older_open_question(self):
        messages = [
            msg("m1", "reviewer", hours_ago(3), "Why did this fail?"),
            msg("m2", "reviewer", hours_ago(2), "What is the timeline?"),
            msg("m3", "contributor", hours_ago(1), "It failed due to a timeout, see abcdef1.", in_reply_to="m1"),
        ]
        findings, summary = self._run(messages)
        self.assertEqual(summary["answered_count"], 1)
        self.assertEqual(summary["unanswered_count"], 1)
        unanswered = only(findings, tc.CODE_UNANSWERED_QUESTION)
        self.assertEqual(unanswered[0]["message_id"], "m2")

    def test_explicit_reply_to_non_open_target_does_not_fall_back_implicit(self):
        # m3 explicitly replies to m-nonexistent (dangling); it must NOT
        # silently fall back to resolving the open question m1.
        messages = [
            msg("m1", "reviewer", hours_ago(2), "Why did this fail?"),
            msg("m2", "contributor", hours_ago(1), "Unrelated status update.", in_reply_to="m-nonexistent"),
        ]
        findings, summary = self._run(messages)
        self.assertEqual(summary["answered_count"], 0)
        self.assertIn(tc.CODE_UNANSWERED_QUESTION, codes_of(findings))
        self.assertIn(tc.CODE_DANGLING_REPLY, codes_of(findings))

    def test_reply_to_already_answered_question_does_not_reopen(self):
        messages = [
            msg("m1", "reviewer", hours_ago(3), "Why did this fail?"),
            msg("m2", "contributor", hours_ago(2), "Timeout, see abcdef1.", in_reply_to="m1"),
            msg("m3", "contributor", hours_ago(1), "Follow-up note.", in_reply_to="m1"),
        ]
        findings, summary = self._run(messages)
        self.assertEqual(summary["answered_count"], 1)
        self.assertEqual(summary["unanswered_count"], 0)

    def test_self_reply_contributor_binds_to_nothing(self):
        messages = [msg("m1", "contributor", NOW_ISO, "note", in_reply_to="m1")]
        findings, summary = self._run(messages)
        self.assertEqual(summary["answered_count"], 0)
        self.assertEqual(findings, [])

    def test_reviewer_message_with_no_question_mark_but_interrogative_is_detected(self):
        messages = [msg("m1", "reviewer", hours_ago(1), "Please clarify how the retry cap was chosen")]
        findings, summary = self._run(messages)
        self.assertEqual(summary["question_count"], 1)
        self.assertIn(tc.CODE_UNANSWERED_QUESTION, codes_of(findings))

    def test_two_questions_in_one_message_counts_as_one_question(self):
        messages = [msg("m1", "reviewer", hours_ago(1), "What changed? Why did it change?")]
        findings, summary = self._run(messages)
        self.assertEqual(summary["question_count"], 1)
        self.assertEqual(len(only(findings, tc.CODE_UNANSWERED_QUESTION)), 1)

    def test_identical_timestamp_question_and_response_resolved_by_index(self):
        same = hours_ago(1)
        messages = [
            msg("m1", "reviewer", same, "Why did this fail?"),
            msg("m2", "contributor", same, "Timeout, see abcdef1."),
        ]
        findings, summary = self._run(messages)
        # m2 appears later in the array, so the tiebreak places it after
        # m1 chronologically -- it resolves the question.
        self.assertEqual(summary["answered_count"], 1)

    def test_unicode_question_and_response(self):
        messages = [
            msg("m1", "reviewer", hours_ago(2), "为什么这个失败了？"),
            msg("m2", "contributor", hours_ago(1), "因为超时了，见 abcdef1。"),
        ]
        findings, summary = self._run(messages)
        self.assertEqual(summary["answered_count"], 1)
        self.assertNotIn(tc.CODE_UNANSWERED_QUESTION, codes_of(findings))

    def test_empty_text_reviewer_message_is_not_a_question(self):
        messages = [msg("m1", "reviewer", hours_ago(1), "")]
        findings, summary = self._run(messages)
        self.assertEqual(findings, [])
        self.assertEqual(summary["question_count"], 0)


# ==========================================================================
# process_thread -- NO_ARTIFACT_REFERENCE / RESTATEMENT_ONLY (integration)
# ==========================================================================

class TestProcessThreadNoArtifactReference(unittest.TestCase):
    def _run(self, messages):
        return tc.process_thread(0, mk_thread("T-1", messages), NOW, tc.DEFAULT_UNANSWERED_MAX_HOURS)

    def test_response_without_artifact_flagged(self):
        messages = [
            msg("m1", "reviewer", hours_ago(2), "Why did this fail?"),
            msg("m2", "contributor", hours_ago(1), "I verified this works and it's fine now."),
        ]
        findings, _ = self._run(messages)
        self.assertIn(tc.CODE_NO_ARTIFACT_REFERENCE, codes_of(findings))
        f = only(findings, tc.CODE_NO_ARTIFACT_REFERENCE)[0]
        self.assertEqual(f["in_response_to"], "m1")
        self.assertEqual(f["message_id"], "m2")

    def test_response_with_artifact_not_flagged(self):
        messages = [
            msg("m1", "reviewer", hours_ago(2), "Why did this fail?"),
            msg("m2", "contributor", hours_ago(1), "Fixed in `retry_client.py`, see commit abcdef1."),
        ]
        findings, _ = self._run(messages)
        self.assertNotIn(tc.CODE_NO_ARTIFACT_REFERENCE, codes_of(findings))

    def test_unmatched_contributor_message_not_evaluated(self):
        # No open question -- a generic status update should not be
        # evaluated for artifact references at all.
        messages = [msg("m1", "contributor", hours_ago(1), "I verified this works.")]
        findings, _ = self._run(messages)
        self.assertEqual(findings, [])


class TestProcessThreadRestatementOnlyIntegration(unittest.TestCase):
    def _run(self, messages):
        return tc.process_thread(0, mk_thread("T-1", messages), NOW, tc.DEFAULT_UNANSWERED_MAX_HOURS)

    def test_restatement_only_flagged_in_thread(self):
        q = "Why did you change the retry logic in the client implementation?"
        messages = [
            msg("m1", "reviewer", hours_ago(2), q),
            msg("m2", "contributor", hours_ago(1), q),
        ]
        findings, _ = self._run(messages)
        self.assertIn(tc.CODE_RESTATEMENT_ONLY, codes_of(findings))
        self.assertIn(tc.CODE_NO_ARTIFACT_REFERENCE, codes_of(findings))

    def test_restatement_only_implies_no_artifact_reference_too(self):
        q = "Why did you change the retry logic in the client implementation?"
        messages = [
            msg("m1", "reviewer", hours_ago(2), q),
            msg("m2", "contributor", hours_ago(1), q),
        ]
        findings, _ = self._run(messages)
        codes = codes_of(findings)
        self.assertIn(tc.CODE_RESTATEMENT_ONLY, codes)
        self.assertIn(tc.CODE_NO_ARTIFACT_REFERENCE, codes)

    def test_restatement_with_artifact_only_no_artifact_absent(self):
        q = "Why did you change the retry logic in the client implementation?"
        messages = [
            msg("m1", "reviewer", hours_ago(2), q),
            msg("m2", "contributor", hours_ago(1), q + " See commit abcdef1."),
        ]
        findings, _ = self._run(messages)
        self.assertNotIn(tc.CODE_RESTATEMENT_ONLY, codes_of(findings))
        self.assertNotIn(tc.CODE_NO_ARTIFACT_REFERENCE, codes_of(findings))

    def test_genuine_answer_with_artifact_flags_neither(self):
        messages = [
            msg("m1", "reviewer", hours_ago(2), "Why did this fail?"),
            msg("m2", "contributor", hours_ago(1), "Timeout in `client.py`; see commit abcdef1."),
        ]
        findings, _ = self._run(messages)
        self.assertNotIn(tc.CODE_RESTATEMENT_ONLY, codes_of(findings))
        self.assertNotIn(tc.CODE_NO_ARTIFACT_REFERENCE, codes_of(findings))

    def test_restatement_finding_carries_ratios(self):
        q = "Why did you change the retry logic in the client implementation?"
        messages = [
            msg("m1", "reviewer", hours_ago(2), q),
            msg("m2", "contributor", hours_ago(1), q),
        ]
        findings, _ = self._run(messages)
        r = only(findings, tc.CODE_RESTATEMENT_ONLY)[0]
        self.assertEqual(r["overlap_ratio"], 1.0)
        self.assertEqual(r["new_token_ratio"], 0.0)
        self.assertEqual(r["in_response_to"], "m1")


# ==========================================================================
# build_report -- aggregation, sorting, InputError
# ==========================================================================

class TestBuildReport(unittest.TestCase):
    def test_root_not_a_list_raises_input_error(self):
        with self.assertRaises(tc.InputError):
            tc.build_report({"task_id": "x"}, NOW, tc.DEFAULT_UNANSWERED_MAX_HOURS)

    def test_root_string_raises_input_error(self):
        with self.assertRaises(tc.InputError):
            tc.build_report("nope", NOW, tc.DEFAULT_UNANSWERED_MAX_HOURS)

    def test_empty_array_zero_findings(self):
        report, total = tc.build_report([], NOW, tc.DEFAULT_UNANSWERED_MAX_HOURS)
        self.assertEqual(total, 0)
        self.assertEqual(report["summary"]["total_threads"], 0)
        self.assertEqual(report["findings"], [])
        self.assertEqual(report["thread_summaries"], [])

    def test_generated_at_echoes_now(self):
        report, _ = tc.build_report([], NOW, tc.DEFAULT_UNANSWERED_MAX_HOURS)
        self.assertEqual(report["generated_at"], NOW_ISO)

    def test_options_echoes_unanswered_max_hours(self):
        report, _ = tc.build_report([], NOW, 24)
        self.assertEqual(report["options"]["unanswered_max_hours"], 24)

    def test_counts_by_code_has_all_nine_codes_even_when_zero(self):
        report, _ = tc.build_report([], NOW, tc.DEFAULT_UNANSWERED_MAX_HOURS)
        self.assertEqual(set(report["summary"]["counts_by_code"].keys()), set(tc.ALL_CODES))
        self.assertTrue(all(v == 0 for v in report["summary"]["counts_by_code"].values()))

    def test_findings_sorted_by_task_id_then_code(self):
        data = [
            mk_thread("T-B", [msg("m1", "reviewer", hours_ago(1), "Why?")]),
            mk_thread("T-A", [msg("m2", "reviewer", hours_ago(1), "Why?")]),
        ]
        report, _ = tc.build_report(data, NOW, tc.DEFAULT_UNANSWERED_MAX_HOURS)
        task_ids = [f["task_id"] for f in report["findings"]]
        self.assertEqual(task_ids, sorted(task_ids))

    def test_thread_summaries_sorted_by_task_id_then_record_index(self):
        data = [
            mk_thread("T-Z", []),
            mk_thread("T-A", []),
            mk_thread("T-A", []),
        ]
        report, _ = tc.build_report(data, NOW, tc.DEFAULT_UNANSWERED_MAX_HOURS)
        ids = [s["task_id"] for s in report["thread_summaries"]]
        self.assertEqual(ids, ["T-A", "T-A", "T-Z"])

    def test_malformed_record_contributes_no_thread_summary(self):
        data = ["not-a-dict", mk_thread("T-1", [])]
        report, _ = tc.build_report(data, NOW, tc.DEFAULT_UNANSWERED_MAX_HOURS)
        self.assertEqual(len(report["thread_summaries"]), 1)
        self.assertEqual(report["thread_summaries"][0]["task_id"], "T-1")

    def test_total_threads_counts_all_records_including_malformed(self):
        data = ["not-a-dict", mk_thread("T-1", [])]
        report, _ = tc.build_report(data, NOW, tc.DEFAULT_UNANSWERED_MAX_HOURS)
        self.assertEqual(report["summary"]["total_threads"], 2)

    def test_total_findings_matches_len_findings(self):
        data = [mk_thread("T-1", [msg("m1", "reviewer", hours_ago(1), "Why?")])]
        report, total = tc.build_report(data, NOW, tc.DEFAULT_UNANSWERED_MAX_HOURS)
        self.assertEqual(total, len(report["findings"]))

    def test_deterministic_finding_order_across_two_calls(self):
        data = [
            mk_thread("T-1", [
                msg("m1", "reviewer", hours_ago(3), "Why did this fail?"),
                msg("m2", "reviewer", hours_ago(2), "What about this too?"),
            ]),
        ]
        r1, _ = tc.build_report(data, NOW, tc.DEFAULT_UNANSWERED_MAX_HOURS)
        r2, _ = tc.build_report(data, NOW, tc.DEFAULT_UNANSWERED_MAX_HOURS)
        self.assertEqual(tc.canonical_json(r1), tc.canonical_json(r2))

    def test_top_level_keys(self):
        report, _ = tc.build_report([], NOW, tc.DEFAULT_UNANSWERED_MAX_HOURS)
        self.assertEqual(
            set(report.keys()),
            {"generated_at", "options", "summary", "thread_summaries", "findings"},
        )

    def test_report_is_json_serializable(self):
        data = [mk_thread("T-1", [msg("m1", "reviewer", hours_ago(1), "Why?")])]
        report, _ = tc.build_report(data, NOW, tc.DEFAULT_UNANSWERED_MAX_HOURS)
        json.dumps(report)  # must not raise


# ==========================================================================
# canonical_json
# ==========================================================================

class TestCanonicalJson(unittest.TestCase):
    def test_ends_with_single_trailing_newline(self):
        out = tc.canonical_json({"a": 1})
        self.assertTrue(out.endswith("\n"))
        self.assertFalse(out.endswith("\n\n"))

    def test_keys_sorted(self):
        out = tc.canonical_json({"b": 1, "a": 2})
        self.assertTrue(out.index('"a"') < out.index('"b"'))

    def test_compact_separators(self):
        out = tc.canonical_json({"a": 1, "b": [1, 2]})
        self.assertNotIn(", ", out)
        self.assertNotIn(": ", out)

    def test_ascii_only(self):
        out = tc.canonical_json({"a": "任务"})
        self.assertTrue(all(ord(c) < 128 for c in out))
        self.assertIn("\\u", out)

    def test_roundtrips(self):
        obj = {"a": 1, "b": [1, 2, {"c": "d"}]}
        out = tc.canonical_json(obj)
        self.assertEqual(json.loads(out), obj)


# ==========================================================================
# No-wall-clock-read source scan
# ==========================================================================

class TestNoWallClockRead(unittest.TestCase):
    def test_source_has_no_forbidden_wall_clock_calls(self):
        with open(SCRIPT, "r", encoding="utf-8") as fh:
            source = fh.read()
        forbidden = [
            "now" + "()",
            "utc" + "now",
            "time" + "." + "time",
        ]
        for token in forbidden:
            self.assertNotIn(token, source, f"forbidden wall-clock token found: {token!r}")


# ==========================================================================
# CLI (subprocess) tests
# ==========================================================================

class TestCLI(unittest.TestCase):
    def test_missing_now_is_usage_error(self):
        result = run_cli([COMPLETE_FIXTURE])
        self.assertEqual(result.returncode, 2)

    def test_unparseable_now_is_usage_error(self):
        result = run_cli([COMPLETE_FIXTURE, "--now", "not-a-date"])
        self.assertEqual(result.returncode, 2)

    def test_nonexistent_file_is_usage_error(self):
        result = run_cli(["/nonexistent.json", "--now", NOW_ISO])
        self.assertEqual(result.returncode, 2)

    def test_not_json_is_usage_error(self):
        result = run_cli(["not_json.txt", "--now", NOW_ISO])
        self.assertEqual(result.returncode, 2)

    def test_root_object_not_array_is_usage_error(self):
        result = run_cli(["object_not_array.json", "--now", NOW_ISO])
        self.assertEqual(result.returncode, 2)

    def test_empty_array_is_valid_and_clean(self):
        result = run_cli(["empty_array.json", "--now", NOW_ISO])
        self.assertEqual(result.returncode, 0)
        report = json.loads(result.stdout)
        self.assertEqual(report["summary"]["total_findings"], 0)

    def test_negative_unanswered_max_hours_is_usage_error(self):
        result = run_cli(["empty_array.json", "--now", NOW_ISO, "--unanswered-max-hours", "-1"])
        self.assertEqual(result.returncode, 2)

    def test_complete_fixture_exits_zero(self):
        result = run_cli([COMPLETE_FIXTURE, "--now", NOW_ISO])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["summary"]["total_findings"], 0)

    def test_incomplete_fixture_exits_one(self):
        result = run_cli([INCOMPLETE_FIXTURE, "--now", NOW_ISO])
        self.assertEqual(result.returncode, 1)
        report = json.loads(result.stdout)
        self.assertGreater(report["summary"]["total_findings"], 0)

    def test_incomplete_fixture_triggers_all_nine_codes(self):
        result = run_cli([INCOMPLETE_FIXTURE, "--now", NOW_ISO])
        report = json.loads(result.stdout)
        counts = report["summary"]["counts_by_code"]
        for code in tc.ALL_CODES:
            self.assertGreater(counts[code], 0, f"expected {code} to be triggered")

    def test_output_flag_writes_file(self):
        out_path = os.path.join(HERE, "_test_cli_output.json")
        try:
            result = run_cli([COMPLETE_FIXTURE, "--now", NOW_ISO, "-o", out_path])
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout, "")
            with open(out_path, "r", encoding="utf-8") as fh:
                report = json.load(fh)
            self.assertEqual(report["summary"]["total_findings"], 0)
        finally:
            if os.path.exists(out_path):
                os.remove(out_path)

    def test_two_runs_byte_identical(self):
        out1 = os.path.join(HERE, "_test_cli_r1.json")
        out2 = os.path.join(HERE, "_test_cli_r2.json")
        try:
            run_cli([INCOMPLETE_FIXTURE, "--now", NOW_ISO, "-o", out1])
            run_cli([INCOMPLETE_FIXTURE, "--now", NOW_ISO, "-o", out2])
            with open(out1, "rb") as fh:
                b1 = fh.read()
            with open(out2, "rb") as fh:
                b2 = fh.read()
            self.assertEqual(b1, b2)
        finally:
            for p in (out1, out2):
                if os.path.exists(p):
                    os.remove(p)

    def test_unanswered_max_hours_flag_changes_verdict(self):
        low = run_cli([INCOMPLETE_FIXTURE, "--now", NOW_ISO, "--unanswered-max-hours", "0"])
        high = run_cli([INCOMPLETE_FIXTURE, "--now", NOW_ISO, "--unanswered-max-hours", "99999"])
        low_report = json.loads(low.stdout)
        high_report = json.loads(high.stdout)
        low_count = low_report["summary"]["counts_by_code"][tc.CODE_UNANSWERED_OVERDUE]
        high_count = high_report["summary"]["counts_by_code"][tc.CODE_UNANSWERED_OVERDUE]
        self.assertGreater(low_count, high_count)
        self.assertEqual(high_count, 0)

    def test_help_exits_zero(self):
        result = run_cli(["--help"])
        self.assertEqual(result.returncode, 0)

    def test_stdout_used_when_no_output_flag(self):
        result = run_cli([COMPLETE_FIXTURE, "--now", NOW_ISO])
        self.assertTrue(result.stdout.strip().startswith("{"))

    def test_now_accepts_offset_form(self):
        result = run_cli([COMPLETE_FIXTURE, "--now", "2026-08-03T00:00:00+00:00"])
        self.assertEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
