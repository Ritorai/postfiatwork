# commit-claim-auditor (`claimhist.py`)

## Purpose

Audits *historical claims* embedded in `README.md` and `captured_output.txt`
files against the current state of the artifacts they describe. Two claim
families only:

- **SHA256_CLAIM** — "this file's SHA-256 digest is `<64 hex chars>`."
- **TESTCOUNT_CLAIM** — "this test suite has `N` tests" (e.g. from a
  `Ran N tests in ...` unittest summary line).

The point: a refactor changes a report file or removes a test, and the
README still quotes the old hash / old count, and nobody notices. This tool
recomputes both kinds of claims against the artifacts on disk (and, for
test counts, optionally against a real test run) and tells you which
claims are stale.

This tool is intentionally narrow. It does **not** validate CLI flags,
exit codes, usage text, or any other general README/CLI correctness — that
is the separate `doc-validator` tool's job. `claimhist.py` only knows about
hashes and test counts.

## Exact rerun command

```
python3 claimhist.py --root <directory-to-scan> [-o <output-file>] [--run-tests]
```

- `--root` (required) — directory to scan recursively for `README.md` and
  `captured_output.txt` files.
- `-o` / `--output` (optional) — write the canonical JSON report here
  instead of stdout.
- `--run-tests` (optional, off by default) — actually execute discovered
  `test_*.py` modules to recompute TESTCOUNT_CLAIMs. Without this flag,
  every TESTCOUNT_CLAIM is reported `NOT_RECOMPUTED`; this tool never runs
  your test suite unless you explicitly ask it to.

Reproduce this project's own determinism proof exactly as it was run:

```
python3 claimhist.py --root fixture -o out1.json
python3 claimhist.py --root fixture -o out2.json
sha256sum out1.json out2.json                      # identical

cp -r fixture loc_aaa/fx
cp -r fixture loc_zzzzzzzz/fx
python3 claimhist.py --root loc_aaa/fx -o out_a.json
python3 claimhist.py --root loc_zzzzzzzz/fx -o out_z.json
sha256sum out1.json out_a.json out_z.json           # all three identical
```

## Claim formats supported

### SHA256_CLAIM

