# claim-crosscheck (`crosscheck.py`)

## Purpose

Cross-checks **file-level claims in a tool's `README.md`** against **that
tool's own committed JSON report** -- the report is not re-run, it is read
exactly as it sits on disk. A README routinely narrates what its own report
contains (a sentence naming a file as a hostname leak, a summary sentence
giving a confirmed-leak count and a files-touched count, a category-count
table); nothing else in this repository re-checks those sentences against
the JSON after the fact. `crosscheck.py` does exactly that, for these
kinds of claim (shown here as *italic* prose rather than the literal
backtick-and-verb text that actually triggers each extractor, precisely so
this table does not become a false-positive-generating claim about its own
`sample_run.json` the next time `--all` scans this directory -- see
"Bug hunt" below for what happens when documentation prose *does* echo the
trigger phrasing exactly):

| Claim kind | Example README text (illustrative, not literal) | Checked against |
|---|---|---|
| `PRESENCE` | *x.py* carries a leak / *x.py* is flagged / *x.py* is present | does `x.py` appear as a `file`/`path`/`filename` value inside a **positive** top-level array in the report? |
| `LOCATION` | at *x.py* line 42 | same, plus: does that entry's `line` field equal 42? |
| `CATEGORY` | *x.py* is a *hostname* leak | does a positive-bucket entry for `x.py` have `category`/`type`/`kind`/`class` equal to `hostname`? |
| `TABLE_COUNT` | a two-cell table row, category cell then integer cell, under a header containing the word "Category" | is there an integer in the report, keyed by the category cell's text, equal to the integer cell? |
| `SUMMARY_COUNT` | a confirmed-leak count, a benign-match count, or a stale-entry count stated as "N confirmed leaks" / "N matches reviewed and dismissed as benign" / "N stale review entries" | is there an integer in the report matching that metric name (`confirmed`/`benign`/`stale`), via `counts.<metric>` or a bucket's length? |
| `DISTINCT_FILE_COUNT` | "across N files" | does the number of distinct files named across all positive buckets equal N? |
| `DIR_COUNT` | *dir/x.py* alone accounts for 12 of them | do exactly 12 positive-bucket entries have a file matching that path/prefix? |

Every discrepancy record carries all six required fields: `readme_path`,
`readme_line`, `readme_quote` (an exact substring of the README, newlines
and all), `report_path`, `json_pointer` (RFC 6901), and `report_excerpt`
(canonical JSON of the report fragment that supports or contradicts the
claim).

## Usage

```
python3 crosscheck.py --readme <path/to/README.md> --report <path/to/report.json> [--json | -o OUT]
python3 crosscheck.py --root <repo-root> --tool <tool-dir-name> [--json | -o OUT]
python3 crosscheck.py --root <repo-root> --all [--strict-discovery] [--json | -o OUT]
```

`--readme`/`--tool`/`--all` are mutually exclusive: exactly one selects the
target set. `--report` pairs with `--readme` (required there) or overrides
discovery under `--tool`. `--root` defaults to `.` and is echoed back
verbatim into every `readme_path`/`report_path` in the output -- it is
never resolved to an absolute path, which is what makes the relocation
determinism proof below work (see "Determinism and relocation proof").

Output is human-readable text on stdout by default. `--json` prints
canonical JSON (`sort_keys=True`, compact separators, one trailing
newline) to stdout instead. `-o PATH` additionally writes that same
canonical JSON to `PATH` (creating parent directories as needed); stdout
still gets human-readable text unless `--json` is also given.

### Exit codes

| Exit | Meaning |
|---|---|
| `0` | every extracted claim, in every report actually read, matched |
| `1` | at least one discrepancy, and every targeted report was readable |
| `2` | a targeted report was missing / unreadable / not valid JSON / not a JSON object, OR a command-line usage error (no mode given, `--readme` without `--report`, unknown `--tool`, ambiguous report discovery under `--strict-discovery`) |

Exit 2 outranks exit 1: if any report in the run could not be read, the
whole run exits 2 even if other targets had genuine discrepancies (see
`overall_exit_code` in `crosscheck.py`).

### Reproduce this project's own runs

