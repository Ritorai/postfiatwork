# LIMITATIONS.md

A repository-wide register of what these tools **cannot** reliably detect or
prove.

Every tool here checks form and consistency, not truth. This document collects,
in one place, the specific ways each one is wrong — so that a reviewer acting on
a report knows in advance which findings to distrust and which silences to not
read as all-clear.

## How to read this register

Entries are grouped by failure mode:

- **[FP] False positive** — the tool reports something that is not a problem.
- **[FN] False negative** — a real problem the tool will not report.
- **[SB] Scope boundary** — the tool deliberately does not look, by design.
- **[PG] Precision gap** — the tool is right about the class of problem but
  imprecise about its location, extent, or severity.

Every entry names the tool or rule, the failure mode, the practical
consequence, the evidence behind it, and a workaround where one exists.

**Evidence is labelled, and the labels are load-bearing:**

| Label | Meaning |
|-------|---------|
| **measured** | A number produced by an actual committed run, preserved in a committed report file. |
| **hand-classified (exhaustive)** | A human read every finding and judged it. |
| **hand-classified (sample)** | A human read a subset and the conclusion was extrapolated to the rest. |
| **stated** | Asserted in the tool's own README with no counted run behind it. |
| **unknown** | Not disclosed anywhere. Absence of an entry is **not** evidence of absence of a limitation. |

A count and a judgement about that count are different claims. `220 findings`
is measured; `essentially all of them are false positives` is a judgement over a
15-item sample. This register keeps those apart everywhere, including where the
source README does not.

---

## 1. False positives [FP]

### 1.1 `nondeterminism-scanner` — ND004_UNSAFE_REPR

**Failure mode.** Flags `repr(X)`, `f"{X!r}"`, and `"...%r..." % X` unless `X`
is judged "obviously safe" (literals, obviously-safe containers, comparisons,
boolean ops, or calls to a whitelist of builtins with deterministic `repr`).
Anything else — including a plain `str` held in a variable — is flagged.

**Consequence.** ND004 dominates the report and buries the other five rules. A
reviewer who reads the finding count rather than the findings will conclude the
codebase is riddled with non-determinism when the high-severity rules
(ND001 wall-clock, ND002 unsorted listdir, ND005 unseeded random) may be clean.

**Evidence.**

- **measured** — a committed self-scan of the ~30 sibling tool directories
  produced `findings_count: 251` across `files_scanned: 87`, of which
  **ND004_UNSAFE_REPR: 220** — 88% of all findings. Source:
  [`nondeterminism-scanner/self_scan_report.json`](nondeterminism-scanner/self_scan_report.json),
  the unedited output of the run documented in
  [`nondeterminism-scanner/README.md`](nondeterminism-scanner/README.md).
- **hand-classified (sample)** — a random sample of **15 of the 220**
  (`random.seed(7)`) was read by hand. All 15 were `%r`/`!r` applied to a plain
  `str`, none of which has a non-deterministic `repr()`. The remaining 205 were
  **not** read.

> **Discrepancy in the source, recorded rather than resolved.** The README's own
> Limitations section describes this as a "**220/220**" false-positive rate,
> while its self-scan section states plainly that only 15 were read by hand and
> that a custom-`__repr__` object elsewhere in the 220 "is indistinguishable,
> from the outside of this exercise, from the 15/15 false positives actually
> read." **The demonstrated rate is 15/15 on a sample; 220/220 is an
> extrapolation.** The correct reading is: 220 measured findings, 15 verified
> false positives, 205 unexamined. This register does not repeat the 220/220
> figure as measured.

**Workaround.** Filter it out and triage the rest first:
`python3 ndscan.py --root <dir> --rule ND001 --rule ND002 --rule ND005`, or run
with `--min-severity high`. Re-enable ND004 only when auditing a module that
actually `repr()`s domain objects.

---

### 1.2 `weak-assertion-scanner` — WA003_SELF_DERIVED_EXPECTATION

**Failure mode.** Flags any `self.assertEqual(first, second)` where both
arguments contain, at any depth, a call rooted at the subject module. The
intent is to catch `assertEqual(f(x), f(x))` tautologies. The effect is to catch
every property-based test — determinism, symmetry, order-invariance,
idempotence — because those legitimately compare two related invocations.

