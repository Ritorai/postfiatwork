#!/usr/bin/env python3
"""Automated tests for the Evidence Integrity Validator."""
import json
import os
import subprocess
import sys
import tempfile
import unittest

import validator

HERE = os.path.dirname(os.path.abspath(__file__))
VALID_CID = "QmYvH6Y2VpnFUops1WaVY9fCKy1c6u6BFdDJfEwpffgLuE"
VALID_CID_2 = "QmcXDn4mMwz7m1fZY4RmZiQrX37phk56nPPBiFYFFZDLDv"
VALID_TX = "9F2BE303A1C4D5E6F708192A3B4C5D6E7F8091A2B3C4D5E6F708192A3B4C5D6E"
VALID_TASK = "task_b3a3e54adcac730636afd7e9ca80b798"


def rec(**over):
    base = {
        "submission_id": "sub_0001",
        "task_id": VALID_TASK,
        "wallet": "rJ8St5nxoH4DvX",
        "cid": VALID_CID,
        "tx_hash": VALID_TX,
    }
    base.update(over)
    return base


class TestValidRecords(unittest.TestCase):
    def test_clean_record_has_no_issues(self):
        summary, clean = validator.validate_records([rec()])
        self.assertTrue(clean)
        self.assertEqual(summary["totals"], {"records": 1, "clean": 1, "rejected": 0})
        self.assertEqual(summary["records"][0]["issues"], [])

    def test_cidv1_accepted(self):
        cid1 = "bafy" + "a" * 50
        summary, clean = validator.validate_records([rec(cid=cid1)])
        self.assertTrue(clean, summary["records"][0]["issues"])


class TestMissingFields(unittest.TestCase):
    def test_missing_field_flagged(self):
        r = rec()
        del r["tx_hash"]
        summary, clean = validator.validate_records([r])
        self.assertFalse(clean)
        self.assertIn("MISSING_FIELD:tx_hash", summary["records"][0]["issues"])

    def test_empty_field_flagged(self):
        summary, clean = validator.validate_records([rec(wallet="   ")])
        self.assertFalse(clean)
        self.assertIn("EMPTY_FIELD:wallet", summary["records"][0]["issues"])

    def test_non_object_record_flagged(self):
        summary, clean = validator.validate_records(["not-an-object"])
        self.assertFalse(clean)
        self.assertEqual(summary["records"][0]["issues"], ["RECORD_NOT_OBJECT"])


class TestMalformedReferences(unittest.TestCase):
    def test_bad_cid_flagged(self):
        summary, _ = validator.validate_records([rec(cid="QmTooShort")])
        self.assertIn("MALFORMED_CID", summary["records"][0]["issues"])

    def test_bad_tx_hash_lowercase_flagged(self):
        summary, _ = validator.validate_records([rec(tx_hash=VALID_TX.lower())])
        self.assertIn("MALFORMED_TX_HASH", summary["records"][0]["issues"])

    def test_bad_tx_hash_wrong_length_flagged(self):
        summary, _ = validator.validate_records([rec(tx_hash="ABCD")])
        self.assertIn("MALFORMED_TX_HASH", summary["records"][0]["issues"])

    def test_bad_task_id_flagged(self):
        summary, _ = validator.validate_records([rec(task_id="task_XYZ")])
        self.assertIn("MALFORMED_TASK_ID", summary["records"][0]["issues"])


class TestDuplicates(unittest.TestCase):
    def test_duplicate_submission_id_flags_both(self):
        summary, clean = validator.validate_records([rec(), rec(cid=VALID_CID_2)])
        self.assertFalse(clean)
        for entry in summary["records"]:
            self.assertIn("DUPLICATE_SUBMISSION_ID", entry["issues"])

    def test_duplicate_cid_reference_flagged(self):
        summary, _ = validator.validate_records([rec(), rec(submission_id="sub_0002")])
        for entry in summary["records"]:
            self.assertIn("DUPLICATE_CID_REFERENCE", entry["issues"])

    def test_distinct_records_are_clean(self):
        _, clean = validator.validate_records(
            [rec(), rec(submission_id="sub_0002", cid=VALID_CID_2)]
        )
        self.assertTrue(clean)


