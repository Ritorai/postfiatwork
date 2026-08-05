# readme-index

The root `README.md` of this repository says **13 tools / 476 tests**. It has
said that since it was written, and it was correct then: 476 is exactly the sum
of its own 13-row table. The repository now has **43 tool directories**, every
one of them with a `README.md`.

`readmeindex.py` derives the index from the tree instead of from memory. It
reads each tool README's title and claimed test count, reconciles those against
the root README's table, and regenerates that table. Counts come only from
claims it can actually parse; anything it cannot parse is reported as
`ambiguous` or `not stated` rather than guessed or zeroed.

**Repository-wide result: 43 tools, 36 with a derivable claim totalling 4,103
tests, 1 ambiguous, 6 not stated.**

## Requirements

Python 3 standard library only: `argparse`, `json`, `os`, `re`, `sys`. No
third-party packages, no network. A test asserts the import list.

## Usage

```
python3 readmeindex.py --root .                                        # with a clone
python3 readmeindex.py --corpus corpus.tsv                             # without one
python3 readmeindex.py --root . --root-readme README.md -o report.json
python3 readmeindex.py --root . --root-readme README.md --rewrite README.new.md
```

| Exit | Meaning |
|---|---|
| `0` | the root README index matches the derived index |
| `1` | differences were found |
| `2` | setup error: bad `--root`/`--corpus`, malformed corpus, missing heading, `--rewrite` without `--root-readme` |

`--root` and `--corpus` are mutually exclusive and one is required.

## How a claimed count is decided

Three rules, applied in priority order. Every decision records the line numbers
that produced it in the report's `evidence` array, so a reviewer can check a
call without rerunning anything.

1. **`strong_summary`** — a bold `**N tests` or a `Ran N tests` on a line that
   *also* carries `OK`, `all passing`, or `exit 0`. This is the line an author
   writes to state the suite total, so it outranks everything else.
2. **`self_report`** — `test_*.py … N … tests`, the tool naming its own suite.
3. **`bare`** — a bare count anywhere, allowing up to two words between the
   number and `tests`.

One distinct value at the highest rule that fired is a `claim`. Several
distinct values is `ambiguous`. Nothing at all is `missing`.

The priority order is not cosmetic. `transcript-drift` mentions five different
numbers — `Ran 26 tests`, `174 tests`, `Ran 41 tests`, `3`, `57` — because it is
a tool *about* test-count drift and quotes other tools' claims while explaining
itself. Only one of those lines, `**57 tests, OK, exit 0.**`, is its own. Rule 1
picks it; a flat "collect every number" rule reports the file as hopeless.

## Two bugs this tool had, both found by running it

Neither was found by reading the code.

**Summing fixtures into the total.** The first version treated distinct
`test_*.py` self-reports as additive, which is right for `regression-checker`
(131 + 8 + 35 = 174, and its README says so). Run against the whole repository,
that rule reported `commit-claim-auditor` as **157** — because that README names
a bundled *fixture*, `test_example.py` with 3 tests, alongside its real 154-test
suite. 157 appears nowhere in that file. It is a confident, wrong, invented
number of exactly the kind this repository exists to catch.

The fix is not a longer regex. Several distinct test files with no stated total
is now `ambiguous`, and tools that genuinely have several suites state the total
themselves and are caught by rule 1 first. `TestFixtureSumRegression` pins both
halves.

**The prefilter was not a superset.** `--corpus` mode works on pre-extracted
candidate lines, which is only sound if the prefilter can never drop a line a
rule would match. `TestPrefilterIsSuperset` failed on
`` - `test_claimhist.py` - 154 unit/integration tests ``: the window between the
digit and `tests` was too narrow. Sixteen lines repository-wide were being
dropped.

Honest outcome: after widening the window and regenerating the corpus from 233
to 249 rows, **no verdict changed and the total is still 4,103**. The old corpus
happened to be sufficient. It was not *provably* sufficient, and "happened to be
right" is not a property worth shipping.

## What corroborates the extraction

The reconciliation run reports **zero `count_differs`**. All 13 tools already in
the root README table — 95, 63, 39, 36, 34, 32, 29, 29, 27, 26, 26, 23, 17 —
were re-derived independently from their own READMEs and every one matched. The
31 differences are 30 tools absent from the index plus 1 aggregate difference.

## Relationship to `index-generator`

`index-generator` already exists in this repository and overlaps substantially:
it has `extract_claimed_test_count`, `discover_tools`, `compute_totals`,
`render_index`, `parse_index`, `diff_index` and `check_root_readme_drift`, and a
CLI with `--root`, `--write-index`, `--check-index`, `--root-readme` and `-o`.
Anyone reviewing this directory should know that before reading further.