1. **sha256sum transcript form (primary case)**: a line consisting of a
   64-hex-char digest, one or more spaces/tabs, an optional `*`
   (coreutils' binary-mode marker), and the rest of the line taken
   verbatim as the filename:
   ```
   e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b85  report.txt
   ```
2. **prose association form**: a bare 64-hex-char token appears in a line
   that is not in transcript form. The tool looks for a filename to
   associate it with, in this exact order:
   - backtick-quoted tokens containing a dot, on the **same line**
     (`` `report.txt` ``), leftmost first;
   - otherwise, bare word tokens containing a dot, on the **same line**
     (`report.txt`, `sub/dir/report.txt`);
   - if the same line has zero candidates, the **nearest previous
     non-blank line** is checked (skipping over any number of blank
     lines), then the **nearest next non-blank line**.

   If a line (same or the chosen neighbour) yields two or more *distinct*
   filename candidates, the claim is `MALFORMED`
   (`ambiguous_filename_association`) rather than guessed at. If no
   candidate is found anywhere, it is `MALFORMED`
   (`no_filename_association`).

### TESTCOUNT_CLAIM

Tried in this priority order per line — a single line contributes **at
most one** TESTCOUNT_CLAIM:

1. `Ran N tests in` — literal unittest summary output, e.g.
   `Ran 137 tests in 0.045s`.
2. `**N tests across M tools**` — bold markdown aggregate claim.
3. `N tests` — bare fallback, e.g. `137 tests`.

In every shape, if the matched digit run is immediately preceded by a
comma and a digit (e.g. the `234` inside `1,234 tests`), the claim is
`MALFORMED` (`ambiguous_number_format`) instead of silently parsing a
truncated number — comma-grouped counts are not supported, on purpose.

## Provenance

For every claim the report records the source file (path relative to
`--root`), the 1-based line number, and — when the target is inside a git
work tree and `git` is on `PATH` — the commit SHA and author date
(ISO-8601) of the last change to that exact line, via
`git blame --porcelain -L<line>,<line> -- <file>` followed by
`git show -s --format=%H|%aI <sha>`. When git is unavailable, `--root` is
not inside a git work tree, or blame/show fail for any reason, the commit
fields are `null` and `provenance.note` is `"GIT_UNAVAILABLE"` — this never
aborts the run and never invents a SHA. If the blamed line is uncommitted
(git's all-zero SHA), `provenance.note` is `"UNCOMMITTED_LINE"` instead.

## Recomputation

- **SHA256_CLAIM** — the referenced file (resolved first relative to the
  directory of the claiming file, then relative to `--root`) is hashed
  with SHA-256 and compared byte-for-byte against the claim.
- **TESTCOUNT_CLAIM** — recomputation requires *running a test suite*,
  which this tool refuses to do unless `--run-tests` is passed explicitly.
  With `--run-tests`, the tool looks in the directory of the claiming file
  for files named `test_*.py`: exactly one → run
  `python3 -m unittest <module> -v` there and parse the **last**
  `Ran N tests in` line from the combined stdout+stderr; zero →
  `MISSING_SOURCE`/`no_test_module_found`; more than one →
  `MALFORMED`/`ambiguous_test_module`; a run producing no parseable
  summary line → `MALFORMED`/`unittest_execution_failed`. The count is
  never guessed.

## Statuses

`CURRENT`, `STALE`, `MISSING_SOURCE`, `NOT_RECOMPUTED`, `MALFORMED`.

## Exit codes

| Code | Meaning |
|------|---------|
| `0` | Every claim is `CURRENT` or `NOT_RECOMPUTED`. |
| `1` | At least one claim is `STALE`, `MISSING_SOURCE`, or `MALFORMED`. |
| `2` | Invalid input or execution failure: bad `--root` (missing or not a directory), an unwritable `-o` path, or bad CLI arguments (argparse itself already exits `2` for the latter). |

## Expected-results table (bundled `fixture/`)

Running `python3 claimhist.py --root fixture` against the bundled fixture
repo produces exactly these 5 claims (see `sample_report_run1.json`):

| source_file | line | claim_type | status | reason |
|---|---|---|---|---|
| README.md | 7 | SHA256_CLAIM | STALE | hash_mismatch |
| README.md | 11 | TESTCOUNT_CLAIM | NOT_RECOMPUTED | test_execution_not_requested |
| captured_output.txt | 2 | SHA256_CLAIM | CURRENT | — |
| captured_output.txt | 10 | TESTCOUNT_CLAIM | NOT_RECOMPUTED | test_execution_not_requested |
| captured_output.txt | 14 | TESTCOUNT_CLAIM | NOT_RECOMPUTED | test_execution_not_requested |

Overall exit code: `1` (the `report.txt` hash claim in `README.md` is
genuinely stale — the fixture's second commit edited `report.txt` after
the README's hash claim was written, on purpose, to demonstrate detection).
With `--run-tests` added, the three TESTCOUNT_CLAIMs above instead
recompute to `CURRENT` (the bundled `test_example.py` really has 3 tests),
but the overall exit code is still `1` because the hash claim remains
stale.

## Determinism and canonical JSON

The report is written with
`json.dumps(obj, sort_keys=True, separators=(",",":"), ensure_ascii=True) + "\n"`,
via `open(path, "w", encoding="utf-8", newline="\n")`. Every list in the
report is explicitly sorted; the `claims` list is sorted on
`(claim_type, source_file, line, associated_target, status)` and then, as
a **final, always-present tiebreaker, on the canonical JSON dump of the
claim itself** — so the order is a guaranteed total order even between two
findings that are identical on every documented field. No wall clock is
ever read (no current-time call anywhere in `claimhist.py`, including
comments and docstrings), and no absolute path, duration, hostname, or
mtime is ever written into the report — every path in the output is
relative to `--root`. This was verified with real runs: scanning the same
fixture from the same directory twice, and scanning copies of that fixture
placed at two different absolute paths with different directory names,
all three runs produced **byte-identical** output (see the transcript in
`captured_output.txt` for the actual `sha256sum` values obtained).

## Bug found and fixed

**Bug**: a bare 64-hex-char hash immediately followed by a dot-extension
with **no separating whitespace** (e.g. `` <64 hex chars>.report ``, which
can occur when a hash is glued directly onto a sentence) was
mis-identified as its own "filename" candidate by the bare-filename regex,
because hex digits satisfy `\w` and the regex had no way to distinguish
"a hash followed by `.report`" from "a genuine dotted filename token".
The tool would then report `associated_target` as a bogus string literally
containing the 64-hex claimed hash itself, and try (and fail) to resolve
it as a file, producing a misleading `MISSING_SOURCE` instead of the
correct `MALFORMED`/`no_filename_association`.

**Triggering input**:
```
See aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.report for details.
```

**Fix**: `find_filename_candidates()` now masks every 64-hex-char token in
the line (replacing it with an equal-length run of `#`, a character
matched by neither the backtick nor bare filename regex) *before* running
either filename regex, so a hash can never itself be mistaken for part of
a filename, whether bare or backtick-quoted.

**Pinning tests**: `TestFindFilenameCandidates.test_masks_hash_before_bare_match_regression`,
`TestFindFilenameCandidates.test_masks_hash_before_backtick_match`, and the
dedicated `TestBugRegressionHashAdjacentDot` class (3 tests) in
`test_claimhist.py`.

## Limitations

1. **The adjacent-line filename heuristic has no semantics.** It only
   looks at the nearest non-blank line above or below a stray hash (after
   same-line backtick/bare search fails); a hash whose intended filename
   is mentioned two paragraphs earlier, with an unrelated non-blank line
   directly adjacent, will be reported `MALFORMED`/`no_filename_association`
   even though a human reader would resolve it correctly from context.
2. **Comma-grouped test counts and free-form prose counts are refused, not
   parsed.** `1,234 tests` is flagged `MALFORMED`/`ambiguous_number_format`
   rather than read as 1234, and phrasing like "we currently have around
   137 unit tests" or "test count: 137" (colon-separated, no trailing word
   "tests" immediately after the digits) matches none of the three
   supported shapes and is silently not detected as a claim at all.
3. **`--run-tests` requires exactly one `test_*.py` file co-located with
   the claiming file.** If the real suite lives in a different directory
   (e.g. a `tests/` subfolder next to a top-level `README.md`) or spans
   multiple `test_*.py` modules that must be run together, the tool cannot
   locate a single module to run and reports `MISSING_SOURCE`
   (`no_test_module_found`) or `MALFORMED` (`ambiguous_test_module`)
   instead of attempting to guess or aggregate.
4. **Git provenance is line-content-position based, not claim-content
   based.** `git blame` on the current line number reports when *that
   line* was last touched; a purely cosmetic reflow (e.g. rewrapping
   surrounding prose) that shifts a claim to a different line number, or
   that touches the line without changing the claimed value itself, can
   attribute a more recent commit/date to a claim whose actual value never
   changed.

5. **The tool cannot distinguish a claim from documentation *about* claims, and
   flags its own README.** Running `claimhist.py --root .` inside this directory
   reports 3 `MALFORMED` findings at `README.md` lines 92, 192 and 215. All three
   are this README's own illustrative examples of formats the tool deliberately
   refuses (the comma-grouped `1,234 tests` sample, and a bare example hash with
   no filename beside it). Any document that explains claim syntax will trip this.
   There is no suppression mechanism; the workaround is to read `source_file`
   before acting on a finding. The self-audit transcript is in
   `captured_output.txt` and the report in `self_audit_report.json`.

## What's in this directory

- `claimhist.py` — the tool.
- `test_claimhist.py` — 154 unit/integration tests (`python3 -m unittest
  test_claimhist -v`).
- `fixture/` — a real, git-initialized demonstration repo (3 commits)
  used for the expected-results table above and the determinism proof.
- `captured_output.txt` — a real transcript: the verbose test run, every
  documented CLI invocation with its actual stdout and exit code, the
  sha256sum determinism/relocation block, and error-path runs.
- `sample_report_run1.json` / `sample_report_run2.json` — the two
  byte-identical reports from the "same directory, run twice" step of the
  determinism proof.
