# Contributor Throughput and Reliability Reporter

Stdlib-only Python 3 -- `argparse`, `datetime`, `json`, `os`,
`statistics` and `sys` between the tool and its stale-number check. No
third-party packages, no network.

## Exact rerun commands

```
python3 -m unittest test_throughput test_check_counts -v
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
| tests | `Ran 89 tests` / `OK` |
| ok fixture | `status=ok contributors=3 over_ceiling=0`, exit **0** |
| breach fixture (both runs) | `status=ceiling_breach contributors=3 over_ceiling=1`, exit **1** |
| both reports SHA-256 | `87b46866b8e98fca0c329414213b2dfa46966f00629472c50227b235f922fea1` |
| `cmp` | BYTE-IDENTICAL |
| `--refusal-ceiling 0.99` | exit **0** |
| missing file | `INVALID_INPUT`, exit **2** |

## Stale-number check

The three committed reports are each a function of one event fixture and
the flags the run was given, and until now nothing in this directory
would have noticed when they stopped agreeing. `check_counts.py`
recomputes all three from the fixtures and the documented rerun commands
and refuses to pass when a committed value and its source inputs have
drifted apart.

One traced number, as a worked example: `report_ok.json` records
`contributors[bob].counts.rewarded` = **1**. Its source input is
`events_ok.json`; the rule is "one for each distinct `task_id` bob
appears on that carries a `rewarded` event", and `t3` is the only one.
Every other value below is traced the same way.

| artifact | source input | flags | paths traced |
|----------|--------------|-------|--------------|
| `report_ok.json` | `events_ok.json` | none (defaults) | 45 |
| `report_breach_run1.json` | `events_breach.json` | none (defaults) | 45 |
| `report_breach_run2.json` | `events_breach.json` | none (defaults) | 45 |

Forty-three paths per report -- `report_version`, both `config` values,
three `totals`, three `grade_counts`, `status`, and eleven per
contributor for three contributors (the six `counts`, both medians,
`refusal_rate`, `grade`, `over_ceiling`) -- plus a check that the
contributors come back in the documented order and a check that the file
is in the canonical form `serialize()` writes. 135 in total.

**There are no exemptions.** Every path is either recomputed from the
fixture or pinned to the invocation this README documents.
`report_version` is pinned to `1.0` and the two `config` values to
argparse's defaults, because all three reports come from a command with
no flags; `test_the_pinned_flags_are_the_documented_ones` reads those
defaults out of `throughput.py` rather than restating them. Reading
`config` back out of the report being checked would have made a report
naming flags the documented command never passed look fresh.

Run it from this directory as `python3 check_counts.py`. On the committed
tree it prints one line and exits **0**:

```
checked=135 stale=0 missing=0 unexpected=0 duplicate=0 format=0
```

Pointed at a directory whose values have drifted -- the tests point it at
a temporary copy with one value edited -- it names the path, prints what
it recomputed against what it found, and exits **1**:

```
STALE report_ok.json contributors[bob].counts.rewarded expected=1 found=99
checked=135 stale=1 missing=0 unexpected=0 duplicate=0 format=0
```

Exit **2** covers a file it cannot read, a fixture the tool itself would
refuse, a report shape it does not recognise, and a usage error, matching
the tool's own 0/1/2 split. An optional single argument names the
directory to check; it defaults to the one holding the script, and the
script is always run through `python3` rather than an executable bit.

### Coverage is by construction, not by a list

Both the committed report and the recomputation are flattened to dotted
paths and the two sets compared:

| verdict | meaning |
|---------|---------|
| `STALE` | the path is in both and the values differ |
| `MISSING` | the recomputation produces it, the report does not carry it |
| `UNEXPECTED` | the report carries it, the recomputation does not produce it |
| `DUPLICATE` | one contributor name appears in `contributors` more than once |
| `FORMAT` | the file is not the canonical rendering of its own contents |

Comparison is on the canonical JSON rendering of each value, not on `==`,
because in Python `1 == 1.0 == True`. A count that turned into `13.0` or
`false`, or a `min_tasks` of `2.0` where `argparse` would have produced
`2`, is reported rather than accepted.

A repeated contributor name is disambiguated as `name#2` rather than
overwritten, so a tampered value cannot shelter behind its twin; without
that, the entry that happened to come second silently replaced the first.
The `FORMAT` check exists because every value in a report can be right
while the file still fails to reproduce byte for byte -- a pretty-printed
copy, a missing trailing newline, or a duplicated JSON key whose first
occurrence `json.loads` throws away.

### The recomputation rule

Mirrors `analyze()`. Every part of it that is easy to get wrong is pinned
by a test, and two agreement tests drive both implementations over the
same inputs: 324 generated runs over 289 distinct fixtures (whole-hour
UTC, explicit offsets, zone-less stamps and sub-second precision), and
six hand-built shapes that between them reach all five grades and a
refusal-rate tie, which the generator never does.

- Events are grouped by contributor first and by `task_id` second, so one
  task touched by two contributors is counted once for each of them.
- Per contributor-task, the timestamp kept for a state is the earliest
  one carrying it. `analyze()` reaches the same value by sorting on
  `(timestamp, state)` and keeping the first of each state; the secondary
  key only orders different states sharing a timestamp, so the per-state
  minimum is the same.
- `accepted` and `submitted` each add one when that state appears at all,
  however many times it appears. `rewarded` wins over `refused`: a task
  carrying both counts as rewarded and not as refused, which is the
  `elif` in `analyze()`. `terminal` is `rewarded + refused`.
