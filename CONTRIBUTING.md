# CONTRIBUTING.md

How to add or change a tool in this repository.

This is the contributor-facing document. The machine-facing output contract —
canonical JSON, exit codes, ordering, timing, Decimal, `captured_output.txt` —
is specified in [EVIDENCE_STANDARD.md](EVIDENCE_STANDARD.md), with the code
citation behind every rule. This file covers workflow and layout and points at
that document rather than restating it. How a submission is *reviewed* is a
separate concern and lives in the reviewer guide.

---

## 1. Ground rules

**Standard library only.** Python 3, no third-party packages, no build step, no
network calls at runtime. A reviewer must be able to clone the repository and
run any tool immediately. This is not a stylistic preference — a dependency the
reviewer has to install is a dependency that can differ between their machine
and yours, and the whole point of the output contract is that it does not.

**No network access inside a tool.** Several tools deliberately stop at
structural validation because of this. `evidence-validator` checks that a CID is
well-formed but does not resolve it; `xrpl-address` validates the base58 and
checksum of an address but cannot tell you whether it is funded; `xrpl-auditor`
validates payout reference structure but never queries the ledger. If your tool
needs a fact from the network, it takes that fact as an input file.

**Every finding carries a code and a location.** A finding a reviewer cannot
navigate to is not actionable. Location means an array index, a line number, or
an RFC 6901 JSON Pointer, depending on the input shape.

**One malformed record must not abort the run.** Report it and keep going. If a
single corrupt row halts the batch, every real problem after that row is
invisible, and the tool has converted one data-quality issue into a total
outage. `preflight`, `queue-auditor`, and `wallet-reconciler` all follow this.

**Refuse to assert what the data does not support.** This is the strongest
convention in the repository and the one most worth preserving:

- `budget-forecaster` returns `null` for burn rate on a single-record history
  rather than `0`. Zero would claim "this project spends nothing per week" — a
  confident falsehood someone could budget against.
- `throughput-reporter` grades a contributor `INSUFFICIENT_DATA` before any
  rate-based grade. One refusal out of one task is 100% by arithmetic and
  meaningless in fact.
- `sybil-detector` does not cluster wallets that share only timing and length.
  That is weak evidence, and branding two independent contributors as
  coordinated on it would be a false accusation.

When in doubt, emit `null` and a finding that says why, not a number that looks
authoritative.

---

## 2. Repository layout

One directory per tool, at the repository root, named in `kebab-case`. There are
currently **33** such directories.

Inside a tool directory:

| File | Required | Purpose |
|------|----------|---------|
| `<entrypoint>.py` | yes | The CLI. `snake_case`, usually a shortened form of the directory name — `nondeterminism-scanner/ndscan.py`, `wallet-reconciler/wallet_reconcile.py`. |
| `test_<entrypoint>.py` | yes | The unittest suite, runnable as `python3 -m unittest test_<entrypoint> -v`. |
| `README.md` | yes | Purpose, exact rerun command, expected-results table, exit-code meanings, and honest limitations. |
| `captured_output.txt` | yes | Committed transcript of a real run. Structure specified in EVIDENCE_STANDARD.md §6. |
| fixtures | as needed | Input JSON and any committed report files, e.g. `budget-forecaster/history.json`, `forecast_run1.json`, `forecast_run2.json`. |

The entrypoint and test module names must line up, because
`regression-checker/regress.py` discovers tools by directory one level deep and
runs their documented command. A tool that does not follow the naming convention
is invisible to it.

---

## 3. Adding a tool

1. **Create the directory** and the entrypoint. Start from an existing tool with
   a similar input shape rather than from scratch — the output contract is
   easier to inherit than to reimplement. `consolidate/consolidate.py` is the
   cleanest reference for canonical JSON and named exit constants;
   `wallet-reconciler/wallet_reconcile.py` is the reference for money.

2. **Implement the output contract.** Work through the checklist in
   EVIDENCE_STANDARD.md §7. The four that are most often missed:
   `newline="\n"` on the output file; an explicit sort on every emitted list;
   `required=True` on `--now`; and `parse_float=Decimal` at the parse boundary
   rather than `Decimal(str(x))` afterwards.

3. **Write the tests.** Cover, at minimum: the clean path (exit `0`), the
   findings path (exit `1`), every distinct finding code, malformed input
   (exit `2`), a malformed record mid-batch that does not abort the run, and a
   determinism test asserting two runs on the same fixture are byte-identical.

4. **Hunt for a real bug before you ship.** See §5.

5. **Write the README.** Exact rerun command, expected-results table, exit-code
   meanings, and a limitations section. See §4.

6. **Capture the output.** Run the documented commands for real and commit the
   transcript as `captured_output.txt`.

