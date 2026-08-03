# exit-harness

## Purpose

`exitharness.py` runs manifest-defined CLI cases and compares the
*observed* exit code, stdout, and stderr of each case against the
*expected* result declared in the manifest. It exists so that a reviewer
has one repeatable, deterministic way to confirm a repository's claimed
`0` / `1` / `2` exit-code contract (and any output contracts on top of
it) without hand-running commands.

It is a single stdlib-only Python 3 file (`exitharness.py`) plus a test
suite (`test_exitharness.py`). No third-party packages, no network
access, no non-stdlib imports anywhere.

## Exact rerun command

Point it at the bundled example fixture tree and manifest:

```
python3 exitharness.py --manifest manifest_example.json --root fixture_tools -o report.json
echo "exit=$?"
```

This produces `report.json` (canonical JSON, see below) and exits `0`
because every case in `manifest_example.json` matches its expectation.

To point the harness at a **real** tool directory instead of the bundled
fixtures, write your own manifest with `cwd` values relative to that
directory and run:

```
python3 exitharness.py --manifest my_manifest.json --root /path/to/real/tool/repo -o report.json
```

Nothing else changes -- the fixture tools under `fixture_tools/` are
only a reproducible stand-in so that anyone can run the example above
with no setup. See "Fixture tools" below.

Run the test suite:

```
python3 -m unittest -v test_exitharness
```

## Manifest schema

A manifest is a JSON document. Its value is either:

* a JSON list of case objects directly, **or**
* a JSON object with a top-level `"cases"` key whose value is that list.

Every case object may have these keys:

| Key                              | Required | Type            | Meaning |
|-----------------------------------|----------|-----------------|---------|
| `id`                               | yes      | non-empty string | Unique-ish label for the case; used in the report and in sorting. |
| `cwd`                              | yes      | string           | Working directory for the subprocess, **relative to `--root`**. Must not be absolute and must not resolve (after `..` traversal) outside `--root` -- both are rejected as `CASE_ERROR` (see "Bug found" below). Use `"."` for `--root` itself. |
| `argv`                             | yes      | non-empty list of strings | The command to run, passed directly to `subprocess.run` (no shell). |
| `expect_exit`                      | yes      | integer          | The exit code the process must return. |
| `expect_stdout_canonical_json`     | no       | boolean (default `false`) | If `true`, stdout must parse as JSON *and* be byte-identical to `json.dumps(parsed, sort_keys=True, separators=(",",":"), ensure_ascii=True) + "\n"`. |
| `expect_stdout_contains`           | no       | string           | Stdout must contain this exact substring. |
| `expect_stderr_contains`           | no       | string           | Stderr must contain this exact substring. |
| `timeout_seconds`                  | no       | positive number  | Per-case timeout override, in seconds. If absent, the `--timeout` default (30s unless overridden) is used. |

Any key not in this table causes the case to be reported as
`CASE_MALFORMED` (see below). All four required keys must be present
with the correct type, or the case is likewise `CASE_MALFORMED`.

### CLI arguments

```
exitharness.py --manifest PATH --root PATH -o PATH [--timeout SECONDS]
```

* `--manifest PATH` -- the manifest JSON file to read.
* `--root PATH` -- root directory that every case's `cwd` is resolved
  against.
* `-o PATH` / `--output PATH` -- where to write the canonical JSON
  report.
* `--timeout SECONDS` -- default per-case timeout when a case does not
  set its own `timeout_seconds` (default: 30).

## Report format

The report is written with
`open(path, "w", encoding="utf-8", newline="\n")` and its exact text is:

```
json.dumps(report_obj, sort_keys=True, separators=(",",":"), ensure_ascii=True) + "\n"
```

`report_obj` has three top-level keys:

* `"summary"`: `{"total": N, "matched": N, "failed": N, "malformed": N}`.
* `"harness_exit_code"`: `0` if `failed == 0 and malformed == 0`, else `1`.
  (This mirrors, but is independent of, the harness's real process exit
  code -- see below.)
* `"results"`: the sorted list of one result object per manifest case
  (malformed cases included). Each result object has exactly these keys:
  `id`, `cwd`, `argv`, `expect_exit`, `actual_exit`, `result`, `detail`.
  `actual_exit` is `null` when the case never produced a real exit code
  (timeout, case error, or malformed). `detail` is `null` on `MATCH` and
  a short human-readable string otherwise.

The report **never** contains: durations, timestamps, absolute paths,
or hostnames. Only the manifest-relative `cwd` and the `argv`/`id`
values the manifest itself supplied are stored.

### Ordering (total order guarantee)

