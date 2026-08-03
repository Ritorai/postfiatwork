# Contributor Throughput and Reliability Reporter

Stdlib-only Python 3 (`json`, `statistics`, `datetime`, `argparse`). No
third-party packages, no network.

## Exact rerun commands

```
python3 -m unittest test_throughput -v
python3 throughput.py events_ok.json     -o report_ok.json          ; echo "exit=$?"
python3 throughput.py events_breach.json -o report_breach_run1.json ; echo "exit=$?"
python3 throughput.py events_breach.json -o report_breach_run2.json ; echo "exit=$?"
sha256sum report_breach_run1.json report_breach_run2.json
cmp report_breach_run1.json report_breach_run2.json && echo BYTE-IDENTICAL
python3 throughput.py events_breach.json --refusal-ceiling 0.99 ; echo "exit=$?"
python3 throughput.py /nonexistent.json ; echo "exit=$?"
```

## Expected results

| step | result |
|------|--------|
| tests | `Ran 32 tests` / `OK` |
| ok fixture | `status=ok contributors=3 over_ceiling=0`, exit **0** |
| breach fixture (both runs) | `status=ceiling_breach contributors=3 over_ceiling=1`, exit **1** |
| both reports SHA-256 | `87b46866b8e98fca0c329414213b2dfa46966f00629472c50227b235f922fea1` |
| `cmp` | BYTE-IDENTICAL |
| `--refusal-ceiling 0.99` | exit **0** |
| missing file | `INVALID_INPUT`, exit **2** |

## Observed output on events_ok.json

```
bob    grade=C                 refusal=0.500 acc2sub=48.0 sub2term=24.0
alice  grade=A                 refusal=0.000 acc2sub=8.0  sub2term=21.0
carol  grade=INSUFFICIENT_DATA refusal=0.000 acc2sub=None sub2term=None
```

## Grades

Applied in order, first match wins:

| grade | condition |
|-------|-----------|
| INSUFFICIENT_DATA | fewer than `--min-tasks` terminal outcomes |
| A | refusal_rate ≤ 0.10 **and** median accept→submit ≤ 24h |
| B | refusal_rate ≤ 0.25 |
| C | refusal_rate ≤ `--refusal-ceiling` |
| D | refusal_rate above the ceiling |

## Three judgement calls worth reviewing

**1. A newcomer with one refusal is never branded.** `INSUFFICIENT_DATA` is
checked *before* any rate-based grade, and `over_ceiling` additionally requires
`terminal_count >= --min-tasks`. One refusal out of one task is 100% by
arithmetic but says nothing about reliability. `test_insufficient_data_never_breaches`
pins this — it is the difference between a metric and a smear.

**2. Negative durations are dropped, not reported.** A `submitted` timestamp
earlier than `accepted` is data corruption; averaging it in would yield a
nonsensical negative median. Such pairs are excluded from the median rather than
silently clamped to zero (`test_negative_duration_excluded`).

**3. First occurrence of each state wins.** A task that goes
submitted → verification_requested → submitted uses the *first* submit for the
accept→submit duration, so a resubmission does not retroactively make the
contributor look slower (`test_duplicate_state_uses_first_occurrence`).

## Divide-by-zero handling

`refusal_rate` is `0.0` when there are no terminal outcomes rather than raising
or producing `NaN`. Medians return `null` rather than `0` when no valid pair
exists — `0` would falsely read as "instant".

## Determinism

Contributors sorted by `(-refusal_rate, contributor)`; tasks and events walked
in sorted order; rates rounded to 6 dp and hours to 4 dp; `json.dumps` with
`sort_keys=True`, `separators=(",",":")`, `ensure_ascii=True`, trailing newline.

## Exit codes

0 = no contributor over ceiling · 1 = at least one over · 2 = invalid input
