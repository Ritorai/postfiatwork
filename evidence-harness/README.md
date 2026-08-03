# Evidence Verification Harness

A pre-submission self-check. You point it at a machine-readable statement of
what a task brief demands (`requirements.json`) and at your **own** evidence
bundle directory. It tells you, per requirement, whether the bundle satisfies
it and — when it does not — the *specific* gap you have to close before you
submit.

It is not a grader and it does not decide whether your work is good. It only
answers one narrow question: *does this bundle contain the evidence the brief
said it must contain?*

* Python 3 standard library only. No third-party packages, no network access.
* Verified on stock `python3` (CPython 3.10.12 on Linux).
* Output is canonical JSON and byte-for-byte reproducible across runs.

---

## Usage

```
python3 harness.py <requirements.json> <bundle_dir> [-o PATH] [--strict]
```

| Argument | Meaning |
| --- | --- |
| `requirements.json` | JSON object describing what the brief demands |
| `bundle_dir` | directory holding the evidence bundle to inspect |
| `-o`, `--out PATH` | also write the canonical JSON report to `PATH` (parent directories are created) |
| `--strict` | treat unrecognised requirement keys, an unreadable/empty bundle, and a requirements file with nothing verifiable in it as gaps |

The canonical JSON report always goes to stdout. `-o` writes the identical
bytes to a file as well, so you can diff two runs without capturing stdout.

---

## Exact rerun commands

Run these from the repository root.

```bash
# 1. the test suite
python3 -m unittest test_harness -v

# 2. a bundle that satisfies the brief
python3 harness.py requirements.json bundle_good
echo "exit=$?"

# 3. a bundle that does not
python3 harness.py requirements.json bundle_bad
echo "exit=$?"

# 4. prove the report is reproducible
python3 harness.py requirements.json bundle_bad -o report_bad_run1.json
python3 harness.py requirements.json bundle_bad -o report_bad_run2.json
sha256sum report_bad_run1.json report_bad_run2.json
cmp report_bad_run1.json report_bad_run2.json && echo "byte-identical"

# 5. unreadable / invalid input
python3 harness.py no_such_requirements.json bundle_good
echo "exit=$?"
```

`captured_output.txt` in this directory is the verbatim transcript of exactly
those commands, with a visible `exit=$?` line after each one.

## Expected results

| Command | Exit | Outcome |
| --- | --- | --- |
| `python3 -m unittest test_harness -v` | 0 | `Ran 63 tests` / `OK` |
| `python3 harness.py requirements.json bundle_good` | 0 | `"status":"pass"`, 5 checks pass, 0 gaps |
| `python3 harness.py requirements.json bundle_bad` | 1 | `"status":"gap"`, 4 gaps, 1 check passes |
| `python3 harness.py requirements.json bundle_good --strict` | 0 | `"status":"pass"` |
| `python3 harness.py no_such_requirements.json bundle_good` | 2 | `harness: input error: cannot read requirements file ...` |
| `python3 harness.py requirements.json no_such_bundle` | 2 | `harness: input error: bundle directory ... does not exist` |
| `cmp report_bad_run1.json report_bad_run2.json` | 0 | no output — the two reports are byte-identical |

### Which checks each fixture fails

| Check | `bundle_good` | `bundle_bad` | Why `bundle_bad` fails |
| --- | --- | --- | --- |
| `required_files` | pass | **fail** | no `README.md` in the bundle |
| `required_commands` | pass | **fail** | `python3 -m unittest` never appears; the suite was run as `python3 test_tool.py` |
| `require_exit_codes` | pass | pass | `partial_log.txt` does carry `exit=0` and `exit=1` lines |
| `require_hashes` | pass | **fail** | only `sha1sum` was run, so there are 40-hex digests but no 64-hex ones |
| `min_test_count` | pass | **fail** | largest run is `Ran 4 tests`, brief requires 10 |

---

## requirements.json

Every key is optional. An absent, `null`, `false`, `0` or `[]` value means the
brief does not demand that thing, and the corresponding check is reported as
`skipped` rather than silently dropped.

```json
{
  "required_files": ["*.py", "README.md"],
  "required_commands": ["python3 -m unittest", "python3 -m json.tool"],
  "require_exit_codes": true,
  "require_hashes": true,
  "min_test_count": 10
}
```

| Key | Type | Check performed |
| --- | --- | --- |
| `required_files` | list of glob patterns | each pattern must match at least one file in the bundle |
| `required_commands` | list of command strings | each string must appear in some readable text file in the bundle |
| `require_exit_codes` | boolean | at least one exit-status marker must appear in the captured output |
| `require_hashes` | boolean | at least one 64-hex-character sha256 digest must appear |
| `min_test_count` | integer | a `Ran N tests` line must be present with `N >= min_test_count` |

Type errors (`"required_files": "*.py"`, a negative `min_test_count`, a
non-object top level, malformed JSON) are input errors and exit 2 — they are
never silently coerced.

### What each check accepts

* **`required_files`** — `fnmatch`-style patterns; `*`, `?` and `[seq]` all
  work. A pattern matches if it matches the whole posix-style path relative to
  the bundle root **or** the bare file name. So `*.py` finds `src/tool.py`,
  while `docs/*.md` stays anchored to `docs/`.
