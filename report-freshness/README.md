# report-freshness

A stdlib-only Python 3 CLI that checks whether committed report artifacts
are still trustworthy. For **regenerable** entries -- current-state
snapshots -- it regenerates them with each report's own offline
generation command and compares the bytes against the committed report
**byte-for-byte**. For **pinned** entries -- point-in-time evidence that
must never be touched -- it verifies the committed file still exists and
records its SHA-256 and byte length, without ever running a generator
against it. It answers one of two questions per manifest entry,
depending on `kind`:

- `kind: "regenerable"` -- *does this committed report still reproduce?*
- `kind: "pinned"` -- *is this committed evidence still there, unaltered?*

## Files

| File | Purpose |
|---|---|
| `freshness.py` | the checker |
| `manifest.json` | the 5 tool/report pairs this checker covers (3 regenerable, 2 pinned), and how to regenerate each regenerable one |
| `test_freshness.py` | 193 unit/integration tests (`unittest`, stdlib only) |
| `freshness_report.json` | committed, unedited output of `python3 freshness.py -o freshness_report.json` run from this directory against the real repository |
| `captured_output.txt` | real terminal output of the test run and the two-location proof, in the `=== $ ... ===`/`exit=` format from `transcript-drift/FORMAT.md` |

## Requirements

Python 3 standard library only: `argparse`, `hashlib`, `json`, `os`,
`subprocess`, `sys`, `tempfile`. No third-party packages, no network
access. Verified on stock `python3` (CPython 3.11.15 on Linux).

## Why this tool exists

This repository has already shipped stale reports that nobody caught
until someone happened to re-clone and re-run things by hand:

1. **`regression-checker/baseline_coverage_report.json`** enumerates every
   tool directory under the repository root and audits baseline coverage
   for each one. It was committed when the repository had 45 tool
   directories. A 46th directory, `claim-crosscheck/`, was added later,
   and the committed report stopped reproducing -- it no longer lists that
   directory's coverage state. Three tests in that same tool's suite that
   hardcoded "45 directories" / "22 baselined" also started failing, for
   the identical reason: they encoded a snapshot of repository state as
   if it were a constant.
2. **`claim-crosscheck/sample_run.json`** enumerates every tool
   directory's README/report pair and cross-checks claims. Adding
   `regression-checker/baseline_coverage_report.json` changed which
   report file is the *unambiguous* discoverable report for
   `regression-checker/` -- report discovery for that directory correctly
   became ambiguous/skipped -- and that changed `sample_run.json`'s
   committed bytes. The committed sample stopped reproducing the moment a
   sibling tool committed an unrelated-looking report file.

Both are real commits in this repository's history, not hypotheticals.
Building this checker produced a **third, live instance of the same
disease while it was being built**: adding `report-freshness/` itself
(this directory), and later `transcript-schema/` and
`adversarial-suite/`, changed what `regression-checker`, `claim-crosscheck`,
and `transcript-schema`'s own generators produce, simply by existing as
new tool directories the repo-enumerating tools now see. See "Two-location
proof" below for the real, observed states -- they are `stale`, not a
tuned "all clean" result.

All three are exactly the class of bug this tool is built to catch
mechanically instead of by accident: *a report that was correct when it
was committed, and is now silently wrong.*

## Not every committed report SHOULD regenerate, though

Two artifacts in this repository are the opposite problem: they are
**point-in-time evidence**, not current-state snapshots, and treating
them as regenerable would be actively wrong, not just imprecise:

- **`env-leak-scanner/leak_report_2026-08-04.json`** -- the date is
  *in the filename*. It is a scan of what the repository looked like on
  2026-08-04. Regenerating it against today's tree does not "refresh"
  it, it destroys the thing it was committed to record, and replaces it
  with an unrelated scan of an unrelated tree that happens to share a
  filename.
