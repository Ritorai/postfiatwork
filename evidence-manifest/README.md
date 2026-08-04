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
