# Post Fiat Payload / Memo Validator

Stdlib-only Python 3 (`argparse`, `decimal`, `hashlib`, `json`, `sys`). No
third-party packages, no network calls. Rejects malformed XRPL memo and
transaction payloads before they reach Post Fiat task processing.

## What was reused vs. what is new

The sibling tool `xrpl-address/address_validate.py` already implements
XRPL Base58Check address validation. Per the brief, this tool reuses that
work rather than reimplementing it.

**Reused verbatim (same names, same algorithm, copied into this single-file
tool since only `payload_validate.py` ships):**
- `ALPHABET` / `ALPHABET_MAP` -- the XRPL base58 alphabet (`rpshna...`, *not*
  Bitcoin's alphabet)
- `b58decode()` -- base58 string to raw bytes
- `double_sha256()` -- SHA256(SHA256(x)), used for the checksum
- `classify()` -- recognises classic (`0x00`), X-address mainnet
  (`0x05 0x44`) and X-address testnet (`0x04 0x93`) payloads and their
  expected lengths
- The checksum/prefix/length decision logic itself, i.e. the body of
  `address_validate.validate_address()`, ported here as
  `address_subissues()`. It returns the same sub-codes
  (`BAD_ALPHABET`/`BAD_LENGTH`/`BAD_PREFIX`/`BAD_CHECKSUM`) for diagnostics;
  the caller folds any non-empty result into a single `INVALID_ADDRESS`
  finding since that's the code named in this tool's spec.
- The XRPL test addresses in `test_payload_validate.py` (`CLASSIC`,
  `CLASSIC2`, `XMAIN`, `XTEST`, `BADPREF`, `BADLEN`) are the same known-good
  values used in `xrpl-address`'s fixtures, reused as test data.

**Left out on purpose (not reused):** the denylist feature. Denylisting is
an address-level concern out of scope for payload/memo validation.

**Everything else is new** for this tool:
- hex memo decoding and the decoded-byte-limit check (`INVALID_HEX`,
  `MEMO_TOO_LARGE`)
- UTF-8 validation of decoded memo bytes (`MEMO_NOT_UTF8`)
- required-field checking across 5 logical fields (`MISSING_REQUIRED_FIELD`)
- self-payment detection (`SELF_PAYMENT`)
- the entire `decimal.Decimal`-based amount pipeline for `amount_drops` /
  `amount_pft`, including the `parse_float=Decimal` / `parse_constant`
  JSON-parsing setup, NaN/Infinity rejection, negative/fractional checks,
  and the 1e17-drops range ceiling (`INVALID_AMOUNT`, `AMOUNT_OUT_OF_RANGE`)
- duplicate `payload_id` detection (`DUPLICATE_PAYLOAD_ID`)
- non-object array element handling (`MALFORMED_RECORD`)
- the deterministic canonical-JSON report assembly, the `--max-memo-bytes`
  flag, the `-`/stdin and `-o`/file-output CLI surface, and the exit-code
  contract
- the full test suite (179 tests)

## Input shape

The input is a JSON array. Each element is expected to be a JSON object
("record") with these fields:

| field | type | required | notes |
|---|---|---|---|
| `payload_id` | string | yes | non-empty; must be unique across the array |
| `memo_hex` | string | yes | hex-encoded memo bytes; `""` means an empty memo and is valid |
| `account` | string | yes | XRPL classic (`r...`) or X-address (`X...`/`T...`) |
| `destination` | string | yes | same address rules as `account`; must differ from `account` |
| `amount_drops` | int, numeric string, or JSON number | exactly one of these two | whole-number drops, `0 <= drops <= 1e17` |
| `amount_pft` | int, numeric string, or JSON number | exactly one of these two | non-negative PFT amount, may be fractional |

Extra/unknown fields on a record are ignored (not flagged). An array
element that is not a JSON object at all (a string, number, list, `null`,
or boolean) is `MALFORMED_RECORD` and gets no further checks.

## Finding codes

| code | meaning |
|---|---|
| `INVALID_HEX` | `memo_hex` isn't a string, has odd length, or contains non-hex characters (internal whitespace counts as invalid -- no silent tolerance) |
| `MEMO_TOO_LARGE` | decoded memo exceeds `--max-memo-bytes` (default 1024). **Boundary is `>`, not `>=`**: a memo of exactly the limit passes |
| `MEMO_NOT_UTF8` | decoded memo bytes fail strict UTF-8 decoding |
| `MISSING_REQUIRED_FIELD` | a required field is absent, or (`payload_id`/`account`/`destination` only) present but empty/wrong type; also used when neither `amount_drops` nor `amount_pft` is present |
| `INVALID_ADDRESS` | `account`/`destination` fails XRPL Base58Check (bad alphabet, length, prefix, or checksum -- detail names the sub-code) |
| `SELF_PAYMENT` | `account == destination` as raw strings (checked whenever both are non-empty strings, even if one/both are otherwise invalid addresses) |
| `INVALID_AMOUNT` | negative, NaN/Infinity, non-numeric, fractional-drops, or both-`amount_drops`-and-`amount_pft`-present |
| `AMOUNT_OUT_OF_RANGE` | `amount_drops > 100_000_000_000_000_000` (100 billion XRP in drops); `==` is allowed |
| `DUPLICATE_PAYLOAD_ID` | a `payload_id` seen at an earlier index repeats; the earlier occurrence is canonical and stays clean, later ones are flagged |
| `MALFORMED_RECORD` | the array element itself is not a JSON object |

## Money handling

`json.loads(text, parse_float=decimal.Decimal, parse_constant=<reject>)` is
used so JSON float literals become exact `Decimal` values from their
original text (not `Decimal(str(float(...)))`, which would first round
through a 64-bit float and could silently corrupt values). Bare `NaN` /
`Infinity` / `-Infinity` JSON tokens make the *entire parse* fail (exit 2)
via `parse_constant`; a **quoted** string `"NaN"` in an amount field is a
different case -- it parses fine and is caught per-record as
`INVALID_AMOUNT` via `Decimal.is_nan()`. Amounts are emitted in the report
as JSON strings, rendered with `format(d, "f")` so large or small values
never appear in scientific notation.

## Canonical JSON / determinism

Reports are serialized with
`json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)`
plus one trailing `\n`. Findings carry a deterministic total order: the
top-level `findings` array is sorted by `(index, code, field, detail)`,
and each record's own `findings` list is sorted by `(code, field, detail)`
before being counted. Running the tool twice on the same input with the
same flags produces byte-identical output (verified below).

## Exit codes

- **0** -- parsed successfully, zero findings
- **1** -- parsed successfully, one or more findings
- **2** -- invalid input or usage: file not found, unreadable, invalid JSON
  (including a bare `NaN`/`Infinity` token), top-level JSON value is not an
  array, or a bad CLI argument (argparse's own usage errors also exit 2)

## Exact rerun commands

```
python3 -m unittest test_payload_validate -v
python3 payload_validate.py payloads_ok.json ; echo "exit=$?"
python3 payload_validate.py payloads_bad.json -o r1.json ; echo "exit=$?"
python3 payload_validate.py payloads_bad.json -o r2.json ; echo "exit=$?"
sha256sum r1.json r2.json
cmp r1.json r2.json && echo BYTE-IDENTICAL
python3 payload_validate.py payloads_bad.json --max-memo-bytes 1000000 ; echo "exit=$?"
cat payloads_bad.json | python3 payload_validate.py - ; echo "exit=$?"
python3 payload_validate.py /nonexistent.json ; echo "exit=$?"
```

`r1.json`/`r2.json` are scratch files produced by the verification run;
they are not part of this deliverable and are not shipped in the archive.
Full real output of every command above is in `captured_output.txt`.

## Expected results

| step | result |
|---|---|
| tests | `Ran 179 tests` / `OK` |
| `payloads_ok.json` | `status=clean`, 7 payloads, 0 findings, exit **0** |
| `payloads_bad.json` (both runs) | `status=issues`, 16 payloads, 19 findings, exit **1** |
| both reports' SHA-256 | identical to each other (see `captured_output.txt` for the exact hex digest) |
| `cmp r1.json r2.json` | `BYTE-IDENTICAL` |
| `--max-memo-bytes 1000000` on `payloads_bad.json` | findings drop from 19 to 18 (the `MEMO_TOO_LARGE` finding on `memo-too-large` disappears; `ok` count rises from 1 to 2), exit **1** |
| stdin (`-`) on `payloads_bad.json` | same as the file run, exit **1** |
| `/nonexistent.json` | `INVALID_INPUT: file not found: ...`, exit **2** |

## `payloads_bad.json` -- all 10 codes, by `payload_id`

| index | payload_id | code(s) triggered |
|---|---|---|
| 0 | *(n/a -- element is the string `"not-an-object"`)* | `MALFORMED_RECORD` |
| 1 | *(n/a -- `{}`)* | `MISSING_REQUIRED_FIELD` x5 (`payload_id`, `memo_hex`, `account`, `destination`, `amount`) |
| 2 | `bad-hex-odd-length` | `INVALID_HEX` (odd length) |
| 3 | `bad-hex-chars` | `INVALID_HEX` (non-hex characters) |
| 4 | `memo-too-large` | `MEMO_TOO_LARGE` (1025 decoded bytes > 1024 default) |
| 5 | `memo-not-utf8` | `MEMO_NOT_UTF8` (single byte `0xFF`) |
| 6 | `checksum-flip-demo` | `INVALID_ADDRESS` (`account` = the flipped-checksum address, see below) |
| 7 | `bad-alphabet-addr` | `INVALID_ADDRESS` (`destination` contains `!`/`#`, outside the XRPL alphabet) |
| 8 | `self-payment` | `SELF_PAYMENT` (`account == destination`) |
| 9 | `negative-drops` | `INVALID_AMOUNT` (`amount_drops = -5`) |
| 10 | `fractional-drops` | `INVALID_AMOUNT` (`amount_drops = 12.5`, not a whole number) |
| 11 | `nan-pft` | `INVALID_AMOUNT` (`amount_pft = "NaN"`) |
| 12 | `both-amounts` | `INVALID_AMOUNT` (both `amount_drops` and `amount_pft` present) |
| 13 | `drops-out-of-range` | `AMOUNT_OUT_OF_RANGE` (`amount_drops = 100000000000000001`, one above the 1e17 ceiling) |
| 14, 15 | `dup-id` (twice) | index 14 is clean (canonical); index 15 gets `DUPLICATE_PAYLOAD_ID` |

## The checksum-bites demonstration

`payloads_bad.json` record `checksum-flip-demo` (index 6) uses:

```
account = "raLnyS4PTuc5SgXGHqYA894a4eoKqoFwu"
```

This is the known-good address `raLnyR4PTuc5SgXGHqYA894a4eoKqoFwu` with
position 5 changed from `R` to `S` -- both characters are in the XRPL
alphabet, so the length and alphabet checks pass. Only the trailing
4-byte `SHA256(SHA256(payload))` checksum disagrees, and only the
checksum step catches it, producing:

```
{"code":"INVALID_ADDRESS","detail":"account failed XRPL Base58Check: BAD_CHECKSUM","field":"account","index":6,"payload_id":"checksum-flip-demo"}
```

`test_payload_validate.py::TestReusedAddressLogic::test_flipped_char_address_is_correct_length_and_alphabet`
pins this exact property (same length, all characters in-alphabet, exactly
one character differs) before asserting the checksum step is what fails it.

## A real bug found and fixed

While hunting for edge cases (per the brief's checklist) I tested reading
a payload file saved with a leading UTF-8 byte-order mark (BOM,
`EF BB BF`) -- a common artifact from Windows editors such as Notepad, and
directly relevant given this tool's typical execution environment. The
original implementation opened input files with `encoding="utf-8"` and
read stdin with the platform default, neither of which strips a BOM. The
result: a perfectly valid JSON array failed to parse at all --

```
INVALID_INPUT: invalid JSON: Unexpected UTF-8 BOM (decode using utf-8-sig): line 1 column 1 (char 0)
```

exit code 2 -- even though the payload content itself was completely
valid. This is a real usability defect: a well-formed input file gets
rejected as "invalid input" for a reason that has nothing to do with the
payload data. **Fixed the tool** (not the test) by (a) opening files with
`encoding="utf-8-sig"`, which transparently strips a leading BOM and
behaves identically to `utf-8` when there is none, and (b) stripping a
leading `U+FEFF` from stdin text via a small `_strip_bom()` helper, since
stdin doesn't support a `-sig` codec the same way. Regression tests are in
`TestUtf8BomHandling` (4 tests, both the file and stdin paths, both with
and without a BOM present).

## Design decisions worth flagging (things a reviewer should scrutinise)

1. **`SELF_PAYMENT` is raw string equality only.** It does not decode
   X-addresses to compare underlying account IDs, so a classic address and
   an X-address that both encode the *same* underlying XRPL account are
   **not** flagged as a self-payment (`test_xaddress_vs_classic_same_underlying_account_not_flagged`
   pins this). A determined payload could route funds to itself by mixing
   address formats and slip past this check.
2. **No range ceiling on `amount_pft`.** `AMOUNT_OUT_OF_RANGE` is only
   defined for drops against the hard-coded 1e17-drop XRP supply figure,
   per the spec. PFT is a different token with no such well-known supply
   constant given in the brief, so an absurdly large `amount_pft` (e.g.
   `1e30`) currently passes cleanly. If Post Fiat has a real PFT supply
   cap it should be added as an explicit constant, the same way `MAX_DROPS`
   is.
3. **Unbounded numeric-string parsing before the size check.** `amount_drops`/
   `amount_pft` accept arbitrarily long numeric strings (verified up to a
   401-digit value in testing without crashing or misbehaving), and the
   `AMOUNT_OUT_OF_RANGE` detail message embeds the full decimal expansion
   of whatever was submitted. This is correct, but a hostile input with a
   very long digit string is accepted for parsing (and rendered in full in
   the output) before being rejected on range grounds -- there's no
   independent input-size cap on the numeric-string length itself, which
   could matter if payloads come from an untrusted source at scale.

## Scope limit

Like the reused XRPL address logic, this is entirely offline structural
and arithmetic validation. It does not query the XRPL ledger -- it cannot
tell you whether an address is funded/activated, whether a `payload_id`
was already processed by a *previous* run (only duplicates *within one
input array* are caught), or whether decoded memo text is semantically
sensible for its task.
