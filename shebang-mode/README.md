# Shebang / executable-bit consistency checker

Stdlib-only Python 3. No third-party packages, no network. Reads the Git
index; never writes to it.

## The rule

> A Git-tracked regular **text** file is marked executable in the index
> **if and only if** its first line is a shebang (`#!`).

Both directions are enforced, because they fail for different reasons.

| code | condition | why it matters |
|------|-----------|----------------|
| `SM001_EXEC_WITHOUT_SHEBANG` | index mode `100755`, file does not start with `#!` | the kernel refuses to exec it (`ENOEXEC`) or hands it to whatever shell invoked it. The mode bit promises something the file cannot deliver. |
| `SM002_SHEBANG_WITHOUT_EXEC` | file starts with `#!`, index mode `100644` | the shebang is inert. The file must be run through an explicit interpreter, so its first line is documentation the filesystem contradicts. |

## Exact rerun commands

```
python3 -m unittest test_shebangmode
bash capture.sh
bash selfscan.sh
```

`capture.sh` runs every other command this directory documents, against
fixture repositories it builds and removes. `selfscan.sh` runs this tool
against this repository and writes `selfscan_output.txt`. No scan command
is listed here with a placeholder argument, because a command with a
placeholder in it is not one anybody ran.

### Why the repository scan is a second script

`regen-preflight` re-runs every `<tool>/capture.sh` inside a throwaway
copy of the repository made with `copytree(..., ignore=(".git", ...))`, so
that a regeneration can never touch the real working tree. That copy has
no `.git`, so this tool correctly exits 2 there. A `capture.sh` containing
a repository self-scan could therefore never reproduce under
`regen-preflight` — it would be reported as permanent drift, forever, for
a reason that has nothing to do with the repository being wrong. This was
measured, not predicted: the first version put the self-scan in
`capture.sh` and `regen-preflight` reported exactly that drift.

So `capture.sh` keeps only what reproduces anywhere, and the repository
result lives in `selfscan_output.txt`. `regen-preflight` discovers
regenerators by the exact name `capture.sh`, and `transcript-drift` and
`transcript-schema` read `<tool>/captured_output.txt` by that exact name,
so the second transcript is additive. Both were checked by running them
before and after.

The cost, stated as a cost: `selfscan_output.txt` is scanned by no
repo-wide tool, so nothing checks that it stays current. Its numbers are
quoted in this README, where a reader can re-run one command to test
them.

## Expected results

| step | result |
|------|--------|
| tests | `Ran 57 tests` / `OK` |
| conforming fixture | `status=ok findings=0`, exit **0** |
| fixture broken both ways | `SM001` on `runme`, `SM002` on `deploy.sh`, exit **1** |
| after running the two printed `fix` commands | exit **0** |
| `--root` not a directory | exit **2** |
| `--root` not inside a checkout | exit **2** |
| `--root` inside a checkout but not its root | exit **2** |
| unwritable `-o` | exit **2** |
| **this repository** | **146 × `SM002`, exit 1** — see below |

## CLI

```
shebangmode.py [--root PATH] [-o FILE | --output FILE]
               [--exclude PREFIX ...] [--quiet]
```

| flag | description |
|------|-------------|
| `--root PATH` | The Git checkout to scan. Must be the **root** of the checkout; see "A subdirectory is not a scannable root". Default `.`. |
| `-o`, `--output FILE` | Write the canonical JSON report here instead of stdout. stdout then gets a one-line summary. |
| `--exclude PREFIX` | Skip paths at or under this repo-relative prefix. Repeatable. Excluded paths are still listed in the report. |
| `--quiet` | Suppress the stdout summary. The exit code is unchanged. |

## Exit codes

0 = every tracked text file satisfies the rule · 1 = mismatches found ·
2 = usage or setup error

Exit 2 is never used for "the repository has a problem". A bad `--root`,
a missing `git`, an unwritable `-o` and a tracked file that cannot be read
are all setup errors, because in each case the tool cannot answer the
question — which is a different fact from answering it and finding nothing.