class TestCliExitCodes(unittest.TestCase):
    def _run(self, payload_text):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            fh.write(payload_text)
            path = fh.name
        try:
            return subprocess.run(
                [sys.executable, os.path.join(HERE, "validator.py"), path],
                capture_output=True, text=True,
            )
        finally:
            os.unlink(path)

    def test_exit_zero_on_clean(self):
        p = self._run(json.dumps([rec()]))
        self.assertEqual(p.returncode, 0)
        self.assertEqual(json.loads(p.stdout)["totals"]["rejected"], 0)

    def test_exit_one_on_issues(self):
        p = self._run(json.dumps([rec(tx_hash="nope")]))
        self.assertEqual(p.returncode, 1)

    def test_exit_two_on_invalid_json(self):
        p = self._run("{ this is not json")
        self.assertEqual(p.returncode, 2)
        self.assertIn("INVALID_JSON", p.stderr)

    def test_exit_two_on_non_array(self):
        p = self._run(json.dumps({"submission_id": "x"}))
        self.assertEqual(p.returncode, 2)
        self.assertIn("EXPECTED_JSON_ARRAY", p.stderr)

    def test_exit_two_on_missing_file(self):
        p = subprocess.run(
            [sys.executable, os.path.join(HERE, "validator.py"), "/nonexistent.json"],
            capture_output=True, text=True,
        )
        self.assertEqual(p.returncode, 2)
        self.assertIn("FILE_NOT_FOUND", p.stderr)


# =========================================================================
# Trailing-newline anchors: `$` accepted one character too many
# =========================================================================
#
# Appended at the end of the file, and every import these tests need is
# already at the top, so no existing line moves. Several repository-wide
# reports in this repository record findings by line number.

VALID_CIDV1 = "bafy" + "a" * 50

#: One known-good value per format pattern. The patterns themselves are
#: read off the module by name rather than restated here, so these tests
#: run unchanged against a source that predates any of this and report a
#: failure rather than an AttributeError.
GOOD_FOR_PATTERN = {
    "CIDV0_RE": VALID_CID,
    "CIDV1_RE": VALID_CIDV1,
    "TXHASH_RE": VALID_TX,
    "TASK_ID_RE": VALID_TASK,
}

#: Values one character (or more) away from a good one, with names that
#: read in a `-v` listing. The first entry is the case this change
#: repaired; the rest were already refused and are here so the scope of
#: the repair is pinned rather than described.
NEAR_MISSES = (
    ("trailing_newline_twice", "\n\n"),
    ("newline_then_character", "\nX"),
    ("one_extra_character", "X"),
    ("trailing_crlf", "\r\n"),
    ("trailing_space", " "),
    ("trailing_tab", "\t"),
    ("leading_newline", "\n"),
)


def module_patterns():
    """Every compiled pattern defined at validator.py's module scope."""
    found = {}
    for name, value in vars(validator).items():
        if isinstance(value, type(validator.TXHASH_RE)):
            found[name] = value
    return found


