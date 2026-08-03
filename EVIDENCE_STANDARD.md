# EVIDENCE_STANDARD.md

The output contract every tool in this repository is expected to honour.

This document is derived from the committed code, not from intent. Every rule
below cites the file that implements it. Where tools disagree with each other,
the disagreement is recorded as a **Divergence** rather than smoothed over —
a standard that describes code which does not exist is worse than no standard.

Scope: this is the machine-facing output contract. Contributor workflow lives
in [CONTRIBUTING.md](CONTRIBUTING.md); how to review a submission lives in the
reviewer guide.

---

## 1. Canonical JSON

Every report is serialised with sorted keys, tight separators, ASCII escaping,
and exactly one trailing newline.

Reference implementation — `consolidate/consolidate.py`, `canonical_dumps`:

```python
def canonical_dumps(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n"
```

`nondeterminism-scanner/ndscan.py` and `regression-checker/regress.py` both
define a byte-identical function under the name `canonical_json`.

`schema-checker/schema_check.py` splits the same contract across two
functions — the newline is added by the caller, not the encoder:

```python
def canonical(obj) -> str:
    """Canonical, deterministic JSON encoding used for output and for value equality."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)

def render(report: dict) -> str:
    """Canonical JSON text plus a trailing newline."""
    return canonical(report) + "\n"
```

Both shapes satisfy the contract. New tools should prefer the single-function
form, because the split form makes it possible to call `canonical()` and forget
the newline.

**Why each setting matters.** `sort_keys=True` removes dict insertion order from
the output. `separators=(",",":")` removes whitespace, which otherwise varies
with nesting helpers. `ensure_ascii=True` removes any dependence on the
platform's Unicode handling and makes the file safe to diff and hash on any
system. The trailing newline makes the file a well-formed POSIX text file so
`sha256sum`, `cmp`, and `diff` behave predictably.

### 1.1 Writing the report

Write with an explicit newline translation so Windows checkouts cannot silently
convert `\n` to `\r\n` and change the hash:

```python
with open(args.output, "w", encoding="utf-8", newline="\n") as fh:
    fh.write(text)
```

That form is used by `consolidate/consolidate.py`,
`nondeterminism-scanner/ndscan.py`, and — with `encoding="ascii"` —
`schema-checker/schema_check.py`.

> **Divergence (open).** `regression-checker/regress.py` writes its report with
> `open(args.output, "w", encoding="utf-8")` and does **not** pass
> `newline="\n"`. On Linux and macOS this is identical; on a Windows checkout it
> would emit CRLF and produce a different SHA-256 than the same run on Linux.
> New tools must pass `newline="\n"`. `regress.py` should be brought into line;
> until it is, do not treat its output hash as cross-platform stable.

---

## 2. Exit codes

Three codes, and the 1-vs-2 split is the load-bearing part.

| Code | Meaning |
|-----:|---------|
| `0` | The run completed and found nothing to report. |
| `1` | The run completed and produced findings. |
| `2` | The run could not be completed — bad input, bad usage, or unwritable output. |

`consolidate/consolidate.py` names them:

```python
EXIT_NO_FINDINGS = 0
EXIT_FINDINGS = 1
EXIT_USAGE_ERROR = 2
```

and selects between the first two purely on finding count:

```python
total_findings = len(kept)
exit_code = EXIT_NO_FINDINGS if total_findings == 0 else EXIT_FINDINGS
```

`nondeterminism-scanner/ndscan.py` collapses the same decision to one line, and
notably treats a per-file scan error as a finding rather than a setup failure:

```python
return 1 if (findings or errors) else 0
```

**`1` and `2` must never be conflated.** "The data has problems" and "the
pipeline could not run" are different incidents with different owners. A CI job
that treats any non-zero as the same thing will page the wrong person. A tool
that returns `1` when it actually failed to read its input is reporting a clean
scan of nothing, which is a false negative dressed as a result.

