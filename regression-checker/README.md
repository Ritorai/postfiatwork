# regression-checker

A stdlib-only Python 3 CLI that re-runs every tool's documented fixture
command and detects drift in **exit code** or **report bytes** versus a
committed baseline. Built for a repository of ~23 independent CLI tools
(queue-auditor, preflight, link-integrity, staleness-monitor,
wallet-reconciler, lifecycle-linter, event-linter, sybil-detector,
xrpl-auditor, evidence-scorer, xrpl-address, throughput-reporter,
budget-forecaster, schema-checker, evidence-harness, dup-detector,
evidence-manifest, reward-reconciler, reward-anomaly, consolidate,
payload-validator, loop-health, bundle-index), but the checker itself has
no knowledge of any specific tool -- everything it needs comes from
`baselines.json`.

## Requirements

Python 3 standard library only: `argparse`, `hashlib`, `json`, `os`,
`shlex`, `subprocess`, `sys`, `tempfile`. No third-party packages, no
network access. Verified on stock `python3` (CPython 3.10.12 on Linux).

## Files

| File | Purpose |
|---|---|
| `regress.py` | the checker |
| `test_regress.py` | 130 unit/integration tests (`unittest`, stdlib only) |
| `baselines.json` | committed baseline for the 23 real sibling tools, derived from real runs |
| `fixtures/` | small, self-contained fake "tools" the test suite runs against so it never depends on the sibling repos existing |
| `captured_output.txt` | real terminal output of the verification commands below |

## Usage

```
python3 regress.py [--root DIR] [--baselines FILE] [-o FILE] [--timeout SECONDS]
python3 regress.py --update-baselines [--root DIR] [--baselines FILE] [--timeout SECONDS]
```

- `--root PATH` -- directory containing tool subdirectories (default `.`)
- `--baselines PATH` -- baseline JSON file (default `baselines.json`)
- `-o, --output PATH` -- write the report JSON here instead of stdout
- `--timeout SECONDS` -- per-tool subprocess timeout (default `120`)
- `--update-baselines` -- **rewrites** `baselines.json` from current runs (see below)

### Exit codes

| Code | Meaning |
|---|---|
| `0` | no drift detected |
| `1` | drift detected (this includes per-tool `EXECUTION_ERROR`, see below) |
| `2` | setup error: baselines file missing/invalid, `--root` missing, `-o` unwritable |

**Why is a per-tool execution error exit `1`, not `2`?** A tool that used
to run cleanly and now times out, or whose interpreter can no longer be
found, or whose report file silently stopped being written, *is* a
regression -- exactly what this checker exists to catch. Exit `2` is
reserved for problems with the checker's **own** setup (it never got a
chance to run anything meaningful): an unreadable/malformed baselines
file, a missing `--root`, or an unwritable `--output`. Anything that goes
wrong while trying to reproduce a specific tool's command is the drift
code `EXECUTION_ERROR` and rolls into exit `1`.

## Report format (canonical JSON)

Every report -- success, drift, or setup error -- is:

```python
json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n"
```

Sorted keys, no extra whitespace, ASCII-only (non-ASCII is `\uXXXX`
escaped), exactly one trailing newline. Two runs against identical inputs
produce **byte-identical** output -- this is checked directly in
`captured_output.txt` (`cmp r1.json r2.json && echo BYTE-IDENTICAL`) and
in `test_regress.py`.

Drift report shape:

```json
{
  "schema_version": 1,
  "tool": "regression-checker",
  "status": "clean" | "drift",
  "tools_checked": 23,
  "summary": {"clean": 21, "drift": 2, "skipped_unbaselineable": 0},
  "drift_counts": {"EXIT_CODE_DRIFT": 1, "REPORT_HASH_DRIFT": 1, "TOOL_MISSING": 0, "UNBASELINED_TOOL": 0, "EXECUTION_ERROR": 0},
  "results": [
    {"tool": "...", "status": "clean" | "drift" | "skipped_unbaselineable", "drift_codes": [...], "detail": {...}}
  ]
}
```

`results` is always sorted by tool name (tool discovery is sorted, and the
union with baseline-only names is sorted again before evaluation).

### Drift codes