class TestEndAnchors(unittest.TestCase):
    """Every format pattern must end at the end of the string.

    `$` in Python matches at the end of the string *or* just before a
    single trailing newline, so `^[0-9A-F]{64}$` accepted a 65-character
    value ending in "\\n". README.md says `tx_hash` is "exactly 64
    uppercase hex characters" and its schema table says `[0-9A-F]{64}`;
    a 65-character string is neither. The same one-character hole was in
    all four patterns.
    """

    def test_good_values_still_match(self):
        for name in sorted(GOOD_FOR_PATTERN):
            with self.subTest(name):
                pattern = getattr(validator, name)
                self.assertIsNotNone(pattern.match(GOOD_FOR_PATTERN[name]))

    def test_one_trailing_newline_is_refused(self):
        # This is the case that used to pass, on every pattern rather
        # than only the one the fixture happens to exercise.
        for name in sorted(GOOD_FOR_PATTERN):
            with self.subTest(name):
                pattern = getattr(validator, name)
                self.assertIsNone(pattern.match(GOOD_FOR_PATTERN[name] + "\n"))

    def test_the_near_misses_that_were_already_refused_still_are(self):
        # Scope, stated as a test: exactly one trailing newline slipped
        # through and nothing else did. If a future change makes one of
        # these start passing, that is a new hole, not this one.
        for name in sorted(GOOD_FOR_PATTERN):
            pattern = getattr(validator, name)
            good = GOOD_FOR_PATTERN[name]
            for label, affix in NEAR_MISSES:
                value = good + affix if label != "leading_newline" else affix + good
                with self.subTest(pattern=name, case=label):
                    self.assertIsNone(pattern.match(value))

    def test_no_module_pattern_uses_a_dollar_anchor(self):
        # The structural guard, and the reason it discovers patterns by
        # introspection rather than from a list: a fifth pattern added
        # later is covered without anyone remembering to add it here.
        for name, pattern in sorted(module_patterns().items()):
            with self.subTest(name):
                self.assertNotIn("$", pattern.pattern)

    def test_every_start_anchored_pattern_is_end_anchored(self):
        for name, pattern in sorted(module_patterns().items()):
            if not pattern.pattern.startswith("^"):
                continue
            with self.subTest(name):
                self.assertTrue(
                    pattern.pattern.endswith("\\Z"),
                    "%s starts with ^ but does not end with \\Z: %s"
                    % (name, pattern.pattern),
                )

    def test_introspection_finds_the_four_documented_patterns(self):
        # Keeps the two tests above from silently covering nothing if
        # the patterns are ever moved out of module scope.
        self.assertEqual(sorted(module_patterns()), sorted(GOOD_FOR_PATTERN))


class TestTrailingNewlineRecords(unittest.TestCase):
    """A record whose field carries a trailing newline is now rejected."""

    def test_tx_hash_with_trailing_newline_flagged(self):
        summary, clean = validator.validate_records([rec(tx_hash=VALID_TX + "\n")])
        self.assertFalse(clean)
        self.assertEqual(summary["records"][0]["issues"], ["MALFORMED_TX_HASH"])

    def test_task_id_with_trailing_newline_flagged(self):
        summary, clean = validator.validate_records([rec(task_id=VALID_TASK + "\n")])
        self.assertFalse(clean)
        self.assertEqual(summary["records"][0]["issues"], ["MALFORMED_TASK_ID"])

    def test_cidv0_with_trailing_newline_flagged(self):
        summary, clean = validator.validate_records([rec(cid=VALID_CID + "\n")])
        self.assertFalse(clean)
        self.assertEqual(summary["records"][0]["issues"], ["MALFORMED_CID"])

    def test_cidv1_with_trailing_newline_flagged(self):
        summary, clean = validator.validate_records([rec(cid=VALID_CIDV1 + "\n")])
        self.assertFalse(clean)
        self.assertEqual(summary["records"][0]["issues"], ["MALFORMED_CID"])

    def test_a_field_that_is_only_a_newline_is_empty_not_malformed(self):
        # README: "Format checks only run when the field is present and
        # non-empty". "\n".strip() is empty, so this is EMPTY_FIELD and
        # must NOT also trip MALFORMED_TX_HASH.
        summary, clean = validator.validate_records([rec(tx_hash="\n")])
        self.assertFalse(clean)
        self.assertEqual(summary["records"][0]["issues"], ["EMPTY_FIELD:tx_hash"])

    def test_a_clean_record_is_still_clean(self):
        summary, clean = validator.validate_records([rec()])
        self.assertTrue(clean, summary["records"][0]["issues"])


