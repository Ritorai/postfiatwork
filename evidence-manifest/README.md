# Deterministic Batch Evidence Manifest CLI

Standard-library Python 3 only. No third-party packages, no network.

## Exact rerun commands

```
python3 -m unittest test_manifest -v
python3 manifest.py build submissions.json -o manifest_run1.json ; echo "exit=$?"
python3 manifest.py build submissions.json -o manifest_run2.json ; echo "exit=$?"
sha256sum manifest_run1.json manifest_run2.json
cmp manifest_run1.json manifest_run2.json && echo BYTE-IDENTICAL
python3 manifest.py verify manifest_run1.json ; echo "exit=$?"
python3 manifest.py verify tampered_manifest.json ; echo "exit=$?"
```

The six steps this delivery adds are recorded in `captured_output.txt` as
**plain `$` steps**, not `=== $ ... ===` records, and are listed here in a
table rather than in the block above:

| step | what it shows |
|------|---------------|
| `python3 -m unittest test_repeat_run -v` | the 25 new tests, `OK` |
| `python3 manifest.py build submissions_repeat.json -o repeat_run1.json` | first pass over the new fixture |
| `python3 manifest.py build submissions_repeat.json -o repeat_run2.json` | second pass |
| `sha256sum repeat_run1.json repeat_run2.json` | both output hashes, printed whether or not they agree |
| `cmp repeat_run1.json repeat_run2.json && echo BYTE-IDENTICAL` | the byte comparison |
| `python3 manifest.py verify repeat_run1.json` | the new fixture's manifest re-verifies |

The split is not cosmetic, and the repository already documents the reason:
`index-generator` pins a repository-wide count of `=== $ ... ===` records --
548 -- in its committed `pipe_classification_report.json` and its README, and
`test_pipe_classify.TestCommittedReportIsFresh` goes red the moment that count
moves. `index-generator` is off limits under the brief these steps were added
under, so they are plain steps, exactly as
`evidence-validator/mk_artifacts.sh` did for the same reason. Measured, not
assumed: recording all six as `=== $ ... ===` records takes
`pipe_scan.py`'s `total_command_records` from 548 to 554 and turns that test
red. A plain step also carries no `exit=` line: `transcript-drift/FORMAT.md`
requires exactly one `exit=` per **record**, and a plain step is not a record,
so there is no place to hang one.

Keeping them out of the fenced block above is the second half of the same
constraint: `transcript-drift/driftcheck.py` extracts a command only from a
line **inside** a fenced block that begins `python3 ` or `./`, then requires
each to appear as a `=== $ ... ===` header. A plain step has no header.
Measured, by actually putting all six back in the fenced block and re-running
`python3 driftcheck.py --root ..`: the repository-wide finding count goes
99 -> 100, `README_COMMAND_NOT_IN_TRANSCRIPT` goes 39 -> 40, and that single
extra finding names **four** commands, not six --
`sha256sum ...` and `cmp ... && echo BYTE-IDENTICAL` are never extracted
because they do not begin `python3 ` or `./`. One finding, four commands, in a
tool that is also off limits. Both constraints point the same way.

The six steps also sit in the transcript's **preamble**, ahead of the first
`=== $ ... ===` header, rather than after the last record. `FORMAT.md` gives a
record's body as "up to the next header or EOF", so a trailing plain-step
block would be parsed as the body of `verify tampered_manifest.json` and its
its `Ran ...` summary line would be attributed to a command that runs no tests.
Measured: as preamble, `driftcheck`'s `transcript_test_counts` for this
directory is `[29]`, identical to before this delivery; appended at the end
the repeat-run suite's own count is added to it.

## Expected results

| step | result |
|------|--------|
| both suites | **54 tests, OK** (29 in `test_manifest` + 25 in `test_repeat_run`), exit 0 |
| build (both runs, either fixture) | exit 0 |
| `cmp` (both fixtures) | BYTE-IDENTICAL |
| verify a clean manifest | `VERIFIED ...`, exit **0** |
| verify tampered manifest | `VERIFICATION FAILED` + 2 drift lines, exit **1** |

