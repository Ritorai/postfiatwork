# scorecard

A stdlib-only Python 3 CLI that converts exported task histories into
deterministic, **descriptive** per-contributor scorecards: completion rate,
average verification rounds, refusal rate, and evidence-type mix.

```
python3 scorecard.py HISTORIES.json --now 2026-08-03T00:00:00Z [options]
```

No third-party packages, no network access, and **no reads of the system
clock anywhere in the report path**. Every computation is driven exclusively
by the UTC reference time you pass via the required `--now` argument. Run
the tool twice with the same input file and the same `--now` and you get
byte-identical output, forever, regardless of when you actually run it.

## The ethical requirement (read this first)

**These numbers are descriptive context. They are not a ranking, a grade,
or a basis for penalizing anyone.** This is encoded in the tool, not just
asserted in prose:

- There is no rank, percentile, letter grade, or single composite "score"
  field anywhere in the output. `test_no_forbidden_keys_anywhere_in_output`
  in `test_scorecard.py` walks the entire output tree and asserts none of
  `rank`, `percentile`, `grade`, `composite`, `score` (outside the word
  "scorecard" itself), `tier`, or `leaderboard` appears as a key fragment.
- `scorecards` is sorted **only by contributor id**, ascending, never by
  any metric. Sorting by a metric *is* a form of ranking, so it's not done.
  `test_scorecards_ordered_by_contributor_id_not_by_any_metric` proves this
  concretely: in that test, the contributor with the *lower* completion
  rate sorts first, because "amy" < "zed" alphabetically -- ordering
  tracks identity, not merit.
- Every report carries a machine-readable `disclaimer` object:
  `{"not_a_ranking": true, "not_a_basis_for_penalization": true, "text": "..."}`.
  This is JSON data a downstream consumer can branch on, not just a
  docstring a human might skip.
- **What these numbers genuinely cannot tell you**: they measure
  *throughput and review friction* -- how many tasks reached a terminal
  state, how many review round-trips they took, what evidence was
  attached. They do **not** measure code quality, task difficulty, effort,
  or professionalism. A contributor who takes on harder, more ambiguous,
  or more contested tasks will tend to accumulate more refusals and more
  verification rounds than one who takes on easy, unambiguous tasks --
  *even if the harder-task contributor's actual work is better*. Comparing
  two contributors' numbers without knowing what kind of work they took on
  is comparing apples to a difficulty-weighted unknown. Do not use this
  tool to rank, discipline, or compensate people.

## What we matched from the sibling tools

This tool is a sibling of `staleness-monitor` (`staleness.py`) and
`loop-health` (`loop_health.py`), which established the "injected `--now`,
never read the wall clock" pattern for this family of tools. We
deliberately matched, rather than reinvented, the following conventions:

- **The reproducibility contract itself**: `--now` is a required CLI
  argument, parsed once in `main()`, and threaded explicitly as an ordinary
  function parameter (`now`) into every function that needs it.
  `datetime.now`/wall-clock reads do not appear anywhere in the report
  path -- see "Why no wall-clock reads" below.
- **`parse_utc_timestamp`**: copied verbatim (same accepted/rejected forms:
  trailing `Z`, explicit zero offset, rejects non-zero offsets and
  timezone-naive strings).
- **`iso_z`**: copied verbatim.
- **Canonical JSON**: identical `json.dumps(obj, sort_keys=True,
  separators=(",", ":"), ensure_ascii=True)` plus one trailing `\n`.
- **Exit code scheme**: `0` clean / `1` findings / `2` usage error, and the
  same `-o`/`--output` flag semantics.
- **Chronological re-sorting**: like `loop_health.py`, this tool does not
  trust the input array's event order. Each task's events are re-sorted by
  `(parsed_at, original_index_in_the_events_array)` before anything is
  computed from them, with the same documented tiebreak (later input
  position wins on an exact timestamp tie).
