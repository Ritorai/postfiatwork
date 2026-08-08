# readme-index

The root `README.md` of this repository says **13 tools / 476 tests**. It has
said that since it was written, and it was correct then: 476 is exactly the sum
of its own 13-row table. The repository now has **44 tool directories**, every
one of them with a `README.md`.

`readmeindex.py` derives the index from the tree instead of from memory. It
reads each tool README's title and claimed test count, reconciles those against
the root README's table, and regenerates that table. Counts come only from
claims it can actually parse; anything it cannot parse is reported as
`ambiguous` or `not stated` rather than guessed or zeroed.

**Repository-wide result: 44 tools, 37 with a derivable claim totalling 4,175
tests, 1 ambiguous, 6 not stated.** That count includes this tool, whose own
README claims 72. The figures are pinned to the tree as committed here; the
scan the numbers were first derived from saw 43 tools and 4,103 tests, before
this directory existed. A reviewer with a clone gets different figures on
purpose: `--root .` sees the 51 tool directories the tree has today and totals
5,115. The corpus is the pinned 44-tool snapshot limitation 3 describes, and
the headline above is that snapshot's number, not today's.

That aggregate moved by 31 in the commit that added atomic writes below, and
the move is the tool working rather than a number drifting. This tool's suite
went from 41 tests to 72, so `corpus.tsv`'s rows for *this* directory were
re-extracted from the edited README, `index_report.json` re-derived and
`root_readme_after.md` regenerated. Leaving the corpus alone would have left a
committed extraction that disagrees with the README it was extracted from,
which is the exact defect this directory exists to detect. No other tool's
rows were touched: the corpus stays the pinned 44-tool snapshot it says it is.

## Requirements

Python 3 standard library only: `argparse`, `json`, `os`, `re`, `stat`,
`sys`, `tempfile`. No third-party packages, no network. A test asserts the
import list, comparing a sorted list rather than a set so its failure message
cannot reorder itself between runs. `stat` and `tempfile` arrived with atomic
writes, below, and are the only imports added since the first version.

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
itself. Only one of those lines — its own bold summary, which also reports a
passing run — states its suite total. Rule 1 picks it; a flat "collect every
number" rule reports the file as hopeless.

This bit back while the file you are reading was being written. An earlier draft
quoted that summary line verbatim, in full, as an example — and the tool then
classified *this* README as `ambiguous`, because two lines now carried a bold
count plus a success marker and it could not tell the illustration from the
claim. Reproducing the exact shape a rule matches, inside prose explaining that
rule, is the same trap `transcript-drift` falls into. The example above is
therefore described rather than quoted.

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
to 249 rows, **no verdict changed and the total was still 4,103**. The old corpus
happened to be sufficient. It was not *provably* sufficient, and "happened to be
right" is not a property worth shipping.

## A third defect: a failed write destroyed the previous output

Both output paths — `--rewrite` and `-o` — used to be written the obvious way:

```
with open(path, "w", encoding="utf-8", newline="\n") as fh:
    fh.write(text)
```

`open(path, "w")` truncates the destination as its very first act, before a
single byte of the replacement exists. So a write that fails part-way — a full
disk, an exhausted quota, a process killed mid-write — leaves a *prefix* of the
new output where the old output used to be.

For `-o` that costs a regenerable report. For `--rewrite` it is worse than
that: `--rewrite` is aimed at a repository's root `README.md`, a
hand-maintained file, and the documented invocation aims it at the same file
`--root-readme` just read. A half-written README is not a failed run, it is a
damaged working tree, and the exit code that reports the failure arrives after
the damage is done.

`write_text_atomically` replaces both writes. The text goes to a sibling temp
file in the destination's own directory and `os.replace` moves it onto the
destination in one step; anything that raises before the replace unlinks the
temp file and leaves the destination with its original bytes, mtime and inode.
The sibling is not incidental — `os.replace` is atomic only within one
filesystem, so a temp file under the system temp directory would reintroduce a
copy step that can fail half-way.

Six details are there to keep this a fix and not a behaviour change:

- **Modes.** `mkstemp` creates `0600`. Replacing a `0644` file with it would
  silently narrow the file's permissions, so an existing destination's mode is
  copied onto the temp file first, and a new destination gets `0666 & ~umask`
  — what `open()` would have produced. `test_a_new_destination_gets_the_mode_plain_open_would_have_given`
  compares against a file created by a real `open()` under the same umask
  rather than against a hard-coded number.
- **Symlinks.** `open(path, "w")` follows a symlink and writes through it.
  `os.replace` onto the link would swap it for a regular file, so the path is
  resolved first.
- **`BaseException`, not `Exception`.** A `KeyboardInterrupt` between `mkstemp`
  and `os.replace` would otherwise leave a `.readmeindex-*.tmp` behind — the
  kind of litter this repository's own scanners report.
