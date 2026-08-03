# consolidate.py

A standard-library-only Python 3 CLI that discovers per-tool JSON reports
under a directory tree and produces **one canonical, deterministic
consolidated report**. It preserves per-tool provenance, computes a global
severity rollup, and merges findings about the same `task_id` across tools
without ever collapsing two distinct sources into one.

No third-party imports. No network access. `python3` standard library only.

## Why this exists

The sibling report-generating tools in this repo (`lifecycle-linter`,
`sybil-detector`, `xrpl-auditor`, `throughput-reporter`, `budget-forecaster`,
`queue-auditor`, `preflight`, `link-integrity`, `staleness-monitor`,
`wallet-reconciler`, `dup-detector`, `evidence-scorer`, `evidence-manifest`,
`reward-reconciler`, `schema-checker`, `xrpl-address`, `event-linter`,
`evidence-harness`) each emit their own JSON report shape. None of them share
a schema, and **none of them embed a field that names the producing tool**.
This CLI's adapter layer fingerprints each report structurally and normalises
it into one common finding shape, so a reviewer can look at a single
snapshot instead of manually reconciling eighteen different JSON dialects.

## Usage

```
python3 consolidate.py ROOT [-o FILE] [--severity-threshold {info,warning,error,critical}]
```

- `ROOT` -- directory to scan recursively for `*.json` files.
- `-o FILE`, `--output FILE` -- write the canonical report to `FILE` instead
  of stdout.
- `--severity-threshold LEVEL` -- only roll up findings at or above `LEVEL`
  (default: no filtering, i.e. everything from `info` up).

### Exit codes

| code | meaning |
|------|---------|
| 0 | scan succeeded, zero findings remain after severity filtering |
| 1 | scan succeeded, one or more findings remain after severity filtering |
| 2 | invalid input / usage error (bad args, missing/non-directory root, unwritable `-o` path) |

## Reproduce everything

```
python3 -m unittest test_consolidate -v
python3 consolidate.py reports_clean ; echo "exit=$?"
python3 consolidate.py reports_mixed -o report_run1.json ; echo "exit=$?"
python3 consolidate.py reports_mixed -o report_run2.json ; echo "exit=$?"
sha256sum report_run1.json report_run2.json
cmp report_run1.json report_run2.json && echo BYTE-IDENTICAL
python3 consolidate.py reports_mixed --severity-threshold critical ; echo "exit=$?"
python3 consolidate.py /nonexistent_dir ; echo "exit=$?"
grep -c "/sessions\|/tmp\|/home" report_run1.json ; echo "abs-path-grep-exit=$?"
```

`report_run1.json` / `report_run2.json` are scratch verification artifacts,
not part of the deliverable -- they are not shipped in this directory or the
zip.

## Determinism contract

- Canonical JSON: `json.dumps(obj, sort_keys=True, separators=(",",":"),
  ensure_ascii=True)` plus a single trailing `\n`.
- File discovery walks the tree and is **fully sorted** on the final flat
  list of relative paths -- the underlying OS's directory-iteration order
  never leaks into the result.
- **No absolute paths ever appear in the output.** Every `source_report`
  value is relative to the scanned root, using forward slashes regardless of
  platform. This was verified explicitly (see "Relocation test" below): the
  same directory copied to a different absolute path on a different machine
  produces a byte-identical report.
- No wall-clock timestamps, PIDs, hostnames, or other machine-specific
  values are emitted anywhere in the output.
- Running `-o` with a path that lives inside the scanned root is guarded:
  the CLI excludes its own about-to-be-written output file from discovery
  (by resolved path), so re-running the tool twice with `-o` inside `ROOT`
  does not make run #2 "discover" run #1's own output as an extra report.
  (This was a real bug caught by the test suite -- see "Bug found" below.)

## Observed report shapes

Before writing any adapter, real report files from each sibling tool were
inspected directly. This is what was actually observed (trimmed):

**lifecycle-linter** (`report_invalid_run1.json`) -- flat array under `findings`,
items keyed by `code`/`detail`/`line`/`task_id`; also has `finding_counts` and
a `totals` dict with `events`/`findings`/`tasks`:
```json
{"findings":[{"code":"BACKWARD_TRANSITION","detail":"...","line":8,"task_id":"task_backward"}],
 "finding_counts":{...},"totals":{"events":19,"findings":13,"tasks":7}}
```

