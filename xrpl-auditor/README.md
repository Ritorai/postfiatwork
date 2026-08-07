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
| tests | `Ran 36 tests` / `OK` |
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

`\A[0-9A-F]{64}\Z` — exactly 64 **uppercase** hex characters. Lowercase is
rejected rather than normalised: XRPL renders transaction hashes uppercase, so a
lowercase value indicates the reference was transcribed or transformed somewhere
in the pipeline, which is worth surfacing rather than silently accepting.

### Why `\A`/`\Z` and not `^`/`$`

This pattern used to be `^[0-9A-F]{64}$`, and that is not the same contract.
In Python, `$` matches at the end of the string **or immediately before a
trailing newline**, so a 65-character `tx_hash` whose 65th character was `\n`
matched a regex documented as "exactly 64". `^` was never the problem — it is
anchored, and a *leading* newline was always rejected, which is why the gap
was one-ended and easy to miss. `\Z` matches only at the true end of the
string.

**The consequence was bigger than one missing finding.** `audit` groups
payouts by the raw `tx_hash` string, so `<hash>` and `<hash>` plus a newline
are two different keys. On the parent commit a duplicated hash with one byte
appended produced *no finding of any kind*: no `MALFORMED_TX_HASH`, because the
regex accepted it, and no `REUSED_ACROSS_TASKS`, because the two records no
longer collided. The report came back `"status": "clean"` at exit **0**.

### What this change fixes, and what it leaves standing

Stated exactly, because the two are easy to conflate:

- It **does** stop that run reporting clean. The record is now reported as
  `MALFORMED_TX_HASH` and the process exits **1**, so the pair cannot pass an
  audit unnoticed.
- It **does not** restore the `REUSED_ACROSS_TASKS` finding for that pair.
  `by_hash` still keys on the raw string, and a malformed hash is deliberately
  *not* normalised before grouping. Normalising would mean running reuse
  analysis over a value the tool has just declared malformed, and silently
  repairing operator data is the behaviour the "lowercase is rejected rather
  than normalised" paragraph above argues against. The operator is told the
  record is malformed; fixing the data is their call, and the reuse finding
  appears on the next run once it is fixed.

`test_the_evaded_duplicate_is_reported_as_malformed_not_as_reuse` pins that
second bullet, so it is a recorded decision rather than something a later
reader discovers. `test_an_unevaded_duplicate_is_still_reported_as_reuse` is
its control: the ordinary reuse path is untouched.

Nine tests across the two classes; **six fail against the parent commit** and
three pass on both trees. The three that pass are deliberate controls: the
exact 64-character hash is still accepted, a leading newline was already
rejected (so the change is not mistaken for having fixed both ends), and an
un-evaded duplicate is still reported as reuse. Of the seven tests that
existed when the repair was first written, five failed on the parent; the
sixth failure is `test_the_evaded_duplicate_is_reported_as_malformed_not_as_reuse`,
added afterwards, which fails there for a different reason — on the parent that
pair produces no findings at all, so it does not produce the malformed one
either. The full per-test breakdown on both trees is in
`TRAILING_NEWLINE_EVIDENCE.txt`, sections 5 and 6.

`test_no_single_character_affix_makes_a_valid_hash` puts every printable ASCII
character plus the ASCII whitespace and NUL controls once before and once after
a valid hash: 202 values, each 65 characters long, so every one must be
rejected. Its worth, stated plainly: **exactly one** of those 202 cases
discriminated between the old pattern and the new one, and it is the same
trailing-newline case that `test_a_trailing_newline_is_malformed` covers on its
own. `^` was anchored, so the 101 prefix cases and 100 of the 101 suffix cases
were already rejected on the parent. It is a guard against a future re-widening
of the pattern, not evidence that the defect was broad. The test asserts both
its case count and its alphabet size, so the matrix cannot shrink and keep
passing.

Every run behind the paragraphs above — the two patterns compared side by
side, the reproduction on a pristine clone of the parent commit, the tests on
both trees, and the byte-comparisons below — is recorded with its real output
and real exit status in **`TRAILING_NEWLINE_EVIDENCE.txt`** in this directory.
The probe programs that build its fixtures are printed in full inside it, so
every fixture can be rebuilt from the transcript alone.

The committed fixtures are unaffected: all three committed audit reports
regenerate byte-for-byte after the change, at the same
`0f4a873696761cf94b9b1da8b7d18fe2ddb01f0ac32c3c21b71aa5f2e9c195b6` recorded in
the table above. Nothing in the repository's own data contained a trailing
newline in a hash; this closes the hole rather than changing any existing
verdict.

### Scope note

The root `README.md` index row for this directory still reads `27`, which this
change makes stale. That table is a generated artifact — `readme-index/`
regenerates its rows *and* its `**Totals:**` line from one computation, and
pins copies in `root_readme_after.md`, `corpus.tsv` and `index_report.json`.
Hand-editing one cell would leave the totals line no longer summing, which
`CONTRIBUTING.md` calls out as an invariant and `REVIEWERS_GUIDE.md` lists as
the cheapest check a reviewer has. Regenerating the whole index is a different
change from repairing one validator, so it is not done here.

To be exact about the cost rather than waving it away: `readme-index` *does*
read that cell. `readmeindex.py --root .. --root-readme ../README.md` reports
`count_differs` for this tool, taking that reconciliation from 1 differing row
to 2. It already reports six other tools the same way on the parent commit and
its exit status does not change, so no gate's verdict flips — but "no gate
reads it" would have been false. The row is stale, it is disclosed here, and
it is left for whoever regenerates the index rather than half-fixed by hand.

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
