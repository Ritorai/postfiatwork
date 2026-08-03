# Deterministic Reward Reconciliation CLI

Stdlib-only Python 3 (`argparse`, `json`, `decimal`, `sys`). No third-party
packages, no network access. Compares expected task rewards against
recorded payouts and surfaces settlement errors before funds are
finalized. Output is canonical: byte-identical across repeated runs on the
same inputs.

## What this tool does

Reads two JSON arrays -- expected rewards and recorded payouts -- joins
them on `task_id`, and reports five kinds of settlement issue:

| issue code | meaning |
|------------|---------|
| `MISSING_PAYOUT` | an expected reward has no corresponding payout |
| `DUPLICATE_PAYOUT` | more than one payout recorded for the same task |
| `UNEXPECTED_PAYOUT` | a payout exists for a task with no expected reward |
| `AMOUNT_MISMATCH` | payout amount differs from the expected amount |
| `WALLET_MISMATCH` | payout went to a different wallet than expected |

`reconcile.py` is the CLI entry point. `reconcile()` inside it is a pure
function with no I/O, directly unit-testable and reused as-is by the
`reward-anomaly` tool in this repo.

## Input shape

Both inputs are JSON arrays of objects with the same three fields:

| field | type | required |
|-------|------|----------|
| `task_id` | non-empty string | yes |
| `wallet` | non-empty string | yes |
| `amount` | string or integer, parsed as `Decimal` | yes |

Amounts are parsed via `decimal.Decimal` and quantized to 6 dp
(`SCALE = Decimal("0.000001")`). **JSON floats are rejected outright**
(exit 2) because binary floats cannot represent settlement values
exactly -- pass amounts as strings or integers. A duplicate `task_id`
within the *expected* set is also a fatal input error (exit 2); duplicate
`task_id`s in the *payouts* set are legitimate and become
`DUPLICATE_PAYOUT` findings instead.

## Flags

| flag | description |
|------|-------------|
| `expected` (positional) | Path to a JSON array of expected rewards. Required. |
| `payouts` (positional) | Path to a JSON array of recorded payouts. Required. |
| `-o`, `--out PATH` | Write the canonical JSON report to this file instead of stdout. Optional; without it, the report is printed to stdout. |

## Exit codes

| code | meaning |
|------|---------|
| 0 | balanced, no findings |
| 1 | mismatched, one or more settlement issues |
| 2 | invalid input / processing error (bad JSON, wrong shape, float amount, missing field, duplicate `task_id` in the expected set, unreadable file) |

## Determinism guarantees

- Findings sorted by `(task_id, issue, wallet)`, independent of input order.
- Output serialized with `sort_keys=True`, `separators=(",",":")`,
  `ensure_ascii=True`, one trailing newline.
- Running the tool twice on the same inputs produces byte-identical files
  (verified below).

## Exact rerun commands

```
python3 -m unittest test_reconcile -v
python3 reconcile.py expected_rewards.json recorded_payouts.json -o report_run1.json ; echo "exit=$?"
python3 reconcile.py expected_rewards.json recorded_payouts.json -o report_run2.json ; echo "exit=$?"
sha256sum report_run1.json report_run2.json
cmp report_run1.json report_run2.json && echo BYTE-IDENTICAL
```

## Expected results

| step | result |
|------|--------|
| tests | `Ran 23 tests` / `OK` |
| both reconciliation runs | exit **1** (mismatched) |
| both reports SHA-256 | `bc5a197234abcba48ef039e9d0f3dd20c590dfa9782c057481550c8c7d9e7b56` |
| `cmp` | BYTE-IDENTICAL |

`expected_report.json` is a committed copy of that report for diffing.
`report_run1.json` / `report_run2.json` are scratch output reproduced by
the commands above (not shipped as fixtures beyond `expected_report.json`).
Full real captured output of the commands above is in `captured_output.txt`.

## Fixture contents

`expected_rewards.json` (5 records, 10.000000 total) vs
`recorded_payouts.json` (6 records, 12.750000 total) deliberately
exercises all five issue types plus a correctly balanced record
(`task_b3a3e54adcac730636afd7e9ca80b798`).

## See also

`RUN_COMMANDS.md` in this directory has the same reproduction commands in
a shorter, copy-paste-first form.
