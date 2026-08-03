# REVIEWERS_GUIDE.md

How to review this repository's ~33 tools without reading all 334 files.

This guide is for a verifier deciding what to trust. It orders the work by
information gained per minute spent, separates claims you can check in seconds
from claims that cost you a clone and a test run, maps the limitations that
recur across tools so you can check a class of defect once instead of 33 times,
and points at the weakest evidence in the repository by file.

---

## Provenance of this guide

**No tool was executed and no test suite was run to produce this document.** It
was written by static inspection only — reading committed READMEs, committed
source files, committed report files, and the commit tree.

Consequently every number quoted below is either **read from a committed report
file** (and labelled as such) or **a claim made by a README** (and labelled as
such). Nothing here is a fresh measurement. Where this guide tells you a claim
is true, it means the claim is internally consistent with committed artifacts —
not that this guide re-derived it.

The register of what each tool cannot do is [`LIMITATIONS.md`](LIMITATIONS.md);
the output contract is [`EVIDENCE_STANDARD.md`](EVIDENCE_STANDARD.md); this
guide is about **how to check**, and deliberately does not restate either.

---

## 1. Review sequence

Reviewing tool-by-tool is the expensive way, because it re-checks the same
contract 33 times. Review **by property across all tools**, then drill into
individual tools only where a property check fails.

### Phase 0 — Repository shape (5 minutes, no clone)

Establish what you are actually reviewing before trusting any inventory.

1. Count top-level tool directories in the tree. **33.**
2. Count rows in the root [`README.md`](README.md) tools table. **13.**
3. The difference — **20 tools** — are committed, tested, and documented in
   their own READMEs but absent from the repository's own index.

**Do this first.** The root README is the natural starting inventory and it is
stale. A review scoped from it silently omits 60% of the repository. See
§4.1.

### Phase 1 — Contract conformance across all tools (cheap, high yield)

Every tool claims the same output contract. Check the contract, not the tools.
Each of these is a grep or a single-file read, and each one that passes
retires the same question for every tool at once:

| Check | What you are looking for |
|---|---|
| Canonical JSON | `json.dumps(..., sort_keys=True, separators=(",",":"), ensure_ascii=True)` and a trailing newline |
| Output write | `newline="\n"` on the `open()` that writes the report |
| Exit codes | `0` clean / `1` findings / `2` could-not-run, with `2` never used for findings |
| List ordering | an explicit `sorted(...)` on every emitted list, with a documented key |
| Clock | `--now` present and `required=True`; no `time.time`, `utcnow`, or `now()` substrings |
| Money | `parse_float=Decimal` at the parse boundary; amounts emitted as strings |

Three known failures of this phase are already documented in
[`EVIDENCE_STANDARD.md`](EVIDENCE_STANDARD.md) and you should expect to find
them: no tool implements the canonical-dump sort tiebreak; `regress.py` omits
`newline="\n"`; `forecast.py` type-gates instead of using `parse_float=Decimal`.
Finding exactly those three and nothing else is a good sign. Finding a fourth is
a real result.

### Phase 2 — Evidence integrity (cheap, and where the weak spots are)

For each tool directory, without running anything:

1. Does `captured_output.txt` exist?
2. Do the commands in it match the commands in the README?
3. Does it contain a two-run `sha256sum` pair and a `cmp` result?
4. **Are the two hashes actually equal, and does the transcript's hash match
   the committed report file?** A committed transcript and a committed report
   are both in the tree; a hash claimed in the former must match the latter.
5. Are there absolute paths, durations, hostnames, or mtimes anywhere in the
   committed evidence? (One tool fails this — §4.2.)

Step 4 is the highest-value cheap check in the entire review, because it is the
only place where a committed claim can be falsified against a committed
artifact without running a single tool.

### Phase 3 — Self-scan tools (the only measured evidence)

Two tools have been run against the repository itself and committed the
unedited output:

- [`nondeterminism-scanner/self_scan_report.json`](nondeterminism-scanner/self_scan_report.json)
- [`weak-assertion-scanner/self_scan_report.json`](weak-assertion-scanner/self_scan_report.json)

These are the only measured numbers in the repository. Read both. They are also
where the repository is most honest about its own false-positive rates, and
where one overstatement lives (§4.3).

### Phase 4 — Overlapping tools (check the pair, not each half)

Several tools do near-identical jobs on different input shapes. Reviewing one
carefully and diffing against its sibling is much cheaper than reviewing both:

