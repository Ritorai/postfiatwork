# postfiatwork

Verification and integrity tooling for the [Post Fiat](https://postfiat.org) /
Task Node contributor network.

What these tools cannot detect or prove is collected in [LIMITATIONS.md](LIMITATIONS.md), grouped by
false positive, false negative, scope boundary, and precision gap. Contributor
workflow is in [CONTRIBUTING.md](CONTRIBUTING.md); the output contract every
tool follows is in [EVIDENCE_STANDARD.md](EVIDENCE_STANDARD.md).

Forty-four standalone command-line tools. **Standard-library Python 3 only** — no
third-party packages, no network calls, no build step. Clone and run.

```
git clone https://github.com/Ritorai/postfiatwork
cd postfiatwork/schema-checker
python3 -m unittest test_schema_check -v
```

**4,175 tests claimed across 37 of 44 tools.** That figure is derived from
each tool's own README by `readme-index/`, which also records the 1 ambiguous
and 6 unstated cases rather than guessing them. It is a sum of claims, not a
run: this repository has no single command that executes every suite.

## Why these exist

Task Node rewards are only as trustworthy as the evidence behind them. Each tool
here checks one link in that chain — that a payout matches what was promised,
that evidence hasn't been tampered with, that a task's lifecycle actually
happened in a legal order, that submitted evidence contains real artifacts
rather than boilerplate.

Every tool follows the same contract so they compose in a pipeline:

- **Canonical JSON output** — `sort_keys=True`, `separators=(",",":")`,
  `ensure_ascii=True`, trailing newline. Repeat runs on the same input are
  byte-identical; each tool ships two runs of the same fixture plus their
  matching SHA-256 as proof.
- **CI-friendly exit codes** — `0` clean, `1` findings, `2` unreadable input.
  The 1-vs-2 split is deliberate: "the data has problems" and "the pipeline
  couldn't run" are different incidents and shouldn't be conflated.
- **Every finding carries a code and a location** — index, line number, or
  RFC 6901 JSON Pointer.
- **Malformed records never abort a run.** One bad row is reported and the rest
  of the batch is still processed, so a single corrupt entry can't mask real
  problems further down the file.

## The tools

| Tool | Tests | Title |
|------|------:|-------|
| [`budget-forecaster`](budget-forecaster) | 36 | Deterministic Task-Reward Budget Forecaster |
| [`bundle-index`](bundle-index) | 170 | Bundle Index CLI |
| [`bundle-verifier`](bundle-verifier) | not stated | bundle-verifier |
| [`claim-checker`](claim-checker) | 224 | claimcheck |
| [`commit-claim-auditor`](commit-claim-auditor) | ambiguous | commit-claim-auditor (`claimhist.py`) |
| [`consolidate`](consolidate) | 152 | consolidate.py |
| [`contradiction-detector`](contradiction-detector) | 168 | contradict.py |
| [`crosspath-runner`](crosspath-runner) | 72 | crosspath-runner |
| [`doc-validator`](doc-validator) | not stated | docval.py -- README-vs-argparse documentation validator |
| [`dup-detector`](dup-detector) | 110 | dupdetect |
| [`env-leak-scanner`](env-leak-scanner) | 65 | env-leak-scanner |
| [`event-linter`](event-linter) | 26 | Task Lifecycle Event Linter (JSON array input) |
| [`evidence-harness`](evidence-harness) | 63 | Evidence Verification Harness |
| [`evidence-manifest`](evidence-manifest) | 29 | Deterministic Batch Evidence Manifest CLI |
| [`evidence-scorer`](evidence-scorer) | 39 | Objective Evidence Quality Scorer |
| [`evidence-validator`](evidence-validator) | 17 | Evidence Integrity Validator |
| [`exit-harness`](exit-harness) | not stated | exit-harness |
| [`index-generator`](index-generator) | 138 | index-generator |
| [`lifecycle-linter`](lifecycle-linter) | 26 | Task Lifecycle Event Linter |
| [`limitations-probe`](limitations-probe) | not stated | limitations-probe |
| [`link-integrity`](link-integrity) | not stated | link_integrity.py |
| [`loop-health`](loop-health) | 202 | loop-health |
| [`nondeterminism-scanner`](nondeterminism-scanner) | 221 | ndscan |
| [`path-collision-scanner`](path-collision-scanner) | 95 | path-collision-scanner |
| [`payload-validator`](payload-validator) | 179 | Post Fiat Payload / Memo Validator |
| [`preflight`](preflight) | not stated | preflight |
| [`queue-auditor`](queue-auditor) | 175 | queue_audit.py |
| [`readme-index`](readme-index) | 72 | readme-index |
| [`regression-checker`](regression-checker) | 174 | regression-checker |
| [`reward-anomaly`](reward-anomaly) | 143 | Reward Anomaly Detection CLI |
| [`reward-reconciler`](reward-reconciler) | 23 | Deterministic Reward Reconciliation CLI |
| [`schema-checker`](schema-checker) | 95 | schema-checker |
| [`scorecard`](scorecard) | 188 | scorecard |
| [`snapshot-diff`](snapshot-diff) | 222 | snapdiff.py |
| [`staleness-monitor`](staleness-monitor) | 156 | staleness-monitor |
| [`sybil-detector`](sybil-detector) | 29 | Configurable Sybil Wallet-Cluster Detector |
| [`tamper-runner`](tamper-runner) | 144 | tamper-runner |
| [`thread-check`](thread-check) | 229 | thread-check |
| [`throughput-reporter`](throughput-reporter) | 32 | Contributor Throughput and Reliability Reporter |
| [`transcript-drift`](transcript-drift) | 57 | transcript-drift |
| [`wallet-reconciler`](wallet-reconciler) | 141 | Wallet Ledger Reconciliation CLI |
| [`weak-assertion-scanner`](weak-assertion-scanner) | 202 | weakassert |
| [`xrpl-address`](xrpl-address) | 34 | XRPL Classic and X-Address Validator |
| [`xrpl-auditor`](xrpl-auditor) | 27 | XRPL Payout Reference Auditor |

**Totals:** 44 tools; 4175 tests from 37 tools with a derivable claim (1 ambiguous, 6 not stated).

## Judgement calls, collected

The interesting decisions were mostly about **refusing to assert things the data
doesn't support**:

- `budget-forecaster` returns `null` for burn rate on a single-record history
  rather than `0`. Zero would claim "this project spends nothing per week" — a
  confident falsehood someone could budget against. `null` says "unknown".
- `throughput-reporter` grades a contributor `INSUFFICIENT_DATA` before any
  rate-based grade. One refusal out of one task is 100% by arithmetic and
  meaningless in fact; without that guard the tool hands a D to someone on their
  first day.
- `sybil-detector` deliberately does **not** cluster wallets that share only
  timing and length. That's weak evidence, and branding two independent
  contributors as coordinated on it would be a false accusation. Shared CID does
  the real work.
- `lifecycle-linter` treats `verification_requested → submitted` as legal.
  Resubmission after review is the normal path; penalising it would be backwards.
  An early version flagged it as a duplicate — caught by a test before shipping.
- `evidence-scorer` damps specificity on tiny samples. An early version scored
  the single word `"Done."` at 0.50 and **passed** it, because a trailing full
  stop made the token look "specific". Both causes fixed, both pinned by tests.
- Floats are rejected outright wherever money is involved. Binary floats can't
  represent settlement values exactly, and silently coercing one puts rounding
  error into a number someone acts on.

## What these tools are not

They check **form and consistency, not truth**. `evidence-scorer` can't tell
whether a submission is correct — only whether it looks like real work.
`sybil-detector` surfaces candidates for human review, not verdicts.
`xrpl-auditor` validates reference structure but never queries the ledger, so it
can't confirm a transaction actually settled. Each README states its own limits.

None of them should be wired to an automatic penalty without a human in the loop.

## License

MIT
