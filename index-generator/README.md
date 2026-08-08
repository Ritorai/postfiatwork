# index-generator

A stdlib-only Python 3 CLI tool that generates a repository `INDEX.md` for a
multi-tool repository (a repo with ~N top-level tool directories, each
containing an entrypoint script, a test module, a README, etc.) **and**
self-checks the result against the real tree, so documentation drift --
like a root README claiming "13 tools / 476 tests" while 33 tool
directories actually exist -- is reported as a finding instead of being
silently baked into a pretty-looking index.

## Purpose

- Discover tool directories one level deep under a repo root.
- Extract a one-line description and a claimed test count from each tool's
  `README.md`, never inventing either when absent.
- Emit a deterministic `INDEX.md` table plus a canonical JSON findings
  report.
- Cross-check: missing files, missing descriptions/counts, entrypoints
  without a matching test module, drift between a previously-written
  `INDEX.md` and a freshly computed one, and drift between a root README's
  claimed tool/test counts and reality.

## Files in this delivery

```
indexgen.py           entrypoint / implementation
test_indexgen.py      test suite (161 tests)
test_capture.py       regression tests for the transcript-generation path (capture.sh)
capture.sh            regenerates captured_output.txt; every record is a real run
pipe_scan.py          Finding 4 disclosure utility (repo-wide, read-only)
pipe_classify.py      shell-quote-aware companion: labels each `|` pipeline/or/quoted/escaped
test_pipe_classify.py test suite for pipe_classify.py (72 tests)
pipe_classification_report.json  committed pipe_classify.py output for the current tree
compare_relocation.py normalises + hashes captured_output.txt for the relocation proof
repro_findings.txt    live reproduction of Findings 1-3 against the ORIGINAL transcript
CHECK_INDEX_CRASH_EVIDENCE.txt  before/after runs for the --check-index crash (see below)
README.md             this file
fixture_repo/         6-tool fixture repo used for the demo + proof runs (generated, not committed)
root_readme_sample.md sample root README used to demonstrate ROOT_README_COUNT_DRIFT
captured_output.txt   real transcript: test run, CLI invocations, determinism proof
sample_report.json    sample JSON report (fixture_repo, findings present)
sample_report_clean.json  sample JSON report (a clean single-tool repo, zero findings)
sample_INDEX.md       sample generated INDEX.md (from fixture_repo)
```

## Exact rerun commands

Run the test suite (indexgen.py itself):

```
python3 -m unittest test_indexgen -v
```

Run the transcript-generation regression tests (this task's addition; see
"Transcript-generation path" below): `python3 -m unittest test_capture -v`.
(Not shown as a fenced command here on purpose: `capture.sh` itself only
ever runs a non-self-referential SUBSET of this module as one of its
records -- see "Verification" for exactly why the full run can't check
itself from inside its own transcript.)

Regenerate `fixture_repo/` (not committed as loose files -- see
"Regenerating the fixture"):

```
python3 make_fixture_repo.py fixture_repo
```

Generate an index + report for the fixture repo (every path below is
relative on purpose -- this is the literal command `capture.sh` records):

```
python3 indexgen.py --root fixture_repo --root-readme root_readme_sample.md --write-index fixture_INDEX.md -o fixture_report.json
```

Check a previously-written index for drift (without rewriting it):

```
python3 indexgen.py --root fixture_repo --check-index fixture_INDEX.md -o fixture_report_check1.json
```

Regenerate `captured_output.txt` itself, and run all the demonstrations
above (plus determinism/relocation proofs, error paths, and the Finding
1-4 regression coverage) for real:

```
bash capture.sh
```

## Expected results (fixture_repo, 6 tools)

Running `python3 indexgen.py --root fixture_repo --root-readme root_readme_sample.md
--write-index /tmp/x.md -o /tmp/x.json` against the included `fixture_repo/`
(whose tools are deliberately a mix of healthy and broken) produces:

| Tool    | Has README | Has captured_output.txt | Test module(s)  | Claimed tests | Notable findings |
|---------|------------|--------------------------|------------------|----------------|-------------------|
| alpha   | yes        | yes                      | test_alpha.py    | 4              | none (healthy) |
| beta    | yes        | **no**                   | test_beta.py     | null           | MISSING_CAPTURED_OUTPUT, NO_CLAIMED_TEST_COUNT |
| gamma   | yes        | yes                      | test_gamma.py    | 6              | ENTRYPOINT_TEST_MISMATCH (helper.py) |
| delta   | **no**     | yes                      | (none)           | null           | MISSING_README, MISSING_TEST_MODULE, ENTRYPOINT_TEST_MISMATCH |
| epsilon | yes        | yes                      | test_epsilon.py  | null           | NO_DESCRIPTION, NO_CLAIMED_TEST_COUNT |
| zeta    | yes        | yes                      | test_zeta.py     | 3              | none (unicode + CRLF README, healthy) |

Totals line: `**Totals:** 6 tools; test count: 13 (from 3 tools; 3 unknown)`.

