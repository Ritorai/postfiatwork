# queue_audit.py

A stdlib-only Python 3 CLI that audits PFTL task-queue snapshots for
structural and consistency problems: duplicate task IDs, malformed
records, status/list mismatches, invalid rewards, invalid or non-UTC
timestamps, deadlines before creation, and summary counts that disagree
with the actual per-bucket task counts.

No third-party dependencies, no network access. Uses only: `argparse`,
`json`, `sys`, `os`, `datetime`, `decimal`, `math`.

## Usage

```
python3 queue_audit.py <input.json | ->  [-o OUTPUT]
```

- `<input.json>` — path to a snapshot JSON file.
- `-` — read the snapshot from stdin instead of a file.
- `-o OUTPUT`, `--output OUTPUT` — write the report to `OUTPUT` instead
  of stdout.

Examples:

```
python3 queue_audit.py snapshot.json
cat snapshot.json | python3 queue_audit.py -
python3 queue_audit.py snapshot.json -o report.json
```

## Exit codes

| Code | Meaning |
|------|---------|
| `0`  | Clean snapshot — no findings. |
| `1`  | Snapshot was read and parsed successfully; one or more findings were reported. |
| `2`  | Input could not be read or parsed (missing file, invalid JSON, wrong top-level shape) or a usage error occurred (e.g. missing arguments — this is also argparse's own exit code for `--help`/usage errors, so the contract lines up naturally). |

## Input shape

A snapshot is a JSON object with two top-level fields:

```json
{
  "tasks": [ { ... }, { ... } ],
  "summary": { "outstanding": 2, "rewarded": 1 }
}
```

`tasks` (required, must be an array) — each element is expected to be an
object with these required fields:

| Field        | Type                        | Notes |
|--------------|------------------------------|-------|
| `task_id`    | non-empty string             | Unique identifier for the task. |
| `title`      | non-empty string              | Human-readable title. Any Unicode is fine. |
| `status`     | non-empty string              | Current status, e.g. `"outstanding"`, `"rewarded"`, `"expired"`. |
| `list`       | non-empty string              | Which bucket the task currently appears in. Expected to equal `status`. |
| `reward`     | JSON number, >= 0             | The task's reward. Parsed as `Decimal`, never `float`. |
| `created_at` | string, ISO-8601, explicit UTC | e.g. `"2026-01-01T00:00:00Z"` or `"...+00:00"`. |
| `deadline`   | string, ISO-8601, explicit UTC | Same format as `created_at`. |

`summary` (optional, defaults to `{}` if absent — must be an object if
present) — maps a bucket name (any string used as a `list` value by some
task) to the expected count of tasks in that bucket. Buckets that appear
in the tasks array but not in `summary` are treated as having an
expected count of `0` (and vice versa), which surfaces as a
`SUMMARY_COUNT_MISMATCH` finding if the actual count is nonzero.

A task record that isn't a JSON object at all (e.g. `null`, a bare
string, a number) is reported as a single `MALFORMED_RECORD` finding
using a synthetic identifier `<index:N>` (N = its position in the
`tasks` array), since it has no usable `task_id`.

## Output shape

Canonical JSON, single object, on stdout (or the `-o` file):

```json
{"finding_count":0,"findings":[],"result":"clean","task_count":6}
```

- `result` — `"clean"` or `"findings"`.
- `finding_count` — `len(findings)`.
- `task_count` — number of elements in the input `tasks` array (including malformed ones).
- `findings` — array of finding objects, sorted by `(code, task_id, detail)` for a stable, deterministic order. Each finding is:

```json
{"code": "INVALID_REWARD", "task_id": "NEG-REWARD", "detail": "reward is negative (-5)"}
```

For findings that aren't tied to one specific task (currently only
`SUMMARY_COUNT_MISMATCH`), `task_id` holds the bucket name instead of a
task identifier, so the deterministic sort key still makes sense.

### Canonical JSON contract

Output is produced with:

```python
json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n"
```

i.e. sorted keys, no extraneous whitespace, all non-ASCII characters
`\uXXXX`-escaped, and exactly one trailing newline. There are no
runtime-dependent fields anywhere in the output: no wall-clock
timestamps, no hostnames, no absolute paths, no field whose value
depends on set/dict iteration order. Running the tool twice on the same
input byte-for-byte reproduces the same output — see
`test_two_runs_byte_identical` and the `sha256sum`/`cmp` steps in
`captured_output.txt`.

## Finding codes

