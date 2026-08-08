# snapdiff.py

A stdlib-only Python 3 CLI that diffs two exported task-node snapshots
taken at different times and reports exactly what changed: tasks
added, tasks removed, status transitions, reward changes, evidence
added, evidence removed, generic field changes, and summary changes —
as deterministic canonical JSON, with exit codes 0/1/2.

No third-party dependencies, no network access. Uses only: `argparse`,
`json`, `sys`, `decimal`.

## What we reused from queue-auditor

Per the task instructions, `/sessions/.../outputs/queue-auditor/` was
read first. `snapdiff.py` reuses, verbatim:

- **The top-level input shape**: a JSON object with a `"tasks"` array
  and a `"summary"` object (`queue_audit.py`'s `snapshot_clean.json` /
  `snapshot_dirty.json` shape). We did not invent a new envelope.
- **The task record fields** `task_id`, `title`, `status`, `list`,
  `reward`, `created_at`, `deadline` — the same names, same intent.
- **The canonical JSON contract**:
  `json.dumps(obj, sort_keys=True, separators=(",", ":"),
  ensure_ascii=True) + "\n"`, verified with the same
  `sha256sum`/`cmp` byte-identity technique in `captured_output.txt`.
- **The exit-code convention**: `0` = clean/no-difference, `1` =
  findings/differences (successfully computed), `2` = invalid
  input/usage.
- **The `-o`/`--output` flag** and the `queue_audit.py: error: ...`
  style of fatal-error message on stderr (adapted to
  `snapdiff.py: error: ...`).
- **The reward-precision fix**: `json.loads(text, parse_float=Decimal)`
  so a reward literal is built directly from source text into a
  `Decimal`, never through a 64-bit float. `queue_audit.py`'s own
  README documents finding and fixing exactly this bug; `snapdiff.py`
  starts from the fixed version rather than reintroducing it.

**What we added, since queue-auditor's schema has no notion of it**:
an optional `"evidence"` field on each task record — a JSON array of
arbitrary items (in our fixtures, `{"id": ..., "type": ...}` objects) —
because the task spec requires an `EVIDENCE_ADDED`/`EVIDENCE_REMOVED`
category that the reused schema doesn't define. Everything else about
the task shape is unchanged.

**Where snapdiff.py validates less than queue_audit.py, deliberately**:
`queue_audit.py`'s job is to enforce the *entire* snapshot schema
(every field, every type). `snapdiff.py`'s job is only to (a) safely
establish task identity via `task_id` and (b) safely parse money via
`reward` — then diff everything else generically as opaque JSON
values. A task record missing `title` entirely is not fatal to
`snapdiff.py`; it just diffs from `null` to whatever the other
snapshot has, surfaced as an ordinary `FIELD_CHANGED` entry. This is a
deliberate division of labor between the two sibling tools, not an
oversight — see Limitations #1 below.

## Usage

```
python3 snapdiff.py BEFORE.json AFTER.json [-o OUTPUT] [--ignore FIELD ...]
```

