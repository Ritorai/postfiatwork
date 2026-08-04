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


## 4 limitations a reviewer should scrutinise

Found by **running this tool against adversarial inputs**, not by reading it.
Every claim is reproduced by
[`limitations-probe/probe.py`](../limitations-probe/probe.py), which exits
non-zero if any of them stops reproducing.

1. **A split payout to the WRONG wallet is reported without naming that
   wallet (RR-4).** Expect `3.500000` to `rHONEST`; record two payouts of
   `1.750000` each to `rATTACKER`. The report contains exactly one finding:
   `DUPLICATE_PAYOUT`, `"wallet": "rHONEST"` — the *expected* wallet. The
   string `rATTACKER` **does not appear anywhere in the report.** The wallet
   comparison lives in the single-payout branch and never runs when payouts
   are grouped, so `WALLET_MISMATCH` cannot fire for a split payout. A reader
   triaging a `DUPLICATE_PAYOUT` finding will not learn the money went
   somewhere else. This is the most serious item here and it is a defect, not
   a trade-off.

2. **An out-of-range exponent crashes instead of exiting `2` (RR-2).** An
   amount of `"1E+999999999"` raises an uncaught `decimal.InvalidOperation`
   and exits **`1`** with a Python traceback and no JSON report.
   `value.quantize(SCALE)` sits outside the `try/except InvalidOperation` that
   guards `Decimal(str(raw))`. Exit `1` is the code this tool uses for
   "mismatches found", so a caller reading exit codes will record a malformed
   input as a reconciliation result.

3. **Differences below the settlement scale are quantized away, not rejected
   (RR-1).** Expected `1.0000004` against paid `1.0000001` reports
   `"status": "balanced"`, zero findings, exit `0`. Both quantize to
   `1.000000`. That is the documented 6-dp precision doing its job, but the
   consequence — a real discrepancy silently absorbed rather than flagged as
   over-precise input — was not stated. Across a large batch these do not
   cancel; they accumulate in whichever direction the rounding falls.

4. **Amounts have no sign or range check (RR-3).** A negative expected reward
   reconciles cleanly against a negative payout: `"status": "balanced"`,
   exit `0`, `expected_total: "-5.000000"`. Scientific notation is likewise
   accepted, so `"1E+2"` and `"100"` are the same amount. Neither is
   necessarily wrong, but neither is checked, and a reconciler is the last
   place a negative reward should pass unremarked.
