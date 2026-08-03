# loop-health

A stdlib-only Python 3 CLI that reads exported task histories and reports
resubmission rounds, overdue reviews, and a refusal-reason distribution as
canonical, reproducible JSON.

```
python3 loop_health.py HISTORIES.json --now 2026-08-03T00:00:00Z [options]
```

No third-party packages, no network access, and **no reads of the system
clock anywhere in the report path**. Every age and overdue computation is
driven exclusively by the UTC reference time you pass via the required
`--now` argument. Run the tool twice with the same input file and the same
`--now` and you get byte-identical output, forever, regardless of when you
actually run it.

## What we matched from staleness-monitor

This tool is a sibling of `staleness-monitor` (`staleness.py`), which
established the "injected `--now`, never read the wall clock" pattern for
this family of tools first. We deliberately matched, rather than
reinvented, the following conventions from it:

- **The reproducibility contract itself**: `--now` is a required CLI
  argument, parsed once in `main()`, and threaded explicitly as an ordinary
  function parameter (`now`) into every function that needs it
  (`process_task`, `build_report`). `datetime.now`/wall-clock reads do not
  appear anywhere in the report path -- see "Why no wall-clock reads" below.
- **`parse_utc_timestamp`**: copied verbatim (same accepted/rejected forms:
  trailing `Z`, explicit zero offset, rejects non-zero offsets and
  timezone-naive strings).
- **`iso_z`** and **`format_age`**: copied verbatim, including the
  `"<sign><d>d <h>h <m>m"` rendering and the "sub-minute remainders are
  truncated, exact value lives in `*_seconds`" convention.
- **Canonical JSON**: identical `json.dumps(obj, sort_keys=True,
  separators=(",", ":"), ensure_ascii=True)` plus one trailing `\n`.
- **Exit code scheme**: `0` clean / `1` findings / `2` usage error, and the
  same `-o`/`--output` flag semantics (write the report to a file instead
  of stdout, same file-write error handling).
- **Strict-inequality boundary semantics**: a value exactly equal to a
  configured threshold does **not** breach; one unit past it does. Applied
  here to both `--review-overdue-hours` and `--max-rounds`.
- **Doc style**: a "Known limitations" section that names real,
  unresolved risk areas rather than hiding them, and a suite structured as
  one `unittest.TestCase` subclass per concept with dynamically-generated
  test methods (`setattr(TestClass, "test_x", make_test(...))`) for dense,
  table-driven coverage.

What is genuinely different here (not matched, because the domain differs):
staleness-monitor evaluates one flat record per task against `deadline` /
`created_at`. loop_health.py evaluates a whole **event history** per task,
sorts it into chronological order itself (the input is not assumed to
already be sorted), and derives resubmission rounds and a task's *latest*
state from that ordering. There is no "urgency bucket" concept here (no
critical/warning/info split) -- findings are flat and code-keyed instead.

## Installation

None. It's one file (`loop_health.py`) that only imports from the Python 3
standard library (`argparse`, `json`, `sys`, `datetime`). Requires Python
3.9+ (uses `datetime.fromisoformat`; tested on 3.10).

## Usage

```
python3 loop_health.py INPUT_FILE --now ISO8601_UTC
                        [--max-rounds N]
                        [--review-overdue-hours HOURS]
                        [-o OUTPUT_FILE | --output OUTPUT_FILE]
```

| Flag | Required | Default | Meaning |
|---|---|---|---|
| `INPUT_FILE` | yes | -- | Path to a JSON file containing an array of task history records. |
| `--now` | yes | -- | UTC reference time, ISO-8601 (`Z` or `+00:00` suffix). Never defaulted, never read from the OS clock. |
| `--max-rounds` | no | `3` | A task's `resubmission_rounds` strictly greater than this triggers `EXCESSIVE_RESUBMISSIONS`. |
| `--review-overdue-hours` | no | `72` | Hours after a task's latest `awaiting_review`/`submitted` event before `REVIEW_OVERDUE` fires. |
| `-o`, `--output` | no | stdout | Write the JSON report to a file instead of stdout. |