- **`transcript-drift/drift_report_after_migration.json`** -- this is the
  "after" half of a committed **before/after pair**
  (`drift_report_before_migration.json` / `drift_report_after_migration.json`)
  documenting one specific migration that ran at one specific commit. A
  before/after pair whose "after" half gets regenerated later, against an
  unrelated later tree, stops meaning anything -- it is no longer paired
  with the "before" it was committed to be compared against.

A freshness checker that flagged either of those `stale` would be worse
than useless: it would report a true, expected, permanent difference
(today's tree vs. a frozen historical snapshot) with the same "this
needs attention" signal as an actual regression, training reviewers to
either investigate a non-issue every time or -- worse -- to start
ignoring `stale` findings altogether, including the real ones. `kind`
exists so the manifest states this explicitly instead of the checker
guessing from file content: **`kind: "pinned"` means the generator is
never invoked, period.** See "What it does" and "States" below for how
that is enforced, and `TestEvaluateEntry.test_pinned_entry_generator_never_invoked_*`
/ `TestCLI.test_pinned_entry_generator_never_invoked_end_to_end` in
`test_freshness.py` for how it is tested: a pinned entry is pointed at a
generator that writes a marker file and exits loudly (77) if it is ever
run, and the tests assert that marker file never appears.

## What it does

For every entry in `manifest.json`, branch on `kind`:

**`kind: "regenerable"`:**

1. Confirm the tool directory (and the script the entry names inside it)
   still exists -- if not, state `tool_missing`, nothing else is
   attempted.
2. Run the entry's `generation.argv` command from `generation.cwd`, with
   the `{OUT}` placeholder substituted for a fresh path under a
   `tempfile`-created scratch directory (never the committed path, never
   inside the repository).
3. If the command could not be launched, timed out, exited with a code
   other than `expected_exit_code`, or produced no output file at all,
   state `generation_failed`.
4. Otherwise read the committed report (if any) and the regenerated
   bytes and compare them:
   - no committed report at all -> `missing`
   - bytes identical -> `match`
   - bytes differ -> `stale`

**`kind: "pinned"`:**

1. The generator is **never invoked** -- no subprocess, no tool/script
   existence check, nothing under `generation` is executed even if it is
   present in the manifest (it is provenance metadata only; see below).
2. Read the committed file.
   - it exists -> `pinned_present`, its SHA-256 and byte length are
     recorded
   - it does not exist -> `pinned_missing` -- **this is an error state**,
     not a shrug. Evidence that has vanished is a real problem, distinct
     from evidence that has (correctly, expectedly) never changed.

An `expected_exit_code` of `1` on a regenerable entry is not a failure.
Five of the manifest's regenerable entries (`claim-crosscheck`, `nondeterminism-scanner`, `regression-checker`, `transcript-schema` and `weak-assertion-scanner`) exit `1` by design because their
generators report real, expected findings against this repository
(un-baselined directories; one intentional discrepancy; real transcript
findings). The manifest records that expectation per entry, so "the
generator exited nonzero" and "the generator exited a *different* code
than documented" are distinguished explicitly -- only the second is
`generation_failed`.

## Usage

```
python3 freshness.py [--manifest FILE] [--root DIR] [-o FILE] [--timeout SECONDS]
```

- `--manifest FILE` -- path to the manifest (default: `manifest.json` next
  to `freshness.py`)
- `--root DIR` -- repository root that every manifest path
  (`generation.cwd`, `committed_report`, `inputs`) is relative to (default
  `..`, i.e. run this from inside `report-freshness/`)
- `-o, --output FILE` -- write the JSON report here instead of stdout
- `--timeout SECONDS` -- per-entry subprocess timeout, **regenerable
  entries only** -- pinned entries never invoke a subprocess, so this
  never applies to them (default `120`)

Run it from this directory against the real repository:

```
python3 freshness.py -o freshness_report.json
```

## manifest.json fields

`manifest.json` is `{"schema_version": 2, "entries": [...]}`. Every entry
is validated strictly at load time -- a malformed manifest is a setup
error (exit `2`), never silently ignored or partially applied.

| Field | Required | Meaning |
|---|---|---|
| `id` | yes | unique string identifying this entry (`tool:report-filename` by convention); output is sorted by this field |
| `tool` | yes | the tool directory's name; a single path component, no `/` |
| `kind` | yes | `"regenerable"` or `"pinned"` -- see "Not every committed report SHOULD regenerate, though" above. Governs everything below. |
| `committed_report` | yes | repository-relative path to the committed report this entry checks |
| `generation.argv` | required if `kind: "regenerable"`; optional (provenance only, never executed) if `kind: "pinned"` | the generation command as a JSON array of argv tokens (no shell involved). For a regenerable entry it must contain the literal token `{OUT}` somewhere -- that token is substituted with a fresh scratch path before the command runs. For a pinned entry `{OUT}` is not required (the command is never run, so nothing is ever substituted into it) |
| `generation.cwd` | required alongside `generation.argv` | repository-relative directory the command is run from (matches each tool's own documented usage, e.g. "run from this directory") |
| `expected_exit_code` | required if `kind: "regenerable"`; optional (and ignored) if `kind: "pinned"` | the generator's documented exit code for this exact invocation against this exact repository state (a JSON integer; booleans are rejected explicitly -- see "Booleans are not exit codes" below) |
| `inputs` | no (default `[]`) | repository-relative paths the generation is known to read besides the whole tree (documentation only, not used for caching/invalidation) |
| `description` | no (default `null`) | free-text note on what the tool/report pair does, and for pinned entries, why it is pinned |

For a pinned entry, `generation`/`expected_exit_code` exist purely as a
historical record of the command that originally produced the evidence
file -- useful for a reviewer who wants to know how it was made, without
this checker ever running it again. A pinned entry with no `generation`
key at all is equally valid; `env-leak-scanner`'s and
`transcript-drift`'s manifest entries both include it anyway, for that
provenance value.

### Booleans are not exit codes

In Python, `bool` is a subclass of `int`, so a naive
`isinstance(value, int)` check on `expected_exit_code` would accept a
manifest entry with `"expected_exit_code": false` and then compare it
equal to a real exit code of `0` (or `true` equal to `1`), silently
turning a real mismatch into an accepted match. `is_exit_code()` rejects
`bool` explicitly; a boolean there is a malformed manifest (exit `2`), not
a value that gets coerced. This applies to `expected_exit_code` on both
kinds -- even the optional one on a pinned entry, if present, is still
validated.

## States

| State | Kind | Meaning |
|---|---|---|
| `match` | regenerable | regenerated bytes are byte-identical to the committed report, and the exit code matched `expected_exit_code` |
| `stale` | regenerable | generation succeeded (expected exit code, output file produced) but the regenerated bytes differ from the committed report |
| `missing` | regenerable | generation succeeded but no committed report exists at the declared path |
| `generation_failed` | regenerable | the command could not be launched, timed out, exited with a code other than `expected_exit_code`, or ran to completion but produced no output file at all |
| `tool_missing` | regenerable | the tool directory named in `generation.cwd`, or the script the manifest names inside it, does not exist -- generation was never attempted |
| `pinned_present` | pinned | the committed evidence file exists; its SHA-256 and byte length are recorded. The generator was **not** invoked. |
| `pinned_missing` | pinned | the committed evidence file is gone. **This is an error state** -- deleted evidence is a real, distinct problem from "correctly frozen and unchanged". The generator was still **not** invoked; there is nothing to regenerate a pinned entry into. |

Every entry ends up in exactly one state, determined entirely by its
`kind` -- a regenerable entry can never be `pinned_present`/`pinned_missing`
and a pinned entry can never be `match`/`stale`/`missing`/
`generation_failed`/`tool_missing`. `committed_sha256` / `committed_bytes`
are always reported for every entry; `regenerated_sha256` /
`regenerated_bytes` are `null` for every pinned entry (nothing was ever
regenerated) and for a regenerable entry that never produced output.
`generator_invoked` is `true` only for a regenerable entry whose
generation command actually ran -- it is always `false` for pinned
entries and for regenerable entries that turned out `tool_missing`.

## Exit codes

| Code | Meaning |
|---|---|
| `0` | every manifest entry is `match` or `pinned_present` |
| `1` | ran to completion; at least one regenerable entry is `stale` or `missing` -- a real, checkable finding, nothing crashed |
| `2` | setup error: manifest missing/unreadable/malformed, `--root` not a directory, or `--output` could not be written; no entries were checked |
| `3` | at least one entry is `generation_failed`, `tool_missing`, or `pinned_missing` -- freshness/integrity of that entry could not even be determined, or committed evidence has vanished |

`3` outranks `1`, which outranks `0`: if anything couldn't be evaluated
at all, or if evidence is missing outright, that is reported ahead of
"merely" stale content.

## Path independence

`freshness_report.json` never contains an absolute path, a hostname, or a
timestamp. Concretely:

- Every path field in the output (`committed_report`, `generation_cwd`,
  `generation_argv`, `inputs`) is copied verbatim from `manifest.json`,
  which itself only ever contains repository-relative strings.
- `--root`'s *value* (an absolute path, since subprocesses need a real
  `cwd`) is used only to build filesystem paths for reading/executing; it
  is never written into the report.
- Subprocess stdout/stderr is discarded entirely, on purpose -- a
  traceback from a launch failure can contain the absolute path of the
  interpreter or script that failed, so it is never embedded. Failure
  reasons in the report are short strings this script constructs itself
  (`"command timed out after 30s"`, `"generation command exited 2,
  expected 0"`, exception *class names* like `FileNotFoundError` but
  never exception *messages*).
- `entries` is always sorted by `id`, and every JSON object is emitted
  with sorted keys (`canonical_json`), so the same manifest run against
  the same repository content produces byte-identical output regardless
  of where the repository checkout lives on disk.

See "Two-location proof" below for the actual verification of this
property, run twice from two differently-named absolute paths.

## make_fixtures.py

Not shipped. `test_freshness.py`'s fixtures are small synthetic "tool"
directories (a `gen.py` whose behavior is driven by a `ctrl.json` file
written by the test itself) built fresh under `tempfile` at the start of
each test, not committed binary/text blobs. There is nothing here for a
generator to round-trip byte-for-byte, so the base64/binary-mode/
empty-directory concerns that `make_fixtures.py` exists to solve
elsewhere in this repository do not apply to this tool's test suite.

## Tests

```
python3 -m unittest test_freshness -v
```

193 tests, `OK`. They cover, per the task brief's five named cases plus
the pinned/kind behaviour added afterward:

- **matching** -- `TestEvaluateEntry.test_state_match`,
  `TestRelocatedRepository.test_relocated_report_reflects_real_states`,
  `TestCLI.test_exit_0_all_match`
- **modified/stale** -- `TestEvaluateEntry.test_state_stale_modified`,
  `test_regenerable_entry_still_detects_modification_after_kind_split`,
  `TestClassifyRegenerablePure.test_stale_*`, `TestCLI.test_exit_1_stale`
- **missing** -- `TestEvaluateEntry.test_state_missing`,
  `TestClassifyRegenerablePure.test_missing_*`, `TestCLI.test_exit_1_missing`
- **failed-generation** -- unexpected exit code, generator that writes
  nothing, a bad interpreter (launch error), and a timeout, each as its
  own `TestEvaluateEntry` and `TestRunGeneration` test, plus
  `TestCLI.test_exit_3_generation_failed`
- **relocated-repository** -- `TestRelocatedRepository` builds a synthetic
  repo (with a mix of regenerable and pinned entries), copies it to a
  second, differently-named absolute path, runs the checker from both,
  and asserts byte-identical output, matching SHA-256, and that neither
  location's absolute path appears in either output
- **pinned -- never regenerated** -- `TestEvaluateEntry.test_pinned_entry_generator_never_invoked_even_when_tool_and_script_exist`
  and `test_pinned_entry_generator_never_invoked_when_missing_too` point a
  pinned entry at a "poison" generator that writes a marker file and exits
  77 if it is ever run, and assert the marker never appears;
  `TestCLI.test_pinned_entry_generator_never_invoked_end_to_end` repeats
  this through the real CLI subprocess. `TestRealManifestDogfood.test_pinned_entries_in_real_manifest_never_invoke_generator`
  asserts the same against the real manifest.
- **`pinned_missing` is an error** -- `TestEvaluateEntry.test_state_pinned_missing`,
  `TestCLI.test_exit_3_pinned_missing` (asserts exit code `3`, not `1`),
  `TestCLI.test_exit_3_pinned_missing_outranks_stale`
- also: malformed manifest (54 distinct structural defects, including 10
  pinned-entry-specific ones and 6 `kind`-specific ones, one test each,
  in `TestManifestValidationErrors`), a manifest entry pointing at a
  nonexistent tool directory or nonexistent script (`TestToolMissingReason`,
  `TestCLI.test_exit_3_tool_missing`), a generator that exits nonzero *as
  expected* vs. *unexpectedly*
  (`TestEvaluateEntry.test_expected_nonzero_exit_treated_as_legitimate_*`
  vs. `test_state_generation_failed_unexpected_exit`), and each of the
  four exit codes 0/1/2/3 individually (`TestCLI.test_exit_*`)

None of these tests hardcode a fact about the surrounding repository
(directory counts, specific committed byte content, current tool states)
except the fixed, author-controlled shape of `report-freshness/`'s own
`manifest.json` -- its entry count and its regenerable/pinned split are
things this project authored, not things observed by scanning a mutable
filesystem, so asserting "at least 3 regenerable and at least 2 pinned"
against it is not the anti-pattern this tool exists to diagnose. The
handful of tests that run against the *real* manifest and repository
(`TestRealManifestDogfood`) otherwise assert invariants -- "every state
is a valid state", "output is deterministic across two runs", "no
absolute path substrings appear", "no pinned entry ever invokes its
generator" -- never a specific state for a specific regenerable tool,
because asserting a specific state there is exactly the mutable-repo-state
anti-pattern this tool exists to diagnose (see "the irony" below).

## The irony: this tool's own report can go stale too

`freshness_report.json` is itself a committed report, generated by a
command (`python3 freshness.py -o freshness_report.json`), sitting next
to the code that produced it -- precisely the pattern this tool exists to
audit. Two deliberate choices limit the damage:

1. **The manifest does not include `report-freshness` checking itself.**
   `freshness.py` has no committed "expected output" of `freshness.py` to
   regenerate and compare -- there is no second-order entry
   `"report-freshness:freshness_report.json"` in `manifest.json`, of
   either kind. Adding a regenerable one would require freezing the exact
   repository state the report was generated against, which is precisely
   the moving target this tool is built to stop pretending is frozen.
   Adding a pinned one would just be wrong for a different reason:
   `freshness_report.json` is *not* point-in-time evidence like the two
   real pinned entries -- it is meant to track the current tree, so
   pretending it is frozen would hide real staleness instead of
   documenting it.
2. **The dogfood tests assert invariants, not values.** `TestRealManifestDogfood`
   checks structural properties of a *fresh* run against whatever the
   repository looks like right now, so `freshness_report.json` going
   stale (any regenerable tracked report changing state after this
   commit) fails a *future manual re-run comparison*, not the test suite
   shipped here. If a reviewer wants to know whether `freshness_report.json`
   itself is still fresh, the mechanical way to check is the same one
   this tool teaches for everything else: regenerate it
   (`python3 freshness.py -o /tmp/check.json`) and diff. That is
   deliberately left as a manual step rather than a self-referential
   manifest entry, for the reason above.

In practice, adding `report-freshness/` to this repository -- and, in the
same commit, `transcript-schema/` and `adversarial-suite/` -- is itself an
instance of motivating cases #1/#2 above: they are new tool directories,
and the five *regenerable* entries (`claim-crosscheck`, `nondeterminism-scanner`, `regression-checker`, `transcript-schema`, and `weak-assertion-scanner`) all enumerate every tool
directory, every README/report pair, or every `.py` file in the
repository as part of what they check.
The committed `freshness_report.json` in this directory reports their
*actual* state as of this commit, including wherever that self-effect
shows up -- see "Two-location proof" for the real, observed states
rather than a hoped-for "all clean" result. The two *pinned* entries are,
correctly, unaffected by any of this -- that is the entire point of
marking them pinned.

## Limitations

These are concrete failure modes, not "may produce false positives":

1. **A regenerable generator that embeds a timestamp, PID, or other
   run-varying value will always read as `stale`, forever, even when
   nothing meaningful changed.** This checker has no concept of "expected
   to differ in this one field" -- it compares whole files byte-for-byte.
   None of the five regenerable tools in the shipped manifest do this
   (each was spot-checked by running it twice and hashing both
   outputs), but a manifest entry added later for a tool that does would
   need that tool fixed first (or would simply, correctly, never show
   `match`). Note that this is exactly the failure mode `kind: "pinned"`
   exists to sidestep for artifacts that are *supposed* to be frozen --
   the fix for a genuinely time-varying report is to mark it pinned, not
   to leave it regenerable and permanently red.
2. **A regenerable generator with side effects on the tool's own
   directory or on shared state runs those side effects for real, every
   time the checker runs.** `run_generation()` executes the real command
   with a real subprocess; it does not sandbox filesystem or network
   access beyond redirecting `-o`/`{OUT}` to a scratch path. This is the
   limitation worth weighting most heavily -- it is not hypothetical, it
   is what actually happened while building this tool: `claim-crosscheck`
   and `transcript-schema` both re-scan the entire repository tree
   (READMEs, transcripts, report files) on every regeneration, so running
   this checker after adding new files anywhere in the repo changes what
   they find, which is real and correct, but means the checker is not a
   pure, side-effect-free "diff" in the general case -- it is only as
   side-effect-free as each manifest's underlying regenerable tool.
   **Moving `env-leak-scanner` to `kind: "pinned"` removes the worst
   instance of exactly this problem**: its generator scans the *entire*
   repository tree for leaked paths/hostnames, including this very
   directory's own files, so every commit to anywhere in the repo would
   have changed its regenerated output. Pinning it doesn't just fix the
   "point-in-time evidence" issue above -- it also means this checker no
   longer triggers that tool's full-repository side effect on every run.
3. **A report whose generation requires network access, unavailable
   credentials, or non-deterministic external state cannot be usefully
   entered as `kind: "regenerable"` at all** -- this repository's hard
   "no network" constraint made that moot for tool selection here, but
   nothing in `freshness.py` detects or flags "this failed because of the
   network" specially; it would simply, indistinguishably, show
   `generation_failed`. (It also cannot always be entered as `kind:
   "pinned"` either -- pinned only fits evidence that is *supposed* to be
   frozen; a report that is supposed to reflect live external state but
   can't be regenerated offline has no good home in this manifest at
   all.)
4. **Comparison is exact-byte-only; it cannot tell "meaningfully
   equivalent JSON" from "different bytes".** A report re-serialized with
   different key order, different float formatting, or a trailing
   newline added/removed is `stale` even if a JSON-level diff would show
   no semantic change. This is intentional (byte-identity is the whole
   point per the task brief) but it does mean a regenerable tool whose
   generator's *formatting* is non-deterministic (unsorted dict iteration
   in an older Python, for example) will never show `match` even though
   its *content* might be fine every time.
