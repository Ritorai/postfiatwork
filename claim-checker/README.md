# claimcheck

Standard-library Python 3 only. No third-party packages, no network access.

`claimcheck.py` reads a free-text "verifier notes" file, extracts three
kinds of CLAIM from it (a SHA-256 digest, a test count, an exit code), and
checks each one against a submitted evidence-bundle directory: hashing the
named file, actually running the bundle's `unittest` suite, or (under a
tight safety gate) actually running a claimed command. Every claim in the
report carries both a `result` and the `evidence_source` that produced it
-- a verdict with no visible source is exactly what this tool exists to
prevent, so it never emits one.

It answers one narrow question per claim: *given what the notes assert
and what is actually sitting in the bundle, is this specific, quoted
assertion MATCHED, MISMATCHED, or UNSUBSTANTIATED -- and what, exactly,
did the tool look at to decide?*

---

## Relationship to `bundle-index` and `evidence-harness`

This tool is the third member of a small family of stdlib-only,
canonical-JSON, exit-0/1/2 evidence tools in this repository, and it
deliberately reuses their conventions rather than inventing new ones:

* **Canonical JSON.** Same serialisation as both siblings:
  `json.dumps(report, sort_keys=True, separators=(",", ":"),
  ensure_ascii=True)` plus one trailing `\n`, written with
  `sys.stdout.buffer.write` so no platform can rewrite the line ending.
* **File discovery.** `discover_files()` in `claimcheck.py` is the same
  walk as `bundle_index.py`'s: `os.walk(..., onerror=lambda e: None)`,
  sorted, forward-slash, root-relative paths, no descent into symlinked
  directories.
* **Exit-code contract.** 0 / 1 / 2, with 2 reserved for "nothing was
  checked, no verdict exists" -- an unreadable/missing bundle or notes
  file never produces a partial report.
* **`-o`/`--output` semantics.** Identical bytes always go to stdout;
  `-o` additionally writes the same bytes to a file, creating parent
  directories as needed. As with `bundle_index`, **do not point `-o`
  inside the bundle directory you are checking** -- the report file did
  not exist when the bundle was hashed, but it will exist for the next
  run, which will then hash the previous run's own report as bundle
  content. The verification commands below always write `r1.json`/
  `r2.json` as siblings of the bundle directories, never inside them.
* **`InputError` -> exit 2** exception convention and the
  `bundle: input error: ...` stderr message shape.
* **No timestamps, no durations, no hostnames, no absolute paths** in the
  report body -- see "No absolute paths, ever" below.

