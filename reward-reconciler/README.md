# Deterministic Reward Reconciliation CLI

Stdlib-only Python 3 (`argparse`, `errno`, `json`, `decimal`, `sys`). No third-party
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
| 2 | invalid input / processing error: bad JSON, wrong shape, float amount, missing field, duplicate `task_id` in the expected set, unreadable file, a directory, non-UTF-8 bytes, an amount outside the range `Decimal` can quantize to 6 dp, a signalling NaN amount, a CLI usage error, or an unwritable `--out` (which includes stdout: a closed pipe or a full disk) |

Those three are the only codes the process ever returns, and that is the
whole of the contract. It is worth stating explicitly because the failure
this directory just repaired was the contract being broken from the inside:
an uncaught exception exits `1`, and `1` here means *mismatched*. Four
documented conditions, plus an unwritable `--out`, were escaping as
tracebacks — so a caller reading exit codes was recording malformed input,
and in one case a **balanced** settlement, as a reconciliation result.

The table that pins this lives in the test suite. `EXIT_CODE_CASES`
holds one row per documented code and one per trigger the exit-`2` row names
above, each invoking the real CLI as a subprocess and asserting the exact
process exit status. Three guards keep the table honest rather than
decorative: the set of codes in the table must equal the set of codes this
README documents; every case must quote a phrase that literally occurs in
this file; and the exit-`2` cell above must be *exactly* the twelve phrases in
`EXIT_TWO_TRIGGERS`, reassembled — `test_the_readme_row_is_exactly_the_trigger_list`
compares the parsed cell against a string built from the tuple. That is what
makes the check bidirectional: deleting a trigger from the row fails it, and
so does adding one without adding a matching case. An earlier draft asserted
only `trigger in cell`, which caught the deletion and missed the addition, and
the sentence you are reading claimed otherwise.

Reaching exit `2` by crashing is not reaching exit `2`, so
`test_no_case_leaves_a_python_traceback` asserts that no case in the table
prints one, and `test_every_rejection_names_its_reason_as_json` asserts each
rejection emits `{"error": ..., "detail": ...}` on stderr —
`INVALID_INPUT` for a bad input, `OUTPUT_ERROR` for a report that could not
be written.

## Determinism guarantees

- Findings sorted by `(task_id, issue, wallet)`, independent of input order.
- Output serialized with `sort_keys=True`, `separators=(",",":")`,
  `ensure_ascii=True`, one trailing newline.
- Running the tool twice on the same inputs produces byte-identical files
  (verified below).

## Exact rerun commands

```
python3 -m unittest test_reconcile -v
python3 -m unittest test_reconcile.TestDocumentedExitCodes test_reconcile.TestRepairedExitCodes
python3 reconcile.py expected_rewards.json recorded_payouts.json -o report_run1.json ; echo "exit=$?"
python3 reconcile.py expected_rewards.json recorded_payouts.json -o report_run2.json ; echo "exit=$?"
sha256sum report_run1.json report_run2.json
cmp report_run1.json report_run2.json && echo BYTE-IDENTICAL
```

## Expected results

| step | result |
|------|--------|
| tests | `Ran 50 tests` / `OK` |
| both reconciliation runs | exit **1** (mismatched) |
| both reports SHA-256 | `bc5a197234abcba48ef039e9d0f3dd20c590dfa9782c057481550c8c7d9e7b56` |
| `cmp` | BYTE-IDENTICAL |

`samples_invalid/` holds one committed fixture per documented exit-`2`
trigger, so the table runs the CLI against files in the tree rather than
against something a test generated. `not_utf8.json` is committed as raw
bytes on purpose — generating it would let a later edit quietly change what
"not UTF-8" means. `EXIT_CODE_EVIDENCE.txt` records every case run against
the pre-fix source and against the fixed source; `mk_exitcode_evidence.sh`
regenerates it. That file takes `env-leak-scanner`'s repo-wide scan from 936
confirmed to 946, every one of the ten in `absolute_path` and every one of
them an occurrence of the two unwritable device targets the evidence uses.
Those have to be absolute paths — being absolute, and pointing at a device, is
what makes them unwritable. This sentence names no path itself, deliberately:
an earlier draft quoted them and each quotation added another finding, so the
number it was reporting moved every time it was corrected.
The scan is not a gate and the committed leak report is a pinned dated
snapshot, but a commit that moves a number the repository measures should say
so rather than let a reviewer find it. `captured_output.txt` and `test_output.txt` are regenerated
by `mk_captured_output.sh`, which runs every command in the block above and
writes what they actually printed. Neither regenerator is named `capture.sh`,
and that is deliberate: `regen-preflight` discovers regenerators by that exact
name and diffs their output against the committed file, `mk_exitcode_evidence.sh`
reads Git history (which is absent from the copy preflight makes), and both
transcripts embed unittest's `Ran N tests in ...s` wall-clock, which no
byte-for-byte diff can survive.