7. **Update the root `README.md` table.** See §6 — this step is currently being
   skipped, and it shows.

---

## 4. Writing the README

The limitations section is not boilerplate and is the most-read part of the
file. Write it as though the reader is deciding whether to trust your output in
a decision that matters, because they are.

Good limitations name the failure mode concretely. From the committed tools:

- `weak-assertion-scanner`: "WA003 has a real, demonstrated, high-volume
  false-positive mode (property/determinism/symmetry/idempotence tests)."
- `loop-health`: "`resubmission_rounds` undercounts resubmit-after-refusal by
  design (only adjacent `verification_requested->submitted` counts)."
- `wallet-reconciler`: "Out-of-order detection compares adjacent pairs, not a
  running watermark, e.g. `[05,01,02]` won't flag index 2."
- `payload-validator`: "`SELF_PAYMENT` is raw string equality only; does not
  decode X-addresses to compare underlying accounts."

Each of those tells you exactly which inputs the tool will get wrong. "May
produce false positives in some cases" tells you nothing and should not be
committed.

State what the tool does **not** do, especially where a reader would reasonably
assume otherwise — the network-access boundary in §1 is the usual case.

---

## 5. Bug hunting

Before shipping, actively try to break your own tool, and record what you found.

The convention here is that **when a test catches a bug, you fix the tool, not
the test.** Two examples already in the repository:

- `lifecycle-linter` treats `verification_requested → submitted` as legal.
  Resubmission after review is the normal path. An early version flagged it as a
  duplicate; a test caught it before it shipped.
- `evidence-scorer` scored the single word `"Done."` at 0.50 and **passed** it,
  because a trailing full stop made the token look "specific". Both causes were
  fixed and both are pinned by tests.

Note the shape of both: a plausible-looking rule that was wrong about a real
case. That is what to go looking for. Fixture-shaped inputs will not find it —
try empty input, a single record, duplicate identifiers, a record that is
malformed in the middle of a valid batch, and values at the boundary of every
threshold you hard-coded.

A weak test suite is worse than a small one, because it reports confidence it
has not earned. `weak-assertion-scanner` exists to find exactly that, and it
will be run against your directory:

```
cd weak-assertion-scanner
python3 -m unittest test_weakassert -v
python3 weakassert.py --root ../<your-tool>
```

Be aware of its own documented false-positive mode before acting on its output.

---

## 6. Keep the root README honest

**This step is currently being skipped, and the root `README.md` is stale.**

At the time of writing it states "Thirteen standalone command-line tools" and
"476 tests across 13 tools, all passing", and its table lists 13 tools. That
figure is internally consistent — the per-tool counts in the table do sum to
exactly 476 — so it was accurate when written. It is not accurate now: the
repository contains **33** tool directories, so **20** tools are committed,
tested, and documented in their own READMEs but absent from the root table.

Summing the test counts each of those 33 READMEs claims gives **3,444** across
the 30 tools that state a figure; `doc-validator`, `link-integrity`, and
`preflight` do not state one. Those are *claimed* counts read from the READMEs,
not counts observed from a run — do not copy them into the root README as
verified totals. Regenerate the table from actual `unittest` output, and make
the three tools that omit a count state one.

The general rule: **a count in a document is a claim, and a claim needs a run
behind it.** If you cannot produce the run, do not state the number.

Adding a tool is not finished until the root table includes it.

---

## 7. Before you open a change

- [ ] `python3 -m unittest test_<entrypoint> -v` passes, and you have the output.
- [ ] Two runs on the same fixture are byte-identical (`sha256sum`, `cmp`).
- [ ] A run from a **different absolute path** produces the same hash. Two runs
      from the same directory do not prove this — a leaked absolute path is
      identical in both. See EVIDENCE_STANDARD.md §6.1.
- [ ] Exit codes `0`, `1`, and `2` each demonstrated by a real invocation.
- [ ] `captured_output.txt` regenerated and committed.
- [ ] README limitations section names concrete failure modes.
- [ ] Root `README.md` table updated.
- [ ] No absolute paths, durations, hostnames, or mtimes anywhere in the report.

---

## 8. Commit messages

State what changed and why it was wrong before. The repository's existing history
is a reasonable model:

```
lifecycle-linter: treat verification_requested -> submitted as legal
snapshot-diff: strengthen jsonify test to assert exact 27-digit Decimal string
loop-health: strengthen healthy-fixture CLI test to assert zero findings
```

Each names the tool, the change, and — where relevant — the assertion that got
stronger. A message like "fix tests" gives a reviewer nothing to check against.

---

## License

MIT. By contributing you agree your work is licensed under the same terms.
