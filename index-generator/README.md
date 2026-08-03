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
test_indexgen.py      test suite (138 tests)
README.md             this file
fixture_repo/         6-tool fixture repo used for the demo + proof runs
root_readme_sample.md sample root README used to demonstrate ROOT_README_COUNT_DRIFT
captured_output.txt   real transcript: test run, CLI invocations, determinism proof
sample_report.json    sample JSON report (fixture_repo, findings present)
sample_report_clean.json  sample JSON report (a clean single-tool repo, zero findings)
sample_INDEX.md       sample generated INDEX.md (from fixture_repo)
```

## Exact rerun commands

Run the test suite:

```
python3 -m unittest test_indexgen -v
```

Generate an index + report for a repo:

```
python3 indexgen.py --root fixture_repo \
    --write-index /tmp/out_INDEX.md \
    --root-readme root_readme_sample.md \
    -o /tmp/out_report.json
```

Check a previously-written index for drift (without rewriting it):

```
python3 indexgen.py --root fixture_repo --check-index /tmp/out_INDEX.md -o /tmp/out_report2.json
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

| Code | Meaning |
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
