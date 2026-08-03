# XRPL Payout Reference Auditor

Stdlib-only Python 3. No third-party packages, no network.

## Exact rerun commands

```
python3 -m unittest test_payout_audit -v
python3 payout_audit.py payouts_clean.json roster.json -o audit_clean.json      ; echo "exit=$?"
python3 payout_audit.py payouts_dirty.json roster.json -o audit_dirty_run1.json ; echo "exit=$?"
python3 payout_audit.py payouts_dirty.json roster.json -o audit_dirty_run2.json ; echo "exit=$?"
sha256sum audit_dirty_run1.json audit_dirty_run2.json
cmp audit_dirty_run1.json audit_dirty_run2.json && echo BYTE-IDENTICAL
python3 payout_audit.py /nonexistent.json roster.json ; echo "exit=$?"
```

## Expected results

| step | result |
|------|--------|
| tests | `Ran 27 tests` / `OK` |
| clean fixture | `status=clean issues=0`, exit **0** |
| dirty fixture (both runs) | `status=issues issues=9`, exit **1** |
| both audits SHA-256 | `0f4a873696761cf94b9b1da8b7d18fe2ddb01f0ac32c3c21b71aa5f2e9c195b6` |
| `cmp` | BYTE-IDENTICAL |
| missing file | `UNREADABLE_INPUT`, exit **2** |

## Issue codes (all 5 exercised by payouts_dirty.json)

| code | count | what triggers it in the fixture |
|------|-------|--------------------------------|
| MALFORMED_RECORD | 2 | `p8` missing wallet/tx_hash; a bare string element |
| MALFORMED_TX_HASH | 2 | `p5` lowercase hash; `p7` the literal `SHORT` |
| REUSED_ACROSS_TASKS | 2 | `p1` and `p2` share one hash under two different tasks |
| REUSED_WITHIN_TASK | 2 | `p3` and `p4` repeat one hash under the same task |
| UNKNOWN_TASK_ID | 1 | `p6` references `task_NOT_IN_ROSTER` |

## Hash contract

`^[0-9A-F]{64}$` — exactly 64 **uppercase** hex characters. Lowercase is
rejected rather than normalised: XRPL renders transaction hashes uppercase, so a
lowercase value indicates the reference was transcribed or transformed somewhere
in the pipeline, which is worth surfacing rather than silently accepting.

## Reuse semantics

Both sides of a reuse are flagged, not just the later occurrence, because at
audit time there is no basis for deciding which record is the legitimate one —
that is a human call. `REUSED_ACROSS_TASKS` and `REUSED_WITHIN_TASK` are
independent: a hash repeated under one task yields only the within-task code, as
pinned by `test_within_task_reuse_is_not_across_task`.

## Roster format

Accepts either a plain array of task-ID strings or an array of objects carrying
a `task_id` key, so it can consume an existing task export without reshaping.

## Malformed handling

A bad element never aborts the run. It is recorded with its 0-based array index
and auditing continues over the remaining well-formed records, so one corrupt
row cannot hide real settlement issues further down the file.

## Determinism

Issues sorted by `(issue, index, payout_id)`; hash groups walked in sorted order;
`json.dumps` with `sort_keys=True`, `separators=(",",":")`, `ensure_ascii=True`,
trailing newline, explicit utf-8 on write.

## Flags

| flag | description |
|------|-------------|
| `payouts` (positional) | Path to a JSON array of recorded payouts to audit. Required. |
| `roster` (positional) | Path to a JSON array of known task_ids (each entry a bare string or an object with a `task_id` field) that payouts are checked against. Required. |
| `-o`, `--out PATH` | Write the canonical JSON report to this file instead of stdout. When set, stdout instead gets a one-line summary: `status=<status> issues=<n>`. |

## Exit codes

0 = clean · 1 = issues found · 2 = unreadable input