| Pair | Difference |
|---|---|
| `lifecycle-linter` / `event-linter` | Same lifecycle transition graph. JSONL vs JSON array; per-record vs grouped-per-task reporting. |
| `reward-reconciler` / `reward-anomaly` | `reward-anomaly` extends reconciliation with duplicate/orphan/invalid-amount detection. |
| `queue-auditor` / `snapshot-diff` | Same snapshot format; `snapshot-diff` does lighter structural validation *by design*. |
| `evidence-validator` / `evidence-harness` / `evidence-scorer` | Shape validation vs brief-satisfaction vs quality triage. Three different questions, routinely confused. |
| `staleness-monitor` / `loop-health` | Both consume task histories with a required `--now`. |
| `nondeterminism-scanner` / `weak-assertion-scanner` | Both AST-only static scanners with the same class of blind spot (§3.1). |

If the transition graph in `lifecycle-linter` is right, `event-linter`'s is
almost certainly right too — and if they *differ*, that is a finding worth more
than either review alone.

### Phase 5 — Individual tools

Only now, and only for tools that failed a property check or that a decision
actually depends on.

---

## 2. Claim cost table

What each class of claim costs to verify, and what the check requires.

### Cheap — seconds, no clone, no execution

| Claim | How to check | Requires |
|---|---|---|
| "33 tool directories" | Count directories in the tree | Tree view |
| "476 tests across 13 tools" | Sum the root README table | Arithmetic on one file |
| "Exit codes are 0/1/2" | Read the exit constants and `return` statements | One source file |
| "Canonical JSON" | Read the `json.dumps` call | One source file |
| "Clock is never read" | Grep `time.time`, `utcnow`, `now()`; check imports | One source file |
| "`--now` is required" | Read the `add_argument` call | One source file |
| "Amounts are Decimal-safe" | Read the `json.loads`/`json.load` call for `parse_float` | One source file |
| "No absolute paths in evidence" | Grep committed evidence for `/` prefixes and `C:\` | Whole tree grep |
| "ND004 produced 220 findings" | Read the committed `self_scan_report.json` | One report file |
| "README commands match the transcript" | Diff README fenced blocks against `captured_output.txt` | Two files per tool |

### Medium — a clone, no test run

| Claim | How to check | Requires |
|---|---|---|
| "Two runs are byte-identical" | `sha256sum` the two committed report files against each other | Clone + `sha256sum` |
| "The transcript's hash matches the committed report" | Hash the committed report; compare to the transcript | Clone + `sha256sum` |
| "The tool is stdlib-only" | Read every `import` in the entrypoint | Clone + grep |
| "Every finding carries a code and location" | Read the finding constructor | Clone |

### Expensive — clone and execute

| Claim | How to check | Requires |
|---|---|---|
| "N tests, all passing" | `python3 -m unittest test_<x> -v` per tool | Clone + 33 test runs |
| "Exit code 1 on findings" | Run against a findings fixture; check `$?` | Clone + execution |
| "The tests are not vacuous" | Read the assertions, or run `weak-assertion-scanner` — but see §3.1 | Clone + judgement |
| "Output is location-independent" | Run from two different absolute paths; compare hashes | **Two clones at different paths** |
| "The tool handles malformed mid-batch records" | Craft a fixture; run it | Clone + fixture authoring |

**The most expensive claim is also the least evidenced.** Location independence
requires two clones at different absolute paths. No committed evidence
demonstrates it — every committed determinism proof is a same-directory rerun,
which cannot detect a leaked path because the leak is identical in both runs.
See §4.2 and §4.4.

### Cost-saving order

If you have thirty minutes: Phase 0, then Phase 2 step 4, then read the two
self-scan reports. That combination will tell you more about whether this
repository's evidence is trustworthy than running all 33 test suites, because
test suites confirm the code does what its tests say — not whether the committed
claims match the committed artifacts.

---

## 3. Limitation map

Limitations that recur across tools. Check the class once; it applies to every
tool in the row.

### 3.1 Static analysis without data flow or types

**Tools:** `nondeterminism-scanner`, `weak-assertion-scanner`, `doc-validator`.

All three work on syntax. None has data-flow analysis or type inference.
Consequences, in their own words: `assertTrue(True)` satisfies
weak-assertion-scanner's WA001 exactly as well as a real assertion; ndscan's
alias resolution is file-wide rather than scope-aware and import-based only;
doc-validator's phantom-flag detection is token-scanning over prose and its
false-positive rate is "high and inherent … not fixable with a bigger regex."

**What this means for you:** a clean report from any of the three is weak
evidence. Their *findings* are worth reading; their *silences* are not.

### 3.2 Heuristics presented as detections

**Tools:** `claim-checker` (filename-token grammar; nearest-backtick-before
association for exit-code claims), `thread-check` (hand-picked thresholds, LIFO
binding of implicit replies), `dup-detector` (Jaccard over k-gram shingles),
`bundle-index` (markdown heading detection that is not structure-aware).

Each names its heuristic honestly in its README. The risk is a reader treating
a finding code as a verdict.

### 3.3 Masking — one defect hides others on the same record

**Tools:** `reward-anomaly`, `preflight`, `queue-auditor`.

All three short-circuit per-record validation on the first defect.

**What this means for you:** finding counts understate defects, and a re-run
after fixing the reported issues can surface new ones. Never treat a single pass
as a complete inventory; iterate to a clean run.

### 3.4 Identity matching is raw string equality

**Tools:** `scorecard`, `reward-anomaly`, `payload-validator`.

No normalisation anywhere — case, whitespace, or Unicode. `scorecard` treats
`"j.doe"` and `"J.Doe"` as different contributors. `payload-validator`'s
`SELF_PAYMENT` check does not decode X-addresses, so the same account expressed
two ways is not matched.

**What this means for you:** any identity-keyed metric silently splits, and any
identity-based check can be defeated by re-encoding.

### 3.5 Structure without network confirmation

**Tools:** `evidence-validator`, `xrpl-address`, `xrpl-auditor`,
`payload-validator`.

Deliberate and repository-wide: no tool makes a network call. A clean run across
all four proves every reference is *well-formed*. It says nothing about whether
any of them exist, settled, or are funded.

### 3.6 Timestamp handling

**Tools:** `staleness-monitor`, `link-integrity`.

Neither supports leap seconds — `datetime.fromisoformat` rejects a `:60` field,
so a valid leap-second timestamp is an input error rather than a parsed time.
Both make documented tie-breaking judgement calls.

### 3.7 Undisclosed limitations

**Tools:** `evidence-manifest`, `reward-reconciler`, `schema-checker`.

These three disclose **no** limitations anywhere. Every comparably sized tool
here discloses three to five.

**What this means for you:** treat the absence as missing documentation, not as
absence of limitations, and prioritise these three for scrutiny. This is a
review-priority signal, not a defect claim.

---

## 4. Weakest evidence

Ranked by how much the claim exceeds what backs it. Start here if you are
looking for the soft spots.

### 4.1 The root README is a stale inventory

[`README.md`](README.md) states "Thirteen standalone command-line tools" and
"**476 tests across 13 tools, all passing**". The per-tool counts in its table
sum to exactly 476, so the figure was correct when written.

The repository now contains **33** tool directories. **20 tools are missing from
the repository's own index.**

**Why it is the weakest evidence:** it is the first file a reviewer reads and
the one most likely to define review scope. It is not wrong in what it says —
it is wrong in what it omits, which is harder to notice.

**Check:** count directories; sum the table. Both cheap.

### 4.2 A committed absolute path

[`weak-assertion-scanner/README.md`](weak-assertion-scanner/README.md) documents
its self-scan as run with:

```
--root /sessions/sharp-stoic-knuth/mnt/outputs
```

**Two problems.** The command is not reproducible by any reviewer — that path
exists only on the machine that produced it. And it is exactly the defect class
the repository's own conventions prohibit in committed evidence.

**Why it matters beyond the one file:** it is direct proof that same-directory
determinism reruns do not catch location dependence. The scanner's own two runs
would have hashed identically with that path embedded.

**Check:** grep the tree for `/sessions/`. Cheap.

### 4.3 ND004's false-positive rate is overstated in one place

[`nondeterminism-scanner/README.md`](nondeterminism-scanner/README.md) describes
ND004's false-positive rate as **220/220** in its Limitations section. Its
self-scan section states that a random sample of **15** of the 220
(`random.seed(7)`) was hand-read, and that a custom-`__repr__` object elsewhere
in the 220 "is indistinguishable, from the outside of this exercise, from the
15/15 false positives actually read."

**The two statements are not compatible.** 220 findings is measured and
verifiable in
[`self_scan_report.json`](nondeterminism-scanner/self_scan_report.json). 15/15
is a demonstrated sample result. 220/220 is an extrapolation stated as a
measurement.

Compare [`weak-assertion-scanner/README.md`](weak-assertion-scanner/README.md),
which states all **67** WA003 findings were "reviewed all 67 individually" —
an exhaustive classification, and correspondingly stronger.

**Why it matters:** this is the repository's best evidence, and one sentence in
it overstates. Both READMEs are otherwise unusually candid, which is precisely
why the overstatement is worth flagging rather than excusing.

**Check:** read both sections of the ndscan README. Cheap.

### 4.4 No location-independence evidence anywhere

Every committed determinism proof is two runs from the **same** directory. That
demonstrates independence from iteration order, hash seed, and clock. It does
**not** demonstrate independence from absolute path, because a leaked path is
byte-identical in both runs.

Given §4.2 shows a path leak did occur in committed evidence, this gap is not
theoretical.

**Check:** expensive — requires two clones at different absolute paths. This is
the single most valuable expensive check available.

### 4.5 Test counts are claimed, not verified

Summing what each README claims gives **3,444 tests across the 30 tools that
state a figure**. [`doc-validator`](doc-validator/README.md),
[`link-integrity`](link-integrity/README.md), and
[`preflight`](preflight/README.md) state no count at all.

Every one of these is a README assertion. The only test counts verifiable
without execution are those echoed in a committed `captured_output.txt`
transcript — and a transcript is itself a committed claim, not an observation
you made.

**Check:** cheap to compare README against transcript; expensive to verify by
running.

### 4.6 `regression-checker` trusts partial reports

[`regression-checker/README.md`](regression-checker/README.md) documents that a
`report_mode: "file"` tool's report is fully trusted once it exists, "even if
written by a crashing/partial run", and that `expected_exit_code: false` is
silently accepted as `0` because JSON `bool` decodes as an `int` subclass.

**Why it matters disproportionately:** this is the tool that guards every other
tool's output stability. A weakness here weakens the whole regression story, not
one directory. Combined with its missing `newline="\n"`
(see [`EVIDENCE_STANDARD.md`](EVIDENCE_STANDARD.md) §1.1), the drift-detection
layer is the least sound part of the repository.

### 4.7 `captured_output.txt` has no schema

Nothing validates that the commands in a transcript match the commands in the
README, so the two can drift silently. The transcript is the primary evidence
artifact for every tool and it is unstructured by design.

**Check:** manual diff per tool. Cheap per tool, tedious across 33.

---

## 5. Quick per-tool triage

Read a tool's findings closely; treat its silence as weak, per §3.

**Highest scrutiny (undisclosed limitations, §3.7):** `evidence-manifest`,
`reward-reconciler`, `schema-checker`.

**Read findings sceptically (documented high false-positive modes):**
`nondeterminism-scanner` ND004, `weak-assertion-scanner` WA003,
`doc-validator` DOC002 and its module-invocation rule, `queue-auditor`
empty-title and `STATUS_LIST_MISMATCH`, `preflight` case-sensitive double
reporting, `snapshot-diff` `INVALID_REWARD`.

**Trust findings, distrust silence (documented false negatives):**
`weak-assertion-scanner` (syntax-only), `contradiction-detector` (only six wired
comparators), `dup-detector` (short documents structurally exempt),
`loop-health` (undercounts resubmit-after-refusal), `wallet-reconciler`
(adjacent-pair ordering), `preflight` (duplicate `task_id` silently dropped),
`regression-checker` (trusts partial reports).

**Well-evidenced relative to the rest:** `nondeterminism-scanner` and
`weak-assertion-scanner` — the only two with committed self-scan reports and
hand-classified false-positive analysis, notwithstanding §4.3.

---

## 6. What this guide does not tell you

- **Whether any test actually passes.** Nothing was run. Every "N tests" figure
  here is a README claim relayed as a claim.
- **Whether the tools are correct.** They check form and consistency, not truth.
  A tool can be perfectly implemented and still validate a fabricated
  submission — `evidence-scorer` says so about itself.
- **Whether undocumented limitations exist.** §3.7 flags three tools that
  disclose none. It cannot tell you what they are.
- **Whether output is location-independent.** No committed evidence establishes
  it either way (§4.4).
- **Whether the review sequence in §1 is optimal.** It is ordered by information
  gained per minute using static inspection, which is a judgement, not a
  measurement.

**None of these tools should be wired to an automatic penalty without a human in
the loop.** That is the repository's own stated position and it survives this
review unchanged.
