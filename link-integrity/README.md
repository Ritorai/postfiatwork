# link_integrity.py

A stdlib-only Python 3 CLI that checks **cross-export link integrity**
between an exported task-lifecycle stream and an exported
evidence-submission stream.

## Scope (read this first)

This tool checks whether the two exports **agree with each other**. It
deliberately does **not** re-implement:

- **Schema validation** — it does not check that `evidence_type` or `value`
  have any particular shape, does not validate `state` against an enum of
  legal values, and does not enforce any field beyond the minimum needed to
  perform cross-export linking (`task_id`, `state`, `at` for lifecycle;
  `submission_id`, `task_id`, `submitted_at` for evidence). That is the job
  of a schema linter, not this tool.
- **Lifecycle transition-order validation** — it does not check whether a
  sequence of states is a legal progression (e.g. whether `submitted` may
  follow `rewarded`). That is the job of a lifecycle-order linter, not this
  tool.

This scope boundary is deliberate: existing linters already cover schema
shape and lifecycle-transition order. `link_integrity.py` answers one
narrower question — do the task-lifecycle export and the
evidence-submission export reference each other consistently?

## Input shape

### Lifecycle export

A JSON array of lifecycle event objects:

```json
[
  {"task_id": "task-001", "state": "created",   "at": "2026-01-01T00:00:00Z"},
  {"task_id": "task-001", "state": "submitted", "at": "2026-01-01T01:00:00Z"},
  {"task_id": "task-001", "state": "rewarded",  "at": "2026-01-01T02:00:00Z"}
]
```

Required fields, all must be present:

- `task_id`: non-empty string.
- `state`: non-empty string. This tool only gives special meaning to the
  literal values `"submitted"`, `"rewarded"`, and `"refused"`; any other
  value is passed through without interpretation (state enum validation is
  out of scope).
- `at`: string, expected to be an ISO-8601 UTC timestamp (see below).

### Evidence export

A JSON array of evidence-submission objects:

```json
[
  {"submission_id": "sub-001", "task_id": "task-001", "evidence_type": "screenshot",
   "value": "...", "submitted_at": "2026-01-01T00:30:00Z"}
]
```

Required fields, all must be present:

- `submission_id`: non-empty string.
- `task_id`: non-empty string.
- `evidence_type`, `value`: must be present as keys but their content is
  **not validated at all** (any JSON value is accepted) — validating them is
  schema-linter territory.
- `submitted_at`: string, expected to be an ISO-8601 UTC timestamp.

### Timestamp format

Only two UTC designators are accepted: a trailing `Z`, or a literal
`+00:00` offset. Both are treated as the same instant. Any other offset
(e.g. `+02:00`, `-05:00`) is treated as **non-UTC** and rejected as
`IMPOSSIBLE_TIMESTAMP`, per the task spec. Fractional seconds
(`.123456`) are accepted; fractional digits beyond 6 are truncated (not
rounded). The year must fall within `2000`-`2100` inclusive. Calendar
values are validated with Python's `datetime` constructor (so, for
example, `2026-02-30` and a `23:59:60` leap-second string are both
rejected as impossible — this tool has no notion of leap seconds).

Anything that is missing entirely, or present but of the wrong JSON type
(e.g. a number instead of a string), is a **structural** problem and causes
exit code 2 (invalid input), not a violation. A timestamp that is present
as a string but doesn't parse per the rules above is a **content** problem
and is reported as the `IMPOSSIBLE_TIMESTAMP` violation (exit code 1), not
a usage error.

## Violation codes

- **`UNKNOWN_TASK_REFERENCE`** — an evidence record's `task_id` does not
  appear in any lifecycle event. One violation per offending evidence
  record.
- **`DUPLICATE_SUBMISSION_ID`** — the same `submission_id` appears more
  than once in the evidence export. One violation per duplicated
  `submission_id`, listing every distinct `task_id` involved and how many
  times it occurred.
- **`EVIDENCE_BEFORE_TASK_CREATED`** — an evidence record's `submitted_at`
  is strictly earlier than the task's first (chronologically earliest)
  lifecycle event. Evidence submitted at **exactly** the same instant as
  task creation is **not** flagged (strict `<` comparison).
- **`EVIDENCE_AFTER_TERMINAL_STATE`** — an evidence record's `submitted_at`
  is strictly later than the task's **earliest** arrival at a terminal
  state (`rewarded` or `refused`). If a task has more than one terminal
  event (a lifecycle-order anomaly this tool does not itself judge), the
  earliest one is used as "the task reaching" terminal state, since that's
  the first point at which further evidence becomes suspect. Evidence at
  exactly the terminal instant is not flagged.
- **`MISSING_EVIDENCE_FOR_SUBMITTED_STATE`** — a task has at least one
  lifecycle event with `state == "submitted"`, but the evidence export
  contains zero records for that `task_id` (regardless of timing). One
  violation per such task.
- **`IMPOSSIBLE_TIMESTAMP`** — a timestamp string is unparseable, uses a
  non-UTC offset, or has a year outside `2000`-`2100`. Applies to both the
  lifecycle `at` field and the evidence `submitted_at` field; the violation
  records which export ("lifecycle" or "evidence") and which field it came
  from.

`ORPHAN_LIFECYCLE_TASK` is **not** a violation code emitted by this tool. A
task with lifecycle events but no evidence and no `submitted` state is a
completely normal, uninteresting case (the task simply hasn't reached the
evidence-submission point yet) and is never flagged.