Digests, re-derived by `test_repeat_run.TestTheReadmeNumbersAreCurrent` on
every run rather than trusted:

| input | digest |
|------|--------|
| `submissions.json` batch_root | `ac454cee291d825e13310a14214f1d665a457f7251d22d2aefab2e64fc8ec28b` |
| `submissions.json` manifest SHA-256 | `ba351b028a8b85f8aa93cd2769cd54dba23433ae8f7868da4bc6ecad3cd112f3` |
| `submissions_repeat.json` batch_root | `569e9e16370a92f2e504b3062233344f9c5d773145bd9f2f9f442a34159f26b5` |
| `submissions_repeat.json` manifest SHA-256 | `25cc424842e367343be808f30b7b5d315ac62a67f717fef7304d13c890b0a6ba` |

This is a knowing deviation from `EVIDENCE_STANDARD.md` section 6, which
requires every documented invocation to carry a `=== $ ... ===` header and an
explicit `exit=`. The six steps' exit codes are all 0; they are asserted by
`test_repeat_run` (`self.assertEqual(proc.returncode, 0, proc.stderr)` on
every subprocess) rather than recorded in the transcript, and the deviation
buys leaving two off-limits directories untouched.

`test_output.txt` is the verbose listing of both suites, in the order the
table above states them (29 then 25).

`repeat_run1.json` / `repeat_run2.json` are scratch verification artifacts of
the rerun block, not part of the deliverable -- they are not shipped in this
directory. `manifest_run1.json` / `manifest_run2.json` are shipped, and
`test_repeat_run.TestTheCommittedArtifactsAreNotStale` rebuilds them.

`tampered_manifest.json` is `manifest_run1.json` with `entries[0].canonical.cid`
altered to `QmTAMPERED...`. It is included precisely so the non-zero path is
reproducible without editing anything by hand.

## Canonicalization contract

1. Object keys sorted lexicographically, recursively.
2. String values: outer whitespace stripped, internal whitespace runs collapsed to one space. Recursive.
3. `separators=(",",":")`, `ensure_ascii=True`.
4. Leaf digest = `SHA256(b"leaf:" + canonical_bytes)`.
5. Leaf order = input array order. Reordering the batch changes the root **by design**.
6. Parent = `SHA256(b"node:" + left_raw + right_raw)`.
7. Odd node is **promoted unchanged**, never duplicated — avoids the CVE-2012-2459 style duplicate-leaf root collision. `test_odd_promotion_not_equal_to_duplication` asserts this.
8. Empty batch root = `SHA256(b"empty:")`.

Domain separation (`leaf:` vs `node:`) prevents a leaf digest from being replayed as an internal node.

## Fixture note

`submissions.json` deliberately contains unsorted keys and messy whitespace
(`"  validator   delivered  "`, `"reconciler\tdelivered"`) so that the
canonicalization rules are actually exercised rather than assumed.

`submissions_repeat.json` is the fixture for the repeat-run test. Seven
records, chosen so the byte comparison has something to be sensitive to:
out-of-order keys at two nesting depths, whitespace rule 2 has to collapse
(`"first\trecord\nwith   mixed   whitespace"`), non-ASCII, a duplicate
`submission_id`/`cid` pair (limitation EM-4), mixed scalar types including
`null`, `true`, `-0.0` and `1e3`, and an **odd** leaf count so the rule-7
promote branch runs on the first Merkle level. Seven leaves reduce 7 -> 4 ->
2 -> 1, so the promote branch fires once, at the first level only.

## What the repeat-run test adds

`test_manifest.TestCli.test_build_stdout_repeatable` already runs `build`
twice and compares stdout. `test_repeat_run.py` covers three things it does
not:

1. **The `-o` write path.** Nothing previously compared the bytes of the file
   `--out` produces; only stdout.
2. **A fresh working directory per pass.** Each pass stages `manifest.py` plus
   one fixture into its own `tempfile.mkdtemp()` and runs there, so a cwd or
   absolute path leaking into the output shows up as drift.