| Code | Fires when |
|------|------------|
| `DUPLICATE_TASK_ID` | The same (valid, non-empty string) `task_id` appears on 2+ task records. One finding per duplicated ID (not per extra occurrence), with the occurrence count in `detail`. |
| `MALFORMED_RECORD` | A task record isn't a JSON object, or is missing a required field, or a required field (other than `reward`) has the wrong type / is `null` / is an empty string. |
| `STATUS_LIST_MISMATCH` | Both `status` and `list` are present and valid strings, but `status != list`. |
| `INVALID_REWARD` | `reward` is present but is negative, non-numeric (string/bool/array/object), or NaN/infinite. (A *missing* `reward` is `MALFORMED_RECORD` instead — see Design notes.) |
| `INVALID_TIMESTAMP` | `created_at` or `deadline` is a string but doesn't parse as an ISO-8601 timestamp with an explicit, zero UTC offset (naive timestamps and non-zero offsets are both rejected). |
| `DEADLINE_BEFORE_CREATED` | Both `created_at` and `deadline` parse as valid UTC timestamps, and `deadline < created_at`. Equal timestamps are not flagged. |
| `SUMMARY_COUNT_MISMATCH` | For some bucket name (from `summary` keys and/or `list` values seen in `tasks`), the `summary` count doesn't equal the actual number of tasks whose `list` equals that bucket. Also fires if a `summary` value isn't numeric (string/null/bool/etc). |

## Design notes / decisions worth knowing about

- **Reward parsing uses `parse_float=Decimal`.** `queue_audit.py` calls
  `json.loads(text, parse_float=Decimal)` so that any JSON number
  literal with a decimal point or exponent is built directly from the
  source text into a `Decimal`, and never touches a 64-bit `float` as an
  intermediate representation. See "Bug found" below for why this
  matters.
- **`reward` missing vs. `reward` wrong-typed are different codes.** A
  record with no `reward` key (or `reward: null`) is `MALFORMED_RECORD`
  ("missing required field"). A record with a `reward` key present but
  holding a string, boolean, array, object, or NaN/Infinity is
  `INVALID_REWARD` ("non-numeric"/"NaN"/"infinite"). This split follows
  the task spec's wording (`INVALID_REWARD` explicitly lists
  "non-numeric" as one of its triggers) but is a judgment call — see
  Limitations below.
- **`NaN`/`Infinity`/`-Infinity` are not rejected at the JSON-parsing
  stage.** Python's `json` module accepts these bare tokens by default
  (via `parse_constant`) and returns `float('nan')`/`float('inf')`. This
  tool does *not* override `parse_constant` to raise, because doing so
  would make a single NaN reward abort the entire audit with exit code 2
  instead of surfacing as an `INVALID_REWARD` finding (exit code 1) —
  and the task spec lists NaN/Infinity as an `INVALID_REWARD` trigger,
  not a fatal-input trigger. Instead, `validate_reward()` explicitly
  checks `math.isnan`/`math.isinf` (and the `Decimal` equivalents) and
  turns them into findings. This is what "reject NaN/Infinity explicitly
  (`json.loads` accepts them by default — you must guard)" is
  implemented as here.
- **Duplicate detection only tracks valid string `task_id`s.** A record
  with a missing/malformed `task_id` gets its own `MALFORMED_RECORD`
  finding and a synthetic `<index:N>` identifier; it's not considered
  for `DUPLICATE_TASK_ID` purposes since there's no real ID to compare.
- **A malformed record can still count toward its bucket.** If a task's
  `list` field is valid but some other field (e.g. `title`) is missing,
  it still contributes to that bucket's actual count for
  `SUMMARY_COUNT_MISMATCH` purposes. Only records whose `list` field
  itself is invalid/missing are excluded from bucket counting.
- **Unknown bucket names are not a special case.** There's no hardcoded
  enum of valid `list`/`status` values. If a task uses a bucket name
  that doesn't appear in `summary`, that's just another bucket with an
  implied expected count of `0`, and it surfaces naturally as
  `SUMMARY_COUNT_MISMATCH` — this is how the tool detects "a task whose
  list value isn't a known bucket" without needing a bucket whitelist.
- **Top-level shape errors (not a JSON object, missing/wrong-typed
  `tasks`, wrong-typed `summary`) are fatal (exit 2)**, not findings —
  the tool can't meaningfully audit a document that isn't shaped like a
  snapshot at all. A *missing* `summary` key, however, is treated as
  `{}` (soft-fail into findings, exit 1), since a queue snapshot missing
  its summary block is still auditable and arguably more interesting to
  report on than to hard-reject.

## Bug found during development

While writing the reward-precision regression tests
(`TestRewardPrecisionRegression` in `test_queue_audit.py`), I found and
fixed a real precision-loss bug in the reward-handling code:

**The bug:** the original implementation called plain `json.loads(text)`
(no `parse_float` override) and then converted the parsed `reward` value
to `Decimal` via `Decimal(str(raw))` inside `validate_reward()`. But by
the time `validate_reward()` ever sees the value, `json.loads` has
already parsed it into a 64-bit binary `float`, silently rounding any
literal with more precision than `float64` can represent. Converting
*that* float to a string and then to `Decimal` does not recover the
original precision — it just makes an exact `Decimal` copy of an
already-corrupted number, with no finding raised at all.

