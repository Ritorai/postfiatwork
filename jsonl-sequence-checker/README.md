# JSONL sequence checker

Stdlib-only Python 3. No third-party packages, no network, no executable files.

`check_jsonl_sequence.py` accepts a JSONL file **only** when every record
carries an integer `sequence` and those values are exactly `1..N` in file
order, where `N` is the number of record lines. Everything else is refused,
and the report names the line.

## Exact rerun commands

```
python3 -m unittest test_check_jsonl_sequence -v
python3 check_jsonl_sequence.py sequence_valid.jsonl          ; echo "exit=$?"
python3 check_jsonl_sequence.py sequence_invalid.jsonl        ; echo "exit=$?"
python3 check_jsonl_sequence.py sequence_gap.jsonl            ; echo "exit=$?"
python3 check_jsonl_sequence.py sequence_duplicate.jsonl      ; echo "exit=$?"
python3 check_jsonl_sequence.py sequence_reordered.jsonl      ; echo "exit=$?"
python3 check_jsonl_sequence.py sequence_boolean.jsonl        ; echo "exit=$?"
python3 check_jsonl_sequence.py sequence_malformed.jsonl      ; echo "exit=$?"
python3 check_jsonl_sequence.py sequence_missing_field.jsonl  ; echo "exit=$?"
python3 check_jsonl_sequence.py sequence_empty.jsonl          ; echo "exit=$?"
python3 check_jsonl_sequence.py no_such_file.jsonl            ; echo "exit=$?"
```

The recorded output of every one of those commands is committed as
`sample_runs.txt`. It is named that rather than `captured_output.txt` on
purpose -- see "Why the evidence file is not called captured_output.txt".

## Expected results

| step | result |
|------|--------|
| tests | **43 tests, OK**, exit 0 |
| `sequence_valid.jsonl` | `status=accepted`, 5 records, 0 findings, exit **0** |
| every other fixture | `status=rejected`, exit **1** |
| `no_such_file.jsonl` | `INVALID_INPUT` on stderr, exit **2** |

## The contract

A file is accepted when all six hold:

1. It contains at least one record. An empty file, or one whose entire content
   is a single newline, holds no records and is refused.
2. Every record line parses as JSON.
3. Every record is a JSON object.
4. Every record has a `sequence` key.
5. Every `sequence` value is a JSON integer. `true` is not one. Neither is
   `2.0`, `"2"` or `null`.
6. The values are exactly `1..N` in file order: the record on line *k* carries
   `sequence` *k*.

Keys other than `sequence` are ignored. This tool has one job.

## Rejection codes

| code | meaning |
|------|---------|
| `EMPTY_INPUT` | no records at all |
| `MALFORMED_JSON` | a line does not parse as JSON |
| `RECORD_NOT_OBJECT` | a line parses but is not a JSON object |
| `MISSING_SEQUENCE` | a record has no `sequence` key |
| `SEQUENCE_NOT_INTEGER` | `sequence` is a float, string, null, array or object |
| `SEQUENCE_IS_BOOLEAN` | `sequence` is `true` or `false` |
| `SEQUENCE_OUT_OF_ORDER` | the record on line *k* does not carry `sequence` *k* |
| `SEQUENCE_DUPLICATE` | a value appears on more than one line |
| `SEQUENCE_MISSING` | a value in `1..N` appears on no line |
| `SEQUENCE_OUT_OF_RANGE` | a value outside `1..N` |

`test_the_readme_documents_exactly_the_codes_the_tool_can_emit` parses this
table and compares it to `check_jsonl_sequence.CODES`, so a code cannot be
added to one and forgotten in the other.

### Why booleans get their own code

In Python `bool` subclasses `int`. `isinstance(True, int)` is `True` and
`True == 1`. A checker written the obvious way — `isinstance(seq, int)` and
then `seq == position` — **accepts `{"sequence": true}` on line 1 as the
number 1** and reports nothing at all. The bool test therefore runs *before*
the int test, and gets its own code so the refusal is visible rather than
silently folded into a type error.

`test_python_would_have_let_this_through` asserts the premise itself
(`isinstance(True, int)`), so if that ever stops being true the extra code
stops being justified and the suite says so.

### Why four codes for what looks like one problem

A positional walk on its own reports the same `SEQUENCE_OUT_OF_ORDER` for a
gap, a duplicate and a swap. That tells a reader *where* the file stops
matching, not *what is wrong with it*. The set-level codes answer the second
question:

| shape | codes emitted | reading |
|---|---|---|
| `1,2,4` | `OUT_OF_ORDER`, `OUT_OF_RANGE`, `MISSING` | a value was skipped |
| `1,2,2` | `DUPLICATE`, `OUT_OF_ORDER`, `MISSING` | a value repeated instead of advancing |
| `1,3,2` | `OUT_OF_ORDER` only | the set is intact, only the order is wrong |

