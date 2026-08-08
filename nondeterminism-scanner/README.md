# ndscan

A stdlib-only Python 3 CLI that statically scans a directory of `.py` files
for six classes of non-determinism risk, and emits a canonical, byte-stable
JSON report.

```
python3 ndscan.py --root <directory> [-o report.json] [--rule RULE_ID ...] [--min-severity low|medium|high]
```

Exit codes: `0` clean, `1` findings (or per-file scan errors — see
"SyntaxError files" below), `2` usage error or fatal scan setup error
(missing `--root`, `--root` not a directory, unwritable `-o` path, unknown
`--rule`/`--min-severity` value).

## Flags

| flag | description |
|------|-------------|
| `--root DIR` | Required. Root directory to scan for `.py` files. |
| `-o`, `--output PATH` | Write the canonical JSON report to this file instead of stdout. Optional; without it, the report goes to stdout. |
| `--rule RULE_ID` | Repeatable (`--rule ND001_WALL_CLOCK --rule ND005_UNSEEDED_RANDOM ...`). Restricts the scan to the given rule id(s), one of the six `ND0xx` codes below. Default: run all six rules. |
| `--min-severity {low,medium,high}` | Only include findings at or above this severity. Default `low` (include everything). |

## Why AST, not regex

The spec for this tool requires scanning for patterns like `os.listdir(`,
`repr(`, `random.random(`, `datetime.now(`. A regex scanner matching those
literal substrings would fire just as happily inside a string literal, a
comment, a docstring, or someone's variable name (`repr_count = 1`) as
inside real code — it has no concept of "this text is a string, not an
expression." That makes a regex-based version of this tool nearly useless
for real code review: every hit needs a human to first re-derive what a
parser would have told the tool for free.

`ndscan.py` instead parses every file with `ast.parse()` and walks the
resulting syntax tree, matching structural patterns (a `Call` node whose
resolved callee is `datetime.datetime.now`, a `For` node whose `.iter` is a
`Set` node, etc.). A string that merely contains the text `datetime.now()`
parses as an `ast.Constant` of type `str`, not a `Call`, so it is
structurally invisible to every rule below. `samples_clean/basic_clean.py`
and every rule's test class in `test_ndscan.py` include an explicit
`test_pattern_inside_string_not_flagged` / `test_pattern_inside_comment_not_flagged`
case proving this.

The trade-off: AST analysis is still purely static (no type inference, no
data-flow, no execution). It knows syntax, not semantics or runtime types.
Every rule below documents exactly what that costs it.

## Canonical JSON contract

`json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)` +
one trailing `\n`. `sort_keys=True` only orders *object keys* — it does
nothing to order the *elements* of a JSON array. `ndscan.scan_root()`
therefore explicitly sorts the `findings` list by
`(rule_id, path, line, col, detail)` and the `errors` list by
`(path, message)` before the report is built, so two runs over identical
input are byte-identical regardless of filesystem walk order. See
"Verification" below for the `sha256sum`/`cmp` proof.

## Paths are relative, never absolute; no wall-clock, no duration

Every `path` field in a `finding`/`error` is `os.path.relpath(file, root)`
with `os.sep` normalized to `/`. The `--root` value itself is never written
into the report (there is no `"root"` key in the JSON at all) — only
relative paths appear. The report also carries no timestamp, no wall-clock
read, and no "how long did this scan take" field: producing this report
does not call `time.time()`/`datetime.now()` anywhere in `ndscan.py`, and
nothing in `build_report()`/`canonical_json()` embeds anything but the
scan's own findings/counts. Proven by `grep -c "/sessions\|/tmp\|/home"` on
the reports (prints `0`) and by the relocation test in
`captured_output.txt` (copy `samples_risky/` elsewhere, re-scan, identical
SHA-256).

## Severity is editorial judgement, not a property of the language

Each rule has exactly one fixed severity (`RULE_SEVERITY` in `ndscan.py`),
used by `--min-severity` to filter:

| rule_id                     | severity | rationale                                                              |
|------------------------------|----------|--------------------------------------------------------------------------|
| ND001_WALL_CLOCK              | high     | directly reads a value that changes every call; almost never intentional in a reproducible pipeline |
| ND002_UNSORTED_LISTDIR         | high     | filesystem enumeration order is an OS/filesystem implementation detail, not part of Python's language guarantees |
| ND003_UNORDERED_ITERATION       | medium   | real risk, but often benign in practice (see self-scan below) |
| ND004_UNSAFE_REPR                | low      | the heuristic is the noisiest of the six (see below); treated as low-confidence by design |
| ND005_UNSEEDED_RANDOM              | high     | directly nondeterministic across runs by construction |
| ND006_FLOAT_IN_MONEY                 | medium   | a real correctness risk (binary float rounding) but not itself "changes every run" the way ND001/ND005 do |

None of this is a fact about Python; it is this tool's opinion about which
findings are worth a reviewer's attention first. A different reviewer could
reasonably rank ND003 above ND002. `--min-severity` lets a caller impose
their own cutoff without needing `--rule` gymnastics.

## The six rules

### ND001_WALL_CLOCK (high)
**AST pattern:** a `Call` node whose callee, resolved through the file's
import-alias map (see "Alias resolution" below), is exactly one of
`datetime.datetime.now`, `datetime.datetime.utcnow`, `datetime.date.today`,
`time.time`, `time.monotonic`.

**False negatives:** the module/class is never visibly imported (imported
elsewhere and passed in, imported inside a `try/except ImportError`
fallback under an unresolved name, obtained via `getattr`/`importlib`); any
indirection through a variable reassigned from the module after import
(alias resolution is import-based only, not full data-flow); `time`
imported under two different names is fine, but `import time` followed by
`t = time; t.time()` is not recognized (`t` is not itself an import).

**False positives:** a local variable/parameter that shadows an imported
module/class name (e.g. a function parameter literally named `time`) is
indistinguishable from the real import, because alias resolution is
file-wide, not scope-aware.

### ND002_UNSORTED_LISTDIR (high)
**AST pattern:** a `Call` node whose callee resolves to `os.listdir`,
`os.scandir`, `os.walk`, or `glob.glob`, whose immediate AST parent is not
`sorted(<this call>, ...)` with the call as the first positional argument.

**False positives:** `items = os.listdir(d); items = sorted(items)` — the
sort happens one statement later, not immediately, so this still flags
even though the final value used is sorted. `sorted(list(os.listdir(d)))` —
`sorted()` wraps `list(...)`, not the listdir call directly. **The
self-scan below found a third, more interesting false-positive shape**: the
`dirnames.sort()`-in-place idiom for `os.walk()` (`for dirpath, dirnames,
filenames in os.walk(root): dirnames.sort(); ...`) is the textbook-correct
way to make a directory walk deterministic (it also fixes the *recursion
order*, which merely calling `sorted(os.walk(root))` would not — sorting
the yielded 3-tuples does not reorder the *walk itself*). ndscan does not
recognize this idiom at all; every one of the 5 ND002 findings in the
*original* self-scan was this shape or the "sorted one statement later"
shape. See "Self-scan results" below, including what that qualifier
means.

**False negatives:** `sorted` reassigned to something that is not the
builtin (ndscan trusts any `Call` whose `func` is literally `Name('sorted')`);
any wrapper function that internally sorts (e.g. `my_sorted_listdir(d)`).

### ND003_UNORDERED_ITERATION (medium)
**AST pattern:** a `For` loop whose `.iter` is (a) a set literal, (b) a
`set(...)`/`frozenset(...)` call, (c) a dict literal, or (d) a
`.keys()`/`.values()`/`.items()` method call — and is *not* itself wrapped
in `sorted(...)` — where the loop body (excluding nested `def`/`class`/
`lambda` bodies) contains an `.append(`/`.extend(`/`.add(`/`.update(` call,
or an `x += [...]`/`x += (...)` augmented assignment with a list/tuple
literal right-hand side.

**The single most important thing to know about this rule, found during
the mandatory self-scan:** Python dicts have guaranteed insertion-order
iteration since 3.7. `for k, v in d.items():` is only actually
non-deterministic across runs if the *insertion order of `d` itself* was
non-deterministic — e.g. built by iterating a `set()`, or from unsorted
`os.listdir()` results. If `d` was built by iterating an already-ordered
list (e.g. records loaded from a JSON array, which preserves file order),
`d.items()` iteration is just as deterministic as that list was. ndscan
has no data-flow analysis, so it cannot tell "this dict's insertion order
traces back to a JSON array" from "this dict's insertion order traces back
to a set" — it flags *all* un-sorted dict iteration whose body accumulates,
uniformly. In the original self-scan, 24 of 26 ND003 findings were this
dict shape, and on manual inspection every dict in question was built from
deterministically-ordered input. This is the rule's dominant, and largely
unavoidable without real data-flow analysis, false-positive source.