Reserve `2` for: missing or unreadable input, malformed input the tool cannot
parse at all, unknown or invalid CLI arguments, and an unwritable `--output`
path. Everything the tool successfully *analysed* and disliked is `1`.

---

## 3. Deterministic ordering

`sort_keys=True` orders dictionary keys. It does **not** order list items. Every
list emitted in a report therefore needs its own explicit sort, or the report is
only accidentally reproducible.

What the committed tools actually do:

`consolidate/consolidate.py`:

```python
def _finding_sort_key(f):
    return (
        f["source_tool"],
        f["source_report"],
        f["task_id"] if f["task_id"] is not None else "",
        f["code"],
        f["severity"],
        f["detail"],
    )
```

`schema-checker/schema_check.py`:

```python
def sort_entries(entries: list) -> list:
    """Deterministic ordering: (pointer, code, message)."""
    return sorted(entries, key=lambda e: (e["pointer"], e["code"], e["message"]))
```

`nondeterminism-scanner/ndscan.py`:

```python
    def sort_key(self):
        return (self.rule_id, self.path, self.line, self.col, self.detail)
```

Each key ends in a free-text field (`detail` or `message`), which in practice
distinguishes findings that agree on every preceding field.

> **Divergence (open).** A stronger convention is sometimes described for this
> repository: append the canonical JSON dump of the item as a final sort key, so
> the ordering is a guaranteed total order regardless of field content. **No
> committed tool currently does this.** All three keys above are field tuples.
> They are a total order only if no two distinct findings share every field
> including `detail` — which is true for current fixtures but is not enforced
> anywhere and is not guaranteed by construction.
>
> This standard therefore requires only the field-tuple sort, because that is
> what the code supports. Adding the canonical dump as a terminal tiebreak is
> **recommended for new tools** and is the safe choice; it cannot change the
> order of items that already differ, and it removes the unenforced assumption
> for items that do not. Do not describe the canonical-dump tiebreak as existing
> repository behaviour until a tool actually implements it.

`ndscan.py` also reuses the sort key for de-duplication, which is worth copying —
it makes "these two findings are the same" and "these two findings tie" the same
question:

```python
findings = list({f.sort_key(): f for f in findings}.values())
findings.sort(key=lambda f: f.sort_key())
```

---

## 4. Timing: never read the clock

A tool that reads the wall clock cannot be re-run to the same output tomorrow,
which makes every hash in its evidence unreproducible.

Any tool whose logic depends on "now" takes it as a **required** argument.

`staleness-monitor/staleness.py`:

```python
parser.add_argument(
    "--now",
    required=True,
    help="UTC reference time in ISO-8601 (e.g. 2026-08-02T00:00:00Z). Required; never read from the system clock.",
)
```

`loop-health/loop_health.py`:

```python
parser.add_argument(
    "--now",
    required=True,
    help=(
        "UTC reference moment in ISO-8601 (e.g. 2026-08-03T00:00:00Z). Required; "
        "this value is never defaulted and the wall clock is never consulted."
    ),
)
```

`required=True` is deliberate and should be copied. A `--now` that defaults to
the current time is not an injected clock; it is a wall-clock read with extra
steps, and it will silently produce irreproducible output the first time someone
omits the flag.

Both modules import only `from datetime import datetime, timedelta, timezone` —
the `time` module is absent entirely.

**Keep the substrings out of the file, not just out of the call graph.**
Reviewers grep for `time.time`, `utcnow`, and `now()`. None of those three
substrings appear anywhere in `staleness.py` or `loop_health.py`, including in
comments and docstrings. A commented-out `# datetime.utcnow()` costs you a
review round for no benefit.

Related: reports must not embed wall-clock-derived or machine-derived values —
run durations, absolute filesystem paths, hostnames, usernames, or file mtimes.
Each one makes the output differ between two correct runs.

---

## 5. Money and Decimal

