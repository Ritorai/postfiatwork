# Evidence Integrity Validator

Stdlib-only Python 3 (`argparse`, `json`, `re`, `collections.Counter`). No
third-party packages, no network. CI-ready pre-reward check for Task Node
evidence records: validates record shape, checks CID / transaction-hash /
task-id formats, and flags duplicate submissions before any reward is
finalized.

## What this tool does

Reads a JSON array of evidence records and, for each one, checks:

- all five required fields are present and non-empty
- `cid` matches an IPFS CIDv0 (`Qm` + 44 base58 chars) or CIDv1 (`bafy` +
  50+ lowercase base32 chars) shape
- `tx_hash` is exactly 64 uppercase hex characters
- `task_id` matches `task_` + 32 lowercase hex characters
- `submission_id` is not reused across records in the same input
  (`DUPLICATE_SUBMISSION_ID`, flagged on **every** record that shares it)
- `cid` is not reused across records in the same input
  (`DUPLICATE_CID_REFERENCE`, flagged on **every** record that shares it)

A record with zero issues is `"status": "clean"`; any issue makes it
`"rejected"`. The process exits non-zero if any record was rejected.

## Input shape

A JSON array of objects, each with:

| field | type | required |
|-------|------|----------|
| `submission_id` | non-empty string | yes |
| `task_id` | non-empty string, must match `task_[0-9a-f]{32}` | yes |
| `wallet` | non-empty string | yes |
| `cid` | non-empty string, must match a CIDv0 or CIDv1 shape | yes |
| `tx_hash` | non-empty string, must match `[0-9A-F]{64}` | yes |

A top-level element that is not a JSON object is reported as a single
`RECORD_NOT_OBJECT` issue and does not crash the run. A top-level value
that is not a JSON array at all is a fatal input error (exit 2), not a
per-record finding.

## Issue codes

| code | meaning |
|------|---------|
| `MISSING_FIELD:<field>` | one of the five required fields is absent |
| `EMPTY_FIELD:<field>` | the field is present but not a non-empty string |
| `RECORD_NOT_OBJECT` | the array element itself is not a JSON object |
| `MALFORMED_CID` | `cid` is present and non-empty but matches neither CID shape |
| `MALFORMED_TX_HASH` | `tx_hash` is present and non-empty but not 64 uppercase hex chars |
| `MALFORMED_TASK_ID` | `task_id` is present and non-empty but not `task_` + 32 lowercase hex |
| `DUPLICATE_SUBMISSION_ID` | this record's `submission_id` also appears on another record in the same input |
| `DUPLICATE_CID_REFERENCE` | this record's `cid` also appears on another record in the same input |

Format checks (`MALFORMED_*`) only run when the field is present and
non-empty; a missing/empty field is reported once via `MISSING_FIELD` /
`EMPTY_FIELD` rather than also tripping a format check on `None`/`""`.

### The anchors are `\Z`, not `$`

Every format pattern in `validator.py` ends with `\Z`. This is not a
style choice, and it was not always true.

In Python, `$` matches at the end of the string **or just before a
single newline at the end of the string**. `^[0-9A-F]{64}$` therefore
accepted a *sixty-five* character value ending in `"\n"` — which is not
"exactly 64 uppercase hex characters", the rule stated above, and not
what the schema table's `[0-9A-F]{64}` says either. The same
one-character hole was in all four patterns, so a `cid`, a `tx_hash` and
a `task_id` could each carry a trailing newline and the record came back
`"status": "clean"` with exit `0`. `\Z` has no such exception: it
matches only at the true end of the string.

Exactly one trailing newline slipped through and nothing else did — not
two newlines, not a newline followed by anything, not a trailing space,
tab or CRLF, not a leading newline. `TRAILING_NEWLINE_EVIDENCE.txt`
measures that against the pre-fix patterns read out of Git, pattern by
pattern and case by case, and `sample_trailing_newline.json` is the
committed fixture: three records that differ from valid ones by exactly
one `"\n"` each, producing one `MALFORMED_*` code apiece and nothing
else.

The repair changed no error code, no flag and no exit code. A value that
fails the documented format produces the `MALFORMED_*` code already
listed above, and the process exits `1`, already documented below as
"one or more records had validation issues".