**sybil-detector** (`report_sybil_run1.json`) -- no `findings`/`issues` array
at all; a `clusters` array of `{alert, pairs, score, signals, size, wallets}`,
with nested `pairs`:
```json
{"clusters":[{"alert":true,"score":1.0,"wallets":["rSyb1","rSyb2","rSyb3"],...}],
 "totals":{...}}
```

**xrpl-auditor** (`audit_dirty_run1.json`) -- array under `issues`, code field
is named `issue` (not `code`), plus `payout_id`/`task_id`/`index`:
```json
{"issues":[{"issue":"UNKNOWN_TASK_ID","detail":"...","payout_id":"p6","task_id":"task_NOT_IN_ROSTER"}],
 "totals":{"roster_tasks":5,"well_formed_payouts":7,...}}
```

**throughput-reporter** (`report_breach_run1.json`) -- **no findings array at
all**. An aggregate per-contributor report with `grade`/`over_ceiling`; only
the top-level `status` and per-contributor `over_ceiling` flag signal a
problem:
```json
{"contributors":[{"contributor":"dave","grade":"D","over_ceiling":true,"refusal_rate":0.667,...}],
 "grade_counts":{"A":1,"D":1,...}}
```

**budget-forecaster** (`forecast_breach.json`) -- also **no findings array**,
a single aggregate object with `over_budget: true/false`:
```json
{"over_budget":true,"projection":{"projected_total":"41.500000",...},
 "config":{"budget_cap":"20.000000",...}}
```

**queue-auditor** -- `findings` array like lifecycle-linter, but the counters
key is singular `finding_count`, and the overall status field is called
`result`, not `status`.

**preflight** -- `issues` array, code field `code`, message field `message`
(not `detail`), plus a unique `ready: bool` top-level field. Some issues
(e.g. `DUPLICATE_SUBMISSION_ID`) have no `task_id` at all, only
`submission_id`.

**link-integrity** -- `violations` array, `code`/`message`, top-level
`schema_version` + `summary.is_clean`. `DUPLICATE_SUBMISSION_ID` violations
reference **multiple** task ids via a plural `task_ids` list instead of a
singular `task_id`.

**staleness-monitor** -- `findings` is **an object keyed by severity bucket**
(`critical`/`warning`/`info`), not a flat array:
```json
{"findings":{"critical":[{...,"bucket":"critical","code":"OVERDUE_PROPOSED","task_id":"S-1"}],
 "warning":[...],"info":[...]},"windows":{...}}
```

