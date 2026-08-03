# contradict.py

A stdlib-only Python 3 CLI that runs a fixed set of **existing** postfiatwork
checker tools against the same case data and reports where two of them make
**incompatible claims about the same proposition**. It never replaces or
reimplements checker logic; it only orchestrates subprocess calls, parses
their canonical JSON reports, and compares specific, pre-identified pairs of
claims that are known (from reading the checkers' own source and READMEs)
to genuinely overlap.

```
python3 contradict.py CASE_ROOT [-o FILE] [--checkers-root PATH] [--timeout SECONDS]
```

- `CASE_ROOT` is either one case directory (containing recognized input
  files directly) or a directory containing several case subdirectories.
  Every case is processed independently and the results are aggregated.
- `-o/--output FILE` — write the canonical JSON report to `FILE` instead of
  stdout.
- `--checkers-root PATH` — directory holding the checker tool subdirectories
  (`preflight/preflight.py`, `link-integrity/link_integrity.py`, ...).
  Defaults to the `checkers/` directory bundled next to this script, which
  contains unmodified copies of the ten checker scripts this tool knows how
  to drive. Point this at a live `postfiatwork` checkout to use those
  instead — the two are interchangeable because contradict.py never edits
  checker code, only calls it.
- `--timeout SECONDS` — per-checker subprocess timeout (default 20s). A
  timeout is treated as `EXECUTION_FAILURE`, never a hang.

Exit codes: `0` all applicable checkers agree, `1` a contradiction was
found, `2` invalid input, a missing checker, or a checker execution failure.

## The definition that matters

**Two checkers producing different exit codes, or even different findings,
is not automatically a contradiction.** Every tool in this repo has a
narrower scope than "judge everything about this record" by design —
`link-integrity` deliberately does not validate schema; `preflight`
deliberately does not check lifecycle order. A rule that fired on any
observed difference would be constant noise and would train people to
ignore the tool.

**A `*_CONTRADICTION` is emitted only when two checkers each make an
explicit, checkable claim about the *same proposition* — the same
task_id/submission_id, the same field, the same value — and those claims
cannot both be true.** Silence from a checker never counts as a claim
except where the comparator can prove the checker actually inspected the
relevant task/record and had the opportunity to complain (see each
comparator's docstring in `contradict.py` for the exact rule). Two
checkers merely looking at different things, or looking at the same thing
with a documented granularity difference, is `SCOPE_DIVERGENCE` —
surfaced for visibility, **excluded from the exit code**, and never
labelled a contradiction.

This distinction is enforced in code: `contradict.py` does not have a
generic "diff two reports" mode. It has six hand-written comparator
functions (`compare_linkage`, `compare_amount`, `compare_timestamp`,
`compare_lifecycle_scope`, `compare_validity`, `compare_identity`), each
wired to exactly the two checkers whose overlap was verified by reading
their source, and each encoding the specific join key and specific
opposite-verdict condition that makes a finding a genuine contradiction
rather than a scope difference.

## Contradiction codes

| Code | Precise meaning | Checkers involved |
|---|---|---|
| `VALIDITY_CONTRADICTION` | One checker says a record's field is structurally valid; another says the same field on the same record is malformed. | queue-auditor + staleness-monitor (the `deadline` field) |
| `LINKAGE_CONTRADICTION` | One checker says evidence for submission S links to a known task; another says the same submission is orphaned. | preflight + link-integrity |
| `AMOUNT_CONTRADICTION` | Two money checkers each state an explicit paid amount for the same task_id, and the amounts differ. | reward-reconciler + reward-anomaly |
| `TIMESTAMP_CONTRADICTION` | One checker rejects a specific timestamp value as impossible; another processed the same task's timeline, including that value, without complaint. | link-integrity + lifecycle-linter |
| `IDENTITY_CONTRADICTION` | One checker says two submission ids are near-duplicate/the same underlying content; another says both are fully original and unrelated. | dup-detector + evidence-scorer |
| `EXECUTION_FAILURE` | A checker that should have run (its required input files are present) crashed, timed out, exited with an unexpected code, or produced non-JSON output. Forces exit 2. | any |
| `CHECKER_UNAVAILABLE` | A checker's required input files are present in the case, but its script cannot be found under `--checkers-root`. Forces exit 2. | any |
| `SCOPE_DIVERGENCE` | Not a contradiction. Two checkers differ only because their scopes differ by design. Reported for transparency; **never affects the exit code**. | several, see below |

## The overlap map (derived from reading the actual code and READMEs)

The task brief named several plausible-looking pairs. Here is what reading
the source actually showed:

- **preflight vs link-integrity** — real overlap, but on **different join
  files**: preflight decides "orphaned" by checking a submission's
  `task_id` against a *task export* (`tasks.json`); link-integrity decides
  the same word by checking it against a *lifecycle export*
  (`lifecycle.json`). Both explicitly disclaim lifecycle-order validation
  (that's lifecycle-linter/event-linter's job) and preflight's
  `EVIDENCE_TYPE_MISMATCH` has no counterpart in link-integrity, which by
  its own README does not validate `evidence_type` at all — that
  difference is `SCOPE_DIVERGENCE`, not a contradiction.
- **queue-auditor vs staleness-monitor** — real overlap on the `deadline`
  field specifically: queue-auditor requires `deadline` to be a non-empty
  string (`MALFORMED_RECORD` if null); staleness-monitor's own docstring
  calls `deadline` "the only [key] allowed to hold null". Same field, same
  record, opposite validity verdicts. staleness-monitor's time-window
  findings (`OVERDUE_PROPOSED`, `STALE_ACCEPTED`, `STALE_SUBMITTED`) have
  no queue-auditor counterpart at all — queue-auditor never reasons about
  elapsed time — so those are `SCOPE_DIVERGENCE`.
- **schema-checker vs evidence-validator** — investigated and **not
  wired up**. schema-checker is a generic engine with no built-in field
  semantics (it validates whatever `--schema` you give it); its own
  shipped example schema and evidence-validator's hardcoded `cid`
  regex only coincidentally share a key name (`cid`) and would disagree
  about a value like `"a1b2c3d4"` — but that disagreement is an artifact
  of which example schema happens to be loaded, not an inherent property
  of either tool. Wiring these two together would encode a fixture
  coincidence as a "finding", which is exactly the kind of noise this
  tool is trying to avoid. Left out on purpose.
- **reward-reconciler vs reward-anomaly vs wallet-reconciler** — the first
  two genuinely overlap: both compute "is the amount paid for task T
  correct?", reward-reconciler from an `expected_rewards.json` +
  `recorded_payouts.json` pair with 6-decimal quantization and exact
  equality, reward-anomaly from a `reward_tasks.json` (`price`) +
  `reward_payouts.json` (`amount`) pair with a configurable tolerance and
  no quantization. When both flag an explicit amount for the same
  task_id and those amounts differ, that's `AMOUNT_CONTRADICTION`.
  reward-reconciler's `WALLET_MISMATCH` has no counterpart — neither
  reward-anomaly nor wallet-reconciler has a wallet field in their record
  schema at all (verified: zero `wallet` occurrences in either script) —
  `SCOPE_DIVERGENCE`. **wallet-reconciler was investigated and excluded**:
  it has no `task_id` and no `wallet` field; it is a single wallet's
  internal running-balance reconciliation with no external "expected"
  source and no identifier that joins to anything reward-reconciler or
  reward-anomaly produce. There is nothing for it to agree or disagree
  about with the other two.
- **lifecycle-linter vs event-linter vs link-integrity** — lifecycle-linter
  and event-linter share byte-identical transition graphs but different
  I/O (JSONL vs JSON array) and, critically, different "duplicate"
  vocabularies: lifecycle-linter's `DUPLICATE_STATE` fires on any
  back-to-back repeat of a state regardless of timestamp; event-linter's
  `DUPLICATE_EVENT` only fires on an exact `(state, occurred_at)` repeat.
  Both READMEs document this as deliberate. contradict.py surfaces a
  `DUPLICATE_STATE`-without-`DUPLICATE_EVENT` pairing as `SCOPE_DIVERGENCE`
  specifically so it is never mistaken for a contradiction.
  link-integrity's real overlap is with **timestamp validity**, not
  duplicate detection: it strictly parses every lifecycle `at` timestamp
  with an ISO-8601 regex and flags `IMPOSSIBLE_TIMESTAMP`; lifecycle-linter
  never validates timestamp format at all, only ordering by string
  comparison. A syntactically-impossible timestamp that still sorts fine
  lexically passes lifecycle-linter with zero complaints. That is
  `TIMESTAMP_CONTRADICTION`.
- **evidence-scorer vs dup-detector** — both judge submission text, by
  different mechanisms: dup-detector uses token 5-gram Jaccard similarity
  (tolerates small edits); evidence-scorer's `originality` component uses
  exact-sentence-string matching within the batch (zero tolerance for a
  single changed word). A lightly-reworded near-duplicate pair can score
  above dup-detector's threshold while sharing zero identical sentences
  with evidence-scorer, driving `originality` to `1.0` for both records —
  one tool says "these are essentially the same content", the other says
  "these are unrelated and fully original". `IDENTITY_CONTRADICTION`.

## Case directory format

Each case is a directory containing some subset of these well-known,
case-relative filenames. A checker is "applicable" to a case iff all of
its required filenames are present; contradict.py never invents data:

| Filename | Consumed by | Shape |
|---|---|---|
| `tasks.json` | preflight | JSON array: `task_id, title, status, required_evidence[]` |
| `evidence.json` | preflight, link-integrity | JSON array: `submission_id, task_id, evidence_type, value, submitted_at` |
| `lifecycle.json` | link-integrity | JSON array: `task_id, state, at` |
| `events.jsonl` | lifecycle-linter | JSON-Lines: `task_id, state, occurred_at` |
| `events.json` | event-linter | JSON array of the same event shape |
| `queue_tasks.json` | queue-auditor | JSON object: `{"tasks": [...]}`, records need `task_id, title, status, list, reward, created_at, deadline` |
| `staleness_tasks.json` | staleness-monitor | bare JSON array, records need `task_id, title, status, created_at, deadline` (deadline may be `null`) |
| `expected_rewards.json` / `recorded_payouts.json` | reward-reconciler | JSON arrays: `task_id, wallet, amount` |
| `reward_tasks.json` / `reward_payouts.json` | reward-anomaly | JSON arrays: `task_id, status, price` / `payout_id, task_id, amount, at` |
| `submissions.json` | evidence-scorer, dup-detector | JSON array: `submission_id, text` |

`queue_tasks.json` and `staleness_tasks.json` are deliberately **separate**
filenames even though queue-auditor and staleness-monitor both conceptually
consume "a task queue snapshot" — see Real bug #1 below for why sharing one
filename between them was wrong.

`staleness-monitor` requires a `--now` value; contradict.py always passes
the fixed constant `REFERENCE_NOW = "2026-06-01T00:00:00Z"` baked into the
script, never the system clock, so repeat runs stay byte-identical.

## Report shape

```json
{
  "cases": [ {"case_id": "...", "checkers_applicable": [...],
              "checkers_not_applicable": [...], "issues": [...],
              "contradiction_count": N, "scope_divergence_count": N,
              "execution_issue_count": N, "issue_count": N} ],
  "code_counts": { "<every known code>": N },
  "known_checkers": [ "...sorted list of the 10 checker ids..." ],
  "report_version": "1.0",
  "status": "agree" | "contradictions_found" | "execution_error",
  "summary": { "case_count": N, "total_contradictions": N, ... },
  "tool_version": "1.0.0"
}
```

Every `issue` has the same shape regardless of code: `checkers` (sorted
list of involved checker ids), `subject` (the shared key(s) the claim is
about — `task_id`, `submission_id`, etc.), `claims` (each checker's stated
position), and a human `message` explaining *why* this is or is not a
contradiction. Cases are sorted by `case_id`; issues within a case are
sorted by `(code, checkers, subject, claims, message)` — fully
deterministic, independent of dict/filesystem iteration order.

Checker discovery and case discovery are both explicitly sorted
(`sorted(ADAPTERS)`, `sorted(os.listdir(...))`) rather than relying on
filesystem enumeration order.

Nothing in the report is an absolute path, a wall-clock read, a duration,
or a hostname: checker subprocesses are always launched with `cwd` set to
the case directory and given bare relative filenames, so nothing in a
checker's own stdout/stderr can smuggle a path into the comparison logic,
and contradict.py never captures raw stdout/stderr text into the report
in the first place — only its own pre-written, generic sentences for
`EXECUTION_FAILURE`/`CHECKER_UNAVAILABLE` detail strings.

## Verification

Ten commands were run exactly as specified and their real output captured
verbatim in `captured_output.txt`, plus a relocation test and a run
against the live sibling checkers appended at the end for completeness.

- `python3 -m unittest test_contradict -v` → **168 tests, all passing**
  (`Ran 168 tests ... OK`).
- `python3 contradict.py cases_agree` → exit `0`.
- `python3 contradict.py cases_conflict -o r1.json` → exit `1`.
- `python3 contradict.py cases_conflict -o r2.json` → exit `1`.
- `sha256sum r1.json r2.json` → identical digest
  (`e1bbd94e51d3d0787be10aed18a901f1b011d1d85b3e541188e747f45e22be42`)
  for both, `cmp` confirms `BYTE-IDENTICAL`. (`r1.json`/`r2.json` are
  scratch output and are not part of this delivered tree or the zip.)
- `python3 contradict.py /nonexistent_dir` → exit `2`.
- `grep -c "/sessions\|/tmp\|/home" r1.json` → `0`.
- Relocation test: `cases_conflict/` copied to an unrelated absolute path
  four directories deep and re-run there → same SHA-256 as the original
  location.
- Run against the **live sibling checkers** (`--checkers-root` pointed at
  the real `postfiatwork` checkout rather than the bundled copies):
  `cases_agree` → exit 0, `cases_conflict` → exit 1 with all five
  contradiction codes present, byte-for-byte the same report as running
  against the bundled copies (the vendored scripts are unmodified copies
  of the same source). Also ran directly against the sibling repo's own
  shipped `dup-detector/records_dupes.json` fixture, unmodified, as
  `submissions.json` — **result: no contradiction**. See "Real checker
  run" below for why, and why that is the right answer, not a false
  negative.

## Real checker run — what actually happened

Pointing `--checkers-root` at the live `postfiatwork` checkout and running
`cases_agree`/`cases_conflict` reproduces the same results as the bundled
copies, since the vendored `checkers/` scripts are unmodified copies of
the same source (this doubles as regression coverage: if the live repo's
checker semantics ever drift from the vendored copies, this run would
start disagreeing with the bundled-copy run and that's a signal worth
investigating).

The more informative test was running `evidence-scorer` and `dup-detector`
against the sibling repo's own **unmodified shipped fixture**
(`dup-detector/records_dupes.json`, used as-is as `submissions.json`,
no data authored by this tool). That fixture's near-duplicate pair
(`SUB-1003`/`SUB-1005`, one witness statement reworded with a couple of
substituted words) is flagged by dup-detector at score `0.777778` — but
evidence-scorer's originality for both records comes out to `0.333333`,
not `1.0`, because one of the pair's three sentences is untouched by the
rewrite and evidence-scorer's exact-sentence matcher does catch that one
sentence. contradict.py's `compare_identity` rule requires **both**
originality scores to equal exactly `1.0` before calling it an
`IDENTITY_CONTRADICTION` — a deliberately strict bar. Against this real
fixture the bar is not met, so **no contradiction is reported**, which is
the correct, honest answer: evidence-scorer did partially notice the
overlap, so there is no clean case of "one tool says duplicate, the other
says fully original" here — just two tools disagreeing on *how much*
overlap there is, which is exactly the kind of scope/sensitivity
difference this tool is designed not to over-report. I built a synthetic
case (`cases_conflict/identity_conflict`, four fully-reworded sentences)
specifically to demonstrate the clean version of this contradiction,
documented above and included in the required verification run.

## Real bugs found and fixed while building this

**Bug 1 — filename collision made a self-contained comparator case
spuriously fail an unrelated checker's schema.** Early on,
`queue-auditor` and `staleness-monitor` were both wired to read a shared
`tasks.json` filename (reasonable-looking, since both audit "the task
queue"). But `preflight` was *also* wired to `tasks.json` for its own,
structurally incompatible task-export schema (`task_id, title, status,
required_evidence` vs queue-auditor's `task_id, title, status, list,
reward, created_at, deadline`). Any case built to exercise the
preflight/link-integrity comparator that happened to be named `tasks.json`
made queue-auditor and staleness-monitor spuriously "applicable" too —
and they then failed with `EXECUTION_FAILURE` on the mismatched schema,
polluting every linkage-comparator case with unrelated noise. Root cause:
filename-based applicability detection assumed "same concept" implies
"same schema", which is false here. Fixed by giving queue-auditor and
staleness-monitor their own distinct filenames (`queue_tasks.json`,
`staleness_tasks.json`), confirmed by a regression test
(`AdapterRegistryTests.test_queue_auditor_and_staleness_use_distinct_filenames`).

**Bug 2 (the more serious one) — a relative `--checkers-root` broke every
checker invocation with a misleading error.** `run_checker()` launches
each checker subprocess with `cwd` set to the *case* directory (so that
relative filenames stay relative and nothing in a checker's own output
can leak an absolute path). `discover_checker_path()` originally built the
script path with a plain `os.path.join(checkers_root, dirname, script)`
and never called `os.path.abspath()`. If a caller passed a relative
`--checkers-root` (e.g. `--checkers-root checkers`, run from the repo
root), that relative script path was correct at the moment
`os.path.isfile()` checked it — but then got handed to `subprocess.run()`
with `cwd` already switched to the case directory, so Python tried to
resolve `checkers/reward-reconciler/reconcile.py` **relative to the case
directory**, failed to find it, and exited 2 with its own
"can't open file" error. contradict.py then reported this, correctly by
its own logic but for the wrong underlying reason, as `EXECUTION_FAILURE:
checker reported invalid input or a usage error (exit 2)` — which reads
exactly like the checker itself rejecting the data, not like a path
resolution bug in the orchestrator. This was caught by running the exact
same case through `--checkers-root checkers` (relative) vs an absolute
path and getting different results for identical input — should never
happen for a tool that claims byte-stable, environment-independent
output. Fixed by making `discover_checker_path()` always return
`os.path.abspath(...)`. Covered by
`RunCheckerTests.test_relative_checkers_root_still_works` and
`test_discover_checker_path_absolute_even_for_relative_root`. The
default `--checkers-root` was always already absolute (derived from
`__file__`), so this bug could not affect the required verification
commands, which never pass `--checkers-root` explicitly — but it would
have bitten the very first person who ran `--checkers-root checkers`
from the repo root, which is the natural thing to type.

## Limitations a reviewer should scrutinise

1. **Six comparators, not a general N-way reconciler.** contradict.py
   only knows about the specific checker pairs documented above. Adding a
   new checker or a new overlap requires writing a new adapter entry and
   a new hand-written comparator function — there is no automatic
   "diff any two JSON reports" fallback, by design (a generic diff would
   reintroduce the exact noise problem this tool exists to avoid), but it
   does mean real contradictions between *unwired* pairs (e.g.
   xrpl-auditor vs reward-reconciler on a payout's transaction reference)
   go undetected rather than merely unreported.
2. **`AMOUNT_CONTRADICTION` and `IDENTITY_CONTRADICTION` only fire when
   both checkers produce an explicit finding.** If one checker is silent
   (clean) on a task/submission and the other is not, contradict.py does
   not currently infer the silent checker's implied position from the raw
   input it was given (it deliberately avoids re-reading checker inputs
   itself, to keep every claim traceable to something a checker actually
   said). This is conservative — it undercounts rather than
   overclaims — but it does mean some real disagreements (one checker
   silently agrees with a value the other flags as wrong) are not
   surfaced. `LINKAGE_CONTRADICTION`, `VALIDITY_CONTRADICTION`, and
   `TIMESTAMP_CONTRADICTION` are less conservative in this respect
   because "no finding for this specific task" is a safe, checkable proxy
   for "this checker inspected the task and had nothing to say" for those
   three tools specifically (verified by reading their code) — that proxy
   does not hold for every checker in general.
3. **Concurrency was not stress-tested for the general case.** During
   development, two `contradict.py` processes launched concurrently
   against the same case data occasionally produced a spurious
   `EXECUTION_FAILURE` that vanished on a sequential retry, in this
   sandboxed build environment specifically. Root-caused to Bug 2 above,
   not to concurrency itself — after the fix, five back-to-back
   sequential runs and three repeated live-checker runs all produced
   byte-identical output — but running many contradict.py invocations
   fully in parallel against a resource-constrained host was not
   independently stress-tested, and `run_checker()` has no retry-on-
   transient-failure logic, so a genuinely flaky checker subprocess
   (not observed here, but not ruled out on other hosts) would surface as
   a hard `EXECUTION_FAILURE`/exit 2 rather than a retried success.

## Files

- `contradict.py` — the tool.
- `test_contradict.py` — 168 unittest tests.
- `checkers/` — unmodified vendor copies of the ten checker scripts this
  tool drives by default.
- `cases_agree/` — eight fixture cases (one per comparator plus three
  edge cases: an empty case directory, a case where only one checker of a
  pair can evaluate, and a `SCOPE_DIVERGENCE`-only case) — full run exits
  `0`.
- `cases_conflict/` — five fixture cases, one per contradiction code that
  has a comparator wired up — full run exits `1`.
- `captured_output.txt` — real terminal output of the required
  verification commands, the relocation test, and the real-checker run.
