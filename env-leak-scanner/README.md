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

`username` is zero with no review entries at all: no login name is recoverable
from any tracked document. The positive control below is what makes that worth
saying.

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
line a rule would match.** Two tests hold it up, and neither is an assertion
about intent:

- `TestPrefilterIsSuperset` runs all 22 rule-positive fixtures through
  `PREFILTER_RE` and requires every one to match — plus a second test that
  every fixture actually fires a rule, so the first cannot pass vacuously.
- `TestFullScanEqualsCandidateScan` writes a document mixing leaks, URLs,
  relative paths and clean prose to disk, scans the full text, scans the
  prefiltered candidates, and requires the two finding lists to be
  **identical**, while asserting the fixture is not empty.

`candidates_repo.json` is committed, so a reviewer can re-derive the report
without a clone. A reviewer *with* a clone should ignore all of this and run
`python3 leakscan.py --root . --review env-leak-scanner/review.json`, which
reads every byte directly.

## Tests

**65 tests, `OK`, exit 0.** CPython 3.10.12, Linux x86_64.

```
python3 -m unittest test_leakscan
```

Positive cases for all five categories and every rule, nine negative cases
(URLs, relative paths, markdown tables, prose, mid-line `@`), the prefilter
superset proof, full-vs-candidate equality, fail-closed review semantics,
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
   prefilter is proven to be a superset of the *current* rule set by the two
   tests above. Add a rule that can match a line containing none of the
   prefilter's trigger substrings and that proof lapses silently — the
   superset test will still pass unless a fixture for the new rule is added to
   `TestPrefilterIsSuperset.POSITIVES`. That coupling is a maintenance hazard,
   not a guarantee.

4. **This directory will report itself.** A leak report has to quote the
   leaked strings or the findings are unreadable, so `leak_report_2026-08-04.json`,
   `candidates_repo.json`, `review.json`, `captured_output.txt` and this README
   all contain `/sessions/sharp-stoic-knuth/...` and `/tmp/...`. Re-running the
   scanner after this commit will therefore report `env-leak-scanner`'s own
   files. That is expected, it is not a bug, and it is the reason the scan
   whose results are published here was run against the repository as it stood
   **before** this directory existed.