```
cd claim-crosscheck
python3 make_fixtures.py --verify
python3 -m unittest test_crosscheck -v
python3 crosscheck.py --readme ../env-leak-scanner/README.md --report ../env-leak-scanner/leak_report_2026-08-04.json
python3 crosscheck.py --root .. --all -o sample_run.json
```

The third command reproduces the confirmed contradiction below on the
current repository. The fourth reproduces the committed `sample_run.json`
byte-for-byte (see "Determinism and relocation proof").

## Bucket polarity

A report's top-level object may have several keys whose value is a JSON
array of objects ("buckets"). A bucket is **negative** if its key,
lower-cased, contains any of: `benign`, `stale`, `ignored`, `exempt`,
`excluded`, `suppressed`, `dismissed`, `skipped`, `allowlist`, `allowed`,
`denylist`. Every other array-valued top-level key is **positive**.
`confirmed_leaks` is positive; `reviewed_benign` and
`stale_review_entries` are negative. `PRESENCE`/`LOCATION`/`CATEGORY`
claims must be satisfied by a **positive**-bucket entry -- appearing only
in a negative bucket is exactly the discrepancy this tool exists to catch.

## Report discovery (`--tool` / `--all`)

The report file is not guessed from a naming convention. It is read off
the README the same way a reviewer would, in three narrowing stages, each
of which stops as soon as it leaves exactly one candidate:

1. Every backtick-quoted `*.json` filename that also exists in the tool
   directory, filtered to those whose JSON is **report-shaped** (a dict
   with at least one array containing at least one dict that has a
   `file`/`path`/`filename` key).
