# schema-checker

`schema_check.py` validates a batch of evidence payloads against a **declarative schema file**
and emits a deterministic, canonical JSON report in which every violation carries an
RFC 6901 JSON Pointer to the offending location.

* Python 3 standard library only. No third-party packages, no network access.
* Verified on stock `python3` (CPython 3.10.12).
* Every violation is reported, not just the first.

---

## 1. Files

| File | Purpose |
|---|---|
| `schema_check.py` | The CLI and the validation library. |
| `test_schema_check.py` | unittest suite (95 tests). |
| `schema.json` | Fixture schema exercising every constraint kind, incl. nested objects and arrays. |
| `payloads_valid.json` | A conforming batch (3 records). |
| `payloads_invalid.json` | A batch of 7 records; different records break different constraint kinds and record index 2 breaks four at once. |
| `schema_malformed.json` | A deliberately broken schema, used to prove exit code 2. |
| `captured_output.txt` | Verbatim console capture of the runs described in section 3. |
| `README.md` | This file. |

---

## 2. Usage

```
python3 schema_check.py <schema.json> <payloads.json> [-o REPORT.json]
```

| Option | Meaning |
|---|---|
| `-o`, `--out PATH` | Write the canonical JSON report to `PATH` instead of stdout. A one-line human summary goes to **stderr**; stdout stays empty so the report file is the only machine-readable artifact. |
| `--version` | Print the tool version and exit. |

---

## 3. Exact rerun commands

Run these from the directory containing the files.

```bash
# 1. test suite
python3 -m unittest test_schema_check -v

# 2. conforming batch -> exit 0
python3 schema_check.py schema.json payloads_valid.json ; echo "exit=$?"

# 3. non-conforming batch, twice, into two files -> exit 1 both times
python3 schema_check.py schema.json payloads_invalid.json -o report_run1.json ; echo "exit=$?"
python3 schema_check.py schema.json payloads_invalid.json -o report_run2.json ; echo "exit=$?"

# 4. prove the two reports are byte-identical
sha256sum report_run1.json report_run2.json
cmp report_run1.json report_run2.json && echo "IDENTICAL"

# 5. malformed schema -> exit 2
python3 schema_check.py schema_malformed.json payloads_valid.json ; echo "exit=$?"

# 6. nonexistent file -> exit 2
python3 schema_check.py schema.json does_not_exist.json ; echo "exit=$?"
```

### Expected results

| # | Command | Expected exit | Expected outcome |
|---|---|---|---|
| 1 | `python3 -m unittest test_schema_check -v` | 0 | `Ran 95 tests ... OK` |
| 2 | CLI on `payloads_valid.json` | **0** | `"status":"conform"`, `"violation_count":0` |
| 3a | CLI on `payloads_invalid.json` `-o report_run1.json` | **1** | `"status":"violations"`, `"violation_count":19` |
| 3b | same, `-o report_run2.json` | **1** | identical content to `report_run1.json` |
| 4 | `sha256sum` + `cmp` | 0 | same digest, `cmp` silent |
| 5 | CLI with `schema_malformed.json` | **2** | `"status":"error"`, 11 entries in `schema_errors`, `violations` empty |
| 6 | CLI with a nonexistent payload file | **2** | `"status":"error"`, one `IO_ERROR` in `io_errors` |

Violation code counts on `payloads_invalid.json` (19 total):

| Code | Count |
|---|---|
| `PATTERN_MISMATCH` | 4 |
| `ENUM_MISMATCH` | 2 |
| `MISSING_REQUIRED` | 2 |
| `MIN_LENGTH` | 2 |
| `TYPE_MISMATCH` | 2 |
| `DUPLICATE_ITEMS` | 1 |
| `MAXIMUM` | 1 |
| `MAX_ITEMS` | 1 |
| `MAX_LENGTH` | 1 |
| `MINIMUM` | 1 |
| `MIN_ITEMS` | 1 |
| `UNEXPECTED_KEY` | 1 |

---

## 4. Exit codes

| Code | Meaning |
|---|---|
| `0` | The payload document conforms to the schema. |
| `1` | The schema is well-formed but the payload document has one or more violations. |
| `2` | Something could not be processed: schema or payload file unreadable, not valid UTF-8, not valid JSON, or the schema itself is not a well-formed schema. Also the exit code argparse uses for bad command-line arguments, and the code returned if `-o` cannot be written. |