Concretely: a reward literal of `123456789012345678.123456` in the
source JSON was silently turned into `123456789012345680` (losing the
entire fractional part *and* rounding the integer part) with zero
findings — directly violating the "Use Decimal for reward arithmetic,
never float" requirement, since float was being used as an unavoidable
intermediate step during parsing. A related manifestation: an extremely
small negative reward like `-1e-400` underflows to `-0.0` as a `float`,
and `-0.0 < 0` is `False` in Python, so the old code would have silently
accepted a negative reward as valid.

**The fix:** `run()` now calls
`json.loads(text, parse_float=Decimal)`, so any JSON number literal with
a decimal point or exponent is built directly into a `Decimal` from the
original source text and never touches `float`. `validate_reward()` was
updated to handle `Decimal` as the normal numeric path, and now
explicitly rejects any *bare finite* `float` that reaches it (since,
under the fixed parsing contract, that should only happen if some future
caller forgets to pass `parse_float=Decimal` — better to fail loudly
than silently reintroduce the precision bug). Plain integers are
unaffected (`json`'s default integer parsing is already exact/arbitrary
precision).

Regression tests: `test_high_precision_reward_preserved_exactly_via_parse_float_decimal`,
`test_high_precision_reward_would_have_been_corrupted_by_plain_float_parsing`,
`test_tiny_negative_reward_flagged_via_decimal_no_underflow`,
`test_tiny_negative_float_would_have_underflowed_to_negative_zero`, and
`test_cli_end_to_end_preserves_high_precision_reward` (all in
`test_queue_audit.py`) pin this down, including an end-to-end CLI check.

## Limitations / false-positive risks

A reviewer should scrutinize these three judgment calls:

1. **`title` is required to be a non-empty string.** The task spec
   doesn't explicitly say titles can't be empty. If some real PFTL
   producer legitimately emits `title: ""` for a placeholder task, this
   tool will flag it as `MALFORMED_RECORD`, which may be a false
   positive depending on the real system's conventions.
2. **`STATUS_LIST_MISMATCH` requires exact string equality between
   `status` and `list`.** The task spec's example (`status: "rewarded"`,
   `list: "outstanding"`) suggests these two fields are supposed to
   track the same bucket, but if the real system ever has a legitimate
   status→list mapping that *isn't* identity (e.g. status `"paid"` maps
   to list `"rewarded"`), this tool will produce a false positive on
   every such task. There's no such mapping defined anywhere in the
   spec, so identity was the only defensible default, but it's worth
   confirming against real snapshot data.
3. **A missing `reward` key is `MALFORMED_RECORD`, but a wrong-typed
   `reward` (string/bool/array/object) is `INVALID_REWARD`.** This
   split is a reasonable reading of the spec (which lists "non-numeric"
   under `INVALID_REWARD`), but a reviewer might instead expect *all*
   reward problems, including a missing key, to land under the single
   `INVALID_REWARD` code, or conversely expect wrong-typed rewards to be
   `MALFORMED_RECORD` like every other wrong-typed field. Either
   convention is defensible; this tool picked one and documents it here
   so it isn't a surprise.

## Fixtures

- `snapshot_clean.json` — 6 valid tasks across 4 buckets
  (`outstanding`, `in_progress`, `rewarded`, `expired`), summary counts
  match exactly, one title includes non-ASCII characters. Produces exit
  code `0` with zero findings.
- `snapshot_dirty.json` — 16 task records engineered to trigger every
  one of the 7 finding codes at least once (a duplicate pair, a `null`
  record, a record missing `task_id`, a record missing `title`, a
  record with a wrong-typed `status`, a status/list mismatch, a
  negative reward, a string reward, a NaN reward, a boolean reward, an
  unparseable timestamp, a non-UTC-offset timestamp, a naive timestamp,
  a deadline before its creation time, and summary counts engineered to
  mismatch in multiple ways including an unknown bucket and a
  non-numeric summary value). Produces exit code `1`.

## Tests

```
python3 -m unittest test_queue_audit -v
```

175 tests covering: constant values, canonical JSON formatting,
timestamp parsing (19 data-driven cases), reward validation (23
data-driven cases including the precision regression), every required
field missing/null/empty/wrong-typed (24 data-driven cases), status/list
matching and mismatching, duplicate detection (single pair, triple,
multiple groups, malformed IDs excluded), deadline-before-created,
summary count mismatches (including non-numeric and boolean summary
values, unknown buckets in either direction, zero-count buckets), top-level
document shape errors, determinism (repeated-run equality, dict
key-order independence, sorted findings), full CLI subprocess
integration (both fixtures, stdin, `-o`/`--output`, nonexistent file,
malformed JSON, no arguments, `--help`, byte-identical repeated runs),
and the reward-precision regression described above.