**wallet-reconciler** -- `findings` array keyed by `event_id`, **no
`task_id` concept at all** (it's ledger/event-oriented, not task-oriented).
Some finding types (e.g. `DUPLICATE_EVENT_ID`) have no `detail`/`message`
text field whatsoever.

**dup-detector** -- totally different: `flagged_pairs` array of
`{submission_id_a, submission_id_b, score, overlap_count,
overlapping_shingles}`. No `code`, no `task_id`, no severity of any kind.

**evidence-scorer** -- `records` array of `{submission_id, passed, score,
components, evidence}`. No explicit findings; a "finding" only exists
implicitly as `passed: false`.

**evidence-manifest** -- a Merkle-style manifest (`algorithm`, `batch_root`,
`entries`, `leaf_prefix`, ...). **Not a findings report at all** -- this is
used deliberately as the real-world example of a sibling tool whose output
correctly falls through to `UNRECOGNISED_REPORT_SHAPE` rather than being
silently skipped.

**reward-reconciler** -- `findings` array, code field named `issue` (like
xrpl-auditor) but items carry `wallet` instead of `payout_id`, and never
include a `detail`/`message` text field.

**schema-checker** -- `violations` array of `{code, message, pointer}`. No
`task_id` at all (structural JSON-pointer violations, not task-scoped).

**xrpl-address** -- `addresses` array where each item carries `issues` as a
**list of bare code strings** (`["BAD_CHECKSUM"]`), not a list of finding
objects.

**event-linter** -- nested: top-level `tasks` array, each task carries its
own `violations` list; the code field inside a violation is named
`violation` (yet another vocabulary word). Some inner violations have
`task_id: null` and must fall back to the outer task's `task_id`.

**evidence-harness** -- `checks` array of `{check, detail, status: "pass"/
"fail", gaps, evidence}`. Bundle-level, not task-level; only `status ==
"fail"` checks are findings.

## Adapter layer

`consolidate.py` ships one adapter function per shape above (17 total),
registered in `ADAPTERS`. Each adapter receives the parsed top-level JSON
value and returns `None` if the shape doesn't match, or a (possibly empty)
list of normalised findings if it does. Adapters fingerprint reports using a
combination of distinctive top-level/nested keys (there is no "tool name"
field in any observed report to key off of directly) -- see the `adapt_*`
functions and `test_every_known_adapter_matches_exactly_one_of_the_registry`
in `test_consolidate.py`, which asserts none of the 17 canned real-shape
samples cross-matches another tool's adapter.

Any JSON file that:
- fails to parse as JSON at all → `INVALID_JSON` (severity `critical`)
- parses but its top-level value is not a JSON object → `UNRECOGNISED_REPORT_SHAPE`
- is a JSON object but matches none of the 17 registered shapes → `UNRECOGNISED_REPORT_SHAPE`
- cannot be opened (permission error, etc.) → `UNREADABLE_FILE` (severity `critical`)

...becomes its own finding rather than being silently dropped. These
synthetic findings are always `severity: "critical"`, which means they
always survive `--severity-threshold` filtering (the threshold is a floor,
and critical is the top of the scale) -- an unrecognised report can never be
hidden by cranking up the threshold.

## Normalised finding shape

```json
{
  "source_tool": "lifecycle-linter",
  "source_report": "sub/dir/report.json",
  "task_id": "task_backward",
  "code": "BACKWARD_TRANSITION",
  "severity": "error",
  "detail": "'submitted' -> 'proposed' moves backward"
}
```

When the source report has no explicit detail/message text (wallet-reconciler,
reward-reconciler), `detail` is synthesised deterministically from the
item's remaining fields, sorted by key: `"amount=-5; event_id=\"e4\"; ..."`.

## Severity normalisation

None of the 18 sibling tools use a shared severity vocabulary, and only
**one** (`staleness-monitor`, via its `bucket` field) expresses severity
explicitly at all. Canonical order (low to high):
`info < warning < error < critical`.

| source tool | default severity | override rule |
|---|---|---|
| lifecycle-linter | error | -- |
| event-linter | error | -- |
| xrpl-auditor | error | -- |
| queue-auditor | error | -- |
| link-integrity | error | -- |
| preflight | error | -- |
| schema-checker | error | -- |
| reward-reconciler | error | -- |
| wallet-reconciler | error | `CLOSING_BALANCE_MISMATCH` / `NEGATIVE_RUNNING_BALANCE` → **critical** (the ledger doesn't balance) |
| xrpl-address | error | `DENYLISTED` → **critical** (compliance hit) |
| staleness-monitor | *(from source `bucket` field)* | the tool's own `critical`/`warning`/`info` vocabulary happens to equal our canonical vocabulary exactly, so it is used verbatim |
| evidence-harness | error | -- (only `status: "fail"` checks become findings) |
| dup-detector | warning | -- (a similarity score is inherently probabilistic, never treated as a certain defect) |
| evidence-scorer | warning | -- (only `passed: false` records become findings) |
| throughput-reporter | warning | -- (only `over_ceiling: true` contributors become findings) |
| sybil-detector | critical | -- (only `alert: true` clusters become findings; the tool already gates this at its own `alert_threshold`) |
| budget-forecaster | critical | -- (only emitted when `over_budget: true`; financial breach) |

Internal-tool errors (`INVALID_JSON`, `UNRECOGNISED_REPORT_SHAPE`,
`UNREADABLE_FILE`) are always `critical`.

## Grouping and provenance

Findings with a non-null `task_id` are grouped into `findings_by_task`, one
entry per distinct `task_id`, sorted lexicographically:

```json
{
  "task_id": "task_shared_demo",
  "worst_severity": "error",
  "contributing_sources": [
    {"source_tool": "lifecycle-linter", "source_report": "..."},
    {"source_tool": "xrpl-auditor", "source_report": "..."}
  ],
  "findings": [ /* both original findings, unmerged, verbatim */ ]
}
```

Two sources reporting on the same task are **never** collapsed into one
representative finding -- both appear in `findings`, and both appear in
`contributing_sources`. Findings with `task_id: null` (most tools that are
not task-scoped: wallet-reconciler, dup-detector, schema-checker, xrpl-address,
evidence-scorer, throughput-reporter, budget-forecaster, sybil-detector, plus
any task_id-less item from a task-scoped tool) go into `ungrouped_findings`
instead, sorted deterministically.

Every scanned file -- recognised or not, zero findings or not -- gets one
entry in the top-level `reports` array (`source_report`, `source_tool`,
`recognised`, `finding_count`), independent of `--severity-threshold`. This
is the full provenance ledger: a reviewer can always see every file that was
scanned and how it was classified, even when filtering findings down to
`critical` only.

## Global severity rollup

```json
"severity_rollup": {"counts": {"info": 0, "warning": 5, "error": 40, "critical": 21}, "worst_severity": "critical"}
```

`--severity-threshold LEVEL` filters findings (and therefore the rollup,
`findings_by_task`, `ungrouped_findings`, `totals.findings_total`, and the
exit code) down to items at or above `LEVEL`. It does **not** filter the
`reports` provenance list, which always reflects the unfiltered scan.

## Bug found during testing

While hunting for a real bug per the task's edge-case list, the test suite
caught a genuine one: **writing `-o FILE` where `FILE` lives inside the
directory being scanned corrupts the *next* run.** Run #1 writes its
consolidated report into the scanned root; run #2 then scans that root again
and "discovers" run #1's own output as an additional (unrecognised) JSON
report, inflating `reports_scanned`/`reports_unrecognised` and potentially
flipping the exit code. This was caught by
`test_two_cli_runs_produce_byte_identical_output_files` when it was first
written to write `-o` inside the scanned directory (it failed: run #2's
report differed from run #1's). Fixed by having `discover_json_files()`
accept an `exclude_realpath` (the resolved path of the file about to be
written) and skip it during the walk; `build_report()`/`main()` thread this
through from `-o`. Regression test:
`test_output_file_written_inside_scanned_root_excludes_itself_on_rerun` in
`test_consolidate.py`.

## Limitations / false-positive risks a reviewer should scrutinise

1. **Structural fingerprinting, not a declared schema.** No sibling tool
   embeds a "this is tool X's report" marker. Adapters match on a handful of
   distinctive keys (e.g. `"trace" in data and "ledger_version" in data` for
   wallet-reconciler). A *hypothetical* future report shape that happens to
   reuse the same distinctive key combination for an unrelated purpose could
   be silently misattributed to the wrong tool (it would still be attributed
   to *some* recognised tool, not dropped -- but the `source_tool` label
   could be wrong). `test_every_known_adapter_matches_exactly_one_of_the_registry`
   guards the 17 shapes captured here, but cannot guard against tools not
   yet observed.

2. **Multi-task findings are deliberately not split, and become invisible to
   task-level grouping.** `link-integrity`'s `DUPLICATE_SUBMISSION_ID`
   violation references two task_ids via a plural `task_ids` list. Rather
   than manufacture two synthetic per-task findings that don't exist in the
   source report, this finding's `task_id` is set to `null` and it lands in
   `ungrouped_findings`. A reviewer relying purely on `findings_by_task` to
   audit a specific task could miss a real cross-task finding that
   references that task only via the plural field.

3. **Severity is a per-adapter/per-code default, not a per-tool-configured
   value.** Only `staleness-monitor` carries genuine severity information in
   its own report; every other tool's severity is this consolidator's
   editorial judgement (documented in the mapping table above), not
   something the source tool asserts. Two organisations consolidating the
   same underlying reports could reasonably disagree with, e.g., "all
   dup-detector matches are `warning`, never `critical`" regardless of how
   high the similarity score is -- the score itself is available in
   `detail` for a reviewer to re-judge, but the automatic rollup will not
   escalate it.

## Test suite

`python3 -m unittest test_consolidate -v` runs 152 tests covering: canonical
serialisation, every adapter (positive match + negative/cross-shape
rejection + at least one behavioural assertion), discovery (empty dir,
non-JSON files, nested subdirectories, case-insensitive extension, sorted
output, no absolute paths), the full pipeline (invalid JSON, empty array,
unicode, permission errors, cross-tool task merging, identical-content files
not collapsing, severity filtering, threshold-survives-for-unrecognised),
determinism (two in-process runs, a relocated directory copy, walk-order
independence), the CLI (all exit codes, `-o`/`--output`, `--severity-threshold`
validation, byte-identical repeated writes, the self-exclusion regression),
and the shipped `reports_clean`/`reports_mixed` fixtures themselves.