class TestTrailingNewlineFixture(unittest.TestCase):
    """The committed fixture, read off disk rather than rebuilt here."""

    FIXTURE = os.path.join(HERE, "sample_trailing_newline.json")

    def load(self):
        with open(self.FIXTURE, "r", encoding="utf-8") as fh:
            return json.load(fh)

    def test_fixture_records_carry_exactly_one_trailing_newline_each(self):
        # The fixture is only meaningful if each record differs from a
        # valid one by exactly one "\n" and nothing else. Every field is
        # walked, not just the three that carry a format check: a stray
        # newline on `wallet` or `submission_id` would make the fixture
        # say something it is not supposed to say, and would otherwise
        # go unnoticed.
        offenders = []
        for record in self.load():
            self.assertEqual(sorted(record), sorted(validator.REQUIRED_FIELDS))
            for field in sorted(record):
                value = record[field]
                with self.subTest(record=record["submission_id"], field=field):
                    self.assertIsInstance(value, str)
                    # The ONLY whitespace anywhere is a trailing newline,
                    # and never more than one of them.
                    self.assertEqual(value.rstrip("\n"), value.strip())
                    self.assertFalse(value.endswith("\n\n"))
                    if value.endswith("\n"):
                        offenders.append((record["submission_id"], field))
        self.assertEqual(
            offenders,
            [("sub_3001", "tx_hash"), ("sub_3002", "task_id"), ("sub_3003", "cid")],
        )

    def test_fixture_is_rejected_record_by_record(self):
        summary, clean = validator.validate_records(self.load())
        self.assertFalse(clean)
        self.assertEqual(summary["totals"], {"records": 3, "clean": 0, "rejected": 3})
        self.assertEqual(
            [e["issues"] for e in summary["records"]],
            [["MALFORMED_TX_HASH"], ["MALFORMED_TASK_ID"], ["MALFORMED_CID"]],
        )

    def test_fixture_trips_no_other_issue(self):
        # No missing field, no empty field, no duplicate: the fixture
        # isolates the anchor defect and nothing else.
        summary, _ = validator.validate_records(self.load())
        self.assertEqual(
            summary["issue_totals"],
            {"MALFORMED_CID": 1, "MALFORMED_TASK_ID": 1, "MALFORMED_TX_HASH": 1},
        )

    def test_stripping_the_newlines_makes_the_fixture_clean(self):
        # The other half of the same claim: the only thing wrong with
        # these records is the trailing newline.
        records = [
            {k: v.rstrip("\n") for k, v in record.items()} for record in self.load()
        ]
        summary, clean = validator.validate_records(records)
        self.assertTrue(clean, summary["records"])


class TestTrailingNewlineCli(unittest.TestCase):
    """End to end, as a subprocess, exactly as the README documents."""

    def run_cli(self, name):
        return subprocess.run(
            [sys.executable, os.path.join(HERE, "validator.py"),
             os.path.join(HERE, name)],
            capture_output=True, text=True,
        )

    def test_fixture_exits_one(self):
        proc = self.run_cli("sample_trailing_newline.json")
        self.assertEqual(proc.returncode, 1, proc.stderr)
        self.assertEqual(
            json.loads(proc.stdout)["totals"],
            {"records": 3, "clean": 0, "rejected": 3},
        )

    def test_the_valid_sample_still_exits_zero(self):
        proc = self.run_cli("sample_valid.json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(
            json.loads(proc.stdout)["totals"],
            {"records": 2, "clean": 2, "rejected": 0},
        )

    def test_the_invalid_sample_still_exits_one(self):
        proc = self.run_cli("sample_invalid.json")
        self.assertEqual(proc.returncode, 1, proc.stderr)
        self.assertEqual(
            json.loads(proc.stdout)["totals"],
            {"records": 3, "clean": 0, "rejected": 3},
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
