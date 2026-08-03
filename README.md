# postfiatwork

Verification and integrity tooling for the [Post Fiat](https://postfiat.org) /
Task Node contributor network.

Thirteen standalone command-line tools. **Standard-library Python 3 only** — no
third-party packages, no network calls, no build step. Clone and run.

```
git clone https://github.com/Ritorai/postfiatwork
cd postfiatwork/schema-checker
python3 -m unittest test_schema_check -v
```

**476 tests across 13 tools, all passing.**

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

| Tool | Tests | What it checks |
|------|------:|----------------|
| [`schema-checker`](schema-checker) | 95 | Validates evidence payloads against a declarative schema. Required keys, types, regex, enums, nested objects and arrays. Every violation gets a JSON Pointer path. |
| [`evidence-harness`](evidence-harness) | 63 | Pre-flight: run it against your **own** bundle before submitting. Checks the bundle actually contains what the brief demands and names the specific gap. |
| [`evidence-scorer`](evidence-scorer) | 39 | Scores evidence on computable signals — concrete artifacts, token specificity, length, and verbatim boilerplate shared across submissions. |
| [`budget-forecaster`](budget-forecaster) | 36 | Projects reward spend from history. Decimal-safe; refuses to invent a burn rate from one data point. |
| [`xrpl-address`](xrpl-address) | 34 | XRPL classic and X-address validation. Correct XRPL base58 alphabet, prefixes, and double-SHA256 checksum. |
| [`throughput-reporter`](throughput-reporter) | 32 | Per-contributor throughput, refusal rate, median turnaround, reliability grade. |
| [`evidence-manifest`](evidence-manifest) | 29 | Deterministic batch manifest — canonicalised records, per-record digests, Merkle root, tamper verification. |
| [`sybil-detector`](sybil-detector) | 29 | Groups wallets by shared CIDs, near-identical evidence length, and burst timing into scored clusters. |
| [`xrpl-auditor`](xrpl-auditor) | 27 | Payout transaction references: hash structure, reuse across or within tasks, unknown task IDs. |
| [`lifecycle-linter`](lifecycle-linter) | 26 | Task lifecycle histories from JSONL. Illegal transitions, skipped states, post-terminal events. |
| [`event-linter`](event-linter) | 26 | Same lifecycle graph from a JSON array, reported grouped per task. |
| [`reward-reconciler`](reward-reconciler) | 23 | Expected rewards vs recorded payouts. Missing, duplicate, unexpected, amount- and wallet-mismatched. |
| [`evidence-validator`](evidence-validator) | 17 | Required fields, CID and XRPL hash formats, duplicate submission IDs. |

Each directory has its own README with exact rerun commands, an expected-results
table, and the design judgement calls worth arguing with.

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
