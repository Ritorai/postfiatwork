# SEVEN_TRANSCRIPTS.md — closing the last `TRANSCRIPT_HAS_NO_COMMAND_RECORDS` gap

This document covers exactly one delivery: migrating (or explicitly, reasonedly
refusing) the seven legacy, pre-`FORMAT.md` transcripts that were, before this
work, the only seven files in the repository still producing
`TRANSCRIPT_HAS_NO_COMMAND_RECORDS` from both `transcript-schema/
validate_transcript.py` and `transcript-drift/driftcheck.py`:

`bundle-index`, `crosspath-runner`, `doc-validator`, `link-integrity`,
`nondeterminism-scanner`, `preflight`, `weak-assertion-scanner`.

It builds on `transcript-drift/migrate.py` and `MIGRATION.md` (the prior
delivery) without modifying either tool's logic. **No exit value was
invented anywhere in this delivery.** Every `exit=` line now inside a header
record was already sitting in the file, in plain text, before this work
started; `migrate.py`'s promotion is a pure textual wrap (`$ cmd` →
`=== $ cmd ===`) around a region that already contained a real
`exit=<int>` line — see `migrate.py`'s own module docstring for the exact
rule, unchanged here.

## 1. What `migrate.py` already does, run honestly, first

Running the existing tool against the seven, unmodified, in **default**
mode (`--verify-no-regression`, the tool's own default) refuses **all
seven**, unchanged:

```
python3 transcript-drift/migrate.py --dry-run \
  bundle-index/captured_output.txt crosspath-runner/captured_output.txt \
  doc-validator/captured_output.txt link-integrity/captured_output.txt \
  nondeterminism-scanner/captured_output.txt preflight/captured_output.txt \
  weak-assertion-scanner/captured_output.txt

counts: {"refused": 7}
exit: 3
```

Raw output: `seven_transcripts_reports/migrate_default_dry_run_output.txt`
(human-readable) and `migrate_default_dry_run_report.json` (machine-readable),
both regenerated against the reconstructed pre-migration bytes and committed
unedited.

Every one of the seven is refused for the **same structural reason**: each
file has one or more `$ command` lines whose region already contains a real,
recoverable `exit=<int>` — so `migrate.py`'s bare-line promotion rule
*would* apply — but applying it would, when measured against that tool's own
`driftcheck.py` findings, turn 1 finding
(`TRANSCRIPT_HAS_NO_COMMAND_RECORDS`) into 2 or 3 (real, previously-dormant
mismatches between the transcript and its README — see §4). The default
`--verify-no-regression` rule (documented in `migrate.py` and `MIGRATION.md`
§1, Rule 3) treats that as a regression for the tool and reverts the
rewrite, leaving the file byte-for-byte as found.

**This default-mode result closes nothing.** Left as-is, all seven remain
parseable by nothing in the repository. §2 explains the decision made here.

## 2. The decision: `--no-verify-no-regression`, applied once, deliberately

`verify-no-regression`'s guard answers one question: *does promoting this
transcript increase the number of `driftcheck.py` findings for this tool?*
That is a different question from the one this task is required to answer,
which is: *would promoting this transcript require inventing a value?* The
two are independent. Every promotion `migrate.py` performs — in either
mode — uses only an `exit=<int>` value that is already, verbatim, present in
the file; `--no-verify-no-regression` does not relax that rule even
slightly, it only changes whether a *safe* promotion is additionally vetoed
because of what it reveals about a *different* file (the tool's README).

Promoting these seven makes `driftcheck.py` able to compare their README's
claimed commands against real transcript records **for the first time**.
Several of those comparisons come back mismatched — a README exit-code table
missing a row, a documented command the transcript never actually shows, a
test count the README never states. **These are real, pre-existing gaps
between each tool's README and its own evidence file, not something this
migration created or should hide.** Refusing to promote in order to keep
those gaps dormant would be optimizing the wrong number: it would keep
`driftcheck.py`'s finding count flat while leaving the bigger, structural
problem — seven transcripts nothing in the repository can parse at all —
completely unaddressed, which is precisely the gap this task exists to close.

So: this delivery runs `migrate.py --no-verify-no-regression` against the
seven, once, and commits the result. Both numbers — what DEFAULT does and
what UNIFORM does — are reported side by side below, exactly as measured,
with nothing tuned to make either look better.

```
python3 transcript-drift/migrate.py --no-verify-no-regression \
  bundle-index/captured_output.txt crosspath-runner/captured_output.txt \
  doc-validator/captured_output.txt link-integrity/captured_output.txt \
  nondeterminism-scanner/captured_output.txt preflight/captured_output.txt \
  weak-assertion-scanner/captured_output.txt

counts: {"migrated": 7}
exit: 0
```

Raw output: `seven_transcripts_reports/migrate_uniform_output.txt` and
`migrate_uniform_report.json`, committed unedited — this is the literal run
that produced the seven files' committed bytes.

`crosspath-runner`'s **README is untouched** by this delivery — only its
`captured_output.txt` was migrated. Its README's exit-code table (headed
`| Code | Meaning |` instead of `| Exit | Meaning |`, which is why
`driftcheck.py`'s `TABLE_EXIT_HEADER_RE` never sees its `0` row — see
`MIGRATION.md` §6) is the subject of a separate, concurrent task and is
deliberately left alone here.

## 3. Per-file outcome

Every one of the seven ends in status **migrated**: at least one bare
`$ command` line had a recoverable `exit=` and was promoted. None of the
seven's *whole file* was refused, but every file also has zero or more
individual commands that could **not** be promoted, and are correctly left
as plain, un-headered text — FORMAT.md's explicit allowance
(`body := line*`) — rather than fabricated. The "left bare" column below is
the per-record refusal accounting the brief requires: exactly which command,
at which original line, and why.

| Tool | Promoted (of candidates) | Left bare — line : command (no recoverable `exit=` in its region) |
|---|---|---|
| `bundle-index` | 16 / 16 | — none; every bare candidate had a recoverable `exit=` |
| `crosspath-runner` | 3 / 11 | 18: `python3 --version` · 21: `uname -s -m` · 24: `python3 -m unittest test_crosspath 2>&1 \| tail -n 3` · 37: `cat manifest.json` · 219: `grep -c "/root\|/tmp\|/home\|xp_a\|xp_b" out.json` · 268: `python3 -c "print the coverage block"` · 392: `cmp crosspath_report.json <(rerun) && echo REPRODUCIBLE` · 395: `grep -c "/root\|/tmp\|/home\|xp_a\|xp_b" crosspath_report.json` |
| `doc-validator` | 5 / 9 | 1: `python3 -m unittest test_docval -v` · 209: `sha256sum r1.json r2.json` · 213: `cmp r1.json r2.json && echo BYTE-IDENTICAL` · 224: `grep -c "/sessions\|/tmp\|/home" r1.json` |
| `link-integrity` | 6 / 8 | 156: `sha256sum report_run1.json report_run2.json` · 160: `cmp report_run1.json report_run2.json && echo BYTE-IDENTICAL` |
| `nondeterminism-scanner` | 5 / 13 | 1: `python3 -m unittest test_ndscan -v` · 239: `sha256sum r1.json r2.json` · 243: `cmp r1.json r2.json && echo BYTE-IDENTICAL` · 254: `grep -c "/sessions\|/tmp\|/home" r1.json` · 258: `mkdir -p /tmp/relocated_samples_risky && cp -r samples_risky/* ...` · 259: `python3 ndscan.py --root samples_risky -o /tmp/orig_relocation_check.json ; sha256sum ...` · 261: `python3 ndscan.py --root /tmp/relocated_samples_risky -o /tmp/relocated_relocation_check.json ; sha256sum ...` · 263: `cmp /tmp/orig_relocation_check.json /tmp/relocated_relocation_check.json && echo RELOCATION-BYTE-IDENTICAL` |
| `preflight` | 7 / 9 | 206: `sha256sum report_run1.json report_run2.json` · 210: `cmp report_run1.json report_run2.json && echo BYTE-IDENTICAL` |
| `weak-assertion-scanner` | 8 / 16 | 228: `sha256sum r1.json r2.json` · 232: `cmp r1.json r2.json && echo BYTE-IDENTICAL` · 243: `grep -c "/sessions\|/tmp\|/home" r1.json` · 247: `cp -r samples_weak /tmp/relocated_weak_copy` · 248: `python3 weakassert.py --root samples_weak > /tmp/reloc_a.json` · 249: `python3 weakassert.py --root /tmp/relocated_weak_copy > /tmp/reloc_b.json` · 250: `sha256sum /tmp/reloc_a.json /tmp/reloc_b.json` · 253: `cmp /tmp/reloc_a.json /tmp/reloc_b.json && echo RELOCATION-IDENTICAL` |

**Why each "left bare" line is genuinely unsourceable, not a parser miss:**
in every case above, the command's own committed output — a `sha256sum`
listing, a `cmp` result, a `grep -c` count, the literal text
`BYTE-IDENTICAL` or `REPRODUCIBLE` — is present in the file, but no
`exit=<int>` line was ever written for that specific command, anywhere
between it and the next `$ ` line. The record's *observable result* was
captured; its *exit status* was not. `migrate.py` does not infer `exit=0`
from `BYTE-IDENTICAL` or a clean `sha256sum` listing — see the crosspath-
runner example in `test_seven_transcripts.py`
(`TestUnsafeRefusalGroundedInCrosspathRunner`), which reproduces this exact
shape (a `unittest` run piped through `tail`, matching `index-generator/
capture.sh`'s documented "Finding 2" masking bug) as a grounded, real-input
test.

Full line-by-line promotion/left-bare detail for all seven, machine-checked:
`seven_transcripts_reports/migrate_uniform_report.json`.

## 4. Newly-surfaced `driftcheck.py` findings — disclosed, not hidden

Promoting these seven made `driftcheck.py`'s README-vs-transcript comparison
run for the first time on each of them. It found real, pre-existing
mismatches — none of which this delivery fixes (fixing README claims is out
of scope here; see `MIGRATION.md` for the precedent of leaving these as
named, tracked gaps rather than silently editing READMEs to make numbers
agree):

| Tool | Before (driftcheck) | After (driftcheck) | New finding(s) |
|---|---|---|---|
| `bundle-index` | 1: `TRANSCRIPT_HAS_NO_COMMAND_RECORDS` | 3 | `EXIT_CODE_MISMATCH` (`readme_exit_claims: [-2,0,1]`, transcript also shows `2`, unacknowledged) · `README_COMMAND_NOT_IN_TRANSCRIPT` (1: the generic usage synopsis `python3 bundle_index.py <bundle_dir> [-o PATH]`) · `TEST_COUNT_NOT_CLAIMED_IN_README` (transcript shows `Ran 170 tests`, README never states a count) |
| `crosspath-runner` | 1 | 2 | `EXIT_CODE_MISMATCH` (README claims `[1,2]`, transcript also shows `0`, unacknowledged — the pre-existing "Code" vs "Exit" table-header bug, see §2) · `README_COMMAND_NOT_IN_TRANSCRIPT` (5, mostly usage-grammar synopses; see `MIGRATION.md` §6 for the same pattern on other tools) |
| `doc-validator` | 1 | 2 | `EXIT_CODE_MISMATCH` (README claims `[0,2]`, transcript also shows `1`, unacknowledged) · `README_COMMAND_NOT_IN_TRANSCRIPT` (1: `python3 -m unittest test_docval -v` — genuinely run, at line 1, but piped/never given `; echo "exit=$?"`, so its own record can't be promoted either; see §3) |
| `link-integrity` | 1 | 2 | `README_COMMAND_NOT_IN_TRANSCRIPT` (1: the usage synopsis `python3 link_integrity.py LIFECYCLE_FILE EVIDENCE_FILE [-o OUTPUT_FILE]`) · `TEST_COUNT_NOT_CLAIMED_IN_README` (transcript shows `Ran 137 tests`) |
| `nondeterminism-scanner` | 1 | 2 | `EXIT_CODE_MISMATCH` (README claims `[1]` only; transcript shows `0` and `2` too) · `README_COMMAND_NOT_IN_TRANSCRIPT` (3, including the un-promotable `test_ndscan -v` run and two usage synopses) |
| `preflight` | 1 | 3 | `EXIT_CODE_MISMATCH` (README claims `[2]` only; transcript shows `0` and `1` too) · `README_COMMAND_NOT_IN_TRANSCRIPT` (3 usage synopses) · `TEST_COUNT_NOT_CLAIMED_IN_README` (transcript shows `Ran 183 tests`) |
| `weak-assertion-scanner` | 1 | 3 | `EXIT_CODE_MISMATCH` (README claims `[2]` only; transcript shows `0` and `1` too) · `README_COMMAND_NOT_IN_TRANSCRIPT` (4, including two relocation-proof invocations) · `TEST_COUNT_MISMATCH` (README states `35`/`3198` in unrelated prose contexts; transcript's real `Ran 202 tests` matches neither) |

Repository-wide (all 46 directories with content on disk), measured with
`driftcheck.py --root .` before and after this delivery's rewrite (raw
reports: `seven_transcripts_reports/driftcheck_before.json` /
`driftcheck_after.json`, unedited):

```
BEFORE (repo-wide): 95 findings
  TRANSCRIPT_HAS_NO_COMMAND_RECORDS: 7   <- exactly the seven
  ... (48 TRANSCRIPT_RECORD_HAS_NO_EXIT, 32 README_COMMAND_NOT_IN_TRANSCRIPT,
       3 EXIT_CODE_MISMATCH, 3 TEST_COUNT_MISMATCH,
       2 TEST_COUNT_NOT_CLAIMED_IN_README -- all pre-existing, untouched here)

AFTER  (repo-wide): 105 findings
  TRANSCRIPT_HAS_NO_COMMAND_RECORDS: 0   <- the gap named in this task, closed
  ... (48 TRANSCRIPT_RECORD_HAS_NO_EXIT unchanged, 39 README_COMMAND_NOT_IN_TRANSCRIPT (+7),
       9 EXIT_CODE_MISMATCH (+6), 4 TEST_COUNT_MISMATCH (+1),
       5 TEST_COUNT_NOT_CLAIMED_IN_README (+3))
```

`TRANSCRIPT_HAS_NO_COMMAND_RECORDS` goes from 7 to **0**: every remaining
transcript in the repository is now at least minimally parseable.
`TRANSCRIPT_RECORD_HAS_NO_EXIT` (48) is completely unaffected — those
findings belong to files this delivery does not touch (the six tools
`MIGRATION.md` already documented as correctly, permanently refused —
`event-linter`, `evidence-manifest`, `lifecycle-linter`, `reward-reconciler`,
`sybil-detector`, `xrpl-auditor` — none of which are among the seven).
Every other increase is a real, previously-dormant README/transcript
mismatch, now visible and individually named per tool in the table above
instead of hidden behind an opaque "unparseable" status.

`transcript-schema/validate_transcript.py`'s own-grammar check (no README
required) shows the sharper, unambiguous half of this result — every one of
the seven now validates completely cleanly against `FORMAT.md`'s grammar,
independent of what `driftcheck.py`'s README comparison finds:

```
BEFORE (repo-wide validate_transcript.py --root .): 113 findings, including
  7 TRANSCRIPT_HAS_NO_COMMAND_RECORDS (the seven) and 50
  TRANSCRIPT_PREAMBLE_EXIT_LOOKALIKE (info-severity: every exit=-shaped line
  in a header-less file is, by definition, preamble)

AFTER: 56 findings -- ZERO of either code among the seven. The 56 remaining
  findings (48 TRANSCRIPT_RECORD_HAS_NO_EXIT, 5 TRANSCRIPT_RECORD_DUPLICATE_EXIT
  info, 3 TRANSCRIPT_RECORD_EXIT_MALFORMED) all belong to the six
  already-refused tools named above and are untouched by this delivery.
```

Raw reports: `seven_transcripts_reports/validate_transcript_before.json` /
`validate_transcript_after.json`, unedited.

## 5. Tests

`python3 -m unittest discover -p "test_*.py"`, run from `transcript-drift/`:

```
Ran 232 tests in ~2s
OK
```

(158 in the pre-existing `test_migrate.py`, 57 in the pre-existing
`test_driftcheck.py`, 17 new in `test_seven_transcripts.py` — the file
added by this delivery.) `transcript-schema/`'s own suite
(`python3 -m unittest discover -p "test_*.py"`) is unaffected by this
delivery and still passes: 194 tests, `OK`.

**Six tests in the pre-existing `test_migrate.py` were updated, not
added**, because they build their fixtures by copying real tool
directories out of this repository (`TestRealRepoFixtures`,
`TOOL_DIRS_FOR_FIXTURE` includes `crosspath-runner`), and that copy now
picks up crosspath-runner's already-migrated transcript instead of its
pre-migration bytes. Per the task's own guidance: a test asserting "this
transcript has no records" breaks the moment the transcript is migrated: it
was pinned to a promotion count / a refusal reason specific to the *old*
bytes, not to an end-state property. Each was rewritten to assert the
end-state invariant instead (record count and content, settled-vs-refused
status, byte-stability under a second run) — the same pattern the file
already used for `limitations-probe`, `path-collision-scanner`, and
`regression-checker`, whose committed transcripts were *also* already
migrated before this delivery. No assertion about `migrate.py`'s *logic*
changed; only the fixture's starting state did, and the tests were brought
in line with it. `test_seven_transcripts.py` additionally covers all seven
inputs directly (end-state, zero-fabrication, real-input unsafe refusal),
stable ordering (§ `TestStableOrdering`), and repeated offline runs (§
`TestRepeatedOfflineRuns`), as named in the task brief.

Reproduce:

```
cd transcript-drift
python3 -m unittest discover -p "test_*.py" -v
cd ../transcript-schema
python3 -m unittest discover -p "test_*.py" -v
```

## 6. Byte-identity: original checkout vs. a relocated copy

The whole tree was copied to a differently-named absolute path
(`/tmp/build_9/relocated_xyz`, alongside the original at
`/tmp/build_9/repo`) and every report generator run again there. Full
detail, including the one documented, expected exception, is in
`seven_transcripts_reports/RELOCATION_EVIDENCE.txt`. Summary:

* `driftcheck.py --root .` and `transcript-schema/validate_transcript.py
  --root .` reports are **byte-identical** (matching sha256, no
  normalisation needed) between the original and relocated tree — neither
  tool's report ever contains an absolute path, only tool-relative labels.
* The seven `captured_output.txt` files themselves are **byte-identical**
  across the relocation (matching sha256 each).
* `migrate.py --all --root .`'s own report is **not** byte-identical
  without normalisation, and this is expected and documented, not silently
  worked around: the report's `path` field is, by design, the literal path
  the tool was invoked with, which is `--root`-relative and therefore
  differs between `/tmp/build_9/repo/...` and
  `/tmp/build_9/relocated_xyz/...` by construction. Substituting each
  tree's own root prefix for a placeholder in that one field before
  comparing makes the two reports equal by JSON structural equality — every
  other field (status, reason, promoted/left_bare/refused_records,
  verification counts) already matched. No file was hand-edited to produce
  this result; the substitution happens only in the comparison step
  described in `RELOCATION_EVIDENCE.txt`, never on disk.

## 7. Limitations — real failure modes

1. **A body line that happens to be exactly `$ <text>` is always treated as
   a command echo, never as prose that happens to start with `$ `.**
   Unchanged from `MIGRATION.md`'s own §8.1, and still present in these
   seven: e.g. `doc-validator`'s `python3 -m unittest test_docval -v` at
   line 1 is a real command with a real `Ran N tests`/`OK` block below it,
   but because it is piped/never given `; echo "exit=$?"`, `migrate.py`
   correctly leaves it bare — but the *reason* it stays bare (no exit=
   recorded) is indistinguishable, to this tool, from a hypothetical case
   where a line reading `$ rm -rf /` appeared in a prose paragraph
   discussing a dangerous command someone should NOT run. Nothing in this
   repository's seven files exhibits that specific false-positive shape,
   but the rule cannot tell the two apart, and a future transcript could.

2. **A genuinely-run command with a real result recorded (a `sha256sum`
   listing, `BYTE-IDENTICAL`, `REPRODUCIBLE`) but no `exit=` line is
   permanently unrecoverable by this tool, forever, even though a human
   reading the same transcript can usually infer with high confidence that
   it succeeded.** This is not a bug to fix; it is the entire point of the
   "never fabricate" rule, but it is worth stating plainly as a cost: 33
   individual commands across the seven files (the "left bare" column in
   §3) will never become checkable command records unless someone re-runs
   them and captures a real `exit=$?` this time. `crosspath-runner`'s
   `python3 -m unittest test_crosspath 2>&1 | tail -n 3` (line 24) is the
   concrete example this delivery's `test_seven_transcripts.py` grounds a
   test in — it is also, precisely, the masking bug `index-generator/
   capture.sh`'s "Finding 2" already diagnosed and fixed going forward
   (`bash -c 'set -o pipefail; ...'`) for *new* transcripts; it cannot
   retroactively fix an already-committed one.

3. **`driftcheck.py`'s `README_COMMAND_NOT_IN_TRANSCRIPT` cannot distinguish
   "this command was never run" from "this is a `## Usage` grammar
   synopsis using literal brackets/placeholders" from "this command's
   record exists but has no `exit=` and therefore isn't a header at all."**
   All three shapes appear among the seven's newly-surfaced findings in §4
   (e.g. `preflight`'s three `README_COMMAND_NOT_IN_TRANSCRIPT` entries are
   all usage synopses, not missing runs; `doc-validator`'s one entry is a
   command that *was* run but couldn't be promoted). Reading the count
   alone, without opening the `detail.commands` list, will overstate how
   much real drift exists. `MIGRATION.md` §6 documents the same
   ambiguity for the four tools migrated in the prior delivery; it is a
   property of `driftcheck.py`'s command-detection heuristic
   (`cmd.startswith("python3 ")`), not something specific to these seven,
   and is not fixed here — fixing it is a `driftcheck.py` change, out of
   scope for a migration task that is deliberately restricted to
   transcript files.

4. **The decision in §2 (uniform rule over default) is a per-batch,
   human-documented choice, not something `migrate.py` enforces or can
   detect on its own.** Nothing in the tool distinguishes "a maintainer
   deliberately decided the dormant findings this surfaces are acceptable"
   from "someone ran `--no-verify-no-regression` out of habit without
   reading what it surfaces." The guard rail migrate.py ships
   (`--verify-no-regression`, default on) is real and was not weakened;
   this document is the only thing standing between "explicitly disabled it, once,
   with a table of every consequence" and "silently overrode a safety
   default." A reviewer who reads the commit but not this file would see
   seven newly-migrated transcripts and nine newly-surfaced drift findings
   with no explanation connecting the two.

## 8. Deliverables in this directory

* `bundle-index/`, `crosspath-runner/`, `doc-validator/`, `link-integrity/`,
  `nondeterminism-scanner/`, `preflight/`, `weak-assertion-scanner/`
  (each tool's own directory, elsewhere in the repository) — the seven
  migrated `captured_output.txt` files.
* `test_seven_transcripts.py` — the 17 new focused tests (§5).
* `test_migrate.py` — 6 pre-existing tests updated to end-state assertions
  (§5); no other line changed.
* `seven_transcripts_reports/` — every generated report referenced above,
  committed unedited: `migrate_default_dry_run_report.json` +
  `migrate_default_dry_run_output.txt` (the refused-x7 baseline, §1),
  `migrate_uniform_report.json` + `migrate_uniform_output.txt` (the real run
  behind the committed bytes, §2), `driftcheck_before.json` /
  `driftcheck_after.json`, `validate_transcript_before.json` /
  `validate_transcript_after.json` (§4), `RELOCATION_EVIDENCE.txt` +
  `migrate_all_relocation_check_repo.json` /
  `migrate_all_relocation_check_relocated.json` (§6).
* this file.
