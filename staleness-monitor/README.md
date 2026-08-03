# staleness-monitor

A stdlib-only Python 3 CLI that reads open Task Node records and reports
overdue / stale / malformed tasks as canonical, reproducible JSON.

```
python3 staleness.py TASKS.json --now 2026-08-02T00:00:00Z [options]
```

No third-party packages, no network access, and (this is the load-bearing
property of the whole tool) **no reads of the system clock anywhere in the
report path**. Every age, overdue check, and timestamp comparison is driven
exclusively by the UTC reference time you pass via the required `--now`
argument. Run the tool twice with the same input file and the same `--now`
and you get byte-identical output, forever, regardless of when you actually
run it.

## Installation

None. It's one file (`staleness.py`) that only imports from the Python 3
standard library (`argparse`, `json`, `sys`, `datetime`). Requires Python
3.9+ (uses `datetime.fromisoformat`; tested on 3.10).

## Usage

```
python3 staleness.py INPUT_FILE --now ISO8601_UTC
                      [--accepted-stale-hours HOURS]
                      [--submitted-stale-hours HOURS]
                      [-o OUTPUT_FILE | --output OUTPUT_FILE]
```

| Flag | Required | Default | Meaning |
|---|---|---|---|
| `INPUT_FILE` | yes | -- | Path to a JSON file containing an array of task records. |
| `--now` | yes | -- | UTC reference time, ISO-8601 (`Z` or `+00:00` suffix). Never defaulted, never read from the OS clock. |
| `--accepted-stale-hours` | no | `48` | Hours after `created_at` an `accepted` task becomes stale. |
| `--submitted-stale-hours` | no | `72` | Hours after `created_at` a `submitted` task becomes stale. |
| `-o`, `--output` | no | stdout | Write the JSON report to a file instead of stdout. |

### Exit codes

| Code | Meaning |
|---|---|
| `0` | Input parsed successfully; zero findings (no window breached). |
| `1` | Input parsed successfully; one or more findings were produced. |
| `2` | Invalid input or usage error: missing/unparseable `--now`, missing/unreadable input file, input file is not valid JSON, JSON root is not an array, a task record is missing a required key, or a negative `--*-stale-hours` value. |

## Input shape

The input file must be a JSON array. Every element must be a JSON object
containing exactly these keys (all five keys must be *present*; `deadline`
is the only one allowed to hold `null`):

```json
{
  "task_id": "T-123",
  "title": "Draft the launch memo",
  "status": "proposed",
  "created_at": "2026-07-30T12:00:00Z",
  "deadline": "2026-08-01T00:00:00Z"
}
```

- `task_id` -- any JSON scalar; used verbatim in findings and to sort them.
- `title` -- any JSON scalar; used verbatim in findings.
- `status` -- expected to be one of `"proposed"`, `"accepted"`,
  `"submitted"`. A value outside this set is **not** a usage error: the
  record is simply inert with respect to the three status-specific checks
  (`OVERDUE_PROPOSED`, `STALE_ACCEPTED`, `STALE_SUBMITTED`). It can still
  produce `MALFORMED_CREATED_AT`, `MALFORMED_DEADLINE`, or
  `DEADLINE_BEFORE_CREATED`, since those three checks do not depend on
  `status` at all. Comparison against the allowed set is case-sensitive.
- `created_at` -- ISO-8601 UTC timestamp string (see "Timestamp format"
  below). Required key; if the *value* is missing/unparseable/non-UTC it
  produces `MALFORMED_CREATED_AT` rather than an exit-2 usage error.
- `deadline` -- ISO-8601 UTC timestamp string, or JSON `null` if no
  deadline has been set. If non-null and unparseable/non-UTC it produces
  `MALFORMED_DEADLINE`.

If a record is missing one of the five required *keys* entirely (not just
an empty/bad value), or the JSON root isn't an array, or an array element
isn't an object, the whole run is treated as a usage error: exit code 2,
nothing is written to stdout/`-o`.

### Timestamp format

Accepted:
- A trailing `Z`/`z` with no embedded offset: `2026-08-02T00:00:00Z`
- An explicit zero UTC offset: `2026-08-02T00:00:00+00:00` or `...-00:00`
- Optional fractional seconds: `2026-08-02T00:00:00.500000Z`

