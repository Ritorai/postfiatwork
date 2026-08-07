# Wallet Ledger Reconciliation CLI

Standard-library Python 3 only. No third-party packages, no network access.

## What this tool does

Replays a wallet ledger -- an opening balance, a chronological list of
events (`reward`, `grant`, `airdrop`, `chat_spend`), and a stated closing
balance -- and verifies that the arithmetic actually adds up. It produces:

- a **per-event running-balance trace** (the audit trail: exactly where the
  balance was after each event, and why), and
- a **findings list** of anything wrong with the ledger (bad data, ordering
  problems, balance violations, or a closing-balance mismatch).

The report is canonical JSON: identical input always produces byte-identical
output, so it's diffable and safe to store as evidence.

## Relationship to reward-reconciler

This tool extends the two-file expected/payout diff tool at
`reward-reconciler/reconcile.py` into a single-file chronological ledger
replay. What was reused vs. new:

**Reused (same shape, same reasoning):**
- Overall module structure: an `InputError` exception for exit-2 cases, a
  pure `reconcile()` function with no I/O (directly unit-testable), a
  `canonical_json()` serializer, and a thin `main()`/`argparse` wrapper.
- The exit-code contract: 0 = clean, 1 = findings, 2 = invalid input.
- The canonical JSON contract: `sort_keys=True`,
  `separators=(",", ":")`, `ensure_ascii=True`, one trailing newline.
- The "findings are always fully sorted, independent of input order"
  determinism guarantee, and the `-o` output-to-file option.
- Parsing amounts exclusively through `decimal.Decimal`, never `float`.

**New in this tool:**
- A single unified ledger document (opening balance + ordered events +
  closing balance) instead of two independent unordered record arrays --
  this requires *sequential*, order-dependent processing (running balance,
  timestamp ordering) rather than reward-reconciler's set-based diff.
- `json.loads(..., parse_float=Decimal, parse_constant=...)` so JSON number
  literals are parsed directly from source text, never round-tripped
  through a 64-bit float (see "Precision" below). reward-reconciler only
  ever received amounts as strings/ints via manual `Decimal(str(raw))`,
  which does not have this problem in the first place but also does not
  need to handle bare JSON number literals or NaN/Infinity tokens.
  reward-reconciler quantizes every amount to a **fixed 6-decimal-place
  scale**; this tool intentionally does **not** quantize -- wallet amounts
  can carry more precision than 6dp (see the precision demo below), so
  amounts here keep whatever precision the input gave them.
- The `_NonFinite` sentinel for NaN/Infinity handling (not needed in
  reward-reconciler, which never parsed raw JSON with `parse_float`/
  `parse_constant` overrides).
- All seven finding codes (`DUPLICATE_EVENT_ID`, `OUT_OF_ORDER_TIMESTAMP`,
  `NEGATIVE_RUNNING_BALANCE`, `CLOSING_BALANCE_MISMATCH`,
  `UNKNOWN_EVENT_TYPE`, `INVALID_AMOUNT`, `INVALID_TIMESTAMP`) and the sign
  convention / running-balance logic are new.
- The per-event `trace` array (reward-reconciler has no equivalent -- it
  diffs two unordered sets, there is no "running" anything to trace).
- `_amt_str()`, which forces plain fixed-point decimal formatting
  (`format(d, "f")`) instead of `str(Decimal)`, to avoid scientific
  notation on very small deltas -- see "Bug found" below.

## Sign convention

Amounts in the input are always given as **positive magnitudes**. The
event's `"type"` determines the sign applied to the running balance:

| type         | effect on balance |
|--------------|--------------------|
| `reward`     | `+amount`          |
| `grant`      | `+amount`          |
| `airdrop`    | `+amount`          |
| `chat_spend` | `-amount`          |

A negative amount in the input is **never** treated as an implicit debit --
it is always rejected as `INVALID_AMOUNT`. This is a deliberate design
choice: allowing a negative `chat_spend` amount to silently become a credit
(double negative) would be a much more dangerous bug than simply refusing
to guess.

## Money handling

- All amounts are parsed with `decimal.Decimal` exclusively. `float` is
  never used anywhere in the money path.
- `json.loads(text, parse_float=Decimal, parse_constant=_NonFinite)`: JSON
  number literals like `123456789012345678.123456789` are built directly
  from the source text by the `Decimal` constructor, never passed through
  an intermediate 64-bit `float`. This is the exact bug this task warned
  about (`Decimal(str(json_parsed_float))` silently truncates to ~17
  significant digits); this tool never does that round-trip.