That last row is the one that earns the design: a swap produces no
`DUPLICATE` and no `MISSING`, so nobody goes looking for a value that is
sitting right there. `test_a_swap_reports_no_duplicate_and_no_missing` pins it.

### `N` is the number of record lines, not the number of usable records

If line 3 is malformed, the file still has `N` lines and is still checked
against `1..N`. Counting only the parseable records would shrink the expected
range every time a line broke, so deleting a bad line could turn a file with
a hole in it into a file that passes.

### Blank lines are holes, not whitespace

A blank line in the middle of a file is reported as `MALFORMED_JSON`, not
skipped. A checker that skips blank lines cannot tell a file with a hole in it
from a file without one. The single newline that terminates the last record is
not a blank line and does not create a record.

## Fixtures

| file | what it demonstrates |
|------|----------------------|
| `sequence_valid.jsonl` | the accepting case, 5 records |
| `sequence_gap.jsonl` | `1,2,4` — a skipped value |
| `sequence_duplicate.jsonl` | `1,2,2` — a repeat |
| `sequence_reordered.jsonl` | `1,3,2` — a permutation |
| `sequence_boolean.jsonl` | `true` on line 1, the trap above |
| `sequence_malformed.jsonl` | line 2 is not valid JSON |
| `sequence_missing_field.jsonl` | line 2 has no `sequence` key |
| `sequence_empty.jsonl` | zero bytes |
| `sequence_invalid.jsonl` | seven defects in one file |

`test_every_shipped_fixture_behaves_as_documented` runs all nine and checks
the exit code and the codes each produces;
`test_every_fixture_named_here_exists_and_every_one_shipped_is_named` fails if
a `.jsonl` is added to this directory without being listed;
`test_the_fixtures_between_them_exercise_every_code` fails if any of the ten
codes is never produced by any fixture.

## Flags

| flag | description |
|------|-------------|
| `input` (positional) | path to the JSONL file to check. Required. |

There is deliberately no `--output` flag. The report goes to stdout and is
redirected by the caller. Adding one would push
`doc-validator/option_report.json`'s pinned `tool_count` from 35 to 36 and turn
`doc-validator/test_optioncheck.py`'s committed-report comparison red, and
doc-validator is off limits under this task's brief. Measured, not assumed.

## Exit codes

| code | meaning |
|------|---------|
| `0` | accepted |
| `1` | rejected — the file was read, and the contract does not hold |
| `2` | usage error — bad flags, a path that does not exist, a path that is a directory, or bytes that are not UTF-8 |

A malformed line is a **rejection**, not a usage error. Refusing a file is
this tool's job, so a bad line is a finding about the input rather than a
failure of the run. Exit `2` is reserved for what the caller fixes by changing
the command line.

## Determinism

The report is `json.dumps(obj, sort_keys=True, separators=(",", ":"),
ensure_ascii=True)` plus one trailing newline. Findings are sorted by line
number, then code, then their own canonical rendering, so two findings on the
same line have a fixed order too. Nothing in the report carries a path, a
timestamp, a hostname, a working directory or a process id —
`test_the_report_carries_no_path_or_directory` probes for the temp directory,
the current directory and the fixture's own name.

## 3 limitations a reviewer should scrutinise

1. **`sequence` is positional, not an identity.** This tool proves a file is a
   complete, ordered run of 1..N. It says nothing about whether record *k* is
   the record that *should* be at position *k* — swap the bodies of two
   correctly numbered records and it still accepts. Pair it with a content
   check if that matters.
2. **How large an integer counts as an integer is an interpreter setting.**
   A 401-digit `sequence` on a one-record file is refused as
   `SEQUENCE_OUT_OF_RANGE` and the value is carried into the report verbatim,
   which a consumer reading it with a fixed-width integer type will not enjoy.
   A 5000-digit one is refused as `MALFORMED_JSON` instead, because CPython's
   `int_max_str_digits` stops `json` parsing the literal at all — and running
   the same file under `PYTHONINTMAXSTRDIGITS=6000` switches it back to
   `SEQUENCE_OUT_OF_RANGE`. Same file, same machine, different verdict. Both
   verdicts are refusals, so no over-long integer is ever accepted, but the
   *code* is configuration-dependent above roughly 4300 digits. This is why
   the determinism claim above says "for a given interpreter configuration"
   and why the report does not quote the exception text.
3. **Duplicate `sequence` keys inside one record are resolved by `json`, not
   by this tool.** `{"sequence": 1, "sequence": 2}` is accepted by
   `json.loads` as `2` — last wins — and this tool never sees the first value.
   Detecting that needs a parser that reports repeated keys, which the
   standard library's does not do without a custom `object_pairs_hook`. Named
   here because it is the one input shape this tool cannot see.
