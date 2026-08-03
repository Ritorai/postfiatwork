# tamper-runner

A stdlib-only Python 3 CLI that proves a verifier catches **damaged or
falsified** evidence, not merely that it accepts valid evidence.

## Purpose

tamper-runner is a META-tool. Given a VALID evidence fixture directory and
a verifier command, it:

1. copies the fixture into an isolated workspace once per tamper case
   (the original fixture is never modified),
2. applies exactly one deterministic alteration per copy,
3. runs your verifier command against that altered copy,
4. records whether the verifier flagged the alteration.

The insight this tool is built around: a verifier that accepts
*everything* passes a "does it accept valid evidence" check perfectly.
Only tampering distinguishes a real verifier from a rubber stamp. That is
why the headline result of a tamper-runner report is the list of
**ESCAPED** cases -- alterations the verifier did **not** catch -- never
just a count of successes.

A companion tool this was designed to point at is `bundle-verifier`
(`python3 bundleverify.py --bundle DIR`, exiting 0/1/2 for clean/findings/
could-not-run). A small compatible reference implementation is included
at `verifiers/bundleverify.py` for testing and demonstration, but the
verifier command is fully configurable -- tamper-runner does not hard-code
it. This was validated for real against the actual, separately-built
`bundle-verifier` deliverable (a different, more elaborate tool with its
own list-of-objects manifest schema, not the dict schema `verifiers/
bundleverify.py` here uses) -- tamper-runner ran cleanly against it with
zero code changes, all six tamper cases `CAUGHT`, control `CONTROL_OK`.
See the "Bonus section" at the end of `captured_output.txt` for the full
transcript.

## Rerun command

From this directory (paths below are relative to it):

```
python3 tamperrun.py --fixture fixtures/valid_bundle \
  --verifier "python3 verifiers/bundleverify.py --bundle {bundle}"
```

Exit code 0, all six tamper cases `CAUGHT`, control `CONTROL_OK`.

To see the tool call out a rubber-stamp verifier instead:

```
python3 tamperrun.py --fixture fixtures/valid_bundle \
  --verifier "python3 verifiers/weak_verifier.py --bundle {bundle}"
```

Exit code 1, all six tamper cases `ESCAPED` -- printed to stderr as the
headline, and present as `report["escaped_cases"]` in the JSON.

Regenerate the fixture tree (byte-for-byte, from the base64 embedded in
`make_fixtures.py`) at any time with:

```
python3 make_fixtures.py
```

Run the test suite:

```
python3 -m unittest -v test_tamperrun
```

## Pointing tamper-runner at your own verifier

`--verifier` is a single shell-quoted string, parsed with `shlex.split`
(never `shell=True`, so no shell metacharacters/injection risk). If it
contains the literal text `{bundle}`, every occurrence is replaced with
the tampered copy's directory path. If it does not contain `{bundle}`,
the path is appended as the final argument automatically. Examples:

```
--verifier "python3 bundleverify.py --bundle {bundle}"
--verifier "/usr/local/bin/my-verifier --strict {bundle} --format json"
--verifier "/usr/local/bin/my-verifier"          # path appended at the end
```

tamper-runner interprets your verifier's exit code with one convention,
applied uniformly:

| exit code | meaning                          |
|-----------|-----------------------------------|
| `0`       | accepted the bundle as valid       |
| `1`       | flagged a problem                  |
| anything else (2, 3, negative/signal-killed, ...) | could not meaningfully run -- treated as `CASE_ERROR`, never silently counted as a catch |

This matches `bundle-verifier`'s own 0=clean/1=findings/2=could-not-run
contract. If your verifier uses a different convention, wrap it in a
small shim that translates its exit codes to this one before pointing
tamper-runner at it.

## Tamper cases

Each case runs in its own isolated copy of the fixture (`shutil.copytree`
with `symlinks=True`, so a broken symlink in the fixture cannot crash the
copy). Every case is deterministic -- the same fixture always produces the
same alteration.