* **`required_commands`** — whitespace-insensitive substring match. Both the
  requirement and the file contents have every whitespace run collapsed to a
  single space first, so a command wrapped across two indented lines in a
  markdown block still matches.
* **`require_exit_codes`** — recognises `exit=0`, `exit code 1`,
  `exit code: 1`, `exit_code=137` and `exit status 2`, case-insensitively,
  with negative values allowed.
* **`require_hashes`** — exactly 64 hex characters, not embedded in a longer
  hex run, so a 40-char sha1 or a 128-char sha512 does not count as a sha256.
  Digests are lowercased before reporting.
* **`min_test_count`** — parses `Ran N tests` and `Ran 1 test` from any
  scanned file.

## Exit codes

| Code | Meaning |
| --- | --- |
| `0` | every declared check passed; the bundle satisfies the brief |
| `1` | one or more gaps; the report lists each one specifically |
| `2` | unreadable or invalid input — missing/malformed `requirements.json`, missing bundle directory, bundle path is not a directory, or the `-o` report could not be written |

Exit code `2` is also what `argparse` returns for a usage error (missing
arguments), which is consistent: nothing was inspected, so no verdict exists.

## Report shape

```
{"bundle_dir":...,"bundle_files":[...],"checks":[...],"exit_code":1,
 "gap_count":4,"gaps":[...],"requirements_file":...,"schema_version":1,
 "status":"gap","strict":false,"summary":{...},"tool":...,"unscanned_files":[...]}
```

Each entry in `checks` has `check`, `status` (`pass` / `fail` / `skipped` /
`warn`), a human-readable `detail`, a list of `gaps`, and supporting
`evidence`. The top-level `gaps` array is the de-duplicated, sorted union of
every check's gaps — that is the list to work through before resubmitting.

Serialisation is `json.dumps(..., sort_keys=True, separators=(",", ":"),
ensure_ascii=True)` plus a single trailing newline, written as bytes so no
platform ever rewrites the line ending.

---

## Judgement calls

These are decisions the brief did not settle. A reviewer may reasonably want
them changed; they are all localised.

1. **`min_test_count` uses the maximum, not the sum.** If a bundle contains
   several `Ran N tests` lines, summing them would double-count the same run
   pasted into both a log and a README. Taking the largest single run answers
   the question the brief actually asks — *was there a run covering at least N
   tests?* — and is the conservative reading.
2. **`required_files` patterns match the basename as well as the relative
   path.** `fnmatch`'s `*` also crosses `/`, so `*.py` is deliberately lenient
   and will match `src/deep/tool.py`. Anyone wanting strict top-level-only
   matching should write a pattern with no directory component and compare it
   against the `bundle_files` list in the report.
3. **"Appears in some documentation/output file" means any readable UTF-8 text
   file in the bundle,** at any depth. There is no privileged `README.md` or
   `output.txt`. Files containing a NUL byte, files that are not valid UTF-8,
   and files over 2 MiB are not scanned; they are listed in
   `unscanned_files` so the omission is visible rather than silent.
   `__pycache__`, `.git`, `.tox` and similar tooling directories are skipped
   entirely and do not count towards `files_in_bundle`.
4. **Substring matching for commands is intentionally permissive.** The
   harness cannot tell a documented command from one that was actually run —
   nothing in a static bundle can. It verifies the evidence is *present and
   claimed*, which is the pre-submission question; a reviewer still judges
   whether the claim is true.
5. **Unrecognised requirement keys warn rather than fail by default.** A brief
   may demand things this harness cannot check (screenshots, a video). Failing
   by default would push contributors towards deleting keys to get a green
   run. `--strict` flips this to a hard failure for anyone who wants the
   requirements file to be fully covered.
6. **Not-required checks are reported as `skipped`, not omitted.** The report
   therefore always has the same seven entries, so two reports are directly
   diffable and a missing check can never be confused with a passing one.
7. **The report contains no timestamps, durations, or absolute paths beyond
   the two the user typed.** That is what makes repeated runs byte-identical,
   which is in turn what makes the report usable as evidence.

## Repository layout

```
harness.py            the tool
test_harness.py       63 unit tests
requirements.json     the example brief both fixtures are checked against
bundle_good/          fixture bundle that satisfies every requirement
bundle_bad/           fixture bundle that fails four requirements in four ways
captured_output.txt   verbatim transcript of the verification run
report_good_run1.json two identical reports for bundle_good, proving determinism
report_good_run2.json
report_bad_run1.json  two identical reports for bundle_bad, proving determinism
report_bad_run2.json
README.md             this file
```

Both `report_bad_run*.json` hash to
`64ec48dc2f0a27ab85f9b3b80001e13bfc5d9fe8f8d5ad258db0caa255a81b73`, and both
`report_good_run*.json` hash to
`92eb165229761d11b7138dfea41d70a58e57e72daf8d9840d8c2c5a924e88f6a`; `cmp`
confirms each pair byte-for-byte in `captured_output.txt`.

The fixture bundles are genuine: `bundle_good/verification.txt` and
`bundle_bad/partial_log.txt` are captured output from really running those
commands, and the sha256 digests in `bundle_good` are the real digests of the
files sitting next to them.