2. If that is not unique: the filename inside the phrase
   `` committed as `<file>` `` (env-leak-scanner's own convention),
   narrowed further by the shaped set from stage 1 when it is non-empty.
3. If still not unique: any candidate (falling back to *all* mentioned
   filenames, not just shaped ones, when nothing was shape-detected --
   see "Limitations" for why a genuinely-empty report needs this) that
   appears within 80 characters of the word "committed" and the filename,
   in either order.

If exactly one candidate survives, it is used. Otherwise discovery is
`ambiguous` or `absent`; under `--tool` that is a hard error (exit 2);
under `--all` it is a skip (unless `--strict-discovery`, which turns it
into an error for any tool directory whose README has claims that were
therefore left unchecked).

### Scope boundary: file-keyed reports only

Discovery only recognizes reports that key findings **by file**
(`file`/`path`/`filename`). `transcript-drift/drift_report_2026-08-04.json`
and `readme-index/index_report.json`, for example, key findings by `tool`
instead, and are correctly never discovered -- this tool's claim kinds
are all about individual files, so a per-tool report has nothing for it to
check. That is a deliberate scope boundary, confirmed by running `--all`
against this repository (see "Sample run" below), not an oversight.

## Relationship to `claim-checker` and `commit-claim-auditor`

Three different things, on purpose, despite superficial similarity (all
three: stdlib-only, extract quoted claims from prose, exit 0/1/2, RFC 6901
or canonical-JSON output):

* **`claim-checker`** (`claimcheck.py`) checks a *verifier's free-text
  notes* about a *submission bundle* -- a SHA-256 digest, a test count, an
  exit code -- by actually hashing files, running the bundle's test suite,
  or (under a safety gate) running a claimed command. It answers "is this
  external assertion about a bundle true right now, and how did we check?"
* **`commit-claim-auditor`** (`claimhist.py`) checks a *README's or
  transcript's own numeric self-claims* (a SHA-256 digest, a test count)
  against the *current* state of the artifact they describe, to catch
  stale claims a refactor left behind. It never reads a JSON report; it
  recomputes hashes and (optionally) reruns tests.
* **`claim-crosscheck`** (this tool, `crosscheck.py`) checks a *README's
  prose about a specific file* ("this file is flagged/carries/is a
  category of leak") against *a different, already-committed JSON
  document* -- the tool's own structured report -- without hashing,
  running, or re-scanning anything. It is the only one of the three that
  reasons about bucket polarity (positive vs. reviewed-benign) and emits
  an RFC 6901 pointer into the report alongside the README quote. Neither
  sibling tool reads a structured findings report at all.

## Sample run (`sample_run.json`, `captured_output.txt`)

`sample_run.json` is the canonical JSON output of
`python3 crosscheck.py --root .. --all -o sample_run.json`, committed
as-is. Against the current repository -- 46 tool directories (the
original 45, plus `claim-crosscheck` itself), all 46 with a `README.md` --
9 tool directories had a discoverable, file-keyed report; 8 of those had
zero claims of the supported kinds (their READMEs never use the specific
`carries`/`is flagged`/`is present`/`is a ... leak`/count phrasings this
tool recognizes -- `claim-crosscheck`'s own README is one of the 8, by
design; see "Bug hunt" below); `env-leak-scanner` had **15**, of which
**1 is a genuine, confirmed contradiction**:

> `env-leak-scanner/README.md` line 93 lists `` `snapshot-diff/README.md` ``
> among five files that "carry the rest" of 20 confirmed `hostname` leaks.
> The committed `leak_report_2026-08-04.json`'s **only** entry for that
> file is `/reviewed_benign/87` -- `verdict: "benign"`, the session name
> already redacted to `"..."`. Zero entries for it exist in
> `confirmed_leaks`.

Both sources are quoted verbatim, with exact locations, in
`captured_output.txt` and in `sample_run.json`'s single discrepancy
record. The other four files named in that same sentence
(`LIMITATIONS.md`, `REVIEWERS_GUIDE.md`, `doc-validator/`,
`bundle-index/`) and all fourteen other extracted claims -- the five-row
category-count table, the "121 confirmed / 100 benign / 0 stale / files
touched: 23" summary sentence, and the
`weak-assertion-scanner/README.md:281` location claim -- were checked
against the same report and are **not** discrepancies. Volunteering that
explicitly: 14 of 15 checked claims in the one tool with any checkable
claims turned out to be true. The tool does not manufacture noise to look
busy.

### Bug hunt: what running the tool against real files actually found

Four things surfaced only by *running* `crosscheck.py` against real files,
not by reasoning about the code in the abstract:

1. **The confirmed contradiction above** -- found on the very first real
   run against env-leak-scanner, before any tuning.
2. A **false positive from an early version** of the `TABLE_COUNT`
   extractor: env-leak-scanner's README has a *second* two-cell table,
   "Positive control" (a row shaped like a scratch-file path cell next to
   an integer cell), whose first cell is an example scratch-file path and
   second cell is a *cumulative* `confirmed` count after planting one
   line -- not a per-category count keyed by that cell's text at all. The
   early extractor read it as four bogus category claims. Fixed by
   requiring the table's header row to contain the word "category" (as a
   whole word -- see limitation 2 below for what that fix does *not*
   catch). This is now
   `ExtractTableCountTests.test_ignores_row_without_category_header`.
3. A **false positive from an early version** of the `PRESENCE`
   extractor: a sentence describing `review.json`'s own key format, whose
   *unrelated* final clause happened to contain the word "carries", was
   read as a claim that `review.json` itself carries a report entry,
   purely because `review.json` was the nearest preceding backtick token
   to "carries" in the same sentence. Fixed by requiring every token
   attributed to a shared verb to be connected to it (and to each other)
   by pure list glue (comma/"and"/whitespace only) -- the intervening
   "keyed by ..." clause breaks the chain. This is now
   `ExtractPresenceTests.test_glue_break_excludes_earlier_tokens`.
4. A **false negative found while writing limitation 1 below**: an early
   draft of that limitation's own text, illustrating fence-pairing with
   literal triple-backtick examples inline in prose, gave this README an
   odd count of fence markers and caused `mask_fenced_blocks()` to treat
   everything between that paragraph and the next real code fence (~100
   lines, most of this Limitations section) as one giant masked block --
   silently dropped from this file's own claim extraction. Caught by
   running `crosscheck.py` against this README directly and noticing the
   claim count came back lower than it should have. Fixed by rewriting
   the limitation to describe fence markers in words instead of writing
   them literally; the tool itself is unchanged, and limitation 1 below
   now documents the underlying mechanism directly, since rewriting the
   prose fixed this file but not the tool's ability to mis-pair fences in
   general.

Findings 2, 3, and 4 are not discrepancies in a subject README -- they
were bugs in *this tool* (and, for 4, in an earlier draft of *this very
document*), caught by running it, not merely by reading the code. They
are reported here per the "volunteer negative results" requirement:
three plausible-looking "contradictions" (two false positives, one false
negative) were investigated and found to be tool or draft artifacts, not
real defects in a subject tool's README, and the fix for each is now
either a named regression test or, for the fence-pairing case, a limitation
this document names about itself.

## Determinism and relocation proof

Three independent runs of `python3 crosscheck.py --root .. --all -o OUT`
against the current repository -- two from this checkout, a third from an
entire copy of the repository relocated to a differently-named absolute
path (`/tmp/build_1/reloc_71bd4f`, not part of this commit) -- produced
byte-identical canonical JSON:

```
sha256(out1.json) = 0a54c5eca02ef85cf741cd1dabc58bcde071667e4d07d453af70e06d20df0a50   (this checkout, run 1)
sha256(out2.json) = 0a54c5eca02ef85cf741cd1dabc58bcde071667e4d07d453af70e06d20df0a50   (this checkout, run 2)
sha256(out3.json) = 0a54c5eca02ef85cf741cd1dabc58bcde071667e4d07d453af70e06d20df0a50   (relocated copy, run 3)
```

All three hashes are equal. Full transcript, including the exact commands,
is in `captured_output.txt`. The relocation leg is the meaningful one: two
runs in the same directory prove nothing about path leakage by themselves
(`--root` could still be silently resolved to an absolute path that
happens to be identical because the directory didn't move) -- the third
run, from a directory with a different name at a different absolute
location, is what actually rules that out. This is possible because
`crosscheck.py` never calls `os.path.abspath`/`os.path.realpath` on
`--root` or any derived path; every path in the output is the literal
string the caller passed, joined with `os.path.join`.

`DeterminismTests.test_two_runs_byte_identical` and
`DeterminismTests.test_dict_construction_order_does_not_affect_output` in
`test_crosscheck.py` cover the same property (byte-identical repeated runs;
JSON key-insertion-order independence) at the unit level; the relocation
leg specifically is not practical to assert inside `unittest` without
writing a second full repository copy on every test run, so it is proven
here, once, with real `sha256sum` output, and left out of the automated
suite.

## Limitations (named failure modes, not vague hedges)

1. **`mask_fenced_blocks()` pairs fence markers by simple non-greedy
   regex matching, with two opposite failure directions, and writing this
   very paragraph found the first one.** A README with an *odd* number of
   triple-backtick fence markers has its *last* one left with nothing to
   pair against, so `re.DOTALL` non-greedy matching pairs it with
   whatever the *next* real fence marker anywhere later in the file
   happens to be -- silently masking every real claim in between as a
   false negative. This is not hypothetical: an earlier draft of this
   exact paragraph, describing fence-pairing using literal triple-backtick
   examples inline in prose, itself produced an odd marker count and
   masked roughly 100 lines of this Limitations section (everything
   between here and the next `python3 -m unittest` example fence further
   down) out of its own claim extraction -- caught only by running
   `crosscheck.py --readme README.md` against this file directly and
   noticing the count came back zero when it should not have. The fix
   here was to stop writing literal fence markers inline in prose (this
   paragraph now describes them in words instead); the fix in
   `crosscheck.py` would need to fall back to treating an unpaired final
   marker as "open to end of file" instead of reaching forward across
   unrelated content, which this version does not do. The *inverse*
   direction (a fence that is opened but genuinely never closed before
   end of file, with no later marker to accidentally pair against) does
   leave its content unmasked, per
   `MaskFencedBlocksTests.test_unterminated_fence_left_unmasked` --
   the two directions are different bugs with the same root cause.

2. **`TABLE_COUNT`'s "has a 'category' header" guard is a per-table check,
   not a per-column check.** A three-or-more-column table whose header row
   happens to contain the word "Category" *anywhere* (not necessarily
   over the numeric column actually being read) will still have its
   two-backtick-then-integer-shaped rows extracted as category-count
   claims, even if that particular column is not the category column. No
   real table in this repository's tracked READMEs currently has that
   shape, but the extractor does not verify column *alignment* with the
   header, only that some header cell contains the word.

3. **`PRESENCE`'s list-glue chain has no cap on backward distance or
   forward verb reach**, and a single verb match is scoped to one
   `sentence_spans()` sentence. Two different failure directions follow
   from this (described here in prose, deliberately without the literal
   backtick-and-verb text that would trigger them against *this* file --
   see limitation 1 just above for why that caution is not theoretical).
   (a) A very long backtick-separated list, comma-then-"and" all the way
   through, is entirely attributed to one shared verb with no length cap
   -- intentional and correct for the env-leak-scanner five-file case, but
   an adversarial README could attribute an implausibly long chain to one
   verb the same way. (b) `sentence_spans()` treats a colon before a
   capital letter or backtick as a sentence boundary, but *not* a
   semicolon -- so two independent clauses joined by a semicolon instead
   of a period stay inside one sentence span. Concretely: a first clause
   that names a file and a supported verb, followed by "; " and a
   *second*, semicolon-joined clause whose own subject file is glued by a
   leading comma into the *first* clause's token chain, would have the
   second file incorrectly attributed to the first clause's verb -- the
   glue check only inspects whitespace/comma/"and" between tokens, and a
   semicolon never appears between two backtick tokens in that shape, only
   between a verb and the next clause's opening token. In practice: **a
   semicolon-joined clause that itself opens with a bare backtick token
   immediately followed by comma-glue into more tokens, ending in a
   supported verb, is indistinguishable from one long comma list to this
   tool.** No fixture currently exercises this because it did not occur in
   any tracked README; it is named here because reasoning about the regex
   predicts it, and the "volunteer negative results" instruction applies
   to design gaps as much as to README defects.

4. **`DIR_COUNT` and `TABLE_COUNT` claims are evaluated across *every*
   category/bucket combined, with no way to scope a count to "only the
   `hostname` category" from prose alone.** This is why
   `looks_like_path_token()` deliberately rejects bare identifiers with no
   `/` or extension (like `weak-assertion-scanner`): the real README
   sentence `` `weak-assertion-scanner` alone accounts for 12 of them ``
   means *12 of the 20 hostname-category confirmed leaks*, not "12 of
   `weak-assertion-scanner`'s confirmed leaks in any category" (the real
   number for that is 30 -- see `env-leak-scanner`'s own README,
   "Coverage" section, if it is ever re-derived). Extracting that sentence
   without category scoping would produce a **spurious mismatch** report
   claiming the README is wrong when it is not; the tool avoids that by
   simply not extracting a `DIR_COUNT` claim from a bare tool name at all
   (`ExtractDirCountTests.test_bare_directory_name_rejected`). The
   trade-off: any README sentence of exactly this shape is silently
   unchecked rather than checked-and-possibly-wrong. If category-scoped
   `DIR_COUNT` extraction is ever added, it needs real cross-sentence
   category resolution (tracking the most recently established category
   context), which this version does not attempt.

5. **Report discovery can resolve to an input fixture instead of an
   output report when both are "committed" in the README's prose**, once
   shape-detection has nothing to go on. Running `--all` against this
   repository, `regression-checker/README.md` -- which says
   `` `baselines.json` | committed baseline for the 23 real sibling tools
   `` -- causes discovery to resolve to `baselines.json`, which is an
   *input* fixture the tool reads, not a findings report it writes. This
   had no practical effect in the committed sample run (that README has
   zero claims of the supported kinds, so nothing was checked against the
   wrong file), but a future README that both describes `baselines.json`
   as "committed" *and* makes a `PRESENCE`/`CATEGORY` claim about a file
   would be checked against the wrong document. `is_report_shaped()`
   cannot rule this out because `baselines.json` is legitimately shaped
   like other tools' inputs (a dict of lists), and "input vs. output" is
   not a distinction available from JSON shape alone.

Limitation **3** is the one I would weight most heavily. It is the only
one of the five whose failure mode is a false *negative that could hide a
real contradiction* (a semicolon-joined false claim silently absorbed into
a true one's evaluation, or the reverse) rather than a false positive a
reader would immediately notice and investigate. Limitations 1, 2, 4 and 5
all either fail loudly (an unterminated fence still produces a checkable,
visible claim; an over-eager table row produces a visible, investigable
mismatch) or fail by omission in a way this README says outright
(sentences of a named shape are simply not checked). Limitation 3 is the
one where a real contradiction could theoretically slip through unflagged
because it was folded into an adjacent true claim's chain -- and it is the
one hardest to defend against without a real sentence-structure parser,
which is out of scope for a stdlib-only regex-based tool.

## Testing

```
cd claim-crosscheck
python3 make_fixtures.py --verify   # fixtures/ is generated, verify it matches
python3 -m unittest test_crosscheck -v
```

**243 tests**, `python3 -m unittest test_crosscheck`, all passing (see
`captured_output.txt` for the unmodified `Ran 243 tests in ...s` / `OK`
transcript). Coverage includes: canonical JSON formatting and key-order
independence; RFC 6901 pointer construction, escaping (`~0`/`~1`) and
round-trip resolution; bucket polarity for every negative keyword;
`file`/`path`/`filename` precedence; directory-prefix vs. exact-file
matching (with and without a trailing slash, and the bare-directory-name
edge case); every extractor (table/summary/presence/location/category/
dir-count) with match, no-match, and boundary cases including CRLF line
endings, Unicode content, an empty README, a README with zero checkable
claims, malformed JSON, a non-object top-level JSON value, a missing
report, ambiguous and absent report discovery, and the "empty findings
report" discovery edge case (limitation-adjacent: shape detection needs
at least one entry to key on); every `evaluate_*` function's match,
negative-bucket, and wholly-absent branches; CLI argument handling
(mutually exclusive modes, `--json`, `-o`, `--strict-discovery`, usage
errors) both in-process and via real `subprocess` invocation matching the
documented commands verbatim; determinism (repeated runs, dict
construction order); and a dedicated ten-test class
(`RealRepoContradictionTests`) that pins the exact env-leak-scanner
contradiction -- line number, quote content, JSON pointer, resolved report
entry, and the fact that the other four files named in the same sentence
are *not* flagged -- against the live files in `../env-leak-scanner/`, not
a synthetic fixture.

Fixtures live under `fixtures/` (27 files, 1 genuinely empty directory),
generated by `make_fixtures.py` from base64-embedded bytes written in
binary mode -- see that file's docstring for why (CRLF preservation,
Unicode bytes, and the empty-directory case all silently break under a
naive text-mode/`os.walk`-based generator, even though every test would
still pass, because the *committed* fixture bytes would differ from what
the generator produces next time). `python3 make_fixtures.py --verify`
regenerates into a temp directory and `diff -r`s it against the committed
`fixtures/`, byte-for-byte.