**Consequence.** Property tests are exactly the tests this repository asks
contributors to write (EVIDENCE_STANDARD.md requires a determinism test in every
suite). The rule therefore fires hardest on the best-tested directories, and
acting on it would mean deleting the determinism guarantees.

**Evidence.**

- **measured** — a committed self-scan across the sibling tool directories:
  `files_scanned: 35`, `tests_scanned: 3198`, `findings_total: 92`, of which
  **WA003_SELF_DERIVED_EXPECTATION: 67**. Source:
  [`weak-assertion-scanner/self_scan_report.json`](weak-assertion-scanner/self_scan_report.json).
- **hand-classified (exhaustive)** — **all 67 were reviewed individually** and
  every one classified as a false positive of the tool rather than a weak test.
  This is a complete classification, not a sample, and is a stronger result than
  ND004's.

**The honest corollary, from the README itself:** a zero-true-positive result
means the rule found nothing real *in this corpus* — and a corpus that did
contain an accidental `assertEqual(f(x), f(x))` tautology "would look identical
in this report." WA003 currently has no demonstrated ability to distinguish the
two cases.

**Workaround.** Treat WA003 as advisory only. Triage WA001 (no assertion) and
WA002 (call-only) first — those have not been shown to misfire at this rate.

---

### 1.3 `doc-validator` — DOC002 phantom flag

**Failure mode.** Scans README prose for flag-like tokens and reports flags that
do not exist in the CLI. Prose that mentions a flag in passing, or a token that
merely looks like a flag, is indistinguishable from a real documented flag.

**Consequence.** The README calls the false-positive rate "high and inherent to
token-scanning prose, not fixable with a bigger regex." Reviewers learn to skip
DOC002, which also loses its true positives.

**Evidence.** **stated** — no committed count. **unknown** whether the rate has
been measured on any corpus. Source: [`doc-validator/README.md`](doc-validator/README.md).

**Workaround.** None documented. Read DOC002 findings against the CLI by hand.

---

### 1.4 `doc-validator` — module-invocation refusal

**Failure mode.** The `-m`/module-invocation refusal rule is "maximally literal
and noisy" and refuses almost every README's opening `unittest` line — that is,
the exact command this repository's conventions require every README to contain.

**Consequence.** Near-universal firing on conforming directories.

**Evidence.** **stated**. Source: [`doc-validator/README.md`](doc-validator/README.md).

---

### 1.5 `doc-validator` — DOC004 exit-code mismatch

**Failure mode.** Models exit codes `0` and `2` implicitly, so DOC004 can fire
for codes the author never had to reason about.

**Evidence.** **stated**. Source: [`doc-validator/README.md`](doc-validator/README.md).

---

### 1.6 `queue-auditor` — empty-title and status-list rules

**Failure mode.** Two documented over-firings: `title` is required to be a
non-empty string although the spec does not forbid empty titles; and
`STATUS_LIST_MISMATCH` requires exact string equality between `status` and
`list`, which misfires if a real status-to-list mapping is not the identity.

**Consequence.** Valid snapshots reported dirty; exit `1` where `0` is correct.

**Evidence.** **stated** — self-declared as "possible false positive". No count.
Source: [`queue-auditor/README.md`](queue-auditor/README.md).

---

### 1.7 `preflight` — case-sensitive double reporting

**Failure mode.** Case-sensitive matching reports a single typo twice, as both
`EVIDENCE_TYPE_MISMATCH` and `TASK_MISSING_EVIDENCE`.

**Consequence.** Finding counts overstate the number of underlying defects. Any
metric built on preflight's count is inflated by an unknown factor.

**Evidence.** **stated**. Source: [`preflight/README.md`](preflight/README.md).

---

### 1.8 `claim-checker` — shell-metacharacter refusal

**Failure mode.** The refusal is "intentionally broader than strictly necessary,"
producing false-positive refusals — legitimate commands classified
`UNVERIFIABLE_COMMAND`.

**Consequence.** A verifiable claim is reported as unverifiable, which downgrades
honest evidence. Conservative in the safe direction, but it is still a
misreport.

**Evidence.** **stated**. Source: [`claim-checker/README.md`](claim-checker/README.md).

---

### 1.9 `snapshot-diff` — INVALID_REWARD over-application

