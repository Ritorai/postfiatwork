# Task Lifecycle Event Linter (JSON array input)

Stdlib-only Python 3. No third-party packages, no network.

## Exact rerun commands

```
python3 -m unittest test_event_lint -v
python3 event_lint.py events_valid.json   -o report_valid.json        ; echo "exit=$?"
python3 event_lint.py events_invalid.json -o report_invalid_run1.json ; echo "exit=$?"
python3 event_lint.py events_invalid.json -o report_invalid_run2.json ; echo "exit=$?"
sha256sum report_invalid_run1.json report_invalid_run2.json
cmp report_invalid_run1.json report_invalid_run2.json && echo BYTE-IDENTICAL
python3 event_lint.py /nonexistent.json ; echo "exit=$?"
```

## Expected results

| step | result |
|------|--------|
| tests | `Ran 26 tests` / `OK` |
| valid fixture | `status=clean violations=0`, exit **0** |
| invalid fixture (both runs) | `status=violations violations=10`, exit **1** |
| both reports SHA-256 | `9ca2b3bd063b7eabb0c2c085a3aac33fb5abb9c994923cfb3d55ed6b16b775ab` |
| `cmp` | BYTE-IDENTICAL |
| missing file | `UNREADABLE_INPUT`, exit **2** |

## Transition graph

```
proposed               -> accepted, refused
accepted               -> submitted, refused
submitted              -> verification_requested, rewarded, refused
verification_requested -> submitted, rewarded, refused
rewarded / refused     -> terminal
```

`verification_requested -> submitted` is the resubmission loop and is legal.

## Violation classes (all 7 exercised by events_invalid.json)

| class | count |
|-------|-------|
| MALFORMED_EVENT | 3 |
| ILLEGAL_TRANSITION | 2 |
| DUPLICATE_EVENT | 1 |
| MISSING_PROPOSED | 1 |
| POST_TERMINAL_EVENT | 1 |
| TIMESTAMP_DISORDER | 1 |
| UNKNOWN_STATE | 1 |

## Report shape

Findings are **grouped per task**, not flat. Each entry carries `task_id`,
`event_count`, `violation_count`, a sorted `violations` list, and a per-task
`status`. Tasks are emitted in sorted `task_id` order; violations within a task
are sorted by `(violation, index)`; `json.dumps` uses `sort_keys=True`,
`separators=(",",":")`, `ensure_ascii=True` and a trailing newline.

## Semantics worth noting

- **DUPLICATE_EVENT** keys on the `(state, occurred_at)` pair, so the same state
  at a *different* timestamp is a legitimate re-entry (e.g. resubmission) and is
  not flagged. `test_same_state_different_timestamp_not_duplicate` pins this.
- **POST_TERMINAL_EVENT** short-circuits further transition checking for that
  event, so one stray post-terminal event yields exactly one violation rather
  than also reporting a spurious ILLEGAL_TRANSITION.
  `test_post_terminal_suppresses_transition_noise` pins this.
- A malformed or unknown-state element never aborts the run; it is recorded with
  its 0-based array `index` and processing continues.

## Flags

| flag | description |
|------|-------------|
| `events` (positional) | Path to a JSON array of task lifecycle events. Required. |
| `-o`, `--out PATH` | Write the canonical JSON report to this file instead of stdout. When set, stdout instead gets a one-line summary: `status=<status> violations=<count>`. |

## Exit codes

0 = clean · 1 = violations · 2 = unreadable input