- **Destinations that are not regular files.** `-o /dev/null` and
  `-o /dev/stdout` are real ways to say "discard the report" and "pipe the
  report", and `os.replace` onto a device or a pipe destroys the node — running
  as root it would succeed, which is worse than failing. Anything that exists
  and is not a regular file is written through instead, so `/dev/null` stays
  `/dev/null` and a directory still raises `IsADirectoryError`. The tests build
  a private character device, a FIFO and a symlink into `/proc/self/fd` in a
  temp directory rather than aiming at the real `/dev/null`, so a regression
  damages a fixture; each skips where the platform will not create its shape.
- **A destination that cannot be classified at all.** `os.stat` can fail for
  reasons other than "not there": a symlink loop is `ELOOP`, a trailing slash
  on a regular file is `ENOTDIR`. Treating every `OSError` as "the destination
  is new" sends those into `mkstemp` and `os.replace` — and a symlink loop
  really did get quietly replaced by a regular file that way. Only
  `FileNotFoundError` means new; every other `OSError`, and any path with a
  trailing separator, is handed to the direct write, which raises exactly what
  the removed code raised. The tests assert parity with a live `open(path,
  "w")` rather than a hard-coded errno.
- **The order of that check and the symlink resolution.** The regular-file
  question is asked *before* `realpath`, on the path as given. Asking it after
  looks identical and is not: `os.path.realpath("/dev/stdout")` walks to
  `/proc/<pid>/fd/1` and then to a `pipe:[…]` name that does not exist, so an
  `os.path.exists` test on the resolved path sees a phantom, decides the
  destination is new, and tries to create a temp file inside a pipe. That was
  the shape of a real regression in the first version of this fix; it is now
  pinned by `test_a_symlink_into_proc_fd_is_written_through`, and the FIFO test
  alone does not catch it because `realpath` on a FIFO is the FIFO.

**How this is proved rather than asserted.** Seven of the thirty-one new
tests drive a real failure rather than a mocked one: a proxy handle writes the
first 40 characters to the real file, flushes them so they are genuinely on
disk, and then raises `OSError(ENOSPC)` — or, in the seventh,
`KeyboardInterrupt`. Committing the prefix is the point: a proxy that swallowed
the bytes would make the old direct write look innocent. All seven assert the
injector actually fired, so none of them can pass by never reaching the write.

Every guard listed above is pinned by a test that goes red when only that guard
is removed. That was checked by removing each one in turn against a copy of the
tree, not assumed: the `dir=` argument to `mkstemp`, the `os.close`, the
`os.chmod`, the `os.unlink`, `BaseException` instead of `Exception`, the
`realpath`, the not-a-regular-file branch, and the *position* of that branch
before the `realpath` rather than after it. Three of those survived the first
version of this suite and got tests only because the removal experiment found
them.

`ATOMIC_WRITE_EVIDENCE.txt` records the same test file run against the pre-fix
source and against the fixed source, in that order. The pre-fix source is read
out of Git — `git show abb45e1:readme-index/readmeindex.py` — rather than kept
as a second copy in the tree. The pre-fix run reports
`FAILED (failures=4, errors=26)`: three of the failures are destinations left
truncated where the previous bytes should still be, including the in-place
`--rewrite` case; the fourth is the import-list assertion; the twenty-six
errors are the tests that name `write_text_atomically` or `destination_mode`,
neither of which exists there. That is 29 of the 31 new tests. The other two
pin the success path, which the pre-fix source already got right, and the
comment at the head of the new test block says so rather than claiming all
thirty-one fail.

`demo_partial_write.py` shows the same thing without a test framework, and the
evidence file runs it against both sources. Against the pre-fix source it
prints a destination of 40 bytes and `destination unchanged: False`; against
the fixed source, 282 bytes and `True`. It drives `main()` rather than the
helper, because the pre-fix source has no helper to call.

**What this does not do.** It is atomicity, not durability, and not a
transaction. See limitations 6 and 7.

One number this section moves that is worth naming: `env-leak-scanner`'s
repo-wide scan goes from 925 confirmed to 936, all eleven in the
`absolute_path` category and all eleven in the prose above — `/dev/null`,
`/dev/stdout`, `/proc/self/fd`, `/proc/<pid>/fd/1`. They are the subject
matter of the paragraphs that mention them rather than a path leaked out of
the machine this was written on, which is the distinction that tool's own
README draws; the repository has 128 such lines already and the scan is not a
gate. No new `temp_directory`, `home_directory`, `hostname` or `username`
candidate appears.

## What corroborates the extraction

The reconciliation run reports **zero `count_differs`**. All 13 tools already in
the root README table — 95, 63, 39, 36, 34, 32, 29, 29, 27, 26, 26, 23, 17 —
were re-derived independently from their own READMEs and every one matched. The
32 differences are 31 tools absent from the index plus 1 aggregate difference.

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

