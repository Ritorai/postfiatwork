# crosspath-runner

A stdlib-only Python 3 CLI that runs every documented tool command
**twice, from two copies of the tree at two different absolute paths**,
canonicalises each JSON report, and compares the hashes. A tool whose
output depends on where the checkout lives diverges here and nowhere
else in this repository.

## Why this is not `regression-checker`

`regression-checker` compares one run against a hash committed earlier.
That is the right tool for "did this output change?", and it is
**structurally blind to location dependence**:

- Its baseline was recorded from **one** path. A tool that writes its own
  absolute path into its report produces a hash that is wrong on every
  other machine — but the checker only ever sees one hash at a time, so it
  cannot distinguish "leaks its path" from "genuinely changed".
- Worse: if the baseline was recorded from the same path the check runs
  from — the normal case, and exactly what `--update-baselines` does —
  **the leak is identical in both, the hash matches, and the tool is
  reported clean forever.**

This runner needs no baseline at all. Both halves of the comparison
happen in one invocation.

## Requirements

Python 3 standard library only: `argparse`, `hashlib`, `json`, `os`,
`shutil`, `subprocess`, `sys`, `tempfile`. No third-party packages, no
network. Verified on CPython 3.11.15, Linux x86_64.

## Files

| File | Purpose |
|---|---|
| `crosspath.py` | the runner |
| `test_crosspath.py` | 72 unit/integration tests (`unittest`, stdlib only) |
| `captured_output.txt` | real terminal output of the verification commands below |

## Usage

```
python3 crosspath.py [--root DIR] [--manifest FILE] [--only A,B] [--work DIR]
                     [-o FILE] [--timeout SECONDS]
python3 crosspath.py --path-a DIR --path-b DIR --manifest FILE [-o FILE]
```

- `--root PATH` — the tree to copy twice (default `.`)
- `--manifest PATH` — tools manifest; **defaults to
  `<root>/regression-checker/baselines.json` and accepts that file
  unchanged.** `command` and `report_mode` mean the same thing in both
  tools, and maintaining a second copy of that inventory is precisely how
  two inventories drift apart.
- `--path-a` / `--path-b` — compare two checkouts that already exist
  instead of copying. Must be given together.
- `--only A,B` — restrict to a subset of manifest entries
- `--work DIR` — where to place the two copies (default: a temp dir)
- `-o, --output PATH` — write the report JSON here instead of stdout
- `--timeout SECONDS` — per-run subprocess timeout (default `120`)

### Exit codes

| Code | Meaning |
|---|---|
| `0` | every tool produced identical canonical output from both paths |
| `1` | at least one tool diverged |
| `2` | setup error, **or** a tool could not be executed in one or both copies |

**Why an execution error is exit `2` here and exit `1` in
`regression-checker`.** There, a tool that stopped running *is* the
regression being hunted. Here, a tool that did not run in both copies
produced no comparison at all — and reporting "no divergence found" for a
tool that never ran is the same false assurance this repository keeps
finding in other tools. A single unrunnable entry makes the whole report
`"status": "error"`.

### Refusals

`--path-a` and `--path-b` are rejected when they are the same directory
(two runs from one path prove nothing about path dependence — the leak
would be identical in both) **and** when they are the same length (see
below).

## The two copy names, and a bug this tool had

Copies are made at `xp_a` and
`xp_b_this_copy_has_a_deliberately_longer_name`, which differ in spelling
**and in length**. The length difference is not decoration:

> Any artefact of the form `f(len(path)) % N` cancels out whenever `N`
> divides the length difference.

The first version of this tool used names differing by **30** characters.
Two of its own tests — a formatting choice keyed on `len(cwd) % 3` and an
exit code keyed on `len(cwd) % 2` — **passed as "identical"**, because 2
and 3 both divide 30. The runner was silently blind to exactly the class
of defect it exists to find. The difference is now **41**, a prime, so
every period from 2 to 40 is exposed; a period of exactly 41 or a
multiple of it can still hide, which is a real residual gap and is listed
below rather than glossed over. `test_crosspath.py` asserts the
difference is prime, so the property cannot silently regress.

I am recording this because it was found by running the tests, not by
reading the code, and because a tool that quietly fails to detect its own
target class is worse than no tool.

## Divergence codes

| Code | Meaning |
|---|---|
| `PATH_LEAK` | one of the two working paths appears literally inside a tool's own report |
| `REPORT_HASH_DIVERGENCE` | the canonical JSON (or, for non-JSON output, the raw bytes) differ between the two paths |
| `EXIT_CODE_DIVERGENCE` | the same command exited differently in the two copies |
| `NON_CANONICAL_JSON` | output is not JSON and was compared as raw bytes, **or** the canonical JSON matched while the raw bytes did not (unstable formatting) |
| `EXECUTION_ERROR` | the command could not be run in one or both copies |

`PATH_LEAK` and `REPORT_HASH_DIVERGENCE` normally fire together; the leak
code is what tells a reader *why* without making them diff two hashes.

## Report format

