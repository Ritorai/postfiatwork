# env-leak-scanner

A document that tells a reader to `cd /sessions/sharp-stoic-knuth/mnt/outputs`
is not reproducible. Nobody else has that directory; the sandbox it named no
longer exists. `leakscan.py` finds that class of mistake in this repository's
tracked documentation, in five categories, with the source location, the exact
matched text, and a sentence on why it harms reproducibility.

**Repository-wide result: 121 confirmed leaks across 23 files, 100 matches
reviewed and dismissed as benign, 0 stale review entries.** The complete
report is committed as `leak_report_2026-08-04.json`.

## Requirements

Python 3 standard library only: `argparse`, `json`, `os`, `re`, `sys`. No
third-party packages, no network. A test asserts the import list.

## Usage

```
python3 leakscan.py --root .
python3 leakscan.py --root . --review review.json -o leak_report_2026-08-04.json
python3 leakscan.py --root . --candidates -o candidates_repo.json
python3 leakscan.py --scan-candidates candidates_repo.json --review review.json
```

| Exit | Meaning |
|---|---|
| `0` | no confirmed leaks |
| `1` | at least one confirmed leak |
| `2` | setup error: `--root` missing, `--review` missing or malformed, `--output` unwritable |

## Categories and rules

| Category | Rules | Example match |
|---|---|---|
| `absolute_path` | `EL-ABS-POSIX`, `EL-ABS-WIN`, `EL-ABS-UNC` | `/opt/build/out.json`, `D:\builds\out.json`, `\\fileserv\share\x` |
| `home_directory` | `EL-HOME-TILDE`, `EL-HOME-ENV`, `EL-HOME-ABS`, `EL-HOME-WIN` | `~/bin/tool.py`, `$HOME`, `/home/rito/x`, `C:\Users\Rito\x` |
| `temp_directory` | `EL-TEMP-POSIX`, `EL-TEMP-ENV` | `/tmp/a.json`, `/var/folders/...`, `$TMPDIR`, `%TEMP%` |
| `hostname` | `EL-HOST-SESSION`, `EL-HOST-LOCAL`, `EL-HOST-CLOUD` | `/sessions/<name>/...`, `localhost:8080`, `ip-10-0-3-17` |
| `username` | `EL-USER-PATH`, `EL-USER-WINPATH`, `EL-USER-PROMPT` | `rito` out of `/home/rito/x`, out of `C:\Users\Rito\x`, out of `rito@buildbox:~$` |

A span claimed by a specific rule is not re-reported by the generic
`absolute_path` rules, so `/home/rito/x` is a `home_directory` plus a
`username`, never also a bare `absolute_path`.

URLs and relative paths are deliberately not matched: a path only counts when
it begins at a word boundary that is not itself a slash, which is what keeps
`https://github.com/Ritorai/postfiatwork` and `transcript-drift/driftcheck.py`
out of the report. Nine negative tests pin that behaviour.

## Review is data, and it is fail-closed

Step 3 of the task asks that detected matches be reviewed to separate real
leaks from harmless examples. That review lives in `review.json`, keyed by
`<file>:<line>:<matched text>`, and every entry carries a written `reason`:

```json
"schema-checker/captured_output.txt:285:/root/properties/id/type": {
  "verdict": "benign",
  "reason": "RFC 6901 JSON Pointer rooted at the schema document, not the /root home directory. A false positive of EL-HOME-ABS; see README limitation 2."
}
```

A match with **no** review entry is reported as a leak. A new leak therefore
cannot become benign by default, and no regex was quietly weakened to make a
finding disappear — every dismissal is a sentence somebody can disagree with.
An entry with a missing or empty `reason`, or a verdict other than
`leak`/`benign`, is a setup error and exits `2`. The report also lists
`stale_review_entries`: keys that no longer match anything, so a dismissal
cannot outlive the line it was written about. It is currently empty.

## What the repository-wide run found

| Category | Confirmed |
|---|---|
| `temp_directory` | 101 |
| `hostname` | 20 |
| `absolute_path` | 0 |
| `home_directory` | 0 |
| `username` | 0 |

The `weak-assertion-scanner` case named in the task brief **is present**, at
`weak-assertion-scanner/README.md` line 281:

```
python3 weakassert.py --root /sessions/sharp-stoic-knuth/mnt/outputs -o self_scan_report.json
```