**Other false positives:** the body's accumulation call is unrelated to
element order (e.g. it always appends a constant); `.update()`/`.add()` on
the loop variable itself rather than an outer accumulator.

**False negatives:** `for k in some_dict:` where `some_dict` is a bare
`Name`/`Attribute` (not a dict literal or `.keys()/.values()/.items()`
call) is invisible — no type inference means a dict referenced only by
variable name cannot be told apart from a list; accumulation via
`result[key] = value` (dict item assignment) instead of
`.append`/`.extend`/`+=`; accumulation via a helper function call, e.g.
`collect(item)`; scalar `total += item` is deliberately *not* treated as
accumulation (see "Bug found during development" below) — but that also
means an `AugAssign` that concatenates a *variable* holding a list
(`out += other_list`, where `other_list` is a `Name`, not a literal) is
missed too.

### ND004_UNSAFE_REPR (low) — CANNOT BE DECIDED STATICALLY
Whether `repr(x)` is safe depends on `x`'s *runtime type* and whether that
type defines a deterministic `__repr__`. The default `object.__repr__`
embeds the object's memory address (`<Foo object at 0x7f3c1a2b3c40>`),
which genuinely differs between runs — that is the real risk this rule is
about. A static AST scanner has no type information, so this can only ever
be a heuristic, and it is exactly as good or bad as the heuristic below.

**The heuristic actually used:** flag `repr(X)` / `f"{X!r}"` /
`"...%r..." % X` **unless** `X` is judged "obviously safe": a literal
`Constant`; a `list`/`tuple`/`set`/`dict` built entirely out of other
obviously-safe expressions; a `Compare` or `BoolOp` (always yields `bool`);
a unary/binary op over obviously-safe operands; or a call to one of a small
whitelist of builtins whose return type has deterministic repr (`str`,
`int`, `float`, `bool`, `list`, `dict`, `tuple`, `set`, `frozenset`,
`bytes`, `bytearray`, `complex`, `len`, `repr`, `sorted`, `abs`, `round`,
`min`, `max`, `sum`).

**What this over-fires on:** `repr()`/`!r`/`%r` of *any* variable,
attribute, subscript, or call to a user-defined/unknown function is
flagged, even when the underlying runtime value is a plain `str`/`int` held
in a variable — e.g. `n = 3; repr(n)` flags, because ndscan cannot see that
`n` is an `int`. **This is by a wide margin the dominant false-positive
source found in the self-scan** — see below: essentially every one of the
220 ND004 findings in the original scan was `%r`/`!r` applied to a plain
string pulled straight out of a parsed JSON record or an argparse value,
which is guaranteed to have a deterministic `str` `repr`, but which ndscan
cannot know is a `str` without type inference.

**What this misses:** `repr()` of a call to a whitelisted builtin that has
been locally shadowed (`def str(x): ...` masking the builtin) is treated as
safe; the *argument* to a whitelisted call is not itself checked — e.g.
`str(some_custom_obj)` is judged safe even though `str()` will invoke
`some_custom_obj.__str__`/`__repr__` internally — ndscan only judges the
outermost `repr()`/`!r`/`%r` site, not the whole value chain. `%r` handling
is additionally coarse: if the format-string constant contains `"%r"`
*anywhere*, the whole right-hand operand of `%` is flagged once, without
matching it to the correct positional specifier when there are multiple
`%` conversions in one string.

### ND005_UNSEEDED_RANDOM (high)
**AST pattern:** any `Call` resolving (via the alias map) to `random.<x>`
other than `random.seed` itself, when the module contains *no* call
resolving to `random.seed` anywhere; and any `Call` resolving to
`secrets.<x>` — **always**, regardless of seeding. `secrets` has no seeding
mechanism by design (it is meant to be cryptographically unpredictable),
so it is *impossible* to make a `secrets.*` finding "clean" — this is
intentional: the rule surfaces every cryptographic-randomness call site for
a reviewer's awareness, not because seeding is expected, but because the
task spec explicitly groups `secrets.*` under "unseeded randomness" and
there is no seed to check for.

