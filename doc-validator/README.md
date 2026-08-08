# docval.py -- README-vs-argparse documentation validator

Stdlib-only Python 3 (`ast`, `re`, `json`, `argparse`, `os`, `sys`,
`subprocess`, `shlex`). No third-party packages, no network access.

Checks a repository's `README.md` files against the `argparse` CLIs they
document, and reports three kinds of "undocumented reality":

1. **missing documented flags** -- argparse defines a flag the README never mentions, or vice versa
2. **unreachable / undocumented exit codes** -- the README and the code disagree about what `sys.exit()` values are possible
3. **command blocks that do not run** -- the exact commands shown in the README either fail when actually executed, or cannot be safely executed at all
4. **broken entrypoints** -- a command line names a repository-local script or module that is not there, or that the shell cannot start

## Why AST, never `import`

Target `.py` files are attacker-adjacent: they are arbitrary code sitting in
a directory docval was pointed at. `import`ing them to introspect their
`argparse.ArgumentParser` would **execute** that code -- module-level side
effects, `if __name__ == "__main__": sys.exit(main())` guards not
withstanding (a bare top-level `ap.parse_args()` at import time is common
and would abort the whole scan on the first malformed input). docval never
imports a target module. It calls `ast.parse()` on the source text and reads
the syntax tree: `ArgumentParser(...)` constructor calls, `.add_argument(...)`
calls, `.parse_args(...)` calls, `sys.exit(...)` / `raise SystemExit(...)`
calls, and simple `NAME = <int literal>` assignments used to resolve
`sys.exit(SOME_CONST)`. This is slower to write than `importlib` but it is
the only choice that is safe to run against untrusted code, and it is
immune to a specific trap found in this repo's own fixtures: a test file
(`regression-checker/test_regress.py`) embeds a full fake CLI, including
`ap.add_argument(...)` calls, inside a triple-quoted **string** used as a
template for synthetic test scripts. A text/regex scanner of the file would
misread that string as real argparse code; `ast.parse` correctly represents
it as an inert `ast.Constant` string and never walks "into" it. See
`test_string_literal_containing_fake_argparse_code_is_ignored` in
`test_docval.py`.

Executing README **command blocks** is a different matter -- that is the
explicit point of DOC005/DOC006, see "Command execution safety" below. It
also never uses `shell=True` and never imports anything.

## Output contract

- Canonical JSON: `json.dumps(obj, sort_keys=True, separators=(",",":"), ensure_ascii=True)` plus exactly one trailing `\n`. No pretty-printing.
- No absolute paths anywhere in the report -- every `path` field is relative to `--root`, using forward slashes regardless of host OS. No wall-clock, no duration, no timestamp field.
- Findings are sorted by `(code, path, detail)` before serialization, so two runs against the same tree at different times, or the same tree copied to a different absolute location, produce **byte-identical** output.
- Report shape:
  ```json
  {"counts":{...},"finding_count":N,"findings":[{"code":"...","path":"...","detail":"..."}],"ok":true|false,"tool_count":N}
  ```

## Exit codes (of `docval.py` itself)

| Code | Meaning |
|------|---------|
| `0` | scan completed, zero findings (README and code agree) |
| `1` | scan completed, one or more findings |
| `2` | usage error or scan error (`--root` missing/not a directory, cannot read the tree, bad CLI flags) |

## Flags

- `--root PATH` -- root directory to scan (default: `.`). May contain one tool directly, or many sibling tool directories (see "Tool discovery" below).
- `-o`, `--output PATH` -- write the canonical JSON report here instead of stdout.
- `--no-run` -- skip actually *executing* README command blocks; static safety-gate checks (DOC006-style refusals, missing-script detection) still run, but crash/traceback detection (which requires running the command) does not. See "Command execution safety."

## Tool discovery