Digests for `index_report.json` and `corpus.tsv` are recorded in
`captured_output.txt` rather than here. They cannot live in this file: the
corpus is built from every tool README including this one, so writing a corpus
digest into this README changes the corpus and invalidates the digest in the
same edit.

## 7 limitations

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
   of 4,175 therefore *excludes* a tool that has a stated count, and is a lower
   bound rather than a total.

3. **`--corpus` mode classifies extracted candidate lines, not 44 full READMEs.**
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

5. **Only the table is regenerated, and the root README says things outside it.**
   The committed root README also carried "Thirteen standalone command-line
   tools" and "476 tests across 13 tools, all passing" as prose, well outside
   the `## The tools` section this tool rewrites. Regenerating the table alone
   left the file self-contradictory, so those two sentences were corrected by
   hand in the same commit. The tool should detect and rewrite prose claims of
   that shape; it currently does not, and a reviewer should treat those two
   lines as hand-maintained. ("all passing" was dropped rather than restated:
   these are claims parsed from documentation, and no run of all 44 suites
   backs it.)

6. **Atomic is not durable, and no `fsync` is issued.** `write_text_atomically`
   guarantees that the destination holds either the whole old output or the
   whole new output, never a prefix of the new one. It does not guarantee the
   new bytes reach the platter: a power loss shortly after a successful run can
   still lose them. Closing that honestly means `fsync` on the temp file *and*
   on the containing directory, and it was left out rather than done half-way
   — an `fsync` on the file alone would let this section claim a durability
   it does not have.

7. **Each output is atomic on its own; the pair is not a transaction.**
   `--rewrite` and `-o` are two separate replaces, and the rewrite happens
   first. If the report write fails afterwards, the README has already been
   replaced and the run still exits non-zero. Three further consequences of
   replacing in place: the destination's *directory* must now be writable,
   where the old code needed only the file itself to be writable; any hard link
   to the destination keeps the old content instead of seeing the new; and the
   destination is a *new inode*, so it carries the running user's ownership
   rather than the original owner's, along with default ACLs and xattrs. That
   last one bites under `sudo` — `sudo python3 readmeindex.py --root . \
   --root-readme README.md --rewrite README.md` now leaves the README owned by
   root, where the old direct write left the owner alone. Permission *bits* are
   preserved deliberately; ownership is not, and copying it would need
   privileges the tool has no business assuming. All four are inherent to
   atomic replacement rather than oversights, and none of them can leave a
   truncated file behind.

   Two smaller consequences in the other direction. A destination the caller
   cannot write is now *replaced* rather than refused: the old direct write
   raised `PermissionError` on a `0444` file, and replacing it only needs the
   directory. And pointing `--rewrite` and `-o` at the same path no longer
   interleaves two partial writes — it produces the report, cleanly, because
   the report is written second and each write is whole. Neither shape loses
   the previous output to a half-written file, which is what this change is
   for, but neither is what the pre-fix code did.

## Files

| File | What it is |
|---|---|
| `readmeindex.py` | the CLI |
| `test_readmeindex.py` | 72 tests |
| `corpus.tsv` | extracted candidate lines, so the run is reproducible without a clone |
| `index_report.json` | the committed report |
| `root_readme_before.md` | the root README as committed, for the before/after pair |
| `root_readme_after.md` | the regenerated root README |
| `captured_output.txt` | verbatim verification transcript |
| `capture.sh` | regenerates that transcript |
| `ATOMIC_WRITE_EVIDENCE.txt` | the atomic-write suite run against the pre-fix and fixed source |
| `mk_atomic_evidence.sh` | regenerates that evidence file (not named `capture.sh` on purpose — it reads Git history) |
| `demo_partial_write.py` | one partial write, driven through `main()`, reported in five lines |

## Tests

**72 tests, `OK`, exit 0.** CPython 3.11.15, Linux x86_64.

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

`captured_output.txt` also runs the atomic-write subset on its own, which is
`Ran 31 tests` -- a subset count, not a second claim about the suite total.

The thirty-one added in the atomic-write commit cover the helper directly
(new file, overwrite, failed write, temp-file cleanup on both paths, an
existing mode preserved, a new file's mode matching a real `open()` under the
same umask, a symlinked destination written through, a directory destination
still raising `IsADirectoryError`, a character device / a FIFO / a symlink into
`/proc/self/fd` written through rather than replaced, the temp file being a
sibling of the destination, no file-descriptor leak across repeated writes, an
interrupted write still cleaning up, a symlink loop and a trailing separator
raising exactly what the direct write raised, newline translation and UTF-8
round-trip), the umask read and its restoration, and the same guarantee
through `main()` for `--rewrite`, for `-o`, and for the in-place case where
`--rewrite` names the file `--root-readme` just read.