It is not the only one. The same sandbox root appears 20 times in total, and
`weak-assertion-scanner` alone accounts for 12 of them, mostly in
`repair_captured_output.txt` where ten `cd` lines record the author's session
directory one tool at a time. `LIMITATIONS.md`, `REVIEWERS_GUIDE.md`,
`snapshot-diff/README.md`, `doc-validator/` and `bundle-index/` carry the
rest. The 101 `temp_directory` hits are `/tmp/...` scratch paths recorded in
transcripts; the worst of them embed a per-run random suffix
(`/tmp/indexgen_test_gkpschwm/...`) that no reader can ever recreate.

### Two categories came back zero, and that is the interesting part

`absolute_path` and `home_directory` both dropped from a raw 78 and 20 to
**zero** after review. Every single one was a false positive of the same
shape: **RFC 6901 JSON Pointers**. `schema-checker` and `dup-detector` print
error locations like `/root/properties/id/type` and `/records/2/cid`, which
are pointers into a JSON document, not filesystem paths — but they are
syntactically indistinguishable from absolute paths. `/root/...` is the worst
case, because it also looks exactly like the root account's home directory.

That is a real limitation of the tool and it is recorded as 66 individual
review entries rather than patched away by excluding `/root` or `/records`,
which would have made the scanner blind to a genuine `/root/x` leak.

`username` is zero with no review entries at all in
`leak_report_2026-08-04.json`: no login name was recoverable from any
tracked document at the time of that snapshot. The positive control below
is what makes that worth saying.

(A scan of the tree as it stands today does report `username` findings.
Twelve of them predate this commit -- the rule/example table above and
the positive-control transcript have always quoted `/home/rito/x` and
`C:\Users\Rito\x`, and the parent commit's own scan reports them; they
are absent from the snapshot only because it was taken with the
`--candidates` pipeline, which is what the "known lower bound" note
below is about. The rest are this commit's new prose, which quotes
`/home/rito/...` as the counterexample that used to be dropped. See
"The superset test that proved less than its name". The distinction
matters in both cases: these are documentation of a leak pattern, not a
leaked login name, and they are reported rather than hidden.)

## Positive control

A scanner reporting zero of a category might be working or might be blind.
`capture.sh` builds a clean two-line scratch tree, confirms `exit=0` with all
five counters at zero, then appends **one** planted line at a time and re-runs
the real CLI. From `captured_output.txt`:

| After planting | `confirmed` | `by_category` delta |
|---|---|---|
| (clean tree) | 0 | all five zero, `exit=0` |
| `/opt/build/out.json` | 1 | `absolute_path` +1 |
| `/home/rito/out.json` | 3 | `home_directory` +1, `username` +1 |
| `/tmp/a.json` | 4 | `temp_directory` +1 |
| `http://localhost:8080/x` | 5 | `hostname` +1 |

Every category fires, `username` included, and each plant moves exactly the
counter it should. `--root` at a nonexistent path returns `exit=2`.

## Coverage, and how the repository-wide number was obtained

The container this repository is maintained from has **no outbound network
access** (`git clone` returns 403 through the proxy), so the full text of all
122 tracked `.md` and `.txt` files — 1,492,743 bytes — could not be read
locally. Rather than scope the task down, the scan was split:

1. `--candidates` emits every line matching `PREFILTER_RE`, a deliberately
   over-broad superset of what any rule can match. Over the whole repository
   that is **287 lines from 76 files**, 58,699 bytes: small enough to move.
2. `--scan-candidates` classifies that set with the identical rules.

The soundness of this rests on one claim: **the prefilter can never drop a
line a rule would match.**

**That claim used to be false, and the test named after it did not
notice.** See "The superset test that proved less than its name" below.
It is now held up by three things:

- `TestPrefilterIsSuperset.test_prefilter_covers_every_placement_of_every_core`
  takes a set of core strings that between them exercise **all 15 rules**,
  wraps each in every printable single-character prefix and every
  printable single-character suffix, keeps the combinations that genuinely
  fire a rule, and requires `PREFILTER_RE` to match all of them. **3,847
  lines per run**, generated rather than listed, and the test's own floor
  is `assertGreater(checked, 3000)` so the matrix cannot quietly collapse.
- `test_every_rule_is_exercised_by_some_core` pins the coverage: adding a
  rule without adding a core fails loudly instead of silently shrinking
  what the class proves.