Rejected (treated as malformed / non-UTC):
- Any non-zero offset, e.g. `2026-08-02T00:00:00+05:30` -- this is valid
  ISO-8601 but is explicitly **not UTC**, and the finding-code contract
  requires UTC.
- A timezone-naive string with no offset at all, e.g. `2026-08-02T00:00:00`
- Anything `datetime.fromisoformat` cannot parse
- Any non-string JSON value (numbers, booleans, objects, arrays)

## Finding codes

| Code | Fires when | Depends on `status`? |
|---|---|---|
| `OVERDUE_PROPOSED` | `status == "proposed"`, `deadline` parses, and `deadline < --now` (strict). | yes |
| `STALE_ACCEPTED` | `status == "accepted"`, `created_at` parses, and `(--now - created_at) > accepted_stale_hours` (strict). | yes |
| `STALE_SUBMITTED` | `status == "submitted"`, `created_at` parses, and `(--now - created_at) > submitted_stale_hours` (strict). | yes |
| `MALFORMED_DEADLINE` | `deadline` is non-null and fails to parse as a UTC ISO-8601 timestamp (see above). | no |
| `MALFORMED_CREATED_AT` | `created_at` fails to parse as a UTC ISO-8601 timestamp. | no |
| `DEADLINE_BEFORE_CREATED` | Both `created_at` and `deadline` parse successfully, and `deadline < created_at`. | no |

All boundary comparisons are **strict** (`<` / `>`, never `<=` / `>=`): a
deadline exactly equal to `--now`, or an age exactly equal to the
configured stale-hours window, does **not** breach. One second past the
boundary does. This is deliberate and covered by tests
(`test_exactly_at_boundary_no_finding`, `test_one_second_past_boundary_triggers`,
`test_deadline_equal_now_no_finding`, etc).

A single task can produce more than one finding (e.g. a `proposed` task
whose deadline is both before `created_at` and before `--now` produces
both `DEADLINE_BEFORE_CREATED` and `OVERDUE_PROPOSED`).

