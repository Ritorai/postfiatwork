# Reward Anomaly Detection CLI

Standard-library Python 3 only. No third-party packages, no network access.

## What this tool does

Extends the reward-reconciliation tool (`reward-reconciler/reconcile.py` in
this repository) with anomaly detection over a **tasks export** and a
**payouts export**: it flags payouts for refused tasks, duplicate payouts,
amounts outside the stated task price, and payouts without a matching task
-- plus structural data-integrity problems (duplicate payout ids, invalid
amounts/prices, malformed records). The report is canonical JSON: identical
input always produces byte-identical output, so it is diffable and safe to
store as evidence.

## What was reused / what is new

This is an **extension** of `reward-reconciler`, not a new overlapping CLI.

**Reused from reward-reconciler (`reconcile.py`):**
- The record field name `task_id` and the `amount` naming convention for
  money fields.
- The overall module shape: an `InputError` exception used for exit-2
  cases, a pure `reconcile()` function with no I/O (directly
  unit-testable), a `canonical_json()` serializer, and a thin
  `main()`/`argparse` wrapper that only does I/O and exit-code translation.
- The exit-code contract: `0` = clean, `1` = findings, `2` = invalid input.
- The canonical JSON contract: `sort_keys=True`, `separators=(",", ":")`,
  `ensure_ascii=True`, one trailing newline.
- The determinism guarantee that findings are fully sorted independent of
  input order, and the file-output option (reward-reconciler used
  `-o/--out`; this tool uses `-o/--output` per this task's hard contract,
  see "Deviations" below).
- The "ambiguous duplicate suppresses the per-record check" precedent:
  reward-reconciler skips `AMOUNT_MISMATCH`/`WALLET_MISMATCH` for a task
  with more than one payout (reporting only `DUPLICATE_PAYOUT`); this tool
  applies the same precedent to `AMOUNT_ABOVE_PRICE`/`AMOUNT_BELOW_PRICE`/
  `PAYOUT_FOR_REFUSED_TASK` for a task_id with duplicate payouts.
- Decimal-only money parsing (`decimal.Decimal`, never `float`) as a
  design principle -- reward-reconciler enforced this via
  `Decimal(str(raw))` on already-string/int-typed fields; this tool goes
  further (see "New" below) because its input contract allows bare JSON
  number literals, which requires `parse_float=Decimal` at the JSON decode
  boundary, not just `Decimal(str(x))` afterwards.

**New in this tool:**
- The two-export shape itself: a **tasks** export
  (`task_id`/`status`/`price`) and a **payouts** export
  (`payout_id`/`task_id`/`amount`/`at`), and the nine anomaly codes below
  -- reward-reconciler's "expected reward" record has no `status`, no task
  `price` vs. payout `amount` distinction, and no refused-task concept.
- `json.loads(text, parse_float=Decimal, parse_constant=_NonFinite)` so a
  bare JSON number literal (e.g. a task `price` given as
  `123456789012345678.123456789` with no quotes) is parsed directly from
  source text by `Decimal`, never round-tripped through a 64-bit `float`.
  reward-reconciler never needed this because its `amount` field was only
  ever a JSON string or plain int; this tool's `price`/`amount` fields
  accept bare JSON numbers too, per this task's input shape, so the
  `Decimal(str(raw))` pattern is not safe here -- by the time `str(raw)`
  runs, `raw` would already be a corrupted `float` if `json.loads` had used
  its default float parser. See "Money handling" below.
- The `_NonFinite` sentinel for explicit `NaN`/`Infinity` rejection via
  `parse_constant`, and the `_amt_str()` fixed-point formatter (no
  scientific notation, no `-0`) -- both used to avoid the same class of bug
  a related tool in this repo (`wallet-reconciler`) also had to guard
  against.
- All nine anomaly codes and the tolerance mechanism (`--tolerance`).
- The `--tolerance` option: not present in reward-reconciler at all.

**Deviations from reward-reconciler, and why:**
- Long option name is `--output`, not `--out` (`-o` short form is
  unchanged). This task's hard contract explicitly requires
  `-o/--output`; reward-reconciler predates that requirement.
- reward-reconciler treats a duplicate `task_id` in its "expected" file as
  a **structural** error (exit 2, no report produced). This tool treats a
  duplicate `task_id` in the tasks export as a **finding**
  (`MALFORMED_RECORD`, first occurrence wins, exit 1) instead, since the
  task spec's fixed list of nine anomaly codes has no code reserved for
  "invalid input", and treating every messy-but-parseable input as a hard
  failure would make the tool much less useful for batch data with a few
  bad rows. See "Design decisions" below.

