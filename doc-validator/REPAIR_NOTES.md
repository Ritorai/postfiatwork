# Documentation Repair Notes

Repair pass against `doc-validator/docval.py` findings for the postfiatwork
repository. Goal: DOC001 (undocumented flag) and DOC007 (no README) both to
zero, without weakening the validator or inventing behaviour not present in
the code.

## Before / after counts

Baseline: `doc-validator/before_report.json` (generated first, before any
edit). Final: `doc-validator/after_report.json` (generated after all edits).

| code | before | after |
|------|-------:|------:|
| DOC001_UNDOCUMENTED_FLAG | 16 | **0** |
| DOC002_PHANTOM_FLAG | 47 | 47 |
| DOC006_COMMAND_BLOCK_UNPARSEABLE | 138 | 142 |
| DOC007_NO_README | 2 | **0** |
| DOC008_NO_CLI | 1 | 1 |
| **total findings** | **204** | **190** |

DOC001 and DOC007 both reached zero, which is the task's success
criterion. DOC002/DOC006/DOC008 are pre-existing, out of scope for this
task, and explicitly allowed to remain non-zero per the task brief.

### Why DOC006 went from 138 to 142 (not a regression)

Per-file diff of DOC006 shows the count is unchanged for every
pre-existing file; two files that already had a DOC006 finding
(`nondeterminism-scanner/README.md`, `payload-validator/README.md`) show
the same finding at a shifted line number only, because the new `## Flags`
section was inserted earlier in the file. The net +4 is entirely new:
+1 in `evidence-validator/README.md` and +3 in
`reward-reconciler/README.md` -- both brand-new READMEs whose "Exact
rerun commands" blocks intentionally match the sibling convention used
throughout this repo (`python3 -m unittest ... -v`, `sha256sum`, `cmp a b
&& echo BYTE-IDENTICAL`). Those exact command shapes are refused by
docval's command-execution safety contract (no `-m`, no shell
metacharacters) in essentially every other tool's README already -- 138
of these findings existed before any edit here. Matching the established
convention was explicitly requested by the task ("must match the
conventions of the sibling READMEs"); rewriting only these two READMEs'
command blocks into single, pipe-free, non-`-m` invocations to dodge
DOC006 would make them inconsistent with every sibling tool and was not
requested. DOC006 is explicitly out of scope for the pass/fail bar in
this task.

## Files modified (added a `## Flags` section documenting every real
argparse option, values read directly from each tool's `main()`)

- `budget-forecaster/README.md` -- flags: `-k/--open-tasks`, `-o/--out`, `--horizon-weeks`, `--budget-cap`
- `event-linter/README.md` -- flags: `-o/--out`
- `evidence-manifest/README.md` -- flags: `-o/--out` (build subcommand only)
- `evidence-scorer/README.md` -- flags: `-c/--config`, `-o/--out`, `--threshold`, `--target-length`, `--artifact-target`
- `lifecycle-linter/README.md` -- flags: `-o/--out`
- `nondeterminism-scanner/README.md` -- flags: `--root`, `-o/--output`, `--rule`, `--min-severity`
- `payload-validator/README.md` -- flags: `-o/--output`, `--max-memo-bytes`
- `sybil-detector/README.md` -- flags: `-c/--config`, `-o/--out`, `--length-tolerance`, `--burst-window`, `--link-threshold`, `--alert-threshold`
- `throughput-reporter/README.md` -- flags: `-o/--out`, `--refusal-ceiling`, `--min-tasks`
- `xrpl-address/README.md` -- flags: `-d/--denylist`, `-o/--out`
- `xrpl-auditor/README.md` -- flags: `-o/--out`

In every case the section documents *all* of that tool's real flags
(including ones that were already discoverable via the "Exact rerun
commands" block), not just the ones docval flagged as missing -- a
partial table would satisfy the validator but not the actual goal of
"users can discover every real CLI option."

## Files created

- `evidence-validator/README.md` -- new. `evidence-validator/` had
  `validator.py` (a real argparse CLI, 17 passing unit tests) and no
  README at all (DOC007). Read `validator.py` end to end plus
  `test_validator.py` and `run_output.txt`, then wrote a README with:
  what the tool does, input shape, the 8 issue codes, a `## Flags` table
  (`input` positional, `--pretty`), exit codes (0/1/2), a reproducible
  command block (re-run and verified live -- 17 tests, exit 0/1/2 as
  documented), and a "Determinism" section that is deliberately honest
  that this tool's JSON is *not* byte-canonicalized the way sibling
  tools' output is (no `sort_keys`, no fixed separators on the
  top-level object -- confirmed by reading `main()`, which calls plain
  `json.dumps(summary, indent=2 if args.pretty else None)`).

- `reward-reconciler/README.md` -- new. `reward-reconciler/` had
  `reconcile.py` (real argparse CLI, 23 passing unit tests) and only
  `RUN_COMMANDS.md`, not `README.md` (DOC007). Read `reconcile.py` and
  `RUN_COMMANDS.md`, verified the RUN_COMMANDS.md claims live (23 tests
  pass; both reconciliation runs exit 1; both output files hash to
  `bc5a197234abcba48ef039e9d0f3dd20c590dfa9782c057481550c8c7d9e7b56` and
  are byte-identical), then wrote a full README matching sibling
  convention (What this tool does / Input shape / Flags / Exit codes /
  Determinism guarantees / Exact rerun commands / Expected results /
  Fixture contents), all filenames cross-checked against the actual
  directory listing (`reconcile.py`, `test_reconcile.py`,
  `expected_rewards.json`, `recorded_payouts.json`,
  `expected_report.json`). `RUN_COMMANDS.md` was left in place (its
  content is correct and it is cross-referenced from the new README) --
  the task only required a README that stands alone, not the removal of
  the old file.

## The 16 previously-undocumented flags, with real behaviour as read from code

| # | tool | flag | behaviour (from the code) |
|---|------|------|----------------------------|
| 1 | budget-forecaster | `--horizon-weeks` | Weeks to project burn forward. `Decimal` string, default `"4"`. Negative rejected as `INVALID_INPUT` (exit 2). |
| 2 | budget-forecaster | `--open-tasks` (`-k`) | Path to JSON array of open-task estimates. Optional; omitted means 0 committed spend. |
| 3 | budget-forecaster | `--out` (`-o`) | Write report to file instead of stdout; prints a one-line `status=... projected_total=...` summary when used. |
| 4 | event-linter | `--out` (`-o`) | Write report to file instead of stdout; prints `status=... violations=...` summary when used. |
| 5 | evidence-manifest | `--out` (`-o`) | `build` subcommand only. Write manifest to file instead of stdout; prints `batch_root=...`/`records=...` when used. `verify` has no such flag. |
| 6 | evidence-scorer | `--config` (`-c`) | Path to JSON config overriding `weights`/`target_length`/`artifact_target`/`threshold`. Unknown keys rejected (exit 2). Optional. |
| 7 | evidence-scorer | `--out` (`-o`) | Write report to file instead of stdout; prints `status=... passed=... failed=...` summary when used. |
| 8 | lifecycle-linter | `--out` (`-o`) | Write report to file instead of stdout; prints `status=... findings=...` summary when used. |
| 9 | nondeterminism-scanner | `--output` (`-o`) | Write report to file instead of stdout (the short form `-o` was already documented; only the long form was missing). |
| 10 | payload-validator | `--output` (`-o`) | Write report to file instead of stdout; prints `status=... ok=... findings=...` summary when used (long form was missing; `-o` was already documented). |
| 11 | sybil-detector | `--config` (`-c`) | Path to JSON config overriding `weights`/`length_tolerance`/`burst_window`/`link_threshold`/`alert_threshold`. Unknown keys/signal names rejected (exit 2). Optional. |
| 12 | sybil-detector | `--out` (`-o`) | Write report to file instead of stdout; prints `status=... clusters=... alerting=...` summary when used. |
| 13 | throughput-reporter | `--out` (`-o`) | Write report to file instead of stdout; prints `status=... contributors=... over_ceiling=...` summary when used. |
| 14 | xrpl-address | `--denylist` (`-d`) | Path to JSON array of denylisted address strings. Optional; checked only after structural validation passes. |
| 15 | xrpl-address | `--out` (`-o`) | Write report to file instead of stdout; prints `status=... valid=... invalid=...` summary when used. |
| 16 | xrpl-auditor | `--out` (`-o`) | Write report to file instead of stdout; prints `status=... issues=...` summary when used. |

All 11 `--out`/`--output`-style flags share one behaviour: write the
canonical JSON report to the given path instead of stdout, and print a
short human-readable one-line summary to stdout in its place. That
pattern was verified independently in each tool's `main()`, not assumed.

## A mistake caught and fixed during this pass

The first draft of `evidence-validator/README.md` included the sentence
"There is no `-o`/`--out` flag" to make clear the tool has no file-output
option. docval's flag scanner is a plain text-token regex -- it does not
parse negation -- so it read the literal strings `-o` and `--out` as
*documented* flags and raised two new DOC002_PHANTOM_FLAG findings that
were not present in the baseline. Caught by re-running docval after the
first draft (the after-report showed DOC002 at 49, not the baseline 47);
reworded to "This tool has no file-output option of any kind" (no
flag-shaped tokens), re-ran, DOC002 dropped back to the original 47.
Lesson: describing an *absent* flag in a docval-scanned README must avoid
writing the flag's literal token text at all, not just phrase it as a
negative.

## Test suite

`cd doc-validator && python3 -m unittest test_docval -v`:
**`Ran 188 tests` / `OK`** -- unchanged, since no code in `docval.py` was
touched (only README.md content in other tool directories, plus this
directory's own README.md was left untouched too).

## Anything else found along the way

- `reward-reconciler`'s `RUN_COMMANDS.md` claims were independently
  verified against a live run rather than trusted at face value (test
  count, exit codes, and the SHA-256 of the reconciliation report all
  matched). No discrepancy found there.
- No other filename-vs-code discrepancy was found for `reward-reconciler`
  anywhere in the repo (`contradiction-detector/checkers/reward-reconciler/
  reconcile.py` resolves correctly on disk; `REPO_README.md` and the
  other sibling READMEs that reference `reward-reconciler` all use the
  correct `reconcile.py` filename). The task's "correct reward-reconciler
  filename references" appears to describe the outcome of writing an
  accurate README with correct filenames throughout (which the new
  README does), rather than pointing at a separate, pre-existing wrong
  filename defect -- none was found.
- `evidence-manifest`'s `--out` only applies to the `build` subcommand,
  not `verify`; this is easy to miss and is called out explicitly in the
  new Flags section so nobody documents/tries `--out` on `verify`.
