# FORMAT.md — the minimal `captured_output.txt` format

Normative specification for the transcript file every tool directory in this
repository ships. `driftcheck.py` implements exactly this and nothing more.

## Design constraint

This format was written **after** the transcripts, not before them. The
constraint was that the majority of existing `captured_output.txt` files must
already conform, so adopting the format is not a migration. A format nobody can
adopt without rewriting 40 files is not a format, it is a wish.

Everything below is therefore the smallest set of lines that lets a reader
answer three questions mechanically: *what was run*, *what did it exit with*,
and *how many tests passed*.

## Grammar

A transcript is a sequence of **command records**, optionally preceded by
free-text preamble.

```
transcript  := preamble? record*
preamble    := line*                    ; ignored, may be anything
record      := header body
header      := "=== $ " command " ===" NL
body        := line*                    ; up to the next header or EOF
```

### Header — REQUIRED

```
=== $ <command> ===
```

Exactly one space after `===`, one after `$`, one before the closing `===`.
`<command>` is the command line as it was invoked. The regex is:

```python
HEADER_RE = re.compile(r"^=== \$ (.+?) ===\s*$")
```

A file with **zero** headers produces `TRANSCRIPT_HAS_NO_COMMAND_RECORDS`. That
is the whole-file failure: nothing in it can be cross-checked against a README.

### `exit=<int>` — REQUIRED, once per record

```
exit=0
```

Anywhere inside the record body, on a line of its own. The first occurrence
wins. Negative values are legal and mean the process was killed by a signal
(POSIX reports signalled children as negative return codes); `exit=-9` is a
meaningful, checkable record, `exit=` absent is not.

The idiomatic way to produce it is to append `; echo "exit=$?"` to the command.
`driftcheck.py` strips that suffix before comparing a header to a README
command, so the README may show the command with or without it.

```python
EXIT_RE = re.compile(r"^\s*exit=(-?\d+)\s*$")
```

A record with no `exit=` line produces `TRANSCRIPT_RECORD_HAS_NO_EXIT`.

### `Ran <int> tests in <duration>` — REQUIRED for test commands

The unmodified `unittest` summary line. It is not reformatted, not
abbreviated, and the duration is left in place even though nothing reads it.

```python
RAN_RE = re.compile(r"^Ran (\d+) tests? in ")
```

### `OK` / `FAILED (...)` — REQUIRED for test commands

The unmodified `unittest` verdict line, at the start of a line. A `FAILED`
anywhere in the file produces `TRANSCRIPT_SHOWS_TEST_FAILURE` regardless of
what the README says — a committed transcript showing a failure is drift by
definition, because no README in this repository claims a failing suite.

## What is NOT part of the format

- No timestamps, hostnames, or absolute paths are required. Any that appear are
  the author's choice and are ignored here. (`environment-leak` territory,
  a different tool's problem.)
- No ordering requirement between records.
- No requirement that a record contain output at all beyond `exit=`.
- No trailing summary block, no header block, no version line.

Anything outside a record — everything before the first header — is preamble
and is ignored. That is deliberate: several existing transcripts open with a
coverage disclaimer, and that prose is worth keeping.

## Conforming example

```
crosspath-runner -- captured verification output
Environment: CPython 3.11.15, Linux x86_64, stdlib only, no network.

=== $ python3 -m unittest test_crosspath ===
........................................................................
----------------------------------------------------------------------
Ran 72 tests in 1.284s

OK
exit=0

=== $ python3 crosspath.py --root . -o crosspath_report.json ===
exit=1
```

The first two lines are preamble. Two records follow; both carry `exit=`; the
first also carries a test count and a verdict.

## The README side of the contract

`driftcheck.py` reads three kinds of claim out of a `README.md`:

| Claim | Where it is found | Compared against |
|---|---|---|
| a command | a line inside a fenced ``` block beginning `python3 ` or `./` | a record header |
| a test count | `Ran 26 tests`, `26 tests`, `**174 tests**` anywhere in the file | `Ran N tests` |
| an exit code | `exit 1`, `exit=1`, `exit \`2\``, `exit code 2` anywhere | `exit=N` |

Comment lines (`#`) inside a fence are skipped, and a command is normalised
before comparison: whitespace collapsed, a trailing `; echo "exit=$?"` removed,
and a trailing `| tail …` / `| head …` removed.

The exit-code check is deliberately asymmetric. A README that documents an exit
table (`0` clean / `1` drift / `2` setup error) acknowledges all three, so a
transcript exit of `0` or `1` matches. A README that mentions **no** exit code
at all is not accused of a mismatch — there is no claim to contradict. Only a
transcript exit that a claiming README fails to acknowledge is drift.
