# report-impact-predictor

Answers one question, before you make the change:

> I am about to touch these files. Which committed reports in this repository
> does that invalidate, and why?

Stdlib-only Python 3. No third-party packages, no network, no executable
files. `predict_impact.py` reads nothing from the filesystem except the map
you name, so the answer is a pure function of `dependency_map.json` and the
paths you pass in — you can ask about a file that does not exist yet.

## Why this exists

Several tools here commit a report **about other directories**, and a
committed check compares that report against a fresh computation. Change a
file the report counted, and a test in a directory you never opened turns red.
Three of those relationships are load-bearing and none of them is discoverable
by reading the directory you are editing:

| Pin | Where | The trap |
|---|---|---|
| argparse **line numbers** | `doc-validator/option_report.json` | Adding one top-level `import` to any tool's CLI shifts every option below it. The option itself is unchanged. |
| transcript **record counts** | `index-generator/pipe_classification_report.json` | `total_command_records` (548) and `transcript_files_scanned` (51) are summed over every `captured_output.txt` **exactly one level** under the root. Name a new evidence file `captured_output.txt` and both move; name it anything else and they do not. |
| README numbers re-derived from a report | `weak-assertion-scanner/README.md` | `files_scanned` and `tests_scanned` are quoted in prose, and the tool's own regen test recomputes them. Adding a test module means hand-editing the README, not just regenerating the report. |

## Usage

```
python3 predict_impact.py PATH [PATH ...]
python3 predict_impact.py --stdin
python3 predict_impact.py PATH [--map FILE] [--output FILE]
```

- `PATH` — repository-relative changed paths. `./a/b.py`, `a//b.py`, `a\b.py`
  and `a/c/../b.py` are all one path.
- `--stdin` — read the paths from standard input, one per line. Pair it with
  `git status --porcelain -uall | sed -E 's/^...//; s/.* -> //'`, not
  `git diff --name-only` — see below. Cannot be combined with positional paths.
- `--change-kind {add,remove,edit,unknown}` — what kind of change these paths
  represent. Default `unknown`, which is the **conservative** reading: an edge
  counts as `certain` only if it holds for an add, a remove and an edit alike.
  Most edges here are exact for an add or a remove and approximate for an
  in-place edit, so passing `--change-kind add` is what turns the counting
  edges `certain`.
- `--gone-dir NAME` — a top-level directory this change **deletes**.
  Repeatable. A path list cannot tell "one file removed" from "the whole
  directory removed", and the difference decides whether the two
  directory-counting reports move, so it has to be said.
- `--new-dir NAME` — treat this top-level directory as one the change
  *creates*, even though the committed map already lists it. Repeatable. The
  map describes the tree **as committed**, so without this a directory cannot
  ask about its own creation — see "This tool predicted its own delivery".
- `--map FILE` — dependency map to use. Default: `dependency_map.json` next to
  the script.
- `--output FILE` — write the report here instead of stdout. The bytes are
  the same either way.

### Exit codes

| Exit | Meaning |
|---|---|
| `0` | no report is impacted |
| `1` | at least one report is impacted — the finding, not a failure |
| `2` | usage error: bad flags, no paths, an absolute path, a path that escapes the root, or a missing/malformed map |

## Exact rerun commands

Run these from the **repository root**. `predict_impact.py` finds its map next
to itself, so it works from anywhere.

```
( cd report-impact-predictor && python3 -m unittest test_predict_impact -v )
P=report-impact-predictor/predict_impact.py
python3 $P link-integrity/test_probe_module.py --change-kind add  ; echo "exit=$?"
python3 $P docs/notes.md --change-kind add                        ; echo "exit=$?"
python3 $P shebang-mode/notes.txt                                 ; echo "exit=$?"
python3 $P regression-checker/baselines.json --change-kind edit    ; echo "exit=$?"
python3 $P transcript-schema/schema.json --change-kind edit        ; echo "exit=$?"
python3 $P env/test_probe.py --change-kind add                    ; echo "exit=$?"
python3 $P build/notes.md --change-kind add                       ; echo "exit=$?"
python3 $P report-impact-predictor/sample_runs.txt                ; echo "exit=$?"
git status --porcelain -uall | sed -E 's/^...//; s/.* -> //' | python3 $P --stdin ; echo "exit=$?"
ls report-impact-predictor | sed 's|^|report-impact-predictor/|' | python3 $P --stdin --change-kind add --new-dir report-impact-predictor ; echo "exit=$?"
find sortkey-detector -type f | sort | python3 $P --stdin --change-kind remove --gone-dir sortkey-detector ; echo "exit=$?"
python3 $P a/y.py > run1.json ; python3 $P ./a//y.py > run2.json ; cmp run1.json run2.json && echo BYTE-IDENTICAL
python3 $P /etc/passwd                                            ; echo "exit=$?"
python3 $P                                                        ; echo "exit=$?"
```


