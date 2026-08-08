# Deterministic Reward Reconciliation CLI

Standard-library Python 3 only. No third-party packages, no network access.

## Reproduce everything

```
python3 -m unittest test_reconcile -v
python3 -m unittest test_reconcile.TestDocumentedExitCodes test_reconcile.TestRepairedExitCodes
python3 reconcile.py expected_rewards.json recorded_payouts.json -o report_run1.json ; echo "exit=$?"
python3 reconcile.py expected_rewards.json recorded_payouts.json -o report_run2.json ; echo "exit=$?"
sha256sum report_run1.json report_run2.json
cmp report_run1.json report_run2.json && echo BYTE-IDENTICAL
```

## Expected results

- Test suite: `Ran 50 tests` / `OK`
- Both reconciliation runs exit with code **1** (mismatched)
- Both reports share SHA-256 `bc5a197234abcba48ef039e9d0f3dd20c590dfa9782c057481550c8c7d9e7b56`
- `expected_report.json` is a committed copy of that report for diffing

## Exit codes

The authoritative table is `README.md`'s "## Exit codes" section, and
`test_reconcile.py` asserts the test table matches it phrase for phrase. A
second copy here would be a free drift surface with nothing checking it, so
this file names the codes and points at the one table that is pinned:

| code | meaning |
|------|---------|
| 0 | balanced, no findings |
| 1 | mismatched, one or more settlement issues |
| 2 | invalid input / processing error — see `README.md` for the full trigger list |

## Issue codes

`MISSING_PAYOUT`, `DUPLICATE_PAYOUT`, `UNEXPECTED_PAYOUT`, `AMOUNT_MISMATCH`, `WALLET_MISMATCH`

## Determinism guarantees

- Amounts parsed via `decimal.Decimal` quantized to 6 dp. JSON floats are **rejected** (exit 2) because binary floats cannot represent settlement values exactly.
- Findings sorted by `(task_id, issue, wallet)`, independent of input order.
- Output serialized with `sort_keys=True`, `separators=(",",":")`, `ensure_ascii=True`, trailing newline.

## Fixture contents

`expected_rewards.json` (5 records, 10.000000 total) vs `recorded_payouts.json` (6 records, 12.750000 total) deliberately exercises all five issue types plus a correctly balanced record.