| case               | what it does |
|---------------------|--------------|
| `NO_OP`              | The control case. Changes nothing. If the verifier flags the *unmodified* fixture, every other result is meaningless -- see Outcome vocabulary below. |
| `DELETE_FILE`        | Deletes one file from the copy (prefers `data.txt`, falls back to the first file alphabetically that isn't `manifest.json`). |
| `MUTATE_BYTE`        | Flips one byte (XOR 0xFF) at the midpoint of a file, leaving its size unchanged -- the subtle case that a naive "does the file still exist" check will miss. |
| `TRUNCATE_FILE`      | Truncates a file to half its original length. |
| `ALTER_JSON_FIELD`   | Parses the first JSON file (by name) that contains a scalar field, and changes that field's value (int +1, bool flipped, str gets a `_TAMPERED` suffix, etc.), re-serializing the JSON canonically. |
| `STALE_HASH`         | Finds the first field anywhere in a JSON file that "looks like a recorded digest" (all-hex string, length in {32,40,56,64,96,128}) and changes it to a different same-length hex string, so it no longer matches the content it claims to describe. |
| `ADD_UNLISTED_FILE`  | Adds a new file to the copy that is not referenced by any manifest. |

Target selection always prefers a specific, meaningful file per case (so
the six cases exercise six different fixture files where possible) and
falls back to a generic scan when that preferred file is absent. If no
suitable target exists at all for a case in a given fixture, that one
case reports `CASE_ERROR` -- it never aborts the rest of the run.

## Outcome vocabulary

- `CAUGHT` -- a tamper case; the verifier flagged the altered copy (exit 1). This is what you want.
- `ESCAPED` -- a tamper case; the verifier accepted the altered copy as valid (exit 0). **This is the headline finding** -- your verifier missed a real alteration.
- `CONTROL_OK` -- the `NO_OP` control; the verifier accepted the unmodified fixture (exit 0), as it should.
- `CONTROL_FAILED` -- the `NO_OP` control; the verifier flagged the unmodified, valid fixture (exit 1). If you see this, **every other result in the report is meaningless** until it's fixed -- your verifier is rejecting valid evidence, so its rejections of tampered evidence tell you nothing.
- `CASE_ERROR` -- either the tamper itself couldn't be applied (no suitable target in this fixture) or the verifier could not meaningfully run for this one case (crashed with an unexpected exit code, wasn't found, or timed out). Never conflated with `CAUGHT`.

## Exit codes

- `0` -- every tamper case `CAUGHT` and the control was `CONTROL_OK`.
- `1` -- one or more tamper cases `ESCAPED` or `CASE_ERROR`, or the control was `CONTROL_FAILED`. The tool ran fine; the *verifier* has a problem.
- `2` -- the tool itself could not run: bad `--fixture` path, empty/unparsable `--verifier`, or an unwritable `-o` path. Never conflated with `1`.

## Report format

Canonical JSON: `json.dumps(obj, sort_keys=True, separators=(",",":"), ensure_ascii=True)` plus a single trailing `"\n"`, written with
`open(path, "w", encoding="utf-8", newline="\n")`. No absolute paths,
durations, timestamps, hostnames, or mtimes appear anywhere in it, so the
same fixture produces a byte-identical report regardless of where it (or
tamper-runner itself) lives on disk -- see the determinism + relocation
section of `captured_output.txt` for real sha256 values proving this.

Every list in the report (`cases`, `escaped_cases`) is sorted by a
primary key and then, as an explicit final tiebreaker, by the canonical
JSON dump of the item itself (`tr.sorted_with_tiebreak`). This guarantees
output order never depends on tamper-case execution order or any other
incidental factor, only on content.

Top-level shape:

```
{
  "cases": [ {case_id, target, description, verifier_exit_code, outcome, error}, ... ],
  "control": {case_id: "NO_OP", target, description, verifier_exit_code, outcome, error},
  "escaped_cases": [ "CASE_ID", ... ],
  "schema_version": 1,
  "summary": {"caught", "case_errors", "control_ok", "escaped", "total_tamper_cases"},
  "verifier_command_template": "<the --verifier string you passed>"
}
```

## Safety

- The original fixture is opened read-only; every case works on a
  `shutil.copytree(..., symlinks=True)` copy inside a
  `tempfile.TemporaryDirectory()` that is cleaned up automatically at the
  end of the run. No path this tool did not itself create is ever
  removed.
- `safe_join()` refuses any relative path that is absolute, contains `.`/
  `..` components, or whose realpath resolves outside the isolated copy
  (including through a symlink) -- before any read, write, truncate, or
  delete touches the filesystem.
- The verifier is always invoked as an argv list (`subprocess.run(argv,
  shell=False, ...)`), never through a shell, so there is no command
  injection risk from the `--verifier` string's *content* (only from
  shell metacharacters you explicitly wrap in your own `sh -c '...'`, which
  is your choice, not tamper-runner's).

## Bug found during the bug hunt

**Bug:** a broken or "escaping" symlink *anywhere* in the fixture --
completely unrelated to the file `MUTATE_BYTE`/`TRUNCATE_FILE`/
`ALTER_JSON_FIELD`/`STALE_HASH` would actually pick -- turned those cases
into false `CASE_ERROR`s instead of using the perfectly good target file
that was actually available.