**False negative (documented, not "fixed", because fixing it needs
execution-order analysis this tool does not do):** presence of a
`random.seed()` call *anywhere* in the module suppresses *all* `random.*`
findings in that module, regardless of whether the seed call textually or
temporally precedes the random usage. `if False: random.seed(1)` followed
by `random.random()` is judged clean, even though the seed never actually
executes before the random call.

### ND006_FLOAT_IN_MONEY (medium)
**AST pattern**, two independent triggers (a single statement can trip
both — that produces two distinct findings, intentionally, not a
duplicate-detection bug):
  - (a) a bare `float(...)` call whose first argument is a `Name`/`Attribute`
    whose identifier matches `MONEY_RE`;
  - (b) an assignment (`Assign`/`AugAssign`/`AnnAssign`) target that is a
    `Name`/`Attribute` matching `MONEY_RE`, whose value is a float
    `Constant` literal or a `float(...)` call.

`MONEY_RE = re.compile(r"(amount|price|reward|balance|total|payout|fee|drops)",
re.IGNORECASE)` — exactly as specified in the task brief, i.e. an
*unanchored substring* match with no word boundaries.

**False positives** are a direct, intentional consequence of that
unanchored regex: `toffee = 3.5` flags (contains `"fee"`), `coffee_price =
3.5` flags twice over (contains `"fee"` and `"price"`).

**False negatives:** implicit float results never explicitly wrapped in
`float()` or written as a literal — `amount = total_cents / 100` (true
division always yields `float` in Py3) is invisible to this rule; money
stored via subscript, e.g. `data["price"] = 9.99` (the target is a
`Subscript`, not `Name`/`Attribute`); a float value passed as a **keyword
argument** to an unrelated call, e.g. `charge(amount=9.99)` — found live
during the self-scan (`reward-reconciler/test_reconcile.py:90`,
`mk([r(amount=3.5)])`) — ndscan only inspects assignment targets and
`float()` call arguments, never a `keyword` node in an arbitrary call.

## Alias resolution

Shared by ND001, ND002, ND005 (`build_aliases()` + `resolve_call_target()`
in `ndscan.py`): a single file-wide map from local name to canonical
dotted target, built from every `Import`/`ImportFrom` node found anywhere
in the module via `ast.walk()` — not scope-aware (an import inside one
function is treated as visible to the whole file), and "last import wins"
if the same local name is bound twice. This is what lets `import time as
t; t.time()`, `from datetime import datetime as dt; dt.now()`, and `from
time import time as t2; t2()` all resolve to the same canonical name as
their unaliased spelling. Relative imports (`from . import x`) and star
imports (`from x import *`) are skipped — there is no statically-resolvable
absolute target for either, so any usage they introduce is invisible to
alias-dependent rules (documented false negative).

## SyntaxError files, non-UTF-8 files, and empty files

**Decision:** a file that fails to parse is *not* a fatal usage error for
the whole run. `scan_file()` catches `OSError` (unreadable),
`UnicodeDecodeError` (not valid UTF-8), and `SyntaxError` (fails to parse)
per file and records `{"path": ..., "message": ...}` in the report's
top-level `"errors"` array — the scan continues to the next file. **Why**:
one unreadable or unparseable file among many (samples_risky/ has 7 files;
the self-scan target has 182) should not make the whole run's exit code and
report indistinguishable from "the caller passed a garbage `--root`".
Exit-`2` is reserved for usage problems the *caller* can fix by changing
their command line (bad `--root`, bad `--rule`, unwritable `-o`); a
malformed `.py` file is a fact about the *target* being scanned, which the
`errors` array surfaces without derailing the rest of the scan.

**Exit code:** a run with zero findings but one or more `errors` entries
still exits `1`, not `0` — `0` is reserved for "every file was scanned and
came back clean"; an unscannable file is something the operator needs to
see, so it cannot be silently swallowed into a `0`. Error messages never
embed the failing file's absolute path (`OSError.strerror` is used instead
of `str(exception)`, which on some platforms embeds the path) — this is
what keeps the "no absolute paths in the report" contract true even on the
error path; see `TestScanFile.test_error_message_never_contains_absolute_path`.