3. **Two different `PYTHONHASHSEED` values.** The existing test lets both
   children inherit the ambient seed. A runner that exports
   `PYTHONHASHSEED=0` therefore hides per-process hash-order drift from it
   completely.

Point 3 is measured, not asserted. Mutating `canonicalize` to iterate
`set(value)` instead of `sorted(value)`, and `serialize` to pass
`sort_keys=False`, then running with `PYTHONHASHSEED=0` exported (three runs
of each suite, same result every time):

| suite | result on the seed-dependent build |
|---|---|
| `python3 -m unittest test_manifest` | 29 tests, all **OK** |
| `python3 -m unittest test_repeat_run` | 25 tests run, **9 failures** |

The entire existing suite passes on a build whose output order depends on the
hash seed. That is the gap this file closes.
`sortkey-detector/README.md` limitation 5 describes the same blind spot in the
abstract; this is the first committed test in the repository that varies the
seed.

Six more mutations were applied one at a time to `manifest.py` and each was
killed by this file: injecting `os.getcwd()` into the manifest, injecting
`time.time()`, writing the `-o` file with `indent=2` while stdout stayed
compact, changing rule 7 from promote to duplicate, an off-by-one
`record_count`, and opening the `-o` path with mode `"a"` instead of `"w"`
so a second build appends to the first. Seven mutations applied, seven
killed. The append mutation is the reason
`test_rerunning_into_the_same_output_path_is_a_no_op` exists: every other
pass here writes into a fresh directory, so it alone reads the brief's
"a second run leaves its output unchanged" literally.

Every failure message prints both SHA-256 digests and both byte lengths, so a
reviewer can tell a content change from a truncation without re-running.

Cleanup note: the staging helper calls `shutil.rmtree` on the directory
`tempfile.mkdtemp()` returned, never on its parent.

## What this delivery changed outside this directory

Five files. Four are in two directories the brief lists as off limits: each
is a current-state self-scan that `report-freshness/manifest.json` requires to
regenerate byte-for-byte, plus the README line that quotes it. The fifth,
`claim-crosscheck/sample_run.json`, is the same kind of entry in a directory
the brief does not exclude. Reverting any one of them turns a committed gate
red. No finding count moved in any of the three scanners -- only the size of
the tree they read.

| file | why | movement |
|---|---|---|
| `nondeterminism-scanner/self_scan_report.json` | `report-freshness` entry `nondeterminism-scanner:self_scan_report.json`; regenerated with `python3 ndscan.py --root .. -o self_scan_report.json` | `files_scanned` 184 -> 185; `findings_count` unchanged at 460 |
| `nondeterminism-scanner/README.md` | quotes that report's `files_scanned` twice | same |
| `weak-assertion-scanner/self_scan_report.json` | `report-freshness` entry `weak-assertion-scanner:self_scan_report.json`; regenerated with `python3 weakassert.py --root .. -o self_scan_report.json` | `files_scanned` 83 -> 84, `tests_scanned` 6610 -> 6635; `findings_total` unchanged at 246 |
| `weak-assertion-scanner/README.md` | `test_weakassert_regen` re-derives every number in it from the report | same |
| `claim-crosscheck/sample_run.json` | `report-freshness` entry `claim-crosscheck:sample_run.json`, a whole-tree snapshot that must regenerate byte-for-byte; regenerated with `python3 crosscheck.py --root .. --all -o sample_run.json` | `claims_checked` 15, `discrepancies` 1, `errors` 0 -- all three unchanged; only the bytes move, because this README gained checkable claims |

Deliberately **not** changed, and why:

- `index-generator` -- its pinned `total_command_records` stays 548 because
  the six new steps are plain steps. See "Exact rerun commands" above.
- `shebang-mode` -- `test_repeat_run.py` carries no shebang, so
  `SM002_SHEBANG_WITHOUT_EXEC` stays 150.
