# sortkey-detector

## Purpose

`sortdetect.py` catches a specific, real class of bug: a sort key that is
*not a total order*. If a tool sorts a list of records by a handful of
"meaningful" fields and two records tie on all of them, Python's sort is
*stable* -- it silently falls back to preserving whichever order the
records happened to arrive in. That means the tool's output order becomes a
function of *input* order, even though the tool's own JSON output looks
perfectly sorted. The standard fix is to append the canonical JSON dump of
the whole record as a final tiebreak element, so ties can never actually
happen: two records are only ever "equal" for sorting purposes if their
serialised forms are byte-identical.

`sortdetect.py` finds this bug the only honest way: it runs the target tool
for real, multiple times, against multiple *deterministic permutations* of
the same underlying record set, canonicalizes each output, and checks
whether the output order is identical across every permutation. If it is,
the sort key is a total order (or at least behaves like one for every
record combination this tool's field model can produce). If it isn't, that
is the finding, reported with the concrete records that swapped position --
not just "order differs".

This tool never reads or reasons about a target's source code. Every
finding in this repository's `proof_report.json` and `captured_output.txt`
comes from literally subprocessing the target and diffing real bytes.

## Exact rerun command

From this directory:

```
python3 sortdetect.py --tool consolidate \
    --tool-path /path/to/consolidate.py \
    --fixture fixtures/consolidate \
    --permutations 6
```

Replace `--tool consolidate` / the fixture directory with `schema_check` +
`fixtures/schema_check`, or `ndscan` + `fixtures/ndscan`, to run the other
two built-in adapters. In this build environment the three real tools live
alongside this package's parent directory, so from here they can be reached
with relative paths:

```
python3 sortdetect.py --tool consolidate  --tool-path ../fixes/consolidate/consolidate.py               --fixture fixtures/consolidate  --permutations 6
python3 sortdetect.py --tool schema_check --tool-path ../fixes/schema-checker/schema_check.py            --fixture fixtures/schema_check --permutations 6
python3 sortdetect.py --tool ndscan       --tool-path ../fixes/nondeterminism-scanner/ndscan.py          --fixture fixtures/ndscan       --permutations 6
```

To run against the shipped pre-fix controls instead (see "Why the controls
exist" below):

```
python3 sortdetect.py --tool consolidate  --tool-path controls/consolidate_prefix.py  --fixture fixtures/consolidate  --permutations 6
python3 sortdetect.py --tool schema_check --tool-path controls/schema_check_prefix.py --fixture fixtures/schema_check --permutations 6
python3 sortdetect.py --tool ndscan       --tool-path controls/ndscan_prefix.py       --fixture fixtures/ndscan       --permutations 6
```

## How to point it at any tool

This ships two ways to run:

1. **Built-in adapters** (`--tool {consolidate,schema_check,ndscan}`): know
   how to invoke each tool's specific CLI shape, which fixture file/pointer
   holds the record list to permute, and which pointer in the tool's output
   holds the list whose order is compared. `--tool-path` always takes the
   real path to the target script on disk -- **this repository does not
   ship copies of the three real tools**; point `--tool-path` at wherever
   they live in your checkout (see rerun commands above), or use
   `SORTDETECT_CONSOLIDATE_PATH` / `SORTDETECT_SCHEMA_CHECK_PATH` /
   `SORTDETECT_NDSCAN_PATH` environment variables, which `test_sortdetect.py`
   also honours.

2. **Generic `--cmd` mode**, for any other stdlib CLI tool that reads a
   record set from a file and writes canonical JSON containing a sorted
   list:

   ```
   python3 sortdetect.py \
       --tool-path /path/to/your_tool.py \
       --cmd "{tool_path} input.json -o {output}" \
       --fixture /path/to/fixture_dir \
       --record-file input.json --record-pointer /items \
       --output-file out.json --output-list-pointer /sorted \
       --permute-mode list-reorder --permutations 6
   ```

   `{tool_path}` and `{output}` are substituted into the (space-split, no
   shell) `--cmd` template. `--permute-mode` is one of:

   - `list-reorder`: reorders a JSON array located at `--record-pointer`
     (RFC 6901 JSON Pointer syntax; `""` means the whole file is the array)
     inside `--record-file`.
   - `dict-key-reorder`: reorders the top-level key order of a JSON object
     in `--record-file` (probes for a missing/forgotten `sorted()` over a
     dict, in the spirit of ND003).
   - `file-creation-order`: `--record-file` names a directory; the same set
     of files (same names, same bytes) is recreated on disk in a different
     physical write order each permutation (probes for a missing/forgotten
     `sorted()` over `os.listdir`/`os.walk`, in the spirit of ND002).

   `toy_tool/` in this repository is a small, fully-owned, stdlib-only
   target used by the test suite to exercise this generic path end to end,
   independent of the three named tools' specific field models --
   `toy_tool.py` has a correct total-order tiebreak, `toy_tool_broken.py`
   has it removed. It is not one of the three real target tools.

## Exit codes

`sortdetect.py`'s own exit code (not the target tool's):

- `0` -- every permutation produced identical output order (**STABLE**)
- `1` -- output order changed across permutations (**UNSTABLE** -- the
  finding this tool exists to catch)
- `2` -- the detector itself could not complete the run: bad arguments, a
  missing/non-executable target, a target that crashed or produced output
  that isn't valid JSON, a fixture whose record set could not be located,
  etc. Never a raw traceback.

## The three real targets are currently STABLE -- and why the controls exist

Run for real against this build's copies of `consolidate.py`,
`schema_check.py` and `ndscan.py`, all three report **STABLE / exit 0**.
That is the honest, correct result: all three tools were already fixed to
append a canonical-dump tiebreak to their sort key before this detector was
built, so their output order genuinely does not depend on input order.

A detector that always says "stable" is indistinguishable from a detector
that is simply broken. So `controls/` ships three **pre-fix** variants --
`consolidate_prefix.py`, `schema_check_prefix.py`, `ndscan_prefix.py` --
each produced mechanically from the real tool by removing *only* the
trailing canonical-dump tuple element from the one sort-key function that
has it (`_finding_sort_key` / `sort_entries` / `Finding.sort_key`
respectively; nothing else is touched, including comments/docstrings, which
are only prepended with a note identifying the file as a synthetic
control). Verified, real, subprocess reruns of the detector against these
three controls give:

| tool           | real (fixed)     | control (pre-fix)  |
|----------------|-------------------|---------------------|
| `consolidate`  | STABLE (exit 0)   | **UNSTABLE (exit 1)** |
| `schema_check` | STABLE (exit 0)   | STABLE (exit 0)     |
| `ndscan`       | STABLE (exit 0)   | STABLE (exit 0)     |

Only `consolidate`'s control actually goes unstable. This is not a
detector limitation -- it is a real, verified fact about the other two
tools' data models, and it is worth explaining plainly rather than
papering over:

- **`consolidate`**: a finding's dict is `{source_tool, source_report,
  task_id, code, severity, detail}`. `task_id`/`code` are stored as
  whatever raw JSON value the source report gave them (not stringified),
  and the sort key compares them directly. Python's `==` treats `True`,
  `1` and `1.0` as equal (same for `False`/`0`/`0.0`), so two findings
  whose `code` is `true` and `1` respectively tie on every leading sort
  field -- yet `json.dumps(true) != json.dumps(1)`, so they are genuinely
  different records. `fixtures/consolidate/reports/tied.json` contains
  exactly this: six findings in two three-way tie groups
  (`true`/`1`/`1.0` and `false`/`0`/`0.0`), all sharing `task_id: null`,
  `severity: "error"`, `detail: "d"`. Permuting their order in the source
  JSON and rerunning the pre-fix control moves them around in the output;
  the real tool's canonical-dump tiebreak pins them to one order regardless
  of input order.
- **`schema_check`**: a violation entry is *exactly* `{code, message,
  pointer}`, and `pointer` is built compositionally from the JSON path
  (array index or object key) that produced it, so two violations can only
  ever share a `pointer` if they came from the exact same structural slot
  in the exact same document -- which, for any given run, holds exactly one
  value, so there is at most one violation of a given `code` per pointer.
  Every message additionally embeds the specific type name, length, or
  value involved. We tried hard to construct a counterexample (see "Bug
  hunt" below and `fixtures/schema_check/`, which holds 5 wrong-typed
  strings at 5 fixed array slots -- genuinely tied `(pointer, code,
  message)` triples across every permutation) and confirmed by real rerun
  that removing the trailing tiebreak changes nothing observable: the
  triple already is a total order given this tool's field model.
- **`ndscan`**: a `Finding` is `{rule_id, path, line, col, detail,
  severity}`; `severity` is a pure function of `rule_id`
  (`RULE_SEVERITY[rule_id]`), and `(path, line, col)` is the AST node's
  source position, which two distinct nodes in one file cannot share. So
  `(rule_id, path, line, col, detail)` is already collision-free by
  construction. `fixtures/ndscan/` and its `file-creation-order` permute
  mode instead test a different, still-real property this tool depends on
  -- that output does not depend on physical directory-entry creation
  order -- and, as expected, both variants pass.

We are reporting this nuance because the task's own instructions are
explicit that findings must come from real reruns and that a non-finding
must be stated plainly rather than invented. `consolidate`'s case, plus the
fully-owned synthetic `toy_tool.py` / `toy_tool_broken.py` pair (see above,
`--cmd` generic mode: fixed=STABLE, broken=UNSTABLE, both verified by real
subprocess rerun), together demonstrate the detection mechanism works in
general, not merely by luck on one tool's specific field model.

## Determinism

Every list `sortdetect.py` itself emits is explicitly sorted with the
canonical dump of each item as the *final* tiebreak element of its sort
key -- see `summarise_record_moves()` in `sortdetect.py`, whose
`distinct_record_moves` output list genuinely needs it (multiple recorded
swaps commonly share the same `baseline_record` but pair with different
`permutation_record`s; the test suite proves directly, by sorting the same
two records in both insertion orders, that leaving the tiebreak off makes
the result insertion-order-dependent). We practise what we preach.

Canonical JSON: `json.dumps(obj, sort_keys=True, separators=(",", ":"),
ensure_ascii=True) + "\n"`, written with `newline="\n"`. No absolute paths,
durations, timestamps, hostnames, PIDs, or wall-clock/`random` calls appear
anywhere in `sortdetect.py`, `make_fixtures.py`, or any report they
produce (verified by `TestNoForbiddenSubstrings` in the test suite, and by
inspection: subprocess `cwd` is always set to the fixture's own temp copy
directory with relative filenames passed as arguments, so even the *target
tool's own* output fields like `schema_source`/`payload_source`/
`source_report` never contain an absolute path).

Two full runs of the detector, and a full copy of this repository to a
different absolute path under a different name followed by a rerun there,
produce byte-identical reports -- see `captured_output.txt` for the actual
sha256 values from a real run.

## Tests

`python3 -m unittest test_sortdetect -v` runs the full suite (157 tests as
shipped; see `captured_output.txt` for the exact count from a real run).
Coverage includes: all three current tools reported STABLE and all three
pre-fix controls reported per their real, verified behaviour (the
load-bearing set, described above); permutation generation determinism and
full record-set coverage across many `k`/`n` combinations; fixture records
verified to actually tie on every field via direct Python equality checks;
every documented exit-2 condition (missing/crashing/non-JSON-emitting
target, malformed fixture JSON, `--permutations <= 0`, bad JSON pointers,
missing generic-`--cmd` flags, ...) exercised via real subprocess calls,
never a raw traceback; byte-stable reports across two runs; full-repository
relocation to a different absolute path and name with byte-identical
output; and a direct unit-level proof that the detector's own
`distinct_record_moves` tiebreak breaks a real tie (see "Determinism"
above).

Real-tool-dependent tests locate the three real targets via
`SORTDETECT_CONSOLIDATE_PATH` / `SORTDETECT_SCHEMA_CHECK_PATH` /
`SORTDETECT_NDSCAN_PATH` environment variables (defaulting to this build
environment's paths under `/mnt/user-data/outputs/fixes/`) and are skipped,
not failed, if a path doesn't exist -- so the suite is fully runnable using
only what this repository ships.

## Fixtures and controls

`fixtures/` and `controls/` are shipped as real files, and are also fully
regenerable byte-for-byte (including empty directories, which a naive
walk-and-copy silently drops -- this has previously changed report hashes
for reasons unrelated to whatever was actually being measured) via:

```
python3 make_fixtures.py --out .
```

which overwrites `fixtures/`, `controls/` and `toy_tool/` in place from
base64 blobs embedded in `make_fixtures.py` itself, written in binary mode
(no text-mode newline translation, so regeneration is byte-identical on
every platform). `controls/` ships the three pre-fix tool variants
described above -- these are fixtures (synthetic negative-control inputs
to this detector), not copies of the three real target tools, which this
repository intentionally does not ship.

## Bug hunt

We tried hard to break `sortdetect.py` itself via adversarial reruns (not
by reading its own source and reasoning about it). Two real bugs were
found and fixed in the tool (not worked around in the tests):

1. **`--permutations 0` (or negative) crashed with an unhandled
   `IndexError`, exiting `1`** -- masquerading as "UNSTABLE" -- instead of
   the documented exit `2` ("could not run"). Trigger:
   `sortdetect.py --tool consolidate --tool-path controls/consolidate_prefix.py --fixture fixtures/consolidate --permutations 0`.
   Fixed by validating `permutations >= 1` up front in `run_detector()` and
   raising `DetectorError` (exit 2). Pinning tests:
   `TestExitCodeTwo.test_permutations_zero` and
   `test_permutations_negative`.
2. **A malformed/non-JSON `record_file` in the fixture crashed with an
   unhandled `json.JSONDecodeError`** instead of exit `2`. Trigger: point
   `--fixture` at a directory whose `reports/tied.json` contains invalid
   JSON. Fixed by wrapping both `list-reorder` and `dict-key-reorder`
   record-file loads in `run_detector()` with an explicit `try/except
   ValueError -> DetectorError`, plus a general safety net around each
   permutation's whole body (`except Exception -> DetectorError`) so *no*
   future unanticipated exception during a permutation run can escape as a
   raw traceback again. Pinning test:
   `TestExitCodeTwo.test_malformed_record_file_json` (plus
   `test_crashing_target_nonzero_unexpected_exit`,
   `test_target_output_not_valid_json`, and
   `test_target_does_not_write_output_file`, which exercise the same
   safety net from other angles).

We also specifically went looking for a bug in the three *real* target
tools (not this detector) and found none: `schema_check.py`'s violation
model and `ndscan.py`'s `Finding` model are both, as far as we could
construct, genuinely collision-free without their tiebreak (see "Why the
controls exist" above) -- that is a real property of those tools, not a
missed bug, and we are reporting it as such rather than inventing an
instability that isn't there.

## Limitations

1. **Only `consolidate`'s control demonstrates the target instability
   end-to-end.** `schema_check` and `ndscan`'s pre-fix controls are real,
   correctly-constructed mechanical mutations, but their tools' current
   field models make the removed tiebreak provably unreachable (see above)
   -- so those two controls do not, by themselves, prove the *mechanism*
   catches a real regression. The fully-owned `toy_tool.py`/
   `toy_tool_broken.py` pair compensates for this via generic `--cmd` mode,
   but it is a synthetic stand-in, not one of the three named tools.
2. **`file-creation-order` permute mode is a weak signal on modern
   filesystems.** Many filesystems (e.g. most `ext4`/`btrfs`/`tmpfs`
   configurations, and Python's `os.walk` itself once a tool explicitly
   `sorted()`s its listing) do not actually expose physical file-creation
   order through `readdir()`, so this mode mostly proves "the tool's own
   explicit sort works," not "directory order never leaked in" on every
   possible filesystem/OS combination a user might run on.
3. **The generic `--cmd` path assumes the whole target process is a pure
   function of its two file inputs plus argv.** A target that reads
   environment variables, network state, or any file outside the
   materialised fixture copy to decide its output order will not be
   detected as unstable *because of* that dependency -- only order changes
   attributable to the permuted record set are observed.
4. **`--permutations N` covers `N` deterministic rotations/reversals of the
   record set, not all `k!` permutations.** For `k > ~4` this is a strict
   subset of the possible orderings; a tiebreak bug that only manifests for
   some exotic ordering outside this rotation/reversal family (as opposed
   to any pairwise-adjacent-tie-preserving reordering, which rotations and
   reversals both exercise for every pair) could in principle go
   undetected. We chose rotations+reversals specifically because they are
   deterministic (no RNG/seed/clock) and, for the tie-group sizes this
   detector's fixtures use (2-6), they do cover every distinct permutation
   of each small tie group; the gap only widens for much larger tie
   groups than any fixture here uses.
5. **A target whose non-determinism depends on `PYTHONHASHSEED` (e.g. it
   iterates a `set`/`dict` of unhashable-order-sensitive keys without ever
   sorting) will appear "stable" across permutations run within a single
   process invocation of the detector but could still differ across
   separate Python processes with different hash seeds.** This detector
   only compares across *input-record permutations*, not across repeated
   runs with different hash seeds; `captured_output.txt`'s determinism
   section shows repeat runs matching, but that is with `PYTHONHASHSEED`
   held constant by the ambient environment, not something this detector
   independently varies and checks.
