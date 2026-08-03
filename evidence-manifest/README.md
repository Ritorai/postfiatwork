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

## Expected results

| step | result |
|------|--------|
| test suite | `Ran 29 tests` / `OK`, exit 0 |
| build (both runs) | `batch_root=ac454cee291d825e13310a14214f1d665a457f7251d22d2aefab2e64fc8ec28b`, exit 0 |
| manifest file SHA-256 (both runs) | `ba351b028a8b85f8aa93cd2769cd54dba23433ae8f7868da4bc6ecad3cd112f3` |
| `cmp` | BYTE-IDENTICAL |
| verify clean manifest | `VERIFIED ...`, exit **0** |
| verify tampered manifest | `VERIFICATION FAILED` + 2 drift lines, exit **1** |

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