`results` is always sorted, regardless of manifest order, using the key
`(id or "", result, canonical_json_dump_of_the_whole_item)`. The first
two components are the "natural" sort a human would expect; the third
component -- the canonical JSON dump of the entire result object -- is
appended purely as a **tiebreak** so that the ordering is a genuine
total order even when two cases are identical on `id` and `result` (or
even identical on every visible field except something like `detail` or
`manifest`-supplied `argv`). Because the tiebreak is a deterministic
string comparison of the item's own canonical serialization, permuting
the input manifest's case order never changes the output order --
verified by `TestSorting.test_tiebreak_breaks_identical_id_and_result`,
`TestDeterminism.test_permuted_manifest_order_yields_identical_report`,
and the real run in `captured_output.txt`.

## Result codes (per case)

| Code | Meaning |
|------|---------|
| `MATCH` | Exit code (and every requested stdout/stderr check) matched. |
| `EXIT_MISMATCH` | Process ran, but its exit code differed from `expect_exit`. |
| `STDOUT_NOT_JSON` | `expect_stdout_canonical_json` was `true` but stdout did not parse as JSON at all. |
| `STDOUT_NOT_CANONICAL` | stdout parsed as JSON, but was not byte-identical to its canonical form (e.g. extra spaces after `,`/`:`, or unsorted keys). |
| `STDOUT_MISSING_SUBSTRING` | `expect_stdout_contains` was set and the substring was not found in stdout. |
| `STDERR_MISSING_SUBSTRING` | `expect_stderr_contains` was set and the substring was not found in stderr. |
| `TIMEOUT` | The case did not finish within its timeout. Always a case *failure*, never a harness crash. |
| `CASE_ERROR` | The case could not be run at all: missing executable, non-existent `cwd`, or a `cwd` that is absolute or escapes `--root` (see "Bug found"). |
| `CASE_MALFORMED` | The manifest entry itself is invalid (missing/extra/mistyped keys). Reported and skipped; does not abort the run. |

## Harness exit codes (whole run)

| Exit code | Meaning |
|-----------|---------|
| `0` | Every case matched (`failed == 0` and `malformed == 0`). |
| `1` | At least one case failed or was malformed, but the harness itself ran to completion and wrote a report. |
| `2` | The harness could not run at all: manifest file missing / not valid JSON / not a list (or a `{"cases": ...}` object whose value is not a list); `--root` missing or not a directory; the report path (`-o`) could not be written; or the CLI arguments themselves were invalid (missing required flag, non-numeric/non-positive `--timeout`, etc). **No report is written in this case.**

Exit codes `1` and `2` are never conflated: `1` always means "the
harness ran the whole manifest and wrote a report, but something inside
it did not match"; `2` always means "the harness could not produce a
report at all." See `TestCLIThreeExitCodesDemo` for a test that exercises
all three, and `captured_output.txt` for a real transcript of all three.

## Fixture tools (the example manifest's "repository")

`fixture_tools/` is a tiny, self-contained stand-in repository containing
three genuinely-independent stdlib-only CLIs, each with real 0/1/2
behaviour:

* `fixture_tools/validator.py` -- validates a JSON record file.
  `0` = valid; `1` = parses but fails validation (findings printed to
  stdout); `2` = misuse (bad args, unreadable file, or not JSON at all).
* `fixture_tools/linecount.py` -- counts non-blank lines/words in a text
  file. `0` = counted something (canonical JSON on stdout); `1` = file
  has zero non-blank lines (a "finding"); `2` = misuse (bad args, file
  unreadable).
* `fixture_tools/sleeper.py` -- sleeps for a given duration then exits.
  `0` = normal; `1` = "finding" mode requested; `2` = misuse (bad args).
  Its ability to sleep an arbitrary amount is also what the test suite
  uses to exercise `TIMEOUT` handling deterministically.

`fixture_tools/cases/<name>/` holds the tiny input files each case
needs. `manifest_example.json` exercises all three tools across a
successful case, a finding-producing case, and a misuse case each (nine
cases total, all `MATCH`), and is committed alongside
`sample_report_all_match.json`, the exact report it produces.

`manifest_sample_failures.json` is a second, deliberately-imperfect
manifest (wrong expectations, a timeout, a malformed case) committed
alongside the report it produces, `sample_report_with_failures.json`,
so a reader can see every non-`MATCH` result code in one place without
running anything.

These fixtures stand in for a real repository's tools purely so the
example is reproducible with zero setup. To point exit-harness at an
actual repository, write a manifest whose `cwd`/`argv` reference that
repository's real CLIs and pass `--root /path/to/that/repository`
instead of `fixture_tools`.

## Bug found during the bug hunt

**Bug:** `os.path.join(base, other)` in the Python standard library
silently **discards `base` entirely** whenever `other` is an absolute
path:

```python
>>> os.path.join("/tmp/some/root", "/etc")
'/etc'
```