## Input shape

**Tasks export** (`tasks.json`): a JSON array of

```json
{"task_id": "task_001", "status": "accepted", "price": "25.00"}
```

- `task_id`: non-empty string. Required.
- `status`: one of `proposed`, `accepted`, `submitted`, `rewarded`,
  `refused`. Required. Any other value (or non-string) is
  `MALFORMED_RECORD`.
- `price`: the stated task price. Required. Accepts a JSON string
  (`"25.00"`), a JSON integer (`25`), or a bare JSON number literal
  (`25.00`, or a 27-significant-digit literal -- see "Precision"). Must be
  finite and non-negative, else `INVALID_PRICE`.

**Payouts export** (`payouts.json`): a JSON array of

```json
{"payout_id": "pay_001", "task_id": "task_001", "amount": "25.00", "at": "2026-01-01T00:00:00Z"}
```

- `payout_id`: non-empty string. Required.
- `task_id`: non-empty string. Required. (Need not exist in the tasks
  export -- that mismatch is exactly what `PAYOUT_WITHOUT_TASK` detects.)
- `amount`: the amount actually paid. Required. Same accepted shapes as
  `price`. Must be finite and non-negative, else `INVALID_AMOUNT`.
- `at`: an ISO-8601 timestamp, must be timezone-aware (a `Z` suffix or an
  explicit `+HH:MM`/`-HH:MM` offset -- a naive timestamp is rejected).
  Required. Not used for any ordering/business logic in this tool (unlike
  `wallet-reconciler`'s ledger replay); it is validated for shape only, so
  a payouts export can be trusted as an audit trail even though this tool
  itself does not do time-based reasoning over it.

Any record that isn't a JSON object, is missing a required field, has a
non-string/empty id, or (for tasks) an unrecognized `status`, is reported
as `MALFORMED_RECORD` and excluded from all other checks for that record
(see "Design decisions" -- checks do NOT continue independently past a
structural failure, unlike `INVALID_AMOUNT`/`INVALID_PRICE`, which do not
exclude the record from other checks).

A non-array top-level JSON value, invalid JSON syntax, or a missing/
unreadable file is a **structural** error: exit 2, no report produced.

## Anomaly codes

| code | meaning | key fields |
|---|---|---|
| `PAYOUT_FOR_REFUSED_TASK` | a payout exists for a task whose `status` is `refused` | `task_id`, `payout_id`, `price`, `amount` |
| `DUPLICATE_PAYOUT` | two or more payouts reference the same `task_id` | `task_id`, `payout_ids` (all of them), `count` |
| `AMOUNT_ABOVE_PRICE` | payout `amount` exceeds the task's `price` by more than `--tolerance` | `task_id`, `payout_id`, `price`, `amount`, `delta` (`amount - price`, positive) |
| `AMOUNT_BELOW_PRICE` | payout `amount` is under the task's `price` by more than `--tolerance` | `task_id`, `payout_id`, `price`, `amount`, `delta` (`amount - price`, negative) |
| `PAYOUT_WITHOUT_TASK` | a payout's `task_id` does not appear in the tasks export | `task_id`, `payout_id` |
| `DUPLICATE_PAYOUT_ID` | the same `payout_id` appears more than once in the payouts export | `payout_id`, `indices`, `task_ids`, `count` |
| `INVALID_AMOUNT` | a payout's `amount` is non-numeric, NaN/Infinity, null, boolean, or negative | `task_id`, `payout_id`, `amount` (raw), `detail` |
| `INVALID_PRICE` | a task's `price` is non-numeric, NaN/Infinity, null, boolean, or negative | `task_id`, `price` (raw), `detail` |
| `MALFORMED_RECORD` | not a JSON object / missing required field / bad id / bad status / duplicate `task_id` | `source` (`"tasks"`/`"payouts"`), `index`, `task_id`, `payout_id`, `detail` |

`DUPLICATE_PAYOUT` (a business fact: the same task got paid more than
once) and `DUPLICATE_PAYOUT_ID` (a data-integrity fact: the same payout
identifier was used more than once, possibly for different `task_id`s) are
independent and can both fire for the same records.

## Money handling

- All amounts and prices are parsed with `decimal.Decimal` exclusively.
  `float` is never used anywhere in the money path.
- `json.loads(text, parse_float=Decimal, parse_constant=_NonFinite)`: a
  bare JSON number literal like `123456789012345678.123456789` is built
  directly from the source text by the `Decimal` constructor, never
  passed through an intermediate 64-bit `float`. **This is the exact bug
  this task warned about** (`Decimal(str(json_parsed_float))` silently
  truncates to ~17 significant digits because `json.loads` has already
  destroyed the precision by the time `str()` runs) -- this tool never
  performs that round-trip. See the precision demonstration below.
- Bare `NaN`/`Infinity`/`-Infinity` JSON tokens (which `json.loads`
  accepts by default) are intercepted via `parse_constant` into an opaque
  `_NonFinite` sentinel -- never turned into `Decimal('NaN')`, because
  `Decimal('NaN') == Decimal('NaN')` is `False` and `Decimal('NaN') < 0`
  is also `False`, which would silently defeat every downstream
  comparison and let invalid data slip through as if it were valid.
  NaN/Infinity given as a *quoted string* (`"NaN"`, `"Infinity"`) is also
  caught, via an explicit `.is_nan()`/`.is_infinite()` check after
  construction (since `Decimal("NaN")` does not raise on construction).
- Amounts and prices are serialized back to JSON as **strings**
  (e.g. `"25.00"`), never as JSON numbers, so the output side cannot be
  corrupted by a downstream consumer that parses JSON numbers as float64.
- The decimal context precision is raised to 60 significant digits
  (`decimal.getcontext().prec = 60`) so that `amount - price` on
  high-precision values doesn't lose digits to context rounding.
  Construction from a string/int/bare-JSON-number is always exact
  regardless of context; only the `-` in the delta calculation is
  affected.
- Amounts are formatted with `format(d, "f")`, not `str(d)`, to force
  plain fixed-point notation (never `"1E-10"` or `"1E+10"`) and to
  normalize `Decimal('-0')` to `"0"`.

## Design decisions worth knowing about

- **A duplicate `task_id` in the tasks export is `MALFORMED_RECORD`, not a
  hard failure.** The *first* occurrence of a `task_id` wins for all
  downstream lookups (price, status); every later occurrence is reported
  as `MALFORMED_RECORD` with a `detail` naming the index of the first
  occurrence, and is otherwise ignored. This is a deliberate choice: the
  fixed set of nine anomaly codes this task specifies has no
  "DUPLICATE_TASK_ID" code, and there is no code reserved for "some rows
  are unusable" either, so treating this as exit 2 (as reward-reconciler
  does for its analogous case) would mean one bad row anywhere in a large
  batch aborts the entire run with no report at all. First-wins plus a
  finding keeps the tool usable on real, messy batch data.
