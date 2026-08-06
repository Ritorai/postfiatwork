# transcript-schema

A versioned, machine-readable schema (`schema.json`) and a stdlib-only
validator (`validate_transcript.py`) for `captured_output.txt`, checked
directly against `transcript-drift/FORMAT.md` — the normative source of
truth for this repository's transcript format.

## Why this exists, next to transcript-drift

`transcript-drift/driftcheck.py` already parses `captured_output.txt` well
enough to compare it against a README's claims. But it is fundamentally a
**cross-file comparison tool**: it needs both a README and a transcript, and
its three structural checks (`TRANSCRIPT_HAS_NO_COMMAND_RECORDS`,
`TRANSCRIPT_RECORD_HAS_NO_EXIT`, `TRANSCRIPT_SHOWS_TEST_FAILURE`) exist only
as a side effect of building that comparison. It has no reason to notice
that a header is missing a space, that `exit=` was typed `Exit=0`, or that a
unittest verdict line was printed before its own `Ran N tests` line — none
of those affect whether a README's claims match, so driftcheck never looks
for them.

This tool looks at **one transcript at a time**, with no README required,
and validates it directly against the grammar in `FORMAT.md`. It reuses
driftcheck's three structural diagnostic codes where they mean the same
thing (see below), and adds eight of its own for malformed near-misses and
one specific, honestly-scoped kind of misordering.

| | driftcheck.py | validate_transcript.py |
|---|---|---|
| Input | README.md + captured_output.txt | captured_output.txt alone |
| Checks | README claims vs transcript content | transcript vs FORMAT.md grammar |
| Catches | wrong test count, unacknowledged exit code, undocumented command | malformed header/exit/Ran lines, verdict-before-Ran, missing Ran/verdict on test records |
| Shares | 3 codes (see below) | same 3 codes, same meaning |

## Files

| File | Purpose |
|---|---|
| `schema.json` | versioned, machine-readable grammar + diagnostic table |
| `validate_transcript.py` | the validator; loads `schema.json`, never hardcodes a regex or a severity |
| `make_fixtures.py` | generates the 33 test fixtures (27 files + a 6-directory tree) from base64, in binary mode |
| `test_validate_transcript.py` | 194 tests, `python3 -m unittest` |
| `captured_output.txt` | this tool's own transcript — validates against its own validator (see "Dogfooding" below) |
| `validation_report.json` | the committed, generated report from running this validator across all 46 tool directories plus itself |

## Usage

```
# one or more specific files
python3 transcript-schema/validate_transcript.py path/to/captured_output.txt

# every tool directory under a root (mirrors driftcheck.py --root)
python3 transcript-schema/validate_transcript.py --root . -o transcript-schema/validation_report.json

# a non-default schema (used by the "schema really drives behaviour" tests)
python3 transcript-schema/validate_transcript.py FILE --schema /path/to/other_schema.json
```

## Exit codes

| Exit | Meaning |
|---|---|
| `0` | every transcript checked is valid (no error-severity finding anywhere) |
| `1` | at least one transcript was read but has an error-severity finding — this includes an individual unreadable/undecodable file **when scanning `--root`** (the run itself succeeded; that one file failed) |
| `2` | setup error: `--root` is not a directory, `--schema` is missing/invalid JSON/fails self-validation, no target was given, `--root` and file paths given together, **or** (single-file/`paths` mode only) a given file does not exist or cannot be decoded — there being nothing else to report in that mode |

The asymmetry between "one bad file among many" (exit 1) and "the only file
you asked about is unreadable" (exit 2) is deliberate: a `--root` scan over
46 directories should not abort and report nothing just because one
directory's transcript is garbage; a single-file invocation that can't even
read its one argument has nothing left to say, so it is a setup error.

## The schema (`schema.json`)

`schema_version: 1`. Two top-level sections:

- **`patterns`** — every regex the validator uses, each with a `regex` and a
  `description`. Four are FORMAT.md's own regexes verbatim (`header`,
  `exit`, `ran`, `verdict`); four are this tool's own near-miss detectors
  (`header_lookalike`, `exit_lookalike`, `ran_lookalike`) plus a heuristic
  (`test_command`) for recognising a "test command" record, which FORMAT.md
  does not define (see Limitations).
- **`diagnostics`** — every diagnostic code, with a `severity` (`error` —
  contributes to exit 1/status "invalid" — or `info` — reported but never
  fails validation) and a `message`.