The first implementation of `run_case` computed the case's working
directory with exactly this pattern:
`os.path.join(root_abs, case["cwd"])`. Since a manifest's `cwd` field is
attacker/author-controlled data read straight from JSON, a case such as

```json
{"id": "escape", "cwd": "/etc", "argv": ["cat", "passwd"], "expect_exit": 0}
```

would make the harness execute the subprocess in `/etc` -- completely
outside `--root` -- rather than failing loudly. A `..`-laden relative
`cwd` (e.g. `"../../../../etc"`) has the same effect via ordinary path
traversal. Both silently violate the stated contract that "every path in
the manifest is interpreted relative to `--root`" and would also break
the relocation-safety guarantee (behaviour would depend on what happens
to exist at that absolute path on a given machine, not on the relocatable
fixture tree).

**Fix:** added `_resolve_case_cwd()`, which rejects an absolute `cwd`
outright and additionally verifies (via `os.path.realpath`) that the
resolved directory is `--root` itself or a genuine descendant of it
before ever calling `subprocess.run`. Either violation is reported as
`CASE_ERROR` with a clear `detail` message, and the case is not executed.

**Pinning tests:** `TestBugHuntPathEscape.test_absolute_cwd_does_not_escape_root_via_cli`
and `TestBugHuntPathEscape.test_dotdot_cwd_does_not_escape_root_via_cli`
in `test_exitharness.py`, plus the unit-level
`TestRunCase.test_case_error_cwd_absolute_path_rejected` and
`TestRunCase.test_case_error_cwd_escapes_root_via_dotdot`.

## Limitations

1. **Substring checks are exact-string, not regex/whitespace-normalized.**
   `expect_stdout_contains` / `expect_stderr_contains` do a literal
   Python `in` check. A tool that reformats its message slightly between
   versions (extra space, different quoting, trailing punctuation) will
   silently start failing `STDOUT_MISSING_SUBSTRING`/`STDERR_MISSING_SUBSTRING`
   even though the "same" information is present. There is no
   fuzzy/regex matching mode.

2. **`subprocess.run(..., text=True)` performs universal-newline
   translation on captured output.** A tool that deliberately emits raw
   `\r\n` or lone `\r` bytes will have them silently normalized to `\n`
   before exit-harness ever sees them (`test_stdout_contains_match_across_crlf_output`
   demonstrates the substring check still works, but the check is
   against the *translated* text, not the tool's literal bytes). A case
   whose expected-behaviour hinges on exact non-`\n` line endings in the
   *tool's own* stdout cannot be captured by this harness as-is.

3. **A case that is well-formed enough to start but hangs forever without
   printing anything relies entirely on `timeout_seconds` (or the global
   `--timeout` default of 30s) to bound the run.** If a manifest omits
   `timeout_seconds` on a case that can genuinely hang far longer than a
   reasonable default, that case will occupy the harness for the full
   default timeout before being marked `TIMEOUT`, which can make a large
   manifest with several stuck cases slow to finish; there is no global
   wall-clock budget across the whole run, only a per-case one.

4. **No sandboxing beyond the `cwd`-escape fix.** exit-harness confines
   *where a case's cwd may be* (see "Bug found"), but it does not
   sandbox what the invoked `argv` itself can read, write, or reach over
   the network -- a malicious or buggy tool under test can still do
   anything the current OS user can do (write outside `--root` via an
   absolute path argument, make network calls, etc). exit-harness is a
   comparison harness, not a security sandbox.

(Limitations 1-3 are the three required; limitation 4 is included for
completeness since it is directly related to the bug fixed above.)

## Determinism and relocation

The report is designed to be byte-identical for the same manifest +
fixture tree regardless of: run count, manifest case order, or the
absolute path the tree lives at. This is verified both by the test
suite (`TestDeterminism`, `TestRelocation`) and by a real, manual run
recorded in `captured_output.txt` (two runs from the same directory,
then a full copy of the tree to a different absolute path under a
different name, all three reports compared with `sha256sum`).

## Files

* `exitharness.py` -- the tool.
* `test_exitharness.py` -- 126 unit + CLI-integration tests.
* `manifest_example.json` / `sample_report_all_match.json` -- the
  all-`MATCH` example (exit `0`).
* `manifest_sample_failures.json` / `sample_report_with_failures.json` --
  a second example exercising `EXIT_MISMATCH`, `STDOUT_MISSING_SUBSTRING`,
  `TIMEOUT`, and `CASE_MALFORMED` in one report (exit `1`).
* `fixture_tools/` -- the three example CLIs and their tiny per-case
  input fixtures (see "Fixture tools" above).
* `captured_output.txt` -- a real transcript: the verbose test run, all
  three harness exit codes demonstrated, and the determinism +
  relocation sha256 proof.