Three tests keep it from coming back. Two of them name no pattern:
`test_no_module_pattern_uses_a_dollar_anchor` and
`test_every_start_anchored_pattern_is_end_anchored` discover the
compiled patterns by walking this module, so a fifth pattern written
with a `$` fails them without anyone having remembered to extend a
list. The third, `test_introspection_finds_the_four_documented_patterns`,
does name them, and deliberately: it fails if the patterns move out of
module scope and leave that discovery covering nothing — and, as a
side effect, a correctly written fifth pattern also fails it until its
known-good value is added to `GOOD_FOR_PATTERN`. That is the intended
trade. Silent under-coverage is the failure worth preventing; being
made to add one line is not.

## Flags

| flag | description |
|------|-------------|
| `input` (positional) | Path to a JSON file containing an array of evidence records. Required. |
| `--pretty` | `store_true`, default off. Indent the JSON summary with `json.dumps(..., indent=2)` for human reading. Without it, output is compact single-line JSON (`json.dumps(summary)` with default separators — this is **not** the `sort_keys`/fixed-separator canonical form some sibling tools use; see "Determinism" below). |

This tool has no file-output option of any kind. The summary is always
written to stdout; redirect it yourself (`> report.json`) if you need it
in a file.

## Determinism

Key order in the emitted JSON follows Python dict insertion order (the
order fields are assigned in the code), not `sort_keys=True`, and there is
no `ensure_ascii` or fixed-separator normalization. Two runs on the same
input produce the same content, but the exact byte layout is not pinned
the way `reconcile.py`-style tools in this repo pin theirs (no
`sha256sum`-stable byte-identity contract here). `issue_totals` itself
*is* built with `dict(sorted(...))`, so that one sub-object is
key-sorted regardless.

## Exit codes

| code | meaning |
|------|---------|
| 0 | all records clean |
| 1 | one or more records had validation issues |
| 2 | processing error: input file not found, invalid JSON, or top-level JSON value is not an array |

## Exact rerun commands

```
python3 -m unittest test_validator -v
python3 validator.py sample_valid.json   --pretty ; echo "exit=$?"
python3 validator.py sample_invalid.json          ; echo "exit=$?"
python3 validator.py sample_invalid.json --pretty ; echo "exit=$?"
python3 validator.py /nonexistent.json            ; echo "exit=$?"
```

`python3 validator.py sample_trailing_newline.json --pretty` is captured
too, under "Two transcripts" below, and is described in "The anchors are
`\Z`, not `$`". It is kept out of the list above on purpose:
transcript-drift counts a documented rerun command with no
`=== $ ... ===` record against the tool, and this file cannot gain a
fourth such record — see below.

## Expected results

| step | result |
|------|--------|
| tests | `Ran 36 tests` / `OK`, exit **0** |
| `sample_valid.json --pretty` | `totals: {"records": 2, "clean": 2, "rejected": 0}`, exit **0** |
| `sample_invalid.json` | `totals: {"records": 3, "clean": 0, "rejected": 3}`, exit **1** |
| `sample_invalid.json --pretty` | same content, indented | exit **1** |
| `sample_trailing_newline.json --pretty` | `totals: {"records": 3, "clean": 0, "rejected": 3}`, one `MALFORMED_*` per record, exit **1** |
| missing file | `{"error": "FILE_NOT_FOUND", "path": "/nonexistent.json"}` on stderr, exit **2** |

## Two transcripts

`captured_output.txt` and `run_output.txt` are byte-identical copies of
the same capture, and `mk_artifacts.sh` writes both from one run so they
cannot drift apart. Every command in the rerun list above appears, and
so does the trailing-newline fixture and the interpreter version:

| step | header form |
|------|-------------|
| `python3 -m unittest test_validator -v` | `=== $ ... ===` record |
| `sample_valid.json --pretty` | `=== $ ... ===` record |
| `sample_invalid.json --pretty` | `=== $ ... ===` record |
| `python3 --version` | plain `$` step |
| `sample_trailing_newline.json --pretty` | plain `$` step |
| `sample_invalid.json` | plain `$` step |
| `/nonexistent.json` | plain `$` step |