Binary floats cannot represent decimal settlement values exactly. Anywhere an
amount is handled, floats are rejected rather than coerced.

The full-strength form, `wallet-reconciler/wallet_reconcile.py`, `_load_json`:

```python
return json.loads(text, parse_float=Decimal, parse_constant=_NonFinite)
```

Two things are happening, and both are necessary:

**`parse_float=Decimal`** converts at the parse boundary, from the original
token text. This is the part that is easy to get wrong. The common idiom
`Decimal(str(x))` applied *after* a normal `json.loads` is already too late —
`json.loads` has by then built a binary float, and `str()` renders that float's
value, not the digits the file actually contained. The corruption is silent.

**`parse_constant=_NonFinite`** intercepts the bare `NaN`, `Infinity`, and
`-Infinity` tokens, which `json.loads` otherwise accepts and hands back as
floats:

```python
class _NonFinite:
    """Sentinel for a JSON NaN/Infinity/-Infinity literal.

    json.loads() accepts these tokens by default via parse_constant, handing
    back float('nan') / float('inf') / float('-inf'). We intercept that and
    wrap the raw token instead of ever building a Decimal('NaN') or float:
    ...
    """
```

Rejection is then explicit, in `_parse_finite_decimal`:

```python
if isinstance(raw, _NonFinite):
    return None, f"{subject} is {raw.token} (NaN/Infinity is not permitted)"
```

and, for values that did become Decimals:

```python
if value.is_nan() or value.is_infinite():
    return None, f"{subject} must be finite (NaN/Infinity is not permitted)"
```

Note that `Decimal("NaN")` constructs happily without raising, so a type check
alone does not catch it. The finiteness check is not redundant.

### 5.1 Emitting amounts

Amounts are emitted as **strings**, never as JSON numbers — a JSON number would
be re-parsed as a float by the next consumer and undo the work.

`wallet-reconciler/wallet_reconcile.py`, `_amt_str`:

```python
def _amt_str(d):
    """Canonical string form of a Decimal amount: always plain fixed-point
    (never scientific notation, e.g. never '1E-10'), and -0 normalized to
    0 so a zero-effect event never prints a misleading negative-looking
    balance."""
    if d == 0:
        d = Decimal(0)
    return format(d, "f")
```

`format(d, "f")` rather than `str(d)` is the correct choice: `str()` on a
Decimal can produce scientific notation (`1E-10`), which is a different string
for the same value and therefore a different hash.

> **Divergence (open).** `budget-forecaster/forecast.py` does not use
> `parse_float=Decimal`. It loads with a plain `json.load` and enforces Decimal
> safety with a type gate in `_dec` instead:
>
> ```python
> def _dec(raw, where, field):
>     if isinstance(raw, bool) or not isinstance(raw, (str, int)):
>         raise InputError(f"{where}: '{field}' must be a string or integer, got {type(raw).__name__}")
>     try:
>         v = Decimal(str(raw))
>     except InvalidOperation:
>         raise InputError(f"{where}: '{field}' is not a valid decimal: {raw!r}")
>     if v != v:
>         raise InputError(f"{where}: '{field}' is NaN")
> ```
>
> This is *sound* — a JSON float arrives as a Python `float`, fails the
> `isinstance(raw, (str, int))` gate, and is rejected outright rather than
> coerced, so no float ever reaches `Decimal`. The `v != v` line catches
> `Decimal("NaN")`, which `InvalidOperation` does not. But it is a different
> mechanism from `wallet_reconcile.py`, and it rejects rather than accepts
> `1.5`-style JSON numbers.
>
> It also emits with `str(...)` rather than `format(d, "f")`, so a sufficiently
> small or large magnitude could render in scientific notation where
> `wallet_reconcile.py` would not.
>
> New tools should follow `wallet_reconcile.py`. `forecast.py`'s approach is
> acceptable where the input format specifies string/integer amounts, but do not
> cite it as the repository pattern.

---