`validate_transcript.py` reads both sections at start-up
(`load_schema`/`compile_patterns`); nothing in the `.py` file duplicates a
regex or a severity as a Python constant. `TestSchemaDrivesValidator` in
`test_validate_transcript.py` proves this by editing a copy of the schema
(loosening the header regex, downgrading/upgrading a severity, narrowing
the test-command heuristic) and showing the validator's exit code and
findings change accordingly, with the real `schema.json` untouched.

## Diagnostic codes

Shared with `driftcheck.py` (same code, same meaning):

| Code | Meaning |
|---|---|
| `TRANSCRIPT_HAS_NO_COMMAND_RECORDS` | zero `=== $ command ===` headers anywhere in the file |
| `TRANSCRIPT_RECORD_HAS_NO_EXIT` | a record has no line matching `^\s*exit=(-?\d+)\s*$` |
| `TRANSCRIPT_SHOWS_TEST_FAILURE` | a `FAILED (...)` verdict line appears anywhere in the file |

New in this tool:

| Code | Severity | Meaning |
|---|---|---|
| `TRANSCRIPT_RECORD_EXIT_MALFORMED` | error | a line resembles `exit=<int>` (case-insensitive, `:` or `=`) but does not match the strict pattern — e.g. `Exit=0`, `exit=1.5`, `exit: 0` |
| `TRANSCRIPT_RECORD_DUPLICATE_EXIT` | **info** | a second (or later) *valid* `exit=` line appears in a record. FORMAT.md defines first-wins as correct, so this is reported for visibility, not treated as an error |
| `TRANSCRIPT_HEADER_MALFORMED` | error | a line resembles `=== $ command ===` (some `=` run, a literal `$`, another `=` run) but does not match the strict pattern — e.g. missing the space after the first `===` |
| `TRANSCRIPT_RECORD_MISSING_RAN_LINE` | error | a record whose command looks like a test command (matches the `test_command` heuristic) has no `Ran N tests in ` line |
| `TRANSCRIPT_RECORD_MISSING_VERDICT` | error | a record whose command looks like a test command has no `OK`/`FAILED` verdict line |
| `TRANSCRIPT_RECORD_RAN_LINE_MALFORMED` | error | a line resembles the unittest summary line (`ran`/`Ran` + `tests`) but does not match the strict pattern — e.g. lowercase `ran 9 tests in 0.05s` |
| `TRANSCRIPT_RECORD_VERDICT_BEFORE_RAN` | error | **the misordering check** — see below |
| `TRANSCRIPT_PREAMBLE_EXIT_LOOKALIKE` | **info** | an `exit=`-shaped (or near-miss) line appears before the first header, i.e. in preamble, which FORMAT.md ignores entirely — usually means a header was forgotten above it |
| `TRANSCRIPT_FILE_UNREADABLE` | error | the file could not be opened or decoded as UTF-8 |

## Ordering: what "misordered" means here, and what it deliberately does not

FORMAT.md is explicit: *"No ordering requirement between records."* The task
that produced this tool asked for detection of "misordered content," and the
honest way to reconcile the two is to scope misordering to **within a single
record**, never between records.

`valid_out_of_order_records.txt` (a fixture; see
`TestNamedScenarios.test_inter_record_ordering_is_never_flagged`) has a
`cleanup.py` record before a `setup.py` record — nothing in FORMAT.md makes
that wrong, and the validator raises nothing.

Three within-record orderings were considered while building this tool; two
turned out not to be real states a validator can detect, and are named here
so it's clear they were considered and rejected, not overlooked:

1. **"`exit=` appears before its own header"** — not representable. The
   grammar is `record := header body`; anything before a header is either
   preamble (before the first header) or another record's body (between two
   headers). A line can never be "before its own header" without also being
   inside a different record or in preamble — there is no third state to
   detect.
2. **"a second header appears before the first record's body closes"** —
   also not representable, for the same reason: a header **is** what closes
   the previous body. `body := line* up to the next header or EOF` — the
   grammar has no notion of a body that a header interrupts; encountering a
   header always, unconditionally, starts a new record.
3. **A verdict line printed before its own `Ran N tests` line** — this one
   *is* representable and checkable: real `unittest` output always prints
   `Ran N tests in ...` before its `OK`/`FAILED` verdict. Seeing them in the
   reverse order within one record's body is strong evidence the transcript
   was edited or reassembled after capture, not that it was ever genuine
   `unittest` output. This is `TRANSCRIPT_RECORD_VERDICT_BEFORE_RAN`, the one
   ordering check this tool actually performs.

No inter-record ordering rule is invented or enforced anywhere in this tool.

## Findings format