- Durations are collected only when both ends exist and the difference is
  not negative, then reduced to a median in hours rounded to 4dp, or
  `null` when no pair survived. The terminal end is `rewarded` if present,
  else `refused`. A timestamp with no zone is read as UTC, which is not
  the same as reading it in local time -- a subprocess in two other zones
  pins that.
- `refusal_rate` is `refused / terminal` rounded to 6dp, or `0.0` with no
  terminal outcome. Grades apply in order, and both of grade A's
  comparisons are inclusive. `over_ceiling` additionally requires
  `--min-tasks` terminal outcomes.
- Contributors sort by `(-refusal_rate, contributor)` and `status` is
  `ceiling_breach` when any contributor is over the ceiling.

A fixture the tool itself would refuse -- an unknown state, a blank name,
an unparseable timestamp -- is exit **2** here rather than a wall of
`STALE` lines blaming the report for a broken input.

### What it is, and what it is not

**It does not import `throughput.py`**, so a stale report is caught by
something other than the code that wrote it, and
`report-freshness/manifest.json` does not track this tool at all -- before
this there was nothing at either level. But it is a **transliteration** of
`analyze()` into a second file, not an independent derivation: same loop
shape, same `elif`, same rounding constants. It detects the report and the
fixture drifting apart, which is what a stale number is. It does not
detect a bug shared with the tool, and neither does the agreement test,
which compares the copy with its original.

**It writes no report of its own.** A committed report would be one more
number to keep fresh, which is the problem this check exists to catch.

### Where its runs are recorded

In `STALE_NUMBER_EVIDENCE.txt`, not in `captured_output.txt`.
`index-generator` pins the number of `=== $ ... ===` records in this
repository inside `pipe_classification_report.json`, and its own suite
compares that pinned number against a live rescan. To be exact about the
mechanism: `pipe_scan.py` and `pipe_classify.py` open one filename per
tool directory, `captured_output.txt`, so it is the file name and not the
step form that keeps a separate evidence file out of that count. A full
record written here would be counted only if it were written into
`captured_output.txt`; the plain `$ ` form is used for readability, not
for protection. That pinned count is also why the transcript carries no
`python3 --version` record -- adding one would move it -- so the
interpreter is recorded in the evidence file instead. Rebuild that file
with:

```
bash mk_stale_evidence.sh
```

Two of its lines are volatile and will differ on a rebuild: the
`unittest` duration, and the `python3 --version` line on a different
interpreter. `Parent commit:` is a default inside the script naming the
commit the committed copy was produced against, not a reading of the
checkout.

### What this delivery changed outside this directory

`captured_output.txt`'s first record was re-captured so it runs both
suites. It was re-captured on **Python 3.11.15**, whose `unittest -v`
test-id format differs from the one the previous capture used
(`test_x (module.Class)` became `test_x (module.Class.test_x)`), so every
listing line in that record changed shape; the other seven records are
byte-identical to the parent. `test_output.txt` holds the same listing
and was regenerated with it.

Five committed reports elsewhere in the repository go stale as a result
and were regenerated with their own documented commands, so that
`report-freshness` stays at exit 0:

| report | why it moved |
|--------|--------------|
| `transcript-schema/validation_report.json` | records line numbers in every transcript, and this one grew |
| `nondeterminism-scanner/self_scan_report.json` | two new Python files (`files_scanned` 182 to 184; no new finding) |
| `weak-assertion-scanner/self_scan_report.json` | one new test module (`files_scanned` 82 to 83, `tests_scanned` 6553 to 6610; no new finding) |
| `claim-crosscheck/sample_run.json` | this section names `report_ok.json` and the other artifacts in backticks, which moves that tool's report discovery for this directory from `absent` to `ambiguous` with five candidates. Its totals are unchanged at 15 claims / 1 discrepancy / 0 errors, and `claims_checked` for this directory is 0 either way |
| root `README.md` | the index row for this tool and the two aggregate figures |

The root `README.md` half of that last row comes in a pair, and this
delivery ships only one half of it, exactly as commits `b1645ca`,
`d645dbf` and `4cc4339` did. `readme-index/corpus.tsv` is a pinned
extraction that still carries `Ran 32 tests` for this tool, so
`readmeindex.py --corpus corpus.tsv --root-readme ../README.md` goes from
two differences to three: a `count_differs` for `throughput-reporter` on
top of the pre-existing one for `evidence-validator`, and the
`aggregate_differs` moves from 4,202-vs-4,221 to 4,202-vs-4,278.
`readme-index/README.md` also still states 4,202 in its prose. The corpus
half belongs to `readme-index`, which this brief puts off limits;
`b394034` is the shape it takes. The gate itself -- corpus against
`readme-index`'s own mirror, `root_readme_after.md` -- is exit 0 either
way, and every other repository-wide check is at its parent value.

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

## Flags

| flag | description |
|------|-------------|
| `events` (positional) | Path to a JSON array of task events. Required. |
| `-o`, `--out PATH` | Write the canonical JSON report to this file instead of stdout. When set, stdout instead gets a one-line summary: `status=<status> contributors=<n> over_ceiling=<n>`. |
| `--refusal-ceiling N` | Contributors whose refusal rate exceeds this (with at least `--min-tasks` terminal tasks) are flagged `over_ceiling`; also the cutoff between grade `C` and grade `D`. Parsed as `float`, default `0.5`. |
| `--min-tasks N` | Contributors with fewer than this many terminal (completed/refused) tasks are graded `INSUFFICIENT_DATA` instead of being scored, and are exempt from `over_ceiling`. Parsed as `int`, default `2`. |

## Exit codes

0 = no contributor over ceiling · 1 = at least one over · 2 = invalid input