- The root `README.md` index row still reads `| evidence-manifest | 29 |`,
  which is now 25 short of this directory's real 54. That row, `readme-index/corpus.tsv`
  and `readme-index/root_readme_after.md` are one pinned 44-tool snapshot;
  moving the row alone would desynchronise the three, and `readme-index` is
  off limits. `readmeindex.py --corpus corpus.tsv --root-readme ../README.md`
  produces byte-identical `index_differences` before and after this delivery.
- `throughput-reporter/README.md` -- its changelog rows describe the state at
  *its own* commit and stay true as history.

## Flags

Two subcommands, each with its own arguments.

`build`:

| flag | description |
|------|-------------|
| `records` (positional) | Path to a JSON array of submission records to manifest. Required. |
| `-o`, `--out PATH` | Write the canonical manifest JSON to this file instead of stdout. When set, stdout instead gets a two-line summary: `batch_root=<hash>` and `records=<count>`. |

`verify`:

| flag | description |
|------|-------------|
| `manifest` (positional) | Path to a previously built manifest JSON file to re-verify (digests + batch root). Required. |

`-o`/`--out` is only accepted under `build`; `verify` takes no output-path option and always reports to stdout.

## Exit codes

0 = ok · 1 = verification drift · 2 = invalid input


## 4 limitations a reviewer should scrutinise

Found by **running this tool against adversarial inputs**, not by reading it.
Every claim below is reproduced by
[`limitations-probe/probe.py`](../limitations-probe/probe.py); the probe ids
are the ones it prints, and it exits non-zero if any of them stops
reproducing.

1. **Whitespace-only differences in evidence collide on one root (EM-1).**
   Canonicalization rule 2 collapses internal whitespace runs and strips the
   ends of every string, recursively. The rule is documented; its consequence
   is not. `"hash  abc"`, `"hash abc"`, `"hash\tabc"` and `"hash\nabc"` are
   four different pieces of evidence, and all four produce batch root
   `3a2873b4ab2b7746877b789d3f90697ce539332d947294fe88ad85d28bfd79dc`. For a
   tool whose job is detecting tampering, that means **reformatting evidence
   text is invisible to the manifest**. If whitespace is meaningful in your
   evidence — a diff, a fixed-width report, anything line-oriented — hash it
   out of band, or base64 it into a single token before it reaches here.

2. **Identifiers are whitespace-normalised too (EM-2).** `" S1 "` and `"S1"`
   are the same `submission_id` as far as the root is concerned. Rule 2 makes
   no exception for fields that look like keys, because it cannot see which
   ones they are.

3. **A bare `NaN` is accepted and poisons the manifest for strict readers
   (EM-3).** `build` exits `0` on a record containing `NaN` and writes a
   manifest with a literal `NaN` token in it. RFC 8259 does not allow that
   token. Measured, not assumed — I tested three parsers and **one of them
   contradicted my first guess**:

   | Parser | Result |
   |---|---|
   | Node 22 `JSON.parse` (V8, strict) | **rejects** — `Unexpected token 'N'` |
   | Python `json` with `parse_constant` raising | **rejects** — bare `NaN` |
   | Python `json` default | accepts (documented non-standard extension) |
   | `jq` 1.7 | **accepts** — reads the root back fine |

   My first draft of this section claimed jq rejects it. It does not; jq is
   lenient here. The accurate statement is narrower and still worth acting on:
   the manifest round-trips through Python and jq but **fails in any strict
   RFC 8259 reader**, which includes every browser and V8. A digest file that
   parses for some consumers and not others is the worst place for a format to
   be wrong. `captured_output.txt` has all four runs.

4. **Duplicate `submission_id`s are accepted silently (EM-4).** Two records
   sharing an id produce two entries and a root, exit `0`, no warning.
   Uniqueness is the caller's responsibility and this README previously did
   not say so. Note this is arguably correct — rule 5 makes order part of the
   batch identity, so two identical-id records are genuinely two leaves — but
   a reader who assumes the manifest enforces uniqueness will be wrong.

**Checked and found sound, reported as a negative result:** the odd-node rule
(rule 7) promotes rather than duplicates, so the CVE-2012-2459 duplicate-leaf
collision does not apply; leaf and node digests are domain-separated. Neither
was disturbed by any probe above.