## Why the index, and not `os.stat`

Modes come from `git ls-files -s`. The index is what a clone reproduces on
every machine. A working-tree bit can be introduced locally by a umask, an
editor, a `cp` from a FAT volume, or a zip round-trip, and none of those
are facts about the repository. Reading `os.stat` would make the verdict
depend on who ran the checker.

The tests set modes with `git update-index --chmod=+x` for the same
reason. On a filesystem that does not carry the executable bit, `os.chmod`
would silently do nothing and the whole suite would pass vacuously.

## Binary files are skipped, not reported

"Starts with a shebang" is a question about text. A binary whose first two
bytes happen to be `#!` is not a script, and an executable binary is
exactly what an executable bit is for. Detection matches Git's own
heuristic: a NUL byte in the first 8000 bytes.

Stated as a limit rather than glossed: a file whose first NUL falls beyond
that window is classified as text. `test_a_nul_after_the_sniff_window_is_treated_as_text`
pins the boundary, so changing the constant is a deliberate act.

## Exclusions

`--exclude` takes repo-relative path prefixes and is **empty by default**.
It exists for generated, vendored or otherwise not-hand-maintained trees.
This repository has none, so nothing is excluded here and the flag ships
unused; the mechanism is covered by fixtures rather than by pointing it at
real paths to make a number look better.

Two properties the tests pin:

- a prefix matches a whole path or a whole leading directory component, so
  `--exclude doc` does **not** exclude `doc-validator/`. String-prefix
  matching is the obvious implementation and the obvious bug;
- an excluded path is still listed in `skipped` with the prefix that
  excluded it. An exclusion may reduce the findings; it may not make a
  file invisible.

## A subdirectory is not a scannable root

`git -C DIR ls-files` walks **up** to the enclosing repository. A plain
directory nested inside a checkout therefore does not make git fail — it
returns the tracked files at or below `DIR`, which for a fresh scratch
directory is none.

The first version of this tool printed `"status": "ok"` and exited **0**
for exactly that input: a silent pass on a repository it had not looked
at. `--root` must now be the top of a checkout, and anything else is a
setup error naming the root relative to what was given.

**The transcript found this, not the tests.** `capture.sh` created its
scratch directory inside this checkout, and the CLI-contract record that
was supposed to show exit 2 showed exit 0. The unit test that was meant to
cover the case used a `mkdtemp` **outside** any repository, where git
really does fail, so it passed the whole time. That record is kept in
`captured_output.txt` with the explanation next to it.
`TestASubdirectoryIsNotAScannableRoot` now covers both shapes, including a
subdirectory that does contain tracked files — scanning a real subset and
reporting `ok` is the same silent pass in a less obvious costume.

## Messages carry no absolute paths

Every message this tool can emit ends up in a committed transcript, so it
names the checkout root **relative** to the given `--root` (`'..'`,
`'../../..'`) and echoes `--root` exactly as supplied rather than
`abspath`-ing it first. `test_a_relative_root_produces_a_wholly_relative_message`
runs the CLI the way `capture.sh` does and requires that no absolute path
reaches stderr. An earlier draft failed this and put
`/tmp/<build dir>/shebang-mode/capture_work/...` into the transcript.

`capture.sh` uses a fixed relative scratch directory (`capture_work/`,
created and removed by the same script) instead of `mktemp -d` for the
same reason: with `mktemp`, every recorded command header carried two
machine-specific paths and the transcript could not reproduce anywhere
else.

## The report is actionable

Every finding carries the exact command that clears it:

```json
{"code": "SM002_SHEBANG_WITHOUT_EXEC",
 "path": "deploy.sh",
 "mode": "100644",
 "first_line": "#!/usr/bin/env bash",
 "detail": "begins with a shebang but is not marked executable, so the shebang is inert",
 "fix": "git update-index --chmod=+x deploy.sh"}
```

