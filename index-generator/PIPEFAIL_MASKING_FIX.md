# PIPEFAIL_MASKING_FIX.md — closing the exit-masking gap in piped records

## 0. The brief's count was stale, verified and corrected first

The task brief said "12 known piped-command records ... across 10 tool
directories." Before touching anything, `index-generator/pipe_scan.py
--repo-root .` was run against the tree as received:

```
transcript_files_scanned: 49
total_command_records:    518
total_files_with_a_piped_record: 11
total_piped_records:      13
```

Full raw output: `pipefail_fix_reports/pipe_scan_before.json`.

**13 records across 11 directories, not 12/10.** The delta from the
brief's stale number is exactly one directory, `bundle-index`: its
transcript was migrated from bare `$ cmd` lines to `=== $ cmd ===`
records by a separate, prior delivery
(`transcript-drift/SEVEN_TRANSCRIPTS.md`), which made a piped record in
it visible to `pipe_scan.py` for the first time — `pipe_scan.py` only
looks at promoted `=== $ ... ===` headers, so an unpromoted bare line
with a `|` in it was invisible to the scanner until that migration
landed. Nothing in that migration is touched by this delivery.

## 1. Every flagged record was read by hand, not trusted at face value

`pipe_scan.py` does substring matching on header text (`"|" in command`),
not shell-aware parsing. `index-generator/README.md` already documents
one false positive this causes (a `grep` regex-alternation pattern, not a
shell pipe). Reading all 13 flagged records by hand — not just the one
already documented — found **five more of the same shape**, none of them
previously disclosed:

| # | Tool | Line | Flagged text | Real shape | Verdict |
|---|------|------|---------------|------------|---------|
| 1 | `bundle-index` | 258 | `grep -c "/sessions\|/tmp\|/home" r1.json` | `grep` regex alternation inside a quoted pattern; one process, no pipe | **FALSE POSITIVE** |
| 2 | `claim-checker` | 310 | `grep -c "/tmp\|/home\|/sessions" r1.json (expect 0)` | same shape | **FALSE POSITIVE** |
| 3 | `commit-claim-auditor` | 73 | `grep -cE 'time\.time\|utcnow\|now\(\)' claimhist.py` | same shape (`-E`, same reasoning) | **FALSE POSITIVE** |
| 4 | `exit-harness` | 184 | `test -f out_bad.json && echo EXISTS \|\| echo NOT_CREATED` | `\|\|` is the shell **OR** operator, not a pipe; no process's stdout feeds another's stdin | **FALSE POSITIVE** |
| 5 | `index-generator` | 453 | `grep -n 'time\.time\|utcnow\|now()' indexgen.py; echo grep_exit=$?` | `grep` regex alternation; already documented in `index-generator/README.md` "Finding 4" | **FALSE POSITIVE (pre-existing, documented)** |
| 6 | `report-freshness` | 29 | `grep -c "/tmp\|/root\|/home" freshness_report.json` | same shape as #1/#2 | **FALSE POSITIVE** |

None of these six is "fixed" here — there is no pipe in any of them to
fix, and rewriting a working, correct `grep` invocation to make a naive
scanner happy would be optimizing the checker's output instead of the
evidence. Per the task's own instruction (reused verbatim from
`index-generator/README.md`'s precedent): a scanner that reports its own
false positives honestly is worth more than one tuned to show zero.
`index-harness`'s `&&`/`||` case (#4) is a new false-positive *shape* not
previously seen in this repository's disclosures — logical operators, not
just regex alternation, can also trip a naive `"|" in text` check.

The remaining **7 records across 6 directories** are real pipes with a
real exit-masking defect, verified live (see §3), and are what this
delivery fixes:

| # | Tool | Line | Command |
|---|------|------|---------|
| 1 | `commit-claim-auditor` | 5 | `python3 -m unittest test_claimhist -v 2>&1 \| tail -25` |
| 2 | `dup-detector` | 193 | `cat report_run1.json \| head -c 300; echo '...[truncated...]'` |
| 3 | `payload-validator` | 211 | `cat payloads_bad.json \| python3 payload_validate.py - ; echo "exit=$?"` |
| 4 | `queue-auditor` | 200 | `cat snapshot_dirty.json \| python3 queue_audit.py - ; echo "exit=$?"` |
| 5 | `queue-auditor` | 208 | `echo '{not json' \| python3 queue_audit.py - ; echo "exit=$?"` |
| 6 | `transcript-schema` | 13 | `python3 -m unittest test_validate_transcript -v 2>&1 \| tail -6` |
| 7 | `wallet-reconciler` | 171 | `echo '{...NaN...}' \| python3 wallet_reconcile.py - ; echo "exit=$?"` |

## 2. Scoping check: which directories ship a `capture.sh`

Only `env-leak-scanner/`, `readme-index/`, `transcript-drift/`, and
`index-generator/` shipped a `capture.sh` before this task. **None of the
six directories above did.** This is disclosed, not glossed over: for
each of the six, this delivery **adds** a new, narrowly-scoped
`capture.sh` — it does not "restore" or "fix" a prior script, because
none existed. Each new script is scoped to rerunning ONLY its
directory's affected record(s), not regenerating the rest of that
directory's transcript (out of scope, and per the task's own
instruction, changing unrelated command records that other committed
reports depend on is handled separately, in a controlled order, by the
requester).

The seven legacy pre-`FORMAT.md` transcripts
(`transcript-drift/SEVEN_TRANSCRIPTS.md`'s scope: `bundle-index`,
`crosspath-runner`, `doc-validator`, `link-integrity`,
`nondeterminism-scanner`, `preflight`, `weak-assertion-scanner`) and the
missing-`exit=` migration that document covers are **untouched** by this
delivery. `bundle-index` appears in §1's false-positive table above (its
`captured_output.txt` is read, once, to classify one record) but no byte
of it is written by this delivery.

## 3. The bug, reproduced live, and why the fix has two shapes

`/bin/sh` on this system is `dash`:

```
$ sh -c 'set -o pipefail'
sh: 1: set: Illegal option -o pipefail
```

The shared `rec()` convention used by this repository's other
`capture.sh` scripts pipes commands through `sh -c "$*"`, which reports
only the LAST stage's exit status:

```
without pipefail:  false | true   -> rc 0
with pipefail:      false | true   -> rc 1     (bash -c 'set -o pipefail; false | true')
```

`index-generator/capture.sh` already fixed this for its own directory by
requiring `bash` and wrapping every record in
`bash -c 'set -o pipefail; ...'`. This delivery reuses that same
`rec()`-style fix, but reading each of the seven records by hand (never
generalising from one shape to all seven) found **two genuinely different
defect shapes**, not one:

**Shape A — `; echo "exit=$?"` embedded in the command itself**
(`payload-validator`, both `queue-auditor` records, `wallet-reconciler`).
Since the echo is the very next statement after the pipe, `$?` at that
point already reflects the pipeline's status exactly as bash computes
it — `set -o pipefail` on the SAME invocation is sufficient; the header
text does not need to change at all. Verified live:

```
$ sh -c 'cat does_not_exist.json | python3 -c "..." ; echo "exit=$?"'
cat: does_not_exist.json: No such file or directory
exit=0                                                    <- masked
$ bash -c 'set -o pipefail; cat does_not_exist.json | python3 -c "..." ; echo "exit=$?"'
cat: does_not_exist.json: No such file or directory
exit=1                                                    <- fixed
```

**Shape B — a trailing, unconditional statement after the pipe that is
NOT an exit-status echo** (`dup-detector`'s
`cat X | head -c 300; echo '<fixed label>'`, and originally
`commit-claim-auditor`/`transcript-schema`'s `... | tail -N` with no
trailing echo at all, captured by an outer `rec()`-style harness).
**`set -o pipefail` alone does NOT fix this shape** — pipefail only
changes the exit status of the pipeline itself, and that status is
immediately discarded the instant the next unconditional statement runs.
Verified live:

```
$ bash -c 'set -o pipefail; cat missing.json | head -c 300; echo label'
cat: missing.json: No such file or directory
label
$ echo "outer exit: $?"
outer exit: 0                                             <- STILL masked, even with pipefail
```

For Shape B, the fix restructures the command instead of merely adding
pipefail: `commit-claim-auditor`/`transcript-schema` drop `-v | tail -N`
in favour of a direct, unpiped `python3 -m unittest <module>` (reusing
`index-generator/capture.sh`'s Finding-1 precedent: don't pipe a
`unittest` run through a filter at all); `dup-detector` drops the
`cat | head; echo` pair in favour of a single Python process
(`open(...).read()[:300]`) that has no second stage and no trailing
statement to reset `$?` — a real failure (missing/unreadable file) now
raises and exits nonzero in the SAME process that would otherwise have
succeeded silently.

## 4. Per-record disposition

| Record | Disposition | Real exit observed |
|---|---|---|
| `commit-claim-auditor` `unittest -v \| tail -25` | **Rewritten (Shape B)** — capture.sh added; command changed to unpiped `python3 -m unittest test_claimhist` | `exit=0` (154 tests, real pass) |
| `dup-detector` `cat \| head; echo label` | **Rewritten (Shape B)** — capture.sh added; command changed to a single unpiped `python3 -c` | `exit=0` (real file, real truncation, byte-identical body to the original record) |
| `payload-validator` `cat \| python3 - ; echo exit=$?` | **Rerun under pipefail (Shape A)** — capture.sh added; header text unchanged | `exit=1` (byte-identical to original — this record's real output never depended on the bug) |
| `queue-auditor` `cat \| python3 - ; echo exit=$?` | **Rerun under pipefail (Shape A)** — capture.sh added; header text unchanged | `exit=1` (byte-identical) |
| `queue-auditor` `echo '{not json' \| python3 - ; echo exit=$?` | **Rerun under pipefail (Shape A)** — capture.sh added; header text unchanged | `exit=2` (byte-identical) |
| `transcript-schema` `unittest -v \| tail -6` | **Rewritten (Shape B)** — capture.sh added; command changed to unpiped `python3 -m unittest test_validate_transcript` | `exit=0` (194 tests, real pass) |
| `wallet-reconciler` `echo '{...NaN...}' \| python3 - ; echo exit=$?` | **Rerun under pipefail (Shape A)** — capture.sh added; header text unchanged | `exit=1` (byte-identical) |
| `bundle-index` `grep -c "...\|..."` | **False positive — left alone** | n/a, no pipe |
| `claim-checker` `grep -c "...\|..."` | **False positive — left alone** | n/a, no pipe |
| `commit-claim-auditor` `grep -cE '...\|...'` | **False positive — left alone** | n/a, no pipe |
| `exit-harness` `test ... && ... \|\| ...` | **False positive — left alone** | n/a, `\|\|` is OR, not a pipe |
| `index-generator` `grep -n '...\|...'` | **False positive — left alone (pre-existing, documented)** | n/a, no pipe |
| `report-freshness` `grep -c "...\|..."` | **False positive — left alone** | n/a, no pipe |

Every "rerun under pipefail" record reproduced **byte-identical** output
to the original committed record. This is expected, not a coincidence:
the fix changes how a FUTURE failure would be recorded, not today's
already-successful outcome — these commands were never observed to
actually fail in this environment, only shown to be capable of silently
reporting success if their first stage ever does (§3). No value in any
committed `captured_output.txt` was invented; every byte written by this
delivery came from an actual subprocess run, captured by the scripts
committed alongside it.

## 5. Tests

71 new focused regression tests, `test_capture_fix.py` in each of the six
affected directories (13 + 13 + 12 + 11 + 11 + 11), all using real
throwaway subprocesses/files in temp directories created by the tests
themselves — never the real `captured_output.txt` content as a test
fixture for the masking proof itself. Every module proves, at minimum:

1. **A failing FIRST pipeline stage, run the old way, records exit=0**
   (`false | true` under `sh -c`, and each directory's own real command
   shape reproduced with a throwaway failing suite / missing file).
   **This is the explicitly graded direction.**
2. The SAME pipeline, run the fixed way
   (`bash -c 'set -o pipefail; ...'`), records the real nonzero exit.
3. A fully-succeeding pipeline records exit=0 either way — pipefail must
   not manufacture a false failure.
4. `capture.sh` runs end-to-end, is idempotent (rerunning on an
   already-fixed file either reproduces byte-identical output, for
   Shape A, or refuses loudly rather than guessing, for Shape B — see
   `test_*_refuses_to_guess_when_*_absent`), and requires `bash`.

One additional regression was found and fixed WHILE building these
scripts, and is itself covered by a test
(`test_capture_sh_stops_and_propagates_failure_from_the_first_record` in
`queue-auditor/test_capture_fix.py`): `dup-detector/capture.sh` and
`queue-auditor/capture.sh` originally ended with an unconditional
`rm -f "$body_file"` cleanup statement AFTER the Python splice step —
which, if the splice failed (e.g. refused to guess), reset `$?` to the
cleanup command's own (successful) exit, silently swallowing the
failure. This is the exact same "trailing statement masks the real exit"
shape this whole delivery exists to fix, found in the delivery's own
fix scripts, and corrected the same way: capture the splice step's exit
status explicitly before running cleanup, then propagate it.

Reproduce:

```
cd commit-claim-auditor  && python3 -m unittest test_capture_fix -v
cd ../transcript-schema  && python3 -m unittest test_capture_fix -v
cd ../dup-detector       && python3 -m unittest test_capture_fix -v
cd ../payload-validator  && python3 -m unittest test_capture_fix -v
cd ../queue-auditor      && python3 -m unittest test_capture_fix -v
cd ../wallet-reconciler  && python3 -m unittest test_capture_fix -v
```

Each affected directory's full pre-existing suite
(`python3 -m unittest discover -p "test_*.py"`) also still passes,
unchanged in count except for the 71 new tests added:

```
commit-claim-auditor : 167 tests, OK   (154 pre-existing + 13 new)
transcript-schema    : 207 tests, OK   (194 pre-existing + 13 new)
dup-detector          : 122 tests, OK   (110 pre-existing + 12 new)
payload-validator     : 190 tests, OK   (179 pre-existing + 11 new)
queue-auditor          : 186 tests, OK   (175 pre-existing + 11 new)
wallet-reconciler      : 152 tests, OK   (141 pre-existing + 11 new)
```

## 6. Scanner output before/after

```
BEFORE: 13 piped records across 11 directories (518 total command records, 49 transcripts)
AFTER:  10 piped records across  9 directories (518 total command records, 49 transcripts)
```

Raw reports: `pipefail_fix_reports/pipe_scan_before.json` /
`pipe_scan_after.json`. The drop is exactly the 3 real masking bugs this
delivery eliminated by RESTRUCTURING their command (Shape B —
`commit-claim-auditor`'s and `transcript-schema`'s `unittest | tail`
records no longer contain a pipe at all; `dup-detector`'s record no
longer contains a pipe at all), which is why those directories disappear
from the list entirely (`commit-claim-auditor` remains in the list, but
now only for its unrelated false-positive `grep -cE` record).
`payload-validator`, `queue-auditor`, and `wallet-reconciler` still show
up in the AFTER report — **this is correct, not a residual defect**:
their pipes are the deliberate subject of the test (the CLI's documented
"read from stdin via `-`" code path) and were fixed by protecting them
with `set -o pipefail`, not by removing them. `index-generator`'s single
remaining record is the pre-existing, already-documented false positive
(§1, #5) — unchanged by this delivery, and explicitly not "fixed" because
there is no pipe in it.

## 7. `driftcheck.py` / `validate_transcript.py` before/after — the affected directories, and repo-wide

Repo-wide (`--root .`, all 49 directories), before vs after this
delivery's six file changes:

```
driftcheck.py            : 104 total findings, IDENTICAL before and after
                            (per-tool finding lists diffed exactly equal,
                            not just the aggregate count -- see
                            pipefail_fix_reports/driftcheck_before.json /
                            driftcheck_after.json)