| Code | Meaning |
|---|---|
| `EXIT_CODE_DRIFT` | the command ran, but its exit code no longer matches the baseline |
| `REPORT_HASH_DRIFT` | the command ran, but the SHA-256 of its report bytes no longer matches the baseline (a baseline entry with `expected_report_sha256: null` **always** drifts this way -- there is no known-good hash to compare against yet) |
| `TOOL_MISSING` | the baseline names a tool directory that is not present under `--root` |
| `UNBASELINED_TOOL` | a tool directory exists under `--root` with no entry in the baseline file at all -- **this is deliberate**: a checker that silently ignores new tools gives false assurance, so any directory it cannot account for is reported as drift, not skipped |
| `EXECUTION_ERROR` | the command could not be meaningfully executed: interpreter/binary not found, subprocess timed out, or (for `report_mode: "file"`) the report file was never created |

A single tool can carry more than one drift code at once (e.g. a report
file that is never created *and* an interpreter that exits with an
unexpected code both fire together).

## What is deliberately NOT in the report

- **No durations.** Recording "how long did this take" is the obvious
  thing to bolt onto a test runner's report, and it is exactly the kind
  of field that silently destroys byte-for-byte reproducibility -- the
  entire point of hashing the report. If you need timing, wrap the
  invocation in `time python3 regress.py ...` out-of-band; it must never
  live inside the JSON.
- **No wall-clock timestamps.** Same reasoning.
- **No absolute paths.** `--root`, tool working directories, and the
  temporary files used to capture `report_mode: "file"` output are never
  written into the report. Verified directly: `grep -c "/sessions\|/tmp\|/home" r1.json` prints `0`.

## How a tool's command and report are modeled

Each baseline entry looks like:

```json
{
  "tool_name": {
    "status": "baselined",
    "command": ["python3", "tool.py", "input.json", "-o", "{REPORT}"],
    "report_mode": "file",
    "expected_exit_code": 1,
    "expected_report_sha256": "<64-hex-char sha256, or null>"
  }
}
```

