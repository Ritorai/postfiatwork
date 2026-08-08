# weakassert

A stdlib-only Python 3 CLI that statically scans a directory of `unittest`
test suites and reports tests that look weak: no assertion, "does not
raise"-only checks, expected values that are themselves derived from the
module under test, and skipped tests.

## Why AST, not regex

`weakassert.py` parses every file with the `ast` module and only ever
inspects the parsed syntax tree. It never runs a regular expression against
source text, and it never imports or executes the code it scans.

A regex scanner would match `self.assertEqual` inside a string literal,
inside a comment, inside a docstring quoting another test, or inside a
multi-line f-string - and it would silently miss the same call written
across two lines, wrapped in a decorator, or aliased through `cls`. AST
parsing sees exactly the statements Python itself would execute, so it
does not have those failure modes. This is stated as a hard requirement
of the tool, not an implementation preference.

## Install / run

No dependencies beyond the Python 3 standard library (developed and tested
against CPython 3.10; `sys.stdlib_module_names`, used to guess which
imports are "the module under test", was added in 3.10 - the tool falls
back to a hardcoded stdlib-module list on older interpreters, see
`_STDLIB_FALLBACK` in `weakassert.py`).

```
python3 weakassert.py --root PATH_TO_TEST_TREE
```

## CLI

```
weakassert.py [--root PATH] [-o FILE | --output FILE]
              [--category CAT [--category CAT ...]]
```

- `--root PATH` - directory to scan, recursively. Default: `.`
- `-o/--output FILE` - write the JSON report to FILE instead of stdout.
- `--category CAT` - restrict both the report and the exit-code verdict to
  the given categor(y/ies). May be repeated. Valid values: `WA001_NO_ASSERTION`,
  `WA002_CALL_ONLY`, `WA003_SELF_DERIVED_EXPECTATION`, `WA004_SKIPPED_TEST`.

### Exit codes

- `0` - scan completed, zero findings (after any `--category` filter)
- `1` - scan completed, one or more findings
- `2` - usage error (bad `--root`, invalid `--category`, argparse error) or
  an unexpected scan-level failure. Per-file syntax/read errors do NOT
  produce exit 2 - they are recorded in the report's `errors` list and the
  scan continues; only genuinely fatal problems (root missing, bad flags)
  use exit 2.

### `--category` changes the verdict - demonstrated

`demo_category/` contains one file that trips WA001 only:

```
$ python3 weakassert.py --root demo_category            ; echo $?
... one WA001 finding ...
1
$ python3 weakassert.py --root demo_category --category WA004_SKIPPED_TEST ; echo $?
... zero findings, category_filter=["WA004_SKIPPED_TEST"] ...
0
```

Filtering to a category that is not present in the scanned tree flips the
exit code from 1 to 0. The full transcript is in `captured_output.txt`.

## Output format (canonical JSON)

Every report is emitted as `json.dumps(report, sort_keys=True,
separators=(",", ":"), ensure_ascii=True)` plus exactly one trailing `\n`.
`sort_keys=True` only orders *object* keys alphabetically - it does nothing
for the order of items inside a JSON array. `findings` and `errors` are
therefore explicitly sorted by the tool before serialization:

- `findings`: sorted by `(category, path, line, test_name)`
- `errors`: sorted by `path`

This makes two scans of an unchanged tree byte-for-byte identical, and
makes a relocated copy of the same tree produce byte-for-byte identical
output (paths are always relative to `--root`, never absolute; there is no
timestamp, duration, or PID anywhere in the report). Both properties are
verified by `captured_output.txt` (`sha256sum` / `cmp` and the relocation
test) and unit tests in `test_weakassert.py` (`TestCanonicalJson`,
`TestNoAbsolutePathLeakage`).

Report shape:

```
{
  "schema_version": 1,
  "tool": "weakassert",
  "files_scanned": <int>,
  "tests_scanned": <int>,
  "category_filter": [<cat>, ...] | null,
  "summary": {
    "findings_total": <int>,
    "findings_by_category": {"WA001_NO_ASSERTION": <int>, ...},
    "files_with_errors": <int>
  },
  "errors": [{"path": <relative path>, "message": <str>}, ...],
  "findings": [
    {"category": <str>, "path": <relative path>, "line": <int>,
     "test_name": "ClassName.method_name" | "function_name",
     "detail": <str>},
    ...
  ]
}
```

## Test discovery

A file is scanned if its name matches `test_*.py` or `*_test.py` (the two
conventions used throughout this repo's sibling tool suites). `__pycache__`,
`.git`, `.venv`/`venv`, `node_modules`, `.tox`, `.mypy_cache`,
`.pytest_cache`, `build`, `dist`, and any dot-directory are skipped while
walking. Within a file, a "test" is any `def`/`async def` whose name starts
with `test` (matching `unittest.TestLoader.testMethodPrefix`), whether it
is a method of a class or a bare module-level function (pytest-style tests
outside any `TestCase` are still scanned and reported, even though plain
`unittest.main()` would never collect them).

## The four categories

### WA001_NO_ASSERTION

**Pattern:** the test body, walked at *any* nesting depth (inside
`if`/`for`/`while`/`try`/`with`/`subTest`), contains no `ast.Assert`
statement and no `ast.Call` whose target attribute/name starts with
`"assert"` or equals `"fail"`. That single rule covers `self.assertEqual`,
`self.assertTrue`, `self.fail`, `cls.assertX` on classmethods, a bare
`assert` statement, and `with self.assertRaises(...):` (the context
expression is itself a matching `Call` node, so it needs no special case).

**The helper case:** many legitimate tests delegate to a `_check(...)`
helper that does the actual assertion. weakassert resolves **exactly one
level** of helper indirection:
- `self.<name>(...)` is resolved against methods of the *same class*.
- a bare `<name>(...)` is resolved against *module-level* functions.

If that helper's own body (checked with the same "no nested recursion"
rule) contains a direct assertion, the test counts as asserting.

**What it cannot resolve (stated plainly):**
- Two-level chains: `test_x` calls `self._check`, which calls
  `self._check2`, where `_check2` holds the real `self.assertEqual`.
  weakassert stops at `_check` and reports WA001 (verified by
  `TestHasAssertionViaHelper.test_two_level_helper_chain_not_resolved`).
- A helper that receives the test case as a plain parameter rather than
  `self`/`cls`, e.g. `def _check(case, a, b): case.assertEqual(a, b)`.
  In this specific corpus this still happens to match, because the
  detector's "is this an assertion call" rule looks only at the *called
  attribute name* (`assertEqual`), not at the receiver - so `case.assertEqual`
  is (deliberately loosely) treated the same as `self.assertEqual`. This is
  documented as an intentionally loose match, not a guarantee.
- Assertions reached only through a nested function/class literally defined
  inside the test body but never called (dead code) are incorrectly counted
  as "has an assertion" - `ast.walk` cannot tell "defined" from "reachable".
- Helper resolution does not look at inherited base-class methods; only the
  literal `class` body being scanned.

**False-positive risk:** a genuinely well-tested case can be reported if it
routes through two or more helper hops, or through a mixin/base-class
helper. **False-negative risk:** dead, never-called assertion code inside a
test silences a real WA001.

### WA002_CALL_ONLY

**Pattern:** WA001 is true (no assertion found) AND every top-level
statement in the test body (ignoring a leading docstring and bare `pass`)
is either a bare call expression (`foo()`) or a simple assignment whose
value is a single call (`x = foo()`) AND at least one of those calls
targets the *subject module* (see WA003 below for how the subject module is
resolved).

**Distinguishing WA002 from WA001:** WA001 fires for *any* assertion-free
test, including one that calls nothing at all (`pass`) or that only calls
unrelated stdlib helpers (`time.sleep(0)`). WA002 is the strict subset
where the calls being made are into the code under test - i.e. the test
*did* exercise the subject and *still* checked nothing beyond "this did not
raise". `TestCallOnlyCategory.test_non_subject_call_only_flags_wa001_not_wa002`
pins this distinction down.