Checks that fail to parse a timestamp are mutually exclusive with checks
that need that timestamp: if `created_at` is malformed, `STALE_ACCEPTED`,
`STALE_SUBMITTED`, and `DEADLINE_BEFORE_CREATED` are all skipped for that
task (there's nothing valid to compare against); you get
`MALFORMED_CREATED_AT` instead. Same idea for a malformed `deadline` with
respect to `OVERDUE_PROPOSED` and `DEADLINE_BEFORE_CREATED`.

## Urgency buckets

Three buckets: `critical`, `warning`, `info`.

**Data-integrity codes are always `critical`**, regardless of magnitude,
because the record itself cannot be trusted for time math:
`MALFORMED_DEADLINE`, `MALFORMED_CREATED_AT`, `DEADLINE_BEFORE_CREATED`.

**Time-window-breach codes are bucketed by how far past the threshold the
task is** (the "overage"):

- `OVERDUE_PROPOSED` -- overage = `--now - deadline` (only computed once
  positive):
  - `overage >= 24h` -> `critical`
  - `6h <= overage < 24h` -> `warning`
  - `0 < overage < 6h` -> `info`

- `STALE_ACCEPTED` / `STALE_SUBMITTED` -- `age = --now - created_at`,
  `window` = the applicable configured stale-hours, `overage = age - window`
  (only computed once positive, i.e. `age > window`):
  - `overage >= window` (i.e. `age >= 2x window`) -> `critical`
  - `window/2 <= overage < window` (i.e. `1.5x <= age < 2x window`) -> `warning`
  - `0 < overage < window/2` (i.e. `1x < age < 1.5x window`) -> `info`

This means the exact same finding code lands in different buckets
depending on how badly it has blown through the window -- e.g. a
`submitted` task 1 hour past its 72h window is `info`, 50 hours past is
`warning`, and 80 hours past is `critical`.

## Age fields

Every time-based finding (`OVERDUE_PROPOSED`, `STALE_ACCEPTED`,
`STALE_SUBMITTED`, `DEADLINE_BEFORE_CREATED`) carries:

- `age_seconds` -- the exact age/overage, in whole seconds (`int(round(...))`
  of the underlying `timedelta.total_seconds()`). Can be very large; never
  omitted, never rounded to a coarser unit.
- `age_human` -- the same duration rendered as `"<sign><d>d <h>h <m>m"`,
  e.g. `"3d 1h 0m"` for 262800 seconds. Sub-minute remainders are truncated
  (the exact remainder lives in `age_seconds`, not here). A negative
  duration (see "future `created_at`" below) is prefixed with `-`, e.g.
  `"-0d 1h 0m"`.

`MALFORMED_DEADLINE` and `MALFORMED_CREATED_AT` do **not** carry an age
field (there is no valid timestamp to measure an age from); instead they
carry `deadline_raw` / `created_at_raw` with the original offending value.

## Output shape

Canonical JSON (`json.dumps(obj, sort_keys=True, separators=(",", ":"),
ensure_ascii=True)` plus one trailing `\n`):

```json
{"findings":{"critical":[...],"info":[...],"warning":[...]},"generated_at":"2026-08-02T00:00:00Z","summary":{"critical":1,"info":1,"total_findings":3,"total_tasks":20,"warning":1},"windows":{"accepted_stale_hours":48,"submitted_stale_hours":72}}
```

Top-level keys (alphabetized by `sort_keys=True`, so they always land in
this order): `findings`, `generated_at`, `summary`, `windows`.

- `generated_at` -- the injected `--now`, normalized and echoed back.
- `windows` -- the effective `accepted_stale_hours` / `submitted_stale_hours`
  used for this run (defaults or overrides).
- `summary` -- `total_tasks`, `total_findings`, and a count per bucket.
- `findings` -- an object with one array per bucket (`critical`, `warning`,
  `info`). Within each array, findings are sorted by `(str(task_id), code)`
  -- a stable, deterministic order independent of input order or dict
  iteration order.

Each finding object always has `task_id`, `title`, `status`, `code`,
`bucket`, `message`, plus code-specific fields as described above
(`deadline`, `created_at`, `age_seconds`, `age_human`,
`accepted_stale_hours`/`submitted_stale_hours`, or `*_raw`).

## Why no wall-clock reads

`datetime.now()`, `datetime.utcnow()`, and `time.time()` do not appear
anywhere in `staleness.py`'s report path (verified by a grep in
`captured_output.txt`, and enforced by a unit test,
`TestNoWallClockRead.test_source_has_no_datetime_now`, that scans the
script's own source for those exact substrings). The only place "now"
enters the program is `now = parse_utc_timestamp(args.now)` in `main()`,
and that value is threaded explicitly as a function parameter into
`evaluate_task` and `build_report`. This is what makes two runs against
the same input and the same `--now` byte-identical.

## Known limitations / false-positive risks

1. **No leap-second support.** `datetime.fromisoformat` (the only parser
   used) rejects `:60` seconds fields, so a genuine leap-second timestamp
   from an upstream system would be reported as `MALFORMED_*` rather than
   parsed. In practice this is extremely unlikely to matter for task
   records, but it is a real gap versus "fully general ISO-8601."
2. **Fixed, non-configurable bucket thresholds for `OVERDUE_PROPOSED`.**
   The `accepted`/`submitted` stale windows are configurable via CLI flags,
   but the 6h/24h info-warning-critical split for `OVERDUE_PROPOSED`, and
   the 0.5x/1x window-multiplier split for `STALE_*` bucket boundaries, are
   hardcoded constants. A team with different urgency expectations (e.g.
   "any overdue proposal is instantly critical") would need to edit the
   source rather than pass a flag.
3. **`-00:00` is treated as equivalent to `Z`/`+00:00`.** This is
   consistent with `datetime.utcoffset() == timedelta(0)`, but ISO-8601
   purists sometimes use `-00:00` to mean "unknown local offset" rather
   than "UTC." This tool treats it as UTC; a source system that uses
   `-00:00` with that alternate meaning would get its timestamps silently
   accepted rather than flagged.

## Files in this delivery

- `staleness.py` -- the CLI (stdlib only).
- `test_staleness.py` -- unittest suite (156 tests; run with
  `python3 -m unittest test_staleness -v`).
- `tasks_fresh.json` -- fixture with zero findings (exit code 0).
- `tasks_stale.json` -- fixture engineered to trigger all six finding
  codes and all three urgency buckets (exit code 1).
- `README.md` -- this file.
- `captured_output.txt` -- real captured output of the verification
  commands.