`TestTheFixFieldActuallyFixes` runs those printed commands against a real
fixture repository and requires the re-scan to come back clean, in both
directions. A `fix` field that is merely plausible would be worse than
none.

## Why this repository does not pass

Run against this checkout, the tool reports **146 × `SM002_SHEBANG_WITHOUT_EXEC`
and exits 1**. That is a real result, printed in `selfscan_output.txt`
rather than hidden:

```
counts   {"checked": 571, "executable": 0, "skipped_binary": 0,
          "skipped_excluded": 0, "skipped_not_a_regular_file": 0,
          "skipped_unmerged": 0, "tracked": 571, "with_shebang": 146}
by_code  {"SM002_SHEBANG_WITHOUT_EXEC": 146}
```

`git ls-files -s | awk '{print $1}' | sort | uniq -c` returns `571 100644`:
**no file in this repository is tracked as executable**, while 146 carry a
shebang. The first half of the rule has zero violations for the same
reason — there is nothing executable to violate it, which is why
`selfscan_output.txt` prints that 0 next to the executable count rather
than leaving it to be read as a pass.

Five of those 146 are this directory's own `shebangmode.py`,
`test_shebangmode.py`, `capture.sh`, `fixture_repo.sh` and `selfscan.sh`.
The checker reports itself, and the counts are taken with this directory
already in the index, so they are what a reader reproduces after the
commit rather than before it.

**The 146 are not repaired in this commit, and that is a limitation of
this commit rather than a judgement that they are fine.** Repairing them
means changing 146 index modes. A mode change cannot be expressed through
the file-upload route these commits are made with — every uploaded file is
written as `100644` — so the repair would have to be a `git update-index`
plus a push. Committing a checker that reports 146 real mismatches and
saying so is the honest half of the job. `--exclude`-ing the whole
repository to manufacture a green run would not have been; the self-scan
records `"exclude_prefixes": []` so that can be checked rather than taken
on trust.

Anyone with push access can clear it without any judgement calls: every
finding in the report already carries the exact `git update-index` command
for that one file, in the `fix` field shown above, so the whole set can be
extracted from the JSON report and applied. No such bulk command is given
here as a runnable block, because this directory does not ship a command
it has not run, and running it would mutate the index of the repository
being scanned. `TestTheFixFieldActuallyFixes` demonstrates the same
operation against a throwaway fixture, where mutating the index is safe.

A caller who wants the opposite convention — no executable bits anywhere,
every script invoked as `python3 tool.py`, which is what every README here
documents — should strip the 146 shebangs instead. This tool deliberately
does not decide which side gives; it reports that the two disagree and
prints the command for either direction.

## Determinism

Findings sorted by `(code, path)`; skipped entries by `(reason, path)`;
`git ls-files -z` so a path containing a newline cannot split a record;
`json.dumps` with `sort_keys=True`, `separators=(",",":")`,
`ensure_ascii=True`, one trailing newline. No wall-clock, no durations, no
absolute paths — `test_the_report_carries_no_absolute_path` and
`test_two_scans_serialise_byte_identically` pin both.

## Limitations

1. The binary sniff reads a bounded prefix, so a NUL beyond byte 8000
   reads as text. Matches Git; pinned by a test.
2. `#!` is recognised only at byte 0, with no `lstrip`. That is what the
   kernel does, so a file with a blank first line is correctly still
   `SM001` when marked executable — but a reader skimming the file may
   disagree with the tool, and the tests say which of them is right.
3. Symlinks (`120000`) and gitlinks (`160000`) are skipped with their mode
   recorded. The rule is about regular files.
4. The tool reports; it never writes. It cannot repair anything itself,
   by design — a checker that mutates the index is a different and more
   dangerous tool.
5. It does not look at file *extension* or content beyond the first line.
   52 `.py` and `.sh` files in this repository have no shebang at all;
   that is not a violation of this rule and is not reported.