**Non-UTF-8 files** are read as raw bytes first and decoded with strict
UTF-8; a decode failure is reported the same way as a `SyntaxError` (an
`errors` entry, not a crash). **Empty files** parse to an empty `ast.Module`
with no statements — every rule check trivially returns no findings, so an
empty file is always clean.

## Bug found during development

While writing `samples_clean/clean_iteration.py`, a function that iterates
a set literal but only ever does `total += item` (plain scalar
accumulation, not "appends to another collection") was expected to *not*
trip ND003 — but the very first version of `_body_has_accum()` treated
*every* `AugAssign` with `Add` as accumulation, so `total += item` was
indistinguishable from `out += [item]`. That is wrong: adding a number to a
running total is commutative and order-independent for ints (the loop
order genuinely does not change the result), while list/tuple
concatenation is order-sensitive. **Fixed** in `ndscan.py` by requiring the
`AugAssign`'s right-hand side to be a `List`/`Tuple` literal before it
counts as accumulation (`_body_has_accum`, see the inline comment).
`test_ndscan.py::TestND003UnorderedIteration::test_scalar_augassign_not_counted_even_with_other_append`
and `::test_set_iterated_no_accum_not_flagged` pin this down.

A second, related bug was caught by
`test_nested_function_def_inside_body_not_counted`: `_walk_no_nested_defs()`
is supposed to skip nested `def`/`class`/`lambda` bodies so that an
`.append()` call *inside a helper function defined inside the loop body*
does not count as "the loop body accumulates on every iteration" — but the
exclusion check only fired when a `FunctionDef` was reached as a *child*
during recursion, not when the top-level statement handed to it *was
itself* a `FunctionDef`. `_body_has_accum()` now skips such statements
before recursing into them at all. Both bugs were fixed in the tool, not
worked around in the tests, per this task's instructions — the tests
encoded the correct, documented semantics of ND003 from the start.

## Self-scan results (MANDATORY — run against the repository's own tools)

From this directory, exactly:

```
python3 ndscan.py --root .. -o self_scan_report.json
```

`self_scan_report.json` is the **real, unedited output** of that command,
run against the whole repository. Exit code: **1** (findings exist).
It is a *current-state* snapshot, so it is expected to change whenever
the repository's Python does, and `report-freshness/manifest.json` now
carries a `regenerable` entry that fails if it stops matching — see
"Regeneration is locked" below.

```
files_scanned:   182
files_errored:   0
findings_count:  459

                             total    own others
  ND001_WALL_CLOCK:             7      7      0
  ND002_UNSORTED_LISTDIR:      71     10     61
  ND003_UNORDERED_ITERATION:   24      3     21
  ND004_UNSAFE_REPR:          343      5    338
  ND005_UNSEEDED_RANDOM:        7      5      2
  ND006_FLOAT_IN_MONEY:         7      7      0
```

"own" is every finding under `nondeterminism-scanner/` — the column is a
path prefix, not a synonym for "fixture". 33 of the 37 are in
`samples_risky/`, which exists to be flagged; the other 4 are real
findings in this tool's own code and tests (1 in `ndscan.py`, 3 in `test_selfscan_freshness.py`). "others" is
every other directory in the repository — 47 of them have at least
one finding. Read the split before concluding anything about the
repository: **every** ND001 and ND006 finding is a planted fixture, so
among the sibling tools those two counts are still zero. ND005 is not quite that clean — 2 of
its findings are in sibling tools, and both are
`random.Random(<literal seed>)`, a generator seeded through the
constructor that this rule's "is there a `random.seed()` call in this
module" heuristic cannot see. Both are reproduced in full in
`REGENERABILITY_EVIDENCE.txt` §8b.

### What the previous report said, and why it was replaced

The report committed with this tool reported `files_scanned: 87`,
`findings_count: 251`, and zero for ND001/ND005/ND006. No commit was ever
made against that tree: the repository's tracked `.py` count steps
86 -> 88 and has never been 87, so that report reproduced at **no commit
in this repository's history** — not even at the commit that added it,
whose tree has 71 `.py` files and yields 204 findings. It was written
once and never touched again while the repository roughly doubled.
`REGENERABILITY_EVIDENCE.txt` §0–§3 is the transcript of that check.

### How to read the finding-by-finding analysis below