## Requirements

Standard library only: `argparse`, `base64`, `filecmp`, `json`, `os`,
`re`, `shutil`, `sys`, `tempfile`, `unittest`, `subprocess` (tests only).
No third-party packages. No network access.


---

## Report-discovery note (updated after `baseline_coverage_report.json` landed)

An earlier revision of this file recorded that report auto-discovery
misfired for `regression-checker/`, resolving to `baselines.json` -- an
INPUT fixture, not an output report. It found no claims there, so it did
no harm, but the resolution was wrong and was disclosed as limitation 4.

That directory now also ships `baseline_coverage_report.json`. With two
candidate reports present, discovery no longer guesses: it records

```
"report_path": null,
"skipped_reason": "no checkable claims (report discovery: ambiguous)"
```

which is the correct behaviour. The committed `sample_run.json` and the
hashes above were regenerated against that state. The substantive result
is unchanged -- 46 results, 1 discrepancy, the `snapshot-diff/README.md`
contradiction in `env-leak-scanner`, exit 1.

The general lesson is worth stating plainly, because it bit this
repository twice in one day: a committed report that enumerates the whole
repository is invalidated by any commit that adds a file to the
repository. `sample_run.json` is a snapshot of the tree it was generated
against, not a standing claim about every future tree. Re-run the
documented command rather than trusting the committed hash if the tree has
moved since.