5. **`{OUT}` must appear as its own argv token; the checker does not
   parse or rewrite shell syntax.** A regenerable tool whose documented
   command redirects with `> file` instead of `-o file` (shell
   redirection) is not directly expressible in `generation.argv` (a real
   `execve` argv list, no shell) and would need a small wrapper script,
   which none of the five regenerable manifest entries here require.
6. **`kind` is a manual, human judgement call, and this checker cannot
   verify it is the right one.** Nothing stops a manifest author from
   marking a genuinely time-varying, should-be-regenerable report as
   `pinned` to silence real staleness findings, or from marking genuinely
   frozen evidence as `regenerable` and then being surprised when it
   reads `stale` forever. `env-leak-scanner`'s and `transcript-drift`'s
   `pinned` classification here is justified in the manifest's own
   `description` field (a dated filename; one half of a committed
   before/after pair) precisely because that justification has to be
   made in prose, by a person, and reviewed -- the checker enforces the
   *consequences* of the classification (never regenerate a pinned
   entry; always regenerate a regenerable one), not the classification's
   correctness.

## Two-location proof

`freshness.py` was run from the checked-out repository, then from a
second copy of the entire repository at a **differently-named** absolute
path, to prove the JSON output is identical and contains neither
location's path. Full commands and output are in `captured_output.txt`;
summary:

```
sha256(freshness_report.json, run from the repository checkout)     = 5089032f5e25e7d78baf7e3c20f6c9dadbc111816cec43d105c649492bd42ac7
sha256(freshness_report.json, run from a full copy of the repository
       at a second, differently-named absolute path)                = 5089032f5e25e7d78baf7e3c20f6c9dadbc111816cec43d105c649492bd42ac7
```

Identical. `diff` between the two output files reports zero differences,
and a `grep` for each location's own absolute path against both output
files matches nothing in either file (`grep -c` prints `0` for every
file and exits `1`, meaning "no match found" -- the desired result). Full
commands and raw output are in `captured_output.txt`, including two
`sha256sum` records of the five *other* committed reports this manifest
reads (`regression-checker/baseline_coverage_report.json`,
`claim-crosscheck/sample_run.json`, `transcript-schema/validation_report.json`,
`transcript-drift/drift_report_after_migration.json`,
`env-leak-scanner/leak_report_2026-08-04.json`) taken immediately before
and immediately after running `freshness.py` --
byte-identical in both records, proving this checker never wrote to a
single committed report anywhere in the repository; it only ever reads
them and regenerates into a `tempfile.TemporaryDirectory()` it creates
itself.

**Real observed states, as of this commit** (see `freshness_report.json`
in this directory for the full, committed report):