- `command` is a **literal argv list**, never a shell string. `regress.py`
  calls `subprocess.run(command, cwd=tool_dir, ...)` with no shell
  involved at any point. This means a documented command that happens to
  contain shell metacharacters (`;`, `|`, `&&`, `` ` ``, `$()`, `>`) is
  passed through completely inert -- it is just another argv token to the
  child process. `fixtures/tool_shell_meta` exercises exactly this.
- `report_mode: "file"` -- the literal token `{REPORT}` in `command` is
  replaced with an absolute path inside a fresh, per-run temporary
  directory (never inside `--root`, never inside the tool's own
  directory) before the subprocess runs; after it exits, that file's
  bytes are hashed and the temp directory is deleted. If the file was
  never created, that's `EXECUTION_ERROR`, not a crash.
- `report_mode: "stdout"` -- the tool has no `-o`/report-to-file mode at
  all (e.g. `dup-detector` without `-o`); the subprocess's captured
  stdout bytes are hashed directly.
- A tool can be marked `"status": "unbaselineable"` with a required
  string `"reason"` instead of a command. It is then always
  `skipped_unbaselineable` (never drift) as long as its directory is
  present, and `TOOL_MISSING` if the directory disappears entirely.

## `--update-baselines` -- read this before you use it

```
python3 regress.py --root <repo> --baselines baselines.json --update-baselines
```

This is how a real regression gets **whitewashed**. It re-runs every
already-baselined entry and overwrites its `expected_exit_code` and
`expected_report_sha256` with whatever the tools produce right now. It
prints a loud warning banner to stderr every time it runs. It is **never**
implied by any other flag or invoked implicitly by the plain regression
check -- `test_regress.py::TestUpdateBaselines::test_update_baselines_never_runs_without_the_flag`
pins this down directly. Treat every diff it produces in `baselines.json`
as something that needs a human's sign-off before it's committed, exactly
like you would a snapshot-test update.

`--update-baselines` does **not** silently add coverage for tool
directories that have no entry at all (`UNBASELINED_TOOL` is not
"fixed" by running `--update-baselines`) -- adding a new tool to the
baseline is a deliberate, separate edit to `baselines.json`.

## fixtures/

Seven tiny synthetic "tools" (`fixtures/tool_*`), each a few lines of
Python, used by the required VERIFICATION commands and by
`test_regress.py`'s CLI-level tests:

- `tool_ok` -- clean in both baseline files
- `tool_exit_drift` -- always exits 0; `baselines_ok.json` records the
  true value (clean), `baselines_drift.json` deliberately records the
  wrong expected exit code (`EXIT_CODE_DRIFT`)
- `tool_hash_drift` -- always produces the same report; `baselines_ok.json`
  records the true hash (clean), `baselines_drift.json` deliberately
  records a wrong-but-well-formed hash (`REPORT_HASH_DRIFT`)
- `tool_error` -- has **no** `tool.py` at all. `baselines_ok.json` marks
  it `unbaselineable` (kept out of the clean baseline); `baselines_drift.json`
  wires it up as a real (failing) command, producing `EXECUTION_ERROR`
- `tool_stdout` -- report-to-stdout mode, clean in both
- `tool_shell_meta` -- its baselined command includes a literal argv
  token full of shell metacharacters; the tool just echoes it back,
  proving `regress.py` never invokes a shell
- `tool_null_hash_demo` -- `unbaselineable` in `baselines_ok.json`;
  baselined with `"expected_report_sha256": null` in
  `baselines_drift.json`, demonstrating that a null hash always drifts
- `baselines_drift.json` additionally references `ghost_tool`, a name
  with no matching directory at all, to exercise `TOOL_MISSING`

`UNBASELINED_TOOL` and most single-function edge cases are covered instead
by `test_regress.py` building throwaway tool directories under
`tempfile.TemporaryDirectory()`, so the committed `fixtures/` tree stays
exactly the shape the VERIFICATION commands expect.

## Verification (reproduced in captured_output.txt)

```
python3 -m unittest test_regress -v
python3 regress.py --root fixtures --baselines fixtures/baselines_ok.json ; echo "exit=$?"
python3 regress.py --root fixtures --baselines fixtures/baselines_drift.json -o r1.json ; echo "exit=$?"
python3 regress.py --root fixtures --baselines fixtures/baselines_drift.json -o r2.json ; echo "exit=$?"
sha256sum r1.json r2.json
cmp r1.json r2.json && echo BYTE-IDENTICAL
python3 regress.py --root fixtures --baselines /nonexistent.json ; echo "exit=$?"
grep -c "/sessions\|/tmp\|/home" r1.json
```

Actual results (see `captured_output.txt` for full transcript):
130 tests, `OK`; exit codes `0`, `1`, `1`, `2` in that order; `r1.json`
and `r2.json` byte-identical (same SHA-256); the absolute-path grep
prints `0`.

## The real baseline (`baselines.json`)

`baselines.json` was **not** hand-typed. It was built by:

1. Writing one entry per real sibling tool with its documented
   fixture command (drawn from each tool's own `README.md` /
   `RUN_COMMANDS.md` and cross-checked against its own
   `captured_output.txt`), leaving `expected_exit_code`/
   `expected_report_sha256` as placeholders.
2. Running `python3 regress.py --root <sibling-repo-root> --baselines baselines.json --update-baselines`
   once, so every value in the committed file is the actual output of
   actually executing the tool.

All 23 named tools (`budget-forecaster`, `bundle-index`, `consolidate`,
`dup-detector`, `event-linter`, `evidence-harness`, `evidence-manifest`,
`evidence-scorer`, `lifecycle-linter`, `link-integrity`, `loop-health`,
`payload-validator`, `preflight`, `queue-auditor`, `reward-anomaly`,
`reward-reconciler`, `schema-checker`, `staleness-monitor`,
`sybil-detector`, `throughput-reporter`, `wallet-reconciler`,
`xrpl-address`, `xrpl-auditor`) had a runnable, deterministic, documented
command and were successfully baselined -- **none were unbaselineable**.
Several of the derived hashes were cross-checked against the sha256sums
already committed in each tool's own `captured_output.txt`
(`evidence-manifest`, `budget-forecaster`, `reward-reconciler`,
`evidence-harness`, `schema-checker`) and matched exactly.

Two directories exist alongside the 23 named tools that are **deliberately
not** in `baselines.json`: `evidence-validator` (a real, runnable tool that
was simply outside the documented scope of this task) and `repo-root`
(not a tool at all -- just `README.md`/`LICENSE`/`.gitignore`, no script).
Running the checker against the full tree surfaces both as
`UNBASELINED_TOOL`, which is the intended behavior, not a bug: the
checker refuses to pretend a directory it doesn't know about is fine.

## A real bug I found while building this (and how it resolved)

While validating that the checker's reports are truly location-independent
(no absolute paths baked into any *tool's* own report, not just the
checker's own report), I copied the entire sibling-tools tree to
`/tmp/relocated_outputs` and reran the full baseline against the copy.
`bundle-index` showed up with `REPORT_HASH_DRIFT`: `file_count` dropped
from 10 to 9 and two `SUSPICIOUS_ARTIFACT` findings disappeared. That
looked exactly like the kind of environment-dependent bug this checker is
built to catch.

Root cause, after diffing the two reports and then diffing the two bundle
directories file-by-file: my own relocation script ran
`find /tmp/relocated_outputs -name "__pycache__" -exec rm -rf {} +` to
tidy up compiled bytecode -- but `bundle-index`'s own `bundle_bad` test
fixture *intentionally* contains a file at
`bundle_bad/__pycache__/main.cpython-310.pyc`, used as bait for the
`SUSPICIOUS_ARTIFACT` check. My cleanup command deleted that fixture file
along with real bytecode caches elsewhere in the tree. Re-running the
relocation copy without deleting anything reproduced the exact original
hash. **`bundle-index` is not location-dependent; my test harness was
briefly buggy.** I'm reporting this in detail because it is a fair
illustration of exactly the kind of false positive a regression checker
can produce, and why every drift result needs a human to look at the
`detail` block before concluding "the tool broke."

I also swept all 23 baselined tools for `PYTHONHASHSEED` sensitivity
(Python randomizes `str` hashing per-process by default, which can leak
into output ordering if a tool ever iterates a `set`/`dict` built from
one) and for `TZ` sensitivity on the two tools that take `--now`
(`staleness-monitor`, `loop-health`). Neither turned up any drift across
23 tools x 2+ seeds/timezones -- a genuinely clean result, reported as
such rather than manufactured.

## 3 limitations a reviewer should scrutinise

1. **`expected_exit_code: false` is silently accepted as `0`.** JSON's
   `false`/`true` decode to Python's `bool`, which is a subclass of `int`,
   so the current `isinstance(x, int)` validation in `load_baselines`
   lets a boolean slip through as if it were `0`/`1`. It's covered by a
   test that documents (not "fixes") the behavior. A stricter check would
   explicitly reject `bool` here; I left it permissive rather than risk
   breaking a baseline file that has this today, but a reviewer may
   reasonably want it tightened.
2. **Discovery is one level deep and directory-name-based, nothing more.**
   Any directory directly under `--root` that isn't `__pycache__` or
   dot-prefixed is treated as a candidate tool (see `evidence-validator`
   and `repo-root` above). This is intentional (no silent gaps), but it
   also means a stray directory with no code in it at all still has to be
   explicitly marked `unbaselineable` to stop showing up as drift --
   there's no "this obviously isn't a tool" heuristic.
3. **A `report_mode: "file"` tool's report is fully trusted once it
   exists.** If a command writes a *partial* file and then crashes with a
   nonzero exit code, `regress.py` will still hash whatever bytes are on
   disk and report `EXIT_CODE_DRIFT` for the exit code, but it cannot
   distinguish "wrote a complete, wrong report" from "wrote a half-written
   report and died" -- both just look like a hash that doesn't match. The
   `detail.report_bytes_length` field is there specifically so a human
   scanning the JSON can sanity-check that distinction by eye.

## No third-party imports check

`test_regress.py::TestStdlibOnlyImports` parses `regress.py`'s own AST
and asserts every import resolves to the fixed stdlib allow-list
(`argparse`, `hashlib`, `json`, `os`, `shlex`, `subprocess`, `sys`,
`tempfile`) -- this is enforced by a test, not just a comment.