A "tool directory" is the first directory found on each descent path that
directly contains a `README.md` and/or a `.py` file that builds an
`argparse.ArgumentParser` and calls `.parse_args(...)`. Once such a
directory is found, docval does **not** descend further into it. This
matters for repositories like this one's own sibling-tools tree, where a
tool's own sample-bundle fixtures sometimes carry a tiny `README.md` +
`.py` pair of their own (e.g. `bundle-index/bundle_ok/README.md` +
`greeter.py`, a fixture bundle for `bundle_index.py`'s own tests) -- without
this rule those fixtures would be reported as spurious extra "tools."
`--root` itself is scanned the same way: if it directly qualifies, it is
the one tool; otherwise docval walks its subdirectories looking for
qualifying directories, one level at a time, skipping `__pycache__` and
dot-directories (`.git`, etc.).

## Finding codes: detection rule + false-positive risk

### DOC001_UNDOCUMENTED_FLAG
**Rule:** every flag string literal passed to `add_argument()` (e.g. `"-o"`,
`"--output"`) must appear as a token somewhere in `README.md`'s raw text
(prose, tables, or fenced code blocks all count -- see "flag documented only
inside a fenced code block" below). If it does not, that flag is reported.
Implicit `-h`/`--help` (auto-added by argparse unless `add_help=False`) is
**excluded** from this check -- requiring every README to spell out the
universally-understood help flag would be pure noise.
**False-positive risk:** none from implicit flags (excluded, see above).
Real risk: a flag that is genuinely documented but written in a form the
tokenizer does not recognize (e.g. embedded in a word, or spelled out only
as an English phrase like "the output option") will be misreported as
undocumented. Low risk in practice -- every real case found in this repo's
own tools (see self-scan below) was a genuine gap.

### DOC002_PHANTOM_FLAG
**Rule:** the inverse direction, and the more dangerous one for a reader --
a flag-shaped token appearing in the README (`--foo`, `-f`) that argparse
does not define anywhere. Implicit `-h`/`--help` are treated as defined
here too (so mentioning them is never phantom).
**False-positive risk: HIGH, and it is the finding code most worth
scrutinizing.** The token scanner cannot tell "this text is documenting my
own flag" from "this text mentions someone else's flag" or "this text is
explicitly saying a flag does *not* exist." Three concrete false positives
turned up in this repo's own self-scan (see below): a README explaining
"this tool uses `-o/--output` per the hard contract" (**not** `--out`)
still lights up `--out` as phantom, because the token is present even though
the sentence is a negation; a README's prose mentioning `grep -c`,
`wc -l`, or a *different* tool's `--now`/`--schema` flag gets attributed to
the tool under test. docval masks exactly one extremely common instance of
this problem (the `python3 -m unittest ... -v` line that opens nearly every
README in this repo -- see `MODULE_INVOCATION_LINE_RE`) because it appears
in ~30 files and would otherwise drown every other finding. It does **not**
generalize to `sha256sum`, `cmp`, `grep`, or other non-python3 programs
shown in the same command blocks; those remain a known, documented
false-positive source. Treat every DOC002 finding as "needs a human to
read the sentence," not as ground truth.

### DOC003_EXIT_CODE_UNREACHABLE
**Rule:** the README documents an exit code (see the three recognized
documentation shapes below) that no `sys.exit()` / `raise SystemExit()` /
`return` (when the entry point is called as `sys.exit(main())`) in the
module can statically produce. Codes `0` and `2` are always considered
reachable for any module that builds an `ArgumentParser` and calls
`.parse_args()` -- argparse itself exits `0` on `--help` and `2` on a usage
error, regardless of whether the author wrote `sys.exit()` by hand (this is
a modeling assumption, see Limitations).
**False-positive risk:** if the module's own exit value is *dynamic*
(computed and not resolvable to a literal, e.g. `sys.exit(rank_to_code(x))`)
docval does not know the reachable set precisely and suppresses DOC003
entirely for that module rather than guess. This trades false negatives for
avoiding false positives -- see Limitations.

### DOC004_EXIT_CODE_UNDOCUMENTED
**Rule:** the inverse -- a statically-resolvable exit code the module can
produce that the README's Exit-codes documentation never mentions.
**False-positive risk:** the exit-code documentation extraction (see below)
has real recall limits (three prose shapes recognized; a fourth, novel
phrasing would be missed), which shows up here as a false *positive*
against the code (docval thinks it's undocumented when a human would
recognize the sentence). Also suppressed entirely when the module's exit
value is dynamic, for the same reason as DOC003.

**Exit-code documentation, three shapes actually seen in this repo,
all recognized:**
1. dot/bullet-separated prose under an "Exit code(s)" heading: `` 0 = within budget · 1 = projected spend exceeds cap · 2 = invalid input ``
2. a markdown table under an "Exit code(s)" heading: `` | `0` | clean | ``
3. a dash-bullet list under an "Exit code(s)" heading: `` - `0` - scan completed... ``

Outside a heading whose title matches `exit\s*code`, only an unambiguous
inline `exit **N**` / `exit=N` / `exit code: N` / `Exit-`N`` mention (the
literal word "exit" immediately next to the number) is trusted; a bare `N =
...` or a table row elsewhere in the document is *not* treated as exit-code
documentation, because bare numbered tables/lists are extremely common for
unrelated things (test counts, thresholds, schema versions) and scanning
the whole document for them produced exactly this kind of false positive
during development (see "one real bug I found and fixed," below).

### DOC005_COMMAND_BLOCK_FAILED
**Rule:** a command line that passes the safety gate (see below) and then
does not run as written: the process times out after 60s, fails to start,
or -- the main signal -- prints
`Traceback (most recent call last):` to stderr, indicating an uncaught
Python exception. A clean nonzero exit code (e.g. a tool correctly
reporting "findings present" via `sys.exit(1)`) is **not** a failure; only
a genuine crash is. **"The target file is not there" is no longer DOC005**
-- it moved to DOC009, which asks that question once, the same way, for
every launcher form. DOC005 now means only what its name says: docval ran
the command and the run failed.
**False-positive risk:** a tool that legitimately prints the literal string
"Traceback (most recent call last):" as *data* (e.g. while testing an
error-formatting feature) would be misclassified as a crash. Converse risk
(false negative): a tool that catches an exception and prints a custom,
traceback-free error message while still exiting nonzero is correctly
*not* flagged, but a tool that segfaults or is killed by a signal without
that literal string would also not be flagged by the traceback heuristic
(the nonzero-but-not-a-Python-exception case is invisible to this check).

### DOC006_COMMAND_BLOCK_UNPARSEABLE
**Rule:** "refused" and "unparseable" share one code. A command line is
refused if, after stripping a trailing `; echo "exit=$?"`-style idiom (see
"Command execution safety"), it: still contains `;` (chains more than the
one recognized idiom), contains any of `` | & < > ` `` or `$(`, fails
`shlex.split()`, does not start with the literal token `python3`, has no
second token, has a second token starting with `-` (a module/flag
invocation such as `-m unittest`, not a file), is an absolute path, or
escapes the tool directory via `..`. Every refusal is recorded as a finding
-- **never silently skipped**.
**False-positive risk:** the `-m`/flag-form rule is maximally strict and,
applied to this repo's own sibling tools, refuses the *very first* line of
almost every README's rerun-commands block (`python3 -m unittest ... -v`),
since that is a module invocation, not a file invocation. This is a direct,
known, and fully intended consequence of the hard contract's literal
wording ("a python3 invocation of a file inside the tool directory") -- see
Limitations for why this is worth reconsidering, and why we did not soften
it for the self-scan.

### DOC007_NO_README / DOC008_NO_CLI
**Rule:** structural. DOC007 fires when a directory contains a qualifying
argparse CLI but no `README.md` (exact filename, case-sensitive). DOC008
fires when a directory contains `README.md` but no `.py` file that builds
an `ArgumentParser` and calls `.parse_args()` (a `.py` file that only
hand-parses `sys.argv`, or that has no `.py` at all, both count as "no
CLI").
**False-positive risk:** a directory that documents something other than a
CLI (e.g. a repository's top-level `README.md` + `LICENSE`, no tool) will
trigger DOC008 even though it was never meant to be a "tool" in this
convention. See the self-scan for a concrete instance (`repo-root`).

### DOC009_BROKEN_ENTRYPOINT
**Rule:** a command line in a fenced command-bearing block names a
repository-local file that either does not exist, or exists and cannot be
started. Purely static -- nothing is executed, so DOC009 is byte-identical
with and without `--no-run`.

Recognized launcher forms, and the file each one names:

| Written in the README | Entrypoint it names |
|---|---|
| `python3 x.py`, `python x.py` | `x.py` |
| `bash x.sh`, `sh x.sh` (flags skipped) | `x.sh` |
| `./x.sh` | `x.sh`, **and** its executable bit |
| `python3 -m unittest test_x.TestY.test_z` | `test_x.py` |

Anything else names no file and is left entirely to the safety gate:
`sha256sum`, `cmp`, `git`, a bare `python3`, and three cases that review
proved are not paths and were being reported as though they were:

- **`-c` anywhere** (`bash -c 'echo hello'`, `python3 -c "print(1)"`). It
  consumes the next word as an inline program. Treating `echo hello` as a
  filename turns a shell one-liner into a phantom finding, and this
  repository writes that shape in four READMEs.
- **`python3 -m <anything but unittest>`** (`-m pip`, `-m venv`,
  `-m json.tool`, `-m http.server`). A module name says nothing about
  where the module lives, and there is no way to tell a typo'd local
  module from an installed one without importing -- which this tool never
  does. So nothing is claimed either way.
- **`python3 -m unittest discover`**. `discover` is a subcommand, not a
  module, and it is the most common test invocation in this tree. Only a
  target matching `^test[_A-Za-z0-9]*$` is treated as naming a local file.

**Two working directories, because this repository uses both.** A target
counts as present if it resolves under the tool's own directory *or*
under the directory containing it. `python3 driftcheck.py --root .` and
`python3 transcript-drift/driftcheck.py --root .` are both correct
READMEs; the second is written to be run from the repository root. Before
this check existed the resolver knew only the first convention, and the
repository-wide sweep below shows what that cost: **13 of the 15 DOC005
findings in this tree were that false positive**, in four tools whose
READMEs are correct as written.

**Blocks that `cd` are exempt.** A block containing a `cd` sets a
working directory this checker cannot know -- the repository's own top
README runs `git clone`, then `cd postfiatwork/schema-checker`, then
`python3 -m unittest test_schema_check`, which is correct and names a
directory that does not exist until the clone finishes. The whole block
is skipped for DOC009 rather than guessed at. The safety gate is
unaffected: DOC005 and DOC006 still apply inside such blocks.

Whether a line *is* a `cd` is decided by tokenizing it and looking at its
first word, not by searching the block text. A regex over the body did
both wrong things at once, and review found both: it matched the `cd`
inside `python3 -c "import os; cd nowhere"` and exempted that whole block,
hiding a real broken entrypoint; and it missed an indented `  cd
somewhere`, so the lines after it were checked against a directory the
block had already left. `cdrom_mount` is not a `cd`.

**Not filenames.** Absolute paths, `..` escapes, and placeholder text
(`{REPORT}`, `path/to/FILE`, anything containing `{ } < > * ? $` or a bare
`FILE`/`PATH`/`DIR` word) are never reported. The first two are the safety
gate's business; the third is not a path at all. `FILE`/`PATH`/`DIR` are
matched on word boundaries, so `FILEMAKER.py` and `PATHOLOGY.py` are still
checked -- suppressing those would be a false negative hiding a real
defect.

This is asked as a separate question (`is_repo_local_spec`) from "does the
file exist", and the two must not be collapsed, because `resolve_entrypoint`
returns the same "no" for both. A review found that folding them together
made the whole guard a no-op: every absolute path and every `{REPORT}` was
reported as a broken entrypoint, including absolute paths naming files that
were really there. The three tests that were supposed to cover it asserted
`resolve_entrypoint(...) == (None, None)`, which a plain missing file
returns too -- so they passed either way. They now assert
`is_repo_local_spec` directly, and `TestCheckEntrypointLine` asserts the
end-to-end silence, which is what actually failed.

**Why a refused line can carry both DOC006 and DOC009.** They answer
different questions. DOC006 says "docval will not run this line", which
stays true whether or not the file exists. DOC009 says "this file is not
there". Before DOC009, `bash capture.sh` and `bash capture_typo.sh`
produced the *same* single finding -- one more `refused: not a python3
invocation`, indistinguishable from the 225 other refusals this tree
already had. The typo was invisible. That is the blind spot this code closes, and it is why the
refusal is left in place rather than replaced.

**False-positive risk:** the `cd` exemption is the honest weak point -- a
genuinely broken entrypoint inside a `cd` block is silently not checked,
which is a false *negative* chosen deliberately over a guess. On the
false-positive side, a README that documents a file the reader is
expected to create (`python3 my_config.py`) would be reported; no such
line exists in this tree, but the pattern is plausible. The
executable-bit half fires zero times in this repository for a structural
reason worth knowing: no README here uses the `./script` form, and if one
did it would be correct to flag it, because every file this repository
can commit lands at mode `100644` (see `shebang-mode`).

## Command execution safety

docval runs text taken directly out of README files. This is the single
most dangerous thing it does, so the rule is deliberately narrow and loud
rather than clever:

1. `subprocess.run(argv, shell=False, timeout=60, capture_output=True)` --
   **never** `shell=True`, **never** a string passed to a shell.
2. Every command is tokenized with `shlex.split()`. If that fails
   (unbalanced quotes), it is refused (DOC006).
3. A trailing `; echo "exit=$?"` (or similar, matching
   `; *echo\b.*\$\?`) is stripped **before** anything else, because this
   idiom appears in nearly every README in this repo purely to print the
   exit code for a human -- it never changes program behavior, and
   docval independently observes the real exit code via `subprocess`.
   This is the *only* semicolon usage that is not refused.
4. After that, the command must contain **no** shell metacharacters:
   `` | & < > ` `` or `$(`. Any of these gets the command refused as
   DOC006, not silently skipped. **This is a deliberate, conservative
   choice we are owning, not hiding:** several README blocks in this
   repo's own tools legitimately use pipes (`echo '{"a":1}' | python3
   tool.py -`) to demonstrate stdin usage. Those are refused. A more
   permissive tool could special-case a shell-free pipe implementation
   (spawn two processes, connect a real OS pipe, no shell involved) -- we
   chose not to, because every additional case we allow is more attack
   surface for a tool that runs untrusted README text by design.
5. The first token must be exactly `python3` (not `python`, not
   `python3.11`, not a path to an interpreter). The second token must be a
   relative path inside the tool directory (no leading `-`, so `-m`/`-c`
   invocations are refused; no absolute path; no `..` escape). Whether
   that file *exists* is no longer decided here -- the gate's only job is
   "will docval run this?", and there is nothing to run either way. See
   DOC009.
6. Only if all of the above pass does docval actually execute the command
   (unless `--no-run` is given, in which case it stops here -- the command
   is known to be safe-to-run but is not run).

## `--no-run` changes the finding count -- how, exactly

Everything in the safety gate above (steps 1-6) is static: it never needs
to run the command to decide whether to refuse it. Only the
**crash/traceback detection** in DOC005 requires actually running the
process, and after this change that is *all* DOC005 means. So `--no-run`
removes exactly the DOC005 findings and nothing else: DOC006 refusals and
DOC009 entrypoint findings are untouched, because neither ever runs
anything. `samples_inconsistent/` is built so this is directly observable:
12 findings with a normal run, 11 with `--no-run` (see
`captured_output.txt`). Those two totals are unchanged by this change --
the one DOC005 "missing script" finding in `bad_pair/README.md:30` became
a DOC009 finding on the same line, one for one.

## Fixtures

- `samples_consistent/` -- a README and CLI that agree. Zero findings, exit 0.
- `samples_inconsistent/` -- one defect per code across three directories.
- `samples_entrypoints/valid/` -- every command line names a file that is
  really there. **Zero DOC009 findings**; the `bash` and `-m` lines are
  still refused as DOC006, which is the control's whole point.
- `samples_entrypoints/broken/` -- **three DOC009 findings**, one per
  failure mode: a missing `bash` script, a missing `-m unittest` module,
  and a `./run.sh` that exists but is not executable. Its fourth command
  line is correct and must stay silent, so a check that flagged
  everything would fail here.

Both fixture directories have to be pointed at directly. Tool discovery
stops at the first qualifying directory on a descent path, and
`doc-validator/` qualifies, so a scan of `doc-validator` never descends
into its own samples -- which is why the repository-wide numbers below do
not include the fixtures' three findings.

## Test suites

`test_docval.py`: **262 tests**, `Ran 262 tests ... OK`.
`test_optioncheck.py`: **48 tests**, `Ran 48 tests ... OK`.
Both runs are recorded in `captured_output.txt` under
`=== $ ... ===` headers, so `transcript-drift` compares these two numbers
against what the transcript actually shows instead of taking the prose on
trust. Before this change the main suite's record used a bare `$ ` header,
which that checker does not read, and the count went unchecked.

## A pre-existing staleness this commit also repairs

`option_report.json` was already stale at the parent commit, and not
because of anything here: running the parent's own `optioncheck.py`
against the parent tree gives `options 91 / usages 193 /
unsupported_dynamic 6` while the committed report said `85 / 181 / 5`.
Tools landing in this repository had moved it and nobody re-ran it, so
`test_optioncheck.py::test_committed_report_matches_a_live_rescan` was
failing at HEAD.

It is regenerated here because this change had to touch it anyway -- the
README edits below move three `line:` fields inside it -- and shipping it
half-updated would have been worse than either leaving it or fixing it.
The prose figures in "What it found" are updated to match. Flagged rather
than buried: it is a real defect that predates this delivery, it was found
by review rather than volunteered, and it deserved its own commit.

## Scanning the whole repository

`docval.py --root <repo>` does **not** scan a repository. Discovery stops
at the first qualifying directory, and this repository's root qualifies
(it has a `README.md` and no argparse CLI), so that command reports one
tool and one finding -- the `DOC008_NO_CLI: 1` the repository's other
gates use as a baseline. Sweeping the tree means one run per top-level
directory:

```
python3 entrypoint_baseline.py <repo-root>
```

`entrypoint_baseline.py` builds no argparse parser and takes no options,
so it adds nothing to `option_report.json` and is not itself a "tool" for
docval to scan. It passes `--no-run` to every child run: this is a census
of which files README command lines *name*, and executing 51 tools'
documented commands to answer that would be slow and beside the point.
Exit `0` when no entrypoint is broken, `1` when one is, `2` on a bad
argument. It writes no committed report -- a baseline JSON would go stale
the moment anyone edited a README.

Against this repository, before and after this change. DOC006 gains one:
this section documents `entrypoint_baseline.py` in a fenced block, docval
scans its own README like any other, and `<repo-root>` contains a shell
metacharacter, so the line is refused. A first draft of this table said
225 and 493 because it was derived before that block was written; review
caught it against the delivery's own transcript.

| Code | Before | After |
|---|---|---|
| `DOC001_UNDOCUMENTED_FLAG` | 26 | 26 |
| `DOC002_PHANTOM_FLAG` | 226 | 226 |
| `DOC003_EXIT_CODE_UNREACHABLE` | 10 | 10 |
| `DOC004_EXIT_CODE_UNDOCUMENTED` | 4 | 4 |
| `DOC005_COMMAND_BLOCK_FAILED` | 15 | **0** |
| `DOC006_COMMAND_BLOCK_UNPARSEABLE` | 225 | **226** |
| `DOC009_BROKEN_ENTRYPOINT` | -- | **2** |
| **TOTAL** | **506** | **494** |

Every one of the 15 DOC005 findings was the missing-target flavor, and 13
of them were the repo-root-convention false positive described under
DOC009 -- `transcript-schema` (7), `transcript-drift` (3),
`limitations-probe` (2), `crosspath-runner` (1), all of whose command
blocks are correct as written.

### The two DOC009 findings, read by hand

Both were read against their source before anything was claimed about
them, and **both are false positives.** DOC009's true-positive count on
this repository today is **zero**. Stating that plainly is worth more
than a headline:

- `env-leak-scanner/README.md:87` is `python3 weakassert.py --root
  /sessions/... -o self_scan_report.json`. It sits under the sentence
  "The `weak-assertion-scanner` case named in the task brief **is
  present**, at `weak-assertion-scanner/README.md` line 281:". It is
  env-leak-scanner *quoting another file's leaked line as its finding* --
  the tool's entire job -- not an instruction to run anything from
  `env-leak-scanner/`.
- `regression-checker/README.md:665` is `python3 bundle_index.py
  bundle_bad -o {REPORT}`, under "Its baseline entry runs:", in a section
  titled "A real bug hunt finding: `bundle-index`'s baseline does not
  reproduce". It quotes `baselines.json`'s command for a different tool.
  Eight lines further down the same README shows the runnable form,
  `cd bundle-index && python3 bundle_index.py`.

A fenced block that quotes another file's command is indistinguishable,
to a static checker, from one that documents its own entrypoint. That is
the honest limit of this rule and it is the reason the fixtures, not the
repository sweep, are what demonstrate the check works: three findings in
`samples_entrypoints/broken/`, zero in `samples_entrypoints/valid/`, and
`TestCommittedEntrypointFixtures` asserts both counts against the
committed directories so they cannot quietly drift.

Neither README is edited here: this delivery changes the validator, not
the forty-odd READMEs it validates.

## Two real bugs this validator caught in itself during development

Both were caught by running docval against the ~30 real sibling tool
repositories in this workspace and manually checking a sample of the
findings against the source, not by unit tests alone (the unit tests were
written *after*, to pin the fix):

1. **Exit-code documentation via markdown tables was invisible.** The
   first version of the exit-code regex only recognized `exit **N**` and
   `N = description \xb7 ...`-style prose. The majority of this repo's
   READMEs (`bundle-index`, `consolidate`, `loop-health`, `preflight`,
   `queue-auditor`, `regression-checker`, `scorecard`, `snapshot-diff`,
   `thread-check`, and more) document exit codes as a markdown table
   (`| \`0\` | meaning |`), which the regex never matched -- producing 16
   false DOC004 findings claiming exit codes 0/2 were undocumented when
   they plainly were, just in a table. Fixed by scoping a table-row and
   dash-bullet pattern to the text under any heading matching
   `exit\s*code` (case-insensitive), see `_exit_code_sections()`.
2. **`nondeterminism-scanner/README.md` documents its exit codes as
   flowing prose with no dedicated heading** (`` Exit codes: `0` clean, `1`
   findings ``, and later `` Exit-`2` is reserved... ``) using the plural
   "codes" and backtick-wrapped digits directly adjacent to a hyphen. The
   original `EXIT_INLINE_RE` required singular "code" and only tolerated
   markdown-bold wrapping, not backticks, and required whitespace (not a
   bare hyphen) before the number. Fixed by broadening the regex to
   `exit(?:[\s-]*codes?)?[\s:=-]*[\*\`]{0,2}(-?\d+)[\*\`]{0,2}`.

A third defect was caught in AST code, not README parsing: `_returns_of()`
originally tried to skip nested function definitions' own `return`
statements using `for node in ast.walk(func_node): if isinstance(node,
FunctionDef) and node is not func_node: continue` -- but `ast.walk()`
enumerates a function's entire subtree eagerly; `continue` only skips
*processing* the `FunctionDef` node itself, it does not stop `walk()` from
having already queued that function's children (including its `Return`
statements) for later iteration. A `main()` containing a nested helper
function with its own `return 99` would have that `99` incorrectly
attributed to `main`'s own reachable exit codes. Fixed with a hand-written
recursive walker (`_walk_body`) that explicitly does not recurse into
nested `FunctionDef`/`AsyncFunctionDef`/`Lambda` nodes. Pinned by
`test_does_not_descend_into_nested_def_returns`.

## Limitations (things a reviewer should scrutinize)

1. **The `-m`/module-invocation refusal rule is maximally literal and
   therefore maximally noisy.** Applied to this repo's own ~30 tools, it
   refuses the opening `python3 -m unittest ... -v` line of nearly every
   README as DOC006 (134 of 191 findings in the full self-scan are DOC006,
   and the large majority of those are this one pattern repeated). This is
   a defensible reading of the hard contract's literal text ("a python3
   invocation of a file inside the tool directory") and we chose not to
   special-case unittest the way we special-cased the `; echo` idiom,
   because doing so would mean hand-picking one specific safe module
   out of an open-ended space of modules -- but a reviewer could reasonably
   argue this makes DOC006 too blunt an instrument to read at a glance
   without first filtering out the `-m` refusals.
2. **DOC002's false-positive rate is high and inherent to token-scanning
   prose**, not a bug to be fixed with a bigger regex. Distinguishing "this
   README documents its own flag" from "this README mentions someone
   else's flag" or "this README is explicitly saying a flag does not
   exist" requires understanding sentence structure, not just tokenizing
   dashes. Every DOC002 finding in the self-scan below needed a human
   read to classify true vs. false positive; a caller piping
   `finding_count` into a pass/fail gate without reading DOC002 details
   will over-reject clean READMEs.
3. **The implicit-exit-code-0/2 modeling assumption can go either way.**
   Treating every argparse CLI as implicitly capable of exit 0 (via
   `--help`) and exit 2 (via a usage error) is realistic argparse behavior,
   but it means DOC004 can fire for a code the author never had to think
   about and arguably never needs to document (nobody writes "exit 2 means
   you passed a bad flag" for the ten-thousandth argparse tool). It also
   means a module that calls `add_help=False` and manually handles every
   error path without ever calling `.error()` will *not* get an implicit-2
   credit, which is correct but easy to misread as a false negative if the
   module's actual error handling isn't visible in the same read.

## `optioncheck.py` -- cross-tool shared option checking

`docval.py` asks whether a tool documents its own flags. `optioncheck.py`,
in this same directory, asks a question no tool here asked before: **when
two tools accept the same long option, do they accept it the same way?**

It is not a second argparse extractor. It imports `docval` and reuses the
hard, dangerous part -- the AST walk that reads `add_argument(...)` calls
without ever importing the target module -- plus its discovery, constant
resolution, `relpath` and `canonical_dumps`. What it adds is the shape:
`docval.ArgparseInfo` stores flags as a bare set of strings, which answers
"is this documented?" and cannot answer "do two tools agree?".

Four dimensions are compared per option:

| Dimension | Source |
|---|---|
| `action` | the literal `action=`, or `store` when absent |
| `takes_value` | derived from `action` and `nargs`, not guessed |
| `type` | the literal `type=` name, or null |
| `choices` | the sorted literal `choices=` members, or null |

Every option lands in exactly one state: `conflict` (two or more tools
disagree on a dimension), `match` (two or more tools agree), or
`single_use` (only one tool defines it).

### What it found

```
python3 optioncheck.py --root ..
```

```
options compared: 91   conflict: 1   match: 17   single_use: 73
usages: 192   unsupported_dynamic: 9
```

**The finding: `--timeout` is not one option, it is two.**

| `type=` | Tools |
|---|---|
| `float` | `contradiction-detector`, `exit-harness`, `sortkey-detector`, `tamper-runner` |
| `int` | `crosspath-runner`, `regression-checker`, `report-freshness` |

Same spelling, same purpose, incompatible parsing: `--timeout 1.5` is
accepted by four of these tools and rejected by the other three. Nothing in
the repository would have surfaced that, because each tool's own README is
internally consistent -- the inconsistency only exists *between* READMEs,
which is precisely the gap this check covers. `test_optioncheck.py` pins
the finding, so if the tree is fixed or changes shape this README stops
being able to go quietly stale.

### What it refuses to guess

`choices=SOME_CONSTANT`, `type=lambda ...`, `add_argument(*spec)` and
`**kwargs` expansion cannot be resolved from the syntax tree without
executing code. Those are collected into `unsupported_dynamic` **with the
reason** and are excluded from comparison entirely, rather than compared on
their resolvable half -- a half-resolved option compared against a fully
resolved one produces confident nonsense. 6 such definitions exist
today, all of them `choices=` referencing a module constant.

Short flags are reported as `aliases` but are never the grouping key: `-o`
does mean "output" almost everywhere here, but grouping on a single letter
would manufacture conflicts out of unrelated options.

### Rerun

```
python3 -m unittest test_optioncheck
python3 optioncheck.py --root .. -o option_report.json
python3 optioncheck.py --root .. --option --timeout
```

Exit `0` when no option conflicts, `1` when at least one does, `2` on a bad
`--root`. `option_report.json` is the committed output; the test suite
byte-compares it against a live rescan, and separately re-runs the scan
from a relocated copy of the whole repository at a differently-named path
and requires byte-identical output.

## Rerun commands

```
python3 -m unittest test_docval -v
python3 docval.py --root samples_consistent ; echo "exit=$?"
python3 docval.py --root samples_inconsistent -o r1.json ; echo "exit=$?"
python3 docval.py --root samples_inconsistent -o r2.json ; echo "exit=$?"
sha256sum r1.json r2.json
cmp r1.json r2.json && echo BYTE-IDENTICAL
python3 docval.py --root samples_inconsistent --no-run ; echo "exit=$?"
python3 docval.py --root /nonexistent_dir ; echo "exit=$?"
grep -c "/sessions\|/tmp\|/home" r1.json
```

`r1.json`/`r2.json` are scratch outputs of the commands above, not shipped
in this distribution. `self_scan_report.json` (committed in this
distribution) is the real output of running docval against this
workspace's ~30 sibling tool directories -- see `captured_output.txt` for
the full verification transcript and this repository's top-level report
for the finding-by-finding true/false-positive analysis.
