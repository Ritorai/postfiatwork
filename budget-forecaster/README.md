# Deterministic Task-Reward Budget Forecaster

Stdlib-only Python 3 (`json`, `decimal`, `statistics`, `datetime`, `argparse`).
No third-party packages, no network. All money is `Decimal` at 6 dp.

## Exact rerun commands

```
python3 -m unittest test_forecast -v
python3 forecast.py history.json -k open_tasks.json --budget-cap 10000 -o forecast_run1.json ; echo "exit=$?"
python3 forecast.py history.json -k open_tasks.json --budget-cap 10000 -o forecast_run2.json ; echo "exit=$?"
sha256sum forecast_run1.json forecast_run2.json
cmp forecast_run1.json forecast_run2.json && echo BYTE-IDENTICAL
python3 forecast.py history.json -k open_tasks.json --budget-cap 20 ; echo "exit=$?"
python3 forecast.py history_empty.json  ; echo "exit=$?"
python3 forecast.py history_single.json ; echo "exit=$?"
python3 forecast.py /nonexistent.json   ; echo "exit=$?"
```

## Expected results

| step | result |
|------|--------|
| tests | `Ran 36 tests` / `OK` |
| cap 10000 (both runs) | `status=within_budget projected_total=41.500000`, exit **0** |
| both forecasts SHA-256 | `6cf2662f5ff02f9efbb31f7ee988694159312057bc2e08e96d71201b85fa0ebe` |
| `cmp` | BYTE-IDENTICAL |
| cap 20 | `status=over_budget`, exit **1** |
| empty history | exit **0** |
| single-record history | exit **0** |
| missing file | `INVALID_INPUT`, exit **2** |

## Worked example (forecast_run1.json)

History: 6 rewards totalling 30.000000 over a 28-day span → **burn_per_week
7.500000**. Horizon 4 weeks → projected_burn 30.000000. Open tasks commit
11.500000 → **projected_total 41.500000**. Reward stdev 3.449638 against mean
5.000000 gives a relative spread of ~0.69, applied to the projected burn →
variance band 20.697828, so the range is **20.802172 – 62.197828**.

## Two deliberate refusals to guess

**1. A single history record yields `burn_per_week: null`, not zero.** One point
in time establishes no rate at all. Emitting `0` would read as "this project
spends nothing per week", which is a confident and wrong claim; `null` says
"unknown", which is true. `test_single_record_burn_is_null` pins it, and the
same applies to `span_days`.

**2. Floats are rejected outright (exit 2).** A JSON float cannot represent
settlement values exactly, and silently coercing one would put rounding error
into a budget figure someone may act on. Integers and decimal strings are
accepted; `3.5` as a JSON number is not (`test_float_reward_rejected`).

## Other boundary choices

- **Exactly at the cap is not a breach.** Spending the whole budget is not
  overspending it, so the comparison is strictly `>` (`test_exactly_at_cap_is_not_a_breach`).
- **`low` is floored at 0.** A wide variance band on a small projection would
  otherwise produce a negative lower bound, which is meaningless for spend.
- **No `--budget-cap` means no breach is possible**, rather than defaulting to
  some invented ceiling.

## Scope limit

The forecast is a linear extrapolation of the historical mean burn rate. It
assumes the future resembles the observed window and cannot anticipate a change
in task volume, pricing, or contributor count. The variance band communicates
historical spread, not forecast confidence — it is not a statistical prediction
interval and should not be read as one.

## Exit codes

0 = within budget (or no cap set) · 1 = projected spend exceeds cap · 2 = invalid input