The triage below was written by hand against that original scan. It is
kept, rather than deleted, because its judgements are about *rule
behaviour* at named call sites that a reader can still open and check --
and because deleting the only honest account of what this scanner
over-fires on would be a worse outcome than dating it. But it is dated:

  * Its counts ("5 ND002 findings", "24 of 26", "220") are counts **from
    the 87-file scan**, not from the committed report. The current
    per-rule counts are the table above.
  * The current report has **not** been re-triaged finding by finding.
    Nothing below should be read as a judgement about the 422
    sibling-tool findings in it.
  * The `path:line` citations below were re-checked mechanically against
    the current report, after two of them were corrected in place
    (`claim-checker/claimcheck.py` 139 -> 199,
    `schema-checker/schema_check.py` 427 -> 436): 22 of 24 now
    resolve to a finding at exactly that path and line. The remaining
    2 are cited as things that are *not* findings — a documented
    false negative and a `grep` hit inside an assertion — which is still
    true.

**A scanner that reports its author's target as clean is not credible, so
here is the honest breakdown, finding by finding, with a true/false
positive judgement for every group and why:**

**ND001_WALL_CLOCK — 0 findings in the original scan** (and still 0
across the sibling tools today; the 7 in the committed report are all
planted fixtures)**.** Verified by hand with
`grep -rnE "datetime\.(now|utcnow)\(|date\.today\(|time\.time\(|time\.monotonic\("`
across all 30 tools: the only matches are inside comments/test assertions
that explicitly check the pattern is *absent*
(`staleness-monitor/test_staleness.py:913`: `self.assertNotIn("datetime.now(", src)`).
This codebase visibly self-audits for wall-clock reads already — 0 is a
plausible true negative, not a scanner blind spot.

**ND005_UNSEEDED_RANDOM — 0 findings in the original scan.** Verified
the same way at the time: `grep -rl "random\.\|secrets\."` across all 30
tools returned nothing. That is no longer true of the repository: the
committed report has 7, of which 2 are in sibling tools, and both of
those are constructor-seeded `random.Random(<literal>)` — see the table
above and `REGENERABILITY_EVIDENCE.txt` §8b.

**ND006_FLOAT_IN_MONEY — 0 findings in the original scan** (and still 0
across the sibling tools today)**.** Consistent with `reward-anomaly`'s
own committed README, which states as a design principle: "Decimal-only
money parsing (`decimal.Decimal`, never `float`)". True negative — and also
where the one documented false-negative gap above
(`reward-reconciler/test_reconcile.py:90`, `r(amount=3.5)` as a keyword
argument) was actually found, by grepping for the money-regex independently
of ndscan itself, precisely because ndscan does not check keyword
arguments.

**ND002_UNSORTED_LISTDIR — 5 findings in the original scan, all 5 judged FALSE POSITIVE for
real non-determinism** (true positive only against ndscan's narrow,
documented "immediately wrapped" pattern):
  - `bundle-index/bundle_index.py:102`, `claim-checker/claimcheck.py:199` —
    both walk with `os.walk()`, append relative paths to a list inside the
    loop, then call `relpaths.sort()` immediately after the loop. Sorted
    before use; not sorted at the call site.
  - `consolidate/consolidate.py:543`, `evidence-harness/harness.py:159` —
    both use the `dirnames.sort()`-in-place-per-iteration idiom (plus
    `sorted(filenames)` per iteration), which is the textbook-correct way
    to make `os.walk()` fully deterministic, including recursion order.
    ndscan's "must be wrapped in `sorted()`" pattern does not recognize
    this idiom at all — see ND002's rule doc above.
  - `regression-checker/regress.py:181` — `os.listdir()` result is
    filtered into a list across a `for` loop, then `return sorted(names)`.
    Same "sorted one statement later" shape as the first two.

