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


if __name__ == "__main__":
    unittest.main(verbosity=2)