**False-positive risk:** the "top-level statements only" rule deliberately
does not look inside `if`/`for`/`while`/`try` - a call-only assertion
wrapped in a redundant `if True:` is not recognised as WA002 (still WA001,
just not WA002). This under-reports rather than over-reports.

### WA003_SELF_DERIVED_EXPECTATION

**Pattern:** for every `self.assertEqual(first, second)` call anywhere in
the test body, if `first` and `second` **both** contain (anywhere in their
expression tree, at any depth) a call rooted at a name resolved as "the
subject module" (see below), the call is flagged. This is per-`assertEqual`-call,
not per-test: a test can have one flagged line and other, perfectly good,
assertions on other lines.

**Resolving "the subject module":** every name bound by this file's own
`import X [as Y]` or `from X import A [as B]` statements is a candidate,
*except* names bound from a recognised standard-library module (using
`sys.stdlib_module_names`). Whole-module binds (`import forecast as F`) go
into a `modules` set; symbol binds (`from forecast import compute`) go into
a separate `symbols` set. Relative imports (`from . import forecast`) are
always treated as subject, since they can only point at local package code.
This is a per-file, import-statement-only heuristic - it does not run or
introspect the imported module.

**One level of local-variable indirection:** if an `assertEqual` argument
is a bare local variable (e.g. `actual`), and that variable was bound by a
simple, *top-level* (not inside `if`/`for`/...) `actual = <expr>`
assignment earlier in the same test, weakassert also checks `<expr>` for a
subject-module call. This was added specifically to catch
`actual = subject.compute(7); self.assertEqual(actual, subject.compute(7))`
(see "Real bug caught" below) - it is deliberately narrow: it only resolves
when the *entire* argument is a bare name, not when a name merely appears
nested inside a larger expression such as `sorted(rels)` or `len(x)`. An
earlier, broader version that resolved names anywhere in the expression
tree was tried during development and rejected because it turned the
common, legitimate `assertEqual(x, sorted(x))` idiom into a wall of false
positives (see below).

**Be honest about what this misses:**
- Chained aliasing (`a = subject.f(); b = a; assertEqual(b, subject.f())`)
  is not resolved - only one hop of `name = <expr>` is followed.
- It cannot tell a genuinely wrong expected value that happens to route
  through the subject module (e.g. `assertEqual(subject.f(x), subject.g(y))`
  where `g` is a *different*, unrelated function) from a true tautology
  (`assertEqual(subject.f(x), subject.f(x))`). It flags both, by module,
  not by function - a deliberate recall-over-precision choice, stated here
  so no one mistakes it for "same function on both sides only".
- Values passed through a builtin or third-party wrapper around a subject
  call on one side only will not be seen unless the wrapper call's own
  arguments are exactly a bare local variable (see above).

**The dominant false-positive pattern, found for real in this scan:**
Determinism/idempotence/order-independence/roundtrip/symmetry tests have
*exactly* the same AST shape as a tautology:
`self.assertEqual(mod.canonical_json(r1), mod.canonical_json(r2))` where
`r1`/`r2` are built to be equal-but-differently-ordered, or
`self.assertEqual(mod.f(x), mod.f(x))` called twice on purpose to catch
nondeterminism. Both are "both sides call the subject module" by
construction, and both are, in this codebase, deliberate and meaningful
tests - see "Self-scan results" below, where every WA003 finding in the
30-suite corpus turned out to be exactly this pattern. weakassert has no
way to see the test's *intent* (only its test name, which it does not use
as a signal) - this is the single biggest precision gap in the tool.

### WA004_SKIPPED_TEST

**Pattern:** a test (or its enclosing class) is decorated with
`@unittest.skip(...)`, `@unittest.skipIf(...)`, or `@unittest.skipUnless(...)`
(matched on the decorator's attribute/name, whether written as
`unittest.skip` or imported bare as `skip`) - or the test body's first
unconditional top-level statement is `self.skipTest(...)`. A class-level
skip decorator is reported against every `test_*` method in that class,
tagged `class:<decorator-name>` in `detail` so it is distinguishable from a
method's own decorator.