**Failure mode.** `INVALID_REWARD` is emitted for any non-finite numeric literal
anywhere in the document, not only inside a reward field.

**Consequence.** The finding code names the wrong problem and points at the
wrong field, sending a reviewer to inspect rewards when the defect is elsewhere.

**Evidence.** **stated**. Source: [`snapshot-diff/README.md`](snapshot-diff/README.md).

---

### 1.10 `bundle-index` — rerun-block heading detection

**Failure mode.** Heading detection is not markdown-structure-aware and can
match headings inside fenced code samples.

**Evidence.** **stated**. Source: [`bundle-index/README.md`](bundle-index/README.md).

---

## 2. False negatives [FN]

### 2.1 `weak-assertion-scanner` — syntax-only detection

**Failure mode.** All four detectors work on syntax, not control flow or types.
`assertTrue(True)` satisfies WA001 exactly as well as `assertEqual(result, expected)`.

**Consequence.** The canonical worthless assertion passes the scanner clean. A
green weak-assertion report does not mean the suite asserts anything.

**Evidence.** **stated**. Source: [`weak-assertion-scanner/README.md`](weak-assertion-scanner/README.md).

**Workaround.** None automated.

---

### 2.2 `weak-assertion-scanner` — helper and inheritance resolution

**Failure mode.** Helper resolution is one level deep and does not look at
inherited base-class methods — only the literal class body being scanned.
Two-level helper chains are not resolved. Subject-module resolution is
import-statement-only and file-local: it cannot see through re-exports,
`sys.path` manipulation, dynamic `importlib` loading, or aliased imports.

**Consequence.** A test suite that asserts through a base-class helper, or
imports its subject indirectly, is silently under-analysed.

**Evidence.** **stated**. Source: [`weak-assertion-scanner/README.md`](weak-assertion-scanner/README.md).

---

### 2.3 `nondeterminism-scanner` — no data-flow or type inference

**Failure mode.** No data-flow analysis and no type inference anywhere. Alias
resolution is file-wide rather than scope-aware and import-based only. ND002's
"immediately wrapped in `sorted()`" check is syntactic adjacency, not semantic
equivalence.

**Consequence.** Non-determinism reached through an alias, a wrapper function,
or a value whose type is not locally evident is invisible. ND003's dict
sub-check and ND004 are the most exposed.

**Evidence.** **stated**. Source: [`nondeterminism-scanner/README.md`](nondeterminism-scanner/README.md).

---

### 2.4 `contradiction-detector` — six hand-written comparators

**Failure mode.** Only six hand-written comparators exist, for specific checker
pairs. Overlaps between checkers that are not wired together are never compared.
Amount and identity contradictions require explicit findings from *both*
checkers — silent agreement goes unreported.

**Consequence.** A clean contradiction report means "none of the six wired
comparisons disagreed," not "the checkers agree."

**Evidence.** **stated**; the count of six is stated, the count of *unwired*
overlaps is **unknown**. Source: [`contradiction-detector/README.md`](contradiction-detector/README.md).

---

### 2.5 `loop-health` — resubmission undercount

**Failure mode.** `resubmission_rounds` counts only adjacent
`verification_requested → submitted` pairs, so it undercounts
resubmit-after-refusal by design.

**Consequence.** Contributors whose rework follows a refusal look more efficient
than they are. Directional and systematic, not random.

**Evidence.** **stated**, self-described as by design. Magnitude **unknown**.
Source: [`loop-health/README.md`](loop-health/README.md).

---

### 2.6 `loop-health` — no state-machine plausibility check

**Failure mode.** No cross-event plausibility checking. A nonsensical but
well-formed history produces no finding.

**Evidence.** **stated**. Source: [`loop-health/README.md`](loop-health/README.md).

**Workaround.** Run `lifecycle-linter` or `event-linter`, which do check the
transition graph.

---

### 2.7 `wallet-reconciler` — adjacent-pair ordering check

**Failure mode.** Out-of-order detection compares adjacent pairs rather than a
running watermark. The documented example: `[05, 01, 02]` does not flag index 2,
even though `02` precedes `05`.

**Consequence.** Ledgers with a single early displacement followed by an
ascending run pass clean.