### Design note on "reaching a terminal state" with anomalous re-submission

The task-lifecycle stream is not order-validated by this tool. If a task
somehow shows `rewarded` and then a *later* `submitted` event (an anomaly
that a lifecycle-order linter should catch, not this tool), evidence
submitted after the first `rewarded`/`refused` instant is still reported as
`EVIDENCE_AFTER_TERMINAL_STATE`, and `MISSING_EVIDENCE_FOR_SUBMITTED_STATE`
still fires if there is no evidence at all — because both codes just look
at facts ("did evidence arrive after the first terminal instant?", "does a
`submitted` event exist anywhere?"), not at whether the surrounding
sequence makes sense.

## Output format

Canonical JSON is written to stdout (or to `-o/--output FILE` if given):

```json
{"schema_version":"1.0","summary":{"counts_by_code":{},"is_clean":true,"violation_count":0},"violations":[]}
```

- `json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)`
  plus exactly one trailing newline.
- No runtime-dependent fields (no timestamps of the run, no absolute file
  paths) — the report is a pure function of the two input files' contents.
- `violations` is a JSON array sorted by a deterministic total ordering
  (violation code, then `task_id`, then `submission_id`, then the full
  canonical JSON of the violation object as a final tie-breaker), so two
  runs on the same inputs produce byte-identical output.

## Exit codes

- `0` — no violations found.
- `1` — one or more violations found (report was still produced).
- `2` — invalid input or usage error: missing file, unreadable file,
  malformed JSON, top-level value is not a JSON array, a record is missing
  a required field or has a field of the wrong type (including a `null`
  `task_id`), missing/extra CLI arguments, or an unwritable `-o` path. No
  report is produced in this case.

## Usage

```
python3 link_integrity.py LIFECYCLE_FILE EVIDENCE_FILE [-o OUTPUT_FILE]
```

- `-o, --output FILE` — write the canonical JSON report to `FILE` instead
  of stdout. Nothing else is printed to stdout in this mode.

## Reproducing the verification run

From this directory:

```
python3 -m unittest test_link_integrity -v
python3 link_integrity.py lifecycle_ok.json evidence_ok.json ; echo "exit=$?"
python3 link_integrity.py lifecycle_bad.json evidence_bad.json -o report_run1.json ; echo "exit=$?"
python3 link_integrity.py lifecycle_bad.json evidence_bad.json -o report_run2.json ; echo "exit=$?"
sha256sum report_run1.json report_run2.json
cmp report_run1.json report_run2.json && echo BYTE-IDENTICAL
python3 link_integrity.py /nonexistent.json evidence_ok.json ; echo "exit=$?"
python3 link_integrity.py lifecycle_ok.json ; echo "exit=$?"
```

`report_run1.json` / `report_run2.json` are scratch files produced by the
commands above; they are not part of the shipped deliverable set.

## Known limitations / false-positive risks

1. **No leap-second support.** A `submitted_at`/`at` value like
   `2026-06-30T23:59:60Z` (a real, historically valid UTC leap second) is
   rejected as `IMPOSSIBLE_TIMESTAMP` because Python's `datetime` cannot
   represent it. This is a simplification, not a spec gap — genuine leap
   seconds are astronomically rare in synthetic task-evidence data, but a
   reviewer relying on this tool against truly historical UTC data should
   be aware some legitimate timestamps could be flagged.
2. **`EVIDENCE_AFTER_TERMINAL_STATE` uses the earliest terminal event.**
   When a task's lifecycle stream contains more than one terminal-state
   event (itself an anomaly outside this tool's scope to flag), the
   *earliest* one is used as the "reached terminal" instant. This is a
   deliberate, documented choice, but a different, equally defensible
   choice (e.g. using the *latest* terminal event) would produce different
   flags for evidence submitted between two terminal events. Reviewers
   should scrutinize whether earliest-terminal is the right semantic for
   their downstream use of this report.
3. **Minimal structural validation can mask sloppy exports as "clean" for
   unrelated fields.** Because `evidence_type` and `value` are accepted
   without any validation (by design, to stay out of schema-linting
   territory), a garbled `evidence_type` or a `value` of the wrong shape
   will never be caught here — only cross-export link problems are
   reported clean/dirty. A reviewer should not treat exit code 0 from this
   tool as "the evidence export is well-formed," only as "the two exports
   agree on task references, submission uniqueness, and are chronologically
   plausible relative to each other."

## Bug found and fixed during development

The initial implementation opened input files with plain `encoding="utf-8"`.
A JSON file prefixed with a UTF-8 byte-order-mark (BOM) — which is common
in files produced by Windows-native tooling such as Notepad, PowerShell's
`Out-File`, or Excel's JSON/CSV export — decodes under plain `utf-8` to a
string with a leading U+FEFF character, which `json.loads` then rejects
with `Expecting value: line 1 column 1 (char 0)`. This meant a perfectly
valid lifecycle or evidence export could be spuriously rejected with exit
code 2 ("not valid JSON") purely because of how it was saved, not because
of its content. Fixed by opening files with `encoding="utf-8-sig"`, which
transparently strips a leading BOM if present and behaves identically to
`utf-8` when absent. A regression test,
`test_utf8_bom_prefixed_file_is_readable`, covers this in
`test_link_integrity.py`.
