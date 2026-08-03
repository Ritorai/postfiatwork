# preflight

`preflight` is a stdlib-only Python 3 command-line tool that checks a
contributor's **task export** against its **evidence export** before
submission. It focuses on cross-file readiness: task-ID linkage, evidence-type
agreement, and non-empty evidence values. It emits a single deterministic
canonical JSON report and communicates pass/fail through its exit code, so it
is safe to run in scripts and CI.

No third-party packages, no network access. Python 3 standard library only.

## Installation / running

There is nothing to install. Put `preflight.py` somewhere and run it directly:

```
python3 preflight.py TASKS_FILE EVIDENCE_FILE [-o OUTPUT_FILE]
```

It can also be invoked as a module, as long as `preflight.py` is on the
current directory / `sys.path` (e.g. you run it from the directory that
contains it):

```
python3 -m preflight TASKS_FILE EVIDENCE_FILE [-o OUTPUT_FILE]
```

Both forms are equivalent; the module form works because `preflight.py`
guards its CLI entry point with `if __name__ == "__main__": sys.exit(main())`.

## Exit codes

| Code | Meaning |
|------|---------|
| `0`  | Ready to submit -- no issues found. |
| `1`  | Issues found. The full list is in the JSON report. |
| `2`  | Invalid input or usage error: bad CLI usage (missing/unknown arguments -- this is argparse's own default exit code), a file that doesn't exist or can't be read, invalid JSON syntax, or a top-level JSON value that is neither an object nor an array. |

## Input shape

### Task export record

```json
{
  "task_id": "T-1001",
  "title": "Write onboarding guide",
  "status": "in_review",
  "required_evidence": ["url", "text"]
}
```

| Field | Type | Required | Notes |
|---|---|---|---|
| `task_id` | string, non-empty | yes | Identifies the task; joins to evidence records via `evidence.task_id`. |
| `title` | string | yes | May be an empty string; just must be a string. |
| `status` | string, non-empty | yes | Free-form contributor/task status. Two values are treated specially: `"refused"` and `"rewarded"` (see `UNSUBMITTABLE_STATUS` below). Matching is case-sensitive. |
| `required_evidence` | array of strings | yes | The evidence types this task expects. May be an empty array (task needs no evidence). |

### Evidence export record

```json
{
  "submission_id": "S-1",
  "task_id": "T-1001",
  "evidence_type": "url",
  "value": "https://example.com/guide",
  "notes": "draft link"
}
```

| Field | Type | Required | Notes |
|---|---|---|---|
| `submission_id` | string, non-empty | yes | Unique identifier for this evidence submission. |
| `task_id` | string, non-empty | yes | Must match a `task_id` in the task export. |
| `evidence_type` | string, non-empty | yes | Compared, case-sensitively, against the owning task's `required_evidence` list. |
| `value` | any JSON type | key must be present | The evidence payload itself. Checked for emptiness (see `EMPTY_EVIDENCE_VALUE`); non-string values are never considered empty. |
| `notes` | any | no | Not validated at all; purely informational. |

### Both files accept either shape

Each input file may contain **either** a single JSON object (one record) or a
JSON array of objects. A bare object is treated as a one-element array. Any
other top-level JSON value (string, number, boolean, `null`) is an invalid-input
error (exit `2`).

## Issue codes

`preflight` performs these checks and reports every problem it finds as one
entry in the `issues` array of the report:

| Code | Fires when... |
|---|---|
| `ORPHAN_EVIDENCE` | An evidence record's `task_id` does not match any (valid) task record. |
| `TASK_MISSING_EVIDENCE` | A task's `required_evidence` includes a type for which **no** evidence record (of that `task_id` and `evidence_type`) exists at all. A record that exists but has an empty value does **not** count as missing -- it is reported via `EMPTY_EVIDENCE_VALUE` instead, not double-counted here. |
| `EVIDENCE_TYPE_MISMATCH` | An evidence record's `evidence_type` is not present in its task's `required_evidence` list (only checked when the task itself is found; see `ORPHAN_EVIDENCE`). |
| `EMPTY_EVIDENCE_VALUE` | An evidence record's `value` is missing, JSON `null`, an empty string, or a whitespace-only string (unicode-aware -- see below). |
| `DUPLICATE_SUBMISSION_ID` | Two or more (well-formed) evidence records share the same `submission_id`. One issue is emitted per duplicated ID, with a `count` field. |
| `MALFORMED_RECORD` | A task or evidence record is missing a required field, has the wrong type for a required field, or is not a JSON object at all. One issue is emitted per bad field (so a record with three bad fields produces three `MALFORMED_RECORD` issues). |
| `UNSUBMITTABLE_STATUS` | A task's `status` is `"refused"` or `"rewarded"` -- statuses where submitting new evidence makes no sense. |

A malformed task record (any bad/missing field) is excluded entirely from
cross-file checks -- it cannot reliably be linked to evidence, so it does not
participate in `TASK_MISSING_EVIDENCE` or `UNSUBMITTABLE_STATUS` checks, and
any evidence pointing at its `task_id` is reported as `ORPHAN_EVIDENCE`
instead of a more specific error. Likewise, a malformed evidence record is
excluded from duplicate/orphan/mismatch/empty-value checks (only its own
`MALFORMED_RECORD` issue is emitted).

### Unicode whitespace

`EMPTY_EVIDENCE_VALUE`'s whitespace check uses Python's `str.isspace()`,
which recognizes the full Unicode whitespace set -- not just ASCII space,
tab, and newline. This includes characters such as U+00A0 (NO-BREAK SPACE)
and U+3000 (IDEOGRAPHIC SPACE), which a naive `str.strip()` implementation
using only default whitespace would already catch too, but the tool is
explicit and tested against a wide range of individual Unicode whitespace
code points (see `test_preflight.py`).

## Output format (canonical JSON)

The report is a single JSON object:

```json
{
  "ready": true,
  "summary": {
    "task_count": 3,
    "evidence_count": 3,
    "issue_count": 0,
    "issue_counts_by_code": {
      "DUPLICATE_SUBMISSION_ID": 0,
      "EMPTY_EVIDENCE_VALUE": 0,
      "EVIDENCE_TYPE_MISMATCH": 0,
      "MALFORMED_RECORD": 0,
      "ORPHAN_EVIDENCE": 0,
      "TASK_MISSING_EVIDENCE": 0,
      "UNSUBMITTABLE_STATUS": 0
    }
  },
  "issues": []
}
```

- Serialized with `json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)` plus a single trailing `\n`.
- `issue_counts_by_code` always lists all seven codes, even when their count is `0`.
- `issues` is sorted by a deterministic total ordering (issue code first, then
  every other field name/value pair, alphabetically) so that re-running the
  tool on logically identical input -- regardless of the order records
  appeared in the source files -- produces byte-identical output.
- The report never includes wall-clock time, hostnames, or absolute
  filesystem paths, so it can be diffed or hashed across runs/machines.

## CLI usage

```
python3 preflight.py TASKS_FILE EVIDENCE_FILE [-o FILE | --output FILE]
```

- `TASKS_FILE`, `EVIDENCE_FILE` -- required positional paths to the two JSON export files.
- `-o FILE`, `--output FILE` -- write the canonical JSON report to `FILE` instead of stdout. Nothing is printed to stdout in this mode.

Examples:

```
$ python3 preflight.py tasks_ready.json evidence_ready.json
{"issues":[],"ready":true, ...}
$ echo $?
0

$ python3 preflight.py tasks_issues.json evidence_issues.json -o report.json
$ echo $?
1
```

## Running the tests

```
python3 -m unittest test_preflight -v
```

## Limitations / false-positive risks

A reviewer evaluating this tool's output should be aware of these:

1. **Duplicate `task_id` records are not flagged.** If the task export
   contains two records with the same `task_id` (e.g. because of an export
   bug), `preflight` silently keeps the *first* one for all cross-file
   checks and ignores the rest -- there is no dedicated issue code for this.
   If the two records disagree (different `required_evidence`, different
   `status`), the disagreement is invisible in the report, which could mask
   real data corruption.

2. **Case-sensitive matching can double-report a single typo.** Both
   `evidence_type` and `status` comparisons are exact, case-sensitive string
   matches. If a task requires `"url"` and the evidence was submitted as
   `"URL"`, the tool emits **both** `EVIDENCE_TYPE_MISMATCH` (the submitted
   type isn't in the required list) **and** `TASK_MISSING_EVIDENCE` (the
   required type has no matching submission) for what a human would likely
   see as one simple capitalization mistake.

3. **`EMPTY_EVIDENCE_VALUE` only catches `null`, `""`, and whitespace-only
   strings**, per a literal reading of the spec. Non-string "empty-ish"
   values -- `0`, `false`, `[]`, `{}` -- are never flagged, even though a
   reviewer might reasonably consider an empty list or empty object as "no
   evidence provided." This is a real false-negative risk if a client's
   evidence export ever uses `[]` or `{}` as a placeholder for missing data.

4. (Related to #1) **A malformed task record suppresses more specific
   diagnostics for its evidence.** If a task record fails validation (e.g.
   `status` has the wrong type), it is excluded from the task lookup table
   entirely. Any evidence that references its `task_id` is then reported as
   `ORPHAN_EVIDENCE` ("unknown task") rather than, say, a mismatch or
   missing-evidence issue tied to the real (but malformed) task. A reviewer
   scanning only for `ORPHAN_EVIDENCE` entries could misinterpret this as a
   genuinely unrelated/unknown task rather than a task with a data-quality
   problem -- always cross-reference `MALFORMED_RECORD` issues for the same
   `task_id` value appearing as `ORPHAN_EVIDENCE`.

## Known bug found and fixed during development

Testing surfaced a real bug: if a task's `required_evidence` list contained
a duplicate entry (e.g. `["url", "url", "text"]`), the tool emitted a
duplicate, byte-for-byte identical `TASK_MISSING_EVIDENCE` issue for every
repetition, inflating `issue_count` and the report itself. This has been
fixed by deduplicating `required_evidence` (preserving first-seen order)
before generating `TASK_MISSING_EVIDENCE` issues. Regression tests
`test_duplicate_entries_in_required_evidence_deduped` and
`test_duplicate_entries_in_required_evidence_with_matching_evidence` in
`test_preflight.py` cover this.