The split is not aesthetic. index-generator pins a repository-wide
count of `=== $ ... ===` records at 548 — in its committed
`pipe_classification_report.json` and its README, not in its transcript
— and index-generator is off limits under the brief the four plain
steps were added under. Promoting them takes that count to 552 and
turns `test_pipe_classify.TestCommittedReportIsFresh` red. A plain step also
carries no `exit=` line, because FORMAT.md reserves that as a record
terminator and transcript-schema reads a stray one as a second
terminator inside the preceding record. Every exit code involved is
recorded on a real terminator in `TRAILING_NEWLINE_EVIDENCE.txt`.

One earlier version of this file left the missing-file command out
entirely, on the theory that its absolute path would register with
env-leak-scanner. That was wrong: `EL-ABS-POSIX` requires two or more
path segments and `/nonexistent.json` has one, so the step is captured
here and confirmed leaks stay at 851. Multi-segment absolute paths are
a different matter and are kept out of every committed artifact in this
directory — including out of this paragraph, which is why it describes
them rather than quoting one.

## Files

| file | what it is |
|------|------------|
| `validator.py` | the tool |
| `test_validator.py` | its suite |
| `sample_valid.json` | two clean records |
| `sample_invalid.json` | three records covering missing/empty fields, malformed references and duplicates |
| `sample_trailing_newline.json` | three records that differ from valid ones by exactly one trailing newline each — see "The anchors are `\Z`, not `$`" |
| `captured_output.txt`, `run_output.txt` | the capture described under "Two transcripts" |
| `TRAILING_NEWLINE_EVIDENCE.txt` | before/after for the anchor repair, against the pre-fix source read out of Git |
| `mk_artifacts.sh` | regenerates all three of the above: `bash mk_artifacts.sh` |

## What this change leaves for readme-index

The suite went from 17 tests to 36, and the repository's root
`README.md` index row moved with it (17 to 36, and the two aggregate
figures 4,202 to 4,221) — the same three-line edit two earlier commits
in this repository make under the title "root README: re-derive the
index row readme-index's own gate reads".

That is only half of the pair those commits come in. The other half
lives in `readme-index/`, whose `corpus.tsv` is a pinned extraction of
every tool README and still carries this file's old row. The brief this
change was made under puts `readme-index` off limits, so the corpus is
left as it is. The consequence, measured rather than described:

| run | parent | here |
|-----|--------|------|
| `readmeindex.py --corpus corpus.tsv --root-readme root_readme_after.md` | exit 0 | exit 0 |
| `readmeindex.py --corpus corpus.tsv --root-readme ../README.md` | exit 0, no differences | **exit 1**, two differences |
| `readmeindex.py --root .. --root-readme ../README.md` | 5 `count_differs` | 5 `count_differs` |

The gate — the first row, corpus against readme-index's own mirror — is
unaffected. The second row is the cost of doing the root-README half
without the corpus half: one `count_differs` (corpus says 17, the root
index says 36) **and** one `aggregate_differs` (corpus sums to 4,202,
the root index now says 4,221), with the exit code flipping 0 to 1.
The third row is what the root-README edit buys: without it, this
change would have added a sixth `count_differs` there, for this tool.

Two further consequences of the same split, so nobody has to discover
them: `readme-index/README.md` still states 4,202 in its own prose, so
the repository carries two aggregate figures until the corpus is
re-derived; and the root `README.md` is no longer byte-identical to
`readme-index/root_readme_after.md`, as it was at the parent. Nothing
reads that pair — both of that directory's own comparisons rewrite the
mirror into a scratch copy first — but it was true before and is not
now.

The follow-up is one command in that directory, exactly as
`b394034 readme-index: re-derive after reward-reconciler went 23 to 50`
did it: re-extract this README's rows into `corpus.tsv`, then regenerate
`root_readme_after.md` from it.

`shebang-mode` is stale for a related reason and also off limits. Its
committed self-scan says 589 tracked files; it was already three behind
at the parent commit, and this change adds three more, so a live scan
now reports 595. Its `SM002` total is unaffected at 150 — the
regenerator here carries no shebang, precisely because nothing in this
repository can be committed with an executable bit, which is the
condition that rule reports.

## Scope limit

This tool checks record *shape* and *internal consistency* (format,
completeness, duplication) only. It does not query IPFS to confirm a CID
resolves to real content, and it does not query the XRPL to confirm a
`tx_hash` corresponds to a real, confirmed transaction — both would need
network access, which is deliberately out of scope here.