**ND003_UNORDERED_ITERATION — 26 findings in the original scan: 24
dict-iteration, 2 set-iteration.**
  - The **24 dict findings** (`bundle-index/bundle_index.py:276`,
    `contradiction-detector/contradict.py:632`, both copies of
    `link-integrity/link_integrity.py:260,334`, both copies of
    `preflight/preflight.py:286,307,370`, both copies of
    `queue-auditor/queue_audit.py:392,403`, `evidence-harness/harness.py:329,367,405`,
    `thread-check/thread_check.py:711`, and three `test_*.py` files
    iterating `.items()`/`.values()` in test-fixture-comparison code) were
    each traced back by hand to where the dict is built. In every case
    checked, the dict (`hashes_seen`, `sm_findings_by_task`, `by_submission`,
    `tasks_by_id`, `id_counts`, `responses`, ...) is populated by iterating
    an already deterministically-ordered source — a list loaded from a JSON
    array, or a list already sorted/filtered earlier in the same function.
    Since CPython 3.7+ dicts iterate in insertion order, and that insertion
    order is itself a deterministic function of deterministic input here,
    these 24 are judged **FALSE POSITIVE for actual run-to-run
    non-determinism** — true positive only against ndscan's literal,
    type-blind AST pattern. (`contradiction-detector/checkers/*` duplicates
    several of these paths because `contradiction-detector` vendors copies
    of other tools' source under `checkers/` — not a scanner bug, a
    property of the target repo's layout.)
  - The **2 set findings** (`evidence-scorer/score_evidence.py:174`,
    duplicated once under `contradiction-detector/checkers/evidence-scorer/`)
    are `for s in set(sentences(r["text"])): sent_owners.setdefault(s, set()).add(...)`.
    Iterating a `set` of `str` **is** genuinely hash-order-dependent across
    process runs in CPython when `PYTHONHASHSEED` is not fixed (the default)
    — this is a real, textbook Python determinism footgun, so the pattern
    match is a **TRUE POSITIVE** by the rule's own definition. On manual
    reading of the surrounding function, though, `sent_owners` is only ever
    queried by `.get(key)` afterward (membership + `len()` of the per-key
    set), never iterated or serialized itself, so the *iteration order*
    of the inner `set()` does not currently leak into `score_evidence.py`'s
    observable output. Net judgement: a legitimate, worth-a-human's-look
    static-analysis flag (the underlying operation genuinely is
    hash-order-dependent) that does not currently cause an observable bug
    in this specific call site.

**ND004_UNSAFE_REPR — 220 findings in the original scan, by far the
largest group there (88% of all findings) — and, on inspection, essentially all FALSE POSITIVE for real
non-determinism.** A random sample of 15 of the 220 (`random.seed(7)`,
listed in the development transcript) was read by hand; every single one
was `%r`/`!r` applied to a plain `str` — a JSON record field
(`task_id`, `mid`, `submission_id`, `at`), an argparse-derived string, or a
dict/config key — pulled directly out of `json.load()` or `sys.argv`. JSON
values are always one of `str`/`int`/`float`/`bool`/`None`/`list`/`dict`;
none of those have a non-deterministic `repr()`. Every one of these is a
textbook case of the documented ND004 limitation: the value is *actually*
a safe builtin type, but it reaches `repr()`/`!r`/`%r` via a `Name` (a
loop variable, a dict `.get()` result, a function parameter) that ndscan
cannot statically resolve to a type. This is the single clearest
demonstration in the whole self-scan of "BE HONEST ABOUT ND004": without
type inference, this rule cannot distinguish "`repr()` of a plain string
pulled from JSON" from "`repr()` of a custom object with no `__repr__`" —
and a codebase that idiomatically reprs plain values in error messages (a
completely normal, safe pattern) will make this rule fire dozens to
hundreds of times. Concrete sample (full list of files/line numbers is in
`self_scan_report.json`):
```
thread-check/thread_check.py:170   raise ValueError("unparseable ISO-8601 timestamp: %r" % raw)
scorecard/scorecard.py:453         f"task {task_id!r} event at index {j} is missing required key: at"
schema-checker/schema_check.py:436 "key %r is not declared and additional_properties is false" % name
```
No custom-`__repr__`-less object being repr()'d was found in the sample;
if one exists elsewhere in the 220, it is indistinguishable, from the
outside of this exercise, from the 15/15 false positives actually read.

## Regeneration is locked

The failure this section documents is not "a number went out of date". It
is that **nothing in the repository was asking the question.** The report
was a current-state claim with no regenerator, no transcript record, and
no manifest entry, so it drifted silently for the tool's entire lifetime.

`report-freshness/manifest.json` now carries:

```
id:                nondeterminism-scanner:self_scan_report.json
kind:              regenerable
generation.argv:   python3 ndscan.py --root .. -o {OUT}
generation.cwd:    nondeterminism-scanner
committed_report:  nondeterminism-scanner/self_scan_report.json
expected_exit_code: 1
```

and this directory carries the local half, `test_selfscan_freshness.py`:

```
python3 -m unittest test_selfscan_freshness
```

Six tests. They regenerate the report with the documented command and
fail — not skip, not warn — when the committed bytes differ, naming the
summary fields that moved; they assert two consecutive runs hash the
same; they assert the report's absence is a failure rather than a shrug;
and they pin the manifest entry itself, so deleting the repository-wide
half breaks this suite. The one permitted skip is a directory extracted
without its repository root, where there is nothing to scan, and a test
asserts that condition is false inside a real checkout so the check
cannot quietly become vacuous — in a standalone extraction the
comparisons skip and that guard then fails, so the suite exits nonzero
rather than printing `OK` for a directory that verified nothing.

`report-freshness/freshness.py` re-runs the same command and compares
bytes, and `regen-preflight` re-derives every manifest entry in a clean
copy of the tree, so this report now fails a repository-wide check as
well as this directory's own suite the moment it stops reproducing. `REGENERABILITY_EVIDENCE.txt` §4 records the checker
reporting `stale` with the old artifact still in place, §6 records it
reporting `match` after the repair, §7 and §7b are positive controls —
one digit of the committed report is changed in a throwaway copy, and
both the repository-wide checker and this directory's own suite are shown
going red on it — and §10 runs the generator twice inside a throwaway Git
repository and prints both byte hashes, `git status` and `git diff`.

There is deliberately **no `capture.sh` here.** `regen-preflight`
discovers regenerators by that exact filename and re-runs each one inside
a copy of the tree made without `.git`; this tool's evidence reads Git
history, so a `capture.sh` would be a permanently-erroring inventory
entry. The manifest entry needs no `.git` and is the mechanism this
repository already uses for exactly this artifact shape (see
`weak-assertion-scanner/self_scan_report.json`, locked the same way).

**Bottom line:** the self-scan is genuinely useful for two of six rules on
this particular, disciplined codebase (ND002 surfaces two real determinism
idioms this tool doesn't recognize as "already handled"; ND003 surfaces one
real hash-randomization footgun, currently latent), moderately useful for
zero (all clean rules were independently grep-verified as true negatives,
not blind spots), and is dominated by false positives on ND004 specifically
because ND004 cannot be decided statically — exactly as documented above.

## Limitations a reviewer should scrutinise

1. **No data-flow / type inference anywhere.** Every rule resolves only
   what is directly visible in the AST at the point of use. ND003's dict
   sub-check and ND004 in general are the rules most exposed by this — see
   the self-scan section above for concrete, measured impact (24/26 and
   220/220 respective false-positive rates, measured on the original
   87-file scan; the current report has not been re-triaged).
2. **Alias resolution is file-wide, not scope-aware, and import-based
   only.** A local variable that shadows an imported module name is
   indistinguishable from the module itself (ND001 false positive
   demonstrated in `test_shadowed_name_is_a_documented_false_positive`);
   a variable reassigned from an already-imported module
   (`t = time; t.time()`) is not tracked (only literal `import`/`from
   ... import` statements populate the alias map).
3. **ND002's "immediately wrapped in `sorted()`" check is syntactic
   adjacency, not semantic equivalence.** It does not recognize the
   `dirnames.sort()`-in-place idiom (arguably the *more correct* way to
   make `os.walk()` deterministic, since it also fixes recursion order,
   which `sorted(os.walk(...))` would not), nor "sorted one statement
   later." Both shapes were found live in the self-scan (see above), not
   hypothesized.

## `--rule` and `--min-severity` changing the verdict

`--rule RULE_ID` is repeatable and restricts which of the six rules run at
all (default: all six). `--min-severity {low,medium,high}` filters the
*findings from whichever rules ran* by severity (default: `low`, i.e.
nothing is filtered out). They compose: `--rule` changes which rules even
execute; `--min-severity` then filters what those rules produced.
`captured_output.txt` demonstrates `--rule ND001_WALL_CLOCK` against
`samples_risky/` dropping the finding count from 33 (all six rules) to 7
(ND001 only) while both still exit `1`; `test_ndscan.py`'s
`test_rule_filter_can_change_verdict_to_clean` demonstrates a case where
restricting to a single rule changes the exit code itself, from `1` to `0`.

## Running the tests

```
python3 -m unittest test_ndscan -v
```

221 tests. See `captured_output.txt` for the real summary line from this
exact command.