Canonical JSON: `sort_keys=True`, `separators=(",", ":")`,
`ensure_ascii=True`, exactly one trailing newline. No timestamps, no
durations — pinned by a test that greps the report for those words.

```json
{
  "schema_version": 1,
  "tool": "crosspath-runner",
  "status": "identical" | "divergent" | "error",
  "mode": "copied" | "given-paths",
  "tools_compared": 4,
  "summary": {"identical": 3, "divergent": 1, "error": 0, "skipped": 0},
  "code_counts": {"PATH_LEAK": 1, "...": 0},
  "results": [
    {"tool": "...", "status": "...", "codes": ["..."], "detail": {...}}
  ]
}
```

On divergence, `detail.differences` lists up to five **JSON Pointers** to
the differing nodes with both values, so a reader sees `"/cwd"` rather
than two hashes and a shrug.

### No absolute paths in the report

The two working paths are this tool's *inputs*, so quoting them back
would defeat the purpose. Every string that reaches the report goes
through a redactor that replaces them with `<ROOT_A>` and `<ROOT_B>`,
longest-first so a nested pair is never half-replaced. The redactor is
also the leak detector: if it fired inside a tool's own report, that tool
leaked its path, and the finding says so **without reproducing the
path**.

## Verification (reproduced in captured_output.txt)

```
python3 -m unittest test_crosspath
python3 crosspath.py --root <tree> --manifest manifest.json ; echo "exit=$?"
python3 crosspath.py --root <tree> --manifest manifest.json \
        --only clean_tool,path-collision-scanner,regression-checker ; echo "exit=$?"
grep -c "/root\|/tmp\|/home\|xp_a\|xp_b" out.json
```

Actual results: **72 tests, `OK`**; the full run exits `1` with exactly
one divergent tool (`leaky_tool`: `PATH_LEAK` + `REPORT_HASH_DIVERGENCE`,
pinpointed at JSON Pointer `/cwd`); the same run with the planted defect
excluded exits `0` with three identical tools; the absolute-path grep
prints `0`.

## Coverage of the real repository — read this before quoting the result

The brief asks for a repo-wide result. **This run is not repo-wide, and I
am not going to present it as one.** This environment has no outbound git
access — `git clone` returns `403` through the proxy — so only the tool
directories whose source is present here could be executed:

| Entry | What it is |
|---|---|
| `path-collision-scanner` | real, committed, run for real — **identical** across both paths |
| `regression-checker` | real, committed, run for real — **identical** across both paths |
| `clean_tool` | synthetic control, fixed report — identical |
| `leaky_tool` | synthetic control that writes `os.getcwd()` into its report — **divergent**, as designed |

So: **2 of the repository's 39 tool directories were actually executed
cross-path.** Both are clean. The other 37 are untested by this run and
nothing here says otherwise. The runner is written against
`regression-checker/baselines.json` unchanged, so a reviewer with a clone
gets the repo-wide result with:

```
python3 crosspath-runner/crosspath.py --root . \
        --manifest regression-checker/baselines.json -o crosspath_report.json
```

Note that even that command covers **23** entries, not 39 — that is how
many tools `baselines.json` currently lists. The 16 unbaselined
directories are invisible to both this runner and the regression checker,
which is a repository-level gap, not a defect in either tool.

## 3 limitations a reviewer should scrutinise

1. **A prime length difference is not immunity.** The two copy names
   differ by 41 characters, so an artefact whose period is exactly 41, 82,
   … still cancels. Nothing detects a tool that depends on the path's
   *content* rather than its length in a way that happens to agree — for
   example a tool keyed on the first character of its path would need the
   two names to start differently, which they do (`xp_a` vs `xp_b…`), but
   a tool keyed on the third character would not, because both are `_`.
   The general fix is more than two paths; this tool runs exactly two.

2. **It detects location dependence, not non-determinism in general.**
   Both copies are run in the same process environment, on the same
   machine, at nearly the same moment. A tool that varies by
   `PYTHONHASHSEED`, wall-clock, locale, hostname, user, or CPU count
   produces identical output here and is reported clean. `PATH_LEAK` only
   fires on the *literal* working path — a tool that writes
   `os.path.basename(os.getcwd())`, or a hash of its path, leaks
   location-dependent data that this rule will not name (it will still
   diverge, and show up as `REPORT_HASH_DIVERGENCE` with a pointer, but
   it will not be labelled a leak).

3. **The comparison is only as good as the manifest.** A tool absent from
   `baselines.json` is never run, and this runner does not discover tool
   directories on its own — deliberately, because inventing a command for
   a tool is how you end up baselining the wrong invocation. The
   consequence is that "0 divergences" always means "0 divergences among
   the manifest entries", and the manifest currently covers 23 of 39
   directories. A `--strict-inventory` mode that fails on any directory
   under `--root` with no manifest entry would close this; it is not
   implemented, and I would rather name the gap than imply it is covered.

## No third-party imports check

`test_crosspath.py::TestStdlibOnlyImports` parses `crosspath.py`'s own AST
and asserts every import resolves to the fixed stdlib allow-list —
enforced by a test, not by a comment.