**False-positive risk:** WA004 reports the *presence* of a skip marker, not
whether the skip is justified. A `skipUnless(platform_supports_symlinks, ...)`
guard is exactly as "skipped" to this detector as `@skip("flaky, TODO
fix")` - telling those apart requires reading the reason string and the
surrounding code, which is a human judgement call this tool deliberately
leaves to the human (see self-scan results: all 13 real WA004 findings in
this corpus are the former, legitimate kind).
**False-negative risk:** `self.skipTest(...)` reached only conditionally
(inside an `if`) is not detected, since only unconditional top-level
`skipTest` calls are recognised (a conditional skip is not a "this test
never really runs" situation, it is ordinary control flow).

## Self-scan results (real, committed)

`self_scan_report.json` is the actual, unedited output of, run from this
directory:

```
python3 weakassert.py --root .. -o self_scan_report.json
```

It scans every sibling tool directory in this repository, each with its own
`unittest` suite. The command exits `1`, because the scan finds something;
that is the tool's documented "findings present" status, not a failure.

```
files_scanned: 82
tests_scanned: 6468
findings_total: 246
  WA001_NO_ASSERTION: 13
  WA002_CALL_ONLY: 3
  WA003_SELF_DERIVED_EXPECTATION: 122
  WA004_SKIPPED_TEST: 108
files_with_errors: 0
```

Those seven numbers are not typed by hand. `test_weakassert_regen.py`
parses this fenced block out of this README and asserts each value against
`self_scan_report.json`, and asserts that the report byte-matches a fresh
run of the command above. If either drifts, the suite fails.

### Why that guarantee exists: this section used to be wrong

Until this repair, the command documented here was
`python3 weakassert.py --root /sessions/sharp-stoic-knuth/mnt/outputs -o self_scan_report.json`
-- an absolute path inside a sandbox session that no longer exists, so the
committed report could not be rebuilt by anyone, on any machine (it exits
`2`, "not a directory"). Behind that unrunnable command three things had
drifted apart with nothing to catch them:

| | files | tests | findings |
|---|---|---|---|
| what this README claimed | 35 | 3198 | 92 |
| what `self_scan_report.json` actually contained | 39 | 3430 | 112 |
| what the tree produced when this drift was repaired | 77 | 6155 | 242 |

All three rows are a snapshot of the moment the drift was found; the
third is labelled that way rather than "the current tree" because it is
not re-measured when the tree changes. The live numbers are the fenced
block above, which `test_weakassert_regen.py` checks against
`self_scan_report.json` on every run.

The README was not describing its own committed artifact, and the artifact
was not describing the repository. The unedited before/after runs are in
`REGENERABILITY_EVIDENCE.txt`.

Two things changed so this cannot recur silently: the entry
`weak-assertion-scanner:self_scan_report.json` was added to
`report-freshness/manifest.json` as `regenerable`, so the repo-wide
freshness check now rebuilds this report and byte-compares it; and the
README-to-report assertions described above were added.

**This is not "nothing found" and it is not "everything is broken" either -
read the breakdown below**, since raw counts alone would overstate the
problem by a wide margin (WA003 in particular).

### Scope note on the hand review below

Everything from here to the end of this section is the **original hand
review of the 2026-08-04 corpus: 35 files, 92 findings**. It is preserved
because the judgements in it are real -- each finding was opened and read
-- and because the reasoning about *why* WA003 over-reports is what makes
the raw count interpretable at all.

It has **not** been redone for the current 74-file, 239-finding corpus, and
it is not a claim about it. The file-and-line citations below are as of
that earlier corpus and some line numbers have since moved. Read the counts
above as mechanical and current; read the analysis below as a hand review
of an earlier, smaller subset. Redoing it at the current size is a separate
piece of work, not something to imply was done here.

**WA001 / WA002 (8 + 4 findings): all 8 are true positives.** Every one of
them is a real test with zero assertions. Four of the eight are also
WA002 (call-only body that calls the subject module). All eight follow the
exact same real pattern - a JSON-serializability/encodability check written
as a bare call with a `# must not raise` comment instead of an assertion:

- `bundle-index/test_bundle_index.py:428 TestCanonicalJsonBytes.test_output_is_valid_json`
  - `json.loads(out.decode("utf-8"))  # must not raise`
- `claim-checker/test_claimcheck.py:1083 TestCanonicalJsonBytes.test_is_pure_ascii_bytes`
  - `out.decode("ascii")  # must not raise UnicodeDecodeError`
- `dup-detector/test_dupdetect.py:468 TestCanonicalJson.test_output_is_pure_ascii`
  - `canonical_json(report).encode("ascii")`
- `regression-checker/test_regress.py:689 TestBuildReport.test_report_is_json_serializable`
  - `json.dumps(report)  # must not raise`
- `regression-checker/test_regress.py:905 TestCLIOutputHandling.test_output_flag_long_form`
  - opens the `-o` output file and calls `json.load(fh)`, checks nothing else
- `snapshot-diff/test_snapdiff.py:144 TestJsonify.test_jsonify_output_is_json_serializable`
  - `json.dumps(out)  # must not raise`
- `thread-check/test_thread_check.py:1153 TestBuildReport.test_report_is_json_serializable`
  - `json.dumps(report)  # must not raise`
- `loop-health/test_loop_health.py:1304 TestCLI.test_healthy_fixture_stdout_is_valid_json`
  - `json.loads(result.stdout)`, does not check the exit code or content

These pass or fail on "did an exception propagate", which unittest does
turn into a test ERROR - so they are not *worthless*, but they verify
nothing about the actual output content, and in most of these files a
sibling test (e.g. `test_output_is_reparseable`,
`test_unhealthy_fixture_exits_1`) already covers real content, so the
practical severity is low-to-moderate: a genuine gap, worth an explicit
`self.assertTrue(isinstance(out, bytes))`-style assertion or a comment
explaining the intent is already the whole test, but not a sign of
untested code.

**WA003 (67 findings): reviewed all 67 individually; classified as false
positives of the tool, not weak tests.** The dominant shape (roughly
45 of 67, by test name: `test_deterministic_*`, `test_repeated_*`,
`test_*_byte_identical*`, `test_*stable*`) is a deliberate determinism
regression test - call the subject function twice (same or intentionally
reordered/permuted input) and assert the two outputs are equal, e.g.
`bundle-index/test_bundle_index.py:777
TestDeterminismContract.test_two_runs_same_root_byte_identical` or
`consolidate/test_consolidate.py:63 TestCanonicalDumps.test_deterministic_across_calls`.
Given this whole corpus is built around deterministic/canonical output as
a first-class requirement, this pattern is everywhere and is doing exactly
what its name says. The remainder are order-independence checks
(`bundle-index/test_bundle_index.py:397
test_sort_findings_is_stable_and_deterministic_across_runs` - sorts the
same list forwards and reversed, checks convergence), symmetry checks
(`dup-detector/test_dupdetect.py:167 TestJaccard.test_symmetric` -
`jaccard(a, b) == jaccard(b, a)`), idempotence checks
(`dup-detector/test_dupdetect.py:186 test_formatting_is_idempotent` -
`format_score(v) == v`), case-insensitivity checks
(`payload-validator/test_payload_validate.py:138-139`), and a couple of
dunder-equality checks
(`wallet-reconciler/test_wallet_reconcile.py:883 TestNonFiniteSentinel.test_equality`).
None of these are "assert f(x) == f(x), which passes no matter how wrong
f is" in the sense the spec warns about - they compare *related but
distinct* invocations to test a real property (determinism, symmetry,
order-invariance, idempotence), and a broken implementation of that
property would fail them. One nuance worth naming:
`consolidate/test_consolidate.py:92 test_detail_from_sorted_and_deterministic`
has a WA003-flagged line (`self.assertEqual(d1, d2)`, a determinism check)
*and* a second, real content assertion (`self.assertTrue(d1.startswith("a="))`)
in the same test - WA003 correctly flags only the specific weak line, not
the whole (partially fine) test, which is the granularity the tool is
designed to have.

This zero-true-positive result for WA003 is itself informative: it means
the "expected computed by calling the same module that produced actual"
shape, in this specific corpus, is used exclusively for legitimate
determinism/property testing rather than by accident - but it is exactly
the failure mode the category exists to catch, and a corpus that *did*
have an accidental `assertEqual(f(x), f(x))` correctness tautology would
look identical in this report. Treat every WA003 finding as "needs a human
to read the test name and decide", not as "definitely broken".

**WA004 (13 findings, all in `bundle-index/test_bundle_index.py`): all
13 are legitimate, narrowly-scoped conditional skips, not lazy disables.**
Three kinds, all guarded so they only actually skip when genuinely
inapplicable:
- `@unittest.skipUnless(HAS_SYMLINK, ...)` (`HAS_SYMLINK = hasattr(os, "symlink")`,
  true on this Linux host, so not actually skipped here) - symlink tests.
- `@unittest.skipIf(os.name == "nt" or ... geteuid() == 0, ...)` - a POSIX
  permission-bits test that is meaningless as root or on Windows.
- `@unittest.skipUnless((FIXTURES_DIR / "bundle_ok").is_dir(), ...)` /
  `bundle_bad` - class-level guards for two test classes
  (`TestBundleOkFixture`, `TestBundleBadFixture`) whose fixture
  directories (`bundle-index/bundle_ok/`, `bundle-index/bundle_bad/`) do
  in fact exist in this repo, so these are not actually skipped either.
  This is the majority of the 13: a class-level skip decorator is reported
  once per `test_*` method in the class (8 of the 13 findings come from
  these two classes).

No other file in the corpus uses any skip decorator or `skipTest` at all
(confirmed separately with a source grep before building the sample
fixtures). Given ~3198 tests scanned, finding real (not-accidentally-always-on)
skip usage in exactly one file, all defensively guarded, is plausible and
consistent with a corpus this size that otherwise runs its full suite.

**Plausibility of the totals, given ~3198 tests scanned:** 8+4 real
assertion-free tests out of 3198 (about 0.25%) is a small but real and
fixable gap, concentrated in one recurring "does not raise"-on-JSON-encode
idiom that a handful of authors independently reached for. Zero genuine
WA003 tautologies, and skip usage limited to one well-guarded file, are
both consistent with a corpus that was written under an explicit
determinism/reproducibility mandate (visible in nearly every tool's
`canonical_json`/`canonical_dumps` naming) - the property tests this
report's WA003 findings represent are a *direct consequence* of that
mandate, not noise.

## Reproducing the verification run

All commands below were run for real from this directory and the exact
output is captured in `captured_output.txt`:

```
python3 -m unittest test_weakassert -v
python3 weakassert.py --root samples_strong ; echo "exit=$?"
python3 weakassert.py --root samples_weak -o r1.json ; echo "exit=$?"
python3 weakassert.py --root samples_weak -o r2.json ; echo "exit=$?"
sha256sum r1.json r2.json
cmp r1.json r2.json && echo BYTE-IDENTICAL
python3 weakassert.py --root samples_weak --category WA004_SKIPPED_TEST ; echo "exit=$?"
python3 weakassert.py --root /nonexistent_dir ; echo "exit=$?"
grep -c "/sessions\|/tmp\|/home" r1.json
```

`r1.json`/`r2.json` are scratch files produced by the commands above - they
are not part of this deliverable tree (only `self_scan_report.json` is
committed).

### Relocation test

```
cp -r samples_weak /tmp/relocated_weak_copy
python3 weakassert.py --root samples_weak > /tmp/a.json
python3 weakassert.py --root /tmp/relocated_weak_copy > /tmp/b.json
sha256sum /tmp/a.json /tmp/b.json
cmp /tmp/a.json /tmp/b.json && echo RELOCATION-IDENTICAL
```

Both hashes and the `cmp` result are captured in `captured_output.txt`.

## Real bug found and fixed during development

While building the `samples_weak/` fixtures, a test written as:

```python
def test_tautological_compute_different_call_sites(self):
    actual = subject.compute(7)
    self.assertEqual(actual, subject.compute(7))
```

was **not** flagged by the first working version of WA003. The bug: the
original `contains_subject_call` only looked for `ast.Call` nodes literally
inside the `assertEqual` argument expression. `actual` is a bare
`ast.Name`, not a `Call`, so the first argument was invisible to the
detector even though it was, one line earlier, assigned directly from a
subject-module call - the textbook self-derived-expectation case the whole
category exists to catch.

Fixed by adding `_local_assign_map` / `contains_subject_call_with_locals`
(one level of local-variable resolution, restricted to when the entire
argument is a bare name - see WA003 section above for why it is restricted
that way, and what the first, broader attempt at the fix broke). Regression
tests: `TestSelfDerivedExpectationDetection.test_local_variable_indirection_flagged`
and `.test_local_variable_pointing_at_non_subject_not_flagged` (make sure
the fix does not fire when the variable is *not* subject-derived), plus
`.test_chained_alias_not_resolved_documented_limitation` (pins down what
the fix deliberately still does not do). The tool was fixed, not the test -
the test encoded exactly the case the spec calls out ("f(x) == f(x) passes
no matter how wrong f is") and the detector was the thing that was wrong.

## Limitations a reviewer should scrutinise

1. **Subject-module resolution is import-statement-only and file-local.**
   It cannot see through re-exports, `sys.path` tricks, dynamic `importlib`
   loading, or a test file that imports the subject under a name that is
   *also* used for something else in the same file. A test file with no
   non-stdlib imports at all (e.g. it imports its subject via a relative
   package path the heuristic does not recognise) will simply never
   produce WA002/WA003 findings, silently - there is no "I could not
   determine the subject module" warning in the report today.
2. **WA003 has a real, demonstrated, high-volume false-positive mode**
   (property/determinism/symmetry/idempotence tests - see "Self-scan
   results" above, where this was 67-for-67 in the actual corpus). Anyone
   treating "found N WA003 findings" as "N confirmed bugs" without reading
   `detail` and the test name will be misled. The tool intentionally
   favors recall over precision here and says so; a reviewer who wants
   higher precision could extend the detector to compare the two calls'
   argument ASTs and only flag when they are structurally identical (the
   narrowest, highest-confidence tautology shape) - that was considered
   and deliberately not done, to keep the rule simple and auditable, and
   because "different arguments, both from the subject" is still a real
   (if lower-severity) smell worth a human's attention.
3. **All four detectors work on syntax, not control flow or types.** A
   `self.assertTrue(True)` satisfies WA001 exactly the same as
   `self.assertEqual(result, expected)`; the tool has no notion of whether
   an assertion is itself meaningful, only whether one is textually
   present. Similarly, `has_direct_assertion` matches dead code inside a
   never-called nested function (documented above), and helper resolution
   trusts a `self.<name>` or bare `<name>` call to actually route to the
   method/function it looks up by name at "compile" time, with no handling
   of monkeypatching, `__getattr__`, or dynamically added methods.

## Files in this deliverable

- `weakassert.py` - the scanner (stdlib only).
- `test_weakassert.py` - 202 unittest tests (`python3 -m unittest test_weakassert -v`).
- `samples_strong/` - a test suite designed to trip none of the four categories.
- `samples_weak/` - a test suite designed to trip all four categories.
- `demo_category/` - a minimal fixture used only to demonstrate `--category`
  flipping the exit code (see above).
- `self_scan_report.json` - the real, committed report from scanning the
  ~30 sibling tool suites under `outputs/`.
- `captured_output.txt` - real captured terminal output of every command
  in "Reproducing the verification run" above, plus the relocation test.