`run1.json` / `run2.json` are scratch artifacts of that block and are not
shipped here. `sample_runs.txt` has the real terminal output of every command
in that block, each with its own exit code captured directly rather than
through a pipe.

Three things about that pipeline, each of which produced a wrong answer
before it was fixed:

- `git diff --name-only` does **not** list untracked files, so a brand-new
  tool directory — the case this repository produces most often — is
  invisible and the answer comes back almost empty.
- Plain `git status --porcelain` collapses an untracked directory to a single
  entry ending in `/`. On this delivery that yields the depth-0 path
  `report-impact-predictor`, which matches no rule, and the answer names one
  of the seven reports that actually moved. `-uall` expands it to the twelve
  real paths.
- A rename arrives as `R  old -> new` on one line, so `cut -c4-` alone yields
  one nonsense path. The `sed` above takes the destination. (Renaming
  `link-integrity/link_integrity.py` turns `doc-validator`'s gate red, because
  `option_report.json` records each option site's *path*.)

The plain pipeline answers "what did I touch". It cannot answer "what happens when this directory comes into existence or goes away", because the committed map describes the tree as committed -- for that, add `--change-kind add --new-dir NAME` or `--change-kind remove --gone-dir NAME`. `sample_runs.txt` shows all three side by side on this very delivery: the plain pipeline names 5 reports and none `certain`, the `--new-dir` form names all 7 with 5 `certain`.

### Expected results

| step | result |
|------|--------|
| tests | **92 tests, OK**, exit 0 |
| `link-integrity/test_probe_module.py --change-kind add` | 6 impacted, 3 `certain`, exit **1** |
| `docs/notes.md --change-kind add` | 3 impacted, 2 `certain`, exit **1** |
| `shebang-mode/notes.txt` | 0 impacted, exit **0** |
| `regression-checker/baselines.json --change-kind edit` | 2 impacted, 1 `certain`, exit **1** |
| `transcript-schema/schema.json --change-kind edit` | 2 impacted, 1 `certain`, exit **1** |
| `env/test_probe.py --change-kind add` | 5 impacted, 3 `certain`, exit **1** |
| `build/notes.md --change-kind add` | 3 impacted, 2 `certain`, exit **1** |
| `report-impact-predictor/sample_runs.txt` | 0 impacted, exit **0** |
| this directory's own files, `--change-kind add --new-dir report-impact-predictor` | 7 impacted, 5 `certain`, exit **1** |
| `cmp` of two spellings of one path | BYTE-IDENTICAL |
| `/etc/passwd` | usage error on stderr, exit **2** |

## `certain` versus `possible` — read this before trusting a line

Every impacted report carries a `confidence`:

- **`certain`** — at least one reason came from an edge that, for the
  `--change-kind` you asked about, always moves the report. In every scenario
  measured so far the `certain` set is a subset of the reports that actually
  moved, and in the four headline scenarios it is exactly that set.
- **`possible`** — every reason for this report came from an edge that
  over-approximates. It may be a false positive. The reason carries a
  `precision_note` naming the command that settles it.

The tool is deliberately biased toward over-reporting: a false positive costs
you one command, a false negative costs you a red gate in a directory you were
not looking at. Every over-approximating edge is documented in
`dependency_map.json` with a note recording what settles it; the three worth
knowing before you read any output are:

1. **`TOOL_CLI_PY` → `doc-validator:option_report.json`.** doc-validator
   records a depth-1 `.py` only if it builds an `ArgumentParser` **and** calls
   `parse_args` (`docval.py:850-861`), then records line numbers — so an edit
   strictly below every `add_argument` moves nothing. A path cannot see any of
   that. Settle it with `cd doc-validator && python3 -m unittest test_optioncheck`.
2. **`TOOL_README` / `TOOL_JSON` → `claim-crosscheck:sample_run.json`.**
   claim-crosscheck compares a README's numeric claims against the one report
   it discovers next to that README (`crosscheck.py:900-903`), so an edit moves
   the bytes only if the **outcome** of that comparison changes. Settle it with
   `cd report-freshness && python3 freshness.py`.
3. **`BASELINED_TOOL_TREE` → `regression-checker:baseline_coverage_report.json`.**
   `coverage_audit.py` re-runs every tool named in `regression-checker/baselines.json`
   and hashes its output, so a change inside one of those 23 directories moves
   the report only if that tool's own output changes. Measured true positive:
   appending a record to `dup-detector/records_dupes.json` took the report to
   `stale`. Measured false positive: adding a test module to `link-integrity`
   did not.

### Change kind matters, and `unknown` is deliberately weak

Most edges here are exact for an **add** or a **remove** and only
approximate for an in-place **edit**. `nondeterminism-scanner`'s
`files_scanned` moves whenever a `.py` appears or disappears — but editing a
`.py` that produces no findings moves nothing at all. Measured: appending a
comment line to `commit-claim-auditor/fixture/test_example.py` left
`report-freshness` at exit 0 and every enforcing test green.

So `--change-kind unknown` (the default) reports those edges as `possible`,
and only `--change-kind add` or `remove` promotes them. A chain is capped by
its weakest link: a report reached transitively from a `possible` source is
itself `possible`, because the regeneration that would rewrite the
intermediate artifact might never happen.

## Ground truth: the predictions were measured, not reasoned

Each scenario was run for real in a copy of the tree before it was written
down. `sample_runs.txt` has the transcripts.

**Adding `link-integrity/test_probe_module.py` (a new test module):**

| report | measured | predicted |
|---|---|---|
| `nondeterminism-scanner:self_scan_report.json` | freshness → `stale` | `certain` |
| `weak-assertion-scanner:self_scan_report.json` | freshness → `stale` | `certain` |
| `weak-assertion-scanner:README.md` | `test_weakassert_regen` FAILED (failures=2) | `certain` |
| `doc-validator:option_report.json` | `test_optioncheck` **OK** | `possible` |
| `claim-crosscheck:sample_run.json` | freshness → `match` | `possible` |
| `regression-checker:baseline_coverage_report.json` | freshness → `match` | `possible` |
| `index-generator:pipe_classification_report.json` | `test_pipe_classify` **OK** | not reported |

**Creating `docs/notes.md` (a new top-level directory with no README, no
`.py`, no transcript):**

| report | measured | predicted |
|---|---|---|
| `regression-checker:baseline_coverage_report.json` | freshness → `stale` | `certain` |
| `transcript-schema:validation_report.json` | freshness → `stale` | `certain` |
| `claim-crosscheck:sample_run.json` | freshness → `match` | `possible` |
| everything else | unmoved | not reported |

That second row is the one worth pausing on. A new directory with **no**
transcript moves `transcript-schema` and **not** `index-generator`, because
`validate_transcript.py:346-358` records the directory's *name* in
`directories_without_transcript` while `pipe_scan.py:60-61` skips a directory
with no transcript entirely. Two tools, the same depth-1 `captured_output.txt`
predicate, opposite answers to the same change.

### This tool predicted its own delivery

Adding this directory to the repository is itself a change, so it is the
fairest test available. Ground truth was measured by copying
`report-impact-predictor/` into a clean clone and running the gates; the
prediction was made from the committed map with
`--new-dir report-impact-predictor`.

| report | measured | predicted |
|---|---|---|
| `nondeterminism-scanner:self_scan_report.json` | freshness → `stale` | `certain` |
| `weak-assertion-scanner:self_scan_report.json` | freshness → `stale` | `certain` |
| `weak-assertion-scanner:README.md` | `test_weakassert_regen` FAILED | `certain` |
| `regression-checker:baseline_coverage_report.json` | freshness → `stale` | `certain` |
| `transcript-schema:validation_report.json` | freshness → `stale` | `certain` |
| `claim-crosscheck:sample_run.json` | freshness → `stale` | `possible` |
| `doc-validator:option_report.json` | `test_optioncheck` FAILED | `possible` |
| `index-generator:pipe_classification_report.json` | `test_pipe_classify` **OK** | **not reported** |

Seven predicted, seven observed, nothing missed and nothing spurious — and the
one report the tool declined to name is the one that did not move, because
this directory ships no `captured_output.txt`. Both `possible` lines turned
out to be true positives here, which is the point of the label: it means "run
the named command", not "this is noise".

Without `--new-dir` the same run reports five of the seven and misses
`regression-checker` and `transcript-schema`, because `report-impact-predictor`
is already in `known_tool_directories` in the committed map. That is not a bug
being papered over — it is the honest consequence of a map that describes the
tree as committed, and `TestNewDirOverride` pins both halves of it.

## Pinned reports are never impacted

`report-freshness` marks two entries `kind: "pinned"` —
`env-leak-scanner/leak_report_2026-08-04.json` and
`transcript-drift/drift_report_after_migration.json`. They are point-in-time
evidence and must **never** be regenerated. They appear in
`pinned_reports_never_impacted` and never in `impacted`, whatever changed.
`validate_map` refuses a map in which a pinned report declares a trigger,
because that combination is a contradiction rather than a preference.

## The map

`dependency_map.json` is data; `predict_impact.py` is a generic evaluator over
it, with no per-report logic in the source. Each report carries its artifact,
its producer, its `regenerate` command, what enforces it, and its `triggers` —
and each trigger carries its own `precision` and the note recording how that
was established. Precision lives on the **edge**, not the rule, because the
same rule is exact for one consumer and approximate for another.

`test_predict_impact.TestTheCommittedMapIsCurrent` re-derives
`known_tool_directories` from the tree, checks every artifact and producer
exists, checks the pinned set equals `report-freshness`'s pinned set, checks
every manifest entry appears in the map, and checks each `regenerate` command
matches the manifest verbatim. `test_every_certain_edge_declares_its_measurement`
refuses an edge marked `exact` whose note does not record a measurement.

## Direct, transitive, and why the closure terminates

A report is impacted **directly** when a changed path matches one of its
triggers. It is impacted **transitively** when regenerating an already-impacted
report rewrites an artifact that is itself a changed path for another report —
`nondeterminism-scanner/self_scan_report.json` is a `.json` one level down, so
rewriting it is a change `claim-crosscheck` scans. One edge is not
path-derivable at all and is declared explicitly under `propagations`:
weak-assertion-scanner's README is re-derived from its own report by a test.

The closure is a fixed point with an edge-seen set and a "already in this
chain" check, because `claim-crosscheck`'s own report sits inside
`claim-crosscheck`'s own scan scope — without both guards it would not
terminate. `test_the_widest_input_still_terminates` fires every rule at once.

## 3 limitations a reviewer should scrutinise

1. **The map is hand-built and can go stale.** It covers the seven
   `report-freshness` manifest entries — including the `inputs` one of them
   declares — plus the three test-enforced pins named above; it does not claim
   to cover every committed `.json` in the repository. Reports that nothing checks are deliberately absent — adding
   them would produce impact lines no gate would ever confirm.
   `TestTheCommittedMapIsCurrent` catches the drift that is mechanically
   detectable (a new pinned entry, a changed regeneration command, a vanished
   artifact, a new directory); it cannot catch a new *test-enforced* pin
   somebody adds in a tool directory. That one needs a human.
2. **`possible` is a real category, not hedging.** 15 of the 23 edges over-approximate. Modelling them exactly needs to read file *content* —
   which `.py` files build a parser, which `.json` a README claims about,
   whether a baselined tool's own output changed — and this tool reads no
   files by design. Treat a `possible` line as "run the named command", not as
   "this will break". The bias is deliberate and one-directional: the tool
   would rather send you to a command you did not need than stay silent about
   a gate you were about to break.
3. **It predicts staleness, not correctness.** A report can be stale for a
   reason this map does not model (an interpreter version, a changed
   environment variable, a generator whose own source you edited). Exit `0`
   means "no modelled report is impacted", which is a narrower claim than "your
   change is safe". `report-freshness` remains the authority; this tool tells
   you where to look before you have to ask it.

## What a first hostile review found, and what changed

Recording this because the failures are more instructive than the design, and
because a reader deserves to know which parts were wrong before they were
right. Each was reproduced in a clean clone before and after the fix.

1. **A false negative on the most ordinary edit in the repository.** The map
   gave `regression-checker:baseline_coverage_report.json` one trigger,
   `NEW_TOOL_DIR` — and then listed it under `unaffected_reports` for a
   change to `regression-checker/baselines.json`, the input the freshness
   manifest declares for that very entry. Measured: deleting one entry from
   `baselines.json` takes the report to `stale`; so does appending a record to
   `dup-detector/records_dupes.json`, because `coverage_audit` re-runs every
   baselined tool and hashes its output. Fixed by adding the `BASELINES_JSON`
   and `BASELINED_TOOL_TREE` rules and the `baselined_tools` list.
2. **One global ignore list, where the repository has five.** The map carried a
   single `ignored_directory_names` and cited `ndscan.py:63-66` for it — but
   that list has 11 names and not `.hg` or `.svn`, and `coverage_audit` and
   `validate_transcript` skip only dot names and `__pycache__`, so `build/`
   and `dist/` are ordinary directories to them. Measured: creating
   `build/notes.md` moves two reports while the tool answered exit 0 with an
   eight-way "unaffected" list; creating `.hg/x.py` moves
   `nondeterminism-scanner` while the tool said nothing. Fixed by giving each
   rule its own `ignores`, read off its own consumer.
3. **`certain` was not certain.** The counting edges are exact for an add or a
   remove, not for an in-place edit: appending a comment line to a `.py` that
   produces no findings moves nothing, and the tool called three reports
   `certain` for it. Fixed by putting `exact_for` on every edge, adding
   `--change-kind`, defaulting it to the conservative `unknown`, and capping a
   transitive chain's confidence at its weakest link.
4. **The evidence file did not contain the runs the README pointed at**, and
   one recorded exit code was a pipeline's, not the tool's. `sample_runs.txt`
   is now the whole rerun block with each exit code captured directly.
5. **The recommended workflow had the same blind spot the tool exists to
   close.** `git diff --name-only` omits untracked files, so a new tool
   directory produced an almost-empty answer. The README now says
   `git status --porcelain | cut -c4-` and explains why.

## What a second hostile review found

Round one's repairs were real, and none of them generalised. Round two found
that, which is the more useful result.

1. **The ignore lists were still transcribed, not imported.** `TEST_PY` cited
   `weakassert.py` and then carried `.eggs` (which weakassert does not ignore)
   and lacked `env` (which it does). Measured: creating `env/test_probe.py`
   moves `nondeterminism-scanner`, `regression-checker` and
   `transcript-schema` and leaves `test_weakassert_regen` **OK** — and the
   tool called weak-assertion-scanner's report `certain`. Fixed by generating
   every `ignores` list from the consumer module itself, and by
   `test_the_ignore_lists_equal_their_consumers`, which imports
   `weakassert`, `ndscan` and `docval` and compares.
2. **Only one of the two declared manifest inputs was covered.** Round one
   added a rule for `regression-checker/baselines.json` by name.
   `transcript-schema/schema.json` is declared the same way and had no rule;
   measured, bumping its `schema_version` takes `validation_report.json` to
   `stale`, and the tool listed it under `unaffected_reports`. Fixed
   generically: `declared_inputs` is read out of the manifest and the
   `DECLARED_INPUT` rule covers every entry, with
   `test_every_declared_manifest_input_is_covered` iterating the manifest
   rather than a hand-written list.
3. **`NEW_TOOL_DIR` claimed `exact_for: ["add", "remove"]` and could never
   fire on a remove** — it matches a top-level name *not* in
   `known_tool_directories`, and a directory being deleted is by definition
   still in there. Measured: `rm -rf sortkey-detector` takes five entries to
   `stale`, and `regression-checker` was reported `unaffected`. Fixed with
   `--gone-dir` and the `GONE_TOOL_DIR` rule.
4. **No rule connected a generator to its own report.** Changing a finding
   message in `weakassert.py` moves `self_scan_report.json`; the tool said
   `unaffected`. Fixed with `PRODUCER_SOURCE`, `over-approximate` because a
   comment-only edit moves nothing.
5. **`normalize_path` stripped trailing whitespace**, so
   `link-integrity/probe.py ` — a real, if unpleasant, filename that no
   scanner here treats as a `.py` — was answered as `link-integrity/probe.py`
   and came back `certain`. Now only a trailing newline is removed.
6. `sample_runs.txt` still did not match the README block command for
   command, and the README undercounted its own over-approximating edges.
   Both corrected.