Exit `2` short-circuits: if the schema is malformed the payload is **not** validated, and
`violations` is empty. Rationale — violations computed against a schema we do not trust
would be misleading evidence.

---

## 5. Schema DSL specification

A schema file is a JSON **document**:

```json
{
  "root":        <node>,      // required
  "name":        "string",    // optional
  "version":     <any>,       // optional
  "description": "string"     // optional
}
```

Any other top-level key is a `SCHEMA_UNKNOWN_KEYWORD` error.
`root` is the node applied to the payload document root (JSON Pointer `""`).

### 5.1 Node

Every node is a JSON object and **must** declare `type`.

```json
{ "type": "<name>" | ["<name>", ...], ...constraints }
```

Type names: `object`, `array`, `string`, `number`, `integer`, `boolean`, `null`, `any`.
A list of names is a union — the value satisfies the node if it matches any member
(this is how nullable fields are expressed, e.g. `{"type": ["string", "null"]}`).

Type semantics:

* `integer` — a JSON integer. `1.5` is **not** an integer; `1.0` decodes to a Python float and is therefore **not** an integer either.
* `number` — a JSON integer **or** float.
* `boolean` — `true`/`false` only. `true` is **not** accepted as an `integer`, and `1` is **not** accepted as a `boolean`, despite Python's `bool`/`int` relationship.
* `any` — matches every value; all constraint keywords are permitted on it and each is applied only when the runtime value has the matching kind.

### 5.2 Constraint keywords

Applicable to any type:

| Keyword | Type | Meaning |
|---|---|---|
| `enum` | non-empty list | Value must equal one of the listed values (structural equality, see 5.3). |
| `description` | string | Documentation only, never validated against. |

`object`:

| Keyword | Type | Meaning | Violation code |
|---|---|---|---|
| `properties` | object of name -> node | Per-key rules, applied only when the key is present. | (delegated) |
| `required` | list of strings | Each named key must be present. | `MISSING_REQUIRED` |
| `additional_properties` | boolean, default `true` | When `false`, every key not listed in `properties` is rejected. | `UNEXPECTED_KEY` |

`array`:

| Keyword | Type | Meaning | Violation code |
|---|---|---|---|
| `items` | node | Applied to every element. | (delegated) |
| `min_items` | non-negative integer | Inclusive lower bound on length. | `MIN_ITEMS` |
| `max_items` | non-negative integer | Inclusive upper bound on length. | `MAX_ITEMS` |
| `unique_items` | boolean | When `true`, elements must be structurally distinct. | `DUPLICATE_ITEMS` |

`string`:

| Keyword | Type | Meaning | Violation code |
|---|---|---|---|
| `pattern` | string | Python `re` regular expression, applied with `re.search`. | `PATTERN_MISMATCH` |
| `min_length` | non-negative integer | Inclusive lower bound on `len()` (Unicode code points). | `MIN_LENGTH` |
| `max_length` | non-negative integer | Inclusive upper bound on `len()`. | `MAX_LENGTH` |

`number` / `integer`:

| Keyword | Type | Meaning | Violation code |
|---|---|---|---|
| `minimum` | number | Inclusive lower bound. | `MINIMUM` |
| `maximum` | number | Inclusive upper bound. | `MAXIMUM` |

A keyword that exists in the DSL but does not apply to the node's declared type
(e.g. `pattern` on a `number`) is a `SCHEMA_KEYWORD_NOT_APPLICABLE` error.
A keyword that does not exist at all is `SCHEMA_UNKNOWN_KEYWORD`.

### 5.3 Structural equality

`enum` membership and `unique_items` both compare values by their **canonical JSON
encoding** (`sort_keys=True`, compact separators), not by Python `==`. Consequences:

* Object key order is irrelevant: `{"a":1,"b":2}` equals `{"b":2,"a":1}`.
* `true` does **not** equal `1`, even though Python says `True == 1`.

### 5.4 Example