**Evidence.** **stated**, with a concrete worked counterexample — stronger than
a bare assertion. Source: [`wallet-reconciler/README.md`](wallet-reconciler/README.md).

---

### 2.8 `dup-detector` — short documents unreachable

**Failure mode.** Documents shorter than `k` tokens produce empty shingle sets
and can never be flagged. Even at threshold `0.0`, pairs sharing zero shingles
are never reported. Two empty texts score `0.0`, not `1.0`.

**Consequence.** Short boilerplate submissions — the most likely thing to be
duplicated — are structurally exempt.

**Evidence.** **stated**. Source: [`dup-detector/README.md`](dup-detector/README.md).

---

### 2.9 `dup-detector` — whole-document only

**Failure mode.** Detects whole-document near-duplication only, not partial
plagiarism. Jaccard is symmetric, so a short passage copied verbatim into a long
document scores low.

**Evidence.** **stated**. Source: [`dup-detector/README.md`](dup-detector/README.md).

---

### 2.10 `preflight` — duplicate task_ids silently dropped

**Failure mode.** Duplicate `task_id` records are not flagged; the first is
silently kept for all cross-file checks.

**Consequence.** A duplicated task is invisible *and* the discarded record's
evidence is never checked. Silent data loss, not just a missing finding.

**Evidence.** **stated**. Source: [`preflight/README.md`](preflight/README.md).

---

### 2.11 `preflight` — EMPTY_EVIDENCE_VALUE type coverage

**Failure mode.** Catches only `null`, `""`, and whitespace-only strings.
Non-string empty values — `0`, `false`, `[]`, `{}` — are never flagged.

**Evidence.** **stated**. Source: [`preflight/README.md`](preflight/README.md).

---

### 2.12 `scorecard` — no cross-record consistency checking

**Failure mode.** Duplicate task IDs across contributors and implausible state
sequences produce no findings.

**Evidence.** **stated**. Source: [`scorecard/README.md`](scorecard/README.md).

---

### 2.13 `payload-validator` — duplicate detection is per-run

**Failure mode.** Duplicate `payload_id` detection operates only within a single
input array, not across previous runs. There is also no range ceiling on
`amount_pft` (only `drops` has the 1e17 ceiling), and numeric-string parsing is
unbounded before the size check.

**Consequence.** A payload replayed in a later batch is not detected.

**Evidence.** **stated**. Source: [`payload-validator/README.md`](payload-validator/README.md).

---

### 2.14 `regression-checker` — trusted partial reports

**Failure mode.** A `report_mode: "file"` tool's report is fully trusted once it
exists, even if written by a crashing or partial run. Separately,
`expected_exit_code: false` is silently accepted as `0`, because JSON `bool`
decodes as an `int` subclass.

**Consequence.** A tool that crashes mid-write can still pass its regression
check. The `false`/`0` coercion means a malformed baseline validates silently.

**Evidence.** **stated**. Source: [`regression-checker/README.md`](regression-checker/README.md).

---

### 2.15 `consolidate` — multi-task findings lost to grouping