- Bare `NaN`/`Infinity`/`-Infinity` JSON tokens (which `json.loads` accepts
  by default) are intercepted via `parse_constant` into an opaque
  `_NonFinite` sentinel -- never turned into `Decimal('NaN')`, because
  `Decimal('NaN') == Decimal('NaN')` is `False` and `Decimal('NaN') < 0` is
  also `False`, which would silently defeat every downstream comparison and
  make invalid data look clean. NaN/Infinity given as a *quoted string*
  (`"NaN"`, `"Infinity"`) is also caught, via an explicit
  `.is_nan()`/`.is_infinite()` check after construction (since
  `Decimal("NaN")` does not raise).
- Amounts are serialized back to JSON as **strings**
  (e.g. `"1234.567890"`), never as JSON numbers, so the output side cannot
  be corrupted by a downstream consumer that parses JSON numbers as float64.
- The decimal context precision is raised to 60 significant digits
  (`decimal.getcontext().prec = 60`) so that summing many high-precision
  amounts over a long ledger doesn't lose digits to context rounding on
  `+`/`-`. (Construction from a string/int is always exact regardless of
  context; only arithmetic operators are affected.)
- Amounts are formatted with `format(d, "f")`, not `str(d)`, to force plain
  fixed-point notation and rule out scientific notation (see "Bug found").

## Input shape

```json
{
  "opening_balance": "100.500000",
  "closing_balance": "5.123456789",
  "events": [
    {"event_id": "e1", "type": "reward", "amount": "50", "at": "2026-01-01T00:00:00Z"}
  ]
}
```

- `opening_balance` / `closing_balance`: string or JSON number. May be
  **negative** (an already-overdrawn wallet is valid input -- see
  `NEGATIVE_RUNNING_BALANCE` below). Must be finite; NaN/Infinity here is a
  **structural** error (exit 2), not a finding, since the tool cannot
  produce a meaningful report without a usable starting/ending balance.
- `events`: an array, processed **in the order given** (the input order
  *is* the ledger's claimed chronological order -- events are never
  re-sorted by timestamp before replay). Each event requires all four
  keys `event_id`, `type`, `amount`, `at`.
  - `event_id` must be a non-empty string (checked at parse time; there is
    no finding code for a broken identifier since it is used as the join
    key for duplicate detection and the audit trace throughout the report).
  - `type`, `amount`, `at` are always accepted structurally (any JSON type)
    and instead validated *semantically*, producing
    `UNKNOWN_EVENT_TYPE` / `INVALID_AMOUNT` / `INVALID_TIMESTAMP`
    respectively when wrong. This keeps the audit trace complete even when
    individual fields are garbage -- an event with a bad amount still shows
    up in the trace (as `"applied": false`), it just doesn't move the
    balance.
  - `amount` accepts both a JSON string (`"50"`) and a JSON number
    (`50` or `50.5`), and must be a non-negative magnitude.
  - `at` must be an ISO-8601 timestamp explicitly denoting UTC, either via
    a `Z` suffix or a `+00:00`/`-00:00` offset. Naive timestamps and
    non-UTC offsets are rejected as `INVALID_TIMESTAMP`.

Any missing top-level or event key, a non-object top level/event, a
non-array `events`, or a non-string/empty `event_id` is a **structural**
error: exit 2, no report produced. Everything else produces a report with
per-event findings and exit 0/1.

## Finding codes

| code | meaning | key fields |
|---|---|---|
| `DUPLICATE_EVENT_ID` | an `event_id` seen before | `event_id`, `index`, `first_index` |
| `OUT_OF_ORDER_TIMESTAMP` | `at` earlier than the immediately preceding (timestamp-valid) event's `at` | `event_id`, `index`, `at`, `previous_event_id`, `previous_at` |
| `NEGATIVE_RUNNING_BALANCE` | running balance below zero after this event, or the opening balance itself is negative | `event_id` (`null` for the opening-balance case), `index` (`-1` for opening), `balance`, `context` |
| `CLOSING_BALANCE_MISMATCH` | computed closing balance != stated closing balance | `computed_closing_balance`, `stated_closing_balance`, `delta` (`computed - stated`) |
| `UNKNOWN_EVENT_TYPE` | `type` isn't reward/grant/airdrop/chat_spend | `event_id`, `index`, `type` |
| `INVALID_AMOUNT` | non-numeric, NaN/Infinity, negative, null, or boolean amount | `event_id`, `index`, `amount`, `detail` |
| `INVALID_TIMESTAMP` | `at` isn't a valid UTC ISO-8601 timestamp | `event_id`, `index`, `at`, `detail` |