```json
{
  "root": {
    "type": "object",
    "required": ["records"],
    "additional_properties": false,
    "properties": {
      "records": {
        "type": "array",
        "min_items": 1,
        "items": {
          "type": "object",
          "required": ["cid"],
          "properties": { "cid": { "type": "string", "pattern": "^[a-f0-9]{8}$" } }
        }
      }
    }
  }
}
```

A bad `cid` in the third record yields pointer `/records/2/cid`.

---

## 6. Violation codes

Payload violations (exit 1):

| Code | Raised when |
|---|---|
| `TYPE_MISMATCH` | Value does not match any declared type. |
| `MISSING_REQUIRED` | A key named in `required` is absent. |
| `UNEXPECTED_KEY` | Undeclared key present while `additional_properties` is `false`. |
| `ENUM_MISMATCH` | Value is not in `enum`. |
| `PATTERN_MISMATCH` | String does not match `pattern`. |
| `MIN_LENGTH` / `MAX_LENGTH` | String length outside `[min_length, max_length]`. |
| `MINIMUM` / `MAXIMUM` | Number outside `[minimum, maximum]`. |
| `MIN_ITEMS` / `MAX_ITEMS` | Array length outside `[min_items, max_items]`. |
| `DUPLICATE_ITEMS` | Array element repeats an earlier element under `unique_items`. |

Schema errors (exit 2, in `schema_errors`, pointers address the **schema** document):

| Code | Raised when |
|---|---|
| `SCHEMA_NOT_OBJECT` | The schema document is not a JSON object. |
| `SCHEMA_MISSING_ROOT` | No `root` key. |
| `SCHEMA_NODE_NOT_OBJECT` | A node is not a JSON object. |
| `SCHEMA_MISSING_TYPE` | A node has no `type`. |
| `SCHEMA_UNKNOWN_TYPE` | `type` names a type that does not exist. |
| `SCHEMA_UNKNOWN_KEYWORD` | Keyword is not part of the DSL. |
| `SCHEMA_KEYWORD_NOT_APPLICABLE` | Keyword is real but wrong for the declared type. |
| `SCHEMA_BAD_KEYWORD_TYPE` | Keyword value has the wrong JSON type (or a negative length/count). |
| `SCHEMA_BAD_REGEX` | `pattern` does not compile. |
| `SCHEMA_EMPTY_ENUM` | `enum` is `[]`. |
| `SCHEMA_BAD_BOUNDS` | `maximum < minimum`, `max_length < min_length`, or `max_items < min_items`. |

Input errors (exit 2, in `io_errors`, pointer is always `""`):

| Code | Raised when |
|---|---|
| `IO_ERROR` | File missing or unreadable. |
| `ENCODING_ERROR` | File is not valid UTF-8. |
| `JSON_PARSE_ERROR` | File is not valid JSON (message carries line and column). |

---

## 7. JSON Pointer semantics

Pointers follow RFC 6901.

* The document root is the **empty string** `""`.
* Each step is `/` followed by an object key or a zero-based array index: `/records/2/cid`.
* Escaping inside a key: `~` becomes `~0`, `/` becomes `~1`. `~0` is applied first so a
  key literally containing `~1` round-trips correctly (key `a~b` -> `/a~0b`, key `a/b` -> `/a~1b`).
* A `MISSING_REQUIRED` pointer addresses the key that **should** exist
  (`/records/2/tags`), not its parent object. Same for `UNEXPECTED_KEY`, which addresses
  the offending key itself.
* `MIN_ITEMS` / `MAX_ITEMS` address the array; `DUPLICATE_ITEMS` addresses the
  *later* of the two duplicate elements, and the message names the index of the first.

---

## 8. Report format

Always the same key set, canonically encoded
(`json.dumps(..., sort_keys=True, separators=(",",":"), ensure_ascii=True)` plus one
trailing `\n`):

```json
{"exit_code":1,"io_errors":[],"ok":false,"payload_source":"payloads_invalid.json",
 "schema_errors":[],"schema_source":"schema.json","status":"violations","summary":{...},
 "tool_version":"1.0.0","violation_count":19,
 "violations":[{"code":"...","message":"...","pointer":"/records/2/cid"}]}
```

* `status` is `conform`, `violations`, or `error`.
* `summary` maps code -> count and always sums to the length of the populated list.
* `violations` and `schema_errors` are sorted by `(pointer, code, message)`.

---