- **`MALFORMED_RECORD` / `INVALID_TIMESTAMP` / `UNKNOWN_STATE` /
  `EMPTY_HISTORY` semantics**: a malformed record or event is a *finding*
  (exit `1`), never a usage error (exit `2`) -- only a malformed *root*
  (unreadable file, invalid JSON, root isn't an array) is a usage error.
- **Doc style**: a "Known limitations" section that names real, unresolved
  risk areas, and a suite structured as one `unittest.TestCase` subclass
  per concept, with dynamically-generated table-driven test methods where
  that gives denser coverage than hand-writing each case.

What is genuinely different here (not matched, because the domain
differs): the sibling tools evaluate a task/record against thresholds and
emit findings about *that task*. This tool's primary output is a *per-
contributor aggregate* over many tasks -- the unit of reporting is the
contributor, not the task, and a new required field (`contributor`) and
finding code (`MISSING_CONTRIBUTOR`) exist specifically to handle
attribution failures. There is also a new `evidence` array per task with
its own (optional-key, lenient) shape contract, and an entirely new
ethical-requirements layer (disclaimer field, contributor-id-only sort,
no-ranking test) that the sibling tools, being per-task health monitors
rather than people-facing scorecards, had no occasion to need.

## Installation

None. It's one file (`scorecard.py`) that only imports from the Python 3
standard library (`argparse`, `json`, `sys`, `decimal`, `datetime`).
Requires Python 3.9+ (uses `datetime.fromisoformat`; tested on 3.10).

## Usage

```
python3 scorecard.py INPUT_FILE --now ISO8601_UTC
                      [--min-tasks N]
                      [-o OUTPUT_FILE | --output OUTPUT_FILE]
```

| Flag | Required | Default | Meaning |
|---|---|---|---|
| `INPUT_FILE` | yes | -- | Path to a JSON file containing an array of task history records. |
| `--now` | yes | -- | UTC reference time, ISO-8601 (`Z` or `+00:00` suffix). Never defaulted, never read from the OS clock. |
| `--min-tasks` | no | `5` | A contributor with fewer than this many total tasks has their `completion_rate`, `average_verification_rounds`, and `refusal_rate` reported as `null` with an `INSUFFICIENT_DATA` note. |
| `-o`, `--output` | no | stdout | Write the JSON report to a file instead of stdout. |

### Exit codes

| Code | Meaning |
|---|---|
| `0` | Input parsed successfully; either zero findings, or only `INSUFFICIENT_DATA` findings (informational -- see below). |
| `1` | Input parsed successfully; at least one non-informational finding was produced (`MALFORMED_RECORD`, `INVALID_TIMESTAMP`, `UNKNOWN_STATE`, `EMPTY_HISTORY`, and/or `MISSING_CONTRIBUTOR`). |
| `2` | Invalid input or usage error: missing/unparseable `--now`, missing/unreadable input file, input file is not valid JSON, the JSON root is not an array, or a negative `--min-tasks`. |

As with the sibling tools: a structurally malformed **record** inside an
otherwise-valid top-level array is **not** a usage error. It produces a
finding (exit `1`), and the rest of the array is still processed. Only a
malformed *root* is a usage error (exit `2`).

## Input shape

The input file must be a JSON array of task history records:

```json
[
  {
    "contributor": "alice",
    "task_id": "T-100",
    "events": [
      {"state": "proposed", "at": "2026-07-20T09:00:00Z"},
      {"state": "accepted", "at": "2026-07-20T10:00:00Z"},
      {"state": "submitted", "at": "2026-07-21T15:00:00Z"},
      {"state": "verification_requested", "at": "2026-07-22T08:00:00Z"},
      {"state": "awaiting_review", "at": "2026-07-22T09:00:00Z"},
      {"state": "rewarded", "at": "2026-07-24T09:00:00Z"}
    ],
    "evidence": [
      {"evidence_type": "screenshot"},
      {"evidence_type": "log"}
    ]
  }
]
```

- `contributor` -- **required**, must be a non-empty, non-whitespace-only
  JSON string. Absent, `null`, non-string, `""`, or `"   "` all produce a
  `MISSING_CONTRIBUTOR` finding and the record contributes to **no**
  scorecard (it cannot be attributed to anyone). See "Contributor identity
  is not normalized" below for why case and whitespace variants are
  treated as *different* contributors, deliberately.
- `task_id` -- **required**, must be a non-empty JSON string. Missing,
  empty, or non-string produces a `MALFORMED_RECORD` finding; the record
  contributes to no scorecard. (Checked before `contributor`, so its
  finding uses the synthetic `"<index:N>"` identifier if `task_id` itself
  is unusable; if `task_id` is valid but `contributor` is not, the
  `MISSING_CONTRIBUTOR` finding uses the real `task_id`.)
- `events` -- **required**, must be a JSON array (may be empty). Missing or
  non-array `events` is fully unusable for that record (`MALFORMED_RECORD`,
  record contributes nothing). An empty array (`[]`) *is* usable -- it
  produces `EMPTY_HISTORY` and the task still counts toward the
  contributor's `total_tasks` (just never toward `terminal_tasks`).
  - Each element must be a JSON object with `state` (required, non-empty
    string; one of the seven known lifecycle states expected but not
    enforced -- an unrecognized-but-well-formed value produces
    `UNKNOWN_STATE`, not a block) and `at` (required, ISO-8601 UTC
    timestamp string; present-but-unparseable produces `INVALID_TIMESTAMP`,
    missing/non-string produces `MALFORMED_RECORD`). An optional
    `refusal_reason` is validated for type only (must be a string or
    `null` if present; non-string produces `MALFORMED_RECORD`) -- its
    value is not otherwise used by any metric in this tool.
  - An event that is not a JSON object, or has a missing/invalid `state`
    or a missing/non-string `at`, is individually skipped (its own
    `MALFORMED_RECORD` finding); the rest of that task's events are still
    processed.
- `evidence` -- **optional**. Absent entirely means zero evidence items
  (not an error -- a task that never got evidence attached is a normal,
  expected case, not a data defect). If present, must be a JSON array;
  non-array `evidence` is `MALFORMED_RECORD` for the whole record. Each
  element must be a JSON object with `evidence_type` (required, must be a
  string -- including `""`, which is accepted and counted as its own,
  if unhelpful, bucket). An item that is not an object, or has a
  missing/non-string `evidence_type`, is individually skipped (its own
  `MALFORMED_RECORD` finding, carrying `evidence_index`).

### Lifecycle states

```
proposed -> accepted -> submitted -> verification_requested -> awaiting_review -> rewarded
                                                              -> refused
```

The seven known states: `proposed`, `accepted`, `submitted`,
`verification_requested`, `awaiting_review`, `rewarded`, `refused`. Any
other well-formed state string is legal input but triggers `UNKNOWN_STATE`.
Comparison is case-sensitive. This tool, like `loop_health.py`, does not
validate that a task's state sequence is a *plausible* state machine --
see "Known limitations" #3.

### Timestamp format

Identical rule set to the sibling tools' `parse_utc_timestamp`: accepts a
trailing `Z`/`z` or an explicit zero UTC offset (`+00:00`/`-00:00`,
optional fractional seconds); rejects non-zero offsets, timezone-naive
strings, anything `datetime.fromisoformat` cannot parse, and non-string
values.

## What gets computed, and the exact denominator for each metric

This is the part a reviewer should check most carefully. Every rate is
emitted as an object:

```json
{"value": "0.600000", "numerator": 3, "denominator": 5, "note": null}
```

`value` is a decimal STRING (never a JSON float, to keep the exact digits
stable across platforms), computed via exact integer division
(`Decimal(numerator) / Decimal(denominator)`) quantized to six fractional
digits with banker's rounding (`ROUND_HALF_EVEN`). `numerator` and
`denominator` are always the raw integers, present even when `value` is
`null`, so a reader can always recompute the exact ratio independently --
**a rate with no visible denominator is not auditable, and this tool never
emits one.**

### Terminal-state determination (shared by all three task-based metrics)

A task is considered to have **reached a terminal state** if, after
excluding structurally-invalid/unparseable events and re-sorting the rest
chronologically, its **latest** event's state is `rewarded` or `refused`.
This mirrors `loop_health.py`'s "latest chronological state" convention
for `REVIEW_OVERDUE` rather than asking "was there ever a rewarded/refused
event anywhere in this history" -- so a task that was `rewarded` and then
(implausibly) shows a later `proposed` event is **not** counted as
terminal by this tool (see "Known limitations" #3: no state-machine
plausibility checking -- this is a direct consequence of that same
decision, applied consistently).

### `completion_rate`

**Denominator: tasks that reached a terminal state (`rewarded` or
`refused`), for that contributor. NOT all of that contributor's tasks.**
An in-flight task (still `proposed`/`accepted`/`submitted`/
`verification_requested`/`awaiting_review`) is not a failure and is not
evidence of anything about completion yet -- including it in the
denominator would penalize contributors who simply have more work
currently open. Numerator: `rewarded` count.

```
completion_rate = rewarded_tasks / terminal_tasks
```

If `terminal_tasks == 0` (a contributor with tasks, but none of them
terminal yet -- see "Known bug found and fixed" and "Zero terminal tasks"
below), `value` is `null` with `note: "UNDEFINED_ZERO_DENOMINATOR"`, never
a `ZeroDivisionError` and never a silently-wrong `0`.

### `average_verification_rounds`

**Denominator: the same `terminal_tasks` count as `completion_rate`** (not
all tasks -- an in-flight task's round count so far is not yet a finished
signal). "Verification rounds" for one task = the count of **directly
adjacent** `verification_requested -> submitted` pairs in that task's
chronologically-sorted event sequence (identical definition to
`loop_health.py`'s `resubmission_rounds` -- see that tool's README for the
detailed adjacency semantics, including the documented "refused breaks
adjacency" behavior). Numerator: the **sum** of each terminal task's round
count. `average_verification_rounds = sum(rounds over terminal tasks) /
terminal_tasks_count` -- an exact mean, not an approximation.

### `refusal_rate`

**Denominator: the same `terminal_tasks` count**, again. Numerator:
`refused` count.

```
refusal_rate = refused_tasks / terminal_tasks
```

Note `completion_rate.numerator + refusal_rate.numerator ==
terminal_tasks` always holds (every terminal task is exactly one of
`rewarded` or `refused`) -- tested directly in
`test_completion_plus_refusal_numerators_sum_to_terminal`.

### `evidence_type_mix`

**Denominator: that contributor's total count of well-formed evidence
items, across ALL of their tasks (not just terminal ones).** This is
**deliberately a different denominator** from the three metrics above:
evidence can legitimately be attached to a task at any lifecycle stage
(e.g. right after `submitted`, long before any terminal outcome), so
restricting the mix to terminal-task evidence would silently discard real,
already-attached evidence and understate the mix for contributors with a
lot of in-flight work.

```json
{
  "total_evidence_items": 8,
  "by_type": [
    {"evidence_type": "diff", "count": 1, "share": {"value": "0.125000", "numerator": 1, "denominator": 8, "note": null}},
    {"evidence_type": "log", "count": 4, "share": {...}},
    {"evidence_type": "screenshot", "count": 3, "share": {...}}
  ]
}
```

`by_type` is sorted by `evidence_type` ascending (not by count -- sorting
by count would rank evidence types against each other for no reason this
tool needs, and consistency with the "never sort by a metric" rule is
cheap to keep). A contributor with zero evidence items gets
`{"total_evidence_items": 0, "by_type": []}` -- not an error, not a
finding; "no evidence yet" is a normal state for a task that hasn't been
reviewed.

## `--min-tasks` and `INSUFFICIENT_DATA`

A contributor's **total task count** (`total_tasks` -- every structurally-
usable, attributed record, including in-flight and `EMPTY_HISTORY` ones;
NOT `terminal_tasks`) is compared against `--min-tasks` (default `5`)
using **strict `<`**: exactly `--min-tasks` tasks is *sufficient*
(`min_tasks_met: true`); one fewer is *insufficient*. This boundary is
tested explicitly in `test_exactly_at_min_tasks_is_sufficient` and
`test_one_below_min_tasks_is_insufficient`.

When insufficient, `completion_rate`, `average_verification_rounds`, and
`refusal_rate` all get `value: null, note: "INSUFFICIENT_DATA"` -- but
their `numerator`/`denominator` fields are **left populated** (not
nulled), so the underlying counts remain auditable even though the tool
declines to present a rate computed from a small sample as if it were a
reliable signal.

**`--min-tasks` does NOT gate `evidence_type_mix`.** `--min-tasks` is a
threshold on task *count*; `evidence_type_mix`'s own denominator is
evidence *item* count, an unrelated axis. A contributor with only 2 tasks
but 50 well-documented evidence items still gets a fully populated,
auditable mix -- hiding it because "insufficient tasks" would discard real
information for a reason that has nothing to do with it. See
`test_evidence_mix_not_gated_by_min_tasks`.

### Does `INSUFFICIENT_DATA` alone set exit code `1`?

**No, by deliberate design decision.** `INSUFFICIENT_DATA` is the one
finding code in `INFORMATIONAL_CODES` (`scorecard.py`); its presence is
excluded from the exit-code-1 count in `build_report`. Reasoning: having
few tasks is a fact about the sample size, not a defect in the data --
nothing is malformed, missing, or wrong about a contributor who simply
hasn't done many tasks yet. Treating that the same as a genuine data
defect (`MALFORMED_RECORD`, `MISSING_CONTRIBUTOR`, etc.) would conflate
"this data needs to be fixed" with "this contributor doesn't have enough
history yet for this tool's rate metrics to say anything meaningful" --
two very different signals for a caller (e.g. a CI gate) to act on. This
is directly tested: `test_insufficient_data_alone_does_not_set_exit_relevant_count`
and, end-to-end, `test_clean_data_only_insufficient_data_still_exit_0` /
the `--min-tasks 100` verification run below (exit `0` despite three
`INSUFFICIENT_DATA` findings).

## Data-quality finding codes

| Code | Trigger | Exit-1-relevant? |
|---|---|---|
| `MALFORMED_RECORD` | Record/event/evidence-item shape violation (see "Input shape") | yes |
| `INVALID_TIMESTAMP` | An event's `at` is present as a string but fails to parse as UTC ISO-8601 | yes |
| `UNKNOWN_STATE` | An event's `state` is a well-formed non-empty string not among the seven known states | yes |
| `EMPTY_HISTORY` | A record's `events` array is present, is an array, and has zero elements | yes |
| `MISSING_CONTRIBUTOR` | `contributor` absent, non-string, or blank/whitespace-only | yes |
| `INSUFFICIENT_DATA` | A contributor's `total_tasks` is below `--min-tasks` | **no** (informational only) |

## Output shape

Canonical JSON (`json.dumps(obj, sort_keys=True, separators=(",", ":"),
ensure_ascii=True)` plus one trailing `\n`), top-level keys alphabetized by
`sort_keys=True`: `disclaimer`, `findings`, `generated_at`, `options`,
`scorecards`, `summary`.

- `generated_at` -- the injected `--now`, normalized and echoed back.
- `options` -- the effective `min_tasks` used.
- `disclaimer` -- the machine-readable ethical-requirement object (see
  above).
- `summary` -- `total_records` (length of the input array, including
  unusable records), `total_contributors`, `total_findings`, and
  `counts_by_code` (a key for every one of the six finding codes, always
  present, `0` when absent).
- `scorecards` -- one object per contributor with at least one attributed
  record, **sorted by `contributor` string ascending -- never by any
  metric.** Fields: `contributor`, `total_tasks`, `terminal_tasks`,
  `rewarded_tasks`, `refused_tasks`, `min_tasks_met`, `completion_rate`,
  `average_verification_rounds`, `refusal_rate`, `evidence_type_mix`.
- `findings` -- a single flat array. Sorted by `(contributor-or-"",
  task_id-or-"", code, event_index-or-evidence_index-or-(-1), the
  finding's own canonical JSON dump)` -- the last component is a
  deterministic last-resort tiebreak. This ordering exists purely for
  reproducibility; it is not a ranking of contributors by anything.

## Why no wall-clock reads

The only place "now" enters the program is in `main()`, parsed from
`args.now` via `parse_utc_timestamp`, then threaded explicitly as an
ordinary parameter named `now` into `build_report` and `process_record`.
Verified two ways:

1. `TestNoWallClockRead.test_source_has_no_forbidden_wall_clock_calls` in
   `test_scorecard.py` scans this script's own source for the three
   forbidden substrings at test time (built via string concatenation in
   the test so the test file itself doesn't trip the same check).
2. `captured_output.txt` includes the verbatim result of running
   `grep -n "now()\|utcnow\|time.time" scorecard.py` against the shipped
   source -- it returns nothing.

Because `--now` is declared with `required=True` on the `argparse`
argument, omitting it is a usage error handled explicitly in `main()`
(exit `2`) -- there is no fallback path to the system clock to fall back to.

## Contributor identity is not normalized

`"alice"`, `"Alice"`, and `"alice "` (trailing space) are treated as three
**distinct** contributors. This tool does not lowercase, trim, or
otherwise canonicalize the `contributor` string beyond rejecting values
that are blank/whitespace-only outright (those get `MISSING_CONTRIBUTOR`
instead, since there's no identity there to preserve). This is a
deliberate, tested choice (`test_case_variants_treated_as_distinct_contributors`,
`test_trailing_whitespace_variants_treated_as_distinct_contributors`), not
an oversight -- silently merging `"alice"` and `"Alice"` on the tool's own
initiative would be a guess about the data source's intent that this tool
has no basis for making. See "Known limitations" #3 for the real risk this
creates.

## Known limitations (read before relying on this in production)

1. **These numbers cannot tell you anything about task difficulty,
   contribution quality, or effort -- only throughput and review
   friction.** `completion_rate`, `average_verification_rounds`, and
   `refusal_rate` are all downstream of how many of a contributor's tasks
   reached a terminal state and how that terminal state was distributed.
   A contributor deliberately assigned harder, more ambiguous, or more
   contentious tasks will systematically accumulate more refusals and more
   verification rounds than one assigned routine, unambiguous tasks --
   regardless of the actual quality of either contributor's work. This
   tool has no signal about task difficulty at all, so it cannot correct
   for this, and no consumer of this output should assume it has. This is
   the single most important thing a reviewer should scrutinize before
   this data is used for anything beyond "what happened."
2. **Contributor identity is a raw string match with no normalization**
   (see above). If the same person appears in the source data as `"j.doe"`
   in some records and `"J.Doe"` or `"j.doe "` in others -- a realistic
   outcome of manual data entry or multiple export sources -- this tool
   will report them as two or three *separate* contributors, each with
   its own (individually smaller, and therefore more likely to hit
   `--min-tasks`) scorecard, rather than one person's real, combined
   history. Silently merging on the tool's own initiative would be worse
   (an unverifiable guess baked into the numbers), but a data pipeline
   feeding this tool should canonicalize contributor identity *before*
   this stage if that's a known risk in the source data.
3. **No cross-record consistency checking: duplicate `task_id` values
   across different contributors, or an internally implausible state
   sequence within one task, produce no finding at all.** If the same
   `task_id` legitimately (or erroneously) appears in two different
   records under two different `contributor` values, both records are
   processed completely independently and both contribute to their
   respective contributor's scorecard -- there is no
   `DUPLICATE_TASK_ID`-style code in this tool's finding vocabulary to
   flag that (see `test_duplicate_task_id_across_two_contributors_both_counted`).
   Separately, and for the same underlying reason `loop_health.py` doesn't
   validate state-machine plausibility either, a history like `rewarded`
   followed by a later `proposed` event produces no finding -- this tool
   only reasons about the *latest* chronological state for terminal-state
   determination, not whether the full sequence makes sense. "Zero
   findings" should be read as "no configured check fired," not "this
   contributor's task histories are internally sane."

## Zero terminal tasks (division-by-zero guard)

A contributor can have `total_tasks > 0` but `terminal_tasks == 0` (every
one of their tasks is still in-flight, or has an empty/all-malformed event
history). `make_rate()` checks for `denominator == 0` explicitly and
returns `{"value": null, "note": "UNDEFINED_ZERO_DENOMINATOR", ...}` in
that case, for all three task-based rates -- never a `ZeroDivisionError`,
and never a silently-wrong `0` presented as if it meant "zero percent
completion" (which would be actively misleading: it isn't that the
contributor failed every task, it's that none of their tasks have finished
yet). Covered by `test_zero_terminal_tasks_completion_rate_is_null_not_crash`
and the standalone smoke check during development (see below). This is
distinct from, and does not require, `--min-tasks`: a contributor can
clear `--min-tasks` easily (say, 20 open tasks) and still have zero
terminal tasks.

## Bug found and fixed during testing

Running the exact verification sequence specified for this delivery (`-o
r1.json` then `-o r2.json` from the identical input and `--now`, then
`sha256sum`/`cmp`) surfaced a real, genuine non-determinism bug on the
first attempt -- **not** a hypothetical edge case reasoned about in
advance, but one the reproducibility check itself caught:

`r1.json` and `r2.json` were **not** byte-identical.
`cmp` reported a difference at byte 1221. Diffing the parsed JSON located
it: the `MISSING_CONTRIBUTOR` finding for a record whose `contributor` key
was **entirely absent** (as opposed to present-but-`null`/blank) rendered
differently between the two runs:

```
run 1: "...must be a non-empty, non-whitespace JSON string): <object object at 0x70f322988550>"
run 2: "...must be a non-empty, non-whitespace JSON string): <object object at 0x779dda138550>"
```

Root cause: `process_record` uses a module-level sentinel object,
`_MISSING = object()`, to distinguish "key absent" from "key present with
value `None`" (the same pattern `loop_health.py` and `staleness.py` use).
The finding-message code built the human-readable value with a bare
`{contributor!r}` f-string interpolation. When `contributor is _MISSING`,
`repr()` on a bare `object()` instance falls through to
`object.__repr__`, which embeds that specific object's **CPython memory
address** -- a value that is different every time the Python process
starts, even for byte-identical input and `--now`. This silently violated
the tool's core reproducibility contract for exactly the one case
(`contributor` key entirely absent) that the `histories_flagged.json`
fixture exercises.

**Fix** (in `process_record`, `scorecard.py`): compute a fixed,
deterministic placeholder string (`"<absent>"`) whenever `contributor is
_MISSING`, and only fall through to `repr(contributor)` for values that
are genuinely present-but-invalid (`null`, `""`, `"   "`, non-string
types) -- all of which repr deterministically. Two regression tests were
added: `test_missing_contributor_key_message_has_no_memory_address`
(asserts the message never contains `"0x"` and does contain `"<absent>"`)
and `test_repeated_process_record_calls_produce_identical_messages` (calls
`process_record` twice on the identical under-specified input and asserts
the finding lists are equal, not just equal-length/equal-codes). Per the
"fix the tool, not the test" rule: this was a genuine tool defect (a
reproducibility-contract violation), not a wrong test expectation, so the
tool was fixed, and the fixture (`histories_flagged.json`'s
`T-MISSING-CONTRIBUTOR-KEY` record, which omits the `contributor` key
entirely rather than setting it to `null`) was kept exactly as-is because
it is precisely the scenario that caught the bug -- weakening it would
remove the regression coverage.

After the fix, the full verification sequence was re-run from scratch;
`r1.json` and `r2.json` are now byte-identical (see `captured_output.txt`
for the real, post-fix `sha256sum` / `cmp` output).

## Files in this delivery

- `scorecard.py` -- the CLI (stdlib only).
- `test_scorecard.py` -- unittest suite (188 tests; run with
  `python3 -m unittest test_scorecard -v`).
- `histories_clean.json` -- fixture with zero non-informational findings
  (exit code `0`; it does trigger one `INSUFFICIENT_DATA` finding at the
  default `--min-tasks`, for the single-task contributor `carla`, which by
  design does not affect the exit code).
- `histories_flagged.json` -- fixture engineered to trigger all five
  non-informational finding codes plus `INSUFFICIENT_DATA` (exit code `1`).
- `README.md` -- this file.
- `captured_output.txt` -- real captured output of every verification
  command, including the wall-clock grep proof, run **after** the bug fix
  above.

## Reproducible commands

```
python3 -m unittest test_scorecard -v
python3 scorecard.py histories_clean.json --now 2026-08-03T00:00:00Z ; echo "exit=$?"
python3 scorecard.py histories_flagged.json --now 2026-08-03T00:00:00Z -o r1.json ; echo "exit=$?"
python3 scorecard.py histories_flagged.json --now 2026-08-03T00:00:00Z -o r2.json ; echo "exit=$?"
sha256sum r1.json r2.json
cmp r1.json r2.json && echo BYTE-IDENTICAL
python3 scorecard.py histories_clean.json --now 2026-08-03T00:00:00Z --min-tasks 100 ; echo "exit=$?"
python3 scorecard.py histories_clean.json ; echo "exit=$?"
python3 scorecard.py /nonexistent.json --now 2026-08-03T00:00:00Z ; echo "exit=$?"
grep -n "now()\|utcnow\|time.time" scorecard.py
```

`r1.json` / `r2.json` are throwaway scratch files used only to prove
byte-for-byte reproducibility; they are not part of this delivery. See
`captured_output.txt` for the real, captured output of every command
above.