An event with an unknown type or an invalid amount is **not applied** to
the running balance (the balance carries forward unchanged) -- there is no
safe sign/magnitude to apply. An event with an invalid timestamp **is**
still applied (the amount/type are independently valid; only the ordering
check is skipped for that event, and it does not update the "previous
timestamp" cursor used for the next comparison).

A duplicate `event_id` is still applied on every occurrence (the ledger
doesn't get to unilaterally decide which of two same-ID entries is "real";
omitting one would hide information from the audit trail), it's just
flagged so a human investigates.

## Design decisions worth knowing about

- **Same-timestamp events are not out-of-order.** Two events at the exact
  same instant (e.g. simultaneous settlement) are common and not
  inherently an error; only a *strictly earlier* timestamp than the
  immediately preceding event triggers `OUT_OF_ORDER_TIMESTAMP`.
- **Out-of-order detection compares adjacent pairs, not a running
  watermark.** Per the spec wording ("earlier than the previous event's"),
  event N is compared only to event N-1, not to the maximum timestamp seen
  so far. See Limitation 1 below.
- **Zero-amount events are valid**, not `INVALID_AMOUNT` (magnitude `>= 0`
  is accepted, only `< 0` is rejected). A `chat_spend` that brings the
  balance to exactly zero is not `NEGATIVE_RUNNING_BALANCE` (the check is
  strictly `< 0`).
- **Opening balance is checked as a virtual "index -1" event.** If
  `opening_balance` itself is negative, that's reported as
  `NEGATIVE_RUNNING_BALANCE` with `event_id: null`, `index: -1`,
  `context: "opening_balance"` -- consistent with the spec's "at any point"
  wording rather than only checking after events.

## Exit codes

| code | meaning |
|------|---------|
| 0 | reconciled, no findings |
| 1 | findings (ledger did not reconcile cleanly) |
| 2 | invalid input / usage error |

## Determinism guarantees

- Every JSON number is parsed via `Decimal`, never `float` (both amounts
  and, indirectly, the top-level balances, which share the same JSON
  decoder configuration).
- Findings are always sorted by `(index, code)`, with the opening-balance
  virtual check at `index -1` sorting first and `CLOSING_BALANCE_MISMATCH`
  (which has no index -- it's a whole-ledger check) sorting last.
- Output is `sort_keys=True`, `separators=(",", ":")`, `ensure_ascii=True`,
  with a single trailing newline.

## Usage

```
python3 wallet_reconcile.py LEDGER.json
python3 wallet_reconcile.py LEDGER.json -o report.json
python3 wallet_reconcile.py LEDGER.json --output report.json
cat LEDGER.json | python3 wallet_reconcile.py -    # read from stdin
```

## Reproduce everything

```
python3 -m unittest test_wallet_reconcile -v
python3 wallet_reconcile.py ledger_ok.json ; echo "exit=$?"
python3 wallet_reconcile.py ledger_bad.json -o report_run1.json ; echo "exit=$?"
python3 wallet_reconcile.py ledger_bad.json -o report_run2.json ; echo "exit=$?"
sha256sum report_run1.json report_run2.json
cmp report_run1.json report_run2.json && echo BYTE-IDENTICAL
python3 wallet_reconcile.py /nonexistent.json ; echo "exit=$?"
echo '{"opening_balance":"0","closing_balance":"0","events":[{"event_id":"e1","type":"reward","amount":NaN,"at":"2026-01-01T00:00:00Z"}]}' | python3 wallet_reconcile.py - ; echo "exit=$?"
```

(`report_run1.json` / `report_run2.json` are scratch output and are not
shipped in this package.)

Full real captured output of every command above, plus the precision
demonstration, is in `captured_output.txt`.

## Duplicate object keys are refused

A JSON object may legally contain the same member name twice. RFC 8259
says names SHOULD be unique and leaves the rest to the implementation;
Python's `json` keeps the **last** value. So a ledger could say

```
{"event_id": "e1", "type": "reward", "amount": "9999", "amount": "10", ...}
```

and this tool would see one amount, chosen by the parser, with nothing
downstream able to know a choice had been made. Both orientations were
bad and the quiet one was worse: with the wrong value second the tool
reported a balance discrepancy that was really an ambiguous file, and
with the wrong value first it exited `0` and called the document
`reconciled`.

Any repeated key now exits **2** with a message naming the key and, when
the object has one, its `event_id` — anywhere in the document, for keys
this tool never reads, and even when the two values agree, because the
question is whether the file said a thing once, not which value a reader
would have preferred. Two *different* events each carrying an `amount`
is not a duplicate and is unaffected.

Exit `2` and not `1`: the input could not be read unambiguously, which
is a fact about the caller's file rather than a finding about the ledger
it describes. Valid input is untouched — `ledger_ok.json` and
`ledger_bad.json` produce byte-identical reports and the same exit codes
as before.

- `ledger_duplicate_key.json` — the minimal reproducer, in the quiet
  orientation.
- `DUPLICATE_KEY_EVIDENCE.txt` — the parent commit accepting it, this
  version refusing it, valid input byte-compared across both, and
  SHA-256 hashes of every fixture and captured output.

## Test suite

`test_wallet_reconcile.py`: **159 tests**, `Ran 159 tests ... OK`. Covers
amount coercion (all type/edge cases), timestamp coercion, each finding
code individually and in combination, structural input errors, canonical
JSON properties (sorted keys, trailing newline, byte-identical repeats,
deterministic finding order), CLI subprocess exit codes for every path
(stdin, `-o`/`--output`, missing file, malformed JSON, usage error, NaN via
stdin), and the precision/negative-zero regressions described below.

## Precision demonstration (no float corruption)

```
opening_balance:  0
reward amount:    123456789012345678.123456789   (27 significant digits, given as a bare JSON number)
closing_balance:  123456789012345678.123456789
```

Result: `computed_closing_balance` == `123456789012345678.123456789`
exactly, `closing_delta` == `"0"`, exit code **0**. If the amount had been
round-tripped through a 64-bit float (as `Decimal(str(json_parsed_float))`
would do), the value would have silently corrupted to
`123456789012345680` or similar -- `test_float_would_have_corrupted_this_value`
in the test suite demonstrates this exact corruption on the same string and
asserts our coercion path does not reproduce it.

## Bug found during development

`_amt_str()` (used for every amount printed in the report) originally used
plain `str(decimal_value)`. Two real problems surfaced from the tests, not
from inspection:

1. **Scientific notation on small deltas.** `Decimal("10.0000000001") -
   Decimal("10")` is `Decimal('1E-10')`, and `str()` of that is the literal
   string `"1E-10"`. A `CLOSING_BALANCE_MISMATCH` delta (or any small
   running-balance value) could therefore render as `"-1E-10"` instead of
   `"-0.0000000001"` -- technically correct but not the human-auditable
   output the spec asks for ("an unexplained mismatch verdict is useless").
   Fixed by formatting with `format(d, "f")`, which forces fixed-point
   notation regardless of magnitude.
2. **Negative zero.** `Decimal("0") * -1` is `Decimal('-0')`, and
   `str(Decimal('-0'))` is `"-0"`. This is reachable from ordinary input: a
   zero-amount `chat_spend` against a zero balance produces exactly this.
   `Decimal('-0') < 0` is `False`, so it never incorrectly triggers
   `NEGATIVE_RUNNING_BALANCE` -- but the *printed* balance was still the
   misleading string `"-0"`. Fixed by normalizing any value that compares
   equal to zero to `Decimal(0)` before formatting.

Both are covered by regression tests (`test_tiny_delta_flagged`,
`test_zero_amount_chat_spend_from_zero_balance_prints_positive_zero`,
`TestAmtStr`).

## Limitations a reviewer should scrutinise

1. **Out-of-order detection is adjacent-pair only, not a running
   watermark.** Given timestamps `[05, 01, 02]` (dates abbreviated), the
   tool flags index 1 (`01` earlier than `05`) but *not* index 2 (`02`),
   even though `02` is also earlier than the very first event `05`. This
   follows the spec's literal wording ("earlier than the *previous*
   event's"), but a reviewer who expects "earlier than anything already
   seen" will find gaps in detection for ledgers with more than one
   out-of-order run. Tested explicitly in
   `test_out_of_order_compares_only_to_immediately_preceding_event`.
2. **An event with an invalid timestamp still updates nothing about
   ordering state, but an event with an unknown type or invalid amount
   still consumes the "duplicate ID" and "out-of-order" checks normally.**
   In other words the four validation checks (duplicate, type, amount,
   timestamp) are independent of each other rather than short-circuiting --
   this is intentional (maximizes audit information per event) but means a
   single garbage event can appear in the findings list under multiple
   unrelated codes simultaneously, which could look like more distinct
   problems exist than actually do to a reviewer skimming `finding_counts`
   rather than reading the trace.
3. **Decimal context precision is a fixed ceiling (60 significant
   digits), not unbounded.** Construction from source text is always
   exact, but every `+`/`-` during replay rounds to 60 significant digits.
   A ledger combining an extreme magnitude (e.g. 40+ integer digits) with
   extreme sub-cent precision (e.g. 30+ decimal digits) on the *same*
   running total could theoretically lose precision during summation. This
   is far beyond any realistic wallet ledger (the demonstrated case here is
   27 significant digits, well under the ceiling), but it is a ceiling, not
   arbitrary precision.