**Root cause:** `_file_size_or_none()` (used to filter "is this file a
usable candidate" while scanning for a target) caught `OSError`, but
`safe_join()` raises the tool's own `PathEscapeError` for a symlink whose
target resolves outside the isolated copy -- and `PathEscapeError` is not
an `OSError` subclass. The exception propagated out of a list
comprehension mid-scan, aborting the whole case, even when an earlier,
perfectly safe candidate (e.g. `binary.dat`) would otherwise have been
chosen.

**Trigger:** copy the fixture, add a symlink whose target is an absolute
path (so it still resolves after the directory is copied again into each
case's own isolated workspace, but now points *outside* that new copy --
e.g. back at the first copy), then run `apply_mutate_byte` or
`apply_truncate_file` against that second copy. Both raised
`PathEscapeError` and reported `CASE_ERROR` even though `binary.dat` /
`notes.txt` were present and completely unaffected by the symlink.

**Fix:** `_file_size_or_none()` now catches `(OSError, PathEscapeError)`
and returns `None` for either, so one bad candidate is skipped rather than
poisoning the scan. `apply_alter_json_field()` and `apply_stale_hash()`
were hardened the same way (they now `continue` past a JSON file that
can't be safely opened/parsed instead of aborting).

**Pinning tests:** `test_tamperrun.py`,
`TestBrokenSymlinkDoesNotPoisonUnrelatedCases` (5 tests): confirms
`apply_mutate_byte`, `apply_truncate_file`, `apply_alter_json_field`, and
`apply_stale_hash` all still pick their normal, correct target file in a
fixture that additionally contains an unrelated escaping symlink, and
that `_file_size_or_none` returns `None` (not an exception) for that
symlink directly.

## Limitations

1. **Exit-code classification is purely numeric, not semantic.** A
   verifier that crashes with an uncaught exception exits with code `1`
   under CPython's default behavior -- indistinguishable from a verifier
   that legitimately found a problem. tamper-runner has no way to tell
   these apart from the outside. If you want crashes to surface distinctly
   as `CASE_ERROR`, your verifier needs to catch its own internal errors
   and exit with something other than `0` or `1` (e.g. `2`, matching the
   bundle-verifier convention). See `verifiers/uncaught_crash_verifier.py`
   and its pinning test `test_uncaught_crash_documented_limitation` for a
   concrete demonstration of this exact ambiguity.

2. **`safe_join`'s containment check is conservative for every operation,
   including ones where it isn't strictly necessary.** `os.remove()` on a
   symlink unlinks the symlink itself without following it, so deleting a
   symlink that points outside the isolated copy would actually be safe.
   tamper-runner refuses it anyway (via the same blanket realpath
   containment rule used for reads/writes/truncates, which genuinely do
   need it), reporting `CASE_ERROR` for that one case rather than risking
   a more permissive rule being wrong somewhere else. This trades a few
   avoidable `CASE_ERROR`s for a simpler, uniformly-safe rule.

3. **The tamper cases are fixed and few.** Six deterministic alteration
   types plus a control is enough to catch a rubber-stamp verifier, but it
   is not an exhaustive fuzzer. A verifier could catch all six of these
   specific alterations and still miss a wholly different kind of forgery
   (e.g. a valid-looking but semantically wrong reordering of records, a
   timestamp rollback, a cross-file inconsistency that doesn't touch any
   single file's hash). **A clean tamper-runner report proves your
   verifier catches *these* alterations -- it is not a proof that it
   catches all possible tampering.**

4. **Target selection is fixture-shape-dependent.** `ALTER_JSON_FIELD` and
   `STALE_HASH` scan JSON files for "the first scalar field" / "the first
   hex string that looks like a digest" using a fixed, documented
   heuristic (sorted-key depth-first search; hex lengths in
   {32,40,56,64,96,128}). A fixture whose manifest uses a hash format
   outside that length set (e.g. a truncated or custom digest), or that
   buries every scalar behind non-standard structure, may cause
   `CASE_ERROR` for that one case rather than silently picking a wrong
   target -- which is the intended fail-safe behavior, but it does mean
   not every fixture shape is supported out of the box.

5. **No parallelism.** Every case runs sequentially, one verifier
   invocation at a time. For a slow verifier and/or a large fixture this
   means the full run takes roughly `(cases + 1) * verifier_runtime`. This
   was a deliberate simplicity/determinism trade-off, not an oversight,
   but it is a real scaling limitation.

## Files

- `tamperrun.py` -- the tool.
- `test_tamperrun.py` -- 144 tests, `python3 -m unittest -v test_tamperrun`.
- `make_fixtures.py` -- regenerates `fixtures/valid_bundle` byte-for-byte from embedded base64.
- `fixtures/valid_bundle/` -- the valid evidence fixture used by the tests and the example rerun command.
- `verifiers/` -- `bundleverify.py` (a small real reference verifier), plus test-only stubs: `weak_verifier.py` (rubber stamp), `always_reject_verifier.py`, `crashing_verifier.py`, `uncaught_crash_verifier.py`, `exit2_verifier.py`, `slow_verifier.py`, `flaky_verifier.py`.
- `captured_output.txt` -- a real, captured transcript: verbose test run, all three exit codes, and the determinism + relocation sha256 block.
