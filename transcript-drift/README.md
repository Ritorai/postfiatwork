# transcript-drift

Every tool directory in this repository ships a `README.md` that makes
checkable claims — *"Ran 26 tests"*, *"exit 1"*, a block of commands a reviewer
is invited to run — and a `captured_output.txt` that is supposed to be the run
those claims came from. **Nothing checked that the two agreed.** A README could
claim 174 tests while its transcript recorded 130, and no command in the
repository would notice.

`driftcheck.py` notices. It is standard-library-only, deterministic, and exits
non-zero when a README and its transcript disagree.

`FORMAT.md` is the normative specification of the transcript format. It was
written after the transcripts, deliberately, so that adopting it is not a
migration — see its "Design constraint" section.

## Requirements

Python 3 standard library only: `argparse`, `json`, `os`, `re`, `sys`. No
third-party packages, no network, no writes outside `--output`. A test asserts
the import list stays stdlib-only.

## Usage

```
python3 transcript-drift/driftcheck.py --root .
python3 transcript-drift/driftcheck.py --root . --inventory transcript-drift/inventory.json
python3 transcript-drift/driftcheck.py --root . --inventory transcript-drift/inventory.json -o transcript-drift/drift_report_2026-08-04.json
```

| Exit | Meaning |
|---|---|
| `0` | every scanned directory is clean |
| `1` | drift found — see `findings` |
| `2` | setup error: `--root` is not a directory, `--inventory` missing or not JSON, `--output` unwritable |

`--inventory` takes `{tool: {readme: bool, transcript: bool}}` and exists for
one reason: this environment has no outbound git access, so only some
directories' file *contents* are available locally. The inventory lets the tool
report **presence** drift across all 42 directories while claiming **content**
drift only for the ones it actually read. A reviewer with a clone drops
`--inventory` and gets content drift for all 42 from the same command.

## Drift codes

| Code | Meaning |
|---|---|
| `MISSING_README` | directory has no `README.md` |
| `MISSING_TRANSCRIPT` | directory has a README but no `captured_output.txt` to check it against |
| `TRANSCRIPT_HAS_NO_COMMAND_RECORDS` | no `=== $ command ===` header anywhere; nothing in the file is cross-checkable |
| `TRANSCRIPT_RECORD_HAS_NO_EXIT` | a record exists but records no `exit=N` |
| `TEST_COUNT_MISMATCH` | the transcript ran a count the README does not claim, and the README claims some other count |
| `TEST_COUNT_NOT_CLAIMED_IN_README` | the transcript ran tests; the README claims no count at all |
| `TRANSCRIPT_SHOWS_TEST_FAILURE` | a `FAILED (...)` verdict is committed in the transcript |
| `EXIT_CODE_MISMATCH` | the transcript recorded an exit the README's exit claims do not acknowledge |
| `README_COMMAND_NOT_IN_TRANSCRIPT` | a fenced `python3 …` command in the README has no matching record |

## What the repository-wide run found

`drift_report_2026-08-04.json` is the committed artifact — the complete output,
with no findings removed. **Exit 1, 22 findings across 42 directories.**

| Code | Count |
|---|---|
| `TRANSCRIPT_RECORD_HAS_NO_EXIT` | 17 |
| `TRANSCRIPT_HAS_NO_COMMAND_RECORDS` | 4 |
| `MISSING_TRANSCRIPT` | 1 |
| everything else | 0 |

### The finding that implicates me

The four transcripts containing **no command records at all** are:

```
crosspath-runner
limitations-probe
path-collision-scanner
regression-checker
```

Those are the four tools I wrote most recently, and they are the four that
ignore the format the rest of the repository already follows. They use a prose
`$ command` style with no `=== ... ===` delimiters and no `exit=` lines, so a
reader cannot mechanically recover which command produced which output or what
it exited with. The older transcripts — `event-linter`, `evidence-manifest`,
`lifecycle-linter`, `reward-reconciler`, `sybil-detector`, `xrpl-auditor` — all
have proper headers; they just omit `exit=` on some records, which is the
17-count above.

I did not discover this by inspection. I wrote the checker against the format I
believed the repository used, ran it, and it reported my own work as the worst
offender. That finding is left in the report rather than fixed by loosening the
format, because loosening the format to accommodate the newest four files would
have made the tool agree with everything and detect nothing.

### `evidence-validator` has no transcript

The single `MISSING_TRANSCRIPT` is `evidence-validator`. It ships a README
making claims and no recorded run behind them. This came from the GitHub tree
API listing, not from a local file, so it is a presence claim about the public
repository at commit `aa662156d6af526f98f139a8d0c824b78312dda1`.

### No content drift in the eleven directories read in full

`TEST_COUNT_MISMATCH`, `TEST_COUNT_NOT_CLAIMED_IN_README`, `EXIT_CODE_MISMATCH`,
`README_COMMAND_NOT_IN_TRANSCRIPT` and `TRANSCRIPT_SHOWS_TEST_FAILURE` are all
**zero**. That is a negative result and it is only worth anything with a
positive control — see below.

## Positive control

A checker that reports zero of a code might be working or might be blind.
`capture.sh` builds a two-directory scratch tree, plants exactly one defect at a
time, and re-runs the real CLI. `captured_output.txt` shows the tool going from
`exit=0` with all counts zero to `exit=1` with exactly one count at 1, six
times:

| Planted defect | Code raised |
|---|---|
| transcript says `Ran 41 tests`, README claims 3 | `TEST_COUNT_MISMATCH` |
| transcript says `exit=2`, README documents only `0` | `EXIT_CODE_MISMATCH` |
| transcript verdict changed to `FAILED (failures=1)` | `TRANSCRIPT_SHOWS_TEST_FAILURE` |
| header renamed to a command the README never lists | `README_COMMAND_NOT_IN_TRANSCRIPT` |
| `===` delimiters stripped from every header | `TRANSCRIPT_HAS_NO_COMMAND_RECORDS` |
| `captured_output.txt` deleted | `MISSING_TRANSCRIPT` |

Two setup errors are shown returning `exit=2` (`--root` at a nonexistent path,
`--inventory` at a nonexistent file). The scratch tree is relative, never
committed, and removed by the same script. The unit suite covers the same
codes independently.

## One check I nearly shipped blind

The first version of `EXIT_CODE_MISMATCH` read exit claims out of prose only —
`exit 1`, `exit=2`, `exit code 2`. Nearly every README in this repository
documents its exit codes as a markdown table instead:

```
| Exit | Meaning |
|---|---|
| `0` | clean |
```

The word *exit* is in the header row; the numbers are three lines below it. The
prose regex matched none of them, so on most of the repository the check had
**no claims to compare against and could never fire**. Zero
`EXIT_CODE_MISMATCH` findings would have read as "no README misstates its exit
codes" when it actually meant "the check was inert".

I found this because I ran the checker against this tool's own README, and it
accused *this* README of `EXIT_CODE_MISMATCH` on exit `2` — the one exit code
documented only in the table above and nowhere in prose. `readme_exit_claims`
now reads exit tables as well, with eight tests covering it, and the
repository-wide `EXIT_CODE_MISMATCH` count of `0` is a real negative result
rather than an artefact.

## Tests

**57 tests, `OK`, exit 0.** CPython 3.11.15, Linux x86_64.

```
cd transcript-drift && python3 -m unittest test_driftcheck
```

They cover transcript parsing (headers, preamble, first-wins `exit=`, negative
exit codes, `Ran 1 test` singular), command normalisation, README extraction
(fences, `#` comments, bold counts, prose exit claims, markdown exit tables),
each drift code in isolation, end-to-end runs over temporary trees,
determinism, and the stdlib-only import assertion.

## Reproducing this directory

```
sh transcript-drift/capture.sh && python3 transcript-drift/finalize.py
```

(One line, and deliberately so: `finalize.py` runs *after* `capture.sh` has
closed the transcript, so it can never appear in that transcript as a record.
Written on its own line it starts with `python3` and the checker raises
`README_COMMAND_NOT_IN_TRANSCRIPT` against this very README — which it did,
before this line was joined. Limitation 4 below is the general form.)

`capture.sh` re-runs every command in `captured_output.txt`, including the
positive controls, and rewrites the file from the results. Nothing in the
transcript is typed by hand.

`finalize.py` handles the one genuinely self-referential problem here:
`capture.sh`'s last record *generates* the drift report, so at that instant the
transcript's own final record has no `exit=` line and the tool correctly reports
`transcript-drift` for it. `finalize.py` re-runs the identical command against
the finished tree, diffs the in-flight report against the final one, appends the
exact difference and the report's `sha256` as prose, then regenerates once more
and **exits 1 if that append changed a single byte of the report**. The measured
difference is one finding — the transcript's own last record — and the NOTE at
the end of `captured_output.txt` is that machine-written diff, not a claim.

## Determinism

The report has no timestamps and no durations. Two runs over an unchanged tree
produce byte-identical JSON, checked with `cmp` in `captured_output.txt`.
Findings are sorted by `(tool, code)` and `drift_counts` always lists all nine
codes including the zeroes, so a diff between two reports is meaningful.

## Coverage, stated plainly

```
directories_known:       42
content_compared:        11
presence_checked_only:   31
```

The `coverage` block in the report names every directory in each bucket. This
matters: `status: "clean"` from a run that read three directories would
otherwise be indistinguishable from one that read all 42. The 31
presence-checked-only directories may contain content drift this run cannot
see, and the report says so rather than implying they passed.

## 4 limitations

1. **31 of 42 directories were checked for presence only.** No `git clone` is
   possible from this environment (the proxy returns 403), so those
   directories' README and transcript *contents* were never read. Any test
   count or exit code drift inside them is invisible to this run. A reviewer
   with a clone gets the real number from one command; this file must not be
   read as "the repository has 22 drift findings", only as "22 were found in
   what could be read, plus presence across all 42".

2. **Test-count matching is set membership, not per-command.** If a README
   claims `Ran 57 tests` and the transcript has two records — one running 57
   tests and one running 12 — the 12 is reported unmatched, but the checker
   does not know *which* README sentence was supposed to describe *which*
   record. It cannot catch a README that attributes the right number to the
   wrong command.

3. **The exit-code check is asymmetric and can be silenced by omission.** A
   README that states no exit code anywhere is never accused of
   `EXIT_CODE_MISMATCH`, because there is no claim to contradict. Deleting the
   exit table from a README therefore makes that check pass. This is the right
   trade-off for a repository where several READMEs legitimately document no
   exit behaviour, but it means the check rewards silence.

4. **Command matching is textual after normalisation.** `python3 -m unittest
   test_x` and `python3 -m unittest -v test_x` are different commands to this
   tool, as are the same command run from two different working directories.
   Normalisation collapses whitespace and strips a trailing `; echo "exit=$?"`
   and a trailing `| tail`/`| head`; it does not parse shell. Expect false
   `README_COMMAND_NOT_IN_TRANSCRIPT` findings on any README that shows a
   command with flags the transcript ran without.