**Where the scope stops.** `bundle_index` answers "what is in this bundle
and is it reviewable at all?" and `evidence-harness` answers "does this
bundle contain the evidence a brief's `requirements.json` demands?".
Neither one reads or interprets a free-text *verifier's notes* file, and
neither one hashes a specific named file to check one specific claim
against it, runs the bundle's own test suite to check a specific claimed
count, or runs a specific claimed command to check a specific claimed
exit code. `claimcheck` does exactly those three things and nothing else:
**it checks CLAIMS in a notes file against a bundle; it does not index
the bundle's contents wholesale, and it does not decide whether the
bundle satisfies a brief.** A submission can reasonably be run through
all three: `bundle_index` first (is this reviewable?), `evidence-harness`
second (is everything the brief demanded present?), `claimcheck` third
(are this specific reviewer's specific claims about it actually true?).

---

## Usage

```
python3 claimcheck.py <bundle_dir> <notes_file> [-o PATH]
```

| Argument | Meaning |
| --- | --- |
| `bundle_dir` | path to the evidence bundle directory to check claims against |
| `notes_file` | path to the verifier notes file (UTF-8 text) to extract claims from |
| `-o`, `--output PATH` | also write the canonical JSON report to `PATH` (parent directories are created); the identical bytes are always printed to stdout regardless |

The notes file does not have to live inside the bundle, but it may -- the
shipped fixtures do exactly that (`bundle_truthful/notes_truthful.txt`,
`bundle_false/notes_false.txt`), so it is discovered and hashed like any
other bundle file, which is intentional and harmless.

## Exit codes

| Code | Meaning |
| --- | --- |
| `0` | every extracted claim is `MATCHED` (vacuously true if there are zero claims -- see "Judgement calls") |
| `1` | at least one claim is `MISMATCHED`, `UNSUBSTANTIATED`, or `UNVERIFIABLE_COMMAND` |
| `2` | invalid input/usage: `bundle_dir` or `notes_file` does not exist or is the wrong kind of path, `notes_file` is not valid UTF-8, argparse usage error (missing argument), or `-o` could not be written. Nothing was checked, so no verdict (0/1) exists. |

---

## Notes grammar

Verifier notes are free text. `claimcheck` recognises three narrow,
documented patterns, scanned independently on every line; a single line
may contain more than one claim, of the same or different types, and
each occurrence is extracted and verified separately.

### `SHA256_CLAIM`

A 64-hex-character token (case-insensitive, not embedded in a longer hex
run) is a hash claim. `claimcheck` then looks at the text immediately
around the hash, in this order, to see whether a filename is attached:

1. `sha256(FILENAME) = HASH` or `sha256(FILENAME): HASH` or
   `sha256(FILENAME) HASH` (also accepts `sha-256`, any case)
2. `FILENAME: HASH`, `FILENAME = HASH`, `FILENAME sha256: HASH`,
   `FILENAME sha256 = HASH` -- filename immediately before an optional
   `sha256` tag and a mandatory `:`/`=`
3. `HASH  FILENAME` or `HASH *FILENAME` -- `sha256sum`(1)-output style,
   filename immediately after the hash

A "filename" token must contain a dot followed by an extension that
**starts with a letter** (see "A real bug" below for why). If none of the
three forms match, the claim is a **bare hash** with no filename: it is
verified by hashing *every* file in the bundle and checking whether any
matches.

### `TEST_COUNT_CLAIM`

`Ran N tests` / `Ran 1 test` (the exact phrase `unittest` itself prints,
case-insensitive), or a bare `N tests` / `1 test` mention anywhere in a
line that isn't already part of a `Ran ...` match. Verified by actually
running `python3 -m unittest discover -s . -v` inside the bundle
directory (once per report, shared by every `TEST_COUNT_CLAIM`, however
many there are) and parsing the real `Ran N tests` summary line.

### `EXIT_CODE_CLAIM`

A claim requires **both** a backtick-delimited command (`` `python3
foo.py` ``) and one of `exit=N`, `exit code N`, `exit code: N`,
`exit_code=N`, `exit status N` (case-insensitive, negative N allowed) on
the same line. The command associated with an exit-code mention is the
*nearest backtick-delimited command that appears before it* on that line
-- a line can therefore carry several distinct command/exit-code claims.
An exit-code mention with no qualifying backtick command anywhere before
it on the line is still recorded as a claim, with `command` `null` and
result `UNVERIFIABLE_COMMAND` (refusals are never silently dropped -- see
"Safety rules").

---

## Result values

| Result | Meaning |
| --- | --- |
| `MATCHED` | the claim was checked and the bundle confirms it |
| `MISMATCHED` | the claim was checked and a **real observed value contradicts it** |
| `UNSUBSTANTIATED` | **nothing in the bundle could confirm or deny it** -- not the same as false |
| `UNVERIFIABLE_COMMAND` | a claimed command could not be executed safely (refused, or failed/timed out while attempting to run) |

### `MISMATCHED` vs. `UNSUBSTANTIATED` -- the distinction this tool exists to preserve

**`MISMATCHED` means claimcheck completed a real check and got a definite,
contradicting answer.** The file existed and its hash differs; the
command ran and its exit code differs; the test suite ran and its count
differs; a bare hash was searched for across every file in the bundle and
genuinely found nowhere. In every one of these cases *something concrete
was compared against the claim, and it did not match.*

**`UNSUBSTANTIATED` means claimcheck could not perform the check at all.**
The claimed file does not exist in the bundle (so there is nothing to
hash and compare); the claimed filename matches more than one file
ambiguously (so there is no single answer to compare against); the file
exists but could not be read; the bundle's test suite could not be run at
all (interpreter missing, timeout). In every one of these cases *nothing
was compared, because there was nothing to compare it to.*

This distinction matters because they call for different next actions. A
`MISMATCHED` claim is a claim proven wrong -- the notes need correcting or
the bundle needs fixing. An `UNSUBSTANTIATED` claim is a claim that was
never actually checkable from this bundle at all -- it might be true, it
might be false, but *this bundle does not contain the evidence to tell
either way*, which is itself the finding a reviewer needs. Collapsing the
two into one "fail" bucket would hide exactly that difference, so both
count toward exit code `1` but are never merged into a single value.

One deliberate edge case worth being explicit about: a **bare hash claim
that matches no file in the bundle is `MISMATCHED`, not
`UNSUBSTANTIATED`**, even though no filename was named. The check *was*
performed -- every file in the bundle was hashed and compared -- and it
came back negative. That is a completed, contradicting check, not an
unperformable one.

---

## Safety rules (`EXIT_CODE_CLAIM` execution)

Verifier notes are **untrusted input** -- the entire point of this tool
is that notes might be wrong, sloppy, or actively adversarial. An
`EXIT_CODE_CLAIM`'s command comes straight out of that file. The
following rules are non-negotiable and are enforced in `vet_command()`
before anything is ever executed:

1. **`subprocess` is never called with `shell=True`.** The command is
   parsed with `shlex.split()`, never string-interpolated into a shell,
   and passed to `subprocess.run()` as an argv list.
2. **A hard 60-second timeout (`COMMAND_TIMEOUT_SECONDS`) is enforced**
   on every execution via `subprocess.run(..., timeout=...)`.
3. **The raw command text is refused outright if it contains a shell
   metacharacter** (`;`, `|`, `&`, `$`, `<`, `>`, or a literal newline) --
   *before* it is ever parsed or run. This is deliberately stricter than
   strictly necessary (see Limitations): without `shell=True` these
   characters are inert as far as `subprocess` itself is concerned, but a
   command like `` `python3 checker.py; rm -rf /somewhere` `` is exactly
   the shape of thing an untrusted notes file might contain to see if a
   verifier's tooling can be tricked into running a second, hidden
   command, and refusing it outright rather than relying on argv parsing
   alone is the safer posture.
4. **Only a bare `python3 <file-inside-the-bundle> [args...]` invocation
   is permitted.** `argv[0]` must be exactly `python3` (not `python`,
   not `python3.10`, not an absolute interpreter path); `argv[1]` must
   resolve to a file that actually exists inside the bundle, must not be
   an absolute path, and must not resolve outside the bundle via `..`.
5. **Every refusal is recorded as a claim with `result:
   "UNVERIFIABLE_COMMAND"` and an `evidence_source` explaining exactly
   why** -- never silently dropped or skipped. A verifier reading the
   report can see precisely which claims were refused and why, not just
   an absence.

`bundle_false/notes_false.txt` deliberately exercises three different
refusal paths (non-`python3` interpreter, a shell metacharacter, and a
target file outside the bundle) so the refusal behaviour is demonstrated,
not just asserted.

---

## Report shape

```
{"bundle_dir":...,"claim_count":...,"claims":[...],"exit_code":...,
 "notes_file":...,"schema_version":1,"status":...,"summary":{...},
 "tool":"claimcheck","tool_version":"1.0.0"}
```

`bundle_dir` and `notes_file` echo the CLI arguments **verbatim, exactly
as typed** -- this is the one place the report can contain something
that looks like a path, and it is the user's own input, never something
the tool resolved or made absolute (see "No absolute paths, ever").

Each entry in `claims` has exactly these seven fields, always:

| Field | Meaning |
| --- | --- |
| `claim_type` | `SHA256_CLAIM`, `TEST_COUNT_CLAIM`, or `EXIT_CODE_CLAIM` |
| `claim_text` | the verbatim notes line the claim was found on |
| `notes_line_number` | 1-indexed line number in the notes file |
| `asserted_value` | what the notes claimed (structured: e.g. `{"sha256":...,"filename":...}`) |
| `observed_value` | what was actually observed, or `null` if nothing could be observed |
| `result` | one of the four result values above |
| `evidence_source` | what in the bundle was consulted: a filename, the exact command run, or an explicit "nothing in the bundle could substantiate this" / refusal reason |

`claims` is sorted by `(notes_line_number, offset-within-line,
claim_type)` -- `sort_keys=True` only orders JSON *object* keys, never
list items, so the list order is computed and sorted explicitly by the
tool itself, not left to any incidental iteration order. The
offset-within-line is an internal tiebreak (not itself a JSON field); it
guarantees a deterministic total order even for two identical-looking
claims of the same type on the same line.

`summary` is `{"matched":N,"mismatched":N,"unsubstantiated":N,
"unverifiable_command":N}`; the four values always sum to `claim_count`.

---

## No absolute paths, ever

The report contains **no absolute filesystem path that the tool itself
derived.** `bundle_dir` and `notes_file` echo the CLI arguments exactly
as the user typed them (relative, if the user passed relative arguments
-- which the verification commands below always do); every other path
appearing anywhere in the report (`observed_value.filename`,
`hash_claimed_found_at`, `matched_files`, the `command` inside an
`EXIT_CODE_CLAIM`'s `asserted_value`) is a bundle-root-relative,
forward-slash path produced by `discover_files()`/`resolve_filename()`,
never `.resolve()`d or joined with an absolute prefix.

This is proven three ways, not just asserted:

1. `grep -c "/sessions\|/tmp\|/home" r1.json` in `captured_output.txt`
   prints `0`.
2. The **relocation test**: `bundle_false` is copied to a second,
   unrelated absolute path (`/tmp/relocated_elsewhere/deep/path/...`),
   re-checked there, and the SHA-256 of the resulting report is compared
   against the original -- they are identical, which could not be true
   if either report embedded its own bundle's absolute location.
3. `TestDeterminismAndNoAbsolutePath` in `test_claimcheck.py` asserts
   this directly (`test_no_absolute_path_fragments_in_report`,
   `test_relocation_produces_byte_identical_report`) against freshly
   generated temp-directory bundles on every test run, not just the
   shipped fixtures.

The one place an absolute path *can* legitimately appear is an exit-`2`
error message on stderr echoing the path argument exactly as the user
typed it (e.g. `bundle directory '/nonexistent_dir' does not exist`) --
that is the user's own CLI input, not something the tool derived, and it
never enters the JSON report because exit-`2` runs produce no report at
all.

## No wall-clock, no duration, no hostname

The report contains no timestamps, no durations, and no hostname/host
identity, for the same reason `bundle_index` and `evidence-harness` omit
them: any of the three would make two runs over the same bundle and notes
produce different bytes, which would directly contradict the
byte-stability this tool is required to guarantee. `python3 -m unittest
discover -v`'s own progress output *does* print an elapsed-time line
(e.g. `Ran 3 tests in 0.000s`) -- that raw text is never copied into the
report; only the parsed integer test count is.

---

## The fixtures

* **`bundle_truthful/`** -- `checker.py` (exits 0), `test_checker.py`
  (three real, passing tests), and `notes_truthful.txt`, which makes one
  claim of each type, all real and all true: the real SHA-256 of
  `checker.py`, the real test count (3), and the real exit code of
  `python3 checker.py` (0). Every claim is `MATCHED`; exit `0`.

* **`bundle_false/`** -- the same two source files (different content,
  two real tests instead of three) and `notes_false.txt`, which makes
  twelve claims deliberately spanning every result value:

  | # | Claim | Result | Why |
  | --- | --- | --- | --- |
  | 1 | `sha256(checker.py)` = a made-up hash | `MISMATCHED` | real hash differs |
  | 2 | `sha256(missing.py)` = `test_checker.py`'s real hash | `UNSUBSTANTIATED` | `missing.py` does not exist in the bundle |
  | 3 | bare hash matching nothing | `MISMATCHED` | every file hashed, none match |
  | 4 | `sha256(checker.py)` = `test_checker.py`'s real hash | `MISMATCHED` | hash is real, but belongs to a *different* file than claimed (`hash_claimed_found_at` names it) |
  | 5a | sha256sum-style line, first half correct | `MATCHED` | two claims on one line, part 1 |
  | 5b | sha256sum-style line, second half wrong | `MISMATCHED` | two claims on one line, part 2 |
  | 6 | `sha256(test_checker.py)` = real hash, **uppercase** | `MATCHED` | hex case must not matter |
  | 7 | `Ran 10 tests` | `MISMATCHED` | real count is 2 |
  | 8 | `` `python3 checker.py` `` exit code 1 | `MISMATCHED` | real exit code is 0 |
  | 9 | `` `bash checker.py` `` exit=0 | `UNVERIFIABLE_COMMAND` | not a `python3` invocation, refused |
  | 10 | `` `python3 checker.py; echo pwned` `` exit=0 | `UNVERIFIABLE_COMMAND` | shell metacharacter, refused, never executed |
  | 11 | `` `python3 /etc/hostname` `` exit=0 | `UNVERIFIABLE_COMMAND` | target is outside the bundle, refused |

  Two `MATCHED`, six `MISMATCHED`, one `UNSUBSTANTIATED`, three
  `UNVERIFIABLE_COMMAND`; exit `1`. Full JSON is in `captured_output.txt`.

---

## A real bug (found while building this tool)

While writing `TestExtractSha256Occurrences`, a test for a line like
`"Version 2.5.1 sha256: <hash>"` was added expecting no filename to be
extracted -- and it failed. The original filename-token pattern was
`[^\s\`"'()]+\.[A-Za-z0-9]{1,10}`: "something, a dot, 1-10 alphanumeric
characters". `2.5.1` satisfies that (`"2.5"` + `.` + `"1"`), so the tool
was reading a **version number** out of the sentence and treating it as
the claimed filename -- which then failed to resolve against the bundle
and was wrongly reported as an `UNSUBSTANTIATED` claim about a
nonexistent file `"2.5.1"`, instead of correctly falling back to a bare
hash claim (which is what the sentence actually was: "look for this hash
anywhere in the bundle").

The fix (`claimcheck.py`, `_FILE_TOKEN`) requires the extension to
**start with a letter**: `[^\s\`"'()]+\.[A-Za-z][A-Za-z0-9]{0,9}`. Real
file extensions (`.py`, `.txt`, `.json`, `.tar`, ...) start with a
letter; a trailing all-digit group after a dot is almost always a
number, not a file. `test_version_number_not_misread_as_filename` and
`test_decimal_number_not_misread_as_filename` pin this down; a companion
test, `test_numeric_extension_real_filename_still_not_matched_as_such`,
documents the resulting trade-off (a real file literally named
`backup.001` still won't be recognised as a filename) as a deliberate,
accepted limitation rather than a new bug -- see Limitations below.

---

## Limitations a reviewer should scrutinise

1. **The filename-token grammar can still misfire in both directions.**
   The fix above closes the version-number false positive, but the
   underlying approach -- "a filename looks like `something.ext`" -- is a
   heuristic, not a parser. A real file named with a purely numeric
   extension (`backup.001`, `data.2024`) will never be recognised as a
   filename and any claim about it degrades to a bare-hash search
   instead (which still works, just with a less specific
   `evidence_source`). Conversely, a sufficiently filename-shaped
   non-filename token (e.g. `v1.2.py` used as a version tag, not a real
   path) could be misread as a claimed filename that then correctly
   fails to resolve, reported as `UNSUBSTANTIATED`. Both directions are
   judgement calls in a genuinely ambiguous text-parsing problem, not
   silent failures -- every case still produces a claim with a visible
   result and evidence_source, never a wrong answer presented as
   confident.
2. **The shell-metacharacter refusal is intentionally broader than
   strictly necessary.** Because `shell=True` is never used, characters
   like `;` or `|` inside a `shlex`-parsed argv are inert as data --
   `python3 ok.py "a;b"` would be perfectly safe to actually run. This
   tool refuses it anyway, checking the *raw* command text before any
   parsing happens, because distinguishing "a metacharacter that's just
   part of a legitimate quoted argument" from "a metacharacter smuggling
   a second command" reliably would require a much more careful parser
   than a security-critical code path should lean on. The trade-off is a
   small number of false-positive refusals (reported as
   `UNVERIFIABLE_COMMAND` with a clear reason, never silently) in
   exchange for a much simpler, more auditable safety boundary.
3. **`EXIT_CODE_CLAIM` command association is nearest-backtick-before,
   not semantic.** On a line with `` `python3 a.py` did X, then `python3
   b.py` exit=0 ``, the exit code is (correctly) attached to `b.py`. But
   this is purely positional: a hand-written note like `` exit=0 was
   observed for `python3 a.py` `` -- command *after* the exit mention --
   attaches to nothing (`command: null`, `UNVERIFIABLE_COMMAND`), even
   though a human reader would understand the intent immediately. The
   notes grammar requires the command to be written before its exit-code
   claim on the same line; this is documented, not silently handled.

Two smaller, structural notes for completeness: like `bundle_index`, this
tool does not descend into symlinked directories, so a symlink pointing
at a bundle subdirectory is invisible to both hashing and the `python3
<file>` safety check; and the `TEST_COUNT_CLAIM` check runs
`python3 -m unittest discover` exactly once per report even if several
`TEST_COUNT_CLAIM`s appear (a deliberate choice -- the suite's real
behaviour cannot depend on which claim is asking about it), so its
result -- including a failed-import placeholder test counting as `1` test
the way real `unittest` output does -- is shared verbatim across every
`TEST_COUNT_CLAIM` in the notes.

---

## Judgement calls

1. **Zero claims is vacuously `MATCHED`, exit `0`.** A notes file with no
   extractable claims at all (or an empty file) is not an error and is
   not a gap -- there is nothing asserted, so nothing can be wrong.
   `claim_count` is `0`, `summary` is all zeros, `status` is
   `"all_matched"`.
2. **A `TEST_COUNT_CLAIM` triggers exactly one real
   `python3 -m unittest discover -s . -v` run per report, cached and
   shared.** Running it once per claim would be wasteful and would not
   change the answer -- the bundle's test suite does not depend on how
   many times the notes mention a count.
3. **An `EXIT_CODE_CLAIM`'s command is executed at most once per distinct
   command string, cached and shared across identical claims on
   different lines.** If the same exact command is claimed with two
   different exit codes in two places, both claims are checked against
   one real execution, not two separate ones -- re-running an
   already-vetted, already-observed command a second time would not add
   information and would double the safety-relevant attack surface for
   no benefit.
4. **A bare hash claim that matches nothing is `MISMATCHED`, not
   `UNSUBSTANTIATED`** -- see "Result values" above; this is the single
   most consequential judgement call in the tool and is called out
   explicitly there.

---

## Repository layout

```
claimcheck.py           the tool
test_claimcheck.py      180 unit tests
bundle_truthful/         fixture bundle where every claim is MATCHED (exit 0)
  checker.py
  test_checker.py
  notes_truthful.txt
bundle_false/             fixture bundle with a deliberate mix of results (exit 1)
  checker.py
  test_checker.py
  notes_false.txt
captured_output.txt      verbatim transcript of the verification + relocation commands
README.md                this file
```