| Entry | Kind | State |
|---|---|---|
| `regression-checker:baseline_coverage_report.json` | regenerable | `stale` |
| `claim-crosscheck:sample_run.json` | regenerable | `stale` |
| `transcript-schema:validation_report.json` | regenerable | `stale` |
| `env-leak-scanner:leak_report_2026-08-04.json` | pinned | `pinned_present` |
| `transcript-drift:drift_report_after_migration.json` | pinned | `pinned_present` |

Overall exit code: `1` (at least one regenerable entry is `stale`; none
is `generation_failed`/`tool_missing`/`pinned_missing`, so it is not `3`).

This is an honest result, not a placeholder -- adding `report-freshness/`,
`transcript-schema/`, and `adversarial-suite/` to the repository in the
same commit is itself a live instance of the motivating cases above: all
five regenerable entries' generators enumerate tool directories or scan
README/report/transcript/source files across the whole repository, and
now see three new directories that did not exist when their committed
reports were generated. The two *pinned* entries are, correctly,
completely unaffected -- `pinned_present` in both cases, exactly as
before this commit and exactly as they will remain regardless of how
many more directories are added later, because their generators are
never invoked. See "The irony" above for why the regenerable entries
changing state is expected and does not indicate a bug in this checker
-- it indicates the checker is doing exactly its job.
