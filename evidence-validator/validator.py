#!/usr/bin/env python3
"""
Evidence Integrity Validator - CI-ready pre-reward check for Task Node evidence records.

Reads a JSON array of evidence records, validates structure and reference formats,
detects duplicate submissions, and emits a structured JSON summary.

Exit codes:
  0 - all records clean
  1 - one or more records had validation issues
  2 - processing error (unreadable file, invalid JSON, wrong top-level shape)
"""
import argparse
import json
import re
import sys
from collections import Counter

# --- Declared contract -------------------------------------------------------
REQUIRED_FIELDS = ("submission_id", "task_id", "wallet", "cid", "tx_hash")

# IPFS CIDv0: "Qm" + 44 base58 chars (no 0, O, I, l). CIDv1: "bafy" + base32 lowercase.
CIDV0_RE = re.compile(r"^Qm[1-9A-HJ-NP-Za-km-z]{44}$")
CIDV1_RE = re.compile(r"^bafy[a-z2-7]{50,}$")
# XRPL transaction hash: exactly 64 uppercase hex characters.
TXHASH_RE = re.compile(r"^[0-9A-F]{64}$")
# Task Node task id: "task_" + 32 lowercase hex.
TASK_ID_RE = re.compile(r"^task_[0-9a-f]{32}$")

ISSUE_MISSING_FIELD = "MISSING_FIELD"
ISSUE_EMPTY_FIELD = "EMPTY_FIELD"
ISSUE_NOT_OBJECT = "RECORD_NOT_OBJECT"
ISSUE_BAD_CID = "MALFORMED_CID"
ISSUE_BAD_TXHASH = "MALFORMED_TX_HASH"
ISSUE_BAD_TASK_ID = "MALFORMED_TASK_ID"
ISSUE_DUP_SUBMISSION = "DUPLICATE_SUBMISSION_ID"
ISSUE_DUP_REFERENCE = "DUPLICATE_CID_REFERENCE"


def validate_record(record, index):
    """Return a list of issue codes for a single record."""
    issues = []
    if not isinstance(record, dict):
        return [ISSUE_NOT_OBJECT]

    for field in REQUIRED_FIELDS:
        if field not in record:
            issues.append(f"{ISSUE_MISSING_FIELD}:{field}")
        elif not isinstance(record[field], str) or not record[field].strip():
            issues.append(f"{ISSUE_EMPTY_FIELD}:{field}")

    cid = record.get("cid")
    if isinstance(cid, str) and cid.strip():
        if not (CIDV0_RE.match(cid) or CIDV1_RE.match(cid)):
            issues.append(ISSUE_BAD_CID)

    tx = record.get("tx_hash")
    if isinstance(tx, str) and tx.strip():
        if not TXHASH_RE.match(tx):
            issues.append(ISSUE_BAD_TXHASH)

    tid = record.get("task_id")
    if isinstance(tid, str) and tid.strip():
        if not TASK_ID_RE.match(tid):
            issues.append(ISSUE_BAD_TASK_ID)

    return issues


def validate_records(records):
    """Validate a list of records; return (summary_dict, clean_bool)."""
    per_record = []

    sub_counts = Counter()
    cid_counts = Counter()
    for r in records:
        if isinstance(r, dict):
            sid = r.get("submission_id")
            if isinstance(sid, str) and sid.strip():
                sub_counts[sid] += 1
            cid = r.get("cid")
            if isinstance(cid, str) and cid.strip():
                cid_counts[cid] += 1

    for i, record in enumerate(records):
        issues = validate_record(record, i)
        if isinstance(record, dict):
            sid = record.get("submission_id")
            if isinstance(sid, str) and sub_counts[sid] > 1:
                issues.append(ISSUE_DUP_SUBMISSION)
            cid = record.get("cid")
            if isinstance(cid, str) and cid_counts[cid] > 1:
                issues.append(ISSUE_DUP_REFERENCE)

        per_record.append({
            "index": i,
            "submission_id": record.get("submission_id") if isinstance(record, dict) else None,
            "issues": issues,
            "status": "clean" if not issues else "rejected",
        })

    issue_totals = Counter()
    for entry in per_record:
        for code in entry["issues"]:
            issue_totals[code.split(":")[0]] += 1

    rejected = sum(1 for e in per_record if e["issues"])
    summary = {
        "schema_version": "1.0",
        "required_fields": list(REQUIRED_FIELDS),
        "totals": {
            "records": len(records),
            "clean": len(records) - rejected,
            "rejected": rejected,
        },
        "issue_totals": dict(sorted(issue_totals.items())),
        "records": per_record,
    }
    return summary, rejected == 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Validate Task Node evidence records before reward processing."
    )
    parser.add_argument("input", help="Path to a JSON file containing an array of evidence records.")
    parser.add_argument("--pretty", action="store_true", help="Indent the JSON summary.")
    args = parser.parse_args(argv)

    try:
        with open(args.input, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        print(json.dumps({"error": "FILE_NOT_FOUND", "path": args.input}), file=sys.stderr)
        return 2
    except json.JSONDecodeError as exc:
        print(json.dumps({"error": "INVALID_JSON", "detail": str(exc)}), file=sys.stderr)
        return 2

    if not isinstance(data, list):
        print(json.dumps({"error": "EXPECTED_JSON_ARRAY", "got": type(data).__name__}), file=sys.stderr)
        return 2

    summary, clean = validate_records(data)
    print(json.dumps(summary, indent=2 if args.pretty else None))
    return 0 if clean else 1


if __name__ == "__main__":
    sys.exit(main())
