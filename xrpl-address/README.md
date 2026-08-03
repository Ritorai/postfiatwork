# XRPL Classic and X-Address Validator

Stdlib-only Python 3 (`hashlib`, `json`, `argparse`). No third-party packages,
no network. Nothing is queried on-ledger — this is pure structural and
cryptographic validation of the address string itself.

## Exact rerun commands

```
python3 -m unittest test_address_validate -v
python3 address_validate.py addresses_valid.json -o report_valid.json ; echo "exit=$?"
python3 address_validate.py addresses_invalid.json -d denylist.json -o report_invalid_run1.json ; echo "exit=$?"
python3 address_validate.py addresses_invalid.json -d denylist.json -o report_invalid_run2.json ; echo "exit=$?"
sha256sum report_invalid_run1.json report_invalid_run2.json
cmp report_invalid_run1.json report_invalid_run2.json && echo BYTE-IDENTICAL
python3 address_validate.py /nonexistent.json ; echo "exit=$?"
```

## Expected results

| step | result |
|------|--------|
| tests | `Ran 34 tests` / `OK` |
| valid fixture | `status=clean valid=4 invalid=0`, exit **0** |
| invalid fixture (both runs) | `status=issues valid=1 invalid=7`, exit **1** |
| both reports SHA-256 | `cd46dafe514eb23f38f5cbf2a402c4e5c0450e22c3af74b43f6da94afe9f6c0d` |
| `cmp` | BYTE-IDENTICAL |
| missing file | `UNREADABLE_INPUT`, exit **2** |

## The alphabet matters

XRPL uses its **own** base58 ordering, not Bitcoin's:

```
rpshnaf39wBUDNEGHJKLM4PQRST7VWXYZ2bcdeCg65jkm8oFqi1tuvAxyz
```

It begins `rpsh`, which is why classic addresses start with `r`. Decoding an
XRPL address with the Bitcoin alphabet yields a different payload and a
checksum that appears valid to a naive implementation. `test_xrpl_alphabet_is_not_bitcoin`
asserts the two differ, and `test_bitcoin_only_char_rejected` covers `0`, which
exists in Bitcoin's alphabet but not XRPL's.

## Address types

| type | prefix bytes | payload length | leading char |
|------|--------------|----------------|--------------|
| classic | `0x00` | 21 | `r` |
| X-address mainnet | `0x05 0x44` | 31 | `X` |
| X-address testnet | `0x04 0x93` | 31 | `T` |

Checksum = first 4 bytes of `SHA256(SHA256(payload))`, appended to the payload
before base58 encoding.

## Issue codes (all 6 exercised by addresses_invalid.json)

| code | count | trigger in the fixture |
|------|-------|------------------------|
| MALFORMED_RECORD | 2 | an empty string and a JSON number |
| BAD_ALPHABET | 1 | `rNOT!VALID#CHARS` |
| BAD_CHECKSUM | 1 | a valid address with one character advanced |
| BAD_PREFIX | 1 | version byte `0x09` |
| BAD_LENGTH | 1 | 10-byte account id instead of 20 |
| DENYLISTED | 1 | structurally valid, listed in `denylist.json` |

## Notable test

`test_every_single_char_mutation_detected` walks **every position** in a valid
address, advances that character to the next alphabet symbol, and asserts the
result is rejected. This proves the checksum actually does its job rather than
the validator merely pattern-matching the shape.

## Denylist semantics

The denylist is checked **only after** structural validation passes. A malformed
string that happens to appear on the denylist is reported as malformed, not as
denylisted — the more fundamental defect wins, and a denylist entry never
implies the address was otherwise well-formed.
`test_denylist_only_applies_to_structurally_valid` pins this.

## Scope limit

Validation is offline. A structurally valid, correctly checksummed address may
still be unfunded or never activated on-ledger; confirming that needs an XRPL
node query, which is deliberately out of scope here.

## Exit codes

0 = all valid and allowed · 1 = issues found · 2 = unreadable input