## 9. Judgement calls a reviewer should scrutinise

1. **`pattern` uses `re.search`, not `re.fullmatch`.** This mirrors JSON Schema, but it
   means an unanchored pattern matches a substring. Every pattern in `schema.json` is
   explicitly anchored with `^...$`. If you prefer full-match semantics, change the one
   call in `_check_string`.
2. **A `TYPE_MISMATCH` suppresses the deeper checks on that node.** Once a value is the
   wrong type, `pattern` / `minimum` / `items` etc. cannot be meaningfully applied, so we
   report one clear violation instead of a cascade of noise. Sibling keys and other array
   elements are still fully checked, so "report every violation" holds across the
   document — the pruning is per-node only.
3. **Report ordering is lexicographic on the pointer string**, so `/records/10` sorts
   before `/records/2`. This is deterministic (which is the requirement) but not numeric.
   Sorting numerically would need pointer parsing and a mixed-type key.
4. **The report embeds `schema_source` and `payload_source` as the literal argv strings.**
   That is deliberate provenance for an evidence tool, but it makes byte-identity
   contingent on identical invocation paths: the same content invoked via a different
   relative path yields a different digest. Drop those two keys if you want
   content-only determinism.
5. **Schema errors short-circuit payload validation** (section 4) — you get schema errors
   *or* violations, never both.
6. **Structural (canonical-JSON) equality for `enum` and `unique_items`** rather than
   Python `==`, chosen so `true` is not conflated with `1`. It also makes `1` and `1.0`
   distinct, which may surprise.
7. **`integer` is strict**: `1.0` in the payload JSON decodes to a float and fails an
   `integer` node. Use `number` if you want to accept both.
8. **`additional_properties` defaults to `true`.** Nested objects that should be closed
   must say `"additional_properties": false` explicitly; `schema.json` leaves
   `source.meta` open on purpose to show the difference.
9. **Payload shape is whatever `root` says.** The tool does not assume a `records` array;
   the fixtures use `{"batch_id", "records": [...]}` purely because it produces the
   `/records/2/cid` style pointers in the brief.


## 3 limitations a reviewer should scrutinise

Found by **running this tool against adversarial inputs**, not by reading it.
Every claim is reproduced by
[`limitations-probe/probe.py`](../limitations-probe/probe.py), which exits
non-zero if any of them stops reproducing.

1. **`pattern` is a SEARCH, not a full match (SC-1).** A schema declaring
   `"pattern": "[0-9]{4}"` accepts the value `"XX1234XX"` as **conform**.
   Every unanchored pattern in every schema is therefore far more permissive
   than it reads, and the failure is silent — you get `conform`, not a
   warning. This repository's own fixture schema anchors all of its patterns
   with `^...$`, which is exactly why its own test suite never surfaces it.
   Anchor every pattern you write, or treat this checker's `pattern` as
   "contains a match for".

2. **`max_length` counts code points, not bytes (SC-2).** Under
   `"max_length": 4`, all three of `"abcd"` (4 UTF-8 bytes), four `U+00E9`
   (8 bytes) and four `U+1F600` (**16 bytes**) are `conform`. A field sized
   against a byte budget — a `VARCHAR`, an on-chain memo, a fixed record — can
   be overrun **4x** by a payload this checker approves. Grapheme clusters are
   a third unit again: a flag emoji or a combining sequence is one *visible*
   character and several code points.

3. **A schema-supplied pattern can run forever (SC-3).** The schema is an
   *input*. `re.compile()` is validated when the schema loads, but nothing
   bounds match time: `"pattern": "^(a+)+$"` against a 33-character
   non-matching string does not finish. The probe that demonstrates this has
   to impose its own 20-second timeout to terminate — **that timeout belongs
   to the probe, not to this tool.** Any pipeline that accepts a schema from
   an untrusted or merely careless source can be hung by one line of it.
   There is no `--timeout` and no regex complexity check.

**Checked and found sound, reported as a negative result (SC-4):** `type:
"integer"` correctly rejects JSON `true` and the integral float `5.0`, and
rejects `1e400` as a number rather than overflowing. `bool` subclasses `int`
in Python and `5.0 == 5`, so both are easy to get wrong; this checker gets
both right. No limitation is claimed there.