validate_transcript.py    : 56 total findings, IDENTICAL before and after
                            (pipefail_fix_reports/validate_transcript_before.json /
                            validate_transcript_after.json)
```

Per-directory, for the six changed directories specifically, both
checkers report **zero new and zero resolved findings** — the header-text
changes (dropping `-v | tail -N`) did not happen to match or unmatch any
README-documented command string. `wallet-reconciler` carries 4
pre-existing `driftcheck.py` findings and 2 `TRANSCRIPT_RECORD_HAS_NO_EXIT`
+ 1 `TRANSCRIPT_RECORD_DUPLICATE_EXIT` `validate_transcript.py` findings —
all pre-existing, all unrelated to the record this delivery touched (line
171; the pre-existing findings are at lines 160/164, untouched), all
belonging to the separate missing-exit migration and deliberately left
alone here.

## 8. Relocation and determinism

See `pipefail_fix_reports/RELOCATION_EVIDENCE.txt` for full detail.
Summary: the whole tree was copied to a differently-named absolute path
(`/tmp/build_10/relocated_pipefail_check_xyz`, alongside the original at
`/tmp/build_10/repo`), every `capture.sh` was re-run again AT the
relocated path (not just diffed as a static copy), and every repo-wide
report generator was re-run there too.

* `diff -rq` between the two trees: no differences, before or after
  re-running capture.sh at the relocated path.
* `pipe_scan.py`, `driftcheck.py`, and `validate_transcript.py` repo-wide
  reports: byte-identical sha256 between the two paths, **no
  normalisation needed or applied** — none of this delivery's new/changed
  files embed an absolute path, hostname, or timestamp.
* All six `captured_output.txt` files: byte-identical sha256 between the
  two paths, both as a static copy AND after independently re-running
  capture.sh at each path.
* All 71 new tests: pass at both paths, same counts.

## 9. Committed reports expected to go stale

Per the task owner's explicit instruction, these are named but NOT
regenerated by this delivery (the requester regenerates them separately,
in a controlled order):

* `claim-crosscheck/sample_run.json` — enumerates transcripts repo-wide;
  will need to reflect the six changed `captured_output.txt` files.
* `regression-checker/baseline_coverage_report.json` — same reason.
* `transcript-schema/validation_report.json` — this delivery's own
  `pipefail_fix_reports/validate_transcript_after.json` shows the six
  directories individually validate cleanly (or with only their
  pre-existing, unrelated findings); the repo-wide committed
  `validation_report.json` predates this delivery's changes and will
  need a fresh run to reflect them.
* `report-freshness/freshness_report.json` — enumerates transcript
  staleness repo-wide; the six changed `captured_output.txt` files'
  modification times will now be newer than whatever this report last
  recorded.

## 10. Limitations — real failure modes

1. **`pipe_scan.py`'s false-positive rate is now known to be higher than
   previously disclosed, and this delivery's own false-positive
   corrections are themselves hand-classification, not a mechanical
   proof.** Six of the thirteen originally flagged records (46%) are
   false positives — not the one previously documented, but six, found
   only by reading every flagged line's actual shell semantics by hand.
   The `&&`/`||` shape (`exit-harness`) is a new pattern not previously
   catalogued. This delivery's classification of "true pipe" vs "false
   positive" is itself a human judgement call, verified with `sh -c`/
   `bash -c` reproductions (§3) but not proven exhaustively — a
   sufficiently obscure quoting edge case could still be misclassified
   in either direction, and nothing in this repository mechanically
   checks that classification against a real shell parser.

2. **The "rerun under pipefail" records (Shape A) never actually
   exercised the masked failure in this environment — they prove the
   MECHANISM is now safe, not that a real failure was ever caught.** All
   four Shape-A records reproduced byte-identical output to their
   original committed bytes, because their source files
   (`payloads_bad.json`, `snapshot_dirty.json`, the literal `echo`
   payloads) were present and well-formed at rerun time, same as at
   original capture time. The regression tests in §5 prove the masking
   bug and its fix using throwaway, deliberately-broken fixtures — the
   real committed records' own history of actually failing (or not)
   remains unknown, because no prior run of these specific commands with
   a genuinely missing/corrupt input file was ever captured. If
   `payloads_bad.json` (etc.) is ever deleted or corrupted in a future
   commit, THIS is the mechanism that will now correctly surface it as a
   real transcript failure — but that has not happened yet, so this
   delivery's live proof of the fix working on the REAL data is
   necessarily absent; only the general mechanism (§3) and each
   directory's own synthetic reproduction (§5) are proven.

3. **Six directories now have a `capture.sh` that reruns only ONE (or
   two) specific hardcoded record(s), not the whole transcript — this is
   a deliberately narrow tool, and it will silently stop matching the
   moment anything else about that record's surrounding text changes.**
   Each script locates its target record by an EXACT, hardcoded header
   string; if a future edit to `captured_output.txt` rewords that header
   even slightly (extra whitespace, a renamed input file, `-o` vs
   `--output`), the script will refuse to guess (by design — see §5's
   "never fabricate" tests) rather than silently doing the wrong thing,
   but it will also then need a human to notice the refusal and update
   the hardcoded string. This is the intended, disclosed trade-off (a
   full-transcript regenerator like `index-generator/capture.sh` would
   avoid this but was explicitly out of scope — "rerun only the affected
   records" — and would also touch far more of each directory's
   committed evidence than this task authorized).

4. **This delivery did not attempt to find piped records inside
   `README.md` files, only inside `captured_output.txt` — `pipe_scan.py`
   itself only scans the latter, so a README that documents a piped
   command as its "canonical" invocation (independent of whether that
   exact command was ever captured) is outside both the scanner's and
   this delivery's field of view.** `driftcheck.py`'s own
   `README_COMMAND_NOT_IN_TRANSCRIPT` findings (39 repo-wide, unchanged
   by this delivery — §7) are the closest existing signal for that gap,
   and they are a presence check, not a pipefail-masking check.

5. **The six new `test_capture_fix.py` files duplicate a fair amount of
   boilerplate (the generic `false | true` proof, the `TempDir` helper,
   the `have_bash()` guard) across all six directories rather than
   sharing a single implementation.** This mirrors an explicit,
   pre-existing repository convention (every tool directory is
   self-contained; `index-generator/test_capture.py` does not import
   from a shared test-utilities module either), traded deliberately for
   avoiding a new cross-directory dependency that isn't already part of
   this repository's structure — but it does mean a future change to the
   shared proof (e.g. testing a THIRD masking shape) has to be applied
   six times by hand, not once.

**Weighted most heavily: limitation #2.** Every other limitation in this
list is about tooling precision or maintenance cost; #2 is about
evidentiary strength — the four Shape-A records' fix is proven correct
by construction and by throwaway-fixture tests, but has not yet been
proven against a REAL failure of the REAL committed data, because no
such failure has occurred in this repository's history. That gap is
exactly the kind of thing this whole task exists to prevent from staying
silent, so it is named here rather than left implicit.

## 11. Deliverables in this repository

* `commit-claim-auditor/`, `transcript-schema/`, `dup-detector/`,
  `payload-validator/`, `queue-auditor/`, `wallet-reconciler/` — each
  gets a new `capture.sh` and a new `test_capture_fix.py`; the first
  three also get a corrected `captured_output.txt` record (§4).
  `payload-validator/queue-auditor/wallet-reconciler`'s
  `captured_output.txt` files are byte-identical to before (§4) — only
  `capture.sh` and `test_capture_fix.py` are new there.
* `pipefail_fix_reports/` — `pipe_scan_before.json` / `pipe_scan_after.json`,
  `driftcheck_before.json` / `driftcheck_after.json`,
  `validate_transcript_before.json` / `validate_transcript_after.json`,
  `RELOCATION_EVIDENCE.txt` — every generated report referenced above,
  committed unedited.
* this file.