Two things distinguish this tool rather than duplicate it.

`index-generator/README.md` documents its own extraction as "a small set of
regex heuristics" covering `"42 tests"`, `"Tests: 42"`, `"Ran 42 tests"` and
`"42/42 tests"`. That set has no case for a word between the number and
`tests`, which is `bundle-index` (170 unit tests), `claim-checker` (224 unit
tests), `commit-claim-auditor` (154 unit/integration tests) and
`weak-assertion-scanner` (202 unittest tests). The rules above are offered as
the concrete fix for a gap that tool already discloses.

Functionally, `index-generator` writes a separate `INDEX.md` and cross-checks
the root README for drift; it does not regenerate the root README's own
`## The tools` table in place. That regeneration, and its idempotence, is what
this tool adds.

## Determinism and the before/after pair

Output is canonical JSON: `sort_keys=True`, two-space indent, trailing newline.
No timestamps, no durations, no absolute paths.

`captured_output.txt` records the pair that matters. Reconciling against the
committed root README exits `1`. Reconciling against the regenerated README
exits `0`. Rewriting the regenerated README again produces a byte-identical
file (`cmp` clean), so the regeneration is a fixed point rather than a file that
drifts on every run.

```
sha256(index_report.json) = 63942328ecc6e3f0a07c2074ece5a0eb10408ada52d9abb11827c64cdd4d96ce
sha256(corpus.tsv)        = 8576ae76d33f547b1a0f0d8be74598d91da817e0b475748cdb57826cfb1029c1
```

## 4 limitations

1. **A `+` between words defeats the count rules, and it costs a real answer.**
   `exit-harness/README.md` line 283 says
   `` * `test_exitharness.py` -- 126 unit + CLI-integration tests. `` The rules
   allow only `[A-Za-z/]` words between the number and `tests`, so `+` stops the
   match and `exit-harness` is reported as `not stated` when the README plainly
   states 126. This was left unfixed deliberately: every widening of that
   character class also widens what counts as a claim, and the honest failure is
   easier to review than a rule loose enough to swallow prose.

2. **`ambiguous` is a refusal, not an answer.** `commit-claim-auditor` really
   does claim 154; a human reads its README and knows which line is the suite
   and which is the fixture. This tool cannot tell those apart without guessing,
   so it reports `[3, 154]` with both line numbers and declines. The aggregate
   of 4,103 therefore *excludes* a tool that has a stated count, and is a lower
   bound rather than a total.

3. **`--corpus` mode classifies 249 extracted lines, not 43 full READMEs.**
   Soundness rests entirely on the prefilter being a superset of the rules,
   which is proven for the *current* rule set by `TestPrefilterIsSuperset` plus
   a companion test that every fixture actually fires a rule so the first cannot
   pass vacuously. Add a rule that matches something the prefilter does not, and
   the corpus silently becomes incomplete — which is exactly what happened once
   already. A reviewer with a clone should run `--root .` and ignore the corpus.

4. **Titles are the first `# ` heading, and some are filenames.** The
   regenerated table shows `consolidate.py`, `contradict.py`, `claimcheck` and
   `snapdiff.py` as titles, because that is what those READMEs put in their H1.
   The tool reports what is written rather than inventing prose, so the
   regenerated index is less readable than the hand-written 13-row table it
   replaces. That is a deliberate trade of polish for verifiability.

## Files

| File | What it is |
|---|---|
| `readmeindex.py` | the CLI |
| `test_readmeindex.py` | 41 tests |
| `corpus.tsv` | 249 extracted candidate lines, so the run is reproducible without a clone |
| `index_report.json` | the committed report |
| `root_readme_before.md` | the root README as committed, for the before/after pair |
| `root_readme_after.md` | the regenerated root README |
| `captured_output.txt` | verbatim verification transcript |
| `capture.sh` | regenerates that transcript |

## Tests

**41 tests, `OK`, exit 0.** CPython 3.11.15, Linux x86_64.

```
python3 -m unittest test_readmeindex
```

Covering all three extraction rules and their priority, the fixture-sum
regression, the prefilter superset proof and its non-vacuity companion,
`--root` and `--corpus` agreeing on the same synthetic tree, discovery
(including skipping dot-directories and directories with no README), index
parsing and the missing/extra/count/aggregate difference kinds, totals counting
only derivable claims, idempotent regeneration, canonical-JSON stability, and
every exit-2 setup error.