- **Structural failures (`MALFORMED_RECORD`) short-circuit further
  validation of that same record**, unlike `INVALID_AMOUNT`/
  `INVALID_PRICE`, which do not exclude a record from other checks. A
  payout with a bad `amount` still participates in
  `DUPLICATE_PAYOUT_ID`/`DUPLICATE_PAYOUT`/`PAYOUT_WITHOUT_TASK`/
  `PAYOUT_FOR_REFUSED_TASK` (those don't need a valid amount); a task with
  a bad `price` still participates in `PAYOUT_FOR_REFUSED_TASK` (that
  doesn't need a valid price). But a record that is missing a required
  field, or has a bad `task_id`/`payout_id`/`status`, cannot be safely
  cross-referenced at all, so it is excluded from everything once flagged
  `MALFORMED_RECORD`. See Limitation 1 below for the corollary.
- **`DUPLICATE_PAYOUT` suppresses `AMOUNT_ABOVE_PRICE`/
  `AMOUNT_BELOW_PRICE`/`PAYOUT_FOR_REFUSED_TASK`** for that `task_id`: with
  two or more payouts for one task, there is no single unambiguous
  "the payout" to compare against the price or the refused status, so
  only the duplication itself is reported (mirroring reward-reconciler's
  precedent for `AMOUNT_MISMATCH`/`WALLET_MISMATCH`).
- **`PAYOUT_WITHOUT_TASK` is reported once per orphan payout**, not once
  per missing `task_id` -- if three payouts all reference the same
  nonexistent task, that is three separate findings (each payout is
  independently unaccounted for), consistent with `PAYOUT_...` naming a
  per-payout fact. `DUPLICATE_PAYOUT`/`DUPLICATE_PAYOUT_ID`, by contrast,
  are reported once per task_id/payout_id group, since those codes name a
  fact about the *group*.
- **Tolerance is a strict inequality.** A payout is flagged only if
  `|amount - price|` is *strictly greater than* `--tolerance`; a delta
  exactly equal to the tolerance is treated as within tolerance and NOT
  flagged. Tested explicitly at the exact boundary in both directions
  (`test_tolerance_boundary_exactly_equal_not_flagged` /
  `test_tolerance_boundary_one_cent_over_flags` /
  `..._under_flags`).
- **A refused task with no payout at all is not an anomaly.** Only a
  payout that actually exists against a refused task triggers
  `PAYOUT_FOR_REFUSED_TASK`; a task simply being refused is expected,
  normal data.
- **Zero is a valid price and a valid amount.** Only negative values (and
  non-finite/non-numeric values) are rejected as `INVALID_PRICE`/
  `INVALID_AMOUNT`.
- **`price`/`amount` as a JSON string and as a bare JSON number are
  fully interchangeable** -- `"25.00"` and `25.00` (and `25`) all parse to
  equal `Decimal` values and compare equal to each other, including
  across differing trailing-zero representations (`Decimal("99.990000")
  == Decimal("99.99")`).

## Exit codes

| code | meaning |
|------|---------|
| 0 | clean, no anomalies |
| 1 | anomalies found (one or more findings) |
| 2 | invalid input / usage error (bad file, invalid JSON, non-array top level, bad `--tolerance`, missing CLI argument) |

## Determinism guarantees

- Every JSON number is parsed via `Decimal`, never `float`.
- Findings are always sorted by `(code, task_id, payout_id, source,
  index)`, with missing `task_id`/`payout_id`/`source` treated as empty
  string and missing `index` treated as `-1` for sort purposes only (the
  actual output value is still `null`). This sort key is independent of
  the order records appear in the input files.
- Output is `sort_keys=True`, `separators=(",", ":")`, `ensure_ascii=True`,
  with a single trailing newline.
- No wall-clock timestamps, random values, hostnames, or other
  runtime-dependent fields ever appear in the report.

## Usage

```
python3 reconcile_anomaly.py TASKS.json PAYOUTS.json
python3 reconcile_anomaly.py TASKS.json PAYOUTS.json -o report.json
python3 reconcile_anomaly.py TASKS.json PAYOUTS.json --output report.json
python3 reconcile_anomaly.py TASKS.json PAYOUTS.json --tolerance 0.01
cat TASKS.json | python3 reconcile_anomaly.py - PAYOUTS.json    # either side may read from stdin via '-'
```

## Reproduce everything

```
python3 -m unittest test_reconcile_anomaly -v
python3 reconcile_anomaly.py tasks_ok.json payouts_ok.json ; echo "exit=$?"
python3 reconcile_anomaly.py tasks_bad.json payouts_bad.json -o report_run1.json ; echo "exit=$?"
python3 reconcile_anomaly.py tasks_bad.json payouts_bad.json -o report_run2.json ; echo "exit=$?"
sha256sum report_run1.json report_run2.json
cmp report_run1.json report_run2.json && echo BYTE-IDENTICAL
python3 reconcile_anomaly.py tasks_bad.json payouts_bad.json --tolerance 1000000 ; echo "exit=$?"
python3 reconcile_anomaly.py /nonexistent.json payouts_ok.json ; echo "exit=$?"
python3 reconcile_anomaly.py tasks_ok.json ; echo "exit=$?"
```

(`report_run1.json` / `report_run2.json` are scratch output and are not
shipped in this package.)

Full real captured output of every command above, plus the precision
demonstration, is in `captured_output.txt`.

## Test suite

`test_reconcile_anomaly.py`: **143 tests**, `Ran 143 tests ... OK`. Covers:
decimal/money coercion (booleans, null, NaN/Infinity as both bare tokens
and quoted strings, negative values, scientific notation, high-precision
strings); ISO-8601 timestamp validation; task and payout structural
parsing (missing fields, bad ids, bad status, duplicate `task_id`);
`reconcile()` for every one of the nine anomaly codes individually and in
combination (including refused+above-price on the same payout, duplicate
payouts suppressing amount/refused checks, invalid amount still flagging
refused, invalid price skipping the amount comparison); canonical-JSON
properties (sorted keys, single trailing newline, no extraneous
whitespace, byte-identical repeats, order-independence); CLI subprocess
exit codes for every path (clean, anomalies, bad JSON, missing file,
missing argument, non-array top level, `-o`/`--output` both forms,
`--tolerance` including the negative-rejection and boundary cases); and
the precision demonstration both as a direct `Decimal` construction check
and end-to-end through a real JSON file via `json.loads(...,
parse_float=Decimal)`.

## Precision demonstration (no float corruption)

```
task price:      123456789012345678.123456789   (27 significant digits, given as a bare JSON number)
payout amount:    123456789012345678.123456789   (same, bare JSON number)
```

Result: no findings, `status: "clean"`, exit code **0**. If the amount had
been round-tripped through a 64-bit float (i.e. `Decimal(str(x))` applied
*after* `json.loads` had already parsed the literal as `float` -- the
naive pattern this task warned about), the value would have silently
corrupted to `1.2345678901234568E+17`. `captured_output.txt` includes both
the clean reconciliation run and a direct demonstration of that exact
corruption for comparison (`test_float_would_have_corrupted_this_value` in
the test suite asserts this corruption is real and that this tool's
parsing path does not reproduce it).

## Bug hunting notes

Adversarial testing (duplicate-id interactions, scientific-notation
prices, `bool`-as-`int` traps, reordering inputs, mixed malformed/valid
records in the same group, first-seen-wins under a duplicated `task_id`
combined with a payout referencing it) did not turn up a defect in
`reconcile_anomaly.py` itself that survived to the final version. It did
catch two genuine bugs -- both in the test suite while it was being
written, not in the tool:

1. `test_tasks_no_payouts_clean` originally built five task records (one
   per status) all using the `task()` helper's default `task_id="t1"`,
   creating an unintended duplicate `task_id` and tripping
   `MALFORMED_RECORD` -- the test was asserting the wrong thing about its
   own fixture, not testing a tool defect. Fixed by giving each record a
   distinct `task_id`.
2. `test_findings_order_independent_of_task_input_order` originally
   reversed *both* the tasks array and the payouts array and asserted
   full byte-identical output. That assertion is actually false by
   design: each finding's `index` field is the record's position in its
   source array, which is real information about the input file, not an
   artifact of processing order -- reversing the payouts array legitimately
   changes which `index` each finding reports. Fixed by only reversing the
   tasks array in the byte-identical assertion (tasks are looked up by
   key, so their order never affects output when there are no duplicate
   `task_id`s) and adding a second test that reverses payouts and checks
   the `(code, task_id, payout_id)` set/order is unaffected even though
   the `index` values differ.

Both are now regression tests. No corresponding fix was needed in
`reconcile_anomaly.py`.

## Limitations a reviewer should scrutinise

1. **A single malformed field can hide other real problems on the same
   record.** `MALFORMED_RECORD` short-circuits further validation of that
   record (see "Design decisions"): a payout with both an unparsable `at`
   *and* a garbage `amount` is reported only once, as `MALFORMED_RECORD`
   for the `at` problem -- the independently-true `INVALID_AMOUNT` fact
   about the same record is never surfaced. A reviewer skimming
   `finding_counts` could undercount how many amount problems actually
   exist in a batch with many malformed rows.
2. **First-seen-wins for a duplicated `task_id` is a real, opinionated
   design choice, not a neutral default.** If a tasks export legitimately
   has two conflicting definitions of the same `task_id` (e.g. a price
   correction meant to supersede an earlier row), this tool always keeps
   the first one and reports the second as `MALFORMED_RECORD` -- it never
   uses the "corrected" value, and there is no code path to configure
   last-wins instead. A reviewer relying on file order to encode "latest
   wins" will get answers computed against the *original* price.
3. **`PAYOUT_WITHOUT_TASK` and the amount/refused/duplicate checks only
   ever see the *raw* string equality of `task_id`.** There is no
   normalization (case-folding, whitespace-trimming beyond the
   empty-string check, unicode normalization) of `task_id`/`payout_id`
   values. Two ids that a human would consider "the same" but differ in
   case or in invisible whitespace are treated as entirely unrelated,
   which could produce a false `PAYOUT_WITHOUT_TASK` (or hide a real
   `DUPLICATE_PAYOUT`) if the two export systems that produced the tasks
   file and the payouts file don't normalize ids identically upstream.