- The original 22-line `POSITIVES` list is kept as a smoke test, together
  with the assertion that every entry actually fires a rule — so it still
  cannot pass vacuously.
- `TestFullScanEqualsCandidateScan` writes a document mixing leaks, URLs,
  relative paths and clean prose to disk, scans the full text, scans the
  prefiltered candidates, and requires the two finding lists to be
  **identical**, while asserting the fixture is not empty.

`candidates_repo.json` is committed, so a reviewer can re-derive the report
without a clone. A reviewer *with* a clone should ignore all of this and run
`python3 leakscan.py --root . --review env-leak-scanner/review.json`, which
reads every byte directly.

## The superset test that proved less than its name

`README.md` said the whole candidate pipeline's soundness rested on one
claim -- "the prefilter can never drop a line a rule would match" -- and
pointed at `TestPrefilterIsSuperset` as holding it up. `leakscan.py`'s
comment above `PREFILTER_RE` said the same: "a line that matches ANY rule
MUST match this. Tested both ways."

What the test did was run a hand-written list of 22 example lines through
`PREFILTER_RE`. A list cannot establish "no line, ever"; it can only fail
to find a counterexample. And there were counterexamples.

**The bug.** `PREFILTER_RE`'s POSIX branch requires the leading `/` to sit
at start-of-line or after one of `[\s"'`(\[<=,;:]`. `EL-USER-PATH` --
`(?:/home|/Users)/([A-Za-z][A-Za-z0-9._-]{1,31})\b` -- has no such anchor
and matches anywhere in a line. Every entry in `POSITIVES` happened to put
the path at a start of line or after a space, so the gap was invisible.
`EL-ABS-UNC` allows `._-` as the first character after `\\`; the
prefilter's UNC branch allowed only `[A-Za-z0-9]`.

**What was dropped.** A generative sweep over the two affected rule
families -- six core strings, every printable single-character prefix --
produced 582 lines that genuinely fire a rule. The parent commit's
prefilter rejected **457** of them. (The committed test's matrix is
wider: 24 cores covering all 15 rules, prefixes *and* suffixes, 3,847
lines. It is the evidence file's smaller sweep that is quoted here,
because that is the one whose exact output is recorded.) Illustrative
examples:

```
ls foo/home/alice/bin                                  EL-USER-PATH: alice
see ../Users/rito/x                                    EL-USER-PATH: rito
The build wrote its log to ../home/rito/out.json.      EL-USER-PATH: rito
copy from \\_fileserv\share\x                          EL-ABS-UNC
```

This is not confined to `--scan-candidates`: `scan_text` applies the same
prefilter, so the README's advice that a reviewer with a clone should
"ignore all of this and run `leakscan.py --root .`, which reads every
byte directly" did **not** restore the missed findings either.

**The fix.** `/home/|/Users/` is added to `PREFILTER_RE` and the UNC
branch is widened to `[A-Za-z0-9._-]`. Both are one-line changes; the
work was in finding them.

**The repair to the test is the point of this change.** The list is kept
-- it is a good smoke test and it pins the intent of each rule -- and a
generative check is added beside it: one core string per rule family,
every printable single-character prefix, keep the combinations that
genuinely fire a rule, require the prefilter to match all of them. That
check fails on the parent commit and passes here, and it does not depend
on anyone thinking of `foo/home/alice` in advance.

**Impact, measured rather than asserted.** A full re-scan
(`leakscan.py --root .. --review review.json`) is compared before and
after, per file:

```
files outside env-leak-scanner/ with a changed count: 0
confirmed findings outside env-leak-scanner/: 432 -> 432
```

**Not one pre-existing finding changes.** The counterexamples do not occur
in the repository's prior text, so on the corpus as it stood this fix is
behaviour-preserving. The defect was latent; the value of fixing it is the
next `/home/` someone writes mid-line.

Inside this directory the count *does* go up, because this README section
and `PREFILTER_SUPERSET_EVIDENCE.txt` quote `/home/rito/...` and
`../home/alice/...` at the reader on purpose. That is limitation 4 below
-- "this directory will report itself" -- doing exactly what it says, and
it is left visible rather than worked around by mangling the examples
until the scanner stops seeing them. An example a scanner cannot see is
not an example.

No exact figure is given for those two files, deliberately: the evidence
file is part of what the scan reads, so any number printed in it would
change the next time it is regenerated. A self-referential count is not a
fact about the repository, and quoting one would be the same category of
error this whole section is about.

`leak_report_2026-08-04.json` is a dated, pinned snapshot -- `pinned` in
`report-freshness/manifest.json`, so nothing requires it to equal a fresh
scan -- and it is not regenerated by this change. `freshness.py` still
exits `0`. The counts table above under "What it found" still describes
that file accurately.

**But its zeros are now a known lower bound, and that is a soundness
point, not a freshness one.** That snapshot was harvested through
`candidates_repo.json`, which was produced by the *broken* prefilter. Its
`username: 0`, `absolute_path: 0` and `home_directory: 0` are therefore
derived through exactly the branch this section proves was under-broad
for `EL-USER-PATH` and `EL-ABS-UNC`. Re-running the direct
`--root .` scan on the parent tree with the fixed prefilter finds no new
findings there, so the snapshot's zeros are not known to be wrong -- but
they are no longer known to be right either, and "no login name was
recoverable" above should be read as "none was found by a pipeline we now
know could have missed one". The report's own `coverage.note` still says
"the prefilter is a proven superset of every rule, so no rule match can be
lost"; that string is generated by `leakscan.py` and is not corrected
here, because doing so would mean rewriting a dated snapshot's contents.
Anyone relying on those zeros should re-run the direct scan.

`PREFILTER_SUPERSET_EVIDENCE.txt` records both scans, the per-file
comparison, the generative sweep on both trees, and a one-line document
that demonstrates the drop end to end.

## Tests

**70 tests, `OK`, exit 0.** CPython 3.11.15, Linux x86_64.

```
python3 -m unittest test_leakscan
```

Positive cases for all five categories and every rule, nine negative cases
(URLs, relative paths, markdown tables, prose, mid-line `@`), the generative prefilter
superset check, full-vs-candidate equality, fail-closed review semantics,
setup errors, determinism, and the stdlib-only import assertion.

## Determinism

No timestamps, no durations. Two runs inside a single transcript record —
so nothing changes between them — produce `BYTE_IDENTICAL` output.
`sha256(leak_report_2026-08-04.json) =
dab40f0b653132521be15efa35ff84bb4d535517f264a529e4821898c013480b`.

## 4 limitations

1. **The scanner cannot tell a JSON Pointer from a filesystem path.** This is
   not hypothetical: it is 66 of the 100 benign dismissals and it wiped two
   whole categories to zero. Anything that borrows POSIX path syntax for a
   non-filesystem purpose — JSON Pointers, XPath, URL paths written without a
   scheme — will be reported. Reviewing is the mitigation; suppressing
   `/root` and `/records` would have been the wrong one.

2. **`/root` is genuinely ambiguous.** `EL-HOME-ABS` matches it because a real
   `/root/build/out.json` is a real home-directory leak. Every `/root/...` hit
   in this repository turned out to be a schema pointer, so the category reads
   zero — but the next one may not be, and this report gives a reader no way
   to tell the two apart except by opening the file.

3. **This run classified 287 prefiltered lines, not 1.49 MB of prose.** The
   superset property is now checked generatively rather than from a list,
   which closes the placement gap that made it false, but it is still not a
   proof over all strings. Adding a rule without adding a core would shrink
   what the check proves — so `test_every_rule_is_exercised_by_some_core`
   fails in that case rather than passing quietly. What it still cannot
   catch is a rule that matches a line containing none of the prefilter's
   trigger substrings *and* whose core happens to be covered by another
   rule. `EL-USER-PROMPT` is also `^`-anchored, so no prefix can reach it;
   it is covered by the suffix half and by a named case. That residue is a
   maintenance hazard, not a guarantee, and it is why `leakscan.py`'s
   comment above `PREFILTER_RE` says a new rule must be checked the same
   way.

4. **This directory will report itself.** A leak report has to quote the
   leaked strings or the findings are unreadable, so `leak_report_2026-08-04.json`,
   `candidates_repo.json`, `review.json`, `captured_output.txt` and this README
   all contain `/sessions/sharp-stoic-knuth/...` and `/tmp/...`. Re-running the
   scanner after this commit will therefore report `env-leak-scanner`'s own
   files. That is expected, it is not a bug, and it is the reason the scan
   whose results are published here was run against the repository as it stood
   **before** this directory existed.