- `BEFORE`, `AFTER` — paths to two snapshot JSON files (both
  required; there is no stdin support — see Limitations #3).
- `-o OUTPUT`, `--output OUTPUT` — write the report to `OUTPUT`
  instead of stdout. Takes a single value, and giving it twice is a
  usage error (exit `2`) rather than a silent overwrite — see
  "Repeating a single-value flag" below.
- `--ignore FIELD` — exclude `FIELD` from comparison (repeatable).
  Recognized special names `status`, `reward`, `evidence`, `summary`
  disable their entire change category; any other name is excluded
  from generic `FIELD_CHANGED` comparison; a name that never appears
  anywhere is accepted as a harmless no-op (`task_id` is always a
  no-op — it's the identity key, never diffed).

## Exit codes

| Code | Meaning |
|------|---------|
| `0`  | The two snapshots are identical for diffing purposes — zero change entries. |
| `1`  | Both snapshots were read and parsed successfully; one or more changes were found. |
| `2`  | Invalid input or usage: missing/unreadable file, invalid JSON, malformed snapshot shape, duplicate `task_id`, invalid reward, or a CLI usage error — including argparse's own exit code for a usage error such as a missing argument, an unknown flag, or a repeated `-o`/`--output`. (`--help` is not a usage error: argparse prints the help text and exits `0`.) |

## Repeating a single-value flag

`-o`/`--output` takes one value. Repeating it is refused:

```
$ python3 snapdiff.py snapshot_before.json snapshot_after_changed.json -o A.json -o B.json
usage: snapdiff.py [-h] [-o OUTPUT] [--ignore FIELD] before after
snapdiff.py: error: -o/--output accepts a single value but was given twice: -o "A.json", then -o "B.json". Pass it once -- repeating it would silently discard "A.json".
```

Exit code `2`, and **neither** file is created — the refusal happens
during argument parsing, before any diffing or writing.

Until this guard existed, argparse's default store action was
last-one-wins: that same command exited `1`, wrote `B.json`, never
created `A.json`, and printed nothing at all on stderr. A caller who
believed the flag accumulated got exactly one of the two reports they
asked for and no indication which one had been dropped — the worst
shape a failure can take, because the run looks successful. The
before/after runs are recorded in `SINGLE_USE_FLAG_EVIDENCE.txt`,
against the pre-fix source read straight out of Git.

Four decisions inside that guard are worth stating, because each could
reasonably have gone the other way:

1. **Repetition is refused even when the two values are equal.**
   `-o x -o x` expresses the same misunderstanding as `-o x -o y`, and
   a rule with an "unless they happen to match" exception is harder to
   rely on than one without.
2. **The message names the spelling argparse resolved for each
   occurrence**, so `--output A.json` followed by `-o B.json` reports
   `--output` then `-o` rather than one canonical form for both. The
   one place that is not the caller's literal text is an abbreviated
   long option: argparse expands `--outp` to `--output` before any
   action runs, so `--outp x --outp y` is reported as `--output` twice.
   `test_an_abbreviated_long_option_is_reported_by_its_full_spelling`
   pins that, so it is a documented limit rather than a surprise.
   Values are quoted with `json.dumps(..., ensure_ascii=False)`, not
   `%r` — both delimit and escape, JSON is the language this tool
   already speaks, and `ensure_ascii=False` keeps a non-ASCII path
   legible in a message whose job is to let the caller recognise their
   own argument. It also keeps nondeterminism-scanner's committed
   self-scan byte-identical, since that scanner flags `%r`
   syntactically as `ND004` and this change's brief puts its directory
   off limits. That is a real but modest reason, and it is worth being
   precise about: `ND004` aims at `repr()` falling back to
   `object.__repr__`, nondeterminism-scanner's own README names `%r`
   applied to an argparse string as its dominant false positive, and
   this module already carries four older `%r` sites of exactly that
   kind, left untouched here. Nothing about `json.dumps` is a verdict
   on those.
3. **`--ignore` is untouched and stays repeatable.** It is declared
   with `action="append"`, and repeating it is the documented way to
   exclude several fields at once.
4. **The rule is enforced structurally, not per-option.** Every
   optional in the parser must be either repeatable by declaration or
   built on the `SingleUse` action;
   `test_every_optional_action_is_repeatable_or_single_use` walks the
   parser and asserts it. A future option added with a bare store
   action fails that test rather than quietly reintroducing
   last-one-wins.

A fifth decision sits inside the action rather than in its behaviour.
"Has this option already been given?" is recorded on the *namespace*
being filled, under a private attribute named after the destination.
Two shorter answers were tried and rejected, each for a reason that
showed up as a real failure:

- Reading the destination back and testing it for `None` breaks
  whenever something put a value there first.
  `parser.set_defaults(output=...)` and `parse_args(argv, namespace=…)`
  both do, and the probe then reads that preset as a first occurrence,
  refuses the caller's only real one, and prints the word `None` where
  an option spelling belongs.
- Keeping the marker on the *action* breaks harder. argparse actions
  belong to a parser, not to a parse, so two threads parsing through
  one parser overwrite each other's marker: `-o A -o B` comes back
  silently accepted — the exact defect this guard exists to remove —
  and refusals quote another caller's argument. Reusing one namespace
  object across two parses fails the same way in a single thread, by
  refusing a second parse that only ever had one `-o` on it.

A namespace is per-parse and per-thread by construction, so a marker on
it is right in all of those cases. The cost is that the marker would
otherwise be visible in the namespace `parse_args` hands back;
`strip_single_use_markers()` deletes it, and `run()` calls it.
`build_parser()` deliberately returns a plain `ArgumentParser` rather
than a subclass: doc-validator's CLI discovery looks for a variable
assigned from a call literally named `ArgumentParser`, and a subclass
would take every option in this file out of its cross-tool report.
`SingleUse.MARKER_PREFIX` and `marker_attribute()` are
public so a caller can strip it. Twelve tests pin this, including one
that runs 960 concurrent parses through a single shared parser and
asserts that not one repeat slipped through.

The usage line is unchanged by all of this — `SingleUse` swaps the
action, it does not add an option — and
`test_usage_line_is_unchanged_by_the_guard` pins that, comparing the
line with its whitespace collapsed so the suite does not pass or fail
on the terminal width it was run at.
`test_the_usage_line_is_pinned_at_a_narrow_terminal_too` runs the same
check at `COLUMNS=40`, where argparse really does wrap, and both
regenerators export `COLUMNS=80` so the committed transcripts cannot
move for that reason either.

### What this change leaves stale elsewhere

Four repository-wide artifacts record numbers this change moves. Two
are left stale because their directories are off limits under this
change's brief; two could not be left stale, because the tools that own
them compare a committed report against a live rescan in their own test
suites, and were therefore updated. All four, rather than leaving any
of them to be discovered:

- **The root `README.md` tool table** still says `snapshot-diff | 222`,
  and `readme-index/corpus.tsv` still carries the pre-change extraction
  of this README. Running `readme-index` against the live tree
  (`python3 readmeindex.py --root .. --root-readme ../README.md`)
  therefore reports one more `count_differs` than before — a fifth,
  alongside four that already exist for `env-leak-scanner`,
  `path-collision-scanner`, `wallet-reconciler` and `xrpl-auditor`.
  Editing the root README alone does **not** fix this: the root README
  and `readme-index/root_readme_after.md` are byte-identical by design
  and the corpus is the reference for both, so a lone root-README edit
  trades one discrepancy for another (demonstrated in
  `SINGLE_USE_FLAG_EVIDENCE.txt`). The whole repair is one commit owned
  by `readme-index`: re-extract this README's rows into `corpus.tsv`,
  regenerate `root_readme_after.md`, copy it to the root `README.md`.
- **`shebang-mode`'s self-scan** counts tracked files, and this change
  adds three (`SINGLE_USE_FLAG_EVIDENCE.txt` and the two regenerators),
  so `589` becomes `592` in `shebang-mode/selfscan_output.txt` and
  `shebang-mode/README.md`. The `SM002` total is unaffected and stays
  at 150 — the two new `.sh` files carry no shebang precisely because
  nothing here can be committed with an executable bit, which is the
  condition `SM002` reports. `cd shebang-mode && bash selfscan.sh`
  rewrites `selfscan_output.txt`; the three places
  `shebang-mode/README.md` quotes `589` are prose and need editing by
  hand alongside it. (Left stale here: off limits.)
- **`doc-validator/option_report.json`** could not be left stale:
  `test_optioncheck.py` regenerates it and compares, so it is updated
  here, together with the one line of `doc-validator/README.md` that
  quotes its totals (`usages: 193 → 192`,
  `unsupported_dynamic: 6 → 9`). `optioncheck.py` resolves only literal
  `action="..."` strings, so a custom action class necessarily lands in
  its `unsupported_dynamic` list; that is a limit of the scan, not a
  defect here, and `-o`/`--output` still exists and still takes a
  value. Three of the nine are this change: the real one in
  `snapdiff.py`, plus two toy parsers in the tests that exercise
  `SingleUse` away from this tool. The other construction check builds
  the action directly rather than through `add_argument`, to keep a
  fourth out of that list. `weak-assertion-scanner` is updated for the
  same forced reason — its `test_weakassert_regen.py` compares its
  committed `tests_scanned` against a live scan.

## Change categories

Every entry in the `"changes"` array has a `"type"` field, one of:

| Type | Fields | Fires when |
|------|--------|------------|
| `TASK_ADDED` | `task_id`, `task` (full record, jsonified) | A `task_id` is present in AFTER but not BEFORE. |
| `TASK_REMOVED` | `task_id`, `task` | A `task_id` is present in BEFORE but not AFTER. |
| `STATUS_TRANSITION` | `task_id`, `from`, `to` | A shared task's `status` field differs (missing counts as `null`). |
| `REWARD_CHANGED` | `task_id`, `from`, `to`, `delta` | A shared task's parsed reward `Decimal` differs. `from`/`to` are strings or `null`; `delta` is a signed string or `null` if either side has no reward. |
| `EVIDENCE_ADDED` | `task_id`, `items` | Evidence items present in AFTER but not BEFORE (set difference by item content). |
| `EVIDENCE_REMOVED` | `task_id`, `items` | Evidence items present in BEFORE but not AFTER. |
| `FIELD_CHANGED` | `task_id`, `field`, `from`, `to` | Any other tracked scalar/structural field differs (missing counts as `null`). |
| `SUMMARY_CHANGED` | `task_id: null`, `from`, `to` | The top-level `"summary"` object differs (as a whole). |

Top-level report shape:

```json
{"change_count":0,"changes":[],"ignored_fields":[],"result":"identical","task_count_after":0,"task_count_before":0}
```

### Canonical JSON contract

`json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n"`
— sorted keys, no extraneous whitespace, non-ASCII escaped, exactly
one trailing newline. **No runtime-dependent fields anywhere**: no
wall-clock timestamps, no hostnames, no absolute paths, no
set/dict-ordering leakage. The report describes the diff, not when it
was computed. Running the tool twice on the same two inputs produces
byte-identical output — verified with `sha256sum`/`cmp` in
`captured_output.txt`.

### Deterministic total ordering

`sort_keys=True` only orders **dict keys**, never list items. The
`"changes"` list is explicitly sorted by
`(type, task_id or "", canonical_json_of_the_entry_itself)`. The third
key is a canonical dump of the entry's own content, which is a total
order over that content — so two distinct entries can never tie and
swap between runs; only byte-identical entries could "tie", and those
are indistinguishable in the output anyway. `EVIDENCE_ADDED`/
`EVIDENCE_REMOVED` items within one entry are similarly sorted by the
canonical JSON of each item.

## MONEY: Decimal, parse_float, and the precision demonstration

`json.loads(text, parse_float=Decimal, parse_constant=<reject>)` is
used for **both** input files. Every JSON number literal with a
decimal point or exponent is built directly from its source text into
a `Decimal` — it never becomes a 64-bit `float` at any point. `NaN`,
`Infinity`, `-Infinity` bare tokens are rejected outright via
`parse_constant` (reported as `INVALID_REWARD` — see Limitations #2
for why that specific code, even for non-reward fields).

A `reward` given as a JSON string (e.g. `"42"`) is parsed straight
from that string via `Decimal("42")` — never via `Decimal(str(x))` on
an already-parsed value, which is the exact idiom the task spec warns
about (`Decimal(str(x))` on a float-parsed number just makes an exact
`Decimal` copy of an already-corrupted number).

**Precision demonstration** (`TASK-PRECISION` in the fixtures): reward
changes from `123456789012345678.123456789` to
`123456789012345678.123456790` (a difference in the 9th fractional
digit of a 27-significant-digit number). This is **detected** as a
`REWARD_CHANGED` entry with `"delta":"+0.000000001"`. A float-based
diff would not detect this at all —
`float("123456789012345678.123456789") ==
float("123456789012345678.123456790")` is `True` in Python (both
round to the same nearest float64 value); `test_snapdiff.py`'s
`test_precision_demo_would_be_invisible_to_float_diff` pins this down
directly.

`decimal.getcontext().prec` is explicitly raised to `80` at module
load (default is 28 significant digits) as a defensive margin for
very large/high-precision reward values — not strictly required by
the fixtures shipped here, but cheap insurance against a
context-precision rounding surprise on real data with more digits.

## Error codes (all fatal, exit 2)

| Code | Fires when |
|------|------------|
| `MALFORMED_SNAPSHOT` | Top-level document isn't a JSON object; `tasks` missing/not an array; `summary` present but not an object; a task record isn't a JSON object; a task record has a missing/null/non-string/empty `task_id`; a task's `evidence` field is present, non-null, and not an array. |
| `DUPLICATE_TASK_ID` | The same `task_id` appears more than once **within one snapshot** (before or after). See decision below. |
| `INVALID_REWARD` | A `reward` value is boolean, array, object, an unparseable string, NaN, or infinite — or the document contains a bare `NaN`/`Infinity`/`-Infinity` JSON literal anywhere. |

### DUPLICATE_TASK_ID: exit 2, not a reported finding

**Decision: fatal (exit 2), in either snapshot.** This is a deliberate
divergence from `queue_audit.py`, which treats a duplicate `task_id`
as a non-fatal finding (exit 1) — and that's the right call *for an
auditor*, which validates each record independently and doesn't need
cross-record identity for anything.

`snapdiff.py`'s entire job, by contrast, is built on task identity:
every category above (`STATUS_TRANSITION`, `REWARD_CHANGED`,
`EVIDENCE_ADDED/REMOVED`, `FIELD_CHANGED`) is computed by pairing "the
task with this `task_id` in BEFORE" against "the task with this
`task_id` in AFTER". If a `task_id` appears twice in one snapshot,
that pairing is **ambiguous** — picking one occurrence (e.g.
last-wins, as a naive `dict` build would silently do) can produce a
diff report that looks completely plausible but is quietly wrong: a
real status transition could vanish, or worse, a real reward change
(this is a *money* tool) could be masked because the "wrong" duplicate
was compared. An audit tool can afford to report an issue and move on;
a diff tool whose output might drive downstream payment logic cannot
afford to guess. So `snapdiff.py` refuses outright rather than
producing a diff it can't vouch for.

## Fixtures

- `snapshot_before.json` / `snapshot_after_same.json` — 12 tasks each,
  semantically identical values but deliberately different bytes
  (different key order, different whitespace, and — as an explicit
  proof that representation doesn't matter — `TASK-REWARD-TYPEEQ`'s
  reward is the JSON number `42` in `snapshot_before.json` but the
  JSON string `"42"` in `snapshot_after_same.json`). Diffing this pair
  produces **zero** changes and exit code `0`.
- `snapshot_after_changed.json` — same 12-task baseline, modified to
  exercise **every** change category at least once (11 total change
  entries; see captured_output.txt for the full list). Exit code `1`.

## Limitations / judgment calls a reviewer should scrutinize

1. **Lighter structural validation than `queue_audit.py`, by design.**
   `snapdiff.py` only requires a valid `task_id` and a parseable
   `reward`; every other field (`title`, `status`, `list`,
   `created_at`, `deadline`, or any custom field) is optional and
   diffed generically. A task missing `status` entirely doesn't error
   — it just diffs `null -> "outstanding"` as a `STATUS_TRANSITION`.
   This is intentional division of labor with `queue_audit.py` (see
   above), but a reviewer expecting `snapdiff.py` to also reject
   malformed *non-identity* fields will be surprised.
2. **`INVALID_REWARD` is reused for any non-finite numeric literal
   anywhere in the document**, not just inside a `reward` field. The
   task spec defines exactly three error codes and doesn't provide a
   fourth for "NaN/Infinity found somewhere that isn't reward" — since
   `reward` is the only numeric field this schema defines, reusing
   `INVALID_REWARD` for a stray `NaN` in, say, a custom field is a
   defensible but debatable choice.
3. **No stdin support.** Unlike `queue_audit.py` (which takes one
   input and can unambiguously read `-` from stdin), `snapdiff.py`
   takes *two* positional file arguments; allowing `-` for either
   creates an ambiguous "what if both are `-`" case. This tool only
   accepts real file paths for both `BEFORE` and `AFTER`.
4. **Evidence is compared as a set of distinct items, not a
   multiset.** Identity is the full canonical JSON of each evidence
   item (not just its `id`), so reordering never counts as a change,
   and an item whose `type` changed for the same `id` is reported as
   one `EVIDENCE_REMOVED` (old) plus one `EVIDENCE_ADDED` (new) rather
   than a dedicated "evidence changed" category (the spec doesn't
   define one). But a genuine *duplicate* evidence item (same content
   appearing twice) collapses to a single set member — if one copy of
   a duplicate disappears while another remains, that is **not**
   reported. `test_evidence_duplicate_item_collapses_as_set` pins this
   down explicitly.
5. **Missing key and explicit `null` are treated as equivalent** for
   every diffed field (`reward`, `status`, and every generic field).
   A consumer that wants to distinguish "field was never set" from
   "field was explicitly cleared to `null`" will not get that
   distinction from `snapdiff.py`.
6. **`REWARD_CHANGED`'s `delta` is `null`, not an assumed baseline,
   when either side has no reward.** A task going from "no reward" to
   `50` is reported as `{"from":null,"to":"50","delta":null}` rather
   than treating the missing side as `0` and computing `delta:"+50"`.
   This avoids silently implying "not yet assigned" means "zero", but
   a reviewer might reasonably want the opposite convention.

## Bug found during development

While writing `test_task_added` / `test_task_removed`, a real
money-formatting inconsistency turned up: the `TASK_ADDED`/
`TASK_REMOVED` entries' embedded `"task"` snapshot was built by
`jsonify()`-ing the raw record directly, and `jsonify()` only converts
`Decimal` instances to strings. A `reward` that happened to be a plain
JSON *integer* in the source document (e.g. `"reward": 5`) stayed a
bare JSON integer in the `TASK_ADDED` entry's embedded `task` object,
while every `REWARD_CHANGED` entry correctly rendered `reward` as a
string via `decimal_str()`. That's a real violation of the "amounts
are always emitted as strings" contract — one code path
(`REWARD_CHANGED`) obeyed it and another (`TASK_ADDED`/`TASK_REMOVED`)
didn't, and a downstream consumer parsing this tool's own output would
have to handle `reward` as *either* a JSON number *or* a JSON string
depending on which change type it was reading, defeating the entire
point of "emit amounts as strings so a re-parser never has to worry
about float round-tripping."

**The fix:** added `_normalized_task_snapshot()`, which builds the
embedded `task` object via the same `jsonify()` call but then
overwrites the `"reward"` key (only if the original record had one at
all) with `decimal_str(reward)` / `None`, using the already-parsed
`Decimal` from `validate_and_index_tasks()` rather than re-deriving it
from the raw JSON value. `test_task_added` and `test_task_removed`
assert `task["reward"]` is a string; the exact command sequence in
`captured_output.txt` (`TASK-ADDED-ONE` / `TASK-REMOVED-ONE` in
`r1.json`) shows `"reward":"5"` and `"reward":"15"` as strings,
confirming the fix end-to-end. A second bug, `format_signed_delta`
originally rendering the precision-demo delta as `"+1E-9"` (scientific
notation, via plain `str(Decimal(...))`) instead of `"+0.000000001"`,
was caught the same way and fixed with `decimal_str()`'s explicit
`format(value, "f")`; both are exact but `"1E-9"` is a much easier
value to misread in a money diff, so fixed-point was made the only
output shape.

## Reversed-diff inverse property

Diffing `AFTER, BEFORE` (arguments swapped) is verified — not assumed —
to produce the exact structural inverse of `BEFORE, AFTER`:
`TASK_ADDED` <-> `TASK_REMOVED` and `EVIDENCE_ADDED` <->
`EVIDENCE_REMOVED` (same task/items content, swapped category), and
`STATUS_TRANSITION` / `REWARD_CHANGED` / `FIELD_CHANGED` /
`SUMMARY_CHANGED` each get `from`/`to` swapped with `REWARD_CHANGED`'s
`delta` exactly negated (`Decimal` negation is exact, no rounding).
This holds for every entry in `captured_output.txt`'s `rev.json` run —
see `test_reversed_diff_is_exact_inverse` for the automated version.
The only thing that does **not** match 1:1 is the *list position* of
entries, because the sort key's first component is `type`, and
`TASK_ADDED`/`TASK_REMOVED` (and `EVIDENCE_ADDED`/`EVIDENCE_REMOVED`)
swap type names between the two runs, which can move an entry to a
different place in the sorted list. Content-wise, per-entry, the
inverse is exact.

## Tests

```
python3 -m unittest test_snapdiff -v
```

261 tests: canonical JSON formatting (10), `jsonify`
(10), `decimal_str`/`format_signed_delta` (12), `parse_reward_field`
data-driven valid/invalid cases (32 + 4 extra),
`parse_json_document` (parse_float=Decimal, NaN/Infinity rejection,
precision preservation vs. plain `json.loads`, 10 cases),
`validate_shape` (11), `validate_and_index_tasks` (21, including
duplicate detection, evidence type checks, unicode titles),
`diff_documents` per change category -- `TASK_ADDED`/`TASK_REMOVED` (7),
`STATUS_TRANSITION` (5), `REWARD_CHANGED` (14, including the precision
demonstration and the string-vs-number-same-value non-change),
`EVIDENCE_ADDED`/`EVIDENCE_REMOVED` (11, including reordering,
duplicate-collapse, and non-dict items), `FIELD_CHANGED` (12,
including unicode and missing-vs-null), `SUMMARY_CHANGED` (6) --
cross-cutting `--ignore` mechanics (4), determinism/sort-tiebreak (5),
`build_report` (4), the single-use guard (39, below), and full CLI
subprocess integration: both required
fixtures end-to-end, `-o`/`--output`, byte-identical repeated runs
with `sha256sum`, no-absolute-paths-leak check, `--ignore` reducing
(not necessarily zeroing) the change count, the reversed-diff exact-
inverse property, and every fatal-error path (nonexistent file,
missing args, `--help`, invalid JSON, malformed shape, duplicate
`task_id` in either snapshot, invalid reward in several shapes,
evidence wrong type, unwritable `-o` target) plus edge cases (both
snapshots empty, a file diffed against itself, null reward, unicode
titles, `--ignore` for a field that never appears, duplicate `--ignore`
flags deduplicated in the report).

The 39 that cover the single-use guard are the two classes
`TestSingleUseOutputOption` and `TestSingleUseAction`. In an
alphabetically ordered `-v` listing they are the 20th and 21st of 23
classes, near the end, with `TestValidateAndIndexTasks` and
`TestValidateShape` after them.

Sixteen drive the CLI as a subprocess. Three of those cover every
spelling argparse accepts for a repeated `-o`/`--output` written out in
full (short/short, long/long, either mixed order, `--output=`, attached
`-oVALUE`, and the two occurrences separated by an unrelated flag),
each asserting exit `2`, that the option is named, and that *both*
target files are left uncreated. The other thirteen: the message naming
both spellings in the order used; the message naming the value that
would have been discarded; three occurrences refused at the second;
equal values refused; stdout left empty; the exit code being the one
the exit-code table documents; and, on the other side, single `-o`,
single `--output`, no flag at all, `--ignore` repeated three times,
`--ignore` repeated alongside a single `-o`, and the usage line at a
normal and at a 40-column terminal, all still working.

Twenty-three inspect the parser and the action directly: which action
each optional is built on; the structural rule that every optional is
repeatable-by-declaration or `SingleUse`; that the strip helper leaves only the
declared destinations, with and without the option present, and after
`parse_known_args` as well as `parse_args`; that until the helper runs
the per-parse marker is there, under a name the action will tell you;
that `build_parser()` returns a plain `ArgumentParser` and not a
subclass; that a reused parser both accepts a fresh single occurrence
and still refuses a repeat; that one namespace object reused
across two parses is not mistaken for a repeat, including
`parse_known_args` followed by `parse_args`; that one parser shared
across eight threads never silently accepts a repeat, never quotes
another thread's argument, and never loses a legitimate single value
across 960 concurrent parses; that a reused namespace which was not
stripped fails loudly rather than silently; that a value already in the namespace
(from `set_defaults` or a `namespace=` argument) is not an occurrence
and never appears in the message; that an option with a non-`None`
default still refuses repeats; that `nargs=0` is rejected at
construction; that an abbreviated `--outp` is reported by the full
spelling argparse resolved it to; that a non-ASCII value stays readable
in the message; and that the action works on a parser that has nothing
to do with this tool.

Thirty-two of the 39 fail against the pre-fix source; the other seven
pin behaviour it already had and pass on both sides, which is what
makes them useful as regression guards.
`SINGLE_USE_FLAG_EVIDENCE.txt` records the per-test verdict for all
thirty-nine, not only the aggregate — the aggregate is inflated by `subTest`
expansion (three methods times seven spellings) and on its own would
read as a larger claim than the facts support.