Every finding carries `file`, `line`, `text` (the specific offending or
anchoring line, truncated at 300 chars), `code`, `severity`, and an optional
`detail` object with code-specific structured data (e.g. the first and
duplicate values for `TRANSCRIPT_RECORD_DUPLICATE_EXIT`, or the verdict/Ran
line numbers for `TRANSCRIPT_RECORD_VERDICT_BEFORE_RAN`).

For findings about an *absence* (no exit line, no Ran line, no verdict, or
zero records file-wide) there is no single offending line to point at by
definition, so `line`/`text` anchor to the most specific enclosing context:
the record's own header line/text for record-scoped absences, or line 1 of
the file for file-scope absences (`TRANSCRIPT_HAS_NO_COMMAND_RECORDS`). This
convention is applied uniformly and is exercised by
`TestFixtureValidation`, which asserts every finding it sees has a `line
>= 1` integer and non-empty `code`.

## Determinism and relocation

The report has no timestamps, no durations, no absolute paths — file paths
in `findings` and `coverage` are always relative to `--root` (e.g.
`crosspath-runner/captured_output.txt`), never the checkout's absolute
location, and JSON is emitted with sorted keys, compact separators, and
`ensure_ascii=True` (`canonical_json`, mirroring `driftcheck.py`'s own).

Three runs of the exact command used to produce the committed
`validation_report.json`, run from the repository root:

```
python3 transcript-schema/validate_transcript.py --root . -o /tmp/run1.json      # in place, run 1
python3 transcript-schema/validate_transcript.py --root . -o /tmp/run2.json      # in place, run 2
cp -r /tmp/build_5/repo /tmp/build_5/relocated_final_check                       # relocate to a
cd /tmp/build_5/relocated_final_check                                            # differently-named
python3 transcript-schema/validate_transcript.py --root . -o /tmp/run3_relocated.json  # absolute path
sha256sum /tmp/run1.json /tmp/run2.json /tmp/run3_relocated.json
```

produced:

```
8e1f6efc4bcc584757fb215802b0578c5f6aa735526c5335f37dddc53901ccc1  run1.json
8e1f6efc4bcc584757fb215802b0578c5f6aa735526c5335f37dddc53901ccc1  run2.json
8e1f6efc4bcc584757fb215802b0578c5f6aa735526c5335f37dddc53901ccc1  run3_relocated.json
```

All three identical. `TestDeterminism` in the test suite checks the same
property (two in-place runs plus one relocation to a `tempfile.mkdtemp()`
directory with an unrelated name) against the fixture tree on every
`python3 -m unittest` run, so this is exercised automatically, not just
demonstrated once by hand.

`captured_output.txt` in this directory records the actual commands and
actual output that produced these numbers, plus a NOTE at the end (prose
only, no header/exit=/Ran/verdict lines, so it changes nothing about how the
transcript itself is parsed) documenting the one-finding difference between
the report generated *while this file was still being written* and the
final, committed one — the same self-reference handling
`transcript-drift/captured_output.txt` and `finalize.py` use, for the same
reason: the report-generation command is itself one of the recorded
commands, so at the instant it runs, its own `exit=` line for that command
does not exist yet.

## Dogfooding

`validate_transcript.py transcript-schema/captured_output.txt` exits `0`.
This tool's own transcript is written in, and validates against, the exact
format it checks — it is included as one of the 47 directories scanned by
`validation_report.json` (see below) and shows zero findings there too.

## What was actually found across the repository

`validation_report.json` is the committed artifact from running

```
python3 transcript-schema/validate_transcript.py --root . -o transcript-schema/validation_report.json
```

from the repository root. **Exit 1, status `invalid`, 115 findings across
47 directories** (46 tool directories plus `transcript-schema` itself).

| Code | Count |
|---|---|
| `TRANSCRIPT_RECORD_HAS_NO_EXIT` | 48 |
| `TRANSCRIPT_PREAMBLE_EXIT_LOOKALIKE` (info) | 50 |
| `TRANSCRIPT_HAS_NO_COMMAND_RECORDS` | 7 |
| `TRANSCRIPT_RECORD_DUPLICATE_EXIT` (info) | 5 |
| `TRANSCRIPT_RECORD_EXIT_MALFORMED` | 3 |
| `TRANSCRIPT_RECORD_MISSING_RAN_LINE` | 1 |
| `TRANSCRIPT_RECORD_MISSING_VERDICT` | 1 |
| everything else | 0 |

These are real findings, not synthetic ones — every one of the following was
inspected against the actual file before being written down here:

- **7 directories have zero real headers**: `bundle-index`,
  `crosspath-runner`, `doc-validator`, `link-integrity`,
  `nondeterminism-scanner`, `preflight`, `weak-assertion-scanner` — the same
  "prose-style, pre-migration transcript" pattern `transcript-drift/README.md`
  names for `crosspath-runner` (one of its own four). The other three
  `transcript-drift/README.md` names — `limitations-probe`,
  `path-collision-scanner`, `regression-checker` — are **not** in this
  list, meaning the migration referenced in the task brief (commit
  `d4fe654`) reached those three between when that README was written and
  this commit, but the remaining seven transcripts still predate it. This
  tool finds the problem independently of any README claim, purely from
  the transcript's own bytes.
- **`exit-harness/captured_output.txt`** has three records whose exit line
  is written as `exit(all-match)=0`, `exit(some-fail)=1`,
  `exit(harness-error)=2` — a deliberately descriptive style that does not
  match FORMAT.md's `exit=<int>` at all. `TRANSCRIPT_RECORD_EXIT_MALFORMED`
  catches this precisely (pinpointing the exact malformed line), where
  `driftcheck.py` would only ever report the less specific
  `TRANSCRIPT_RECORD_HAS_NO_EXIT` for the same three records.
- **`index-generator/captured_output.txt` line 427** pipes a unittest
  invocation through `grep stale_catalogued`, which filters out the `Ran N
  tests` and `OK` lines the grep doesn't match — so that record is missing
  both required lines for a genuine reason (the pipe ate them), not a typo.
  `TRANSCRIPT_RECORD_MISSING_RAN_LINE` and `TRANSCRIPT_RECORD_MISSING_VERDICT`
  both correctly fire.
- **`contradiction-detector`** has 4 records (and `wallet-reconciler` has 1)
  where `exit=N` appears twice with the same or different value
  (informational only, first value wins per FORMAT.md, and the report says
  so in each finding's `detail`).
- **`TRANSCRIPT_SHOWS_TEST_FAILURE` is zero** and
  **`TRANSCRIPT_RECORD_VERDICT_BEFORE_RAN` is zero** and
  **`TRANSCRIPT_HEADER_MALFORMED` is zero** across the real repository —
  genuine negative results, backed by the fact that all three fire on
  purpose-built fixtures in `test_validate_transcript.py`
  (`TestEveryDiagnosticCodeIndividually`), so "zero" here means "checked and
  clean," not "the check never fires."
- All 50 `TRANSCRIPT_PREAMBLE_EXIT_LOOKALIKE` findings come from exactly
  those same 7 headerless directories (their entire file counts as
  "preamble" under the grammar, so every `exit=N`-shaped line in it is a
  preamble lookalike) — informational, not fatal, but a real, corroborating
  signal of which transcripts predate the migration referenced in the task
  brief.

### A note on repo-wide counts

`validation_report.json` and the numbers above describe the tree **as
committed at the time this file was generated**. Neither this README nor
`test_validate_transcript.py` hardcodes "46 directories," "47 directories,"
or any of the counts above as an assertion anywhere in the test suite —
every test either uses a self-contained fixture tree built by
`make_fixtures.py`, or (for `TestCLIRoot`/`TestDeterminism`) derives its
expectation from data the same test produced. If a 47th tool directory
lands, or an existing transcript is migrated, the *test suite* keeps
passing unchanged; only the *committed report* — a point-in-time snapshot —
goes stale, exactly like `transcript-drift/drift_report_2026-08-04.json`
already does, and re-running the one command above regenerates it.

## Limitations

1. **The "test command" heuristic is a single regex, `\bunittest\b`, matched
   against the header text.** It will not recognise `pytest`, a custom test
   runner, or any command that runs tests without the literal word
   "unittest" in it — such a record's missing `Ran`/verdict lines will
   never be flagged, a false negative. Conversely, a command like
   `grep unittest test_names.txt` would be wrongly classified as a test
   command purely because the word appears in an argument, potentially
   producing a false `TRANSCRIPT_RECORD_MISSING_RAN_LINE` /
   `TRANSCRIPT_RECORD_MISSING_VERDICT` pair for a command that never ran
   tests at all. No repository transcript triggers this false positive
   today, but nothing prevents one from doing so tomorrow.

2. **`index-generator/captured_output.txt` line 427 is a real, unavoidable
   false-negative-adjacent case for a different reason**: piping a real
   `unittest -v` command through `grep stale_catalogued` is a genuine,
   intentional, correctly-flagged violation (see "What was actually found"
   above) — but it also demonstrates that this validator cannot distinguish
   "the tool never ran the tests" from "the tool ran the tests and something
   downstream ate the output," because FORMAT.md's grammar only sees the
   bytes that ended up in the file. A transcript author could exploit this
   exact mechanism (pipe through `grep -v Ran` deliberately) to hide a real
   test failure's `Ran`/verdict lines without the header disappearing, and
   this validator has no way to tell that apart from an honest mistake.

3. **`TRANSCRIPT_RECORD_VERDICT_BEFORE_RAN` only catches reordering that
   survives as two *separately matching* lines.** If a transcript author
   (or a lossy editing tool) merges the `Ran N tests in ...` line and the
   verdict onto one line, or deletes the `Ran` line entirely and leaves only
   the verdict, this check sees zero evidence of reordering — it would
   instead correctly report `TRANSCRIPT_RECORD_MISSING_RAN_LINE`, a
   different and arguably less alarming code than "this was edited after
   capture." The distinction between "this record never had a Ran line" and
   "this record had a Ran line that got deleted after the fact" is not
   representable from the bytes alone, for either driftcheck.py or this
   tool.

4. **Command matching for the `test_command` heuristic and near-miss regexes
   is textual, not semantic**, exactly like `driftcheck.py`'s own command
   normalisation (`driftcheck.py`'s Limitation 4). A `header_lookalike` that
   happens to appear inside genuine free-form command *output* (for example
   a tool that legitimately prints a line shaped like `==$ x ==` as part of
   its own report) produces a `TRANSCRIPT_HEADER_MALFORMED` finding this
   validator cannot distinguish from an actually-broken header. This has not
   been observed in the real repository (`TRANSCRIPT_HEADER_MALFORMED` is
   `0` in `validation_report.json`), but the check has no way to rule it out
   for a tool whose real command output happens to look like that.

## Tests

**194 tests, `OK`, exit 0.** CPython 3.11.15, Linux x86_64.

```
cd transcript-schema && python3 -m unittest test_validate_transcript -v
```

or, from the repository root: `python3 -m unittest discover -s transcript-schema -v`.

Coverage, by section (see the file for the exact class/test names):

- `TestPatternsFromSchema` (51 generated tests) — every regex loaded from
  `schema.json`, positive and negative cases, including near-misses for
  each of the four `*_lookalike`/`test_command` patterns.
- `TestFixtureValidation` (25 generated tests) — one per fixture file,
  asserting the *exact* set of codes produced and that every finding has a
  well-formed `file`/`line`/`text`/`code`.
- `TestNamedScenarios` + `TestEveryDiagnosticCodeIndividually` — the
  brief's named cases explicitly: empty file, preamble-only, CRLF, BOM,
  unicode, negative exit values, `exit=` twice in one record (first wins,
  with the actual first/duplicate values asserted), a header-like line
  inside a body, duplicate header commands (shown *not* to be an error),
  inter-record ordering (shown *not* to be an error), each of the 11
  diagnostic codes individually reachable, and each of this tool's own exit
  codes 0/1/2.
- `TestCLISingleFile` / `TestCLIRoot` — subprocess-level CLI behaviour:
  `-o`, multi-file invocations, `--root` discovery (skips hidden dirs,
  `__pycache__`, and non-directory entries; reports directories with no
  transcript separately), setup errors.
- `TestDeterminism` — two in-place runs plus one relocated run, byte-for-byte
  identical; no absolute paths or timestamp-shaped keys in the report;
  findings sorted.
- `TestSchemaDrivesValidator` (6 tests) — editing a copy of `schema.json`
  (looser header regex, downgraded/upgraded severities, narrowed
  test-command heuristic, bumped `schema_version`) measurably changes the
  validator's exit code and/or reported `transcript_schema_version`,
  proving the schema is read, not duplicated.
- `TestSchemaLoading` (13 tests) — every way `load_schema`/`compile_patterns`
  can reject a broken schema file.
- `TestMakeFixturesGenerator` (14 tests) — base64 round-trip, binary-mode
  CRLF/BOM/invalid-UTF-8 byte preservation on disk, the empty directory
  fixture existing and being empty, `--verify` (both as a function call and
  as a subprocess), and `diff -r` agreement between two independent
  `generate()` calls.
- `TestReportUtilities` — `canonical_json`, `diagnostic_counts`,
  `any_error_severity`, `severity_of`, `truncate`, `split_lines`.
- `TestStdlibOnly` — AST-based import check for both `.py` files (no
  `pip`-installed dependency can be added without this test catching the
  new `import`).

No test hardcodes a repository-wide count; see "A note on repo-wide counts"
above.