`root_readme_sample.md` claims "5 tools and 13 tests" -- 13 matches the sum
of known per-tool counts (4 + 6 + 3), so only the tool-count half of
`ROOT_README_COUNT_DRIFT` fires (5 claimed vs. 6 discovered). Overall exit
code for this run is `1` (findings present).

## Exit codes

| Exit | Meaning |
|------|---------|
| `0`  | Scan completed, zero findings. |
| `1`  | Scan completed, one or more findings were produced. |
| `2`  | Invalid input/usage: `--root` missing or not a directory, unwritable `--write-index`/`-o` path, or a bad/unknown CLI argument. |

Note: an unreadable/missing `--check-index` or `--root-readme` path is
**not** a usage error -- it is reported as an `UNREADABLE_FILE` finding
and the corresponding check is skipped, per the "never fatal" requirement.
Only `--root` and the two output paths are usage-fatal (exit 2).

## Finding codes

| Code | Fires when |
|------|------------|
| `MISSING_README` | A tool directory has no `README.md`. |
| `MISSING_CAPTURED_OUTPUT` | A tool directory has no `captured_output.txt`. |
| `MISSING_TEST_MODULE` | A tool directory has zero `test_*.py` files. |
| `NO_DESCRIPTION` | `README.md` exists but no description line could be extracted (blank, or only headings/badges). |
| `NO_CLAIMED_TEST_COUNT` | `README.md` exists but no recognizable "N tests" style phrase was found. |
| `ENTRYPOINT_TEST_MISMATCH` | A specific `foo.py` entrypoint has no matching `test_foo.py` (fires per missing pairing, even if the tool has other test modules). |
| `UNREADABLE_FILE` | A file that should be read (a tool's `README.md`, `--check-index` path, or `--root-readme` path) could not be opened or decoded; skipped, not fatal. |
| `INDEX_DRIFT` | `--check-index PATH` was given and a row in that existing index is missing, extra, or different vs. the freshly computed index. |
| `MALFORMED_INDEX_ROW` | `--check-index PATH` was given and a five-column row was not compared: either it is **inside** the index table and could not be parsed (wrong cell count, or a claimed-test-count cell that is neither an integer nor `?`), or it has fallen **out** of the table (a blank line or non-table line ended it) in a file that does contain an index table. The finding carries the repo-relative path and 1-based line number so it cannot be mistaken for silence. |
| `NO_INDEX_TABLE` | `--check-index PATH` was given and the file contains no row matching the index header at all. Without this, pointing the flag at the wrong file looks identical to pointing it at an empty index: every tool comes back as `INDEX_DRIFT` "(added)" and nothing says why. |
| `ROOT_README_COUNT_DRIFT` | `--root-readme PATH` was given and its claimed tool count and/or test total disagree with what was actually discovered. This is the check that catches a "13 tools" claim when 33 exist. |

## Determinism contract

- No wall-clock reads anywhere in `indexgen.py` (no `time.time`, `utcnow`,
  or `now()` substrings, including in comments).
- No absolute paths, hostnames, durations, or mtimes appear in the JSON
  report or `INDEX.md` -- everything is repo-relative (tool directory names
  and in-directory filenames only).
- Every list is explicitly sorted. Findings are sorted on
  `(code, tool, location, message)` and then, as a final tiebreaker, on the
  canonical JSON encoding of the finding dict itself -- this guarantees a
  total order even for two findings that are otherwise identical on all
  four semantic fields (see `finding_sort_key` in `indexgen.py`).
- The JSON report is written with
  `json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n"`,
  via `open(path, "w", encoding="utf-8", newline="\n")`. `INDEX.md` is
  written the same way, so both are byte-stable across repeated runs and
  across relocating the scanned tree to a different absolute path -- see
  `captured_output.txt` for the actual sha256sum proof.

## Bug found during the mandatory bug hunt

**Bug:** a tool's genuinely-extracted description that happens to equal the
literal text `_(none)_` (the placeholder `render_index` uses to mean "no
description") was silently reparsed as `None` by `parse_index`. On a repo
where a real, unchanged tool's README's first content line is literally
`_(none)_`, running `--write-index` and then immediately `--check-index`
against the file just written -- with **nothing** in the tree changed --
produced a false-positive `INDEX_DRIFT` ("row differs"), because the fresh
scan's description (`"_(none)_"`, a string) no longer equaled the
reparsed-from-disk description (`None`).

**Triggering input:** a tool directory whose `README.md`'s first
non-empty/non-heading/non-badge line is exactly `_(none)_`, e.g.:

```
_(none)_
3 tests.
```

**Fix:** `render_index`/`parse_index` now use `_encode_description_cell` /
`_decode_description_cell`, which detect the collision case and emit a
disambiguating escaped form (`\_(none)_`) for real content that happens to
equal the sentinel, while the true "no description" case remains the bare
`_(none)_`. `_decode_description_cell` reverses this unambiguously.

**Pinning tests** (`test_indexgen.py`):
- `test_description_literally_equal_to_none_marker_round_trips`
- `test_genuine_none_description_still_parses_as_none`
- `test_none_marker_collision_causes_no_false_index_drift_end_to_end`
  (full end-to-end repro of the false-positive `INDEX_DRIFT`, asserting it
  no longer occurs)


## Second bug: `--check-index` was fatal on a foreign five-column table

**Bug.** `parse_index` treated *any* five-column markdown table as an
index: for every row whose first cell was not the literal `Tool`, it
called `int()` on the fifth cell. A markdown file containing a
five-column table whose last column holds a word therefore raised an
uncaught `ValueError` out of `run()`.

**Triggering input: a file committed in this repository.**
`commit-claim-auditor/README.md` documents its own output with the table
header `| source_file | line | claim_type | status | reason |`, and
`reason` is not an integer. Every `*.md` in the tree was swept through
`--check-index`; that one file, and only that file, crashed it.

**What the README did and did not already say, stated precisely.** It is
tempting to present this as a violation of an existing written contract,
and two sentences nearly say so, but neither actually covers this case
and it would be wrong to claim otherwise:

- "Exit codes" says an "unreadable/missing `--check-index` or
  `--root-readme` **path** is *not* a usage error". That is about a path
  that cannot be opened. This file opened fine; its *content* was the
  problem.
- Limitation 3 mentions a "hand-edited or foreign `--check-index` file",
  but it is about **comma-splitting in the Entrypoint(s)/Test Module(s)
  columns**, not about the count column and not about crashing.

So the defect does not need a quoted contract to stand on. An uncaught
`ValueError` escaping `run()` is a defect on its own terms: the exit
table promises exactly three exit codes, and CPython's exit-on-exception
is `1`, which this tool defines as "scan completed, one or more findings
were produced". A caller branching on the status could not tell the crash
apart from an ordinary drift result -- and no report file was written at
all, so nothing downstream could tell either.

**Fix, in three parts.** The obvious one-line guard (skip a row whose
count cell will not parse) is wrong on its own, and so is the header gate
on its own. Both would have traded a loud failure for a silent one, by
different routes, and the second route was found only by having the change
adversarially reviewed after the first two parts were written.

1. **Only rows under this file's own table header are index rows.** The
   header is matched on its **stripped cells**, not its bytes, so a
   column-aligned or trailing-space variant still counts -- requiring byte
   equality was itself a regression, because a formatter-run index stopped
   being recognised and every tool in it came back as `INDEX_DRIFT`
   "(added)", a message that was false. What the gate removes is the whole
   class of foreign tables, including ones whose fifth column happens to
   be *numeric*: those used to be parsed into phantom tools, which a
   count-cell guard alone would not have touched.

2. **A malformed row inside a real index table is reported, not
   dropped.** It becomes a `MALFORMED_INDEX_ROW` finding carrying the
   repo-relative path and 1-based line number. Silently skipping it would
   be worse than the crash: the row would vanish from `old_rows`, so a
   stale entry naming a tool that no longer exists would stop producing
   its `INDEX_DRIFT` "(removed)" finding, and the run would certify a
   corrupt index as drift-free. `CONTRIBUTING.md` states the rule this
   follows: "One malformed record must not abort the run. Report it and
   keep going." Reporting *and* continuing is the whole rule; the first
   half alone is not a fix.

3. **A five-column row that has fallen OUT of the table is reported
   too.** This is the part the review caught, and it is the same silent
   pass as part 2 reached sideways. A markdown table ends at a blank line
   or any non-table line, so a stale row below a merge-conflict marker --
   which is how this actually shows up -- is not in the table any more.
   Ignoring it dropped it from `old_rows` and the run came back **exit 0,
   zero findings** on a visibly corrupt index. Now, *if the file contains
   an index table at all*, a stray five-column row after it is reported.
   If the file contains no index table anywhere, nothing is reported: a
   document that never claimed to be an index is not a broken index.

   That second clause is what keeps part 1 honest, and it is why
   `NO_INDEX_TABLE` exists as a separate finding -- otherwise pointing
   `--check-index` at the wrong file would look exactly like pointing it
   at an empty index.

**Pinning tests** (`test_indexgen.py`, class `ForeignCheckIndexTests`, 21
tests). Twenty of the twenty-one fail against the parent commit; the
twenty-first is `test_a_real_index_table_still_parses`, the control that
must pass on both.

Part 1 -- foreign tables are not index tables:

- `test_foreign_table_with_a_word_count_yields_no_rows`
- `test_foreign_table_with_a_numeric_count_yields_no_phantom_tools` --
  the case a count-cell guard alone would have missed
- `test_the_committed_file_that_triggered_it_no_longer_crashes` -- guards
  the specific real file, and asserts the row it guards is still there
- `test_cli_exits_1_and_writes_a_report_instead_of_a_traceback` -- a real
  subprocess: no `Traceback` on stderr, and the report exists on disk
- `test_a_column_aligned_index_is_still_an_index`
- `test_a_header_with_different_column_names_is_not_an_index`
- `test_a_real_index_table_still_parses` -- the control

Part 2 -- a malformed row inside the table:

- `test_bad_count_cell_inside_an_index_table_is_reported`
- `test_wrong_cell_count_inside_an_index_table_is_reported`
- `test_a_malformed_row_does_not_silence_a_stale_entry` -- the regression
  the naive fix would have introduced, pinned directly
- `test_malformed_row_finding_carries_the_file_and_line`
- `test_parse_index_wrapper_returns_rows_and_drops_problems` -- written
  out rather than derived; comparing the wrapper to
  `parse_index_rows(d)[0]` is a tautology, and this repository's own
  weak-assertion scanner classifies that shape `WA003`

Part 3 -- a row that fell out of the table:

- `test_a_row_pushed_out_of_the_table_is_reported`
- `test_a_blank_line_inside_the_table_is_reported_not_ignored`
- `test_a_stray_row_in_a_file_with_no_index_table_is_not_reported` -- the
  other half of the rule
- `test_the_conflict_case_end_to_end_does_not_come_back_clean`
- `test_no_index_table_is_its_own_finding`
- `test_a_real_index_produces_no_no_index_table_finding`

Line numbers and labels:

- `test_line_numbers_count_newlines_only` -- `str.splitlines()` also
  splits on form feed and `U+2028`, which no editor counts as a line and
  which put the reported number out by one
- `test_crlf_index_still_parses`
- `test_report_label_is_repo_relative_never_absolute` -- the determinism
  contract at the top of `indexgen.py` forbids an absolute path in any
  output

**Evidence:** `CHECK_INDEX_CRASH_EVIDENCE.txt` -- real runs on a clean
clone of the parent commit and on the fixed tree, side by side. It builds
the guard-only variant mechanically from the parent commit and runs it,
so the "the obvious fix is worse" claim is a measurement rather than an
assertion; it does the same for the merge-conflict case that part 3
exists to catch. It also byte-compares both trees on the things that must
NOT change: `parse_index` over the committed `sample_INDEX.md`, and a
full run over `fixture_repo` (`INDEX.md` and `report.json` both
`cmp`-identical).


## Regenerating the fixture

`fixture_repo/` is produced by `make_fixture_repo.py` rather than committed as
loose files:

```
python3 make_fixture_repo.py fixture_repo
```

It stores contents base64-encoded and writes them in binary mode so the
deliberate CRLF fixture (`zeta/README.md`) survives the round trip. Regenerating
the fixture and re-running `indexgen.py` reproduces the committed
`sample_report.json` and `sample_INDEX.md` hashes exactly; `captured_output.txt`
shows that round trip and the diff proving the trees are byte-identical.


## Transcript-generation path: the bug, and the fix

`indexgen.py` was never buggy. `captured_output.txt` -- the evidence file
this repository requires every tool directory to ship -- was, and there
was no `capture.sh` that regenerated it (this directory shipped a
hand-authored transcript with no runnable script behind it; the
diff/round-trip record even contained a literal placeholder,
`diff -r fixture_repo <regenerated>`, instead of a real destination path
and real `diff` output -- a tell that it was written by hand, not run).

`repro_findings.txt` (not named `captured_output.txt` on purpose, so
neither checker discovers it as a real transcript) reproduces Findings
1-3 against the ORIGINAL committed file, with real command output: the
exact broken record verbatim, `validate_transcript.py` flagging it, the
`grep`-found `Ran 138`/`Ran 140` contradiction, and the live Finding-2
demo below run for real.

**Finding 1.** One record's command was
`python3 -m unittest test_indexgen -v 2>&1 | grep stale_catalogued`.
`grep` filtered out both the `Ran N tests in ...` line and the `OK`/
`FAILED` verdict. `transcript-schema/validate_transcript.py` flagged this
as `TRANSCRIPT_RECORD_MISSING_RAN_LINE` + `TRANSCRIPT_RECORD_MISSING_VERDICT`
-- the only two such findings anywhere in the repository.

**Finding 2 (the serious one).** The `exit=` recorded for that same
record was `grep`'s exit status, not `unittest`'s, because the shared
`rec()` convention runs every record through `sh -c "$*"`, and `sh -c` on
a pipeline reports the LAST command's status. Reproduced live with a
purpose-built suite (one passing test whose name `grep` matches, one
failing test):

```
piped through grep    -> rc = 0    (what gets recorded as exit=)
unittest directly     -> rc = 1    (the truth: the suite FAILED)
with pipefail         -> rc = 1    (surfaces it)
```

A failing test suite could be recorded in committed evidence as
`exit=0`. `test_capture.py`'s `PipefailMaskingTests` reproduces this
exact scenario (and its fix) against a real throwaway failing suite, not
just the minimal case.

**Finding 3.** The old file simultaneously contained `Ran 138 tests`
(stale, from before two tests were added) and `Ran 140 tests` (correct at
the time) for the same suite, in the same committed file.

`capture.sh` also runs two smaller, real, unpiped `unittest` invocations
that legitimately produce their own distinct `Ran N tests` lines and are
not part of Finding 3's contradiction: the two `test_stale_catalogued_*`
regression tests, selected directly by dotted name (2 tests), and the
non-self-referential subset of `test_capture.py` described under
"Verification" (18 tests).

**Finding 4 (repo-wide, disclosed but explicitly NOT fixed here --
scoped to index-generator only).** The shared `rec()` pattern above is
used by every other tool's `capture.sh` in this repository too, so any of
their recorded commands that contain a pipe have the exact same Finding-2
exit-masking problem. `pipe_scan.py` (added in this directory, read-only,
touches nothing outside it) counts them repo-wide:

```
python3 pipe_scan.py --repo-root ..
```

At the time this was regenerated, that command reported **49 transcript
files scanned, 12 piped command records across 10 tool directories**
(`claim-checker`, `commit-claim-auditor`, `dup-detector`, `exit-harness`,
`index-generator`, `payload-validator`, `queue-auditor`,
`report-freshness`, `transcript-schema`, `wallet-reconciler` -- see
`pipe_scan_report.json`'s `files_with_piped_records` for the exact
per-directory counts, produced by the command above; none of the nine
directories other than index-generator are modified by this change).

Honest note on that number: `index-generator` still shows **1**, not
`0`, in this scan -- and it is a genuine false positive of `pipe_scan.py`
itself, caught by actually running the scanner rather than assuming the
fix was complete. The flagged record is
`grep -n 'time\.time\|utcnow\|now()' indexgen.py; echo grep_exit=$?`
(Section 15 of `captured_output.txt`): the `|` there is a regex
alternation operator INSIDE a single-quoted `grep` pattern argument, not
a shell pipe -- there is no pipeline, no masking, and no second process
whose exit status could shadow the first. `pipe_scan.py` does naive
substring matching on the header text, not shell tokenisation, so it
cannot tell the two apart. This is documented as a limitation below
rather than "fixed" by making the header-matching pattern smarter, since
a correct fix would need real shell-quote-aware parsing and this
utility's whole point is to be a small, honest, read-only counter, not a
shell parser.

### Finding 4, current state -- and where the false positives went

The paragraph above describes the tree **as it was when that scan ran**
and is left unedited for that reason. The repo-wide propagation it
anticipated has since landed (`payload-validator`, `queue-auditor`,
`wallet-reconciler`, `commit-claim-auditor`, `dup-detector`,
`transcript-schema`, each in its own commit), so the numbers have moved.
Re-running the same command against the current tree reports **51
transcript files scanned, 545 command records, 11 flagged records across
9 directories**.

That number is still not the number a reader wants. `pipe_scan.py` counts
`|` **characters**, and only **4 of those 11 records contain a shell
pipeline at all**:

| Verdict | Count | Records |
|---|---|---|
| Real pipeline | **4** | `payload-validator` (1), `queue-auditor` (2), `wallet-reconciler` (1) -- all four are `cat`/`echo ... \| prog -` stdin-path records, and **all four are already captured under `set -o pipefail`** |
| Regex alternation inside a quoted `grep` pattern | 6 | `bundle-index`, `claim-checker`, `commit-claim-auditor`, `report-freshness`, and `index-generator` (2: its pre-existing `grep -n` record, plus the `pipe_classify.py --command` demonstration record added below, whose whole point is to be a `\|` that is not a pipe) |
| `\|\|` shell OR operator | 1 | `exit-harness` |

So the exit-masking exposure this section was written to track is
**closed**: every genuine pipeline in the repository's committed
transcripts now runs under `pipefail`, and the residual 6 are characters
that were never pipes.

`pipe_scan.py` is **not** changed to say so. Its raw output is quoted
verbatim in this directory's own committed transcript, and tuning a
disclosure tool until it reports zero is the failure mode this
repository's evidence standard exists to prevent. The classification is
added *beside* it instead:

```
python3 pipe_classify.py --repo-root ..
python3 pipe_classify.py --command 'grep -c "a|b" f.json'
```

`pipe_classify.py` walks exactly the same directories and the same
`=== $ ... ===` grammar, then labels every `|` as `pipeline`,
`or_operator`, `quoted` or `escaped` using POSIX quoting rules (single
quotes make everything literal including backslashes; double quotes
escape only ``$ ` " \`` and newline; `$(...)` and backticks are *not*
quoting, so a pipe inside a command substitution is a real pipeline).
Its committed output is `pipe_classification_report.json`, and
`test_pipe_classify.py` (72 tests) checks three separate things: the
grammar against hand-decidable strings, agreement with `pipe_scan.py` on
which records carry a `|` at all, and -- the part that matters -- the
labels against a real shell, by asserting that `set -o pipefail` can
change the exit status of a command labelled `pipeline` and cannot change
it for one labelled otherwise.

Two test counts appear for this suite, deliberately. `Ran 72 tests` is
the whole module, run against the finished tree; `Ran 70 tests` is what
`captured_output.txt` records, because the transcript's own run excludes
`TestCommittedReportIsFresh` (2 tests). That class byte-compares the
committed `pipe_classification_report.json` against a live rescan, and a
rescan taken from inside `capture.sh` reads `captured_output.txt`
mid-write, so it cannot agree with a report generated from the finished
file. This is the same self-reference exclusion `test_capture.py` already
uses, listed class by class in `capture.sh` rather than filtered, so the
skipped tests are named rather than silently dropped -- and they do run,
and must pass, in the full invocation below.

**The fix**, in `capture.sh`:
1. `rec()` runs every record under `bash -c 'set -o pipefail; ...'`
   instead of plain `sh -c "$*"`. `/bin/sh` on this box is `dash`, which
   does not support `set -o pipefail` at all (`sh -c 'set -o pipefail'`
   exits nonzero with "Illegal option") -- `capture.sh` therefore
   requires `bash` explicitly and exits 2 with a clear message if it is
   missing, rather than silently reverting to the unfixed behaviour.
2. No record whose command runs `unittest` is also piped through a
   filter. Where the old file spotlighted specific tests with
   `... | grep <name>`, `capture.sh` now runs exactly those tests
   directly via `unittest`'s own dotted test-selection syntax -- a real,
   complete, unpiped invocation with its own genuine summary line,
   verdict, and exit code.

## Relocation and determinism (the byte-identity proof)

`indexgen.py`'s own relocation invariance was already proven (Section 14
of `captured_output.txt`, `RelocationTests` in `test_indexgen.py`): its
JSON/Markdown OUTPUT never contains the absolute `--root` path, so two
runs against the same fixture at two different absolute locations produce
byte-identical output files. That is unaffected by this task.

This task adds a SECOND, harder relocation proof: that regenerating
`captured_output.txt` ITSELF -- by running `capture.sh` -- is
location-independent. This is hard honestly, not trivially: a transcript
records real durations -- a line of the shape `Ran <N> tests in
<duration>s` -- and that duration is never the same twice, by design (it
is a real wall-clock measurement of a real `unittest` run). No
illustrative duration is quoted here on purpose: any literal printed in
this README would be a number no particular run produced. There is a SECOND, less obvious volatile
field, found only by actually diffing real runs rather than assuming one
field was the only problem: two of `test_indexgen.py`'s own tests
(`test_unwritable_output_exit_2`, `test_unwritable_write_index_exit_2`)
print an error message containing a `tempfile.TemporaryDirectory`'s
random-suffixed path (e.g. `/tmp/indexgen_test_apnu6l9a/...`) to stderr,
and `capture.sh` faithfully captures that stderr -- so the transcript
embeds two more real, unique-per-run random strings, unrelated to this
task's fix, pre-existing in `test_indexgen.py`, unmodified here. Faking
byte-identity by hand-editing either field out of one copy would violate
"never fabricate a run"; treating the files as byte-identical without
accounting for them would be false.

**The honest approach taken here: normalise both volatile fields in the
COMPARISON, not in either transcript**, via `compare_relocation.py`:

```python
_RAN_LINE_RE = re.compile(r"(Ran \d+ tests? in )[0-9.]+s")
_TMP_DIR_RE = re.compile(r"/tmp/indexgen_test_[A-Za-z0-9_]+")

def normalise(text):
    text = _RAN_LINE_RE.sub(r"\1<DURATION>s", text)
    text = _TMP_DIR_RE.sub("/tmp/indexgen_test_<RANDOM>", text)
    return text
```

The test COUNT (`\d+` right after `Ran `) is deliberately left alone --
two files disagreeing on how many tests ran must still break
byte-identity after normalisation, and `test_capture.py`'s
`CompareRelocationTests` asserts exactly that (plus that an unrelated
real content difference also survives normalisation, so the function
cannot be accused of masking too much).

Three independent, real `bash capture.sh` runs were made -- twice in
place, once from a full copy of the repository relocated to a
differently-named absolute path (`/tmp/build_relocated_xyz`) -- and each
resulting `captured_output.txt` was copied out unmodified, then compared
with `python3 compare_relocation.py run1.txt run2.txt run3.txt` (shown
inline, not fenced: `compare_relocation.py` takes arbitrary
caller-supplied paths, so there is no single canonical invocation for
`capture.sh`'s own transcript to record).

Actual result from that exact run (all three files 51445 bytes):

| File | raw sha256 | normalised sha256 |
|---|---|---|
| run 1 (in place) | `0cc286cec9cfff4bdb60442966901baf110306012910b068cf9861b2288151ec` | `04279f8b6d2ec24108abfe7be611c826653d2620401a841b13fa1884d0be97de` |
| run 2 (in place) | `ed295368371bc71848372eb1ddd2ca59b8ea7619938f5fdfc55e6766aa2256e6` | `04279f8b6d2ec24108abfe7be611c826653d2620401a841b13fa1884d0be97de` |
| run 3 (relocated) | `90be2b0208fcfe2d87cebe1c789332f8f237399694b1e920a5206d7c826877ee` | `04279f8b6d2ec24108abfe7be611c826653d2620401a841b13fa1884d0be97de` |

`raw_byte_identical: false` (as expected -- the two volatile fields
really do differ every run), **`normalised_byte_identical: true`** across
all three, including the relocated one.

These numbers are re-measured whenever this transcript changes, rather
than left as a record of some earlier run. They were stale before this
commit: the table used to report 43715 bytes and normalised
`4a7fdda9...`, which no longer matched the committed transcript. A
relocation proof that quotes hashes of a file that has since changed
proves nothing, so it is re-run instead of carried forward.

**Why the whole repository is relocated, not just `index-generator/`:**
`capture.sh` embeds two self-check records that read sibling tool
directories by relative path (`../transcript-schema/validate_transcript.py`,
`../transcript-drift/driftcheck.py`). A bare copy of `index-generator/`
alone cannot run those two records (both would exit nonzero, and the
relocated transcript would then genuinely differ from the in-place ones
for a real reason, not a volatility artefact). This is itself one of the
limitations below, not glossed over.

## Verification

`transcript-schema/validate_transcript.py` (run from `transcript-schema/`,
against a single file) and `transcript-drift/driftcheck.py` (run from the
repository root, against all 49 tool directories, filtered to
`index-generator` here) were run BEFORE and AFTER this fix:

| Check | Before | After |
|---|---|---|
| `validate_transcript.py ../index-generator/captured_output.txt` | `status: invalid`; 1x `TRANSCRIPT_RECORD_MISSING_RAN_LINE`, 1x `TRANSCRIPT_RECORD_MISSING_VERDICT` (both on the `grep stale_catalogued` record) | `status: valid`; **zero findings** |
| `driftcheck.py --root .` (index-generator's findings only) | `EXIT_CODE_MISMATCH` (exit 0/1 unacknowledged by the README's exit-code prose), `README_COMMAND_NOT_IN_TRANSCRIPT` (2 example commands that didn't literally match any transcript header), `TEST_COUNT_MISMATCH` (README claimed `138`; transcript recorded `138` AND `140` -- Finding 3, visible here as a real cross-check failure, not just a manual read) | **zero findings** |

Reproduce exactly:

```
cd transcript-schema && python3 validate_transcript.py ../index-generator/captured_output.txt
```

and, from the repository root: `python3 transcript-drift/driftcheck.py --root .`
(shown inline, not fenced, for the same reason as the `test_capture -v`
command above -- it inspects the whole repository from outside this
directory, which is a real, correct way to run it, but not literally one
of `capture.sh`'s own relative-path records).

## Limitations

1. **Test-count extraction is a small set of regex heuristics, not real
   comprehension.** It only recognizes English phrasings like
   `"42 tests"`, `"Tests: 42"`, `"Ran 42 tests"`, and `"42/42 tests"`.  A
   README that states its count some other way (spelled-out numbers,
   `"42 assertions"`, non-English text, a count embedded in a badge image
   alt-text) will not be recognized; `extract_claimed_test_count` returns
   `None` and `NO_CLAIMED_TEST_COUNT` fires even though a human reader
   could find the number a few words away.
2. **`ROOT_README_COUNT_DRIFT`'s test-total check only sums tools with a
   *known* per-tool count**, silently excluding tools whose own README
   doesn't state a count. If most tools have an unknown count, this sum
   understates the true total, so the check can flag a root README that is
   actually correct, or fail to flag one that is actually wrong, depending
   on how the unknowns happen to net out. Only the tool-count half of this
   check (claimed N tools vs. actually discovered N) is unconditionally
   reliable.
3. **The Entrypoint(s)/Test Module(s) index columns are comma-joined and
   comma-split, with no comma-escaping.** A real filename containing a
   literal comma (legal on Linux, if unusual for a `.py` module) would be
   incorrectly split into two names when a hand-edited `--check-index`
   file is reparsed, and could produce a spurious `INDEX_DRIFT` or merge
   two unrelated names. (The Description column has an analogous escaping
   mechanism for `|` plus a disambiguation guard for the `_(none)_`
   sentinel -- see "Bug found" above -- the list columns do not have an
   equivalent guard.) *This item used to say "hand-edited **or foreign**";
   the word "foreign" no longer applies, because a file with no index
   table is no longer reparsed at all -- see "Second bug" above.*
4. **The index header is matched on its stripped cell text, not on its
   bytes -- and a table whose header does not match is not an index.** A
   column-aligned or trailing-space variant is recognised; a header that
   renames a column is not, and its rows are simply not index rows. The
   symptom, if that happens, is `NO_INDEX_TABLE` plus one `INDEX_DRIFT`
   "(added)" per tool. That is deliberate -- guessing which
   five-column table is an index is what caused the crash this delivery
   repairs -- but it is a real narrowing and it is stated here rather than
   left to be discovered.
5. **A markdown table ends at a blank line, so an index row below one is
   out of the table.** Such a row is reported as `MALFORMED_INDEX_ROW`
   rather than parsed, and only when the file contains an index table
   somewhere. It is not merged back in.
6. **`MALFORMED_INDEX_ROW` locations sort as strings, so line 10 orders
   before line 9.** The ordering is total and deterministic, which is what
   the determinism contract requires, but it is not numeric.
7. **Discovery is exactly one level deep and never recurses.** A tool
   whose real entrypoint, `README.md`, or `captured_output.txt` lives in a
   nested subdirectory rather than directly inside its top-level tool
   directory is invisible to indexgen: the directory won't even be
   recognized as a "tool" (no top-level `*.py` of its own), so no
   `MISSING_*` finding is produced either -- it is simply absent from the
   index, with no diagnostic pointing at why.
8. **`capture.sh`'s two self-check records (`validate_transcript.py`,
   `driftcheck.py` run against this very file) cannot check the FINISHED
   file, because the record that would report the result is itself part
   of the file being written.** The `validate_transcript.py` record works
   around this honestly by checking a `cp`'d snapshot taken immediately
   before it runs (so it sees a real, complete-up-to-that-point file, not
   a hand-edited one) -- but that snapshot still excludes everything from
   the snapshot step onward. The `driftcheck.py` record does not even get
   that: it reads the literal on-disk `captured_output.txt` by name, so
   it necessarily sees a mid-write file and its result inside the
   transcript should be read as illustrative, not authoritative. The
   authoritative post-completion numbers are the ones in "Verification"
   above, from a separate run made after `capture.sh` exits -- this
   limitation is exactly why that separate run is necessary and is not
   redundant with the embedded ones.
9. **The relocation proof requires relocating the whole repository, not
   just this directory**, because of the same two self-check records:
   they reference `../transcript-schema` and `../transcript-drift` by
   relative path. Given only a standalone copy of `index-generator/`
   (no siblings), `bash capture.sh` still runs and produces a transcript,
   but the two self-check records genuinely fail (nonzero exit, real
   Python tracebacks about missing files) rather than being skipped --
   the relocated transcript would then legitimately differ from the
   in-place ones, not because relocation broke anything indexgen-related,
   but because the evidence-generation script has a real, undisclosed-
   until-now dependency on sibling tooling.
10. **`pipe_scan.py` (Finding 4) does substring matching on header text
   (`"|" in command`), not shell-quote-aware parsing, so it cannot tell a
   real pipe operator from a literal `|` inside a quoted argument.**
   Observed directly, not hypothesised: this directory's OWN regenerated
   `captured_output.txt` still triggers one false-positive hit --
   `grep -n 'time\.time\|utcnow\|now()' indexgen.py; echo grep_exit=$?`
   -- where the `|` is a `grep` alternation operator inside a
   single-quoted pattern, not a pipeline; there is no second process and
   nothing is masked. The repo-wide "10 directories, 12 records" count in
   "Finding 4" is therefore an upper bound that can include such false
   positives in either direction: it also cannot tell a genuinely harmless
   pipe (like this directory's OTHER pattern,
   `grep -c foo bar.json; echo grep_exit=$?`, which explicitly captures
   the inner exit code into printed text and has no masking problem at
   all) apart from one that silently swallows a real test failure.
   Distinguishing false positives from real Finding-2-shaped bugs requires
   reading each flagged record's command by hand, which this task
   deliberately did not do for the nine directories outside its scope.

   **Update: this limitation is now measured, not just stated.** Every
   flagged record was read by hand in a later delivery
   (`PIPEFAIL_MASKING_FIX.md` §1), and the by-hand verdicts have since
   been made mechanical and testable in `pipe_classify.py` -- see
   "Finding 4, current state" above. Against the current tree the
   over-count is **7 of 11 flagged records**, i.e. `pipe_scan.py`'s raw
   number is 2.75x the number of records that actually contain a
   pipeline.
   `pipe_scan.py` itself is still deliberately left naive, and this
   limitation still describes it accurately; what has changed is that a
   reader no longer has to take "upper bound" on trust, because the
   companion tool says by how much and shows its work per record.
   The companion tool has limitations of its own, stated in its module
   docstring: it does not recognise `#` comments or here-documents, both
   absent from every committed record, and both of which would make it
   *over*-report a pipeline rather than hide one.

   A note on the wording of the paragraph above, because it is evidence
   about a different tool. An earlier draft opened that paragraph by
   naming the companion script and then saying it "carries" limitations
   of its own. `claim-crosscheck` flagged the sentence as a PRESENCE
   discrepancy: that verb, sitting beside a path-shaped token, is its cue
   for "this README claims the report lists that file". It is the
   use-versus-mention false positive already documented in
   `claim-crosscheck/README.md`, reproduced here by accident.

   The first attempt to *explain* the flag tripped it a second time, for
   the obvious reason -- the explanation quoted the offending phrase
   verbatim, so the filename and the verb were adjacent again. That is
   worth recording as a property of the detector rather than a nuisance:
   with a substring-adjacency rule, the sentence describing a false
   positive is itself a false positive, and the only ways out are to
   loosen the rule, to suppress the record, or to write around it. This
   README writes around it -- the script is named in one clause and the
   verb appears in another -- and says so, which is the option that
   leaves both the detector and the disclosure intact.

## Notes on design choices

- A directory counts as a "tool" iff it has at least one top-level
  `*.py` file that is not `test_*.py` (per the spec); this intentionally
  includes any auxiliary `*.py` files (e.g. a stray `conftest.py`) as an
  "entrypoint" and may flag `ENTRYPOINT_TEST_MISMATCH` for them -- this
  is a direct, literal consequence of the discovery rule as specified, not
  a bug.
- Findings from `--check-index` compare *rows* (tool -> description,
  entrypoints, test modules, claimed count) between the checked file and
  the freshly computed index; the totals line is not compared.
