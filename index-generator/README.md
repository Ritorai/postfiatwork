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
test_indexgen.py      test suite (140 tests)
test_capture.py       regression tests for the transcript-generation path (capture.sh)
capture.sh            regenerates captured_output.txt; every record is a real run
pipe_scan.py          Finding 4 disclosure utility (repo-wide, read-only)
compare_relocation.py normalises + hashes captured_output.txt for the relocation proof
repro_findings.txt    live reproduction of Findings 1-3 against the ORIGINAL transcript
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
records real durations, e.g. `Ran 140 tests in 0.268s`, and that number
is never the same twice, by design (it is a real wall-clock measurement
of a real `unittest` run). There is a SECOND, less obvious volatile
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
place, once from a full copy of the repository relocated to
`/tmp/build_7/relocated_xyz` (a differently-named absolute path) -- and
each resulting `captured_output.txt` was copied out unmodified, then
compared with `python3 compare_relocation.py run1.txt run2.txt run3.txt`
(shown inline, not fenced: `compare_relocation.py` takes arbitrary
caller-supplied paths, so there is no single canonical invocation for
`capture.sh`'s own transcript to record).

Actual result from that exact run (all three files 43715 bytes):

| File | raw sha256 | normalised sha256 |
|---|---|---|
| run 1 (in place) | `aa80f14593089eb17c23f353c5987d5636724e3122f292bc9f72dd1713abdb1e` | `4a7fdda93325e0005ed95df795b8560790c388af57cbbb43c46218eedea3be6d` |
| run 2 (in place) | `8236db3f411288b086d248892d8f278205fd0e077733e881b70c1f34a0e60f08` | `4a7fdda93325e0005ed95df795b8560790c388af57cbbb43c46218eedea3be6d` |
| run 3 (relocated) | `c237da3b78210880190f9afdf99b26028ca8ca3d643880d7e65e539a0639ec10` | `4a7fdda93325e0005ed95df795b8560790c388af57cbbb43c46218eedea3be6d` |

`raw_byte_identical: false` (as expected -- the two volatile fields
really do differ every run), **`normalised_byte_identical: true`** across
all three, including the relocated one. `diff`ing the three raw files
directly (not shown here for length) confirms the ONLY lines that differ
are `Ran N tests in <duration>s` lines and lines containing an
`indexgen_test_<random>` path -- nothing else, on any of the three
comparisons.

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
   incorrectly split into two names when a hand-edited or foreign
   `--check-index` file is reparsed, and could produce a spurious
   `INDEX_DRIFT` or merge two unrelated names. (The Description column has
   an analogous escaping mechanism for `|` plus a disambiguation guard for
   the `_(none)_` sentinel -- see "Bug found" above -- the list columns do
   not have an equivalent guard.)
4. **Discovery is exactly one level deep and never recurses.** A tool
   whose real entrypoint, `README.md`, or `captured_output.txt` lives in a
   nested subdirectory rather than directly inside its top-level tool
   directory is invisible to indexgen: the directory won't even be
   recognized as a "tool" (no top-level `*.py` of its own), so no
   `MISSING_*` finding is produced either -- it is simply absent from the
   index, with no diagnostic pointing at why.
5. **`capture.sh`'s two self-check records (`validate_transcript.py`,
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
6. **The relocation proof requires relocating the whole repository, not
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
7. **`pipe_scan.py` (Finding 4) does substring matching on header text
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