## 6. `captured_output.txt`

Each tool directory ships a `captured_output.txt` — a plain terminal transcript
of a real run, committed alongside the code. It is not a manifest format and has
no schema; it is evidence that the commands in the README were actually executed
and produced what the README claims.

Using `budget-forecaster/captured_output.txt` as the reference, the transcript
contains, in order:

1. **The verbose test run**, beginning with its own invocation line and ending
   in the unittest summary:

   ```
   === $ python3 -m unittest test_forecast -v ===
   test_breach_detected (test_forecast.TestBudgetCap) ... ok
   test_exactly_at_cap_is_not_a_breach (test_forecast.TestBudgetCap)
   Spending the whole budget is not overspending it. ... ok
   ```

2. **Each documented CLI invocation**, prefixed `=== $ <command> ===`, followed
   by the tool's real stdout and an explicit `exit=<code>` annotation.

3. **A determinism proof** — two independently generated report files hashed and
   compared:

   ```
   === $ sha256sum forecast_run1.json forecast_run2.json ===
   6cf2662f5ff02f9efbb31f7ee988694159312057bc2e08e96d71201b85fa0ebe  forecast_run1.json
   6cf2662f5ff02f9efbb31f7ee988694159312057bc2e08e96d71201b85fa0ebe  forecast_run2.json
   === $ cmp ... && echo BYTE-IDENTICAL ===
   BYTE-IDENTICAL
   ```

4. **Error-path runs** against empty, single-record, and nonexistent inputs,
   showing the literal error message and its exit code.

5. **The full canonical JSON** of one generated report.

### 6.1 What the determinism proof does and does not establish

Two runs launched from the same working directory prove that the tool does not
depend on iteration order, hash seeds, or the clock. They do **not** prove the
output is free of location dependence: an absolute path that leaks into the
report is identical in both runs and the hashes still match.

Demonstrating location independence requires running the tool from a **second,
differently-named absolute path** and comparing that hash to the first. A
`captured_output.txt` that shows only same-directory reruns should not be
described as proving path independence.

---

## 7. Checklist for a new tool

- [ ] Report serialised with `sort_keys=True, separators=(",",":"), ensure_ascii=True`, one trailing newline.
- [ ] Output file opened with `newline="\n"`.
- [ ] Exit `0` clean / `1` findings / `2` could-not-run, with `2` never used for findings.
- [ ] Every emitted list explicitly sorted; sort key documented.
- [ ] No wall-clock read; `--now` is `required=True` if time matters.
- [ ] No absolute paths, durations, hostnames, or mtimes in the report.
- [ ] Amounts parsed at the boundary with `parse_float=Decimal`, non-finite tokens rejected, emitted as strings via `format(d, "f")`.
- [ ] Every finding carries a code and a location.
- [ ] Malformed records are reported and skipped, not fatal.
- [ ] `captured_output.txt` committed, containing the verbose test run, each documented command with its exit code, and a two-run hash comparison.
- [ ] README states exit-code meanings, the exact rerun command, and honest limitations.

---

## Known gaps in this standard

Recorded so a reviewer does not have to rediscover them:

1. **The canonical-dump tiebreak is aspirational, not implemented** (§3). No
   committed tool uses it. Field-tuple sorts are a total order only under an
   assumption nothing enforces.
2. **`regress.py` writes without `newline="\n"`** (§1.1), so its report hash is
   not guaranteed stable across platforms.
3. **Decimal handling is not uniform** (§5) — `wallet_reconcile.py` and
   `forecast.py` use different, individually sound mechanisms.
4. **`captured_output.txt` has no schema.** It is a human-readable transcript.
   Nothing validates that the commands in it match the commands in the README,
   so the two can drift.
5. **This standard is not enforced by CI.** It is prose. `doc-validator` checks
   README/CLI agreement and `nondeterminism-scanner` catches some clock and
   ordering risks, but no tool checks a directory against this document as a
   whole.
