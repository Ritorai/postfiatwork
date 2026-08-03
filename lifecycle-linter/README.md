# Task Lifecycle Event Linter

Stdlib-only Python 3. No third-party packages, no network.

## Exact rerun commands

```
python3 -m unittest test_lifecycle_lint -v
python3 lifecycle_lint.py events_valid.jsonl   -o report_valid.json        ; echo "exit=$?"
python3 lifecycle_lint.py events_invalid.jsonl -o report_invalid_run1.json ; echo "exit=$?"
python3 lifecycle_lint.py events_invalid.jsonl -o report_invalid_run2.json ; echo "exit=$?"
sha256sum report_invalid_run1.json report_invalid_run2.json
cmp report_invalid_run1.json report_invalid_run2.json && echo BYTE-IDENTICAL
python3 lifecycle_lint.py /nonexistent.jsonl ; echo "exit=$?"
```

## Expected results

| step | result |
|------|--------|
| tests | `Ran 26 tests` / `OK` |
| valid fixture | `status=clean findings=0`, exit **0** |
| invalid fixture (both runs) | `status=issues findings=13`, exit **1** |
| both invalid reports SHA-256 | `cca584f0202398e4d793528415d078414299727f4715b17e4f7ef34c6ac8e1aa` |
| `cmp` | BYTE-IDENTICAL |
| missing file | `UNREADABLE_INPUT`, exit **2** |

## Lifecycle graph

```
proposed               -> accepted, refused
accepted               -> submitted, refused
submitted              -> verification_requested, rewarded, refused
verification_requested -> submitted, rewarded, refused
rewarded / refused     -> terminal
```

`verification_requested -> submitted` is the resubmission loop and is **legal**.

## Finding codes (all 8 exercised by events_invalid.jsonl)

| code | count in fixture |
|------|------------------|
| MALFORMED_RECORD | 3 |
| BACKWARD_TRANSITION | 2 |
| MISSING_START | 2 |
| SKIPPED_STATE | 2 |
| DUPLICATE_STATE | 1 |
| NON_MONOTONIC_TIME | 1 |
| POST_TERMINAL_EVENT | 1 |
| UNKNOWN_STATE | 1 |

## Design note on DUPLICATE_STATE

Only a **back-to-back** repeat of the same state is a duplicate. A state may
legitimately recur through the verification loop, so a naive "have I seen this
state before" set produces a false positive on every resubmission. This was
caught by `test_resubmit_loop_allowed_not_duplicate` during development: the
valid fixture's `task_aaa` goes submitted -> verification_requested -> submitted
-> rewarded, which is a correct history and must lint clean. Repeated states
reached by an illegal path are still caught, but by the accurate code
(BACKWARD_TRANSITION or SKIPPED_STATE) rather than by DUPLICATE_STATE.

## Malformed handling

Bad lines never abort the run. Each is recorded as `MALFORMED_RECORD` with its
1-based line number, and linting continues on the remaining well-formed events.
`events_invalid.jsonl` ends with three such lines: a record missing
`occurred_at`, a non-JSON line, and a JSON array instead of an object.

## Exit codes

0 = clean · 1 = findings present · 2 = unreadable input
