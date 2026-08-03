# Evidence Integrity Validator

Stdlib-only Python 3 (`argparse`, `json`, `re`, `collections.Counter`). No
third-party packages, no network. CI-ready pre-reward check for Task Node
evidence records: validates record shape, checks CID / transaction-hash /
task-id formats, and flags duplicate submissions before any reward is
finalized.

## What this tool does

Reads a JSON array of evidence records and, for each one, checks:

- all five required fields are present and non-empty
- `cid` matches an IPFS CIDv0 (`Qm` + 44 base58 chars) or CIDv1 (`bafy` +
  50+ lowercase base32 chars) shape
- `tx_hash` is exactly 64 uppercase hex characters
- `task_id` matches `task_` + 32 lowercase hex characters
- `submission_id` is not reused across records in the same input
  (`DUPLICATE_SUBMISSION_ID`, flagged on **every** record that shares it)
- `cid` is not reused across records in the same input
  (`DUPLICATE_CID_REFERENCE`, flagged on **every** record that shares it)

A record with zero issues is `"status": "clean"`; any issue makes it
`"rejected"`. The process exits non-zero if any record was rejected.

## Input shape

A JSON array of objects, each with:

| field | type | required |
|-------|------|----------|
| `submission_id` | non-empty string | yes |
| `task_id` | non-empty string, must match `task_[0-9a-f]{32}` | yes |
| `wallet` | non-empty string | yes |
| `cid` | non-empty string, must match a CIDv0 or CIDv1 shape | yes |
| `tx_hash` | non-empty string, must match `[0-9A-F]{64}` | yes |

A top-level element that is not a JSON object is reported as a single
`RECORD_NOT_OBJECT` issue and does not crash the run. A top-level value
that is not a JSON array at all is a fatal input error (exit 2), not a
per-record finding.

## Issue codes

| code | meaning |
|------|---------|
| `MISSING_FIELD:<field>` | one of the five required fields is absent |
| `EMPTY_FIELD:<field>` | the field is present but not a non-empty string |
| `RECORD_NOT_OBJECT` | the array element itself is not a JSON object |
| `MALFORMED_CID` | `cid` is present and non-empty but matches neither CID shape |
| `MALFORMED_TX_HASH` | `tx_hash` is present and non-empty but not 64 uppercase hex chars |
| `MALFORMED_TASK_ID` | `task_id` is present and non-empty but not `task_` + 32 lowercase hex |
| `DUPLICATE_SUBMISSION_ID` | this record's `submission_id` also appears on another record in the same input |
| `DUPLICATE_CID_REFERENCE` | this record's `cid` also appears on another record in the same input |

Format checks (`MALFORMED_*`) only run when the field is present and
non-empty; a missing/empty field is reported once via `MISSING_FIELD` /
`EMPTY_FIELD` rather than also tripping a format check on `None`/`""`.

## Flags

| flag | description |
|------|-------------|
| `input` (positional) | Path to a JSON file containing an array of evidence records. Required. |
| `--pretty` | `store_true`, default off. Indent the JSON summary with `json.dumps(..., indent=2)` for human reading. Without it, output is compact single-line JSON (`json.dumps(summary)` with default separators — this is **not** the `sort_keys`/fixed-separator canonical form some sibling tools use; see "Determinism" below). |

This tool has no file-output option of any kind. The summary is always
written to stdout; redirect it yourself (`> report.json`) if you need it
in a file.

## Determinism

Key order in the emitted JSON follows Python dict insertion order (the
order fields are assigned in the code), not `sort_keys=True`, and there is
no `ensure_ascii` or fixed-separator normalization. Two runs on the same
input produce the same content, but the exact byte layout is not pinned
the way `reconcile.py`-style tools in this repo pin theirs (no
`sha256sum`-stable byte-identity contract here). `issue_totals` itself
*is* built with `dict(sorted(...))`, so that one sub-object is
key-sorted regardless.

## Exit codes

| code | meaning |
|------|---------|
| 0 | all records clean |
| 1 | one or more records had validation issues |
| 2 | processing error: input file not found, invalid JSON, or top-level JSON value is not an array |

## Exact rerun commands

```
python3 -m unittest test_validator -v
python3 validator.py sample_valid.json --pretty   ; echo "exit=$?"
python3 validator.py sample_invalid.json          ; echo "exit=$?"
python3 validator.py sample_invalid.json --pretty ; echo "exit=$?"
python3 validator.py /nonexistent.json            ; echo "exit=$?"
```

## Expected results

| step | result |
|------|--------|
| tests | `Ran 17 tests` / `OK`, exit **0** |
| `sample_valid.json --pretty` | `totals: {"records": 2, "clean": 2, "rejected": 0}`, exit **0** |
| `sample_invalid.json` | `totals: {"records": 3, "clean": 0, "rejected": 3}`, exit **1** |
| `sample_invalid.json --pretty` | same content, indented | exit **1** |
| missing file | `{"error": "FILE_NOT_FOUND", "path": "/nonexistent.json"}` on stderr, exit **2** |

Full captured output of every command above is in `run_output.txt`.

## Scope limit

This tool checks record *shape* and *internal consistency* (format,
completeness, duplication) only. It does not query IPFS to confirm a CID
resolves to real content, and it does not query the XRPL to confirm a
`tx_hash` corresponds to a real, confirmed transaction — both would need
network access, which is deliberately out of scope here.