`captured_output.txt` also runs the exit-code classes on their own, which is
`Ran 25 tests` -- a subset count, not a second claim about the suite total.
It is stated here, below the table above, and not next to the exit-code
prose: `index-generator/indexgen.py` takes the *first* `Ran N tests` in a
README as that tool's claim, so a subset count placed higher up would have
had this directory claiming 25 while `readme-index` read 50 from the table.
The two extractors disagreeing about the same file is precisely the kind of
thing this repository exists to notice.

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


## 5 limitations a reviewer should scrutinise (number 2 is now fixed)

Found by **running this tool against adversarial inputs**, not by reading it.
Items 1, 2, 3 and 5 are reproduced by
[`limitations-probe/probe.py`](../limitations-probe/probe.py) as `RR-4`,
`RR-2`, `RR-1` and `RR-3`, and that harness exits non-zero if any of them
stops reproducing. Item 4 is the exception and has no `RR` id: it was found
while writing the exit-code table, and what pins it is
`test_a_write_time_out_failure_is_exit_2` in this directory's own suite,
which pins the exit code and not the state of the file. Saying "every claim"
here would have been one word of tidiness bought with a false sentence.

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

2. **FIXED — an out-of-range exponent used to crash instead of exiting `2`
   (RR-2).** An amount of `"1E+999999999"` raised an uncaught
   `decimal.InvalidOperation` and exited **`1`** with a Python traceback and
   no JSON report, because `value.quantize(SCALE)` sat outside the
   `try/except InvalidOperation` that guards `Decimal(str(raw))`. Exit `1` is
   the code this tool uses for "mismatches found", so a caller reading exit
   codes recorded a malformed input as a reconciliation result.

   It now exits `2` with `{"error":"INVALID_INPUT","detail":"expected[0]:
   amount is not representable at 0.000001 precision: '1E+999999999'"}`.
   `"Infinity"` took the same route and is fixed by the same guard. Three
   siblings of this defect were found while writing the test table and are
   fixed alongside it: a path that is a directory, a file whose bytes are not
   UTF-8, and an unwritable `--out`, each of which also escaped as a
   traceback and exited `1`. The `--out` case is the worst of the four,
   because it could report a **balanced** run as `1` — a clean settlement
   recorded as a broken one.

   This item is left in place rather than deleted: `limitations-probe/`
   reproduces every entry in this list and exits non-zero when one stops
   reproducing, so `probe_rr2` has been rewritten to pin the repair instead
   of the defect. Deleting the item would have left that harness asserting a
   behaviour this directory no longer has.

3. **Differences below the settlement scale are quantized away, not rejected
   (RR-1).** Expected `1.0000004` against paid `1.0000001` reports
   `"status": "balanced"`, zero findings, exit `0`. Both quantize to
   `1.000000`. That is the documented 6-dp precision doing its job, but the
   consequence — a real discrepancy silently absorbed rather than flagged as
   over-precise input — was not stated. Across a large batch these do not
   cancel; they accumulate in whichever direction the rounding falls.

4. **A failed `--out` now exits `2`, but the destination may already be
   truncated.** `open(args.out, "w")` truncates before anything is written,
   so a write that fails part-way — a full disk, a quota — leaves a partial
   report where the previous one was, and the run still exits `2`. The exit
   code is now right; the file is not necessarily intact. A caller that reads
   `exit 2` as "nothing was touched" is wrong.
   `test_a_write_time_out_failure_is_exit_2` pins the code, and nothing pins
   the file, because nothing here makes the write atomic. `readme-index/` in
   this repository does have that (`write_text_atomically`, sibling temp file
   plus `os.replace`); applying it here is a separate change to a separate
   contract and was deliberately not folded into this one.

5. **Amounts have no sign or range check (RR-3).** A negative expected reward
   reconciles cleanly against a negative payout: `"status": "balanced"`,
   exit `0`, `expected_total: "-5.000000"`. Scientific notation is likewise
   accepted, so `"1E+2"` and `"100"` are the same amount. Neither is
   necessarily wrong, but neither is checked, and a reconciler is the last
   place a negative reward should pass unremarked.