### Exit codes

| Code | Meaning |
|---|---|
| `0` | Input parsed successfully; zero findings. |
| `1` | Input parsed successfully; one or more findings were produced. |
| `2` | Invalid input or usage error: missing/unparseable `--now`, missing/unreadable input file, input file is not valid JSON, the JSON root is not an array, or a negative `--max-rounds`/`--review-overdue-hours`. |

Important asymmetry versus a naive reading of "invalid input": a
structurally malformed **record** inside an otherwise-valid top-level array
(e.g. a record missing `task_id`, or an event with a bad `state`) is **not**
a usage error. It produces a `MALFORMED_RECORD` finding (exit `1`), and the
rest of the array is still processed. Only a malformed *root* (file
unreadable, invalid JSON syntax, or the root value isn't a JSON array) is a
usage error (exit `2`). This is a deliberate, spec-driven choice -- see
"Input shape" below.

## Input shape

The input file must be a JSON array of task history records:

```json
[
  {
    "task_id": "T-100",
    "events": [
      {"state": "proposed", "at": "2026-07-20T09:00:00Z"},
      {"state": "accepted", "at": "2026-07-20T10:00:00Z"},
      {"state": "submitted", "at": "2026-07-21T15:00:00Z"},
      {"state": "verification_requested", "at": "2026-07-22T08:00:00Z"},
      {"state": "refused", "at": "2026-07-22T18:00:00Z", "refusal_reason": "missing_attachment"},
      {"state": "submitted", "at": "2026-07-23T09:00:00Z"},
      {"state": "awaiting_review", "at": "2026-07-23T10:00:00Z"},
      {"state": "rewarded", "at": "2026-07-24T09:00:00Z"}
    ]
  }
]
```

- `task_id` -- **required**, must be a non-empty JSON string. A record
  missing this key, or with a `task_id` that is `null`, a number, or any
  other non-string/empty value, is unusable: it produces exactly one
  `MALFORMED_RECORD` finding and contributes nothing else (no
  `resubmission_rounds` entry, no refusal-reason contributions). Since we
  have no real identifier for it, the finding's `task_id` field is a
  synthetic placeholder `"<index:N>"` (N = its position in the top-level
  array), and the finding additionally carries `record_index` -- see
  "Known limitations" for why this placeholder is not a bulletproof
  identifier.
- `events` -- **required**, must be a JSON array (may be empty). Missing or
  non-array `events` is likewise fully unusable for that record
  (`MALFORMED_RECORD`, no further processing). An empty array (`[]`) *is*
  usable -- it produces `EMPTY_HISTORY` and a `resubmission_rounds` entry
  of `0`.
- Each element of `events` must be a JSON object with:
  - `state` -- **required**, non-empty string. One of the seven known
    lifecycle states (below) is expected but not enforced at this level;
    an unrecognized-but-well-formed value produces `UNKNOWN_STATE` rather
    than blocking the record. Comparison is case-sensitive.
  - `at` -- **required**, an ISO-8601 UTC timestamp string (see "Timestamp
    format" below). Present-but-unparseable produces `INVALID_TIMESTAMP`.
    Missing entirely, or not a string, produces `MALFORMED_RECORD` instead
    (a *structural* problem, not a parse failure).
  - `refusal_reason` -- **optional**. See "Refusal-reason distribution"
    below for its full, deliberately lenient handling.

An event that is not a JSON object, or has a missing/invalid `state`, or a
missing/non-string `at`, is individually skipped (one `MALFORMED_RECORD`
finding per bad event) -- the rest of that task's events are still
processed. `INVALID_TIMESTAMP` events (well-formed `state`, unparseable
`at`) are also excluded from the task's chronological sequence, but a
well-formed `refusal_reason` on such an event is still counted (see below).

### Lifecycle states

```
proposed -> accepted -> submitted -> verification_requested -> awaiting_review -> rewarded
                                                              -> refused
```

The seven known states: `proposed`, `accepted`, `submitted`,
`verification_requested`, `awaiting_review`, `rewarded`, `refused`. Any
other well-formed state string is legal input but triggers `UNKNOWN_STATE`.
This tool does not enforce that the sequence of states in a history follows
any particular state machine -- histories with "impossible" transitions
(e.g. `rewarded` followed by `proposed`) are accepted and processed as-is;
no finding code exists for "implausible transition."

### Timestamp format

Identical rule set to `staleness.py`'s `parse_utc_timestamp`:

Accepted:
- A trailing `Z`/`z` with no embedded offset: `2026-08-02T00:00:00Z`
- An explicit zero UTC offset: `2026-08-02T00:00:00+00:00` or `...-00:00`
- Optional fractional seconds: `2026-08-02T00:00:00.500000Z`

Rejected (-> `INVALID_TIMESTAMP` when on an event's `at`; -> exit-2 usage
error when on `--now`):
- Any non-zero offset, e.g. `2026-08-02T00:00:00+05:30`
- A timezone-naive string with no offset at all
- Anything `datetime.fromisoformat` cannot parse
- Any non-string JSON value where a *value* was actually supplied (an
  absent `at` key is `MALFORMED_RECORD`, not `INVALID_TIMESTAMP` -- see
  above)

## What gets computed

### Chronological ordering (input order is not trusted)

Every task's events are re-sorted before anything is computed. The sort
key is `(parsed_at, original_index_in_the_events_array)`. This means:

- The array in the input file does **not** need to be in chronological
  order; the tool sorts it.
- If two events share the exact same `at`, the one that appeared **later**
  in the input array is treated as chronologically later (an explicit,
  tested tiebreak -- not an accident of Python's stable sort). See
  `test_identical_timestamps_tie_break_is_original_order` in the test
  suite.
- Events that fail structural validation (not an object, bad `state`) or
  timestamp parsing (`INVALID_TIMESTAMP`) are excluded entirely from this
  sequence -- they cannot be placed in time, so they cannot affect
  resubmission-round adjacency or "latest state."

### Resubmission rounds

`resubmission_rounds` for a task is the count of **directly adjacent**
`verification_requested -> submitted` pairs in its chronological sequence.
"Directly adjacent" means no other event of any kind sits between them
once malformed/unparseable events have been excluded (an `UNKNOWN_STATE`
event *does* still occupy a slot in the sequence and will break adjacency,
since it is otherwise well-formed and placeable in time).

Zero rounds is healthy: it is reported as data (in the top-level
`resubmission_rounds` array) but is **never** itself a finding. Only
exceeding `--max-rounds` produces a finding (`EXCESSIVE_RESUBMISSIONS`,
strict `>`, boundary exactly-at `--max-rounds` does not breach).

**Deliberately narrow definition -- read this before relying on the
count.** Because only the literal, adjacent `verification_requested ->
submitted` pair counts, a task that goes `verification_requested -> refused
-> submitted` (resubmitted *after being refused*, without an intervening
fresh `verification_requested`) contributes **zero** to
`resubmission_rounds` for that step, even though a human would likely call
that a resubmission. This is intentional per the literal task
specification ("the number of times the task went verification_requested
-> submitted"), not an oversight -- but it is a real undercount risk for
any consumer who expects "resubmission" to mean "any repeat submission
after a setback." See "Known limitations" #1.

### REVIEW_OVERDUE

Fires when a task's **latest** chronological state (after excluding
malformed/unparseable events, as above) is `awaiting_review` or
`submitted`, and its age against `--now` (i.e. `--now` minus the `at` of
that latest event) strictly exceeds `--review-overdue-hours` (default
`72`).

Boundary semantics (matched from staleness.py): **strict `>`**. An age
exactly equal to `--review-overdue-hours * 3600` seconds does **not**
breach; one second past it does. Covered by
`test_awaiting_review_exactly_at_threshold_no_breach`,
`test_awaiting_review_one_second_past_threshold_breaches`, and their
`submitted`-state counterparts.

The finding carries:
- `state` -- `"awaiting_review"` or `"submitted"`.
- `since` -- the (parsed, `Z`-normalized) timestamp of that latest event.
- `age_seconds` -- whole seconds (`int(round(...))` of the exact
  `timedelta.total_seconds()`).
- `age_human` -- e.g. `"3d 1h 0m"` for 262800 seconds; sub-minute
  remainders truncated (exact value is in `age_seconds`).
- `review_overdue_hours` -- the effective threshold used for this run.

### EXCESSIVE_RESUBMISSIONS

Fires when `resubmission_rounds > --max-rounds` (default `3`), strict `>`
-- exactly at `--max-rounds` does not breach. Carries `rounds` and
`max_rounds`.

### refusal_reason distribution

A single, input-wide `refusal_reason_distribution` array (not per-task):
one `{"reason": ..., "count": ...}` entry per distinct reason string seen
across every well-formed `refused` event in the whole input, sorted by
count descending, then reason ascending for a stable tie-break.

Handling of `refusal_reason` (deliberately lenient; documented, not an
accident):

| Situation | Behavior |
|---|---|
| `refusal_reason` is a string (including `""`) on a `state: "refused"` event | Counted, verbatim, as its own distinct bucket. |
| `refusal_reason` is a string on a **non**-`"refused"` event | **Ignored** for the distribution. Not an error, not a finding. |
| `refusal_reason` key absent on a `"refused"` event | **Not counted.** Not an error, not a finding. |
| `refusal_reason` is explicit JSON `null` | Treated the same as absent: not counted, not an error. |
| `refusal_reason` is present but not a string (number, list, object, boolean) | `MALFORMED_RECORD` finding; not counted. |
| `refusal_reason` is a string on a `"refused"` event whose own `at` failed to parse (`INVALID_TIMESTAMP`) | **Still counted.** Timing validity and refusal-bookkeeping are treated as independent -- we don't need to trust *when* a refusal happened to trust *why*. |

Unicode reasons are supported and preserved verbatim in the distribution
(the output is `ensure_ascii=True`, so they appear `\uXXXX`-escaped in the
canonical JSON, but round-trip losslessly through `json.loads`).

### MALFORMED_RECORD / INVALID_TIMESTAMP / UNKNOWN_STATE / EMPTY_HISTORY

All four are findings, not usage errors -- see "Exit codes" above. Their
triggers are described inline above (per-field). Every finding carries at
minimum `task_id`, `code`, `message`; the specific finding codes above add
`record_index` and/or `event_index` and code-specific fields as documented.

## Output shape

Canonical JSON (`json.dumps(obj, sort_keys=True, separators=(",", ":"),
ensure_ascii=True)` plus one trailing `\n`):

```json
{"findings":[...],"generated_at":"2026-08-03T00:00:00Z","options":{"max_rounds":3,"review_overdue_hours":72},"refusal_reason_distribution":[...],"resubmission_rounds":[...],"summary":{...}}
```

Top-level keys (alphabetized by `sort_keys=True`, so they always land in
this order): `findings`, `generated_at`, `options`,
`refusal_reason_distribution`, `resubmission_rounds`, `summary`.

- `generated_at` -- the injected `--now`, normalized and echoed back.
- `options` -- the effective `max_rounds` / `review_overdue_hours` used.
- `summary` -- `total_tasks` (length of the input array, including
  unusable records), `total_findings`, and `counts_by_code` (a key for
  every one of the six finding codes, always present, `0` when absent).
- `resubmission_rounds` -- `[{"task_id": ..., "resubmission_rounds": ...}, ...]`
  for every record that was structurally usable (has a valid `task_id` and
  an `events` array), sorted by `(str(task_id), the record's position in
  the input array)`. Structurally unusable records (see "Input shape") do
  **not** get an entry here.
- `refusal_reason_distribution` -- as described above.
- `findings` -- a single flat array (no bucketing). Sorted by `(task_id,
  code, event_index-or-(-1), the finding's own canonical JSON dump)` --
  the last component is a deterministic tiebreak of last resort, so the
  order is fully reproducible even when two findings share every other
  sort key.

## Why no wall-clock reads

The three call sites that would read the wall clock in a naive
implementation are absent from `loop_health.py` end to end. The only place
"now" enters the program is in `main()`, where it is parsed from
`args.now` -- the value supplied on the command line -- via
`parse_utc_timestamp`, and then threaded explicitly as an ordinary
parameter named `now` into `process_task` and `build_report`. Verified two
ways:

1. `TestNoWallClockRead.test_source_has_no_forbidden_wall_clock_calls` in
   `test_loop_health.py` scans this script's own source for the three
   forbidden substrings at test time (constructed via string
   concatenation in the test so the test file itself doesn't trip the same
   grep).
2. `captured_output.txt` includes the verbatim result of running
   `grep -n "now()\|utcnow\|time.time" loop_health.py` against the shipped
   source -- it returns nothing.

Because `--now` is declared with `required=True` on the `argparse`
argument, omitting it is an `argparse` usage error and exits `2`
automatically -- there is no fallback path to the system clock to fall
back *to*.

## Known limitations (read before relying on this in production)

1. **`resubmission_rounds` undercounts "resubmit after refusal" by
   design.** As detailed above, only a direct, adjacent
   `verification_requested -> submitted` pair counts. A refusal breaks
   that adjacency, so `verification_requested -> refused -> submitted`
   contributes zero rounds for that step. This matches the literal task
   specification, but a reviewer who expects "resubmission" to mean "any
   repeat submission" will see numbers lower than they expect on tasks
   with a refusal-then-resubmit history. `histories_unhealthy.json`'s
   `T-REFUSED-RESUBMITTED` task demonstrates this directly (0 rounds,
   despite a real refusal-then-resubmit in its history).
2. **The `"<index:N>"` synthetic task identifier can collide with a real
   `task_id`.** When a record is structurally unusable (not an object,
   missing/invalid `task_id`), its `MALFORMED_RECORD` finding needs *some*
   value in the `task_id` field, so it uses the placeholder
   `"<index:N>"`. Because `task_id` is an arbitrary JSON string with no
   reserved namespace, nothing stops a *different*, well-formed record
   from legitimately having `"task_id": "<index:3>"` as its real
   identifier -- their findings would then share the same visible
   `task_id` string. They remain distinguishable programmatically (only
   the synthetic placeholder's finding carries a `record_index` field),
   but a human skimming the `findings` array by `task_id` alone could
   momentarily conflate the two. This is inherent to any placeholder
   scheme over an unrestricted string namespace, not something a
   different sentinel string would fully solve; see
   `test_index_placeholder_can_collide_with_a_real_task_id` for the
   documented, tested current behavior.
3. **No cross-event / state-machine plausibility checking.** The tool
   deliberately does not validate that a task's state sequence is a
   *plausible* one (e.g. it will not flag `rewarded` immediately followed
   by `proposed`, two `rewarded` events, or a `refused` with no preceding
   `submitted`/`verification_requested`). It only reasons about
   `resubmission_rounds` (a specific adjacent-pair pattern),
   `REVIEW_OVERDUE` (latest-state + age), and structural/timestamp
   validity. A history that is internally nonsensical but structurally
   well-formed produces no finding at all. Combined with limitation #1,
   this means "healthy" output should be read as "no configured checks
   fired," not "this task's history is sane."

## Bug found and fixed during testing

Testing here means both the 202-test `unittest` suite and an unseeded-off
20,000-iteration randomized fuzz pass (constructing malformed records,
malformed events, mixed valid/invalid `state`/`at`/`refusal_reason`
combinations, and re-running `build_report` twice per input to check for
non-determinism or exceptions) run directly against the library functions
during development. No crash, exception leak, or non-deterministic output
was produced by that fuzz pass.

The one genuine defect the process did surface was in this test suite
itself, not the tool, and both cases were caused by the same mistake
(computing an expected value by hand instead of from the tool's actual,
documented semantics):

- `test_reverse_transition_not_counted` originally built its fixture with
  `hours_ago(10)` for `verification_requested` and `hours_ago(9)` for
  `submitted` and then reversed the *input list* to "test the reverse
  direction." But chronological order is derived from the parsed
  timestamp, not input-array order -- `hours_ago(10)` (10 hours before
  `--now`) is chronologically *before* `hours_ago(9)`, so this was still a
  forward `verification_requested -> submitted` pair regardless of list
  order, and correctly produced 1 round. The test's expectation of `0` was
  wrong; the tool's `1` was right. Fixed by rebuilding the fixture with
  `submitted` chronologically before `verification_requested`
  (`hours_ago(10)` / `hours_ago(9)` swapped between the two states) to
  actually exercise the reverse direction.
- `test_review_overdue_hours_accepts_floats` asserted exit code `0` for
  `--review-overdue-hours 0.5` against `histories_healthy.json`, without
  checking whether that fixture is actually clean at a half-hour
  threshold. It isn't: `histories_healthy.json` includes `T-104`, whose
  latest state is `submitted` 12 hours before `--now` -- correctly
  `REVIEW_OVERDUE` at a 0.5-hour threshold, exit `1`. Fixed by asserting
  the flag is *accepted* (exit code in `{0, 1}`, not `2`, and the output
  still parses as JSON) rather than hardcoding an exit code the fixture
  was never designed to produce at that threshold.

Per the "fix the tool, not the test" rule: in both cases the *tool's*
behavior was correct and matched its own documented semantics; the test's
hand-computed expectation was wrong. Both are now fixed by correcting the
test to match the tool's actual (intended, tested-elsewhere) contract, not
by changing the tool.

## Files in this delivery

- `loop_health.py` -- the CLI (stdlib only).
- `test_loop_health.py` -- unittest suite (202 tests; run with
  `python3 -m unittest test_loop_health -v`).
- `histories_healthy.json` -- fixture with zero findings (exit code 0).
- `histories_unhealthy.json` -- fixture engineered to trigger all six
  finding codes (exit code 1).
- `README.md` -- this file.
- `captured_output.txt` -- real captured output of the verification
  commands, including the wall-clock grep proof.

## Reproducible commands

```
python3 -m unittest test_loop_health -v
python3 loop_health.py histories_healthy.json --now 2026-08-03T00:00:00Z ; echo "exit=$?"
python3 loop_health.py histories_unhealthy.json --now 2026-08-03T00:00:00Z -o r1.json ; echo "exit=$?"
python3 loop_health.py histories_unhealthy.json --now 2026-08-03T00:00:00Z -o r2.json ; echo "exit=$?"
sha256sum r1.json r2.json
cmp r1.json r2.json && echo BYTE-IDENTICAL
python3 loop_health.py histories_unhealthy.json --now 2026-08-03T00:00:00Z --review-overdue-hours 99999 ; echo "exit=$?"
python3 loop_health.py histories_unhealthy.json ; echo "exit=$?"
python3 loop_health.py /nonexistent.json --now 2026-08-03T00:00:00Z ; echo "exit=$?"
grep -n "now()\|utcnow\|time.time" loop_health.py
```

`r1.json` / `r2.json` are throwaway scratch files used only to prove
byte-for-byte reproducibility; they are not part of this delivery.
See `captured_output.txt` for the real, captured output of every command
above.