**Failure mode.** Findings that reference several tasks (for example
`link-integrity`'s plural `task_ids`) are not split, and become invisible to
task-level grouping.

**Consequence.** A finding that exists in the source report disappears from the
per-task view a reviewer actually reads.

**Evidence.** **stated**. Source: [`consolidate/README.md`](consolidate/README.md).

---

### 2.16 `bundle-index` — symlinks and empty directories

**Failure mode.** Does not descend into symlinked directories. Empty directories
produce no findings. `claim-checker` shares the symlink limitation.

**Evidence.** **stated**. Sources: [`bundle-index/README.md`](bundle-index/README.md),
[`claim-checker/README.md`](claim-checker/README.md).

---

### 2.17 `doc-validator` — non-Python crashes

**Failure mode.** Crash detection is traceback-based, so a segfault or
signal-terminated process with no traceback text is not detected. DOC003/DOC004
are suppressed entirely when a module's exit value is not statically resolvable.

**Evidence.** **stated**. Source: [`doc-validator/README.md`](doc-validator/README.md).

---

## 3. Scope boundaries [SB]

These are deliberate. They are listed because a reader can easily assume
otherwise, and because a clean report from any of these tools is routinely
over-read as confirmation of something never checked.

### 3.1 No tool queries any network

No tool in this repository makes a network call. Concretely:

- `evidence-validator` — checks that a CID is well-formed; **does not resolve
  it**. Does not query the XRPL to confirm a `tx_hash` corresponds to a real,
  confirmed transaction. Network access is explicitly out of scope.
  Source: [`evidence-validator/README.md`](evidence-validator/README.md).
- `xrpl-address` — validates base58, prefix, and double-SHA256 checksum offline.
  A structurally valid, correctly checksummed address may be **unfunded or never
  activated**. Confirms neither activation nor funding.
  Source: [`xrpl-address/README.md`](xrpl-address/README.md).
- `xrpl-auditor` — validates payout reference structure and hash reuse; **never
  queries the ledger**, so it cannot confirm a transaction settled.
  Source: [`xrpl-auditor/README.md`](xrpl-auditor/README.md).
- `payload-validator` — does not query the ledger; funded/activated status is
  unknown to it. Source: [`payload-validator/README.md`](payload-validator/README.md).

**Consequence.** A clean run across all four proves every reference is
*well-formed*. It says nothing about whether any of them exist.

---

### 3.2 Form, not truth

- `evidence-scorer` — "measures form, not truth. A submission can be dense with
  hashes, paths and exit codes and still be wrong or fabricated." It is a triage
  filter for low-effort and copy-pasted submissions, not a correctness check,
  and **a genuinely good short answer can score low**. Low scores should prompt
  a human look, not automatic rejection.
  Source: [`evidence-scorer/README.md`](evidence-scorer/README.md).
- `evidence-harness` — checks a bundle against a declared `requirements.json`
  and names the gap. It does not judge quality.
  Source: [`evidence-harness/README.md`](evidence-harness/README.md).
- `sybil-detector` — surfaces candidate clusters for human review, not verdicts.
  It deliberately does **not** cluster wallets that share only timing and
  length, because that is weak evidence and would amount to a false accusation.
  Source: root [`README.md`](README.md).
- `scorecard` — measures throughput and review friction only, not task
  difficulty, quality, or effort. Source: [`scorecard/README.md`](scorecard/README.md).

**None of these should be wired to an automatic penalty without a human in the
loop.** That is the repository's stated position and it is repeated here because
it is the single most consequential limitation in the register.

---

### 3.3 Declared refusals to infer

Behaviour that looks like a bug and is not:

- `budget-forecaster` returns `null` for burn rate on a single-record history
  rather than `0`. Zero would assert "this project spends nothing per week" — a
  confident falsehood someone could budget against.
- `throughput-reporter` grades `INSUFFICIENT_DATA` before any rate-based grade.
  One refusal out of one task is 100% by arithmetic and meaningless in fact.
- `lifecycle-linter` treats `verification_requested → submitted` as **legal**;
  resubmission after review is the normal path. An early version flagged it as
  a duplicate and a test caught it before shipping.

Source: root [`README.md`](README.md).

---

### 3.4 Forecasting is extrapolation, not prediction

`budget-forecaster` linearly extrapolates the historical mean burn rate and
assumes the future resembles the observed window. It cannot anticipate a change
in task volume, pricing, or contributor count. The variance band communicates
**historical spread, not a statistical prediction interval** and must not be
read as a confidence interval.

Source: [`budget-forecaster/README.md`](budget-forecaster/README.md).

---

### 3.5 Structural validation only, by design

- `link-integrity` — minimal structural validation "can mask sloppy exports";
  `evidence_type` and `value` bypass validation by design.
- `snapshot-diff` — lighter structural validation than `queue-auditor`, by
  design; no stdin support.

Sources: [`link-integrity/README.md`](link-integrity/README.md),
[`snapshot-diff/README.md`](snapshot-diff/README.md).

---

## 4. Precision gaps [PG]

### 4.1 Masking: one defect hides others on the same record

Three tools short-circuit per-record validation, so the first defect suppresses
the rest:

- `reward-anomaly` — `MALFORMED_RECORD` short-circuits further validation, so a
  single malformed field can hide other real problems on the same record.
- `preflight` — a malformed task record suppresses more specific diagnostics;
  its evidence is reported as `ORPHAN_EVIDENCE` instead of the accurate finding.
- `queue-auditor` — a missing `reward` key is `MALFORMED_RECORD`, while a
  wrong-typed `reward` is `INVALID_REWARD`; the same underlying defect surfaces
  under two different codes depending on shape.

**Consequence.** Finding counts understate defects per record, and fixing the
reported problem can reveal new findings on re-run. **Iterate to a clean run;
never treat one pass as a full inventory.**

Sources: [`reward-anomaly/README.md`](reward-anomaly/README.md),
[`preflight/README.md`](preflight/README.md), [`queue-auditor/README.md`](queue-auditor/README.md).

---

### 4.2 Identity matching is raw string equality

No tool normalises identifiers. `scorecard` treats `"j.doe"` and `"J.Doe"` as
separate contributors. `reward-anomaly` applies no case, whitespace, or Unicode
normalisation to `task_id`/`payout_id`. `payload-validator`'s `SELF_PAYMENT`
check is raw string equality and does not decode X-addresses to compare the
underlying accounts — so a self-payment expressed as an X-address on one side
and a classic address on the other is not detected.

**Consequence.** Per-contributor metrics silently split across spellings, and an
identity-based check can be defeated by re-encoding the same account.

Sources: [`scorecard/README.md`](scorecard/README.md),
[`reward-anomaly/README.md`](reward-anomaly/README.md),
[`payload-validator/README.md`](payload-validator/README.md).

---

### 4.3 Timestamps: no leap seconds, opinionated tie-breaking

`staleness-monitor` and `link-integrity` both note there is no leap-second
support — `datetime.fromisoformat` rejects a `:60` seconds field outright, so a
valid leap-second timestamp is an input error rather than a parsed time.
`staleness-monitor` treats `-00:00` as equivalent to `Z`/`+00:00` and has fixed,
non-configurable bucket thresholds for `OVERDUE_PROPOSED`.
`link-integrity`'s `EVIDENCE_AFTER_TERMINAL_STATE` uses the **earliest**
terminal event when several exist — a judgement call, not a neutral default.

Sources: [`staleness-monitor/README.md`](staleness-monitor/README.md),
[`link-integrity/README.md`](link-integrity/README.md).

---

### 4.4 First-seen-wins is an opinion

`reward-anomaly` resolves a duplicated `task_id` by keeping the first
occurrence. The README names this as "an opinionated design choice, not a
neutral default." The later record may be the corrected one.

Source: [`reward-anomaly/README.md`](reward-anomaly/README.md).

---

### 4.5 Severity is editorial

`consolidate` assigns severity from a per-adapter default. The source tool never
asserts it. Its own README notes reviewers may disagree with the mapping — so a
severity-filtered consolidate run reflects `consolidate`'s opinion, not the
originating tool's.

`consolidate` also uses structural fingerprinting rather than a declared schema
to identify report types: a future report shape reusing the same key combination
could be misattributed to the wrong tool.

Source: [`consolidate/README.md`](consolidate/README.md).

---

### 4.6 `claim-checker` — positional and heuristic association

The filename-token grammar is a heuristic, not a parser, and misfires in both
directions — numeric-extension files are never recognised.
`EXIT_CODE_CLAIM` association is nearest-backtick-before, purely positional and
not semantic, so a claim can be bound to the wrong command.
`TEST_COUNT_CLAIM` runs the bundle's suite exactly once per report and shares
that result across all such claims.

Source: [`claim-checker/README.md`](claim-checker/README.md).

---

### 4.7 `snapshot-diff` — set semantics and null conflation

Evidence is compared as a **set of distinct items, not a multiset**, so a
duplicate appearing or disappearing is invisible. A missing key and an explicit
`null` are treated as equivalent for every diffed field, so a field being
*deleted* and a field being *set to null* cannot be distinguished.

Source: [`snapshot-diff/README.md`](snapshot-diff/README.md).

---

### 4.8 `bundle-index` and `wallet-reconciler` — reporting granularity and ceilings

`bundle-index` hashes and flags `.git` and `__pycache__` contents per file
rather than as one directory-level finding, inflating counts. Its language
allowlist is narrow (`bash`/`sh`/`console`), so `shell` or `zsh` headings are
not matched.

`wallet-reconciler`'s Decimal context precision is a fixed 60-significant-digit
ceiling, not unbounded. An event with an invalid timestamp updates no ordering
state, but an unknown-type or invalid-amount event still consumes duplicate-ID
and out-of-order checks normally.

Sources: [`bundle-index/README.md`](bundle-index/README.md),
[`wallet-reconciler/README.md`](wallet-reconciler/README.md).

---

### 4.9 `thread-check` — hand-picked thresholds and LIFO binding

`RESTATEMENT_ONLY`'s thresholds are hand-picked, not learned, and the
"question" concept underneath is a small fixed heuristic, not NLP. Implicit
contributor replies (no `in_reply_to`) bind **LIFO** to the most recently asked
open question — a judgement call, not an established fact. Artifact-reference
and question-detection patterns are precision trade-offs and "both directions of
error are real."

Source: [`thread-check/README.md`](thread-check/README.md).

---

### 4.10 `contradiction-detector` — concurrency untested

Concurrent execution "was not comprehensively stress-tested," and transient
checker failures surface as hard errors with no retry.

**Evidence.** **stated**; behaviour under concurrency is **unknown**.
Source: [`contradiction-detector/README.md`](contradiction-detector/README.md).

---

### 4.11 `loop-health` — synthetic identifier collision

The `<index:N>` synthetic task identifier, used when a real `task_id` is absent,
can collide with a genuine `task_id` of the same literal form.

Source: [`loop-health/README.md`](loop-health/README.md).

---

## 5. Repository-level limitations

Not attributable to any single tool.

### 5.1 The root README is stale

The root [`README.md`](README.md) states "Thirteen standalone command-line
tools" and "476 tests across 13 tools, all passing." The per-tool counts in its
table do sum to exactly 476, so it was accurate when written. The repository now
contains **33 tool directories**, so **20 tools** are committed, tested, and
documented in their own READMEs but absent from the root table.

**Consequence.** A reviewer using the root README as an inventory will miss 20
tools, including several in this register.

**Evidence.** **measured** — directory count from the committed tree; the 476
sum is arithmetic over the committed table.

---

### 5.2 Test counts across the repository are claimed, not verified here

Summing the counts each README states gives **3,444** across the **30** tools
that state one. `doc-validator`, `link-integrity`, and `preflight` state no
count — recorded here as **unknown**, not zero and not estimated.

These are **stated** figures read from the READMEs. This document is
documentation-only work and **no test suite was executed to produce it**, so
3,444 must not be cited as a verified total.

---

### 5.3 A committed absolute path leaks the author's environment

`weak-assertion-scanner/README.md` documents its self-scan as having been run
with `--root /sessions/sharp-stoic-knuth/mnt/outputs`.

**Consequence.** Two things. The command as written is **not reproducible** by
any reviewer, since that path exists only on the machine that produced it. And
it is precisely the class of defect the repository's own conventions prohibit —
no absolute paths in committed evidence. A same-directory rerun would not have
caught it, because a leaked absolute path is identical in both runs.

**Evidence.** **measured** — the string is present in the committed README.

---

### 5.4 Nothing enforces the output contract

`EVIDENCE_STANDARD.md` is prose. `doc-validator` checks README/CLI agreement and
`nondeterminism-scanner` catches some clock and ordering risks, but no tool
checks a directory against the contract as a whole. `captured_output.txt` has no
schema, so drift between a README's documented commands and its committed
transcript is undetectable.

Three specific divergences were recorded in `EVIDENCE_STANDARD.md`. **All three
are now closed**, and the entries are kept here as history rather than as open
findings:

| Divergence | Closed by | Tests added |
|---|---|---|
| No tool implemented the canonical-dump sort tiebreak | `96271b93` (consolidate), `8523e2b0` (schema-checker), `52640e6a` (nondeterminism-scanner) | 9 + 8 + 9 |
| `regression-checker/regress.py` wrote without `newline="\n"` | `f229dae9`, tests in `95a97571` | 8 |
| `budget-forecaster/forecast.py` type-gated instead of parsing with `Decimal` | `2b11448c` | 15, plus its 36 existing tests still passing |

Two notes worth keeping. The `ndscan` tiebreak had to be shown **dedup-neutral**,
because `Finding.sort_key()` is also the de-duplication key: `severity` is
`RULE_SEVERITY[rule_id]`, a pure function of `rule_id`, so two findings agreeing
on the five leading fields always serialise identically and still collapse to
one. And `forecast.py`'s behaviour is deliberately **unchanged** — float-shaped
tokens are still refused; what changed is that the refusal now happens on an
exact value, and bare `NaN`/`Infinity` tokens are intercepted before they can
become floats.

What this section still says remains true: the standard is prose, and no tool
checks a directory against it as a whole.

---

## 6. Coverage

All **33** tool directories appear in this register. Tools that disclose no
limitation of a given kind are marked **unknown** — meaning nothing is
documented, **not** that the tool is free of that failure mode.

| Tool | FP | FN | SB | PG | Disclosure |
|------|:--:|:--:|:--:|:--:|------------|
| budget-forecaster | — | — | 3.3, 3.4 | — | limitations section |
| bundle-index | 1.10 | 2.16 | — | 4.8 | limitations section |
| claim-checker | 1.8 | 2.16 | — | 4.6 | limitations section |
| consolidate | — | 2.15 | — | 4.5 | limitations section |
| contradiction-detector | — | 2.4 | — | 4.10 | limitations section |
| doc-validator | 1.3, 1.4, 1.5 | 2.17 | — | — | limitations section |
| dup-detector | — | 2.8, 2.9 | — | — | limitations section |
| event-linter | unknown | unknown | — | unknown | design notes only, no limitations section |
| evidence-harness | unknown | unknown | 3.2 | unknown | no limitations section |
| evidence-manifest | unknown | unknown | unknown | unknown | **no limitations disclosed** |
| evidence-scorer | — | — | 3.2 | — | limitations section |
| evidence-validator | — | — | 3.1 | — | limitations section |
| lifecycle-linter | unknown | unknown | 3.3 | unknown | no limitations section |
| link-integrity | — | — | 3.5 | 4.3 | limitations section |
| loop-health | — | 2.5, 2.6 | — | 4.11 | limitations section |
| nondeterminism-scanner | 1.1 | 2.3 | — | — | limitations + measured self-scan |
| payload-validator | — | 2.13 | 3.1 | 4.2 | limitations section |
| preflight | 1.7 | 2.10, 2.11 | — | 4.1 | limitations section |
| queue-auditor | 1.6 | — | — | 4.1 | limitations section |
| regression-checker | — | 2.14 | — | — | limitations section |
| reward-anomaly | — | — | — | 4.1, 4.2, 4.4 | limitations section |
| reward-reconciler | unknown | unknown | unknown | unknown | **no limitations disclosed** |
| schema-checker | unknown | unknown | unknown | unknown | **no limitations disclosed** |
| scorecard | — | 2.12 | 3.2 | 4.2 | limitations section |
| snapshot-diff | 1.9 | — | 3.5 | 4.7 | limitations section |
| staleness-monitor | — | — | — | 4.3 | limitations section |
| sybil-detector | — | — | 3.2 | — | root README only |
| thread-check | — | — | — | 4.9 | limitations section |
| throughput-reporter | — | — | 3.3 | — | judgement-calls section, root README |
| wallet-reconciler | — | 2.7 | — | 4.8 | limitations section |
| weak-assertion-scanner | 1.2 | 2.1, 2.2 | — | — | limitations + measured self-scan |
| xrpl-address | — | — | 3.1 | — | limitations section |
| xrpl-auditor | — | — | 3.1 | — | root README only |

### Tools with no disclosed limitations

`evidence-manifest`, `reward-reconciler`, and `schema-checker` disclose no
limitations anywhere. Given that every comparably-sized tool here has between
three and five, **the most likely explanation is undisclosed limitations rather
than their absence.** Treat a clean report from these three with the same
caution as any other, and prioritise them for a limitations pass.

`event-linter`, `evidence-harness`, and `lifecycle-linter` have design-note or
semantics sections but no limitations section; their entries above draw on the
root README and are correspondingly thin.

---

## Provenance

This register was compiled from committed documentation and committed report
files only. **No code was executed and no test suite was run to produce it.**

Two numbers here come from committed report files
(`nondeterminism-scanner/self_scan_report.json`,
`weak-assertion-scanner/self_scan_report.json`) and are labelled **measured**.
The judgements layered on those counts are labelled
**hand-classified**, with sample size stated where the classification was
partial. Everything else is **stated** or **unknown**.
